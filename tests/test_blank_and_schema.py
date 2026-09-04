"""The blank sheet must validate against the real schema, not a paraphrase of it.

These tests are the guard against model/schema drift: every other component assumes it can
serialise a ``CharacterSheet`` and get a schema-valid document.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from scribe40k import constants as K
from scribe40k.blank import blank_character, proficiency
from scribe40k.models import CharacterSheet
from scribe40k.paths import CHARACTER_SCHEMA


@pytest.fixture(scope="session")
def schema() -> dict:
    return json.loads(CHARACTER_SCHEMA.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def validator(schema: dict) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_blank_character_validates(validator: Draft202012Validator) -> None:
    doc = blank_character().to_json_dict()
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:20])


def test_blank_character_round_trips(validator: Draft202012Validator) -> None:
    doc = blank_character().to_json_dict()
    reparsed = CharacterSheet.model_validate(doc)
    assert reparsed.to_json_dict() == doc


def test_every_required_root_key_present(schema: dict) -> None:
    doc = blank_character().to_json_dict()
    assert set(schema["required"]) <= set(doc)


def test_nullable_leaves_are_present_not_omitted() -> None:
    """A blank field must serialise as null, never disappear."""
    doc = blank_character().to_json_dict()
    assert doc["bio"]["characterName"] is None
    assert "characterName" in doc["bio"]
    assert doc["wounds"]["totalWounds"] is None
    assert "criticalDamage" in doc["wounds"]


class TestPrintedConstants:
    def test_skill_count_matches_schema(self, schema: dict) -> None:
        required = set(schema["properties"]["skills"]["required"]) - {"additionalSkills"}
        assert {s.key for s in K.SKILLS} == required

    def test_skill_characteristics_match_schema(self, schema: dict) -> None:
        props = schema["properties"]["skills"]["properties"]
        for spec in K.SKILLS:
            want = props[spec.key]["properties"]["characteristic"]["const"]
            assert spec.characteristic == want, f"{spec.key}: {spec.characteristic} != {want}"

    def test_basic_skill_flags_match_schema(self, schema: dict) -> None:
        props = schema["properties"]["skills"]["properties"]
        for spec in K.SKILLS:
            want = props[spec.key]["properties"]["isBasicSkill"]["const"]
            assert spec.is_basic == want, f"{spec.key}: {spec.is_basic} != {want}"

    def test_group_skills_are_exactly_the_daggered_ones(self, schema: dict) -> None:
        props = schema["properties"]["skills"]["properties"]
        from_schema = {
            key
            for key, node in props.items()
            if key != "additionalSkills" and node.get("$ref", "").endswith("groupSkill")
        }
        assert from_schema == K.GROUP_SKILL_KEYS

    def test_armour_locations_match_schema(self, schema: dict) -> None:
        props = schema["properties"]["armour"]["properties"]
        for spec in K.ARMOUR_LOCATIONS:
            node = props[spec.key]["properties"]
            assert spec.location == node["location"]["const"]
            assert spec.hit_roll == node["hitRoll"]["const"]
            assert spec.hit_roll_min == node["hitRollMin"]["const"]
            assert spec.hit_roll_max == node["hitRollMax"]["const"]

    def test_characteristic_abbreviations_match_schema(self, schema: dict) -> None:
        props = schema["properties"]["characteristics"]["properties"]
        for spec in K.CHARACTERISTICS:
            assert spec.abbreviation == props[spec.key]["properties"]["abbreviation"]["const"]

    def test_all_32_printed_minor_powers_are_listed(self, schema: dict) -> None:
        described = schema["properties"]["psychic"]["properties"]["minorPowers"]["description"]
        assert len(K.MINOR_POWERS) == 32
        for power in K.MINOR_POWERS:
            assert power.name in described, f"{power.name} missing from the schema's row list"

    def test_basic_skills_start_usable_and_advanced_do_not(self) -> None:
        sheet = blank_character()
        for spec in K.SKILLS:
            if spec.is_group:
                continue
            skill = getattr(sheet.skills, spec.key)
            assert skill.proficiency.modifier.usable is spec.is_basic


class TestProficiencyTable:
    @pytest.mark.parametrize(
        ("level", "usable", "multiplier", "flat"),
        [
            ("Untrained", False, 0, 0),
            ("Basic", True, 0.5, 0),
            ("Trained", True, 1, 0),
            ("+10", True, 1, 10),
            ("+20", True, 1, 20),
        ],
    )
    def test_matches_schema_prose(
        self, level: str, usable: bool, multiplier: float, flat: int
    ) -> None:
        prof = proficiency(level)
        assert prof.modifier.usable is usable
        assert prof.modifier.characteristicMultiplier == multiplier
        assert prof.modifier.flatBonus == flat
