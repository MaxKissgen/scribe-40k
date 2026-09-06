"""The mapping stage, validation, and the pipeline end to end.

The recorded fixtures under ``tests/fixtures/`` deliberately contain broken data -- current
wounds above total, overspent experience, a Basic level on an Advanced skill, a section job
answering outside its own keys. That is the point: this pipeline's job is to produce a
correctable document from a messy sheet, so the tests exercise messy input rather than
clean input.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from scribe40k import pointer
from scribe40k.blank import blank_character
from scribe40k.llm.base import OcrPage, PageImage, ReasoningResponse
from scribe40k.paths import CHARACTER_SCHEMA, PAGE_FINGERPRINTS, REPORT_SCHEMA, ROOT, SAMPLES
from scribe40k.pipeline.mapper import map_sheet, run_section
from scribe40k.pipeline.report import ExtractionReport, Flag, UnmappedItem, UnmappedSource
from scribe40k.pipeline.sections import ALL_SECTIONS, SECTION_BY_NAME
from scribe40k.pipeline.validate import dedupe_flags, validate_document
from scribe40k.store import CharacterStore

FIXTURES = ROOT / "tests" / "fixtures"
SAMPLE_SCAN = SAMPLES / "Dark Heresy 1 PC Sheet filled.pdf"


# --------------------------------------------------------------------------------------
# JSON Pointer
# --------------------------------------------------------------------------------------


class TestPointer:
    def test_resolves_nested_paths(self) -> None:
        doc = {"a": {"b": [10, 20]}}
        assert pointer.resolve(doc, "/a/b/1") == 20

    def test_empty_pointer_is_the_whole_document(self) -> None:
        doc = {"a": 1}
        assert pointer.resolve(doc, "") is doc

    def test_escaping(self) -> None:
        doc = {"a/b": {"c~d": 1}}
        assert pointer.resolve(doc, "/a~1b/c~0d") == 1

    def test_missing_path_raises_without_a_default(self) -> None:
        with pytest.raises(KeyError):
            pointer.resolve({"a": 1}, "/b")

    def test_missing_path_returns_the_default(self) -> None:
        assert pointer.resolve({"a": 1}, "/b", default=None) is None

    def test_join_escapes(self) -> None:
        assert pointer.join("skills", "dodge", "proficiency") == "/skills/dodge/proficiency"

    def test_set_value(self) -> None:
        doc = {"a": {"b": 1}}
        pointer.set_value(doc, "/a/b", 2)
        assert doc["a"]["b"] == 2

    def test_set_appends_to_a_list(self) -> None:
        doc = {"gear": [{"name": "autogun"}]}
        pointer.set_value(doc, "/gear/-", {"name": "flak coat"})
        assert len(doc["gear"]) == 2

    def test_a_pointer_must_start_with_a_slash(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            pointer.parse("skills/dodge")


class TestDeepMerge:
    def test_nulls_never_overwrite(self) -> None:
        """A model omitting a field must not erase a printed constant."""
        target = {"armour": {"head": {"location": "Head", "armourPoints": None}}}
        pointer.deep_merge(target, {"armour": {"head": {"location": None, "armourPoints": 4}}})

        assert target["armour"]["head"]["location"] == "Head"
        assert target["armour"]["head"]["armourPoints"] == 4

    def test_dicts_merge_recursively(self) -> None:
        target = {"bio": {"characterName": None, "career": "Adept"}}
        pointer.deep_merge(target, {"bio": {"characterName": "Aldleg"}})

        assert target["bio"] == {"characterName": "Aldleg", "career": "Adept"}

    def test_lists_replace_wholesale(self) -> None:
        """A list on this sheet is a complete answer, not a patch."""
        target = {"gear": [{"name": "old"}]}
        pointer.deep_merge(target, {"gear": [{"name": "new"}]})

        assert target["gear"] == [{"name": "new"}]

    def test_reports_what_it_wrote(self) -> None:
        written = pointer.deep_merge({"bio": {"career": None}}, {"bio": {"career": "Adept"}})
        assert written == ["/bio/career"]


# --------------------------------------------------------------------------------------
# Partial fragments
# --------------------------------------------------------------------------------------


class TestPartialFragments:
    """Reasoning models return the shape the prompt asked for, not the full schema."""

    def test_a_proficiency_without_a_modifier_completes_itself(self) -> None:
        from scribe40k.models import SkillProficiency

        prof = SkillProficiency.model_validate({"level": "+20"})

        assert prof.modifier.flatBonus == 20
        assert prof.modifier.usable is True

    def test_an_explicit_modifier_is_left_alone(self) -> None:
        from scribe40k.models import SkillProficiency

        prof = SkillProficiency.model_validate(
            {
                "level": "Trained",
                "modifier": {"usable": True, "characteristicMultiplier": 1, "flatBonus": 0},
            }
        )
        assert prof.modifier.characteristicMultiplier == 1

    def test_specialisations_survive_a_wholesale_list_replacement(self) -> None:
        """The bug this guards: specialisation lists replace, so their items arrive bare."""
        document = blank_character().to_json_dict()
        pointer.deep_merge(
            document,
            {
                "skills": {
                    "commonLore": {
                        "specialisations": [
                            {"subject": "Tech", "proficiency": {"level": "Trained"}}
                        ]
                    }
                }
            },
        )

        finished, flags = validate_document(document)
        spec = finished["skills"]["commonLore"]["specialisations"][0]

        assert spec["proficiency"]["modifier"]["characteristicMultiplier"] == 1
        assert not [f for f in flags if f.rule.startswith("schema.")]


# --------------------------------------------------------------------------------------
# Section jobs
# --------------------------------------------------------------------------------------


class StubReasoning:
    """Returns a scripted envelope, and records the prompt it was given."""

    name = "stub"
    model = "stub"
    supports_structured_output = True

    def __init__(self, envelope, *, supports_vision: bool = True) -> None:
        self.envelope = envelope
        self.supports_vision = supports_vision
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if isinstance(self.envelope, Exception):
            raise self.envelope
        if isinstance(self.envelope, ReasoningResponse):
            return self.envelope
        return ReasoningResponse(
            text=json.dumps(self.envelope),
            parsed=self.envelope,
            provider=self.name,
            model=self.model,
        )


@pytest.fixture
def ocr_pages():
    return {n: OcrPage(pdf_page=n, sheet_page=n, text=f"page {n} text") for n in range(1, 6)}


@pytest.fixture
def images(tmp_path):
    out = {}
    for n in range(1, 6):
        path = tmp_path / f"p{n}.png"
        path.write_bytes(f"image {n}".encode())
        out[n] = PageImage(pdf_page=n, path=path, sheet_page=n)
    return out


class TestRunSection:
    def test_extracts_data_flags_and_unmapped(self, ocr_pages, images) -> None:
        provider = StubReasoning(
            {
                "data": {"bio": {"characterName": "Aldleg"}},
                "uncertain": [
                    {
                        "pointer": "/bio/career",
                        "reason": "faint",
                        "confidence": 0.3,
                        "alternatives": ["Adept"],
                        "snippet": "Career_ Ad...",
                    }
                ],
                "unmapped": [{"text": "+30 Deceive", "location": "left margin"}],
            }
        )

        result = run_section(SECTION_BY_NAME["bio_characteristics"], provider, ocr_pages, images)

        assert result.data == {"bio": {"characterName": "Aldleg"}}
        assert result.record.status == "ok"
        assert len(result.flags) == 1
        assert result.flags[0].pointer == "/bio/career"
        assert result.flags[0].alternatives == ["Adept"]
        assert result.flags[0].confidence == 0.3
        assert result.unmapped[0].text == "+30 Deceive"

    def test_keys_the_job_does_not_own_are_rejected(self, ocr_pages, images) -> None:
        """Otherwise one section silently overwrites another's work."""
        provider = StubReasoning(
            {"data": {"gear": [{"name": "autogun"}], "bio": {"characterName": "wrong"}}}
        )

        result = run_section(SECTION_BY_NAME["equipment"], provider, ocr_pages, images)

        assert "bio" not in result.data
        assert "gear" in result.data
        assert "ignored keys" in result.record.error

    def test_a_provider_exception_fails_only_this_section(self, ocr_pages, images) -> None:
        result = run_section(
            SECTION_BY_NAME["skills"], StubReasoning(RuntimeError("boom")), ocr_pages, images
        )

        assert result.record.status == "failed"
        assert "boom" in result.record.error
        assert result.data == {}

    def test_an_unparseable_reply_fails_cleanly(self, ocr_pages, images) -> None:
        provider = StubReasoning(ReasoningResponse(text="sorry, I cannot", parsed=None))

        result = run_section(SECTION_BY_NAME["skills"], provider, ocr_pages, images)

        assert result.record.status == "failed"
        assert "not a JSON object" in result.record.error

    def test_a_provider_error_is_recorded(self, ocr_pages, images) -> None:
        provider = StubReasoning(ReasoningResponse(text="", error="rate limited"))

        result = run_section(SECTION_BY_NAME["skills"], provider, ocr_pages, images)

        assert result.record.status == "failed"
        assert result.record.error == "rate limited"

    def test_a_section_with_no_input_is_skipped_not_failed(self, images) -> None:
        result = run_section(SECTION_BY_NAME["psychic"], StubReasoning({}), {}, {})

        assert result.record.status == "skipped"

    def test_images_go_only_to_vision_models(self, ocr_pages, images) -> None:
        seeing = StubReasoning({"data": {}}, supports_vision=True)
        blind = StubReasoning({"data": {}}, supports_vision=False)

        run_section(SECTION_BY_NAME["skills"], seeing, ocr_pages, images)
        run_section(SECTION_BY_NAME["skills"], blind, ocr_pages, images)

        assert len(seeing.requests[0].images) == 1
        assert len(blind.requests[0].images) == 0

    def test_a_text_only_model_is_told_not_to_guess_at_ticks(self, ocr_pages, images) -> None:
        blind = StubReasoning({"data": {}}, supports_vision=False)

        run_section(SECTION_BY_NAME["skills"], blind, ocr_pages, images)

        assert "mark a field uncertain rather than guessing" in blind.requests[0].user

    def test_malformed_uncertain_entries_are_ignored_not_fatal(self, ocr_pages, images) -> None:
        provider = StubReasoning(
            {
                "data": {},
                "uncertain": ["just a string", {"no_pointer": 1}, {"pointer": "not-a-pointer"}],
            }
        )

        result = run_section(SECTION_BY_NAME["skills"], provider, ocr_pages, images)

        assert result.record.status == "ok"
        assert result.flags == []


