"""The HTTP API.

The behaviour worth protecting here is that the server, not the client, owns derivation
and validation. The editor can send whatever the user typed; what comes back is always a
consistent document with an up-to-date review state.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from scribe40k.pipeline.report import (
    ExtractionReport,
    Flag,
    ModelRef,
    SourceRef,
    UnmappedItem,
    UnmappedSource,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE40K_DATA", str(tmp_path))
    import scribe40k.api as api

    importlib.reload(api)
    return TestClient(api.app)


@pytest.fixture
def character(client):
    return client.post("/api/characters", json={"name": "Aldleg"}).json()["id"]


def _report_with(flags=(), unmapped=()) -> ExtractionReport:
    return ExtractionReport(
        source=SourceRef(filename="scan.pdf", fileHash="abc", pageCount=12),
        models={
            "ocr": ModelRef(provider="fixture", model="fixture"),
            "reasoning": ModelRef(provider="fixture", model="fixture", supportsVision=True),
        },
        flags=list(flags),
        unmapped=list(unmapped),
    )


class TestCharacterLifecycle:
    def test_create_read_delete(self, client) -> None:
        created = client.post("/api/characters", json={"name": "Aldleg"})
        assert created.status_code == 201
        character_id = created.json()["id"]

        assert client.get(f"/api/characters/{character_id}").status_code == 200
        assert client.delete(f"/api/characters/{character_id}").status_code == 204
        assert client.get(f"/api/characters/{character_id}").status_code == 404

    def test_a_new_character_validates(self, client) -> None:
        document = client.post("/api/characters", json={}).json()["character"]
        assert document["skills"]["dodge"]["isBasicSkill"] is True
        assert document["armour"]["head"]["hitRoll"] == "1-10"

    def test_listing(self, client, character) -> None:
        [summary] = client.get("/api/characters").json()
        assert summary["name"] == "Aldleg"

    def test_unknown_character_is_404(self, client) -> None:
        assert client.get("/api/characters/nope").status_code == 404


class TestPatching:
    def test_derived_values_are_recomputed_server_side(self, client, character) -> None:
        """The client never has to know that bonus is floor(total / 10)."""
        response = client.patch(
            f"/api/characters/{character}",
            json={"operations": [{"pointer": "/characteristics/agility/total", "value": 45}]},
        )

        assert response.json()["character"]["characteristics"]["agility"]["bonus"] == 4

    def test_a_proficiency_level_pulls_its_modifier_with_it(self, client, character) -> None:
        response = client.patch(
            f"/api/characters/{character}",
            json={"operations": [{"pointer": "/skills/dodge/proficiency/level", "value": "+20"}]},
        )

        modifier = response.json()["character"]["skills"]["dodge"]["proficiency"]["modifier"]
        assert modifier["flatBonus"] == 20

    def test_several_edits_in_one_call(self, client, character) -> None:
        response = client.patch(
            f"/api/characters/{character}",
            json={
                "operations": [
                    {"pointer": "/bio/career", "value": "Assassin"},
                    {"pointer": "/wounds/totalWounds", "value": 12},
                ]
            },
        )

        document = response.json()["character"]
        assert document["bio"]["career"] == "Assassin"
        assert document["wounds"]["totalWounds"] == 12

    def test_edits_persist(self, client, character) -> None:
        client.patch(
            f"/api/characters/{character}",
            json={"operations": [{"pointer": "/bio/quirk", "value": "Twitchy"}]},
        )

        assert client.get(f"/api/characters/{character}").json()["character"]["bio"]["quirk"] == (
            "Twitchy"
        )

    def test_a_pointer_that_does_not_exist_is_rejected(self, client, character) -> None:
        response = client.patch(
            f"/api/characters/{character}",
            json={"operations": [{"pointer": "/invented/field", "value": 1}]},
        )

        assert response.status_code == 400
        assert "cannot write" in response.json()["detail"]

    def test_appending_to_gear(self, client, character) -> None:
        response = client.patch(
            f"/api/characters/{character}",
            json={"operations": [{"pointer": "/gear/-", "value": {"name": "2 x stimm"}}]},
        )

        gear = response.json()["character"]["gear"]
        assert gear == [{"name": "stimm", "quantity": 2, "notes": None}]


class TestFlags:
    def test_resolving_a_flag_lowers_the_review_count(self, client, character, tmp_path) -> None:
        import scribe40k.api as api

        api.store.save_report(
            character,
            _report_with(
                flags=[
                    Flag(
                        pointer="/bio/career",
                        severity="warning",
                        rule="model.low_confidence",
                        message="unsure",
                    ),
                ]
            ),
        )

        # A blank character raises rule flags of its own (no name, no wounds), so the
        # count is measured as a delta rather than assumed to start at one.
        before = client.get(f"/api/characters/{character}").json()["reviewCount"]

        response = client.post(
            f"/api/characters/{character}/flags",
            json={"pointer": "/bio/career", "rule": "model.low_confidence", "status": "accepted"},
        )

        assert response.json()["reviewCount"] == before - 1

    def test_a_resolved_flag_does_not_come_back_after_an_edit(self, client, character) -> None:
        """Re-running the rules must not resurrect what the user already dismissed."""
        import scribe40k.api as api

        client.patch(
            f"/api/characters/{character}",
            json={
                "operations": [
                    {"pointer": "/wounds/totalWounds", "value": 12},
                    {"pointer": "/wounds/currentWounds", "value": 17},
                ]
            },
        )
        api.store.save_report(character, api.store.load_report(character) or _report_with())

        # The inconsistency is flagged...
        report = client.get(f"/api/characters/{character}/report").json()
        assert any(f["rule"] == "wounds.current_exceeds_total" for f in report["flags"])

        # ...the user says it is deliberate...
        client.post(
            f"/api/characters/{character}/flags",
            json={
                "pointer": "/wounds/currentWounds",
                "rule": "wounds.current_exceeds_total",
                "status": "accepted",
            },
        )

        # ...and an unrelated later edit does not reopen it.
        client.patch(
            f"/api/characters/{character}",
            json={"operations": [{"pointer": "/bio/quirk", "value": "Twitchy"}]},
        )
        report = client.get(f"/api/characters/{character}/report").json()
        [flag] = [f for f in report["flags"] if f["rule"] == "wounds.current_exceeds_total"]
        assert flag["status"] == "accepted"

    def test_model_confidence_flags_survive_revalidation(self, client, character) -> None:
        """They cannot be recomputed from the document, so they must not be dropped."""
        import scribe40k.api as api

        api.store.save_report(
            character,
            _report_with(
                flags=[
                    Flag(
                        pointer="/bio/career",
                        severity="warning",
                        rule="model.low_confidence",
                        message="faint",
                        alternatives=["Adept"],
                    )
                ]
            ),
        )

        client.patch(
            f"/api/characters/{character}",
            json={"operations": [{"pointer": "/bio/quirk", "value": "Twitchy"}]},
        )

        report = client.get(f"/api/characters/{character}/report").json()
        [flag] = [f for f in report["flags"] if f["rule"] == "model.low_confidence"]
        assert flag["alternatives"] == ["Adept"]

    def test_an_unknown_flag_is_404(self, client, character) -> None:
        import scribe40k.api as api

        api.store.save_report(character, _report_with())

        response = client.post(
            f"/api/characters/{character}/flags",
            json={"pointer": "/nope", "rule": "nope", "status": "accepted"},
        )
        assert response.status_code == 404

    def test_an_unknown_status_is_rejected(self, client, character) -> None:
        import scribe40k.api as api

        api.store.save_report(
            character,
            _report_with(flags=[Flag(pointer="/a", severity="info", rule="r", message="m")]),
        )

        response = client.post(
            f"/api/characters/{character}/flags",
            json={"pointer": "/a", "rule": "r", "status": "banana"},
        )
        assert response.status_code == 400


class TestHandCreatedCharacters:
    """A character typed in by hand deserves the same checking as an imported one."""

    def test_consistency_flags_appear_without_an_import(self, client, character) -> None:
        client.patch(
            f"/api/characters/{character}",
            json={
                "operations": [
                    {"pointer": "/wounds/totalWounds", "value": 12},
                    {"pointer": "/wounds/currentWounds", "value": 17},
                ]
            },
        )

        report = client.get(f"/api/characters/{character}/report").json()

        assert any(f["rule"] == "wounds.current_exceeds_total" for f in report["flags"])

    def test_such_a_report_records_no_source_or_models(self, client, character) -> None:
        client.patch(
            f"/api/characters/{character}",
            json={"operations": [{"pointer": "/bio/quirk", "value": "Twitchy"}]},
        )

        report = client.get(f"/api/characters/{character}/report").json()

        assert report["source"] is None
        assert report["models"] is None

    def test_it_still_validates_against_the_report_schema(self, client, character) -> None:
        import json

        from jsonschema import Draft202012Validator

        from scribe40k.paths import REPORT_SCHEMA

        client.patch(
            f"/api/characters/{character}",
            json={"operations": [{"pointer": "/bio/quirk", "value": "Twitchy"}]},
        )
        report = client.get(f"/api/characters/{character}/report").json()

        schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(report))

        assert not errors, "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:5])


class TestAssignmentTray:
    def test_assigning_a_fragment_writes_it_into_the_field(self, client, character) -> None:
        import scribe40k.api as api

        item = UnmappedItem(text="Twitchy", source=UnmappedSource(pdfPage=1))
        item.ensure_id()
        api.store.save_report(character, _report_with(unmapped=[item]))

        response = client.post(
            f"/api/characters/{character}/unmapped",
            json={"id": item.id, "status": "assigned", "assignedTo": "/bio/quirk"},
        )

        assert response.json()["character"]["bio"]["quirk"] == "Twitchy"
        report = client.get(f"/api/characters/{character}/report").json()
        assert report["unmapped"][0]["status"] == "assigned"
        assert report["unmapped"][0]["assignedTo"] == "/bio/quirk"

    def test_dismissing_a_fragment_changes_nothing_else(self, client, character) -> None:
        import scribe40k.api as api

        item = UnmappedItem(text="scribble", source=UnmappedSource(pdfPage=1))
        item.ensure_id()
        api.store.save_report(character, _report_with(unmapped=[item]))

        client.post(
            f"/api/characters/{character}/unmapped",
            json={"id": item.id, "status": "dismissed"},
        )

        report = client.get(f"/api/characters/{character}/report").json()
        assert report["unmapped"][0]["status"] == "dismissed"

    def test_assigning_without_a_target_is_rejected(self, client, character) -> None:
        import scribe40k.api as api

        item = UnmappedItem(text="x", source=UnmappedSource(pdfPage=1))
        item.ensure_id()
        api.store.save_report(character, _report_with(unmapped=[item]))

        response = client.post(
            f"/api/characters/{character}/unmapped",
            json={"id": item.id, "status": "assigned"},
        )
        assert response.status_code == 400


class TestReference:
    def test_serves_the_printed_sheet_layout(self, client) -> None:
        reference = client.get("/api/reference").json()

        assert len(reference["skills"]) == 48
        assert len(reference["minorPowers"]) == 32
        assert len(reference["armourLocations"]) == 6
        assert reference["page"] == {"widthMm": 213.0, "heightMm": 276.0}

    def test_skills_carry_their_printed_column(self, client) -> None:
        skills = client.get("/api/reference").json()["skills"]
        columns = {s["column"] for s in skills}
        assert columns == {1, 2, 3}

    def test_group_skills_are_marked(self, client) -> None:
        skills = {s["key"]: s for s in client.get("/api/reference").json()["skills"]}
        assert skills["commonLore"]["isGroup"] is True
        assert skills["dodge"]["isGroup"] is False

    def test_blank_sheet_endpoint(self, client) -> None:
        assert client.get("/api/blank").json()["bio"]["characterName"] is None


class TestHealth:
    def test_reports_the_configured_models(self, client) -> None:
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert "provider" in health["ocr"]
        assert "provider" in health["reasoning"]


class TestPageImages:
    def test_a_missing_page_is_404(self, client, character) -> None:
        assert client.get(f"/api/characters/{character}/pages/1").status_code == 404

    def test_a_page_is_served_and_can_be_cropped(self, client, character) -> None:
        from PIL import Image

        import scribe40k.api as api

        pages = api.store.pages_dir(character)
        pages.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 300), "white").save(pages / "page-01.png")

        whole = client.get(f"/api/characters/{character}/pages/1")
        assert whole.status_code == 200

        crop = client.get(
            f"/api/characters/{character}/pages/1",
            params={"x0": 10, "y0": 10, "x1": 110, "y1": 60},
        )
        assert crop.status_code == 200
        with Image.open(__import__("io").BytesIO(crop.content)) as image:
            assert image.size == (100, 50)

    def test_an_empty_crop_is_rejected(self, client, character) -> None:
        from PIL import Image

        import scribe40k.api as api

        pages = api.store.pages_dir(character)
        pages.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 300), "white").save(pages / "page-01.png")

        response = client.get(
            f"/api/characters/{character}/pages/1",
            params={"x0": 100, "y0": 100, "x1": 50, "y1": 50},
        )
        assert response.status_code == 400


class TestStaleFlags:
    """A rule flag is derived state, and must not outlive what it describes.

    On the calibration character nine schema errors survived every correction the user
    could make on screen, because they were computed at import time against a document
    that no longer existed and nothing recomputed them until the next edit.
    """

    def test_opening_a_character_drops_a_flag_that_no_longer_applies(
        self, client, character
    ) -> None:
        import scribe40k.api as api

        api.store.save_report(
            character,
            _report_with(
                flags=[
                    Flag(
                        pointer="/skills/commonLore/specialisations/0/proficiency",
                        severity="error",
                        rule="schema.required",
                        message="'modifier' is a required property",
                    )
                ]
            ),
        )

        report = client.get(f"/api/characters/{character}").json()["report"]

        assert not [f for f in report["flags"] if f["rule"] == "schema.required"]

    def test_a_model_flag_is_not_recomputable_and_survives(self, client, character) -> None:
        """Only what the rules can regenerate may be thrown away."""
        import scribe40k.api as api

        api.store.save_report(
            character,
            _report_with(
                flags=[
                    Flag(
                        pointer="/bio/career",
                        severity="warning",
                        rule="model.low_confidence",
                        message="unsure",
                    )
                ]
            ),
        )

        report = client.get(f"/api/characters/{character}").json()["report"]

        assert [f["rule"] for f in report["flags"] if f["pointer"] == "/bio/career"] == [
            "model.low_confidence"
        ]

    def test_a_resolved_flag_stays_resolved_across_an_open(self, client, character) -> None:
        """Recomputing must not undo the user's judgement."""
        client.get(f"/api/characters/{character}")  # raises the rule flags in the first place
        accepted = client.post(
            f"/api/characters/{character}/flags",
            json={
                "pointer": "/wounds/totalWounds",
                "rule": "wounds.missing_total",
                "status": "accepted",
            },
        )
        assert accepted.status_code == 200

        report = client.get(f"/api/characters/{character}").json()["report"]
        wounds = next(f for f in report["flags"] if f["rule"] == "wounds.missing_total")

        assert wounds["status"] == "accepted"


