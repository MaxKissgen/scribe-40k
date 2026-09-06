"""Pydantic mirror of ``dark-heresy-character-sheet.schema.json``.

The JSON Schema is the contract and is never modified by this project; these models are a
convenience layer over it. Two rules keep the two in step:

* Every object is ``extra="forbid"``, mirroring ``additionalProperties: false``.
* Every leaf that the schema marks required-but-nullable defaults to ``None`` rather than
  being optional, so ``model_dump()`` always emits the key. A blank field on a scanned
  sheet becomes ``null``, never a missing property.

``tests/test_schema_parity.py`` asserts that a round-trip through these models still
validates against the schema, so drift is caught rather than assumed away.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

CharacteristicAbbrev = Literal["WS", "BS", "S", "T", "Ag", "Int", "Per", "WP", "Fel"]
ProficiencyLevel = Literal["Untrained", "Basic", "Trained", "+10", "+20"]
FocusAction = Literal["Free", "Half", "Full", "Reaction"]


def _to_text(value: object) -> object:
    """Accept a bare number where the schema wants free text.

    A reasoning model reading ``RANGE  60`` returns ``60``, not ``"60"``, and pydantic is
    (rightly) strict about not turning ints into strings. But every free-text leaf on this
    sheet is somewhere a player may write only a number -- range, rank, damage, an armour
    type -- so the honest representation of ``60`` in a text field is ``"60"``. Bools are
    left alone so that a real type error still surfaces.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return value


#: A nullable free-text leaf that tolerates a number being written into it.
Text = Annotated[str | None, BeforeValidator(_to_text)]


def _to_int(value: object) -> object:
    """Accept a numeric string where the schema wants an integer.

    The mirror of :func:`_to_text`. Told that text fields are strings, a model promptly
    returned ``"30"`` for a clip and ``"0"`` for penetration. Only a string that *is* an
    integer is converted; anything else is left for validation to reject, so ``"S/3/10"``
    in a clip field still surfaces as an error rather than silently becoming a number.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"[+-]?\d+", stripped):
            return int(stripped)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


#: A nullable non-negative integer leaf that tolerates being written as a string. The bound
#: is on the ``int`` member only: applied to the whole union it would try ``None >= 0``.
Count = Annotated[Annotated[int, Field(ge=0)] | None, BeforeValidator(_to_int)]


def _to_distance(value: object) -> object:
    """Accept the unit a player writes after a range.

    ``RANGE`` is an integer in the schema, but nobody writes a bare number on paper: the
    sheet says ``100m``, ``60 m``, ``30 metres``. Dropping the unit is lossless -- metres
    is the only unit the game uses for weapon range -- and it is the difference between a
    read that lands in the field and one that lands in the review queue.
    """
    if isinstance(value, str):
        stripped = re.sub(r"\s*(m|metres|meters)\.?$", "", value.strip(), flags=re.IGNORECASE)
        return _to_int(stripped)
    return _to_int(value)


#: A range in metres, tolerant of the unit being written out.
Distance = Annotated[Annotated[int, Field(ge=0)] | None, BeforeValidator(_to_distance)]


class Strict(BaseModel):
    """Base for every sheet object: rejects properties the schema does not define."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------------------
# bio
# --------------------------------------------------------------------------------------


class Bio(Strict):
    characterName: Text = None
    playerName: Text = None
    career: Text = None
    rank: Text = None
    homeWorld: Text = None
    quirk: Text = None
    divination: Text = None
    ordoOrFaction: Text = None
    #: The three printed lines joined with newlines preserved.
    description: Text = None


# --------------------------------------------------------------------------------------
# characteristics
# --------------------------------------------------------------------------------------


class Characteristic(Strict):
    abbreviation: CharacteristicAbbrev
    #: Starting value before characteristic advances.
    base: Annotated[int | None, BeforeValidator(_to_int)] = Field(default=None, ge=0, le=100)
    #: Ticked boxes in the "Characteristic Advances" row, 0-4. Each is worth +5.
    advancesTaken: Annotated[int | None, BeforeValidator(_to_int)] = Field(default=None, ge=0, le=4)
    #: The value written in the circle. Stored rather than derived, because temporary or
    #: item modifiers can make the written value differ from base + 5 * advances.
    total: Annotated[int | None, BeforeValidator(_to_int)] = Field(default=None, ge=0, le=100)
    #: floor(total / 10).
    bonus: Annotated[int | None, BeforeValidator(_to_int)] = Field(default=None, ge=0, le=10)


