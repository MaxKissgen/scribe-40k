"""Stage 3b: turn a re-read sheet into suggestions rather than into the sheet.

An update is a printout of a character that somebody has written on: a skill advanced, a
talent crossed out, two gear lines added in the margin. Reading it produces a whole
candidate document, and the obvious thing to do with that document is to save it. That is
exactly what must not happen.

The reason is the same one that made the review step exist. A model reading handwriting is
right most of the time, and the times it is wrong are invisible: a gear line that came
through as "Space" instead of "Spare parts" is a plausible reading, and writing it over the
correct value the user typed last week destroys work with no trace. An import has nothing
to lose -- the sheet was empty -- but an update always does.

So nothing here writes. Every difference becomes a flag: ``update.changed`` where the
printout disagrees with a value, ``update.added`` for a row that is not in the sheet,
``update.removed`` for one that is no longer on the paper. The user accepts them one at a
time, and the sheet is whatever they leave it as.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from .report import Evidence, Flag

#: Fields that are computed, not read. Suggesting a change to a characteristic bonus is
#: suggesting that arithmetic be different.
_DERIVED = (
    "/bonus",
    "/proficiency/modifier",
    "/proficiency/modifier/usable",
    "/proficiency/modifier/characteristicMultiplier",
    "/proficiency/modifier/flatBonus",
)

#: ...and fields that are printed on the form rather than written by a player. A skill's
#: governing characteristic does not change because somebody scanned the page badly.
_PRINTED = (
    "/isBasicSkill",
    "/characteristic",
    "/abbreviation",
    "/location",
    "/hitRoll",
    "/hitRollMin",
    "/hitRollMax",
)

#: Keys that identify a row of a list, in the order they are looked for.
_ROW_KEYS = ("name", "subject", "advance", "weapon", "rank")

#: How alike two row names must be to be taken for the same row. Generous, because the
#: point of an update is that the row has *changed*: "Las pistol" becoming "Las pistol
#: (best)" is an edit to one row, not one row deleted and another added.
_SAME_ROW = 0.8

#: ...and the length at which one name being the start of another is enough on its own.
#: Similarity alone is not: "Scibilia" against "Scibilia 7D (oldest 5D)" scores 0.55,
#: because most of the longer string is absent from the shorter one -- yet a name that has
#: been shortened or extended is the commonest edit there is, and reporting it as a
#: deletion and an unrelated addition loses whatever else was on the row.
_PREFIX_ENOUGH = 4

#: A list that empties completely is far more likely to be a page that read badly than a
#: player who crossed out everything on it. Below this many rows, take it at face value.
_SUSPICIOUS_WIPE = 3


def _is_excluded(pointer: str) -> bool:
    return pointer.endswith(_DERIVED) or pointer.endswith(_PRINTED)


def _normalise(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _row_key(row: object) -> str:
    if isinstance(row, str):
        return _normalise(row)
    if isinstance(row, dict):
        for key in _ROW_KEYS:
            if row.get(key) not in (None, ""):
                return _normalise(row[key])
    return ""


def _label(row: object) -> str:
    """What to call a row when talking to the user about it."""
    if isinstance(row, str):
        return row
    if isinstance(row, dict):
        for key in _ROW_KEYS:
            value = row.get(key)
            if value not in (None, ""):
                return str(value)
    return "this entry"


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | dict):
        return not value
    return False


class _Suggester:
    """Walks two documents side by side and collects what differs."""

    def __init__(self, *, evidence: Evidence | None, allow_removals: bool) -> None:
        self.flags: list[Flag] = []
        self.evidence = evidence
        self.allow_removals = allow_removals

    # -- emitting ------------------------------------------------------------------

    def changed(self, pointer: str, was: object, now: object) -> None:
        self.flags.append(
            Flag(
                pointer=pointer,
                severity="warning",
                rule="update.changed",
                message=f"The printout reads {_quote(now)} here; the sheet holds {_quote(was)}.",
                expected=now,
                actual=was,
                evidence=self.evidence,
            )
        )

    def added(self, pointer: str, row: object) -> None:
        self.flags.append(
            Flag(
                pointer=pointer,
                severity="warning",
                rule="update.added",
                message=(f"The printout has {_quote(_label(row))} here, which the sheet does not."),
                expected=row,
                evidence=self.evidence,
            )
        )

    def removed(self, pointer: str, row: object) -> None:
        if not self.allow_removals:
            return
        self.flags.append(
            Flag(
                pointer=pointer,
                severity="warning",
                rule="update.removed",
                message=(
                    f"{_quote(_label(row))} is on the sheet but not on the printout. "
                    "Crossed out, or missed by the reading?"
                ),
                actual=row,
                evidence=self.evidence,
            )
        )

    # -- walking -------------------------------------------------------------------

    def walk(self, was: object, now: object, pointer: str = "") -> None:
        if _is_excluded(pointer):
            return

        if isinstance(was, dict) and isinstance(now, dict):
            for key in was:
                if key in now:
                    self.walk(was[key], now[key], f"{pointer}/{key}")
            return

        if isinstance(was, list) and isinstance(now, list):
            self.walk_list(was, now, pointer)
            return

        if was == now:
            return

        # Absent from the printout is not the same as deleted from the sheet: a field the
        # reading did not reach comes back null, and so does one somebody scribbled out.
        # The first is much more common, so a blank only ever raises a removal, never a
        # silent overwrite, and only where removals are allowed at all.
        if _is_blank(now):
            if not _is_blank(was):
                self.removed(pointer, was)
            return

        self.changed(pointer, was, now)

    def walk_list(self, was: list, now: list, pointer: str) -> None:
        if not now and len(was) >= _SUSPICIOUS_WIPE:
            self.flags.append(
                Flag(
                    pointer=pointer,
                    severity="warning",
                    rule="update.list_vanished",
                    message=(
                        f"The printout shows nothing here, where the sheet has {len(was)} "
                        "entries. That is more likely a page that read badly than a list "
                        "someone emptied, so nothing has been suggested for removal."
                    ),
                    actual=len(was),
                    evidence=self.evidence,
                )
            )
            return

        pairs, spare_old, spare_new = _pair_rows(was, now)
        for old_index, new_index in pairs:
            self.walk(was[old_index], now[new_index], f"{pointer}/{old_index}")
        for old_index in spare_old:
            self.removed(f"{pointer}/{old_index}", was[old_index])
        for offset, new_index in enumerate(spare_new):
            # Where it would land if accepted: after everything the sheet already has.
            self.added(f"{pointer}/{len(was) + offset}", now[new_index])


def _pair_rows(was: list, now: list) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Decide which row of the printout is which row of the sheet.

    By name first and position second. Position alone is wrong the moment a row is
    inserted or deleted -- everything after it shifts, and a list of twenty gear lines
    reports twenty changes because one was added at the top.
    """
    old_keys = {index: _row_key(row) for index, row in enumerate(was)}
    new_keys = {index: _row_key(row) for index, row in enumerate(now)}

    pairs: list[tuple[int, int]] = []
    spare_old = list(range(len(was)))
    spare_new = list(range(len(now)))

    # Exact names first, so a rename cannot steal a row that matches perfectly.
    for old_index in list(spare_old):
        key = old_keys[old_index]
        if not key:
            continue
        match = next((n for n in spare_new if new_keys[n] == key), None)
        if match is not None:
            pairs.append((old_index, match))
            spare_old.remove(old_index)
            spare_new.remove(match)

    # Then near-enough names: an edited row is still that row.
    for old_index in list(spare_old):
        key = old_keys[old_index]
        if not key:
            continue
        best, score = None, _SAME_ROW
        for new_index in spare_new:
            if not new_keys[new_index]:
                continue
            similarity = _similarity(key, new_keys[new_index])
            if similarity >= score:
                best, score = new_index, similarity
        if best is not None:
            pairs.append((old_index, best))
            spare_old.remove(old_index)
            spare_new.remove(best)

    # Whatever is left has no name to go on -- a row of an unnamed kind, or one whose name
    # is gone -- so position is all there is.
    for old_index in list(spare_old):
        if not spare_new:
            break
        new_index = spare_new[0]
        if old_keys[old_index] or new_keys[new_index]:
            continue
        pairs.append((old_index, new_index))
        spare_old.remove(old_index)
        spare_new.remove(new_index)

    return sorted(pairs), spare_old, spare_new


