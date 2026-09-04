"""Derived values and consistency rules.

Two jobs, deliberately kept apart:

``apply_derivations`` fills in values that follow mechanically from other values --
characteristic bonuses, proficiency modifiers, gear quantities. These are computed, never
extracted, so an OCR misreading cannot produce a self-inconsistent sheet.

``check_consistency`` looks for values that *disagree* with each other and reports them as
findings. It never corrects anything: a player may legitimately have a total that differs
from base + advances because of an implant or a temporary modifier, so these are flags for
a human, not errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from . import constants as K
from .models import CharacterSheet, GroupSkill, Skill, SkillProficiency

Severity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class Finding:
    """A disagreement between values, addressed to a human reviewer."""

    #: RFC 6901 JSON Pointer into the character document.
    pointer: str
    rule: str
    severity: Severity
    message: str
    #: What the rule would have expected, where that is meaningful.
    expected: object = None
    actual: object = None


# --------------------------------------------------------------------------------------
# Derivations
# --------------------------------------------------------------------------------------


def characteristic_bonus(total: int | None) -> int | None:
    """The tens digit of a characteristic, i.e. floor(total / 10)."""
    if total is None:
        return None
    return min(total // 10, 10)


def consistent_modifier(level: str) -> dict:
    spec = K.PROFICIENCY_MODIFIERS[level]
    return {
        "usable": spec.usable,
        "characteristicMultiplier": spec.characteristic_multiplier,
        "flatBonus": spec.flat_bonus,
    }


_QUANTITY_RE = re.compile(
    r"""^\s*
        (?P<qty>\d+)          # leading count
        \s*(?:x|×|\*)?   # optional x, multiplication sign or asterisk
        \s+
        (?P<name>\S.*?)       # the item itself
        \s*$""",
    re.VERBOSE | re.IGNORECASE,
)


def split_quantity(text: str) -> tuple[int | None, str]:
    """Split ``"3 x frag grenade"`` into ``(3, "frag grenade")``.

    Returns ``(None, text)`` unchanged when there is no leading count, so an item that
    simply starts with a number (``"9mm rounds"``) is only split when a separator or
    whitespace makes the count unambiguous.
    """
    match = _QUANTITY_RE.match(text)
    if not match:
        return None, text.strip()
    return int(match.group("qty")), match.group("name").strip()


def _sync_proficiency(prof: SkillProficiency) -> bool:
    """Force a proficiency's modifier to match its level. Returns True if it changed."""
    expected = consistent_modifier(prof.level)
    current = prof.modifier.model_dump()
    if current == expected:
        return False
    prof.modifier.usable = expected["usable"]
    prof.modifier.characteristicMultiplier = expected["characteristicMultiplier"]
    prof.modifier.flatBonus = expected["flatBonus"]
    return True


def apply_derivations(sheet: CharacterSheet) -> list[str]:
    """Recompute every derived value in place.

    Returns the pointers that were changed, which lets the caller report "this was
    corrected for you" rather than silently rewriting the model's output.
    """
    changed: list[str] = []

    # Characteristic bonuses follow from totals.
    for spec in K.CHARACTERISTICS:
        char = getattr(sheet.characteristics, spec.key)
        expected = characteristic_bonus(char.total)
        if char.bonus != expected:
            char.bonus = expected
            changed.append(f"/characteristics/{spec.key}/bonus")

    # Proficiency modifiers follow from levels.
    for spec in K.SKILLS:
        skill = getattr(sheet.skills, spec.key)
        if isinstance(skill, GroupSkill):
            for i, sp in enumerate(skill.specialisations):
                if _sync_proficiency(sp.proficiency):
                    changed.append(f"/skills/{spec.key}/specialisations/{i}/proficiency/modifier")
        elif isinstance(skill, Skill) and _sync_proficiency(skill.proficiency):
            changed.append(f"/skills/{spec.key}/proficiency/modifier")

    for i, extra in enumerate(sheet.skills.additionalSkills):
        if _sync_proficiency(extra.proficiency):
            changed.append(f"/skills/additionalSkills/{i}/proficiency/modifier")

    # Gear quantities parsed out of the name.
    for i, item in enumerate(sheet.gear):
        if item.quantity is None:
            qty, name = split_quantity(item.name)
            if qty is not None:
                item.quantity, item.name = qty, name
                changed.append(f"/gear/{i}/quantity")

    # Printed constants for any minor power the extractor matched by name.
    for i, power in enumerate(sheet.psychic.minorPowers):
        if power.isCustom:
            continue
        printed = K.MINOR_POWER_BY_NAME.get(power.name)
        if printed is None:
            continue
        if power.threshold != printed.threshold:
            power.threshold = printed.threshold
            changed.append(f"/psychic/minorPowers/{i}/threshold")
        if power.focus != printed.focus:
            power.focus = printed.focus  # type: ignore[assignment]
            changed.append(f"/psychic/minorPowers/{i}/focus")
        if power.sustain != printed.sustain:
            power.sustain = printed.sustain
            changed.append(f"/psychic/minorPowers/{i}/sustain")

    return changed


