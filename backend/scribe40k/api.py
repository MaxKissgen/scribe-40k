"""HTTP API, and the server that hosts the editor.

Single user, local machine, no auth. The editor talks to this; so does the PDF exporter,
which drives a headless browser against this same server's ``/print/{id}`` route.

One rule shapes most of the write endpoints: **the server owns derivation and validation.**
The editor may send whatever the user typed, and gets back the finished document plus the
current flags. That keeps the derived values (characteristic bonuses, proficiency
modifiers) correct no matter what the client does, and means the review state is computed
in one place rather than two.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import LOADED_ENV_KEYS, pointer
from . import constants as K
from .blank import blank_character
from .llm.base import ProviderError
from .llm.config import load_config
from .llm.registry import OFFLINE_PROVIDERS, build_ocr_provider, build_reasoning_provider
from .paths import ARMOUR_SILHOUETTE, FRONTEND_DIST
from .pipeline.report import ExtractionReport
from .pipeline.run import PageTarget, PreparedPages
from .pipeline.run import finish as run_finish
from .pipeline.run import prepare as run_prepare
from .pipeline.validate import dedupe_flags, validate_document
from .store import CharacterStore

app = FastAPI(title="scribe-40k", version="0.1.0")

# The Vite dev server runs on another port during development. In production the built
# frontend is served from this same origin and none of this applies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = CharacterStore()


# --------------------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------------------


class PatchOperation(BaseModel):
    """One field edit, addressed by JSON Pointer."""

    pointer: str
    value: Any = None


class PatchRequest(BaseModel):
    operations: list[PatchOperation] = Field(default_factory=list)


class FlagUpdate(BaseModel):
    pointer: str
    rule: str
    status: str


class UnmappedUpdate(BaseModel):
    id: str
    status: str
    assignedTo: str | None = None


class CreateRequest(BaseModel):
    name: str | None = None


class ConfirmImport(BaseModel):
    """Which page of the upload is which page of the sheet.

    Keyed by PDF page number as a string, because that is what JSON object keys are. A
    value of 1-5 is a sheet page, "notes" puts the page's text on a note page, and "skip"
    leaves it out entirely. Omitting the field accepts the proposal as it stands.
    """

    assignment: dict[str, int | Literal["notes", "skip"]] | None = None


class NotePageRequest(BaseModel):
    """A new free-text page, optionally seeded from an unassigned fragment."""

    title: str | None = None
    text: str | None = None
    #: When given, that fragment's text becomes the page's text and it leaves the tray.
    fromUnmapped: str | None = None


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _require(character_id: str) -> dict:
    if not store.exists(character_id):
        raise HTTPException(404, f"no character '{character_id}'")
    return store.load(character_id)


def _report_or_empty(character_id: str) -> ExtractionReport | None:
    return store.load_report(character_id)


def _save_and_revalidate(character_id: str, document: dict) -> dict:
    """Derive, validate, persist, and refresh the rule-based flags.

    Flags a human has already acted on are preserved: re-running the rules must not
    resurrect something the user has explicitly accepted or dismissed.
    """
    finished, rule_flags = validate_document(document)
    store.save(character_id, finished)
    _refresh_rule_flags(character_id, rule_flags)
    return finished


def _refresh_rule_flags(character_id: str, rule_flags: list) -> bool:
    """Replace the recomputable flags with a freshly computed set.

    Returns whether anything changed. Rule flags are derived state: keeping a stale one
    around means the user is asked to fix something that is already fixed, and -- worse --
    that the count never reaches zero however much they correct.
    """
    # A character created by hand has no report yet, but its values still deserve
    # checking, so one is started here rather than only on import.
    report = _report_or_empty(character_id) or ExtractionReport()

    resolved = {(f.pointer, f.rule): f.status for f in report.flags if f.status != "needs_review"}
    # Model-confidence flags are not recomputable from the document, so they survive
    # untouched; everything a rule produced is regenerated from the saved values.
    kept = [f for f in report.flags if not _is_rule_flag(f.rule)]
    for flag in rule_flags:
        flag.status = resolved.get((flag.pointer, flag.rule), "needs_review")

    before = [f.model_dump() for f in report.flags]
    report.flags = dedupe_flags([*kept, *rule_flags])
    if [f.model_dump() for f in report.flags] == before:
        return False

    store.save_report(character_id, report)
    return True


def _is_rule_flag(rule: str) -> bool:
    """True for flags that :func:`validate_document` regenerates on every save."""
    return not rule.startswith(("model.", "ocr.", "ingest."))


def _assignment_from(raw: dict[str, object]) -> dict[int, PageTarget]:
    try:
        return {int(page): target for page, target in raw.items()}  # type: ignore[misc]
    except ValueError as exc:
        raise HTTPException(400, f"page numbers must be integers: {exc}") from exc


def _proposal(character_id: str, prepared: PreparedPages) -> dict:
    """The assignment screen's whole input: one entry per page of the upload."""
    return {
        "id": character_id,
        "status": "awaiting_assignment",
        "sourceName": prepared.source_name,
        "sheetPageCount": K.SHEET_PAGE_COUNT,
        "pages": [
            {
                "pdfPage": page.pdf_page,
                "proposed": prepared.proposal.get(page.pdf_page, "notes"),
                "matchedBy": page.matched_by,
                "matchScore": round(page.match_score, 4),
                "textScore": round(page.text_score, 4) if page.matched_by == "text" else None,
                "ink": round(page.ink, 5),
                "note": page.note or None,
                "hasImage": page.image_path is not None,
                "transcriptionPreview": _preview(prepared.transcriptions.get(page.pdf_page)),
            }
            for page in prepared.ingested.pages
        ],
    }


