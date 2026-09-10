"""Stage 3: run the section jobs and fold their answers into the character document.

Jobs run concurrently and fail independently. A job that errors, returns unparseable JSON,
or writes outside the keys it owns loses its own section and nothing else -- the failure is
recorded in the report so a blank section is always explained rather than mysterious.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field

from ..llm.base import OcrPage, PageImage, ReasoningProvider, ReasoningRequest
from ..pointer import deep_merge
from .report import (
    LOW_CONFIDENCE_THRESHOLD,
    Evidence,
    Flag,
    SectionRecord,
    UnmappedItem,
    UnmappedSource,
)
from .sections import ALL_SECTIONS, Section
from .skill_guard import guard_skill_levels


@dataclass
class SectionResult:
    section: Section
    data: dict = field(default_factory=dict)
    flags: list[Flag] = field(default_factory=list)
    unmapped: list[UnmappedItem] = field(default_factory=list)
    record: SectionRecord | None = None


def _page_text(ocr_pages: dict[int, list[OcrPage]], sheet_pages: tuple[int, ...]) -> str:
    """One transcription for the job, labelled so the model knows what it is reading.

    A sheet page can arrive as several pages of the upload: a character with more gear
    than the printed lines hold spills onto a further page when exported, and a scan of
    that printout has two pages where the form has one. The parts are labelled as
    continuations rather than concatenated silently, because a model handed two slabs of a
    gear list with no explanation reports the entries twice.
    """
    parts = []
    for sheet_page in sheet_pages:
        pages = [page for page in ocr_pages.get(sheet_page, []) if page.text.strip()]
        for index, page in enumerate(pages, start=1):
            label = (
                f"[sheet page {sheet_page}]"
                if len(pages) == 1
                else (
                    f"[sheet page {sheet_page}, part {index} of {len(pages)} -- "
                    "a continuation of the same page, not a second one]"
                )
            )
            parts.append(f"{label}\n{page.text}")
    return "\n\n".join(parts)


#: Keys a model tends to put at the top level of "data" that belong one level down.
#: ``additionalSkills`` is the known offender: the prompt shows it inside "skills", and
#: the first live run returned it beside "skills" instead -- where the ownership check
#: then discarded it, losing a write-in skill without a word.
_RELOCATE: dict[str, tuple[str, str]] = {"additionalSkills": ("skills", "additionalSkills")}


def _relocate_misplaced_keys(data: dict, section: Section) -> dict:
    """Move a mis-nested key to where the schema wants it, if this job owns the parent."""
    for key, (parent, child) in _RELOCATE.items():
        if key not in data or parent not in section.owns:
            continue
        value = data.pop(key)
        target = data.setdefault(parent, {})
        if isinstance(target, dict) and child not in target:
            target[child] = value
    return data


def _restrict_to_owned(data: dict, section: Section) -> tuple[dict, list[str]]:
    """Drop any root key the job has no business writing.

    A model that answers the skills job with a ``bio`` block would otherwise let one
    section silently overwrite another's work. Rejected keys are reported, not hidden.
    """
    if not section.owns:
        return data, []
    kept = {k: v for k, v in data.items() if k in section.owns}
    rejected = sorted(set(data) - set(kept))
    return kept, rejected


def _flags_from_uncertain(
    entries: list, section: Section, sheet_page: int | None, pdf_page: int | None
) -> list[Flag]:
    flags: list[Flag] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pointer = entry.get("pointer")
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            continue

        confidence = entry.get("confidence")
        confidence = float(confidence) if isinstance(confidence, int | float) else None
        reason = entry.get("reason") or "the model was unsure of this reading"

        alternatives = entry.get("alternatives") or []
        alternatives = [str(a) for a in alternatives if isinstance(a, str | int | float)]

        flags.append(
            Flag(
                pointer=pointer,
                severity="warning",
                rule="model.low_confidence",
                message=f"Uncertain reading ({section.title}): {reason}.",
                confidence=confidence,
                alternatives=alternatives,
                evidence=Evidence(
                    # Both are needed: sheetPage names the page for the user, pdfPage
                    # addresses the rendered image the editor crops for the popover.
                    # Without pdfPage the crop -- the most useful part of a flag -- is
                    # silently absent.
                    sheetPage=sheet_page,
                    pdfPage=pdf_page,
                    snippet=entry.get("snippet"),
                ),
            )
        )
    return flags


def _unmapped_from(entries: list, section: Section, pdf_page: int, sheet_page: int | None):
    items: list[UnmappedItem] = []
    for entry in entries:
        if isinstance(entry, str):
            entry = {"text": entry}
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not text or not str(text).strip():
            continue

        item = UnmappedItem(
            text=str(text).strip(),
            source=UnmappedSource(
                pdfPage=pdf_page,
                sheetPage=sheet_page,
                location=entry.get("location"),
            ),
            reason=entry.get("reason") or f"found while reading the {section.title}",
        )
        item.ensure_id()
        items.append(item)
    return items


def run_section(
    section: Section,
    provider: ReasoningProvider,
    ocr_pages: dict[int, list[OcrPage]],
    images: dict[int, list[PageImage]],
) -> SectionResult:
    """Run one mapping job. Never raises: failure is returned as a record."""
    text = _page_text(ocr_pages, section.sheet_pages)
    of_this_section = [image for p in section.sheet_pages for image in images.get(p, [])]
    attached = of_this_section if provider.supports_vision else []
    primary = section.sheet_pages[0]
    # The PDF page number, not the sheet page: the rendered images the editor crops are
    # named by their position in the uploaded file, which is rarely the same thing.
    pdf_page = next((image.pdf_page for image in of_this_section), None)
    # True when at least one sheet page arrived as more than one page of the upload.
    continued = any(
        len(images.get(p, [])) > 1 or len(ocr_pages.get(p, [])) > 1 for p in section.sheet_pages
    )

    if not text.strip() and not attached:
        return SectionResult(
            section=section,
            record=SectionRecord(
                name=section.name,
                status="skipped",
                sheetPages=list(section.sheet_pages),
                error="no transcription and no page image for these pages",
            ),
        )

    request = ReasoningRequest(
        system=section.system_prompt(),
        user=section.user_prompt(text, has_images=bool(attached), continued=continued),
        images=attached,
        label=section.name,
        max_tokens=section.max_tokens,
    )

    try:
        response = provider.complete(request)
    except Exception as exc:  # a provider bug must not take the whole run down
        return SectionResult(
            section=section,
            record=SectionRecord(
                name=section.name,
                status="failed",
                sheetPages=list(section.sheet_pages),
                error=f"provider raised: {exc}",
            ),
        )

    if response.error:
        return SectionResult(
            section=section,
            record=SectionRecord(
                name=section.name,
                status="failed",
                sheetPages=list(section.sheet_pages),
                error=response.error,
            ),
        )

    if not isinstance(response.parsed, dict):
        return SectionResult(
            section=section,
            record=SectionRecord(
                name=section.name,
                status="failed",
                sheetPages=list(section.sheet_pages),
                error="the reply was not a JSON object",
            ),
        )

    envelope = response.parsed
    raw_data = envelope.get("data")
    raw_data = _relocate_misplaced_keys(raw_data if isinstance(raw_data, dict) else {}, section)
    data, rejected = _restrict_to_owned(raw_data, section)

    guard_flags: list[Flag] = []
    if section.name == "skills" and isinstance(data.get("skills"), dict):
        guard_flags = guard_skill_levels(
            data["skills"], text, sheet_page=primary, pdf_page=pdf_page
        )

    flags = _flags_from_uncertain(
        envelope.get("uncertain") or [] if isinstance(envelope.get("uncertain"), list) else [],
        section,
        primary,
        pdf_page,
    )
    unmapped = _unmapped_from(
        envelope.get("unmapped") or [] if isinstance(envelope.get("unmapped"), list) else [],
        section,
        pdf_page if pdf_page is not None else primary,
        primary,
    )

    error = None
    if rejected:
        error = (
            f"ignored keys this job does not own: {', '.join(rejected)} "
            f"(owns {', '.join(section.owns)})"
        )

    return SectionResult(
        section=section,
        data=data,
        flags=[*flags, *guard_flags],
        unmapped=unmapped,
        record=SectionRecord(
            name=section.name,
            status="ok",
            sheetPages=list(section.sheet_pages),
            error=error,
            promptTokens=response.prompt_tokens,
            completionTokens=response.completion_tokens,
            cached=response.cached,
        ),
    )


def map_sheet(
    document: dict,
    provider: ReasoningProvider,
    ocr_pages: dict[int, list[OcrPage]],
    images: dict[int, list[PageImage]],
    *,
    sections: tuple[Section, ...] = ALL_SECTIONS,
    max_workers: int = 4,
) -> tuple[list[Flag], list[UnmappedItem], list[SectionRecord]]:
    """Run every job and merge the results into ``document`` in place.

    Jobs are independent, so they run concurrently. Merging happens afterwards, on the main
    thread and in a fixed order, so the outcome does not depend on which job finished
    first.
    """
    results: dict[str, SectionResult] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(run_section, section, provider, ocr_pages, images): section
            for section in sections
        }
        for future in concurrent.futures.as_completed(futures):
            section = futures[future]
            try:
                results[section.name] = future.result()
            except Exception as exc:  # pragma: no cover - run_section already guards
                results[section.name] = SectionResult(
                    section=section,
                    record=SectionRecord(
                        name=section.name, status="failed", error=f"unexpected: {exc}"
                    ),
                )

    flags: list[Flag] = []
    unmapped: list[UnmappedItem] = []
    records: list[SectionRecord] = []

    for section in sections:
        result = results.get(section.name)
        if result is None:
            continue
        if result.data:
            deep_merge(document, result.data)
        flags.extend(result.flags)
        unmapped.extend(result.unmapped)
        if result.record:
            records.append(result.record)

    return flags, unmapped, records


def confidence_is_low(confidence: float | None) -> bool:
    return confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD
