"""Regressions from the first run against real models.

Every test here corresponds to something the calibration scan did to the pipeline that the
recorded fixtures had not. The transcription excerpts are verbatim from Mistral OCR.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from scribe40k.blank import blank_character
from scribe40k.llm.base import OcrPage, PageImage, ReasoningResponse
from scribe40k.models import CharacterSheet, RangedWeapon, SkillSpecialisation
from scribe40k.pipeline.mapper import run_section
from scribe40k.pipeline.sections import SECTION_BY_NAME
from scribe40k.pipeline.skill_guard import guard_skill_levels, ocr_row_ticks
from scribe40k.pipeline.validate import validate_document
from scribe40k.pointer import deep_merge

#: A slice of the real skills table. Awareness and Barter are Basic skills the player
#: never touched; their single tick is the printed square. Demolition is Advanced with
#: two real ticks. Medicae is Advanced with one.
OCR_SKILLS = """\
|  Acrobatics (Ag) | ☐ | ☐ | ☐ | ☐ | Evaluate | ☑ | ☐ | ☐  |
|  Awareness (Per) | ☑ | ☐ | ☐ | ☐ | Forbidden Lore (Int)† | ☐ |  |   |
|  Barter (Fel) | ☑ | ☐ | ☐ | ☐ |  | ☐ | ☐ | ☐  |
|  Carousel (T) | ☑ | ☐ | ☐ | ☐ |  | ☐ | ☐ | ☐  |
|  Demolition (Int) | ☐ | ☑ | ☑ | ☐ |  | ☐ | ☐ | ☐  |
|   | ☐ | ☐ | ☐ | ☐ | Medicae (Int) | ☐ | ☑ | ☐  |
|   |  |  |  |  | Tech-Use (Int) | ☑ | ☐ | ☑  |
"""


class TestOcrRowTicks:
    def test_counts_ticks_after_each_label(self) -> None:
        rows = ocr_row_ticks(OCR_SKILLS)

        assert rows["awareness"].ticked == 1
        assert rows["demolition"].ticked == 2
        assert rows["acrobatics"].ticked == 0

    def test_reads_the_second_skill_on_a_row_too(self) -> None:
        rows = ocr_row_ticks(OCR_SKILLS)

        assert rows["medicae"].ticked == 1
        assert rows["techUse"].ticked == 2
        # Evaluate's row lost a box in transcription; three is still enough to parse.
        assert rows["evaluate"].ticked == 1

    def test_tolerates_ocr_typos_in_the_bracket(self) -> None:
        """'Carousel (T)' is what the OCR made of 'Carouse (T)'."""
        assert ocr_row_ticks(OCR_SKILLS)["carouse"].ticked == 1

    def test_stops_at_the_next_label(self) -> None:
        """Awareness's boxes must not absorb Forbidden Lore's."""
        assert ocr_row_ticks(OCR_SKILLS)["awareness"].boxes == 4

    def test_ignores_group_skills(self) -> None:
        assert "forbiddenLore" not in ocr_row_ticks(OCR_SKILLS)


class TestGuardSkillLevels:
    def test_the_printed_square_is_not_a_tick(self) -> None:
        """The headline bug: every untouched Basic skill came back as Trained."""
        skills = {"awareness": {"proficiency": {"level": "Trained"}}}

        flags = guard_skill_levels(skills, OCR_SKILLS, sheet_page=1, pdf_page=1)

        assert "awareness" not in skills, "no player marks: the skill should not be reported"
        [flag] = flags
        assert flag.rule == "skill.ocr_tick_mismatch"
        assert flag.alternatives == ["Trained"], "the model's reading stays one click away"
        assert "printed Basic square" in flag.message

    def test_an_advanced_skill_keeps_its_real_ticks(self) -> None:
        skills = {"demolition": {"proficiency": {"level": "+10"}}}

        flags = guard_skill_levels(skills, OCR_SKILLS, sheet_page=1, pdf_page=1)

        assert skills["demolition"]["proficiency"]["level"] == "+10"
        assert flags == []

    def test_over_counting_is_brought_down_not_removed(self) -> None:
        skills = {"techUse": {"proficiency": {"level": "+20"}}}

        guard_skill_levels(skills, OCR_SKILLS, sheet_page=1, pdf_page=1)

        assert skills["techUse"]["proficiency"]["level"] == "+10"

    def test_under_counting_is_left_alone(self) -> None:
        """The model saw the image; the OCR may have invented a tick."""
        skills = {"demolition": {"proficiency": {"level": "Trained"}}}

        flags = guard_skill_levels(skills, OCR_SKILLS, sheet_page=1, pdf_page=1)

        assert skills["demolition"]["proficiency"]["level"] == "Trained"
        assert flags == []

    def test_skills_absent_from_the_transcription_are_untouched(self) -> None:
        skills = {"wrangling": {"proficiency": {"level": "+20"}}}

        assert guard_skill_levels(skills, OCR_SKILLS, sheet_page=1, pdf_page=1) == []
        assert skills["wrangling"]["proficiency"]["level"] == "+20"


class TestMisplacedAdditionalSkills:
    """The model put 'additionalSkills' beside 'skills' rather than inside it."""

    def test_is_folded_into_skills_instead_of_dropped(self, tmp_path) -> None:
        image = tmp_path / "p1.png"
        image.write_bytes(b"png")
        envelope = {
            "data": {
                "skills": {"demolition": {"proficiency": {"level": "+10"}}},
                "additionalSkills": [
                    {
                        "name": "Interrogation (WP)",
                        "characteristic": "WP",
                        "proficiency": {"level": "Trained"},
                    }
                ],
            }
        }

        class Stub:
            name = model = "stub"
            supports_vision = supports_structured_output = True

            def complete(self, request):
                return ReasoningResponse(text=json.dumps(envelope), parsed=envelope)

        result = run_section(
            SECTION_BY_NAME["skills"],
            Stub(),
            {1: OcrPage(pdf_page=1, sheet_page=1, text=OCR_SKILLS)},
            {1: PageImage(pdf_page=1, path=image, sheet_page=1)},
        )

        assert result.record.status == "ok"
        assert result.record.error is None, "nothing should have been reported as ignored"
        assert result.data["skills"]["additionalSkills"][0]["name"] == "Interrogation (WP)"


class TestPartialFragmentsFromLiveModels:
    def test_a_number_in_a_text_field_is_accepted_as_text(self) -> None:
        """A damage of '7' came back as the integer 7 and failed the schema."""
        weapon = RangedWeapon.model_validate({"damage": 7, "reload": 2.0})

        assert (weapon.damage, weapon.reload) == ("7", "2")

    def test_a_bool_in_a_text_field_still_fails(self) -> None:
        with pytest.raises(ValidationError):
            RangedWeapon.model_validate({"damage": True})

    def test_a_range_keeps_its_number_and_loses_its_unit(self) -> None:
        """RANGE became an integer in the schema; players still write '60m'."""
        assert RangedWeapon.model_validate({"range": 60}).range == 60
        assert RangedWeapon.model_validate({"range": "60m"}).range == 60
        assert RangedWeapon.model_validate({"range": "30 metres"}).range == 30

    def test_a_range_that_is_not_a_distance_still_fails(self) -> None:
        with pytest.raises(ValidationError):
            RangedWeapon.model_validate({"range": "10 x PR metres"})

    def test_a_specialisation_without_proficiency_defaults_to_trained(self) -> None:
        assert SkillSpecialisation.model_validate({"subject": "Tech"}).proficiency.level == (
            "Trained"
        )

    def test_one_bare_specialisation_no_longer_sinks_the_whole_document(self) -> None:
        """On the live run this single omission made the entire sheet unparseable,
        which meant no derived values and no consistency checks at all."""
        document = blank_character().to_json_dict()
        deep_merge(
            document,
            {
                "characteristics": {"agility": {"total": 28}},
                "skills": {"commonLore": {"specialisations": [{"subject": "Imperium"}]}},
                "weapons": {"ranged": [{"name": "Las pistol", "range": "60m"}]},
            },
        )

        finished, flags = validate_document(document)

        assert not any(f.rule == "document.unparseable" for f in flags)
        assert finished["characteristics"]["agility"]["bonus"] == 2, "derivations ran"
        assert finished["weapons"]["ranged"][0]["range"] == 60
        CharacterSheet.model_validate(finished)


class TestRowsWithTheirTextInTheWrongCell:
    """The talents and gear blocks are two lines side by side, and models file the text
    unpredictably between them. A row must survive whichever cell its text landed in."""

    def test_a_talent_with_only_notes_becomes_that_talent(self) -> None:
        from scribe40k.models import NamedEntry

        entry = NamedEntry.model_validate({"name": None, "notes": "100' dilum (100 m/s)"})

        assert entry.name == "100' dilum (100 m/s)"
        assert entry.notes is None

    def test_a_real_name_is_not_overwritten_by_notes(self) -> None:
        from scribe40k.models import NamedEntry

        entry = NamedEntry.model_validate({"name": "Electro-graft", "notes": "+10 Tech-Use"})

        assert (entry.name, entry.notes) == ("Electro-graft", "+10 Tech-Use")

    def test_a_bracketed_quantity_moves_to_notes(self) -> None:
        from scribe40k.models import GearItem

        item = GearItem.model_validate({"name": "Spare parts", "quantity": "(100)"})

        assert item.quantity is None
        assert item.notes == "(100)"

    def test_a_bracketed_quantity_joins_existing_notes(self) -> None:
        from scribe40k.models import GearItem

        item = GearItem.model_validate(
            {"name": "Spare parts", "quantity": "(3)", "notes": "(crafting)"}
        )

        assert item.notes == "(crafting) (3)"

    def test_a_numeric_string_quantity_is_a_number(self) -> None:
        from scribe40k.models import GearItem

        assert GearItem.model_validate({"name": "stimm", "quantity": " 3 "}).quantity == 3

    def test_an_integer_field_written_as_a_string_is_accepted(self) -> None:
        """Telling the model 'text fields are strings' made it stringify the numbers."""
        assert RangedWeapon.model_validate({"clip": "30", "penetration": "0"}).clip == 30

    def test_a_non_numeric_string_in_an_integer_field_still_fails(self) -> None:
        with pytest.raises(ValidationError):
            RangedWeapon.model_validate({"clip": "S/3/10"})

    def test_a_bounded_integer_written_as_a_string_is_still_bounded(self) -> None:
        from scribe40k.models import Characteristic

        assert Characteristic.model_validate({"abbreviation": "WS", "total": "28"}).total == 28
        with pytest.raises(ValidationError):
            Characteristic.model_validate({"abbreviation": "WS", "total": "128"})


class TestAnUnloadableDocumentSaysWhy:
    """A null in a required field used to bury the one real problem under eight fake ones.

    With nothing loaded, nothing is derived, and the schema then objects to every derived
    key that is consequently missing -- errors about objects the user never touched, which
    do not go away when they fix the field that actually broke.
    """

    def _document_with_a_null_gear_name(self) -> dict:
        document = blank_character().to_json_dict()
        document["gear"] = [{"name": None, "quantity": None, "notes": None}]
        return document

    def test_the_blocking_value_is_flagged_where_it_is(self) -> None:
        _, flags = validate_document(self._document_with_a_null_gear_name())

        blocking = [f for f in flags if f.rule == "document.invalid_value"]
        assert [f.pointer for f in blocking] == ["/gear/0/name"]
        assert "valid string" in blocking[0].message

    def test_nothing_else_is_reported(self) -> None:
        """Only the root summary and the value itself."""
        _, flags = validate_document(self._document_with_a_null_gear_name())

        assert {f.rule for f in flags} == {"document.unparseable", "document.invalid_value"}

    def test_filling_the_value_clears_every_flag_it_caused(self) -> None:
        document = self._document_with_a_null_gear_name()
        document["gear"][0]["name"] = "Lho sticks"

        _, flags = validate_document(document)

        assert not [f for f in flags if f.rule.startswith(("document.", "schema."))]
