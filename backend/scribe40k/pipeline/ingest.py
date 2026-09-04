"""Stage 0-1: turn a PDF into classified page images.

Nothing here costs an API call. By the time this stage is done we know, for every page in
the document, whether it is blank, which sheet page it represents, and whether its text can
be read straight out of the PDF or has to go through OCR. Everything downstream operates on
that classification rather than on raw page indices.

This matters more than it might sound. The calibration sample is a 12-page duplex scan
whose sheet pages are 1, 3, 5, 7 and 9, with blank backs between them and two pages of
handwritten session notes at the end. A pipeline that assumed "PDF page N is sheet page N"
would misfile every section and burn five OCR calls on blank paper.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
import pymupdf

from .. import constants as K
from ..paths import PAGE_FINGERPRINTS
from .fingerprint import correlation, fingerprint_from_gray, ink_coverage

#: Resolution for the images handed to OCR and shown as review crops.
RENDER_DPI = 200

#: Resolution for classification only. Coarse on purpose: fast, and it suppresses
#: handwriting before the fingerprint even sees it.
CLASSIFY_DPI = 50

#: Below this fraction of dark pixels a page carries nothing worth looking at.
BLANK_INK_THRESHOLD = 0.01

#: A fingerprint must correlate at least this well to claim a sheet page.
MATCH_THRESHOLD = 0.55

#: Characters of extractable text above which a page is taken to have a real text layer,
#: making OCR unnecessary. The blank template alone carries 4,395 characters on page 1, so
#: this separates "produced digitally" from "a scan with a few stray artefacts".
TEXT_LAYER_THRESHOLD = 200

#: Characters of *added* text -- beyond the template's own printed labels -- below which a
#: page is reported as carrying no player data. This is a note for the user, not an OCR
#: decision: a digitally-produced sheet that is simply empty still needs no transcribing.
ADDED_TEXT_THRESHOLD = 40


class PageKind(StrEnum):
    """What a page in the uploaded PDF turned out to be."""

    #: Matched a template page; carries character data.
    SHEET = "sheet"
    #: Effectively no ink. A duplex back, or a separator.
    BLANK = "blank"
    #: Has content but matches no template page. Session notes, a map, an errata page.
    UNRECOGNISED = "unrecognised"


class TextSource(StrEnum):
    """Where this page's text will come from."""

    #: The PDF carries a real text layer; no OCR call needed.
    TEXT_LAYER = "text_layer"
    #: A scan. Must go through an OCR provider.
    OCR = "ocr"


@dataclass
class IngestedPage:
    """One page of the uploaded document, after classification."""

    #: 1-based index in the uploaded PDF.
    pdf_page: int
    kind: PageKind
    #: 1-5 when ``kind`` is SHEET, otherwise ``None``.
    sheet_page: int | None
    text_source: TextSource
    #: Correlation with the best-matching template page.
    match_score: float
    #: Fraction of dark pixels.
    ink: float
    #: Text lifted from the PDF's own text layer, if any.
    embedded_text: str = ""
    #: Where the rendered page image was written.
    image_path: Path | None = None
    #: Rendered image size in pixels, for mapping bounding boxes onto crops.
    image_size: tuple[int, int] | None = None
    #: Human-readable explanation, surfaced in the UI for unrecognised pages.
    note: str = ""


@dataclass
class IngestResult:
    source_pdf: Path
    #: SHA-256 of the uploaded file, used as the OCR cache key.
    file_hash: str
    pages: list[IngestedPage] = field(default_factory=list)

    @property
    def sheet_pages(self) -> list[IngestedPage]:
        """The pages that carry character data, in sheet order."""
        return sorted(
            (p for p in self.pages if p.kind is PageKind.SHEET),
            key=lambda p: p.sheet_page or 0,
        )

    @property
    def unrecognised_pages(self) -> list[IngestedPage]:
        return [p for p in self.pages if p.kind is PageKind.UNRECOGNISED]

    def page_for(self, sheet_page: int) -> IngestedPage | None:
        return next((p for p in self.pages if p.sheet_page == sheet_page), None)

    @property
    def missing_sheet_pages(self) -> list[int]:
        found = {p.sheet_page for p in self.pages if p.kind is PageKind.SHEET}
        return [n for n in range(1, K.SHEET_PAGE_COUNT + 1) if n not in found]

    def summary(self) -> str:
        parts = []
        for page in self.pages:
            label = (
                f"sheet {page.sheet_page}" if page.kind is PageKind.SHEET else str(page.kind.value)
            )
            parts.append(f"p{page.pdf_page}={label}")
        return ", ".join(parts)


