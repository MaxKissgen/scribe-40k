"""Derivations must be mechanical, and consistency rules must not silently correct."""

from __future__ import annotations

import pytest

from scribe40k.blank import blank_character, proficiency
from scribe40k.derive import (
    apply_derivations,
    characteristic_bonus,
    check_consistency,
    expected_movement,
    skill_target,
    split_quantity,
)
from scribe40k.models import GearItem, MinorPower, SkillSpecialisation


class TestCharacteristicBonus:
    @pytest.mark.parametrize(
        ("total", "bonus"), [(None, None), (0, 0), (9, 0), (10, 1), (38, 3), (99, 9), (100, 10)]
    )
    def test_is_the_tens_digit(self, total: int | None, bonus: int | None) -> None:
        assert characteristic_bonus(total) == bonus

    def test_applied_across_all_nine(self) -> None:
        sheet = blank_character()
        sheet.characteristics.agility.total = 28
        sheet.characteristics.intelligence.total = 57
        changed = apply_derivations(sheet)

        assert sheet.characteristics.agility.bonus == 2
        assert sheet.characteristics.intelligence.bonus == 5
        assert "/characteristics/agility/bonus" in changed


class TestQuantityParsing:
    @pytest.mark.parametrize(
        ("raw", "qty", "name"),
        [
            ("3 x frag grenade", 3, "frag grenade"),
            ("2 × stimm", 2, "stimm"),
            ("10 * lho stick", 10, "lho stick"),
            ("4 charge packs", 4, "charge packs"),
            ("flak coat", None, "flak coat"),
            ("  autogun  ", None, "autogun"),
        ],
    )
    def test_leading_counts(self, raw: str, qty: int | None, name: str) -> None:
        assert split_quantity(raw) == (qty, name)

    def test_glued_number_is_not_a_count(self) -> None:
        """ "9mm rounds" is an item name, not nine of something."""
        assert split_quantity("9mm rounds") == (None, "9mm rounds")

    def test_applied_to_gear(self) -> None:
        sheet = blank_character()
        sheet.gear = [GearItem(name="3 x frag grenade"), GearItem(name="flak coat")]
        apply_derivations(sheet)

        assert (sheet.gear[0].quantity, sheet.gear[0].name) == (3, "frag grenade")
        assert (sheet.gear[1].quantity, sheet.gear[1].name) == (None, "flak coat")


class TestProficiencySync:
    def test_a_tampered_modifier_is_repaired(self) -> None:
        sheet = blank_character()
        sheet.skills.dodge.proficiency = proficiency("+20")
        sheet.skills.dodge.proficiency.modifier.flatBonus = 0  # wrong on purpose

        changed = apply_derivations(sheet)

        assert sheet.skills.dodge.proficiency.modifier.flatBonus == 20
        assert "/skills/dodge/proficiency/modifier" in changed

    def test_specialisations_are_synced_too(self) -> None:
        sheet = blank_character()
        sheet.skills.commonLore.specialisations = [
            SkillSpecialisation(subject="Imperium", proficiency=proficiency("+10"))
        ]
        sheet.skills.commonLore.specialisations[0].proficiency.modifier.flatBonus = 0

        apply_derivations(sheet)

        assert sheet.skills.commonLore.specialisations[0].proficiency.modifier.flatBonus == 10


class TestSkillTarget:
    def test_untrained_advanced_skill_is_untestable(self) -> None:
        sheet = blank_character()
        sheet.characteristics.intelligence.total = 40
        assert skill_target(sheet, "Int", sheet.skills.medicae.proficiency) is None

    def test_basic_skill_uses_half_characteristic(self) -> None:
        sheet = blank_character()
        sheet.characteristics.agility.total = 45
        # Dodge is a Basic Skill, so its resting state is Basic.
        assert skill_target(sheet, "Ag", sheet.skills.dodge.proficiency) == 22

    def test_trained_uses_full_characteristic(self) -> None:
        sheet = blank_character()
        sheet.characteristics.agility.total = 45
        sheet.skills.dodge.proficiency = proficiency("Trained")
        assert skill_target(sheet, "Ag", sheet.skills.dodge.proficiency) == 45

    def test_advances_add_flat_bonuses(self) -> None:
        sheet = blank_character()
        sheet.characteristics.agility.total = 45
        sheet.skills.dodge.proficiency = proficiency("+20")
        assert skill_target(sheet, "Ag", sheet.skills.dodge.proficiency) == 65