def _similarity(one: str, other: str) -> float:
    """How likely two row names are to be the same row.

    Plain string similarity, except that one name starting with the other counts as a
    match outright once it is long enough to mean something. A row does not stop being
    itself because somebody wrote out the rest of its name, or stopped bothering to.
    """
    shorter, longer = sorted((one, other), key=len)
    if len(shorter) >= _PREFIX_ENOUGH and longer.startswith(shorter):
        return 1.0
    return difflib.SequenceMatcher(None, one, other).ratio()


def _quote(value: object) -> str:
    if value is None:
        return "nothing"
    text = str(value)
    return f'"{text}"' if text.strip() else "nothing"


def suggest_updates(
    current: dict,
    candidate: dict,
    *,
    covers: set[str],
    evidence: Evidence | None = None,
    allow_removals: bool = True,
) -> list[Flag]:
    """Compare a re-read sheet with the character and describe the differences.

    ``covers`` is the set of root keys the update actually read -- the sections that ran
    and succeeded. Everything else is left alone, because a page that was not in the
    upload is not a page whose fields have been emptied. Upload only page 2 and page 1
    keeps every value it had.
    """
    suggester = _Suggester(evidence=evidence, allow_removals=allow_removals)
    for key in sorted(covers):
        if key in current and key in candidate:
            suggester.walk(current[key], candidate[key], f"/{key}")
    return suggester.flags


def covered_keys(records: list[Any], sections: tuple[Any, ...]) -> set[str]:
    """Root keys the sections that actually ran are answerable for."""
    ran = {record.name for record in records if record.status == "ok"}
    return {key for section in sections if section.name in ran for key in section.owns}