class TestMapSheet:
    def test_merges_every_section(self, ocr_pages, images) -> None:
        document = blank_character().to_json_dict()
        provider = StubReasoning({"data": {}, "uncertain": [], "unmapped": []})

        flags, unmapped, records = map_sheet(document, provider, ocr_pages, images)

        assert len(records) == len(ALL_SECTIONS)
        assert all(r.status == "ok" for r in records)

    def test_one_failing_section_does_not_lose_the_others(self, ocr_pages, images) -> None:
        class Selective(StubReasoning):
            def complete(self, request):
                if request.label == "skills":
                    raise RuntimeError("skills exploded")
                return ReasoningResponse(
                    text="{}",
                    parsed={"data": {"bio": {"characterName": "Aldleg"}}},
                    provider="stub",
                    model="stub",
                )

        document = blank_character().to_json_dict()
        _, _, records = map_sheet(document, Selective({}), ocr_pages, images)

        by_name = {r.name: r for r in records}
        assert by_name["skills"].status == "failed"
        assert by_name["bio_characteristics"].status == "ok"
        assert document["bio"]["characterName"] == "Aldleg"


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------


class TestValidateDocument:
    def test_a_blank_sheet_produces_no_schema_errors(self) -> None:
        _, flags = validate_document(blank_character().to_json_dict())
        assert not [f for f in flags if f.rule.startswith("schema.")]

    def test_derivations_are_applied(self) -> None:
        document = blank_character().to_json_dict()
        document["characteristics"]["agility"]["total"] = 38

        finished, _ = validate_document(document)

        assert finished["characteristics"]["agility"]["bonus"] == 3

    def test_consistency_findings_become_flags(self) -> None:
        document = blank_character().to_json_dict()
        document["wounds"]["totalWounds"] = 12
        document["wounds"]["currentWounds"] = 17

        _, flags = validate_document(document)

        assert any(f.rule == "wounds.current_exceeds_total" for f in flags)

    def test_a_missing_name_is_flagged(self) -> None:
        _, flags = validate_document(blank_character().to_json_dict())
        assert any(f.rule == "bio.missing_name" for f in flags)

    def test_an_unloadable_document_still_returns_something_to_correct(self) -> None:
        """Refusing to produce a document would leave the user nothing to fix."""
        document = blank_character().to_json_dict()
        document["characteristics"]["agility"]["total"] = "not a number"

        returned, flags = validate_document(document)

        assert returned is not None
        assert any(f.rule == "document.unparseable" for f in flags)