class Characteristics(Strict):
    weaponSkill: Characteristic
    ballisticSkill: Characteristic
    strength: Characteristic
    toughness: Characteristic
    agility: Characteristic
    intelligence: Characteristic
    perception: Characteristic
    willpower: Characteristic
    fellowship: Characteristic


# --------------------------------------------------------------------------------------
# skills
# --------------------------------------------------------------------------------------


class ProficiencyModifier(Strict):
    usable: bool
    characteristicMultiplier: Literal[0, 0.5, 1]
    flatBonus: Literal[0, 10, 20]


#: Verbatim from the schema's ``$defs.skillProficiency.modifier`` description. Duplicated
#: here rather than imported from :mod:`scribe40k.constants` to keep this module free of
#: project imports; ``tests/test_blank_and_schema.py`` asserts the two agree.
_MODIFIER_FOR_LEVEL: dict[str, dict] = {
    "Untrained": {"usable": False, "characteristicMultiplier": 0, "flatBonus": 0},
    "Basic": {"usable": True, "characteristicMultiplier": 0.5, "flatBonus": 0},
    "Trained": {"usable": True, "characteristicMultiplier": 1, "flatBonus": 0},
    "+10": {"usable": True, "characteristicMultiplier": 1, "flatBonus": 10},
    "+20": {"usable": True, "characteristicMultiplier": 1, "flatBonus": 20},
}


class SkillProficiency(Strict):
    level: ProficiencyLevel
    modifier: ProficiencyModifier

    @model_validator(mode="before")
    @classmethod
    def _fill_modifier(cls, data):
        """Derive ``modifier`` from ``level`` when it is absent.

        The schema requires both, but the modifier is a pure function of the level, so a
        reasoning model is never asked for it -- the mapping prompts explicitly say to omit
        it. Filling it in here means a partial fragment like ``{"level": "Trained"}``
        becomes a complete, schema-valid object wherever it appears, including inside the
        specialisation lists that replace wholesale on merge.
        """
        if isinstance(data, dict) and "modifier" not in data:
            fallback = _MODIFIER_FOR_LEVEL.get(data.get("level"))
            if fallback is not None:
                data = {**data, "modifier": fallback}
        return data


class Skill(Strict):
    characteristic: CharacteristicAbbrev
    #: Printed information, not a player choice.
    isBasicSkill: bool
    proficiency: SkillProficiency
    notes: Text = None


class SkillSpecialisation(Strict):
    #: The text written on the blank line, e.g. "Imperium" for Common Lore.
    subject: str
    proficiency: SkillProficiency
    notes: Text = None

    @model_validator(mode="before")
    @classmethod
    def _default_proficiency(cls, data):
        """A written-in specialisation with no ticks is still a skill the character has.

        The prompt says to report such a line at "Trained"; models frequently omit the
        proficiency instead. Left unfilled, that one missing key made the *entire*
        character document unparseable on the first live run -- which meant no derived
        values, no consistency checks, and a wall of schema errors. Defaulting here is
        both correct (the paper rule is that a written line counts as Trained) and what
        keeps one bad specialisation from taking the sheet down with it.
        """
        if isinstance(data, dict) and not data.get("proficiency"):
            data = {**data, "proficiency": {"level": "Trained"}}
        return data


class GroupSkill(Strict):
    """A skill marked with a dagger: no proficiency of its own, only specialisations."""

    characteristic: CharacteristicAbbrev
    isBasicSkill: bool
    specialisations: list[SkillSpecialisation] = Field(default_factory=list)
    notes: Text = None


class AdditionalSkill(Strict):
    """A skill written onto a blank line that belongs to no printed group skill."""

    name: str
    characteristic: CharacteristicAbbrev | None = None
    isBasicSkill: bool | None = None
    proficiency: SkillProficiency
    notes: Text = None


class Skills(Strict):
    acrobatics: Skill
    awareness: Skill
    barter: Skill
    blather: Skill
    carouse: Skill
    charm: Skill
    chemUse: Skill
    ciphers: GroupSkill
    climb: Skill
    command: Skill
    commonLore: GroupSkill
    concealment: Skill
    contortionist: Skill
    deceive: Skill
    demolition: Skill
    disguise: Skill
    dodge: Skill
    drive: GroupSkill
    evaluate: Skill
    forbiddenLore: GroupSkill
    gamble: Skill
    inquiry: Skill
    interrogation: Skill
    intimidate: Skill
    invocation: Skill
    lipReading: Skill
    literacy: Skill
    logic: Skill
    medicae: Skill
    navigation: GroupSkill
    performer: GroupSkill
    pilot: GroupSkill
    psyniscience: Skill
    scholasticLore: GroupSkill
    scrutiny: Skill
    search: Skill
    secretTongue: GroupSkill
    security: Skill
    shadowing: Skill
    silentMove: Skill
    sleightOfHand: Skill
    speakLanguage: GroupSkill
    survival: Skill
    swim: Skill
    techUse: Skill
    tracking: Skill
    trade: GroupSkill
    wrangling: Skill
    additionalSkills: list[AdditionalSkill] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# condition tracks
