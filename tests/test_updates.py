"""Re-reading a character from a printout somebody has written on.

The rule the whole feature turns on: an update never writes to the sheet. A model reading
handwriting is right most of the time, and the times it is wrong are plausible -- "Space"
for "Spare parts" -- so saving a re-read document over a character destroys work with no
trace and no way to tell afterwards. Everything it finds becomes a suggestion instead.
"""

from __future__ import annotations

import copy
import importlib
import json

import pytest

from scribe40k.blank import blank_character
from scribe40k.llm.base import OcrPage, ReasoningResponse
from scribe40k.pipeline.diff import covered_keys, suggest_updates
from scribe40k.pipeline.report import Evidence
from scribe40k.pipeline.sections import ALL_SECTIONS


def _character() -> dict:
    document = blank_character().to_json_dict()
    document["bio"]["characterName"] = "Barck"
    document["skills"]["dodge"]["proficiency"] = {
        "level": "Trained",
        "modifier": {"usable": True, "characteristicMultiplier": 1, "flatBonus": 0},
    }
    document["gear"] = [
        {"name": "Autogun", "quantity": 1, "notes": None},
        {"name": "Spare parts", "quantity": None, "notes": None},
        {"name": "Lho sticks", "quantity": 3, "notes": None},
    ]
    document["talentsAndTraits"]["advancesTalentsAndTraits"] = [
        {"name": "Electro-graft", "specialisation": None, "notes": None},
        {"name": "Sound Constitution", "specialisation": None, "notes": None},
    ]
    return document


EVERYTHING = {section.owns[0] for section in ALL_SECTIONS} | {
    "bio",
    "characteristics",
    "skills",
    "gear",
    "talentsAndTraits",
    "weapons",
    "advances",
    "psychic",
}


def _suggest(current, candidate, **kwargs):
    return suggest_updates(current, candidate, covers=EVERYTHING, **kwargs)


def _by_rule(flags):
    return {flag.rule for flag in flags}