class TestMovement:
    def test_derived_from_agility_bonus(self) -> None:
        assert expected_movement(3) == {
            "halfAction": 3,
            "fullAction": 6,
            "charge": 9,
            "run": 18,
        }

    def test_mismatch_is_flagged_not_corrected(self) -> None:
        sheet = blank_character()
        sheet.characteristics.agility.total = 28  # bonus 2
        apply_derivations(sheet)
        sheet.movement.halfAction = 2
        sheet.movement.fullAction = 9  # should be 4

        findings = check_consistency(sheet)
        rules = {f.rule for f in findings}

        assert "movement.mismatch" in rules
        assert sheet.movement.fullAction == 9, "consistency checks must not mutate the sheet"


class TestConsistencyRules:
    def test_total_mismatch_is_a_warning_not_an_error(self) -> None:
        sheet = blank_character()
        c = sheet.characteristics.weaponSkill
        c.base, c.advancesTaken, c.total = 28, 1, 40  # 28 + 5 != 40

        findings = [
            f for f in check_consistency(sheet) if f.rule == "characteristic.total_mismatch"
        ]

        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert findings[0].expected == 33

    def test_current_wounds_above_total(self) -> None:
        sheet = blank_character()
        sheet.wounds.totalWounds, sheet.wounds.currentWounds = 12, 17

        assert any(f.rule == "wounds.current_exceeds_total" for f in check_consistency(sheet))

    def test_overspent_experience(self) -> None:
        sheet = blank_character()
        sheet.advances.totalExperience, sheet.advances.spentExperience = 700, 900

        assert any(f.rule == "advances.overspent" for f in check_consistency(sheet))

    def test_basic_level_on_an_advanced_skill_is_an_error(self) -> None:
        sheet = blank_character()
        sheet.skills.medicae.proficiency = proficiency("Basic")  # Medicae is Advanced

        findings = [
            f for f in check_consistency(sheet) if f.rule == "skill.basic_on_advanced_skill"
        ]

        assert len(findings) == 1
        assert findings[0].severity == "error"

    def test_specialisation_without_a_subject_is_an_error(self) -> None:
        sheet = blank_character()
        sheet.skills.forbiddenLore.specialisations = [
            SkillSpecialisation(subject="   ", proficiency=proficiency("Trained"))
        ]

        findings = [
            f for f in check_consistency(sheet) if f.rule == "skill.specialisation_without_subject"
        ]

        assert len(findings) == 1

    def test_a_clean_blank_sheet_reports_only_missing_characteristics(self) -> None:
        findings = check_consistency(blank_character())
        assert {f.rule for f in findings} == {"characteristic.missing_total"}
        assert len(findings) == 9


class TestMinorPowerPrintedValues:
    def test_printed_values_override_whatever_the_model_read(self) -> None:
        sheet = blank_character()
        sheet.psychic.minorPowers = [
            MinorPower(name="Spectral Hands", threshold=1, focus="Half", sustain=True)
        ]

        apply_derivations(sheet)
        power = sheet.psychic.minorPowers[0]

        assert (power.threshold, power.focus, power.sustain) == (10, "Full", False)

    def test_custom_rows_are_left_alone(self) -> None:
        sheet = blank_character()
        sheet.psychic.minorPowers = [
            MinorPower(
                name="Homebrew Power", threshold=4, focus="Free", sustain=True, isCustom=True
            )
        ]

        apply_derivations(sheet)

        assert sheet.psychic.minorPowers[0].threshold == 4