class TestDedupeFlags:
    def test_same_field_and_rule_collapses(self) -> None:
        flags = [
            Flag(pointer="/bio/career", severity="warning", rule="r", message="a"),
            Flag(pointer="/bio/career", severity="warning", rule="r", message="b"),
        ]
        assert len(dedupe_flags(flags)) == 1

    def test_the_richer_duplicate_wins(self) -> None:
        flags = [
            Flag(pointer="/bio/career", severity="warning", rule="r", message="a"),
            Flag(
                pointer="/bio/career",
                severity="warning",
                rule="r",
                message="b",
                alternatives=["Adept"],
            ),
        ]
        assert dedupe_flags(flags)[0].alternatives == ["Adept"]

    def test_different_rules_on_one_field_both_survive(self) -> None:
        flags = [
            Flag(pointer="/bio/career", severity="warning", rule="one", message="a"),
            Flag(pointer="/bio/career", severity="error", rule="two", message="b"),
        ]
        assert len(dedupe_flags(flags)) == 2

    def test_errors_sort_before_warnings(self) -> None:
        flags = [
            Flag(pointer="/z", severity="warning", rule="w", message="a"),
            Flag(pointer="/a", severity="error", rule="e", message="b"),
        ]
        assert [f.severity for f in dedupe_flags(flags)] == ["error", "warning"]


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------