# --------------------------------------------------------------------------------------


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_template_profile() -> dict:
    if not PAGE_FINGERPRINTS.exists():
        raise FileNotFoundError(
            f"{PAGE_FINGERPRINTS} is missing. Generate it with:\n"
            "    python -m scribe40k.tools.build_assets path/to/blank-template.pdf"
        )
    return json.loads(PAGE_FINGERPRINTS.read_text(encoding="utf-8"))


def load_template_fingerprints() -> dict[int, list[np.ndarray]]:
    """Load the fingerprints written by ``scribe40k.tools.build_assets``.

    A sheet page may have several fingerprints -- one per *layout variant*. The original
    Games Workshop template is one variant; this application's own HTML rendering of the
    same page is another. They are lookalikes, not pixel twins (different fonts, slightly
    different spacing), and correlate at only about 0.38 with each other, so without this
    a sheet exported by this tool could not be imported back into it.
    """
    data = _load_template_profile()
    variants: dict[int, list[np.ndarray]] = {}
    for entry in data["pages"]:
        vector = np.asarray(entry["vector"], dtype=np.float32)
        variants.setdefault(entry["sheetPage"], []).append(vector)
    return variants


def load_boilerplate_tokens() -> frozenset[str]:
    """The template's own printed vocabulary, baked into the committed asset.

    Kept alongside the fingerprints so classification works without the copyrighted
    template PDF being present.
    """
    return frozenset(_load_template_profile().get("boilerplateTokens", ()))