def _preview(transcription) -> str | None:
    """Enough of what was read to recognise the page without opening the scan."""
    if transcription is None or not transcription.ok:
        return None
    collapsed = " ".join(transcription.text.split())
    return collapsed[:400] or None


def _payload(character_id: str, document: dict) -> dict:
    report = _report_or_empty(character_id)
    return {
        "id": character_id,
        "character": document,
        "report": report.to_json_dict() if report else None,
        "reviewCount": report.review_count if report else 0,
    }


# --------------------------------------------------------------------------------------
# Characters
# --------------------------------------------------------------------------------------


@app.get("/api/characters")
def list_characters() -> list[dict]:
    return [s.__dict__ for s in store.list_characters()]


@app.post("/api/characters", status_code=201)
def create_character(body: CreateRequest) -> dict:
    character_id = store.create_blank(body.name)
    return _payload(character_id, store.load(character_id))


@app.get("/api/characters/{character_id}")
def get_character(character_id: str) -> dict:
    """Load a character, re-running the rules that produce its flags.

    Opening a sheet recomputes them because they are derived from the document, and a
    document can become valid without the editor touching it -- a schema tightened, a
    model corrected, a fix made from the CLI. A report saved before any of that would
    show errors that no longer exist and that nothing on screen can clear.
    """
    document = _require(character_id)
    finished, rule_flags = validate_document(document)
    if finished != document:
        store.save(character_id, finished)
    _refresh_rule_flags(character_id, rule_flags)
    return _payload(character_id, finished)


@app.put("/api/characters/{character_id}")
def replace_character(character_id: str, document: Annotated[dict, Body()]) -> dict:
    _require(character_id)
    return _payload(character_id, _save_and_revalidate(character_id, document))


@app.patch("/api/characters/{character_id}")
def patch_character(character_id: str, body: PatchRequest) -> dict:
    """Apply pointer-addressed edits. This is what the editor's autosave calls."""
    document = _require(character_id)

    for operation in body.operations:
        try:
            pointer.set_value(document, operation.pointer, operation.value)
        except (KeyError, IndexError, ValueError) as exc:
            raise HTTPException(400, f"cannot write {operation.pointer}: {exc}") from exc

    return _payload(character_id, _save_and_revalidate(character_id, document))


