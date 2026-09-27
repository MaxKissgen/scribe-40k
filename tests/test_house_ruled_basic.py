"""A skill the rulebook calls Advanced, which a GM allows their table to use untrained.

The printed sheet has four boxes per skill -- Basic, Trained, +10%, +20% -- and the first
is not a tick box: it is printed filled for a Basic Skill and printed empty for an
Advanced one. GMs house-rule that, and ink the square in.

So ``isBasicSkill`` stops being a constant in one direction only. A Basic Skill stays
Basic, because a printed square cannot be unprinted. An Advanced one may be marked Basic,
and the sheet says out loud that this is not what the rulebook prints.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from scribe40k import constants as K
from scribe40k.blank import blank_character, proficiency
from scribe40k.derive import check_consistency
from scribe40k.models import CharacterSheet
from scribe40k.paths import CHARACTER_SCHEMA
from scribe40k.pipeline.diff import suggest_updates
from scribe40k.pipeline.skill_guard import guard_skill_levels

#: Advanced in the rulebook, ordinary (not a dagger group skill), and short enough to read.
ADVANCED = "blather"
BASIC = "awareness"


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(CHARACTER_SCHEMA.read_text(encoding="utf-8")))


def _house_ruled() -> dict:
    """A character whose GM allows Blather untrained."""
    document = blank_character().to_json_dict()
    document["skills"][ADVANCED]["isBasicSkill"] = True
    document["skills"][ADVANCED]["proficiency"] = proficiency("Basic").model_dump(mode="json")
    return document


def _rules(findings) -> set[str]:
    return {finding.rule for finding in findings}


class TestTheSchemaAllowsIt:
    def test_an_advanced_skill_may_be_marked_basic(self, validator) -> None:
        assert not list(validator.iter_errors(_house_ruled()))

    def test_a_basic_skill_may_not_be_unmarked(self, validator) -> None:
        document = blank_character().to_json_dict()
        document["skills"][BASIC]["isBasicSkill"] = False

        errors = list(validator.iter_errors(document))

        assert errors, "the square is printed on the paper; it cannot be taken off"
        assert any("isBasicSkill" in list(error.absolute_path) for error in errors)

    def test_a_group_skill_is_still_pinned(self, validator) -> None:
        """The dagger skills' square sits on the header row, above every specialisation."""
        document = blank_character().to_json_dict()
        group = next(spec.key for spec in K.SKILLS if spec.is_group and not spec.is_basic)
        document["skills"][group]["isBasicSkill"] = True

        assert list(validator.iter_errors(document))


class TestTheSheetSaysSo:
    def test_a_house_rule_is_noted_but_not_an_error(self) -> None:
        findings = check_consistency(CharacterSheet.model_validate(_house_ruled()))

        [note] = [f for f in findings if f.rule == "skill.basic_by_house_rule"]
        assert note.severity == "info"
        assert note.pointer == f"/skills/{ADVANCED}/isBasicSkill"
        assert "house rule" in note.message

    def test_basic_at_rest_is_no_longer_an_error_once_it_is_allowed(self) -> None:
        """'Basic' on an Advanced skill used to be an error. Now it depends on the sheet."""
        assert "skill.basic_on_advanced_skill" not in _rules(
            check_consistency(CharacterSheet.model_validate(_house_ruled()))
        )

    def test_and_still_is_where_nobody_allowed_it(self) -> None:
        document = blank_character().to_json_dict()
        document["skills"][ADVANCED]["proficiency"] = proficiency("Basic").model_dump(mode="json")

        assert "skill.basic_on_advanced_skill" in _rules(
            check_consistency(CharacterSheet.model_validate(document))
        )

    def test_an_untouched_character_says_nothing(self) -> None:
        assert not [
            f for f in check_consistency(blank_character()) if f.rule.startswith("skill.basic_")
        ]

    def test_unmarking_a_printed_square_is_an_error(self) -> None:
        """Unreachable through the schema, but the models are looser than the schema."""
        document = blank_character().to_json_dict()
        document["skills"][BASIC]["isBasicSkill"] = False

        findings = check_consistency(CharacterSheet.model_validate(document))

        [problem] = [f for f in findings if f.rule == "skill.basic_square_missing"]
        assert problem.severity == "error"


#: Four boxes each, fully parsed. Blather's Basic column is marked and the sheet prints it
#: empty; Awareness's is the printed square; Demolition's marks are ordinary advances.
OCR_FOUR_BOXES = """\
|  Awareness (Per) | ☑ | ☐ | ☐ | ☐ |
|  Blather (Fel) | ☑ | ☐ | ☐ | ☐ |
|  Demolition (Int) | ☐ | ☑ | ☑ | ☐ |
|  Medicae (Int) | ☑ | ☑ | ☐ | ☐ |
"""