def _grayscale(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def boilerplate_tokens(text: str) -> set[str]:
    """Split text into the normalised tokens used for boilerplate comparison."""
    return set(" ".join(text.split()).lower().split())


def added_text_length(page_text: str, template_tokens: frozenset[str]) -> int:
    """How much text a page carries beyond the template's own printed labels.

    A digitally-filled sheet is usually the template with values typed on top, so its text
    layer repeats every printed label. Subtracting the template's vocabulary leaves only
    what the player added, which is what actually decides whether OCR is needed.
    """
    tokens = " ".join(page_text.split()).lower().split()
    return sum(len(tok) for tok in tokens if tok not in template_tokens)


def assign_sheet_pages(
    candidates: dict[int, np.ndarray],
    templates: dict[int, list[np.ndarray] | np.ndarray],
) -> dict[int, tuple[int | None, float, str]]:
    """Match inked pages to template pages, one-to-one, best correlation first.

    Deciding each page independently is not good enough. Template pages 4 and 5 are both
    "PSYCHIC POWERS" and share nearly all their furniture, so in the calibration sample
    sheet page 4 correlates 0.63 with template 4 and 0.60 with template 5 -- a coin flip.
    But sheet page 5 correlates 0.97 with template 5, and a sheet has only one of each
    page. Letting confident matches claim their template first resolves the weak one by
    elimination.

    Returns ``{pdf_page: (sheet_page | None, score, note)}``.

    ``candidates`` maps PDF page number to fingerprint, and must already exclude blanks.
    """

    def best_against(vector: np.ndarray, reference) -> float:
        """A page matches a sheet page if it matches *any* of its layout variants."""
        variants = reference if isinstance(reference, list) else [reference]
        return max((correlation(vector, variant) for variant in variants), default=-1.0)

    pairs = sorted(
        (
            (best_against(vector, reference), pdf_page, sheet_page)
            for pdf_page, vector in candidates.items()
            for sheet_page, reference in templates.items()
        ),
        reverse=True,
    )

    best_score: dict[int, tuple[float, int]] = {}
    for score, pdf_page, sheet_page in pairs:
        if pdf_page not in best_score:
            best_score[pdf_page] = (score, sheet_page)

    assigned: dict[int, tuple[int | None, float, str]] = {}
    claimed_sheets: dict[int, int] = {}

    for score, pdf_page, sheet_page in pairs:
        if pdf_page in assigned or sheet_page in claimed_sheets:
            continue
        if score < MATCH_THRESHOLD:
            continue
        assigned[pdf_page] = (sheet_page, score, "")
        claimed_sheets[sheet_page] = pdf_page

    # Anything left over either matched nothing, or wanted a page already taken by a more
    # confident candidate. Both become unrecognised, with an explanation.
    for pdf_page in candidates:
        if pdf_page in assigned:
            continue
        score, wanted = best_score.get(pdf_page, (0.0, 0))
        if score < MATCH_THRESHOLD:
            note = (
                f"Has content but matches no template page (best correlation {score:.2f} "
                f"against sheet page {wanted}). Kept as an extra page."
            )
        else:
            note = (
                f"Best match was sheet page {wanted} ({score:.2f}), but PDF page "
                f"{claimed_sheets[wanted]} matched it more strongly. Possibly a duplicate "
                f"or a second copy of the sheet."
            )
        assigned[pdf_page] = (None, score, note)

    return assigned


def ingest(
    pdf_path: Path,
    image_dir: Path | None = None,
    *,
    render_dpi: int = RENDER_DPI,
) -> IngestResult:
    """Rasterise and classify every page of an uploaded sheet PDF.

    ``image_dir`` receives one PNG per non-blank page, named by PDF page number. Blank
    pages are skipped: nothing downstream, including the review UI, has a use for them.
    """
    pdf_path = Path(pdf_path)
    templates = load_template_fingerprints()
    template_tokens = load_boilerplate_tokens()

    if image_dir is not None:
        image_dir.mkdir(parents=True, exist_ok=True)

    result = IngestResult(source_pdf=pdf_path, file_hash=file_hash(pdf_path))

    with pymupdf.open(pdf_path) as doc:
        # Pass 1: measure every page, and fingerprint the ones with ink on them.
        candidates: dict[int, np.ndarray] = {}
        for index in range(doc.page_count):
            page = doc[index]
            gray = _grayscale(page, CLASSIFY_DPI)
            ink = ink_coverage(gray)
            blank = ink < BLANK_INK_THRESHOLD

            embedded = page.get_text("text") or ""
            # Two independent questions, deliberately not conflated:
            #   * Does this page have a text layer? -> whether OCR is needed at all.
            #   * Does it carry any player data?    -> whether it is worth reading.
            # Using the second to answer the first sent digitally-produced but lightly
            # filled pages through OCR for no reason.
            has_text_layer = len(embedded.strip()) >= TEXT_LAYER_THRESHOLD
            added = added_text_length(embedded, template_tokens)
            source = TextSource.TEXT_LAYER if has_text_layer else TextSource.OCR

            note = ""
            if blank:
                note = "No significant ink; treated as a blank page."
            elif has_text_layer and added < ADDED_TEXT_THRESHOLD:
                note = (
                    "This page has a text layer but nothing beyond the form's printed "
                    "labels, so it appears to be an unfilled sheet."
                )

            result.pages.append(
                IngestedPage(
                    pdf_page=index + 1,
                    kind=PageKind.BLANK if blank else PageKind.UNRECOGNISED,
                    sheet_page=None,
                    text_source=source,
                    match_score=0.0,
                    ink=ink,
                    embedded_text=embedded,
                    note=note,
                )
            )
            if not blank:
                candidates[index + 1] = fingerprint_from_gray(gray)

        # Pass 2: resolve all inked pages against the template together.
        assignment = assign_sheet_pages(candidates, templates)
        for ingested in result.pages:
            match = assignment.get(ingested.pdf_page)
            if match is None:
                continue
            sheet_page, score, note = match
            ingested.sheet_page = sheet_page
            ingested.match_score = score
            # Keep whatever pass 1 already had to say, and add the classifier's note.
            ingested.note = " ".join(part for part in (ingested.note, note) if part)
            ingested.kind = PageKind.SHEET if sheet_page else PageKind.UNRECOGNISED

        # Pass 3: render what is worth looking at. Blank pages are skipped -- nothing
        # downstream, including the review UI, has any use for them.
        if image_dir is not None:
            for ingested in result.pages:
                if ingested.kind is PageKind.BLANK:
                    continue
                # get_pixmap honours /Rotate, so a 270-degree scan comes out upright.
                pix = doc[ingested.pdf_page - 1].get_pixmap(dpi=render_dpi)
                dest = image_dir / f"page-{ingested.pdf_page:02d}.png"
                pix.save(dest)
                ingested.image_path = dest
                ingested.image_size = (pix.width, pix.height)

    return result
