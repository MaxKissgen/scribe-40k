"""Factory for an empty character sheet.

Everything the paper prints is filled in from :mod:`scribe40k.constants`; everything a
player would write is ``null`` or empty. The result validates against the schema as-is,
which makes it the starting point for both a new character in the editor and the target
document that the extraction pipeline fills in.
"""

from __future__ import annotations

from . import constants as K
from .models import (
    AdditionalSkill,
    Advances,
    Armour,
    ArmourLocation,
    Bio,
    Characteristic,
    Characteristics,
    CharacterSheet,
    Corruption,
    FatePoints,
    GroupSkill,
    Insanity,
    MeleeWeaponTraining,
    Movement,
    ProficiencyModifier,
    Psychic,
    Skill,
    SkillProficiency,
    Skills,
    TalentsAndTraits,
    Weapons,
    WeaponTraining,
    WeaponTrainingGroup,
    Wounds,
)


def proficiency(level: str = "Untrained") -> SkillProficiency:
    """Build a proficiency whose modifier is consistent with ``level`` by construction."""
    spec = K.PROFICIENCY_MODIFIERS[level]
    return SkillProficiency(
        level=level,  # type: ignore[arg-type]
        modifier=ProficiencyModifier(
            usable=spec.usable,
            characteristicMultiplier=spec.characteristic_multiplier,  # type: ignore[arg-type]
            flatBonus=spec.flat_bonus,  # type: ignore[arg-type]
        ),
    )


def default_proficiency_for(skill_key: str) -> SkillProficiency:
    """A skill's proficiency before the player marks anything.

    A Basic Skill is always usable at half characteristic even untrained, so its resting
    state is ``Basic``. An Advanced skill cannot be tested at all, so its resting state is
    ``Untrained``.
    """
    spec = K.SKILL_BY_KEY[skill_key]
    return proficiency("Basic" if spec.is_basic else "Untrained")


def blank_characteristics() -> Characteristics:
    return Characteristics(
        **{spec.key: Characteristic(abbreviation=spec.abbreviation) for spec in K.CHARACTERISTICS}
    )


def blank_skills() -> Skills:
    fields: dict[str, object] = {}
    for spec in K.SKILLS:
        if spec.is_group:
            fields[spec.key] = GroupSkill(
                characteristic=spec.characteristic,
                isBasicSkill=spec.is_basic,
                specialisations=[],
            )
        else:
            fields[spec.key] = Skill(
                characteristic=spec.characteristic,
                isBasicSkill=spec.is_basic,
                proficiency=default_proficiency_for(spec.key),
            )
    fields["additionalSkills"] = []
    return Skills(**fields)  # type: ignore[arg-type]


def blank_armour() -> Armour:
    return Armour(
        **{
            spec.key: ArmourLocation(
                location=spec.location,
                hitRoll=spec.hit_roll,
                hitRollMin=spec.hit_roll_min,
                hitRollMax=spec.hit_roll_max,
                type=None,
                armourPoints=None,
            )
            for spec in K.ARMOUR_LOCATIONS
        }
    )


def blank_character() -> CharacterSheet:
    """An empty sheet with every printed constant already in place."""
    return CharacterSheet(
        bio=Bio(),
        characteristics=blank_characteristics(),
        skills=blank_skills(),
        wounds=Wounds(),
        fatePoints=FatePoints(),
        armour=blank_armour(),
        insanity=Insanity(),
        corruption=Corruption(),
        movement=Movement(),
        weapons=Weapons(),
        talentsAndTraits=TalentsAndTraits(),
        gear=[],
        weaponTraining=WeaponTraining(
            basic=WeaponTrainingGroup(),
            pistol=WeaponTrainingGroup(),
            melee=MeleeWeaponTraining(),
            exotic=[],
        ),
        advances=Advances(),
        psychic=Psychic(),
    )


def new_additional_skill(name: str, characteristic: str | None = None) -> AdditionalSkill:
    """A write-in skill from one of the sheet's blank lines."""
    return AdditionalSkill(
        name=name,
        characteristic=characteristic,  # type: ignore[arg-type]
        isBasicSkill=None,
        proficiency=proficiency("Trained"),
    )