class TestNotePages:
    def test_a_note_page_can_be_added(self, client, character) -> None:
        response = client.post(
            f"/api/characters/{character}/note-pages",
            json={"title": "Session 4", "text": "Owed 200 thrones to Vex."},
        )

        assert response.status_code == 201
        assert response.json()["character"]["notePages"] == [
            {"title": "Session 4", "text": "Owed 200 thrones to Vex.", "sourcePdfPage": None}
        ]

    def test_a_fragment_becomes_a_note_page_and_leaves_the_tray(self, client, character) -> None:
        import scribe40k.api as api

        item = UnmappedItem(
            text="Rescued the astropath from the hab-block.",
            source=UnmappedSource(pdfPage=10, location="page 10, whole page"),
        )
        item.ensure_id()
        api.store.save_report(character, _report_with(unmapped=[item]))

        payload = client.post(
            f"/api/characters/{character}/note-pages",
            json={"fromUnmapped": item.id},
        ).json()

        [page] = payload["character"]["notePages"]
        assert page["text"] == "Rescued the astropath from the hab-block."
        assert page["sourcePdfPage"] == 10

        [stored] = payload["report"]["unmapped"]
        assert stored["status"] == "assigned"
        assert stored["assignedTo"] == "/notePages/0/text"

    def test_an_unknown_fragment_is_rejected(self, client, character) -> None:
        import scribe40k.api as api

        api.store.save_report(character, _report_with())

        response = client.post(
            f"/api/characters/{character}/note-pages",
            json={"fromUnmapped": "nosuchthing"},
        )

        assert response.status_code == 404