class TestReadingItOffAScan:
    def test_a_marked_basic_column_on_an_advanced_skill_is_noted(self) -> None:
        skills = {ADVANCED: {"proficiency": {"level": "Trained"}}}

        flags = guard_skill_levels(skills, OCR_FOUR_BOXES, sheet_page=1, pdf_page=1)

        [note] = [f for f in flags if f.pointer == f"/skills/{ADVANCED}/isBasicSkill"]
        assert note.severity == "info"
        assert note.rule == "ocr.basic_column_marked"

    def test_the_mark_still_counts_towards_the_level(self) -> None:
        """Read as Trained: a player miscounting columns is likelier than a house rule."""
        skills = {ADVANCED: {"proficiency": {"level": "Trained"}}}

        guard_skill_levels(skills, OCR_FOUR_BOXES, sheet_page=1, pdf_page=1)

        assert skills[ADVANCED]["proficiency"]["level"] == "Trained"

    def test_a_printed_square_is_not_noted(self) -> None:
        flags = guard_skill_levels({}, OCR_FOUR_BOXES, sheet_page=1, pdf_page=1)

        assert not [f for f in flags if f.pointer == f"/skills/{BASIC}/isBasicSkill"]

    def test_nor_is_a_row_whose_marks_are_all_advances(self) -> None:
        flags = guard_skill_levels({}, OCR_FOUR_BOXES, sheet_page=1, pdf_page=1)

        assert not [f for f in flags if f.pointer == "/skills/demolition/isBasicSkill"]

    def test_a_row_that_lost_a_box_says_nothing_about_which_one_was_marked(self) -> None:
        """Tech-Use came back from the calibration scan as three cells, not four."""
        short = "|  Blather (Fel) | ☑ | ☐ | ☑ |\n"

        flags = guard_skill_levels({}, short, sheet_page=1, pdf_page=1)

        assert not [f for f in flags if f.rule == "ocr.basic_column_marked"]

    def test_the_model_reporting_a_house_rule_settles_it(self) -> None:
        """No note to raise, and the marked square stops counting as an advance."""
        skills = {ADVANCED: {"isBasicSkill": True, "proficiency": {"level": "Trained"}}}

        flags = guard_skill_levels(skills, OCR_FOUR_BOXES, sheet_page=1, pdf_page=1)

        assert not [f for f in flags if f.pointer == f"/skills/{ADVANCED}/isBasicSkill"]
        assert ADVANCED not in skills, "the only mark was the classification, not an advance"

    def test_a_house_ruled_skill_with_a_real_advance_keeps_it(self) -> None:
        skills = {"medicae": {"isBasicSkill": True, "proficiency": {"level": "Trained"}}}

        guard_skill_levels(skills, OCR_FOUR_BOXES, sheet_page=1, pdf_page=1)

        assert skills["medicae"]["proficiency"]["level"] == "Trained"


class TestTheNotesOutliveTheirRequest:
    """Rule flags are regenerated from the document on every save. Not all of these are.

    What a transcription showed is not a property of the character, so a flag from the
    scan-time guard cannot be recomputed and has to survive instead -- which the
    ``ocr.`` prefix is what decides. A suggestion filed under the wrong family disappears
    the next time the character is opened, silently, because a flag that vanishes looks
    exactly like a flag that was resolved.
    """

    def test_what_the_scan_showed_is_kept(self) -> None:
        from scribe40k.api import _is_rule_flag

        assert not _is_rule_flag("ocr.basic_column_marked")
        assert not _is_rule_flag("ocr.tick_mismatch")

    def test_what_the_document_says_is_recomputed(self) -> None:
        from scribe40k.api import _is_rule_flag

        assert _is_rule_flag("skill.basic_by_house_rule")
        assert _is_rule_flag("skill.basic_square_missing")


class TestAPrintoutCanProposeIt:
    def test_an_inked_basic_square_comes_back_as_a_suggestion(self) -> None:
        """A house rule gets decided at the table and marked on the paper afterwards."""
        current = blank_character().to_json_dict()

        flags = suggest_updates(current, _house_ruled(), covers={"skills"})

        assert f"/skills/{ADVANCED}/isBasicSkill" in {f.pointer for f in flags}

    def test_but_a_printout_never_proposes_taking_one_away(self) -> None:
        """A reading that missed the square is likelier than a house rule rescinded."""
        flags = suggest_updates(_house_ruled(), blank_character().to_json_dict(), covers={"skills"})

        assert f"/skills/{ADVANCED}/isBasicSkill" not in {f.pointer for f in flags}

    def test_a_governing_characteristic_still_does_not(self) -> None:
        """The rest of the printed constants are unchanged: a scan gets no vote on them."""
        current = blank_character().to_json_dict()
        candidate = blank_character().to_json_dict()
        candidate["skills"][ADVANCED]["characteristic"] = "Int"

        flags = suggest_updates(current, candidate, covers={"skills"})

        assert f"/skills/{ADVANCED}/characteristic" not in {f.pointer for f in flags}
