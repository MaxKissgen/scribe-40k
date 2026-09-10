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

#: What a *continuation* of an already-claimed page must reach. Lower than the first pass
#: on purpose: the second half of a spilled page carries the rows that would not fit and
#: only some of the headings, so it can never look as much like the page as the first half
#: does. It is judged against a page that has already been claimed, which is a far weaker
#: claim to be making than "this is sheet page 2 and nothing else is".
CONTINUATION_THRESHOLD = 0.2
CONTINUATION_MIN_WEIGHT = 2.0


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
    already_placed: dict[int, int] | None = None,
) -> dict[int, tuple[int, float]]:
    """Match transcriptions to the sheet pages they came from.

    ``transcriptions`` maps PDF page to text, for the pages still looking for a home.
    ``already_placed`` maps PDF page to sheet page for the ones the image matcher has
    already settled. The result maps PDF page to ``(sheet page, recall)``.

    Two passes. The first is one-to-one and best-first, for the same reason the image
    matcher does it: a sheet has one of each page, so letting the confident matches take
    theirs first resolves the doubtful ones by elimination. The second lets what is left
    attach to a page already taken -- see :func:`_continuations`.
    """
    placed = dict(already_placed or {})
    claimed = set(placed.values())

    ranked = sorted(
        (
            (matched, recall, pdf_page, sheet_page)
            for pdf_page, text in transcriptions.items()
            if pdf_page not in placed
            for sheet_page, signature in signatures.items()
            if sheet_page not in claimed
            for recall, matched in [score(text, signature)]
            if recall >= MATCH_THRESHOLD and matched >= MIN_MATCHED_WEIGHT
        ),
        reverse=True,
    )

    identified: dict[int, tuple[int, float]] = {}
    for _matched, recall, pdf_page, sheet_page in ranked:
        if pdf_page in identified or sheet_page in claimed:
            continue
        identified[pdf_page] = (sheet_page, recall)
        claimed.add(sheet_page)

    settled = {**placed, **{page: where for page, (where, _) in identified.items()}}
    identified.update(_continuations(transcriptions, signatures, settled=settled))
    return identified


def _continuations(
    transcriptions: dict[int, str],
    signatures: dict[int, dict[str, float]],
    *,
    settled: dict[int, int],
) -> dict[int, tuple[int, float]]:
    """Pages that are the rest of a page already claimed by another.

    A sheet page is not always one page of the upload. A character with more gear than the
    printed lines hold spills onto a further page when exported, so a scan of that printout
    has two pages where the form has one -- and the second half looks like sheet page 2
    without looking like it *more* than the first half does. One-to-one claiming rejected
    those outright: on a real overflowing export the spill page scored 0.52 against sheet
    page 2, comfortably enough to be recognised, and became a note page purely because the
    first half got there first.

    Two things keep this from swallowing genuine note pages. A continuation may only ever
    join a page that is already claimed -- nothing *starts* a claim this way -- and it must
    directly follow that page in the document. Spill is a property of printing: the
    overflow of a page is the next page, always. A page of session notes at the end of a
    scan is not next to anything, whatever words happen to be on it.
    """
    order = sorted(set(settled) | set(transcriptions))
    joined: dict[int, tuple[int, float]] = {}
    running = dict(settled)

    for position, pdf_page in enumerate(order):
        if pdf_page in running or position == 0:
            continue
        previous = running.get(order[position - 1])
        if previous is None:
            continue

        signature = signatures.get(previous)
        text = transcriptions.get(pdf_page)
        if signature is None or text is None:
            continue

        recall, matched = score(text, signature)
        if recall < CONTINUATION_THRESHOLD or matched < CONTINUATION_MIN_WEIGHT:
            continue

        joined[pdf_page] = (previous, recall)
        running[pdf_page] = previous

    return joined
