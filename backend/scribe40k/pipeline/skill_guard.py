"""A deterministic check on the model's skill levels, using the OCR's own tick boxes.

The first live run showed the reasoning model reading the *transcription* rather than the
image for the skills grid -- and the transcription renders the printed black "Basic"
square as a ticked box, indistinguishable from a real tick. Every Basic skill the player
had not touched came back as "Trained".

The prompt now explains this at length, but a prompt is advice. This is a check: for each
printed skill whose row can be found in the OCR text, count the ticked boxes, subtract the
printed square for Basic skills, and compare with what the model said. Where the model
claims *more* marks than the transcription shows, the level is brought down to what the
evidence supports and the model's reading is kept as a one-click alternative on the flag.
Where the model claims fewer, it is left alone: it had the image, and the OCR may have
invented a tick.

Everything raised here is an "ocr." flag. The prefix is not cosmetic: rule flags are
regenerated from the saved document on every save, and nothing about the document records
what a transcription said, so a flag from this file filed under any other family would be
thrown away the next time the character was opened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import constants as K
from .report import Evidence, Flag

TICKED = "☑"
EMPTY = "☐"

#: Level implied by N player marks on a row.
LEVEL_FOR_MARKS: dict[int, str | None] = {0: None, 1: "Trained", 2: "+10", 3: "+20"}
RANK = {"Untrained": 0, "Basic": 0, "Trained": 1, "+10": 2, "+20": 3}


@dataclass(frozen=True)
class RowTicks:
    key: str
    #: Ticked boxes on the row, including a printed Basic square if the OCR rendered it.
    ticked: int
    #: Boxes of either kind found after the label. Below 3 the row was not really parsed.
    boxes: int
    #: Whether the *first* box -- the Basic column -- is among them. Printed filled on a
    #: Basic Skill; on an Advanced one it is either ink or a misread.
    first: bool = False


def _label_pattern(spec: K.SkillSpec) -> re.Pattern[str]:
    # Match on the name alone, case-insensitively and tolerant of OCR typos in the
    # bracketed characteristic ("Carousel (T)" for "Carouse (T)"): the name is what a
    # human would look for too.
    name = spec.printed_label.split(" (")[0]
    return re.compile(r"\b" + re.escape(name), re.IGNORECASE)


def ocr_row_ticks(text: str) -> dict[str, RowTicks]:
    """Count ticked boxes on each printed skill's row of the OCR transcription.

    Works on the markdown-table form Mistral OCR produces, where a row looks like
    ``| Awareness (Per) | ☑ | ☐ | ☐ | ☐ | Forbidden Lore (Int)† | ☐ | | |``. The boxes
    belonging to a skill are the run of box cells after its label, up to the next cell
    that contains letters (the next skill's label) or the end of the row.
    """
    found: dict[str, RowTicks] = {}
    lines = text.splitlines()

    for spec in K.SKILLS:
        if spec.is_group:
            continue
        pattern = _label_pattern(spec)
        for line in lines:
            if "|" not in line or not pattern.search(line):
                continue
            cells = [cell.strip() for cell in line.split("|")]
            start = next((i for i, cell in enumerate(cells) if pattern.search(cell)), None)
            if start is None:
                continue

            ticked = boxes = 0
            first = False
            for cell in cells[start + 1 :]:
                if cell in (TICKED, EMPTY):
                    if boxes == 0:
                        first = cell == TICKED
                    boxes += 1
                    ticked += cell == TICKED
                elif cell == "":
                    continue
                else:
                    break  # the next label
            if boxes >= 3:
                found[spec.key] = RowTicks(spec.key, ticked, boxes, first)
                break

    return found


def guard_skill_levels(
    skills: dict, ocr_text: str, *, sheet_page: int | None, pdf_page: int | None
) -> list[Flag]:
    """Bring over-counted skill levels down to what the transcription supports.

    Mutates ``skills`` (the model's ``data["skills"]`` fragment) in place and returns the
    flags raised. Only printed, non-group skills are checked; specialisations and
    write-in skills have no printed square and their rows are not reliably parseable.
    """
    if not isinstance(skills, dict):
        return []

    rows = ocr_row_ticks(ocr_text)
    flags: list[Flag] = []

    for key, row in rows.items():
        spec = K.SKILL_BY_KEY[key]
        entry = skills.get(key)
        house_ruled = bool(isinstance(entry, dict) and entry.get("isBasicSkill"))

        flags += _basic_column_note(spec, row, house_ruled, sheet_page, pdf_page)

        if not isinstance(entry, dict):
            continue
        level = (entry.get("proficiency") or {}).get("level")
        if level not in RANK:
            continue

        # A house-ruled Basic skill has a filled first square like any other Basic skill,
        # so the count of *player* marks excludes it either way.
        marks = row.ticked - (1 if spec.is_basic or house_ruled else 0)
        supported = LEVEL_FOR_MARKS.get(max(marks, 0))
        supported_rank = RANK[supported] if supported else 0

        if RANK[level] <= supported_rank:
            continue

        pointer = f"/skills/{key}/proficiency/level"
        if supported is None:
            # No player marks at all: the skill should not have been reported.
            del skills[key]
            what = "no player marks"
        else:
            entry["proficiency"]["level"] = supported
            what = f"{marks} player mark(s)"

        square = (
            " The first box on this row is the printed Basic square, which the "
            "transcription shows as a tick."
            if spec.is_basic or house_ruled
            else ""
        )
        flags.append(
            Flag(
                pointer=pointer,
                severity="warning",
                rule="ocr.tick_mismatch",
                message=(
                    f"The model read {spec.printed_label} as '{level}', but the "
                    f"transcription shows {what}, which supports "
                    f"'{supported or ('Basic' if spec.is_basic else 'Untrained')}'.{square}"
                ),
                alternatives=[level],
                expected=supported,
                actual=level,
                evidence=Evidence(sheetPage=sheet_page, pdfPage=pdf_page),
            )
        )

    return flags


def _basic_column_note(
    spec: K.SkillSpec,
    row: RowTicks,
    house_ruled: bool,
    sheet_page: int | None,
    pdf_page: int | None,
) -> list[Flag]:
    """Note a mark in the Basic column of a skill the rulebook calls Advanced.

    The square is printed empty there, so something is in it -- but what, exactly, cannot
    be settled from a transcription. It is either a GM allowing the skill untrained, or a
    player who counted the columns from the wrong edge and meant Trained.

    The reading stays Trained, which is both the commoner case and the one already tested,
    and the alternative is said out loud instead of guessed at. A single tick of the Basic
    square in the editor is the whole of the correction.

    Unlike the count, this depends on *which* box is marked, so it needs all four. The
    calibration scan is the argument: Tech-Use came back as three cells rather than four,
    with the marks at positions 0 and 2 -- which says nothing at all about the Basic
    column, and would have raised this note on a skill nobody had house-ruled.
    """
    if spec.is_basic or house_ruled or not row.first or row.boxes != 4:
        return []
    return [
        Flag(
            pointer=f"/skills/{spec.key}/isBasicSkill",
            severity="info",
            rule="ocr.basic_column_marked",
            message=(
                f"The Basic column of {spec.printed_label} is marked, and the sheet "
                f"prints that square empty -- it is an Advanced skill. Read as a player "
                f"mark, so it counts towards the level. If your GM allows the skill "
                f"untrained, tick the Basic square instead."
            ),
            evidence=Evidence(sheetPage=sheet_page, pdfPage=pdf_page),
        )
    ]
