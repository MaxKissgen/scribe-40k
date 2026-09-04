"""Printed-truth tables for the Dark Heresy 1st Edition character sheet.

Everything in this module is *pre-printed on the paper*: it is not player data and must
never be asked of an OCR or reasoning model. The extraction pipeline injects these values
deterministically, which removes roughly two thirds of the schema from the model's job and
takes a large class of hallucination off the table.

Sourced by extracting the text layer of the official blank template
(``samples/dark-heresy-blank-template.pdf``, (c) Games Workshop Ltd 2010).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CharacteristicAbbrev = Literal["WS", "BS", "S", "T", "Ag", "Int", "Per", "WP", "Fel"]
ProficiencyLevel = Literal["Untrained", "Basic", "Trained", "+10", "+20"]
FocusAction = Literal["Free", "Half", "Full", "Reaction"]


# --------------------------------------------------------------------------------------
# Characteristics
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CharacteristicSpec:
    key: str
    label: str
    abbreviation: CharacteristicAbbrev


#: Printed left-to-right across the CHARACTERISTICS block. Order is load-bearing: the
#: frontend renders the nine circles in exactly this sequence.
CHARACTERISTICS: tuple[CharacteristicSpec, ...] = (
    CharacteristicSpec("weaponSkill", "Weapon Skill", "WS"),
    CharacteristicSpec("ballisticSkill", "Ballistic Skill", "BS"),
    CharacteristicSpec("strength", "Strength", "S"),
    CharacteristicSpec("toughness", "Toughness", "T"),
    CharacteristicSpec("agility", "Agility", "Ag"),
    CharacteristicSpec("intelligence", "Intelligence", "Int"),
    CharacteristicSpec("perception", "Perception", "Per"),
    CharacteristicSpec("willpower", "Willpower", "WP"),
    CharacteristicSpec("fellowship", "Fellowship", "Fel"),
)

CHARACTERISTIC_BY_KEY: dict[str, CharacteristicSpec] = {c.key: c for c in CHARACTERISTICS}
CHARACTERISTIC_BY_ABBREV: dict[str, CharacteristicSpec] = {
    c.abbreviation: c for c in CHARACTERISTICS
}

#: The maximum number of ticked boxes in a "Characteristic Advances" row.
MAX_CHARACTERISTIC_ADVANCES = 4

#: Each ticked advance box is worth this much.
CHARACTERISTIC_ADVANCE_STEP = 5


# --------------------------------------------------------------------------------------
# Skills
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillSpec:
    key: str
    #: Exactly as printed, including the sheet's own typos. Used for OCR matching.
    printed_label: str
    characteristic: CharacteristicAbbrev
    #: True where the sheet shows a filled black square in the Basic column.
    is_basic: bool
    #: True for skills marked with a dagger, which carry specialisations instead of a
    #: proficiency of their own.
    is_group: bool
    #: Which of the three printed columns the skill sits in (1-3), and its row order.
    column: int
    #: Number of blank write-in lines printed beneath the skill.
    write_in_lines: int = 0
    #: True where the sheet prints a box row against the skill name itself. Group skills
    #: normally do not, but Navigation and Trade are inconsistent on the printed sheet.
    has_own_box_row: bool = True


#: Every skill printed on page 1, in printed order within each column.
#:
#: Note the deliberate oddities preserved from the paper:
#:   * "Evaluate" is printed without its "(Int)" qualifier.
#:   * "Scholasic Lore" is a typo on the sheet; the correct name is Scholastic Lore.
#:     Prompts match on the misspelling because that is what OCR will see.
#:   * Navigation and Trade are dagger group skills that nonetheless print their own box
#:     row; the schema normalises both to specialisations.
#:   * The blank line under Wrangling belongs to ``skills.additionalSkills``, not to
#:     Wrangling itself.
SKILLS: tuple[SkillSpec, ...] = (
    # ---- column 1 ----
    SkillSpec("acrobatics", "Acrobatics (Ag)", "Ag", False, False, 1),
    SkillSpec("awareness", "Awareness (Per)", "Per", True, False, 1),
    SkillSpec("barter", "Barter (Fel)", "Fel", True, False, 1),
    SkillSpec("blather", "Blather (Fel)", "Fel", False, False, 1),
    SkillSpec("carouse", "Carouse (T)", "T", True, False, 1),
    SkillSpec("charm", "Charm (Fel)", "Fel", True, False, 1),
    SkillSpec("chemUse", "Chem-Use (Int)", "Int", False, False, 1),
    SkillSpec("ciphers", "Ciphers (Int)", "Int", False, True, 1, 2, has_own_box_row=False),
    SkillSpec("climb", "Climb (S)", "S", True, False, 1),
    SkillSpec("command", "Command (Fel)", "Fel", True, False, 1),
    SkillSpec("commonLore", "Common Lore (Int)", "Int", False, True, 1, 3, has_own_box_row=False),
    SkillSpec("concealment", "Concealment (Ag)", "Ag", True, False, 1),
    SkillSpec("contortionist", "Contortionist (Ag)", "Ag", True, False, 1),
    SkillSpec("deceive", "Deceive (Fel)", "Fel", True, False, 1),
    SkillSpec("demolition", "Demolition (Int)", "Int", False, False, 1),
    SkillSpec("disguise", "Disguise (Fel)", "Fel", True, False, 1),
    SkillSpec("dodge", "Dodge (Ag)", "Ag", True, False, 1),
    SkillSpec("drive", "Drive (Ag)", "Ag", False, True, 1, 2, has_own_box_row=False),
    # ---- column 2 ----
    SkillSpec("evaluate", "Evaluate", "Int", True, False, 2),
    SkillSpec(
        "forbiddenLore", "Forbidden Lore (Int)", "Int", False, True, 2, 3, has_own_box_row=False
    ),
    SkillSpec("gamble", "Gamble (Int)", "Int", True, False, 2),
    SkillSpec("inquiry", "Inquiry (Fel)", "Fel", True, False, 2),
    SkillSpec("interrogation", "Interrogation (WP)", "WP", False, False, 2),
    SkillSpec("intimidate", "Intimidate (S)", "S", True, False, 2),
    SkillSpec("invocation", "Invocation (WP)", "WP", False, False, 2),
    SkillSpec("lipReading", "Lip Reading (Per)", "Per", False, False, 2),
    SkillSpec("literacy", "Literacy (Int)", "Int", False, False, 2),
    SkillSpec("logic", "Logic (Int)", "Int", True, False, 2),
    SkillSpec("medicae", "Medicae (Int)", "Int", False, False, 2),
    SkillSpec("navigation", "Navigation (Int)", "Int", False, True, 2, 0),
    SkillSpec("performer", "Performer (Fel)", "Fel", False, True, 2, 2, has_own_box_row=False),
    SkillSpec("pilot", "Pilot (Ag)", "Ag", False, True, 2, 2, has_own_box_row=False),
    SkillSpec("psyniscience", "Psyniscience (Per)", "Per", False, False, 2),
    SkillSpec(
        "scholasticLore", "Scholasic Lore (Int)", "Int", False, True, 2, 2, has_own_box_row=False
    ),
    # ---- column 3 ----
    SkillSpec("scrutiny", "Scrutiny (Per)", "Per", True, False, 3),
    SkillSpec("search", "Search (Per)", "Per", True, False, 3),
    SkillSpec(
        "secretTongue", "Secret Tongue (Int)", "Int", False, True, 3, 3, has_own_box_row=False
    ),
    SkillSpec("security", "Security (Ag)", "Ag", False, False, 3),
    SkillSpec("shadowing", "Shadowing (Ag)", "Ag", False, False, 3),
    SkillSpec("silentMove", "Silent Move (Ag)", "Ag", True, False, 3),
    SkillSpec("sleightOfHand", "Sleight of Hand (Ag)", "Ag", False, False, 3),
    SkillSpec(
        "speakLanguage", "Speak Language (Int)", "Int", False, True, 3, 3, has_own_box_row=False
    ),
    SkillSpec("survival", "Survival (Int)", "Int", False, False, 3),
    SkillSpec("swim", "Swim (S)", "S", True, False, 3),
    SkillSpec("techUse", "Tech-Use (Int)", "Int", False, False, 3),
    SkillSpec("tracking", "Tracking (Int)", "Int", False, False, 3),
    SkillSpec("trade", "Trade (Int)", "Int", False, True, 3, 3),
    SkillSpec("wrangling", "Wrangling (Int)", "Int", False, False, 3),
)

SKILL_BY_KEY: dict[str, SkillSpec] = {s.key: s for s in SKILLS}
GROUP_SKILL_KEYS: frozenset[str] = frozenset(s.key for s in SKILLS if s.is_group)
SIMPLE_SKILL_KEYS: frozenset[str] = frozenset(s.key for s in SKILLS if not s.is_group)

#: Column headers over the four proficiency boxes, printed rotated on the sheet.
PROFICIENCY_COLUMNS: tuple[str, ...] = ("Basic", "Trained", "+10%", "+20%")

#: Specialisation examples quoted in the schema, used only as prompt hints.
GROUP_SKILL_EXAMPLES: dict[str, tuple[str, ...]] = {
    "ciphers": ("Underworld", "Acolyte", "Occult"),
    "commonLore": ("Imperium", "Adeptus Arbites", "Tech"),
    "drive": ("Ground Vehicle", "Walker", "Hover Vehicle"),
    "forbiddenLore": ("Daemonology", "Heresy", "Xenos"),
    "navigation": ("Surface", "Stellar", "Warp"),
    "performer": ("Singer", "Dancer", "Storyteller"),
    "pilot": ("Civilian Craft", "Military Craft"),
    "scholasticLore": ("Legend", "Imperial Creed", "Judgement"),
    "secretTongue": ("Acolyte", "Military", "Rogue Trader"),
    "speakLanguage": ("Low Gothic", "High Gothic", "Hive Dialect"),
    "trade": ("Armourer", "Cook", "Copyist"),
}


# --------------------------------------------------------------------------------------
# Skill proficiency -> test modifier
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProficiencyModifier:
    usable: bool
    characteristic_multiplier: float
    flat_bonus: int


#: Verbatim from the schema's ``$defs.skillProficiency.modifier`` description. Derived,
#: never extracted.
PROFICIENCY_MODIFIERS: dict[str, ProficiencyModifier] = {
    "Untrained": ProficiencyModifier(False, 0, 0),
    "Basic": ProficiencyModifier(True, 0.5, 0),
    "Trained": ProficiencyModifier(True, 1, 0),
    "+10": ProficiencyModifier(True, 1, 10),
    "+20": ProficiencyModifier(True, 1, 20),
}


# --------------------------------------------------------------------------------------
# Armour
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmourLocationSpec:
    key: str
    location: str
    hit_roll: str
    hit_roll_min: int
    hit_roll_max: int


#: Hit-location ranges are printed on the body diagram and are fixed constants. Note that
#: the printed "86-00" normalises to a numeric maximum of 100.
ARMOUR_LOCATIONS: tuple[ArmourLocationSpec, ...] = (
    ArmourLocationSpec("head", "Head", "1-10", 1, 10),
    ArmourLocationSpec("rightArm", "Right Arm", "11-20", 11, 20),
    ArmourLocationSpec("leftArm", "Left Arm", "21-30", 21, 30),
    ArmourLocationSpec("body", "Body", "31-70", 31, 70),
    ArmourLocationSpec("rightLeg", "Right Leg", "71-85", 71, 85),
    ArmourLocationSpec("leftLeg", "Left Leg", "86-00", 86, 100),
)

ARMOUR_BY_KEY: dict[str, ArmourLocationSpec] = {a.key: a for a in ARMOUR_LOCATIONS}


# --------------------------------------------------------------------------------------
# Weapon training
# --------------------------------------------------------------------------------------

#: Printed down the first two columns of the Weapon Training Talents block.
BASIC_AND_PISTOL_TRAINING_KEYS: tuple[str, ...] = (
    "bolt",
    "flame",
    "las",
    "launcher",
    "melta",
    "plasma",
    "primitive",
    "sp",
)

#: Printed labels for those keys, in the same order.
BASIC_AND_PISTOL_TRAINING_LABELS: tuple[str, ...] = (
    "Bolt",
    "Flame",
    "Las",
    "Launcher",
    "Melta",
    "Plasma",
    "Primitive",
    "SP",
)

MELEE_TRAINING_KEYS: tuple[str, ...] = ("primitive", "chain", "shock", "power")
MELEE_TRAINING_LABELS: tuple[str, ...] = ("Primitive", "Chain", "Shock", "Power")

#: Blank "Exotic Weapon Training (____)" lines printed on the sheet. The schema is
#: unbounded; this is only the starting row count in the editor.
PRINTED_EXOTIC_TRAINING_LINES = 4


# --------------------------------------------------------------------------------------
# Minor psychic powers
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MinorPowerSpec:
    name: str
    threshold: int
    focus: FocusAction
    sustain: bool


#: The 32 pre-printed rows of the Minor Psychic Powers table on page 4. Threshold, focus
#: and sustain are printed; only the tick in the left column is player data.
MINOR_POWERS: tuple[MinorPowerSpec, ...] = (
    MinorPowerSpec("Call Creatures", 9, "Full", False),
    MinorPowerSpec("Call Item", 5, "Half", False),
    MinorPowerSpec("Chameleon", 7, "Half", True),
    MinorPowerSpec("Déjà vu", 8, "Half", False),
    MinorPowerSpec("Distort Vision", 8, "Free", False),
    MinorPowerSpec("Dull Pain", 8, "Half", False),
    MinorPowerSpec("Fearful Aura", 7, "Full", True),
    MinorPowerSpec("Flash Bang", 6, "Half", False),
    MinorPowerSpec("Float", 8, "Half", True),
    MinorPowerSpec("Forget Me", 6, "Half", False),
    MinorPowerSpec("Healer", 7, "Full", False),
    MinorPowerSpec("Inflict Pain", 8, "Half", True),
    MinorPowerSpec("Inspiring Aura", 6, "Full", True),
    MinorPowerSpec("Knack", 7, "Half", False),
    MinorPowerSpec("Lucky", 6, "Half", False),
    MinorPowerSpec("Precognition", 6, "Half", True),
    MinorPowerSpec("Psychic Stench", 5, "Half", False),
    MinorPowerSpec("Resist Possession", 6, "Reaction", False),
    MinorPowerSpec("Sense Presence", 7, "Half", True),
    MinorPowerSpec("Spasm", 7, "Half", False),
    MinorPowerSpec("Spectral Hands", 10, "Full", False),
    MinorPowerSpec("Staunch Bleeding", 8, "Half", False),
    MinorPowerSpec("Torch", 5, "Half", True),
    MinorPowerSpec("Touch of Madness", 11, "Full", False),
    MinorPowerSpec("Trick", 5, "Half", True),
    MinorPowerSpec("Unnatural Aim", 8, "Half", False),
    MinorPowerSpec("Wall Walk", 8, "Half", True),
    MinorPowerSpec("Warp Howl", 8, "Full", False),
    MinorPowerSpec("Weaken Veil", 9, "Full", True),
    MinorPowerSpec("Weapon Jinx", 8, "Full", False),
    MinorPowerSpec("White Noise", 8, "Full", True),
    MinorPowerSpec("Wither", 6, "Full", False),
)

MINOR_POWER_BY_NAME: dict[str, MinorPowerSpec] = {p.name: p for p in MINOR_POWERS}

#: Blank write-in rows printed under the table.
PRINTED_CUSTOM_MINOR_POWER_LINES = 8


# --------------------------------------------------------------------------------------
# Printed capacities
# --------------------------------------------------------------------------------------

#: How many of each repeating element the paper sheet has room for. The schema is
#: unbounded and the editor lets you add more; these are the *starting* row counts, and
#: the point past which PDF export spills onto a continuation page.
PRINTED_CAPACITY: dict[str, int] = {
    "bio.descriptionLines": 3,
    "insanity.disorders": 4,
    "corruption.malignancies": 4,
    "weapons.ranged": 3,
    "weapons.melee": 4,
    "talentsAndTraits.homeworldBackground": 3,
    "talentsAndTraits.advancesTalentsAndTraits": 15,
    "gear": 21,
    "weaponTraining.exotic": PRINTED_EXOTIC_TRAINING_LINES,
    "advances.rankAdvances.entries": 12,
    "advances.eliteAdvances": 12,
    "psychic.powers": 18,
    "psychic.minorPowers.custom": PRINTED_CUSTOM_MINOR_POWER_LINES,
}

#: Rank blocks printed on page 3.
PRINTED_RANK_BLOCKS = 8


# --------------------------------------------------------------------------------------
# Physical page geometry
# --------------------------------------------------------------------------------------

#: The template's own page size. Neither A4 (210x297) nor Letter (216x279).
PAGE_WIDTH_MM = 213.0
PAGE_HEIGHT_MM = 276.0

SHEET_PAGE_COUNT = 5