class TestNothingChanged:
    def test_a_document_compared_with_itself_suggests_nothing(self) -> None:
        """The property the round-trip test below depends on."""
        current = _character()

        assert _suggest(current, copy.deepcopy(current)) == []

    def test_derived_values_are_never_suggested(self) -> None:
        """A characteristic bonus is arithmetic. Suggesting a change to it is suggesting
        that the arithmetic be different."""
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["characteristics"]["agility"]["bonus"] = 9
        candidate["skills"]["dodge"]["proficiency"]["modifier"]["flatBonus"] = 20

        assert _suggest(current, candidate) == []

    def test_printed_information_is_never_suggested(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["skills"]["dodge"]["characteristic"] = "WP"
        candidate["skills"]["dodge"]["isBasicSkill"] = True
        candidate["armour"]["head"]["hitRoll"] = "99"

        assert _suggest(current, candidate) == []


class TestAChangedValue:
    def test_an_advanced_skill_is_suggested(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["skills"]["dodge"]["proficiency"]["level"] = "+10"

        [flag] = _suggest(current, candidate)

        assert flag.rule == "update.changed"
        assert flag.pointer == "/skills/dodge/proficiency/level"
        assert flag.expected == "+10"
        assert flag.actual == "Trained"

    def test_the_message_says_which_is_which(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["bio"]["characterName"] = "Barck Varn"

        [flag] = _suggest(current, candidate)

        assert "printout reads" in flag.message
        assert "Barck Varn" in flag.message
        assert "sheet holds" in flag.message


class TestARowAppearing:
    def test_a_new_gear_line_is_suggested_where_it_would_land(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["gear"].append({"name": "Frag grenade", "quantity": 2, "notes": None})

        [flag] = _suggest(current, candidate)

        assert flag.rule == "update.added"
        assert flag.pointer == "/gear/3", "after everything the sheet already has"
        assert flag.expected["name"] == "Frag grenade"

    def test_two_new_rows_do_not_land_on_top_of_each_other(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["gear"] += [
            {"name": "Frag grenade", "quantity": 2, "notes": None},
            {"name": "Rebreather", "quantity": 1, "notes": None},
        ]

        pointers = [flag.pointer for flag in _suggest(current, candidate)]

        assert pointers == ["/gear/3", "/gear/4"]

    def test_a_row_inserted_at_the_top_does_not_report_the_whole_list_as_changed(self) -> None:
        """Matching by position alone would report every row below it as edited."""
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["gear"].insert(0, {"name": "Frag grenade", "quantity": 2, "notes": None})

        flags = _suggest(current, candidate)

        assert _by_rule(flags) == {"update.added"}
        assert len(flags) == 1


class TestARowDisappearing:
    def test_a_missing_row_is_suggested_for_removal(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        del candidate["gear"][2]

        [flag] = _suggest(current, candidate)

        assert flag.rule == "update.removed"
        assert flag.pointer == "/gear/2"
        assert "Lho sticks" in flag.message
        assert "Crossed out, or missed by the reading?" in flag.message

    def test_removals_can_be_turned_off_entirely(self) -> None:
        """For a section whose page read badly, where absent means unread."""
        current = _character()
        candidate = copy.deepcopy(current)
        del candidate["gear"][2]

        assert _suggest(current, candidate, allow_removals=False) == []

    def test_a_list_that_vanishes_raises_one_doubt_not_many_removals(self) -> None:
        """Three gear lines gone at once is a page that read badly far more often than a
        player who crossed out everything they own."""
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["gear"] = []

        [flag] = _suggest(current, candidate)

        assert flag.rule == "update.list_vanished"
        assert flag.actual == 3

    def test_a_field_the_printout_left_blank_is_a_removal_not_an_overwrite(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["bio"]["characterName"] = None

        [flag] = _suggest(current, candidate)

        assert flag.rule == "update.removed"
        assert flag.actual == "Barck"


class TestMatchingRows:
    def test_an_edited_row_is_the_same_row(self) -> None:
        """The point of an update is that a row changed. A rename that reads as a delete
        and an add loses the note somebody wrote beside it."""
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["talentsAndTraits"]["advancesTalentsAndTraits"][0] = {
            "name": "Electro graft",
            "specialisation": None,
            "notes": "+10 Tech-Use",
        }

        flags = _suggest(current, candidate)

        assert _by_rule(flags) == {"update.changed"}
        assert {flag.pointer for flag in flags} == {
            "/talentsAndTraits/advancesTalentsAndTraits/0/name",
            "/talentsAndTraits/advancesTalentsAndTraits/0/notes",
        }

    def test_a_wholly_different_row_is_a_swap_not_an_edit(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["gear"][2] = {"name": "Chainsword", "quantity": 1, "notes": None}

        assert _by_rule(_suggest(current, candidate)) == {"update.added", "update.removed"}

    def test_reordered_rows_are_not_changes(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["gear"].reverse()

        assert _suggest(current, candidate) == []


class TestScope:
    def test_a_section_that_did_not_run_is_left_alone(self) -> None:
        """Upload page 2 only, and page 1 keeps every value it had. Absent from the
        upload is not the same as emptied."""
        current = _character()
        candidate = blank_character().to_json_dict()  # nothing was read at all
        candidate["gear"] = [{"name": "Frag grenade", "quantity": 1, "notes": None}]

        flags = suggest_updates(current, candidate, covers={"gear"})

        assert {flag.pointer.split("/")[1] for flag in flags} == {"gear"}

    def test_covered_keys_follows_the_sections_that_succeeded(self) -> None:
        from scribe40k.pipeline.report import SectionRecord

        records = [
            SectionRecord(name="equipment", status="ok"),
            SectionRecord(name="skills", status="failed"),
        ]

        assert covered_keys(records, ALL_SECTIONS) == {
            "weapons",
            "talentsAndTraits",
            "gear",
            "weaponTraining",
        }


class TestEvidence:
    def test_a_suggestion_says_which_document_it_came_from(self) -> None:
        """Otherwise the popover crops the original scan, which is not the paper anybody
        wrote on."""
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["bio"]["characterName"] = "Barck Varn"

        [flag] = _suggest(current, candidate, evidence=Evidence(source="abc123"))

        assert flag.evidence.source == "abc123"


# --------------------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------------------


class StubOcr:
    name = "stub-ocr"
    model = "stub-ocr-1"

    def transcribe(self, pages):
        return [
            OcrPage(
                pdf_page=p.pdf_page,
                sheet_page=p.sheet_page,
                text=f"transcription of PDF page {p.pdf_page}",
                provider=self.name,
                model=self.model,
            )
            for p in pages
        ]


class StubReasoning:
    """Answers every section with one fixed slice of a character."""

    name = model = "stub-reasoning"
    supports_vision = supports_structured_output = True

    def __init__(self, answers: dict[str, dict]) -> None:
        self.answers = answers
        self.labels: list[str] = []

    def complete(self, request):
        self.labels.append(request.label)
        envelope = {"data": self.answers.get(request.label, {})}
        return ReasoningResponse(text=json.dumps(envelope), parsed=envelope)


@pytest.fixture
def answers():
    """What the printout says, section by section. Mutated per test."""
    return {}


@pytest.fixture
def stubbed(monkeypatch, tmp_path, answers):
    monkeypatch.setenv("SCRIBE40K_DATA", str(tmp_path))
    import scribe40k.api as api

    importlib.reload(api)

    reasoning = StubReasoning(answers)
    monkeypatch.setattr(api, "build_ocr_provider", lambda *a, **k: StubOcr())
    monkeypatch.setattr(api, "build_reasoning_provider", lambda *a, **k: reasoning)

    from fastapi.testclient import TestClient

    api.store.save("barck", _character())
    return TestClient(api.app), api, reasoning


@pytest.fixture
def one_page_pdf(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=604, height=782)
    for row in range(18):
        for col in range(12):
            if (row * 5 + col * 3) % 3:
                page.draw_rect(
                    pymupdf.Rect(24 + col * 46, 24 + row * 40, 60 + col * 46, 56 + row * 40),
                    fill=(0, 0, 0),
                )
    path = tmp_path / "printout.pdf"
    doc.save(path)
    doc.close()
    return path


def _start(client, pdf):
    with pdf.open("rb") as handle:
        return client.post(
            "/api/characters/barck/updates",
            files={"file": ("printout.pdf", handle, "application/pdf")},
        )


class TestTheUpdateFlow:
    def test_it_stops_at_the_page_assignment_like_an_import(self, stubbed, one_page_pdf) -> None:
        client, _api, reasoning = stubbed

        response = _start(client, one_page_pdf)

        assert response.status_code == 201
        assert response.json()["kind"] == "update"
        assert response.json()["characterId"] == "barck"
        assert reasoning.labels == []

    def test_the_proposal_survives_a_reload(self, stubbed, one_page_pdf) -> None:
        client, _api, _ = stubbed
        update_id = _start(client, one_page_pdf).json()["updateId"]

        again = client.get(f"/api/characters/barck/updates/{update_id}")

        assert again.status_code == 200
        assert again.json()["updateId"] == update_id

    def test_an_unconfirmed_update_is_listed(self, stubbed, one_page_pdf) -> None:
        client, _api, _ = stubbed
        update_id = _start(client, one_page_pdf).json()["updateId"]

        [entry] = client.get("/api/characters/barck/updates").json()

        assert entry["id"] == update_id
        assert entry["sourceName"] == "printout.pdf"

    def test_it_can_be_abandoned(self, stubbed, one_page_pdf) -> None:
        client, _api, _ = stubbed
        update_id = _start(client, one_page_pdf).json()["updateId"]

        assert client.delete(f"/api/characters/barck/updates/{update_id}").status_code == 204
        assert client.get("/api/characters/barck/updates").json() == []

    def test_an_update_to_a_character_that_does_not_exist_is_refused(self, stubbed) -> None:
        client, _api, _ = stubbed

        assert client.get("/api/characters/nobody/updates").status_code == 404

    def test_a_crafted_id_cannot_reach_outside_the_store(self, stubbed) -> None:
        client, _api, _ = stubbed

        response = client.get("/api/characters/barck/updates/..%2F..%2Fetc")

        assert response.status_code in (400, 404)


class TestConfirmingAnUpdate:
    def _confirm(self, client, one_page_pdf, assignment=None):
        update_id = _start(client, one_page_pdf).json()["updateId"]
        return client.post(
            f"/api/characters/barck/updates/{update_id}/confirm",
            json={"assignment": assignment or {"1": 2}},
        )

    def test_the_character_is_not_written_to(self, stubbed, one_page_pdf, answers) -> None:
        """The whole point of the feature."""
        client, api, _ = stubbed
        answers["equipment"] = {"gear": [{"name": "Frag grenade", "quantity": 2, "notes": None}]}
        before = api.store.load("barck")

        self._confirm(client, one_page_pdf)

        assert api.store.load("barck") == before

    #: What the printout says about page 2, unchanged apart from one new gear line. The
    #: talents have to be repeated: a section that runs and answers with nothing has read
    #: an empty page, and an empty page is exactly what a removal looks like.
    UNCHANGED_PAGE_TWO = {
        "gear": [
            {"name": "Autogun", "quantity": 1, "notes": None},
            {"name": "Spare parts", "quantity": None, "notes": None},
            {"name": "Lho sticks", "quantity": 3, "notes": None},
        ],
        "talentsAndTraits": {
            "homeworldBackground": [],
            "advancesTalentsAndTraits": [
                {"name": "Electro-graft", "specialisation": None, "notes": None},
                {"name": "Sound Constitution", "specialisation": None, "notes": None},
            ],
        },
    }

    def test_what_differs_becomes_suggestions(self, stubbed, one_page_pdf, answers) -> None:
        client, api, _ = stubbed
        printout = copy.deepcopy(self.UNCHANGED_PAGE_TWO)
        printout["gear"].append({"name": "Frag grenade", "quantity": 2, "notes": None})
        answers["equipment"] = printout

        payload = self._confirm(client, one_page_pdf).json()

        assert payload["suggested"] == 1
        report = api.store.load_report("barck")
        [flag] = [f for f in report.flags if f.rule.startswith("update.")]
        assert flag.rule == "update.added"
        assert flag.expected["name"] == "Frag grenade"

    def test_a_printout_that_matches_the_sheet_suggests_nothing(
        self, stubbed, one_page_pdf, answers
    ) -> None:
        """Print a character, scan it back unmarked, and nothing should have changed."""
        client, api, _ = stubbed
        answers["equipment"] = copy.deepcopy(self.UNCHANGED_PAGE_TWO)

        payload = self._confirm(client, one_page_pdf).json()

        assert payload["suggested"] == 0
        report = api.store.load_report("barck")
        assert not [f for f in report.flags if f.rule.startswith("update.")]

    def test_a_crossed_out_row_is_offered_for_removal(self, stubbed, one_page_pdf, answers) -> None:
        client, api, _ = stubbed
        printout = copy.deepcopy(self.UNCHANGED_PAGE_TWO)
        printout["gear"] = [row for row in printout["gear"] if row["name"] != "Lho sticks"]
        answers["equipment"] = printout

        self._confirm(client, one_page_pdf)

        report = api.store.load_report("barck")
        [flag] = [f for f in report.flags if f.rule.startswith("update.")]
        assert flag.rule == "update.removed"
        assert "Lho sticks" in flag.message

    def test_a_suggestion_points_at_the_printout_it_came_from(
        self, stubbed, one_page_pdf, answers
    ) -> None:
        client, api, _ = stubbed
        answers["equipment"] = {"gear": [{"name": "Frag grenade", "quantity": 2, "notes": None}]}

        update_id = self._confirm(client, one_page_pdf).json()["updateId"]

        report = api.store.load_report("barck")
        suggestion = next(f for f in report.flags if f.rule.startswith("update."))
        assert suggestion.evidence.source == update_id
        assert suggestion.evidence.pdfPage == 1

    def test_the_reading_is_recorded(self, stubbed, one_page_pdf, answers) -> None:
        client, api, _ = stubbed
        answers["equipment"] = {"gear": [{"name": "Frag grenade", "quantity": 2, "notes": None}]}

        self._confirm(client, one_page_pdf)

        [record] = api.store.load_report("barck").updates
        assert record.sourceName == "printout.pdf"
        assert record.suggested >= 1
        assert [page.sheetPage for page in record.pages] == [2]

    def test_only_the_pages_uploaded_are_compared(self, stubbed, one_page_pdf, answers) -> None:
        """Page 2 alone must not suggest emptying page 1's fields."""
        client, api, _ = stubbed
        answers["equipment"] = {"gear": []}

        payload = self._confirm(client, one_page_pdf).json()

        assert payload["sectionsRead"] == ["gear", "talentsAndTraits", "weaponTraining", "weapons"]
        report = api.store.load_report("barck")
        assert not any(f.pointer.startswith("/bio") for f in report.flags if "update." in f.rule)

    def test_a_second_reading_of_the_same_printout_does_not_double_up(
        self, stubbed, one_page_pdf, answers
    ) -> None:
        client, api, _ = stubbed
        answers["equipment"] = {"gear": [{"name": "Frag grenade", "quantity": 2, "notes": None}]}

        self._confirm(client, one_page_pdf)
        self._confirm(client, one_page_pdf)

        report = api.store.load_report("barck")
        suggestions = [f for f in report.flags if f.rule.startswith("update.")]
        assert len(suggestions) == len({(f.pointer, f.rule) for f in suggestions})

    def test_confirming_twice_is_refused(self, stubbed, one_page_pdf) -> None:
        client, _api, _ = stubbed
        update_id = _start(client, one_page_pdf).json()["updateId"]
        client.post(
            f"/api/characters/barck/updates/{update_id}/confirm", json={"assignment": {"1": 2}}
        )

        second = client.post(
            f"/api/characters/barck/updates/{update_id}/confirm", json={"assignment": {"1": 2}}
        )

        assert second.status_code == 404

    def test_the_suggestions_are_ordinary_flags(self, stubbed, one_page_pdf, answers) -> None:
        """So the counter, next/previous and mark-all-reviewed all work on them without
        knowing what an update is."""
        client, api, _ = stubbed
        answers["equipment"] = {"gear": [{"name": "Frag grenade", "quantity": 2, "notes": None}]}
        self._confirm(client, one_page_pdf)

        before = client.get("/api/characters/barck").json()["reviewCount"]
        client.post("/api/characters/barck/flags/clear")
        after = client.get("/api/characters/barck").json()["reviewCount"]

        assert before > 0
        assert after == 0


class TestTheCrops:
    def test_a_printouts_pages_are_served_from_its_own_url(self, stubbed, one_page_pdf) -> None:
        client, _api, _ = stubbed
        update_id = _start(client, one_page_pdf).json()["updateId"]

        response = client.get(f"/api/characters/barck/updates/{update_id}/pages/1")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_they_are_not_the_original_scans_pages(self, stubbed, one_page_pdf) -> None:
        """A suggestion is about handwriting on the printout. Cropping the original scan
        would show a page nobody wrote on."""
        client, _api, _ = stubbed
        _start(client, one_page_pdf)

        assert client.get("/api/characters/barck/pages/1").status_code == 404

    def test_a_crafted_character_id_is_refused(self, stubbed) -> None:
        client, _api, _ = stubbed

        assert client.get("/api/characters/..%2F..%2Fetc/pages/1").status_code in (400, 404)


class TestSuggestionsSurvive:
    """They record what a printout said, which no amount of re-validating can recompute.

    The rule flags are regenerated from the document on every save, and a suggestion that
    was mistaken for one vanished the moment the character was next opened -- silently,
    since a flag disappearing looks exactly like a flag resolved.
    """

    def _one_suggestion(self, client, one_page_pdf, answers):
        printout = copy.deepcopy(TestConfirmingAnUpdate.UNCHANGED_PAGE_TWO)
        printout["gear"].append({"name": "Frag grenade", "quantity": 2, "notes": None})
        answers["equipment"] = printout
        update_id = _start(client, one_page_pdf).json()["updateId"]
        client.post(
            f"/api/characters/barck/updates/{update_id}/confirm", json={"assignment": {"1": 2}}
        )

    def _suggestions(self, api):
        return [f for f in api.store.load_report("barck").flags if f.rule.startswith("update.")]

    def test_opening_the_character_does_not_lose_them(self, stubbed, one_page_pdf, answers) -> None:
        client, api, _ = stubbed
        self._one_suggestion(client, one_page_pdf, answers)
        before = len(self._suggestions(api))

        client.get("/api/characters/barck")

        assert before > 0
        assert len(self._suggestions(api)) == before

    def test_editing_the_character_does_not_lose_them(self, stubbed, one_page_pdf, answers) -> None:
        client, api, _ = stubbed
        self._one_suggestion(client, one_page_pdf, answers)
        before = len(self._suggestions(api))

        client.patch(
            "/api/characters/barck",
            json={"operations": [{"pointer": "/bio/quirk", "value": "Nervous"}]},
        )

        assert len(self._suggestions(api)) == before

    def test_accepting_one_still_resolves_it(self, stubbed, one_page_pdf, answers) -> None:
        """Surviving revalidation must not mean surviving the user's answer."""
        client, api, _ = stubbed
        self._one_suggestion(client, one_page_pdf, answers)
        [suggestion] = self._suggestions(api)

        client.post(
            "/api/characters/barck/flags",
            json={"pointer": suggestion.pointer, "rule": suggestion.rule, "status": "user_fixed"},
        )
        client.get("/api/characters/barck")

        assert [f.status for f in self._suggestions(api)] == ["user_fixed"]


class TestNamesThatGrewOrShrank:
    """The commonest edit to a row is to its name, and similarity alone misses it.

    "Scibilia" against "Scibilia 7D (oldest 5D)" scores 0.55 as strings, because most of
    the longer one is absent from the shorter -- so the row read as deleted and a
    different one added, losing the quantity and notes beside it.
    """

    def test_a_name_written_out_in_full_is_the_same_row(self) -> None:
        current = _character()
        candidate = copy.deepcopy(current)
        candidate["gear"][0]["name"] = "Autogun, best quality, with sling"

        flags = _suggest(current, candidate)

        assert [flag.rule for flag in flags] == ["update.changed"]
        assert flags[0].pointer == "/gear/0/name"

    def test_a_name_cut_short_is_the_same_row(self) -> None:
        current = _character()
        current["gear"][0]["name"] = "Autogun, best quality, with sling"
        candidate = copy.deepcopy(current)
        candidate["gear"][0]["name"] = "Autogun"

        assert [flag.rule for flag in _suggest(current, candidate)] == ["update.changed"]

    def test_a_short_prefix_is_not_enough(self) -> None:
        """Otherwise "Axe" would claim "Axe of the Emperor's Wrath" and anything else
        beginning with those letters."""
        current = _character()
        current["gear"][0]["name"] = "Axe"
        candidate = copy.deepcopy(current)
        candidate["gear"][0]["name"] = "Axiomatic cogitator"

        assert {flag.rule for flag in _suggest(current, candidate)} == {
            "update.added",
            "update.removed",
        }
