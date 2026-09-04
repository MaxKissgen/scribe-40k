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

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CharacteristicAbbrev = Literal["WS", "BS", "S", "T", "Ag", "Int", "Per", "WP", "Fel"]
ProficiencyLevel = Literal["Untrained", "Basic", "Trained", "+10", "+20"]
FocusAction = Literal["Free", "Half", "Full", "Reaction"]


class Strict(BaseModel):
    """Base for every sheet object: rejects properties the schema does not define."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------------------
# bio
# --------------------------------------------------------------------------------------


class Bio(Strict):
    characterName: str | None = None
    playerName: str | None = None
    career: str | None = None
    rank: str | None = None
    homeWorld: str | None = None
    quirk: str | None = None
    divination: str | None = None
    ordoOrFaction: str | None = None
    #: The three printed lines joined with newlines preserved.
    description: str | None = None


# --------------------------------------------------------------------------------------
# characteristics
# --------------------------------------------------------------------------------------


class Characteristic(Strict):
    abbreviation: CharacteristicAbbrev
    #: Starting value before characteristic advances.
    base: int | None = Field(default=None, ge=0, le=100)
    #: Ticked boxes in the "Characteristic Advances" row, 0-4. Each is worth +5.
    advancesTaken: int | None = Field(default=None, ge=0, le=4)
    #: The value written in the circle. Stored rather than derived, because temporary or
    #: item modifiers can make the written value differ from base + 5 * advances.
    total: int | None = Field(default=None, ge=0, le=100)
    #: floor(total / 10).
    bonus: int | None = Field(default=None, ge=0, le=10)


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


class SkillProficiency(Strict):
    level: ProficiencyLevel
    modifier: ProficiencyModifier


class Skill(Strict):
    characteristic: CharacteristicAbbrev
    #: Printed information, not a player choice.
    isBasicSkill: bool
    proficiency: SkillProficiency
    notes: str | None = None


class SkillSpecialisation(Strict):
    #: The text written on the blank line, e.g. "Imperium" for Common Lore.
    subject: str
    proficiency: SkillProficiency
    notes: str | None = None


class GroupSkill(Strict):
    """A skill marked with a dagger: no proficiency of its own, only specialisations."""

    characteristic: CharacteristicAbbrev
    isBasicSkill: bool
    specialisations: list[SkillSpecialisation] = Field(default_factory=list)
    notes: str | None = None


class AdditionalSkill(Strict):
    """A skill written onto a blank line that belongs to no printed group skill."""

    name: str
    characteristic: CharacteristicAbbrev | None = None
    isBasicSkill: bool | None = None
    proficiency: SkillProficiency
    notes: str | None = None


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
    totalWounds: int | None = Field(default=None, ge=0)
    #: May be negative when the character is taking critical damage.
    currentWounds: int | None = None
    #: Free text: players record the effect, not just a number.
    criticalDamage: str | None = None
    fatigue: int | None = Field(default=None, ge=0)


class FatePoints(Strict):
    totalFatePoints: int | None = Field(default=None, ge=0)
    currentFatePoints: int | None = Field(default=None, ge=0)


class ArmourLocation(Strict):
    location: str
    hitRoll: str
    hitRollMin: int = Field(ge=1, le=100)
    #: The printed "00" is normalised to 100.
    hitRollMax: int = Field(ge=1, le=100)
    type: str | None = None
    armourPoints: int | None = Field(default=None, ge=0)


class Armour(Strict):
    head: ArmourLocation
    rightArm: ArmourLocation
    leftArm: ArmourLocation
    body: ArmourLocation
    rightLeg: ArmourLocation
    leftLeg: ArmourLocation


class Insanity(Strict):
    currentPoints: int | None = Field(default=None, ge=0)
    degreeOfMadness: str | None = None
    disorders: list[str] = Field(default_factory=list)


class Corruption(Strict):
    currentPoints: int | None = Field(default=None, ge=0)
    degreeOfCorruption: str | None = None
    malignancies: list[str] = Field(default_factory=list)


class Movement(Strict):
    halfAction: int | None = Field(default=None, ge=0)
    fullAction: int | None = Field(default=None, ge=0)
    charge: int | None = Field(default=None, ge=0)
    run: int | None = Field(default=None, ge=0)


# --------------------------------------------------------------------------------------
# page 2
# --------------------------------------------------------------------------------------


class RangedWeapon(Strict):
    name: str | None = None
    class_: str | None = Field(default=None, alias="class")
    damage: str | None = None
    damageType: str | None = None
    penetration: int | None = Field(default=None, ge=0)
    range: str | None = None
    #: Text, because of the "S/3/10" notation.
    rateOfFire: str | None = None
    clip: int | None = Field(default=None, ge=0)
    reload: str | None = None
    specialRules: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class MeleeWeapon(Strict):
    name: str | None = None
    class_: str | None = Field(default=None, alias="class")
    damage: str | None = None
    damageType: str | None = None
    penetration: int | None = Field(default=None, ge=0)
    specialRules: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class Weapons(Strict):
    ranged: list[RangedWeapon] = Field(default_factory=list)
    melee: list[MeleeWeapon] = Field(default_factory=list)


class NamedEntry(Strict):
    name: str
    #: Parenthesised qualifier where present, e.g. "Weapon Training (Las)".
    specialisation: str | None = None
    notes: str | None = None


class TalentsAndTraits(Strict):
    homeworldBackground: list[NamedEntry] = Field(default_factory=list)
    advancesTalentsAndTraits: list[NamedEntry] = Field(default_factory=list)


class GearItem(Strict):
    name: str
    #: Parsed from leading counts such as "3 x frag grenade".
    quantity: int | None = Field(default=None, ge=0)
    notes: str | None = None


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
    advance: str | None = None
    cost: int | None = Field(default=None, ge=0)


class RankAdvances(Strict):
    rank: int = Field(ge=1, le=8)
    entries: list[AdvanceEntry] = Field(default_factory=list)


class Advances(Strict):
    rankAdvances: list[RankAdvances] = Field(default_factory=list, max_length=8)
    eliteAdvances: list[AdvanceEntry] = Field(default_factory=list)
    totalExperience: int | None = Field(default=None, ge=0)
    spentExperience: int | None = Field(default=None, ge=0)


# --------------------------------------------------------------------------------------
# pages 4-5
# --------------------------------------------------------------------------------------


class MinorPower(Strict):
    name: str
    #: Pre-printed for the 32 standard powers; written in for custom rows.
    threshold: int | None = Field(default=None, ge=0)
    focus: FocusAction | None = None
    sustain: bool | None = None
    #: True when the row was written onto a blank line rather than pre-printed.
    isCustom: bool = False


class PsychicPower(Strict):
    name: str | None = None
    threshold: int | None = Field(default=None, ge=0)
    #: Free text, e.g. "Half Action", "1 minute".
    focusTime: str | None = None
    #: Free text rather than boolean: entries such as "Half Action" are common.
    sustained: str | None = None
    range: str | None = None
    description: str | None = None


class Psychic(Strict):
    psyRating: int | None = Field(default=None, ge=0)
    psychicDiscipline: str | None = None
    minorPowers: list[MinorPower] = Field(default_factory=list)
    powers: list[PsychicPower] = Field(default_factory=list)


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

    def to_json_dict(self) -> dict:
        """Serialise for ``character.json``, using schema property names throughout."""
        return self.model_dump(mode="json", by_alias=True)