# --------------------------------------------------------------------------------------


class Wounds(Strict):
    totalWounds: Count = None
    #: May be negative when the character is taking critical damage.
    currentWounds: Annotated[int | None, BeforeValidator(_to_int)] = None
    #: Free text: players record the effect, not just a number.
    criticalDamage: Text = None
    fatigue: Count = None


class FatePoints(Strict):
    totalFatePoints: Count = None
    currentFatePoints: Count = None


class ArmourLocation(Strict):
    location: str
    hitRoll: str
    hitRollMin: int = Field(ge=1, le=100)
    #: The printed "00" is normalised to 100.
    hitRollMax: int = Field(ge=1, le=100)
    type: Text = None
    armourPoints: Count = None


class Armour(Strict):
    head: ArmourLocation
    rightArm: ArmourLocation
    leftArm: ArmourLocation
    body: ArmourLocation
    rightLeg: ArmourLocation
    leftLeg: ArmourLocation


class Insanity(Strict):
    currentPoints: Count = None
    degreeOfMadness: Text = None
    disorders: list[str] = Field(default_factory=list)


class Corruption(Strict):
    currentPoints: Count = None
    degreeOfCorruption: Text = None
    malignancies: list[str] = Field(default_factory=list)


class Movement(Strict):
    halfAction: Count = None
    fullAction: Count = None
    charge: Count = None
    run: Count = None


# --------------------------------------------------------------------------------------
# page 2
# --------------------------------------------------------------------------------------


class RangedWeapon(Strict):
    name: Text = None
    class_: Text = Field(default=None, alias="class")
    damage: Text = None
    damageType: Text = None
    penetration: Count = None
    #: Metres. An integer since the schema was tightened; "100m" still parses.
    range: Distance = None
    #: Text, because of the "S/3/10" notation.
    rateOfFire: Text = None
    clip: Count = None
    reload: Text = None
    specialRules: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class MeleeWeapon(Strict):
    name: Text = None
    class_: Text = Field(default=None, alias="class")
    damage: Text = None
    damageType: Text = None
    penetration: Count = None
    specialRules: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class Weapons(Strict):
    ranged: list[RangedWeapon] = Field(default_factory=list)
    melee: list[MeleeWeapon] = Field(default_factory=list)


class NamedEntry(Strict):
    name: str
    #: Parenthesised qualifier where present, e.g. "Weapon Training (Las)".
    specialisation: Text = None
    notes: Text = None

    @model_validator(mode="before")
    @classmethod
    def _name_from_notes(cls, data):
        """A row whose only legible text landed in ``notes`` is still a row.

        The talents block is two lines side by side; when the left one is illegible the
        model returns ``name: null`` with the right-hand text in ``notes``. A required
        ``name`` then fails validation and, with it, the whole document. That handwriting
        is the entry's identity as far as anyone can tell, so it becomes the name.
        """
        if isinstance(data, dict) and not data.get("name"):
            fallback = data.get("notes") or data.get("specialisation")
            if isinstance(fallback, str) and fallback.strip():
                data = {**data, "name": fallback.strip(), "notes": None}
        return data


class TalentsAndTraits(Strict):
    homeworldBackground: list[NamedEntry] = Field(default_factory=list)
    advancesTalentsAndTraits: list[NamedEntry] = Field(default_factory=list)


class GearItem(Strict):
    name: str
    #: Parsed from leading counts such as "3 x frag grenade".
    quantity: Count = None
    notes: Text = None

    @model_validator(mode="before")
    @classmethod
    def _keep_unparseable_quantity(cls, data):
        """A quantity that is not a number is still information, just not a number.

        Live models put ``"(100)"`` and the like into ``quantity``. Rejecting the whole
        item for it would lose the name; silently nulling it would lose the text. It moves
        to ``notes``, where the reviewer can see it and decide.
        """
        if not isinstance(data, dict):
            return data
        quantity = data.get("quantity")
        if isinstance(quantity, str) and _to_int(quantity) is quantity:
            note = quantity.strip()
            if note:
                existing = data.get("notes")
                data = {
                    **data,
                    "quantity": None,
                    "notes": f"{existing} {note}".strip() if isinstance(existing, str) else note,
                }
            else:
                data = {**data, "quantity": None}
        if not data.get("name") and isinstance(data.get("notes"), str) and data["notes"].strip():
            data = {**data, "name": data["notes"].strip(), "notes": None}
        return data