@app.delete("/api/characters/{character_id}", status_code=204)
def delete_character(character_id: str) -> Response:
    _require(character_id)
    store.delete(character_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------------------


@app.get("/api/characters/{character_id}/report")
def get_report(character_id: str) -> dict:
    _require(character_id)
    report = _report_or_empty(character_id)
    if report is None:
        raise HTTPException(404, "this character has no extraction report")
    return report.to_json_dict()


@app.post("/api/characters/{character_id}/flags")
def update_flag(character_id: str, body: FlagUpdate) -> dict:
    """Accept, fix or dismiss one flag."""
    _require(character_id)
    report = _report_or_empty(character_id)
    if report is None:
        raise HTTPException(404, "this character has no extraction report")

    if body.status not in ("needs_review", "user_fixed", "accepted", "dismissed"):
        raise HTTPException(400, f"unknown flag status '{body.status}'")

    matched = 0
    for flag in report.flags:
        if flag.pointer == body.pointer and flag.rule == body.rule:
            flag.status = body.status  # type: ignore[assignment]
            matched += 1

    if not matched:
        raise HTTPException(404, f"no flag {body.rule} at {body.pointer}")

    store.save_report(character_id, report)
    return {"reviewCount": report.review_count, "updated": matched}


@app.post("/api/characters/{character_id}/flags/clear")
def clear_flags(character_id: str) -> dict:
    """Accept every flag still open, in one action.

    The equivalent of marking a conversation read. Reviewing forty low-confidence readings
    one at a time to reach zero is not review, it is data entry -- and a user who has just
    filled in the last few fields by hand already knows the sheet is right.

    Errors are included. They are the user's to wave through: a schema violation they have
    decided to live with is a decision, not an oversight, and the alternative is a counter
    that can never reach zero. The editor asks first and says how many are errors.
    """
    _require(character_id)
    report = _report_or_empty(character_id)
    if report is None:
        raise HTTPException(404, "this character has no extraction report")

    cleared = 0
    for flag in report.flags:
        if flag.status == "needs_review":
            flag.status = "accepted"
            cleared += 1

    if cleared:
        store.save_report(character_id, report)

    return {"cleared": cleared, "reviewCount": report.review_count}


@app.post("/api/characters/{character_id}/unmapped")
def update_unmapped(character_id: str, body: UnmappedUpdate) -> dict:
    """Assign a stray fragment to a field, or dismiss it."""
    document = _require(character_id)
    report = _report_or_empty(character_id)
    if report is None:
        raise HTTPException(404, "this character has no extraction report")

    item = next((u for u in report.unmapped if u.id == body.id), None)
    if item is None:
        raise HTTPException(404, f"no unassigned fragment '{body.id}'")

    if body.status == "assigned":
        if not body.assignedTo:
            raise HTTPException(400, "assigning a fragment needs a target pointer")
        try:
            pointer.set_value(document, body.assignedTo, item.text)
        except (KeyError, IndexError, ValueError) as exc:
            raise HTTPException(400, f"cannot write {body.assignedTo}: {exc}") from exc
        item.assignedTo = body.assignedTo
        _save_and_revalidate(character_id, document)
        report = _report_or_empty(character_id)
        item = next(u for u in report.unmapped if u.id == body.id)
        item.assignedTo = body.assignedTo

    item.status = body.status  # type: ignore[assignment]
    store.save_report(character_id, report)

    return _payload(character_id, store.load(character_id))


@app.post("/api/characters/{character_id}/note-pages", status_code=201)
def add_note_page(character_id: str, body: NotePageRequest) -> dict:
    """Append a free-text page.

    Exists as its own endpoint rather than as a pointer edit because the useful version of
    this is one action: a fragment in the tray that belongs in prose rather than in a
    field becomes a note page and leaves the tray together, with no window in which the
    page exists and the fragment is still queued.
    """
    document = _require(character_id)
    report = _report_or_empty(character_id)

    item = None
    if body.fromUnmapped:
        if report is None:
            raise HTTPException(404, "this character has no extraction report")
        item = next((u for u in report.unmapped if u.id == body.fromUnmapped), None)
        if item is None:
            raise HTTPException(404, f"no unassigned fragment '{body.fromUnmapped}'")

    pages = document.setdefault("notePages", [])
    index = len(pages)
    pages.append(
        {
            "title": body.title or (item.source.location if item else None),
            "text": body.text or (item.text if item else None),
            "sourcePdfPage": item.source.pdfPage if item else None,
        }
    )

    finished = _save_and_revalidate(character_id, document)

    if item is not None:
        # Reload: _save_and_revalidate rewrote the report underneath us.
        report = _report_or_empty(character_id)
        stored = next(u for u in report.unmapped if u.id == body.fromUnmapped)
        stored.status = "assigned"
        stored.assignedTo = f"/notePages/{index}/text"
        store.save_report(character_id, report)

    return _payload(character_id, finished)


# --------------------------------------------------------------------------------------
# Source pages, for the evidence crops
# --------------------------------------------------------------------------------------


@app.get("/api/characters/{character_id}/pages/{pdf_page}")
def get_page_image(
    character_id: str,
    pdf_page: int,
    x0: Annotated[float | None, Query()] = None,
    y0: Annotated[float | None, Query()] = None,
    x1: Annotated[float | None, Query()] = None,
    y1: Annotated[float | None, Query()] = None,
) -> Response:
    """A rendered page of the original scan, optionally cropped.

    The crop parameters are what puts a picture of the actual handwriting beside a flagged
    field, which is the difference between "is this right?" and a decision the user can
    actually make.

    Deliberately does not require a character to exist yet: the assignment screen shows
    these thumbnails before there is one.
    """
    path = store.pages_dir(character_id) / f"page-{pdf_page:02d}.png"
    if not path.exists():
        raise HTTPException(404, f"no image for PDF page {pdf_page}")

    if None in (x0, y0, x1, y1):
        return FileResponse(path, media_type="image/png")

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dependency
        return FileResponse(path, media_type="image/png")

    with Image.open(path) as image:
        box = (
            max(0, int(x0)),
            max(0, int(y0)),
            min(image.width, int(x1)),
            min(image.height, int(y1)),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            raise HTTPException(400, "the crop rectangle is empty")
        buffer = io.BytesIO()
        image.crop(box).save(buffer, format="PNG")

    return Response(buffer.getvalue(), media_type="image/png")


# --------------------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------------------


@app.post("/api/import", status_code=201)
async def import_pdf(
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Query()] = None,
) -> dict:
    """Upload a scanned sheet, read it, and propose what each page is.

    Stops short of the reasoning model. What comes back is a *proposal*: one entry per
    page of the upload saying which page of the sheet it looks like. Confirming it (or
    correcting it first) is a second request.

    Splitting the import here is not ceremony. Getting the page assignment wrong maps a
    whole sheet into the wrong fields, and no amount of prompt work fixes it afterwards --
    while a person looking at five thumbnails sees it immediately.
    """
    config = load_config()
    try:
        ocr = build_ocr_provider(config.ocr, cache=config.cache)
        build_reasoning_provider(config.reasoning, cache=config.cache)
    except ProviderError as exc:
        raise HTTPException(503, str(exc)) from exc

    stem = Path(file.filename or "character").stem
    character_id = store.new_id(name or stem)
    directory = store.directory(character_id)
    directory.mkdir(parents=True, exist_ok=True)

    source = store.source_path(character_id)
    source.write_bytes(await file.read())

    prepared = run_prepare(
        source,
        ocr,
        image_dir=store.pages_dir(character_id),
        source_name=file.filename or None,
    )
    store.save_pending(character_id, prepared)

    return _proposal(character_id, prepared)


@app.get("/api/imports")
def list_imports() -> list[dict]:
    """Uploads that have been read but not yet confirmed."""
    return store.list_pending()


@app.get("/api/imports/{character_id}")
def get_import(character_id: str) -> dict:
    """The pending proposal, so reloading the assignment screen does not lose it."""
    prepared = store.load_pending(character_id)
    if prepared is None:
        raise HTTPException(404, f"no import waiting for confirmation as '{character_id}'")
    return _proposal(character_id, prepared)


@app.post("/api/imports/{character_id}/confirm")
def confirm_import(character_id: str, body: ConfirmImport) -> dict:
    """Map the pages as assigned. This is the request that spends money."""
    prepared = store.load_pending(character_id)
    if prepared is None:
        raise HTTPException(404, f"no import waiting for confirmation as '{character_id}'")

    config = load_config()
    try:
        reasoning = build_reasoning_provider(config.reasoning, cache=config.cache)
    except ProviderError as exc:
        raise HTTPException(503, str(exc)) from exc

    assignment = _assignment_from(body.assignment) if body.assignment is not None else None
    if assignment is not None:
        unknown = set(assignment) - {p.pdf_page for p in prepared.ingested.pages}
        if unknown:
            raise HTTPException(400, f"the upload has no page {sorted(unknown)[0]}")

    outcome = run_finish(prepared, reasoning, assignment=assignment)
    store.save(character_id, outcome.character)
    store.save_report(character_id, outcome.report)
    store.clear_pending(character_id)

    return _payload(character_id, outcome.character)


@app.delete("/api/imports/{character_id}", status_code=204)
def cancel_import(character_id: str) -> Response:
    """Abandon an import that has not been confirmed."""
    if store.load_pending(character_id) is None:
        raise HTTPException(404, f"no import waiting for confirmation as '{character_id}'")
    store.delete(character_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------


@app.post("/api/characters/{character_id}/export")
def export_pdf(
    character_id: str,
    page_size: Annotated[str, Query()] = "native",
    request: Request = None,  # type: ignore[assignment]
) -> Response:
    """Render this character's print route to PDF.

    Chromium is pointed back at this same server, so the export is a photograph of the
    editor rather than a second implementation of the layout.
    """
    _require(character_id)

    from .export.pdf import ExportError, ExportOptions, render_url_to_pdf

    base = str(request.base_url).rstrip("/") if request else "http://127.0.0.1:8000"
    destination = store.directory(character_id) / f"{character_id}.pdf"

    try:
        render_url_to_pdf(
            f"{base}/print/{character_id}",
            destination,
            ExportOptions(page_size=page_size),
        )
    except ExportError as exc:
        raise HTTPException(503, str(exc)) from exc

    return FileResponse(
        destination,
        media_type="application/pdf",
        filename=f"{character_id}.pdf",
    )


# --------------------------------------------------------------------------------------
# Reference data for the editor
# --------------------------------------------------------------------------------------


@app.get("/api/reference")
def reference() -> dict:
    """Everything printed on the sheet, so the frontend need not restate it.

    One source of truth for the skill list, its column layout, the armour locations and
    the minor-power table. A duplicate of this in TypeScript would drift.
    """
    return {
        "characteristics": [
            {"key": c.key, "label": c.label, "abbreviation": c.abbreviation}
            for c in K.CHARACTERISTICS
        ],
        "skills": [
            {
                "key": s.key,
                "label": s.printed_label,
                "characteristic": s.characteristic,
                "isBasic": s.is_basic,
                "isGroup": s.is_group,
                "column": s.column,
                "writeInLines": s.write_in_lines,
            }
            for s in K.SKILLS
        ],
        "groupSkillExamples": K.GROUP_SKILL_EXAMPLES,
        "proficiencyColumns": K.PROFICIENCY_COLUMNS,
        "armourLocations": [
            {
                "key": a.key,
                "location": a.location,
                "hitRoll": a.hit_roll,
            }
            for a in K.ARMOUR_LOCATIONS
        ],
        "weaponTraining": {
            "basicAndPistol": [
                {"key": k, "label": label}
                for k, label in zip(
                    K.BASIC_AND_PISTOL_TRAINING_KEYS,
                    K.BASIC_AND_PISTOL_TRAINING_LABELS,
                    strict=True,
                )
            ],
            "melee": [
                {"key": k, "label": label}
                for k, label in zip(K.MELEE_TRAINING_KEYS, K.MELEE_TRAINING_LABELS, strict=True)
            ],
        },
        "minorPowers": [
            {
                "name": p.name,
                "threshold": p.threshold,
                "focus": p.focus,
                "sustain": p.sustain,
            }
            for p in K.MINOR_POWERS
        ],
        "printedCapacity": K.PRINTED_CAPACITY,
        "printedRankBlocks": K.PRINTED_RANK_BLOCKS,
        "page": {"widthMm": K.PAGE_WIDTH_MM, "heightMm": K.PAGE_HEIGHT_MM},
    }


@app.get("/api/blank")
def blank() -> dict:
    """An empty sheet, for the editor to start a new character from."""
    return blank_character().to_json_dict()


@app.get("/api/assets/armour-silhouette.png")
def armour_silhouette() -> Response:
    if not ARMOUR_SILHOUETTE.exists():
        raise HTTPException(
            404,
            "assets/armour-silhouette.png is missing. Generate it with: "
            "python -m scribe40k.tools.build_assets path/to/blank-template.pdf",
        )
    return FileResponse(ARMOUR_SILHOUETTE, media_type="image/png")


@app.get("/api/health")
def health() -> dict:
    """Configuration and readiness.

    Reports whether each stage's credential is actually present, because "the key is in
    my .env but nothing happens" is otherwise invisible until an import fails. Only the
    variable name and a boolean are returned -- never a key.
    """
    config = load_config()

    def stage(name: str, cfg) -> dict:
        needs_key = cfg.provider not in OFFLINE_PROVIDERS
        return {
            "provider": cfg.provider,
            "model": cfg.model,
            "envVar": cfg.env_var if needs_key else None,
            "credentialFound": bool(cfg.api_key()) if needs_key else True,
            "stage": name,
        }

    ocr = stage("ocr", config.ocr)
    reasoning = stage("reasoning", config.reasoning)
    ready = ocr["credentialFound"] and reasoning["credentialFound"]

    return {
        "status": "ok" if ready else "missing_credentials",
        "ocr": ocr,
        "reasoning": reasoning,
        "envFileLoaded": bool(LOADED_ENV_KEYS),
        "envFileKeys": LOADED_ENV_KEYS,
        "dataRoot": str(store.root),
        "assetsBuilt": ARMOUR_SILHOUETTE.exists(),
    }


# --------------------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------------------


@app.exception_handler(404)
async def spa_fallback(request, exc):  # noqa: ANN001
    """Serve the editor for any non-API path, so client-side routes work on reload."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=404)

    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)

    return JSONResponse(
        {
            "detail": (
                "The frontend has not been built. Run: cd frontend && npm install && npm run build"
            ),
        },
        status_code=503,
    )


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