class TestReport:
    def _report(self, **kwargs) -> ExtractionReport:
        from scribe40k.pipeline.report import ModelRef, SourceRef

        return ExtractionReport(
            source=SourceRef(filename="x.pdf", fileHash="abc", pageCount=5),
            models={
                "ocr": ModelRef(provider="p", model="m"),
                "reasoning": ModelRef(provider="p", model="m"),
            },
            **kwargs,
        )

    def test_review_count_only_counts_open_flags(self) -> None:
        report = self._report(
            flags=[
                Flag(pointer="/a", severity="warning", rule="r", message="m"),
                Flag(pointer="/b", severity="error", rule="r", message="m", status="user_fixed"),
            ]
        )
        assert report.review_count == 1

    def test_severity_counts(self) -> None:
        report = self._report(
            flags=[
                Flag(pointer="/a", severity="error", rule="r", message="m"),
                Flag(pointer="/b", severity="warning", rule="r", message="m"),
                Flag(pointer="/c", severity="warning", rule="r", message="m"),
            ]
        )
        assert report.severity_counts() == {"error": 1, "warning": 2, "info": 0}

    def test_unmapped_ids_are_stable_across_runs(self) -> None:
        """So dismissing a fragment survives a re-extraction."""

        def make():
            item = UnmappedItem(text="+30 Deceive", source=UnmappedSource(pdfPage=1))
            return item.ensure_id()

        assert make() == make()


# --------------------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------------------


class TestStore:
    def test_round_trips_a_character(self, tmp_path) -> None:
        store = CharacterStore(tmp_path)
        document = blank_character().to_json_dict()
        document["bio"]["characterName"] = "Aldleg"

        store.save("aldleg", document)

        assert store.load("aldleg")["bio"]["characterName"] == "Aldleg"

    def test_ids_do_not_collide(self, tmp_path) -> None:
        store = CharacterStore(tmp_path)
        first = store.create_blank("Aldleg")
        second = store.create_blank("Aldleg")

        assert first == "aldleg"
        assert second == "aldleg-2"

    def test_listing_reports_review_counts(self, tmp_path) -> None:
        store = CharacterStore(tmp_path)
        store.create_blank("Aldleg")

        [summary] = store.list_characters()

        assert summary.name == "Aldleg"
        assert summary.hasReport is False

    def test_a_missing_character_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            CharacterStore(tmp_path).load("nope")

    def test_saving_is_atomic(self, tmp_path) -> None:
        """No .tmp file should survive a completed save."""
        store = CharacterStore(tmp_path)
        store.save("x", blank_character().to_json_dict())

        assert not list(store.directory("x").glob("*.tmp"))


# --------------------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def outcome(tmp_path_factory):
    """Run the whole pipeline over the real scan, with recorded model replies."""
    if not (SAMPLE_SCAN.exists() and PAGE_FINGERPRINTS.exists()):
        pytest.skip("calibration scan or page fingerprints unavailable")

    from scribe40k.llm.config import StageConfig
    from scribe40k.llm.registry import build_ocr_provider, build_reasoning_provider
    from scribe40k.pipeline.run import extract

    ocr = build_ocr_provider(
        StageConfig(provider="fixture", options={"path": str(FIXTURES / "ocr")}), cache=False
    )
    reasoning = build_reasoning_provider(
        StageConfig(provider="fixture", options={"path": str(FIXTURES / "reasoning")}), cache=False
    )
    return extract(SAMPLE_SCAN, ocr, reasoning, image_dir=tmp_path_factory.mktemp("pages") / "img")


