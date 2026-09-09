"""Identifying a sheet page from its transcription, when its image could not be matched.

The image fingerprint in :mod:`~scribe40k.pipeline.fingerprint` compares a page against the
template as a rigid grid, which assumes the page is a flat, axis-aligned rectangle. A
*photograph* of a sheet is none of those things: the paper is tilted, keystoned by the
camera angle, lit unevenly, and surrounded by whatever the phone was pointed at. On a
three-page photographed sheet every page correlated below 0.29 against every template
page, so all three were declared unrecognised.

But the printed furniture that the fingerprint cannot see is still perfectly legible to
OCR. "RANGED WEAPONS", "RANK 1 ADVANCES", "MINOR PSYCHIC POWERS" are printed on exactly
one page each, and they survive a bad photograph intact. So when the image cannot place a
page, its transcription is asked instead -- and since every page is transcribed anyway,
this costs nothing extra.

Deliberately a *fallback*, not a replacement. The image match is cheap, runs before any
model call, and is right about clean scans; this only ever sees what it could not place.
"""

from __future__ import annotations

import re
from collections import Counter

#: Words. Digits and punctuation are dropped: OCR mangles them far more than it mangles
#: printed headings, and the headings are what carry the signal.
_WORD = re.compile(r"[a-z][a-z'-]+")

#: A token counts towards a page's signature only if the template prints it on at most
#: this many pages. One is too strict -- page 5 has no vocabulary of its own at all, only
#: page 4's minus the minor-powers table -- and three lets the reprinted characteristics
#: strip drown everything else out.
MAX_PAGES_PER_TOKEN = 2

#: Weighted recall a transcription must reach before it may claim a sheet page.
MATCH_THRESHOLD = 0.35

#: ...and the weight it must find, so that a page whose signature is thin cannot be
#: claimed on the strength of two coincidental words. Page 3's whole signature is worth
#: only 6, so this is a real constraint rather than a formality.
MIN_MATCHED_WEIGHT = 3.0


def tokenise(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def build_signatures(page_text: dict[int, str]) -> dict[int, dict[str, float]]:
    """Per-page distinctive vocabulary, from the template's own text layer.

    A token's weight is the reciprocal of the number of template pages that print it, so a
    word unique to one page counts double one shared with a second. Words printed on three
    or more pages -- the reprinted characteristics strip, the copyright line -- carry no
    information about which page this is and are dropped entirely.
    """
    pages = {number: tokenise(text) for number, text in page_text.items()}

    appearances: Counter[str] = Counter()
    for tokens in pages.values():
        appearances.update(tokens)

    return {
        number: {
            token: 1.0 / appearances[token]
            for token in tokens
            if appearances[token] <= MAX_PAGES_PER_TOKEN
        }
        for number, tokens in pages.items()
    }


def score(text: str, signature: dict[str, float]) -> tuple[float, float]:
    """How much of one page's signature this transcription contains.

    Returns ``(recall, matched weight)``. The two are used for different jobs, and pages 4
    and 5 are why. Page 5 prints nothing page 4 does not, so a transcription of page 4
    scores full recall against *both*; only the weight of what it matched -- sixty against
    four and a half -- says which it is. Meanwhile a transcription of page 5 matches the
    same four and a half either way, and only recall says it is the page that has nothing
    missing.

    So recall decides whether a page is a candidate at all, and weight ranks the
    candidates. Ranking by recall instead would work only while OCR reads every printed
    word: a page 4 whose minor-powers table came through half-legible has less than full
    recall of page 4 and still full recall of page 5, and would be filed as page 5.
    """
    total = sum(signature.values())
    if total <= 0:
        return 0.0, 0.0
    found = tokenise(text)
    matched = sum(weight for token, weight in signature.items() if token in found)
    return matched / total, matched


def identify_pages(
    transcriptions: dict[int, str],
    signatures: dict[int, dict[str, float]],
    *,
    already_claimed: set[int] = frozenset(),  # type: ignore[assignment]
) -> dict[int, tuple[int, float]]:
    """Match transcriptions to the sheet pages they came from.

    ``transcriptions`` maps PDF page to text; the result maps PDF page to
    ``(sheet page, recall)`` for those confident enough to claim one.

    Claiming is one-to-one and best-first, for the same reason the image matcher does it:
    a sheet has one of each page, so letting the confident matches take theirs first
    resolves the doubtful ones by elimination.
    """
    ranked = sorted(
        (
            (matched, recall, pdf_page, sheet_page)
            for pdf_page, text in transcriptions.items()
            for sheet_page, signature in signatures.items()
            if sheet_page not in already_claimed
            for recall, matched in [score(text, signature)]
            if recall >= MATCH_THRESHOLD and matched >= MIN_MATCHED_WEIGHT
        ),
        reverse=True,
    )

    identified: dict[int, tuple[int, float]] = {}
    taken: set[int] = set(already_claimed)
    for _matched, recall, pdf_page, sheet_page in ranked:
        if pdf_page in identified or sheet_page in taken:
            continue
        identified[pdf_page] = (sheet_page, recall)
        taken.add(sheet_page)

    return identified