class WeaponTrainingGroup(Strict):
    bolt: bool = False
    flame: bool = False
    las: bool = False
    launcher: bool = False
    melta: bool = False
    plasma: bool = False
    primitive: bool = False
    sp: bool = False


class MeleeWeaponTraining(Strict):
    primitive: bool = False
    chain: bool = False
    shock: bool = False
    power: bool = False


class ExoticWeaponTraining(Strict):
    weapon: str


class WeaponTraining(Strict):
    basic: WeaponTrainingGroup = Field(default_factory=WeaponTrainingGroup)
    pistol: WeaponTrainingGroup = Field(default_factory=WeaponTrainingGroup)
    melee: MeleeWeaponTraining = Field(default_factory=MeleeWeaponTraining)
    exotic: list[ExoticWeaponTraining] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# page 3
# --------------------------------------------------------------------------------------


class AdvanceEntry(Strict):
    advance: Text = None
    cost: Count = None


class RankAdvances(Strict):
    rank: int = Field(ge=1, le=8)
    entries: list[AdvanceEntry] = Field(default_factory=list)


class Advances(Strict):
    rankAdvances: list[RankAdvances] = Field(default_factory=list, max_length=8)
    eliteAdvances: list[AdvanceEntry] = Field(default_factory=list)
    totalExperience: Count = None
    spentExperience: Count = None


# --------------------------------------------------------------------------------------
# pages 4-5
# --------------------------------------------------------------------------------------


class MinorPower(Strict):
    name: str
    #: Pre-printed for the 32 standard powers; written in for custom rows.
    threshold: Count = None
    focus: FocusAction | None = None
    sustain: bool | None = None
    #: True when the row was written onto a blank line rather than pre-printed.
    isCustom: bool = False


class PsychicPower(Strict):
    name: Text = None
    threshold: Count = None
    #: Free text, e.g. "Half Action", "1 minute".
    focusTime: Text = None
    #: Free text rather than boolean: entries such as "Half Action" are common.
    sustained: Text = None
    #: Metres. A power whose range is a formula ("10 x PR metres") has no integer to
    #: record; that belongs in ``description``.
    range: Distance = None
    description: Text = None


class Psychic(Strict):
    psyRating: Count = None
    psychicDiscipline: Text = None
    minorPowers: list[MinorPower] = Field(default_factory=list)
    powers: list[PsychicPower] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# note pages
# --------------------------------------------------------------------------------------


class NotePage(Strict):
    """A page of free text that is not part of the printed form.

    Players attach things to a character sheet that the form has no box for: session
    notes, a background write-up, a page of scribbles. The pipeline used to report such a
    page as an unassigned fragment reading "(PDF page 6)", which is a way of saying "there
    was something here" without saying what. A note page holds the actual text.
    """

    title: Text = None
    text: Text = None
    #: Set when the page was transcribed from the uploaded PDF, so the reviewer can put it
    #: beside the scan it came from. Null for a page the user added by hand.
    sourcePdfPage: Annotated[int | None, BeforeValidator(_to_int)] = Field(default=None, ge=1)


# --------------------------------------------------------------------------------------
# root
# --------------------------------------------------------------------------------------


class CharacterSheet(Strict):
    bio: Bio
    characteristics: Characteristics
    skills: Skills
    wounds: Wounds
    fatePoints: FatePoints
    armour: Armour
    insanity: Insanity
    corruption: Corruption
    movement: Movement
    weapons: Weapons
    talentsAndTraits: TalentsAndTraits
    gear: list[GearItem] = Field(default_factory=list)
    weaponTraining: WeaponTraining
    advances: Advances
    psychic: Psychic
    #: Not in the schema's ``required`` list: a character written before note pages
    #: existed is still valid, and gains an empty list the first time it is saved.
    notePages: list[NotePage] = Field(default_factory=list)

    def to_json_dict(self) -> dict:
        """Serialise for ``character.json``, using schema property names throughout."""
        return self.model_dump(mode="json", by_alias=True)