class TestEndToEnd:
    def test_the_character_validates_against_the_schema(self, outcome) -> None:
        schema = json.loads(CHARACTER_SCHEMA.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(outcome.character))

        assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:10])

    def test_the_report_validates_against_its_schema(self, outcome) -> None:
        schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(outcome.report.to_json_dict()))

        assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:10])

    def test_every_section_completed(self, outcome) -> None:
        assert all(s.status == "ok" for s in outcome.report.sections)

    def test_values_reached_the_document(self, outcome) -> None:
        assert outcome.character["bio"]["characterName"] == "Aldleg"
        assert outcome.character["skills"]["dodge"]["proficiency"]["level"] == "Trained"

    def test_derivations_ran_over_extracted_values(self, outcome) -> None:
        assert outcome.character["characteristics"]["agility"]["bonus"] == 2
        assert outcome.character["gear"][0] == {
            "name": "frag grenade",
            "quantity": 3,
            "notes": None,
        }

    def test_broken_values_are_flagged_rather_than_corrected(self, outcome) -> None:
        rules = {f.rule for f in outcome.report.flags}

        assert "wounds.current_exceeds_total" in rules
        assert "advances.overspent" in rules
        assert "movement.mismatch" in rules
        assert "skill.basic_on_advanced_skill" in rules
        # ...and the sheet still holds what was written on it.
        assert outcome.character["wounds"]["currentWounds"] == 17

    def test_model_uncertainty_becomes_a_flag_with_alternatives(self, outcome) -> None:
        [flag] = [f for f in outcome.report.flags if f.rule == "model.low_confidence"]

        assert flag.pointer == "/bio/career"
        assert flag.alternatives == ["Imperial Psycher"]
        assert flag.evidence.snippet.startswith("Career_")

    def test_a_flag_can_locate_its_own_crop(self, outcome) -> None:
        """Without pdfPage the editor cannot show the handwriting beside the field, which
        is the difference between a question the user can answer and one they cannot."""
        [flag] = [f for f in outcome.report.flags if f.rule == "model.low_confidence"]

        assert flag.evidence.sheetPage == 1
        assert flag.evidence.pdfPage == 1

    def test_unmapped_fragments_point_at_the_page_they_came_from(self, outcome) -> None:
        implants = next(u for u in outcome.report.unmapped if "Elec graft" in u.text)

        # Sheet page 4 is PDF page 5 in this out-of-order scan.
        assert implants.source.sheetPage == 4
        assert implants.source.pdfPage == 5

    def test_stray_annotations_reach_the_assignment_tray(self, outcome) -> None:
        texts = [u.text for u in outcome.report.unmapped]
        assert "+30 Deceive" in texts

    def test_the_notes_pages_become_note_pages(self, outcome) -> None:
        """The scan ends with two pages of handwritten notes that are not part of the
        form. They used to reach the tray as '(PDF page 10)' -- the fact that something
        was there, without the something."""
        notes = outcome.character["notePages"]

        assert [n["sourcePdfPage"] for n in notes] == [10, 12]
        assert "Rescued the astropath" in notes[0]["text"]

    def test_a_note_page_says_where_it_came_from(self, outcome) -> None:
        flags = {f.pointer: f for f in outcome.report.flags if f.rule == "ingest.note_page"}

        assert set(flags) == {"/notePages/0/text", "/notePages/1/text"}
        first = flags["/notePages/0/text"]
        assert first.severity == "info", "a note page is not a problem to be fixed"
        assert first.evidence.pdfPage == 10

    def test_a_note_page_that_could_not_be_read_is_a_warning(self, outcome) -> None:
        """Page 12 has no transcription here, which is exactly the case the user needs
        pointing at: an attached page whose content did not survive."""
        empty = next(
            f
            for f in outcome.report.flags
            if f.rule == "ingest.note_page" and f.pointer == "/notePages/1/text"
        )

        assert empty.severity == "warning"
        assert outcome.character["notePages"][1]["text"] is None

    def test_page_classification_is_recorded_for_every_page(self, outcome) -> None:
        assert len(outcome.report.pages) == 12
        sheet = {p.sheetPage: p.pdfPage for p in outcome.report.pages if p.kind == "sheet"}
        assert sheet == {1: 1, 2: 3, 3: 9, 4: 5, 5: 7}

    def test_the_reasoning_model_is_recorded_with_its_vision_support(self, outcome) -> None:
        assert outcome.report.models["reasoning"].supportsVision is True