def skill_target(sheet: CharacterSheet, characteristic: str, prof: SkillProficiency) -> int | None:
    """The number to roll under for a test, or ``None`` when the skill is unusable.

    ``target = floor(characteristic total * multiplier) + flatBonus``.
    """
    if not prof.modifier.usable:
        return None
    spec = K.CHARACTERISTIC_BY_ABBREV.get(characteristic)
    if spec is None:
        return None
    total = getattr(sheet.characteristics, spec.key).total
    if total is None:
        return None
    return int(total * prof.modifier.characteristicMultiplier) + prof.modifier.flatBonus


def expected_movement(agility_bonus: int) -> dict[str, int]:
    """Movement rates in metres, derived from the Agility bonus."""
    return {
        "halfAction": agility_bonus,
        "fullAction": agility_bonus * 2,
        "charge": agility_bonus * 3,
        "run": agility_bonus * 6,
    }


# --------------------------------------------------------------------------------------
# Consistency rules
# --------------------------------------------------------------------------------------


def check_consistency(sheet: CharacterSheet) -> list[Finding]:
    """Report values that disagree with each other. Corrects nothing."""
    findings: list[Finding] = []

    findings += _check_characteristics(sheet)
    findings += _check_movement(sheet)
    findings += _check_tracks(sheet)
    findings += _check_skills(sheet)
    findings += _check_advances(sheet)
    findings += _check_psychic(sheet)

    return findings


def _check_characteristics(sheet: CharacterSheet) -> list[Finding]:
    out: list[Finding] = []
    for spec in K.CHARACTERISTICS:
        char = getattr(sheet.characteristics, spec.key)
        base_ptr = f"/characteristics/{spec.key}"

        if char.total is None:
            out.append(
                Finding(
                    f"{base_ptr}/total",
                    "characteristic.missing_total",
                    "warning",
                    f"{spec.label} has no total; the circle on the sheet was not read.",
                )
            )
            continue

        if char.base is not None and char.advancesTaken is not None:
            expected = char.base + K.CHARACTERISTIC_ADVANCE_STEP * char.advancesTaken
            if expected != char.total:
                out.append(
                    Finding(
                        f"{base_ptr}/total",
                        "characteristic.total_mismatch",
                        "warning",
                        (
                            f"{spec.label}: base {char.base} + {char.advancesTaken} advance(s) "
                            f"= {expected}, but the circle reads {char.total}. Legitimate if an "
                            f"implant or temporary modifier applies."
                        ),
                        expected=expected,
                        actual=char.total,
                    )
                )
    return out


def _check_movement(sheet: CharacterSheet) -> list[Finding]:
    ag_bonus = sheet.characteristics.agility.bonus
    if ag_bonus is None:
        return []
    expected = expected_movement(ag_bonus)
    out: list[Finding] = []
    for field, want in expected.items():
        got = getattr(sheet.movement, field)
        if got is not None and got != want:
            out.append(
                Finding(
                    f"/movement/{field}",
                    "movement.mismatch",
                    "warning",
                    (
                        f"Movement {field} reads {got} m, but an Agility bonus of {ag_bonus} "
                        f"gives {want} m."
                    ),
                    expected=want,
                    actual=got,
                )
            )
    return out


