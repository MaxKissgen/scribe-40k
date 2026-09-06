"""The whole extraction, end to end.

    PDF -> ingest -> classify -> OCR -> map -> derive -> validate -> character + report

Each stage is testable on its own; this module is the wiring. It also enforces the rule
that makes the pipeline safe to run on a messy document: nothing that was read from the
paper is discarded silently. A page that could not be classified, a section job that
failed, a fragment with nowhere to go -- each ends up in the report where the user can see
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..blank import blank_character
from ..llm.base import OcrPage, OcrProvider, PageImage, ReasoningProvider
from ..llm.offline import PassthroughOcr
from .ingest import IngestResult, PageKind, TextSource, ingest
from .mapper import map_sheet
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


@dataclass
class ExtractionOutcome:
    character: dict
    report: ExtractionReport

    @property
    def review_count(self) -> int:
        return self.report.review_count


def _page_images(result: IngestResult) -> dict[int, PageImage]:
    """Rendered images of the matched sheet pages, keyed by sheet page."""
    images: dict[int, PageImage] = {}
    for page in result.sheet_pages:
        if page.image_path is None or page.sheet_page is None:
            continue
        width, height = page.image_size or (0, 0)
        images[page.sheet_page] = PageImage(
            pdf_page=page.pdf_page,
            path=page.image_path,
            sheet_page=page.sheet_page,
            width=width,
            height=height,
        )
    return images


def _transcribe(
    result: IngestResult,
    images: dict[int, PageImage],
    ocr_provider: OcrProvider,
) -> dict[int, OcrPage]:
    """Transcribe the sheet pages, skipping OCR where the PDF already has the text."""
    needs_ocr = [
        img
        for sheet_page, img in images.items()
        if (page := result.page_for(sheet_page)) and page.text_source is TextSource.OCR
    ]
    from_layer = [
        img
        for sheet_page, img in images.items()
        if (page := result.page_for(sheet_page)) and page.text_source is TextSource.TEXT_LAYER
    ]

    transcribed: dict[int, OcrPage] = {}

    if from_layer:
        passthrough = PassthroughOcr()
        passthrough.load_from_ingest(result.pages)
        for page in passthrough.transcribe(from_layer):
            if page.sheet_page is not None:
                transcribed[page.sheet_page] = page

    if needs_ocr:
        for page in ocr_provider.transcribe(needs_ocr):
            if page.sheet_page is not None:
                transcribed[page.sheet_page] = page

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
    if not pages:
        return {}

    from_layer = [
        page
        for page in pages
        if (ingested := result.page_for_pdf(page.pdf_page))
        and ingested.text_source is TextSource.TEXT_LAYER
    ]
    needs_ocr = [page for page in pages if page not in from_layer]

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


def _page_records(result: IngestResult, ocr_pages: dict[int, OcrPage]) -> list[PageRecord]:
    records = []
    for page in result.pages:
        ocr = ocr_pages.get(page.sheet_page) if page.sheet_page else None
        records.append(
            PageRecord(
                pdfPage=page.pdf_page,
                kind=page.kind.value,
                sheetPage=page.sheet_page,
                textSource=page.text_source.value,
                matchScore=round(page.match_score, 4),
                ink=round(page.ink, 5),
                imagePath=page.image_path.name if page.image_path else None,
                note=page.note or None,
                ocrError=ocr.error if ocr else None,
            )
        )
    return records


def _structural_flags(result: IngestResult, ocr_pages: dict[int, OcrPage]) -> list[Flag]:
    """Problems with the document itself, rather than with any one field."""
    flags: list[Flag] = []

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

    for sheet_page, page in sorted(ocr_pages.items()):
        if page.error:
            flags.append(
                Flag(
                    pointer="",
                    severity="error",
                    rule="ocr.page_failed",
                    message=f"Sheet page {sheet_page} could not be transcribed: {page.error}",
                )
            )

    return flags


def _note_page_flags(result: IngestResult, notes: list[dict]) -> list[Flag]:
    """Say where each note page came from, and flag the ones that came out empty.

    A note page is not a problem to be fixed, so this is ``info`` and not a warning. But
    it should not appear from nowhere either: the user needs to know that PDF page 6 was
    not part of the sheet and that its text is now here.
    """
    flags: list[Flag] = []
    for index, note in enumerate(notes):
        pdf_page = note["sourcePdfPage"]
        page = result.page_for_pdf(pdf_page)
        reason = (page.note if page else "") or (
            "it does not match any page of the character sheet"
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


def extract(
    pdf_path: Path,
    ocr_provider: OcrProvider,
    reasoning_provider: ReasoningProvider,
    *,
    image_dir: Path,
    sections: tuple[Section, ...] = ALL_SECTIONS,
    progress=None,
) -> ExtractionOutcome:
    """Run the full pipeline over one sheet PDF."""

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

    images = _page_images(ingested)

    say(f"Transcribing {len(images)} page(s)")
    ocr_pages = _transcribe(ingested, images, ocr_provider)
    cached = sum(1 for p in ocr_pages.values() if p.cached)
    if cached:
        say(f"  {cached} of {len(ocr_pages)} came from the cache")

    extra_pages = _transcribe_extra_pages(ingested, ocr_provider)
    if ingested.unrecognised_pages:
        say(
            f"  {len(ingested.unrecognised_pages)} page(s) are not part of the sheet; "
            "transcribing them as note pages"
        )

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
            *_structural_flags(ingested, ocr_pages),
            *_note_page_flags(ingested, notes),
            *model_flags,
            *rule_flags,
        ]
    )

    report = ExtractionReport(
        source=SourceRef(
            filename=pdf_path.name,
            fileHash=ingested.file_hash,
            pageCount=len(ingested.pages),
        ),
        models={
            "ocr": ModelRef(provider=ocr_provider.name, model=ocr_provider.model),
            "reasoning": ModelRef(
                provider=reasoning_provider.name,
                model=reasoning_provider.model,
                supportsVision=reasoning_provider.supports_vision,
            ),
        },
        pages=_page_records(ingested, ocr_pages),
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
