"""The whole extraction, end to end.

    PDF -> ingest -> classify -> OCR -> [confirm] -> map -> derive -> validate -> character

Each stage is testable on its own; this module is the wiring. It also enforces the rule
that makes the pipeline safe to run on a messy document: nothing that was read from the
paper is discarded silently. A page that could not be classified, a section job that
failed, a fragment with nowhere to go -- each ends up in the report where the user can see
it.

The pipeline splits in two at ``[confirm]``, and the split is where the money is. Reading
a PDF and transcribing it is cheap and reversible; mapping it is neither. So
:func:`prepare` does everything up to and including OCR and hands back a *proposal* --
which page of the upload is which page of the sheet -- and :func:`finish` takes an
assignment, the user's or the proposal unaltered, and does the expensive half. A machine
that has guessed wrong about which page is which will map a whole sheet into the wrong
fields, and the person looking at the thumbnails is the one who can see it in a second.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..blank import blank_character
from ..llm.base import OcrPage, OcrProvider, PageImage, ReasoningProvider
from ..llm.offline import PassthroughOcr
from .ingest import (
    IngestedPage,
    IngestResult,
    PageKind,
    TextSource,
    ingest,
    load_page_text_signatures,
)
from .mapper import map_sheet
from .page_text import identify_pages
from .report import (
    Evidence,
    ExtractionReport,
    Flag,
    ModelRef,
    PageRecord,
    SourceRef,
)
from .sections import ALL_SECTIONS, Section
from .validate import dedupe_flags, validate_document

#: What a page of the upload may be assigned to: one of the five sheet pages, a note page,
#: or nothing at all.
PageTarget = int | Literal["notes", "skip"]


@dataclass
class ExtractionOutcome:
    character: dict
    report: ExtractionReport

    @property
    def review_count(self) -> int:
        return self.report.review_count


@dataclass
class PreparedPages:
    """A PDF read, classified and transcribed, but not yet mapped.

    Everything here was produced without a reasoning call, and can be thrown away and
    rebuilt from the same PDF for the price of the OCR (which is cached, so usually
    nothing). It exists so the page assignment can be shown to a human before the
    expensive half of the pipeline commits to it.
    """

    source_name: str
    ingested: IngestResult
    #: Every non-blank page's transcription, keyed by PDF page.
    transcriptions: dict[int, OcrPage]
    #: What the machine thinks each page is. The starting point for the user's answer.
    proposal: dict[int, PageTarget]

    def to_json_dict(self) -> dict:
        """Serialised to ``pending.json`` so the confirmation can be a separate request."""
        return {
            "version": 1,
            "sourceName": self.source_name,
            "sourcePdf": str(self.ingested.source_pdf),
            "fileHash": self.ingested.file_hash,
            "pages": [
                {
                    "pdfPage": page.pdf_page,
                    "kind": page.kind.value,
                    "sheetPage": page.sheet_page,
                    "textSource": page.text_source.value,
                    "matchScore": page.match_score,
                    "ink": page.ink,
                    "matchedBy": page.matched_by,
                    "textScore": page.text_score,
                    "note": page.note,
                    "embeddedText": page.embedded_text,
                    "imagePath": str(page.image_path) if page.image_path else None,
                    "imageSize": list(page.image_size) if page.image_size else None,
                }
                for page in self.ingested.pages
            ],
            "transcriptions": {
                str(pdf_page): {
                    "text": ocr.text,
                    "provider": ocr.provider,
                    "model": ocr.model,
                    "cached": ocr.cached,
                    "error": ocr.error,
                }
                for pdf_page, ocr in sorted(self.transcriptions.items())
            },
            "proposal": {str(page): target for page, target in sorted(self.proposal.items())},
        }

    @classmethod
    def from_json_dict(cls, data: dict) -> PreparedPages:
        result = IngestResult(
            source_pdf=Path(data["sourcePdf"]),
            file_hash=data["fileHash"],
            pages=[
                IngestedPage(
                    pdf_page=entry["pdfPage"],
                    kind=PageKind(entry["kind"]),
                    sheet_page=entry["sheetPage"],
                    text_source=TextSource(entry["textSource"]),
                    match_score=entry["matchScore"],
                    ink=entry["ink"],
                    matched_by=entry["matchedBy"],
                    text_score=entry["textScore"],
                    embedded_text=entry.get("embeddedText", ""),
                    image_path=Path(entry["imagePath"]) if entry["imagePath"] else None,
                    image_size=tuple(entry["imageSize"]) if entry["imageSize"] else None,
                    note=entry.get("note", ""),
                )
                for entry in data["pages"]
            ],
        )
        return cls(
            source_name=data["sourceName"],
            ingested=result,
            transcriptions={
                int(pdf_page): OcrPage(
                    pdf_page=int(pdf_page),
                    sheet_page=None,
                    text=entry["text"],
                    provider=entry["provider"],
                    model=entry["model"],
                    cached=entry["cached"],
                    error=entry["error"],
                )
                for pdf_page, entry in data["transcriptions"].items()
            },
            proposal={int(page): target for page, target in data["proposal"].items()},
        )


def _image_of(page: IngestedPage) -> PageImage | None:
    if page.image_path is None:
        return None
    width, height = page.image_size or (0, 0)
    return PageImage(
        pdf_page=page.pdf_page,
        path=page.image_path,
        sheet_page=page.sheet_page,
        width=width,
        height=height,
    )


def _page_images(result: IngestResult) -> dict[int, list[PageImage]]:
    """Rendered images of the matched sheet pages, grouped by sheet page.

    A list rather than one image per page, because a sheet page can arrive as more than
    one page of the upload.
    """
    images: dict[int, list[PageImage]] = {}
    for page in result.sheet_pages:
        image = _image_of(page)
        if image is None or page.sheet_page is None:
            continue
        images.setdefault(page.sheet_page, []).append(image)
    return images


def _transcribe(
    result: IngestResult,
    ocr_provider: OcrProvider,
) -> dict[int, OcrPage]:
    """Transcribe the sheet pages, keyed by PDF page.

    Keyed by PDF page rather than sheet page because that is the only key that is unique:
    two uploaded pages can be the same sheet page. Grouping happens later, once the user
    has confirmed which is which.
    """
    pages = [image for page in result.sheet_pages if (image := _image_of(page))]
    return _transcribe_images(result, pages, ocr_provider)


def _transcribe_images(
    result: IngestResult,
    pages: list[PageImage],
    ocr_provider: OcrProvider,
) -> dict[int, OcrPage]:
    """Read the given pages, skipping OCR where the PDF already carries the text."""
    if not pages:
        return {}

    from_layer = [
        page
        for page in pages
        if (ingested := result.page_for_pdf(page.pdf_page))
        and ingested.text_source is TextSource.TEXT_LAYER
    ]
    from_layer_pages = {page.pdf_page for page in from_layer}
    needs_ocr = [page for page in pages if page.pdf_page not in from_layer_pages]

    transcribed: dict[int, OcrPage] = {}
    if from_layer:
        passthrough = PassthroughOcr()
        passthrough.load_from_ingest(result.pages)
        for page in passthrough.transcribe(from_layer):
            transcribed[page.pdf_page] = page
    if needs_ocr:
        for page in ocr_provider.transcribe(needs_ocr):
            transcribed[page.pdf_page] = page

    return transcribed


def _transcribe_extra_pages(
    result: IngestResult,
    ocr_provider: OcrProvider,
) -> dict[int, OcrPage]:
    """Transcribe the pages that are *not* part of the sheet, keyed by PDF page.

    These are the pages a player attaches to their sheet: session notes, a background
    write-up, a page of scribbles. They used to be reported as an unassigned fragment
    reading "(PDF page 6)" -- a way of saying "there was something here" without saying
    what. Reading them costs one more OCR call per page and turns them into note pages the
    user can actually see.
    """
    pages = [
        PageImage(pdf_page=p.pdf_page, path=p.image_path, sheet_page=None)
        for p in result.unrecognised_pages
        if p.image_path is not None
    ]
    return _transcribe_images(result, pages, ocr_provider)


def _recover_pages_from_text(
    result: IngestResult,
    extra: dict[int, OcrPage],
) -> dict[int, tuple[int, float]]:
    """Reclassify unrecognised pages using what OCR read on them.

    The image fingerprint assumes a flat, square-on page. A photograph of a sheet -- tilted,
    keystoned, lit from one side, with a desk visible around the paper -- defeats it
    completely: on a three-page phone photo of this very sheet, every page scored below
    0.29 against every template page and all three were filed as notes.

    The printed headings survive the photograph perfectly well, though, and OCR reads them.
    This pass gives those pages a second chance against the template's per-page vocabulary,
    and mutates the ingest result for the ones it can place. It only ever runs on pages the
    image matcher gave up on, so a clean scan reaches mapping exactly as before.
    """
    signatures = load_page_text_signatures()
    if not signatures:
        return {}

    candidates = {
        page.pdf_page: transcription.text
        for page in result.unrecognised_pages
        if (transcription := extra.get(page.pdf_page)) and transcription.ok
    }
    if not candidates:
        return {}

    placed = {p.pdf_page: p.sheet_page for p in result.sheet_pages if p.sheet_page}
    identified = identify_pages(candidates, signatures, already_placed=placed)

    for pdf_page, (sheet_page, recall) in identified.items():
        page = result.page_for_pdf(pdf_page)
        if page is None:
            continue
        page.kind = PageKind.SHEET
        page.sheet_page = sheet_page
        page.matched_by = "text"
        page.text_score = recall
        page.note = (
            f"The image of this page matched no template page (best correlation "
            f"{page.match_score:.2f}), but its transcription carries "
            f"{recall:.0%} of the words printed on sheet page {sheet_page}. Photographs "
            f"of a sheet often land here: the paper is tilted and lit unevenly, which the "
            f"image match cannot see past but OCR can."
        )

    return identified


def _note_pages(result: IngestResult, extra: dict[int, OcrPage]) -> list[dict]:
    """One note page per unrecognised page, in PDF order."""
    notes = []
    for page in result.unrecognised_pages:
        transcription = extra.get(page.pdf_page)
        text = transcription.text.strip() if transcription and transcription.ok else ""
        notes.append(
            {
                "title": f"From PDF page {page.pdf_page}",
                "text": text or None,
                "sourcePdfPage": page.pdf_page,
            }
        )
    return notes


def _page_records(result: IngestResult, transcriptions: dict[int, OcrPage]) -> list[PageRecord]:
    records = []
    for page in result.pages:
        ocr = transcriptions.get(page.pdf_page)
        records.append(
            PageRecord(
                pdfPage=page.pdf_page,
                kind=page.kind.value,
                sheetPage=page.sheet_page,
                textSource=page.text_source.value,
                matchScore=round(page.match_score, 4),
                matchedBy=page.matched_by,
                textScore=round(page.text_score, 4) if page.matched_by == "text" else None,
                ink=round(page.ink, 5),
                imagePath=page.image_path.name if page.image_path else None,
                note=page.note or None,
                ocrError=ocr.error if ocr else None,
            )
        )
    return records


def _structural_flags(result: IngestResult, transcriptions: dict[int, OcrPage]) -> list[Flag]:
    """Problems with the document itself, rather than with any one field."""
    flags: list[Flag] = []

    # Every page becoming a note page is not five separate notices about five note pages;
    # it is one thing having gone wrong, and saying so once is the difference between a
    # user who knows to re-scan and one who thinks the tool ate their character.
    if not result.sheet_pages and result.unrecognised_pages:
        flags.append(
            Flag(
                pointer="",
                severity="error",
                rule="ingest.nothing_recognised",
                message=(
                    f"None of the {len(result.pages)} page(s) in this PDF could be "
                    "identified as part of a Dark Heresy character sheet, either from the "
                    "page image or from what was read on it, so the sheet itself is empty "
                    "and everything found has been put on note pages. If this really is a "
                    "character sheet, the most likely causes are a photograph taken at an "
                    "angle, a different edition of the form, or a scan too dark to read."
                ),
                actual=len(result.pages),
            )
        )

    for missing in result.missing_sheet_pages:
        flags.append(
            Flag(
                pointer="",
                severity="warning",
                rule="ingest.missing_sheet_page",
                message=(
                    f"Sheet page {missing} was not found in the uploaded PDF, so the fields "
                    f"printed on it are empty. Check the scan includes every page."
                ),
                actual=missing,
            )
        )

    for pdf_page, transcription in sorted(transcriptions.items()):
        if transcription.error:
            where = result.page_for_pdf(pdf_page)
            what = (
                f"Sheet page {where.sheet_page}"
                if where and where.sheet_page
                else f"PDF page {pdf_page}"
            )
            flags.append(
                Flag(
                    pointer="",
                    severity="error",
                    rule="ocr.page_failed",
                    message=f"{what} could not be transcribed: {transcription.error}",
                )
            )

    return flags


def _note_page_flags(result: IngestResult, notes: list[dict]) -> list[Flag]:
    """Say where each note page came from, and flag the ones that came out empty.

    A note page is not a problem to be fixed, so this is ``info`` and not a warning. But
    it should not appear from nowhere either: the user needs to know that PDF page 6 was
    not part of the sheet, that both the image match and the transcription were given a
    chance to place it, and that its text is now here.
    """
    flags: list[Flag] = []
    for index, note in enumerate(notes):
        pdf_page = note["sourcePdfPage"]
        page = result.page_for_pdf(pdf_page)
        reason = (page.note if page else "") or (
            "neither its image nor its transcription matched any page of the character sheet"
        )
        empty = not note["text"]
        flags.append(
            Flag(
                pointer=f"/notePages/{index}/text",
                severity="warning" if empty else "info",
                rule="ingest.note_page",
                message=(
                    f"PDF page {pdf_page} is not part of the character sheet ({reason}), "
                    + (
                        "and nothing could be read from it. Open the scan to see what is there."
                        if empty
                        else "so what was read from it was put on this note page."
                    )
                ),
                evidence=Evidence(pdfPage=pdf_page),
            )
        )
    return flags


def prepare(
    pdf_path: Path,
    ocr_provider: OcrProvider,
    *,
    image_dir: Path,
    source_name: str | None = None,
    progress=None,
) -> PreparedPages:
    """Read, classify and transcribe a PDF, and propose what each page is.

    The cheap half of the pipeline: no reasoning call is made, so the result can be shown
    to the user, argued with, and thrown away.
    """

    def say(message: str) -> None:
        if progress:
            progress(message)

    pdf_path = Path(pdf_path)

    say("Reading and classifying pages")
    ingested = ingest(pdf_path, image_dir)
    matched = len(ingested.sheet_pages)
    blanks = sum(1 for p in ingested.pages if p.kind is PageKind.BLANK)
    say(
        f"  {len(ingested.pages)} pages: {matched} matched the sheet, "
        f"{blanks} blank, {len(ingested.unrecognised_pages)} unrecognised"
    )

    say(f"Transcribing {len(ingested.sheet_pages)} page(s)")
    transcriptions = _transcribe(ingested, ocr_provider)
    cached = sum(1 for p in transcriptions.values() if p.cached)
    if cached:
        say(f"  {cached} of {len(transcriptions)} came from the cache")

    if ingested.unrecognised_pages:
        say(
            f"  {len(ingested.unrecognised_pages)} page(s) matched no template page; "
            "transcribing them to see what they are"
        )
    extra_pages = _transcribe_extra_pages(ingested, ocr_provider)

    # Second chance for anything the image match could not place. A note page is meant to
    # be where content ends up when nothing else fits, not the first thing tried.
    recovered = _recover_pages_from_text(ingested, extra_pages)
    for pdf_page, (sheet_page, recall) in sorted(recovered.items()):
        say(f"  PDF page {pdf_page} reads as sheet page {sheet_page} ({recall:.0%} of its words)")

    transcriptions.update(extra_pages)

    return PreparedPages(
        # The name the user uploaded, not the name it was stored under: the store calls
        # every scan "source.pdf", which is no help at all on the assignment screen.
        source_name=source_name or pdf_path.name,
        ingested=ingested,
        transcriptions=transcriptions,
        proposal=propose_assignment(ingested),
    )


def propose_assignment(result: IngestResult) -> dict[int, PageTarget]:
    """What the machine believes each page of the upload is.

    Blank pages are proposed as skipped rather than left out, so that every page of the
    document appears on the assignment screen. A duplex back that the user knows is not
    blank is exactly the sort of thing they should be able to drag back in.
    """
    return {page.pdf_page: _target_of(page) for page in result.pages}


def apply_assignment(result: IngestResult, assignment: dict[int, PageTarget]) -> None:
    """Overwrite the classification with what the user decided.

    Pages the assignment does not mention keep what they had, so a partial answer is a
    correction rather than a replacement.
    """
    for page in result.pages:
        target = assignment.get(page.pdf_page)
        if target is None:
            continue
        moved = target != _target_of(page)

        if isinstance(target, int):
            page.kind = PageKind.SHEET
            page.sheet_page = target
        elif target == "notes":
            page.kind = PageKind.UNRECOGNISED
            page.sheet_page = None
        else:
            page.kind = PageKind.BLANK
            page.sheet_page = None

        if moved:
            page.matched_by = "user"
            page.note = "Assigned by hand, overriding what the page was classified as."


def _target_of(page: IngestedPage) -> PageTarget:
    if page.kind is PageKind.SHEET and page.sheet_page:
        return page.sheet_page
    return "skip" if page.kind is PageKind.BLANK else "notes"


def sheet_pages_in_parts(assignment: dict[int, PageTarget]) -> dict[int, int]:
    """Sheet pages covered by more than one page of the upload, and by how many.

    This used to be an error -- a sheet has one of each page, so two uploads claiming to
    be page 2 looked like a mistake. It is not: a character with more gear than the printed
    lines hold spills onto a further page when exported, and a scan of that printout has
    two pages where the form has one. They are read together as one page.
    """
    counts: dict[int, int] = {}
    for target in assignment.values():
        if isinstance(target, int):
            counts[target] = counts.get(target, 0) + 1
    return {page: count for page, count in sorted(counts.items()) if count > 1}


def finish(
    prepared: PreparedPages,
    reasoning_provider: ReasoningProvider,
    *,
    assignment: dict[int, PageTarget] | None = None,
    sections: tuple[Section, ...] = ALL_SECTIONS,
    progress=None,
) -> ExtractionOutcome:
    """Map an assignment of pages into a character document.

    The expensive half. ``assignment`` defaults to the proposal, which is what the CLI and
    a straight-through import use.
    """

    def say(message: str) -> None:
        if progress:
            progress(message)

    ingested = prepared.ingested
    apply_assignment(ingested, assignment if assignment is not None else prepared.proposal)

    images = _page_images(ingested)
    ocr_pages: dict[int, list[OcrPage]] = {}
    for page in ingested.sheet_pages:
        transcription = prepared.transcriptions.get(page.pdf_page)
        if transcription and page.sheet_page:
            transcription.sheet_page = page.sheet_page
            ocr_pages.setdefault(page.sheet_page, []).append(transcription)

    extra_pages = {
        page.pdf_page: transcription
        for page in ingested.unrecognised_pages
        if (transcription := prepared.transcriptions.get(page.pdf_page))
    }

    say(f"Mapping {len(sections)} section(s)")
    document = blank_character().to_json_dict()
    model_flags, unmapped, records = map_sheet(
        document, reasoning_provider, ocr_pages, images, sections=sections
    )
    for record in records:
        if record.status != "ok":
            say(f"  {record.name}: {record.status} -- {record.error}")

    notes = _note_pages(ingested, extra_pages)
    document["notePages"] = notes

    say("Deriving and validating")
    document, rule_flags = validate_document(document)

    flags = dedupe_flags(
        [
            *_structural_flags(ingested, prepared.transcriptions),
            *_note_page_flags(ingested, notes),
            *model_flags,
            *rule_flags,
        ]
    )

    report = ExtractionReport(
        source=SourceRef(
            filename=prepared.source_name,
            fileHash=ingested.file_hash,
            pageCount=len(ingested.pages),
        ),
        models={
            "ocr": ModelRef(provider=_ocr_provider_name(prepared), model=_ocr_model(prepared)),
            "reasoning": ModelRef(
                provider=reasoning_provider.name,
                model=reasoning_provider.model,
                supportsVision=reasoning_provider.supports_vision,
            ),
        },
        pages=_page_records(ingested, prepared.transcriptions),
        flags=flags,
        unmapped=unmapped,
        sections=records,
    )

    say(
        f"Done: {report.review_count} field(s) need review, "
        f"{len(report.unmapped)} unassigned fragment(s)"
    )

    if not reasoning_provider.supports_vision:
        say(
            "  Note: the reasoning model is text-only, so ticked boxes (skills, weapon "
            "training) are likely under-reported."
        )

    return ExtractionOutcome(character=document, report=report)


def _ocr_provider_name(prepared: PreparedPages) -> str:
    """Read back off the transcriptions, since ``finish`` never sees the OCR provider."""
    return next((p.provider for p in prepared.transcriptions.values() if p.provider), "unknown")


def _ocr_model(prepared: PreparedPages) -> str:
    return next((p.model for p in prepared.transcriptions.values() if p.model), "unknown")


def extract(
    pdf_path: Path,
    ocr_provider: OcrProvider,
    reasoning_provider: ReasoningProvider,
    *,
    image_dir: Path,
    sections: tuple[Section, ...] = ALL_SECTIONS,
    progress=None,
) -> ExtractionOutcome:
    """Both halves in one call, taking the proposed page assignment as read.

    What the CLI runs, and what an import does when nobody intervenes.
    """
    prepared = prepare(pdf_path, ocr_provider, image_dir=image_dir, progress=progress)
    return finish(prepared, reasoning_provider, sections=sections, progress=progress)