def _check_tracks(sheet: CharacterSheet) -> list[Finding]:
    out: list[Finding] = []
    w = sheet.wounds
    if (
        w.totalWounds is not None
        and w.currentWounds is not None
        and w.currentWounds > w.totalWounds
    ):
        out.append(
            Finding(
                "/wounds/currentWounds",
                "wounds.current_exceeds_total",
                "warning",
                f"Current wounds ({w.currentWounds}) exceed total wounds ({w.totalWounds}).",
                expected=w.totalWounds,
                actual=w.currentWounds,
            )
        )

    f = sheet.fatePoints
    if (
        f.totalFatePoints is not None
        and f.currentFatePoints is not None
        and f.currentFatePoints > f.totalFatePoints
    ):
        out.append(
            Finding(
                "/fatePoints/currentFatePoints",
                "fate.current_exceeds_total",
                "warning",
                (
                    f"Current fate points ({f.currentFatePoints}) exceed the total "
                    f"({f.totalFatePoints})."
                ),
                expected=f.totalFatePoints,
                actual=f.currentFatePoints,
            )
        )
    return out


def _check_skills(sheet: CharacterSheet) -> list[Finding]:
    out: list[Finding] = []
    for spec in K.SKILLS:
        skill = getattr(sheet.skills, spec.key)
        ptr = f"/skills/{spec.key}"

        if isinstance(skill, GroupSkill):
            for i, sp in enumerate(skill.specialisations):
                if not sp.subject.strip():
                    out.append(
                        Finding(
                            f"{ptr}/specialisations/{i}/subject",
                            "skill.specialisation_without_subject",
                            "error",
                            (
                                f"{spec.printed_label} has a specialisation marked "
                                f"'{sp.proficiency.level}' but no subject written on the line."
                            ),
                        )
                    )
            continue

        if skill.proficiency.level == "Basic" and not spec.is_basic:
            out.append(
                Finding(
                    f"{ptr}/proficiency/level",
                    "skill.basic_on_advanced_skill",
                    "error",
                    (
                        f"{spec.printed_label} is an Advanced skill (no filled square in the "
                        f"Basic column), so it cannot sit at 'Basic'. Expected 'Untrained' or "
                        f"a ticked advance."
                    ),
                    expected="Untrained",
                    actual="Basic",
                )
            )
    return out


def _check_advances(sheet: CharacterSheet) -> list[Finding]:
    out: list[Finding] = []
    a = sheet.advances
    if (
        a.totalExperience is not None
        and a.spentExperience is not None
        and a.spentExperience > a.totalExperience
    ):
        out.append(
            Finding(
                "/advances/spentExperience",
                "advances.overspent",
                "warning",
                (
                    f"Spent experience ({a.spentExperience}) exceeds total experience "
                    f"({a.totalExperience})."
                ),
                expected=a.totalExperience,
                actual=a.spentExperience,
            )
        )

    seen: set[int] = set()
    for i, block in enumerate(a.rankAdvances):
        if block.rank in seen:
            out.append(
                Finding(
                    f"/advances/rankAdvances/{i}/rank",
                    "advances.duplicate_rank",
                    "error",
                    f"Rank {block.rank} appears more than once.",
                    actual=block.rank,
                )
            )
        seen.add(block.rank)

        summed = sum(e.cost for e in block.entries if e.cost is not None)
        for j, entry in enumerate(block.entries):
            if entry.advance and entry.cost is None:
                out.append(
                    Finding(
                        f"/advances/rankAdvances/{i}/entries/{j}/cost",
                        "advances.missing_cost",
                        "info",
                        f"Rank {block.rank} advance '{entry.advance}' has no cost.",
                    )
                )
        del summed  # retained for a future total-XP cross-check
    return out


def _check_psychic(sheet: CharacterSheet) -> list[Finding]:
    out: list[Finding] = []
    p = sheet.psychic
    has_content = bool(p.minorPowers or p.powers or p.psychicDiscipline)
    if has_content and p.psyRating is None:
        out.append(
            Finding(
                "/psychic/psyRating",
                "psychic.powers_without_rating",
                "warning",
                "Psychic powers are recorded but Psy Rating is blank.",
            )
        )

    for i, power in enumerate(p.minorPowers):
        if not power.isCustom and power.name not in K.MINOR_POWER_BY_NAME:
            out.append(
                Finding(
                    f"/psychic/minorPowers/{i}/name",
                    "psychic.unknown_printed_minor_power",
                    "warning",
                    (
                        f"'{power.name}' is not one of the 32 printed minor powers but is not "
                        f"marked as a write-in row."
                    ),
                    actual=power.name,
                )
            )
    return out
