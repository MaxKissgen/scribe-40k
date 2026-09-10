"""What a character looked like the last time it was printed.

The template fingerprints in ``assets/`` describe the *blank* form, which is the right
reference for a sheet filled in by hand. It is the wrong one for a sheet this program
printed: a scribe export is the form plus a page of values, and once a character has
enough gear to outgrow the printed lines, the page it spills onto looks like no template
page at all.

That would not matter if such a PDF were only ever re-imported as a file, because it
carries its own text layer and is identified from that for nothing. But the interesting
path is the other one: print it, cross things out, add a talent in the margin, scan it
back. The text layer does not survive a printer, and what comes back is a photograph of a
page whose ink pattern the blank template does not describe.

So the layout is recorded at the moment of export, page by page: a coarse fingerprint of
each page as printed and which sheet page it was. Scanning that printout back matches
against the character's own paper rather than against a blank form -- the same picture,
give or take a scanner and some handwriting -- and the recording already knows that sheet
page 1 was printed as two pages.

The one thing it cannot know is whether the sheet in the scanner is the sheet that was
printed. Edit a character after printing it and the recording describes a page that no
longer exists. Several are kept for that reason, the best match wins, and a poor best
match falls back to the template as before.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pymupdf

from .fingerprint import correlation, fingerprint_from_gray
from .ingest import CLASSIFY_DPI, load_page_text_signatures
from .page_text import identify_pages

#: Correlation at which a page is taken to be one of the recorded printed pages *on its
#: own merits*. Two of these are enough to establish that the document in the scanner is
#: this printout, which is the question that actually matters.
STRONG_MATCH = 0.45

#: ...and the floor for the rest, once that is established. Deliberately low, because at
#: that point the work is being done by elimination rather than by the score: a printed
#: page whose overflow is half a gear list correlates 0.22 with its own scan and 0.40 with
#: a different printed page, and only the fact that the other page is already spoken for
#: gets it home. A document that is not this printout never reaches this floor, because it
#: never produces the two strong matches that unlock it.
WEAK_MATCH = 0.18

#: Strong matches required before the weak ones are believed.
MATCHES_TO_TRUST_A_LAYOUT = 2

#: How many printings to remember. Enough to cover "I printed it, played two sessions,
#: printed it again, and it is the first one I marked up".
KEEP = 5


@dataclass
class PrintedPage:
    """One page of one printing."""

    index: int
    sheet_page: int | None
    #: 1-based position among the pages that carry this sheet page.
    part: int
    vector: list[float]
    #: The page's own text layer, kept for diagnosis rather than matching.
    text: str = ""


@dataclass
class PrintLayout:
    id: str
    recorded_at: str
    source_name: str
    pages: list[PrintedPage] = field(default_factory=list)

    def to_json_dict(self) -> dict:
        return {
            "version": 1,
            "id": self.id,
            "recordedAt": self.recorded_at,
            "sourceName": self.source_name,
            "pages": [
                {
                    "index": page.index,
                    "sheetPage": page.sheet_page,
                    "part": page.part,
                    "vector": [round(v, 4) for v in page.vector],
                    "text": page.text,
                }
                for page in self.pages
            ],
        }

    @classmethod
    def from_json_dict(cls, data: dict) -> PrintLayout:
        return cls(
            id=data["id"],
            recorded_at=data["recordedAt"],
            source_name=data.get("sourceName", ""),
            pages=[
                PrintedPage(
                    index=page["index"],
                    sheet_page=page["sheetPage"],
                    part=page.get("part", 1),
                    vector=page["vector"],
                    text=page.get("text", ""),
                )
                for page in data["pages"]
            ],
        )

    def parts_of(self, sheet_page: int) -> int:
        return sum(1 for page in self.pages if page.sheet_page == sheet_page)


def record(pdf_path: Path, *, source_name: str | None = None) -> PrintLayout:
    """Fingerprint every page of a freshly exported PDF and work out what each one is.

    The export has a text layer, so the sheet pages identify themselves. A page that
    identifies as nothing is the overflow of the page before it -- which is what overflow
    is, and the only place it can have come from in a document this program just wrote.
    """
    pdf_path = Path(pdf_path)
    signatures = load_page_text_signatures()

    vectors: dict[int, np.ndarray] = {}
    texts: dict[int, str] = {}
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            page = doc[index]
            pix = page.get_pixmap(dpi=CLASSIFY_DPI, colorspace=pymupdf.csGRAY)
            gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            vectors[index + 1] = fingerprint_from_gray(gray)
            texts[index + 1] = page.get_text("text") or ""

    identified = identify_pages(texts, signatures) if signatures else {}

    pages: list[PrintedPage] = []
    seen: dict[int, int] = {}
    previous: int | None = None
    for number in sorted(vectors):
        sheet_page = identified.get(number, (None, 0.0))[0]
        if sheet_page is None:
            sheet_page = previous
        part = seen.get(sheet_page, 0) + 1 if sheet_page else 1
        if sheet_page:
            seen[sheet_page] = part
        previous = sheet_page
        pages.append(
            PrintedPage(
                index=number,
                sheet_page=sheet_page,
                part=part,
                vector=[float(v) for v in vectors[number]],
                text=texts[number],
            )
        )

    return PrintLayout(
        id=uuid.uuid4().hex[:8],
        recorded_at=datetime.now(UTC).isoformat(),
        source_name=source_name or pdf_path.name,
        pages=pages,
    )


@dataclass(frozen=True)
class ReferenceMatch:
    sheet_page: int
    part: int
    score: float
    layout_id: str


def match_against(
    vectors: dict[int, np.ndarray],
    layouts: list[PrintLayout],
) -> dict[int, ReferenceMatch]:
    """Place uploaded pages against recorded printings, best-first and one-to-one.

    ``vectors`` maps uploaded PDF page to fingerprint. Only one printing may win: pages
    matched against two different printings of the same character would interleave two
    versions of it. So the layout with the strongest total agreement is chosen first, and
    only its pages are used.
    """
    if not layouts or not vectors:
        return {}

    best_layout: tuple[float, PrintLayout] | None = None
    for layout in layouts:
        total = 0.0
        for vector in vectors.values():
            scores = [
                correlation(vector, np.asarray(page.vector, dtype=np.float32))
                for page in layout.pages
                if len(page.vector) == len(vector)
            ]
            total += max(scores, default=0.0)
        if best_layout is None or total > best_layout[0]:
            best_layout = (total, layout)

    assert best_layout is not None
    layout = best_layout[1]

    ranked = sorted(
        (
            (correlation(vector, np.asarray(page.vector, dtype=np.float32)), pdf_page, page.index)
            for pdf_page, vector in vectors.items()
            for page in layout.pages
            if page.sheet_page and len(page.vector) == len(vector)
        ),
        reverse=True,
    )
    by_index = {page.index: page for page in layout.pages}

    matched: dict[int, ReferenceMatch] = {}
    used: set[int] = set()
    for score, pdf_page, index in ranked:
        if score < WEAK_MATCH or pdf_page in matched or index in used:
            continue
        printed = by_index[index]
        assert printed.sheet_page is not None
        matched[pdf_page] = ReferenceMatch(
            sheet_page=printed.sheet_page,
            part=printed.part,
            score=float(score),
            layout_id=layout.id,
        )
        used.add(index)

    # All of it or none of it. The weak matches are only believable as part of a whole
    # document that has already been recognised; on their own they are noise with a
    # sheet page attached.
    confident = sum(1 for found in matched.values() if found.score >= STRONG_MATCH)
    if confident < min(MATCHES_TO_TRUST_A_LAYOUT, len(by_index)):
        return {}

    return matched
