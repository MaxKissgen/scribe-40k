"""The page assignment step, and the two halves of the pipeline it sits between.

Getting the page assignment wrong maps a whole sheet into the wrong fields, and nothing
downstream can recover from it. So the expensive half of the pipeline does not start until
someone has had the chance to look at the thumbnails and say yes.
"""

from __future__ import annotations

import json

import pytest

from scribe40k.llm.base import OcrPage, ReasoningResponse
from scribe40k.pipeline.ingest import IngestedPage, IngestResult, PageKind, TextSource
from scribe40k.pipeline.run import (
    PreparedPages,
    apply_assignment,
    duplicate_sheet_pages,
    finish,
    prepare,
    propose_assignment,
)


def _page(pdf_page: int, kind: PageKind, sheet_page: int | None = None) -> IngestedPage:
    return IngestedPage(
        pdf_page=pdf_page,
        kind=kind,
        sheet_page=sheet_page,
        text_source=TextSource.OCR,
        match_score=0.9 if sheet_page else 0.1,
        ink=0.2,
    )


@pytest.fixture
def ingested():
    return IngestResult(
        source_pdf="scan.pdf",
        file_hash="abc",
        pages=[
            _page(1, PageKind.SHEET, 1),
            _page(2, PageKind.BLANK),
            _page(3, PageKind.UNRECOGNISED),
        ],
    )


class TestProposal:
    def test_every_page_of_the_upload_is_offered(self, ingested) -> None:
        """Including the blank ones. A duplex back the classifier called blank is exactly
        the sort of thing a user needs to be able to drag back in."""
        assert propose_assignment(ingested) == {1: 1, 2: "skip", 3: "notes"}


class TestApplyAssignment:
    def test_a_page_can_be_moved_onto_a_sheet_page(self, ingested) -> None:
        apply_assignment(ingested, {3: 2})
        moved = ingested.page_for_pdf(3)

        assert moved.kind is PageKind.SHEET
        assert moved.sheet_page == 2

    def test_a_sheet_page_can_be_sent_to_the_notes(self, ingested) -> None:
        apply_assignment(ingested, {1: "notes"})
        moved = ingested.page_for_pdf(1)

        assert moved.kind is PageKind.UNRECOGNISED
        assert moved.sheet_page is None

    def test_a_page_can_be_dropped_entirely(self, ingested) -> None:
        apply_assignment(ingested, {3: "skip"})

        assert ingested.page_for_pdf(3).kind is PageKind.BLANK

    def test_a_moved_page_records_that_a_person_moved_it(self, ingested) -> None:
        """So the report does not later claim the machine was confident about it."""
        apply_assignment(ingested, {3: 2})

        assert ingested.page_for_pdf(3).matched_by == "user"
        assert "by hand" in ingested.page_for_pdf(3).note

    def test_a_page_left_where_it_was_is_not_relabelled(self, ingested) -> None:
        apply_assignment(ingested, {1: 1, 2: "skip", 3: "notes"})

        assert [p.matched_by for p in ingested.pages] == ["image", "image", "image"]

    def test_pages_the_assignment_does_not_mention_keep_what_they_had(self, ingested) -> None:
        apply_assignment(ingested, {3: 2})

        assert ingested.page_for_pdf(1).sheet_page == 1


class TestDuplicates:
    def test_two_pages_claiming_one_sheet_page_are_reported(self) -> None:
        assert duplicate_sheet_pages({1: 2, 2: 2, 3: "notes"}) == [2]

    def test_notes_and_skips_may_repeat(self) -> None:
        assert duplicate_sheet_pages({1: "notes", 2: "notes", 3: "skip", 4: "skip"}) == []


class TestPendingRoundTrip:
    """The assignment is a separate HTTP request, so the read half has to survive on disk
    without re-reading the PDF or paying for OCR twice."""

    def test_everything_finish_needs_survives(self, ingested) -> None:
        prepared = PreparedPages(
            source_name="scan.pdf",
            ingested=ingested,
            transcriptions={
                1: OcrPage(pdf_page=1, sheet_page=1, text="page one", provider="x", model="y"),
                3: OcrPage(pdf_page=3, sheet_page=None, text="notes", provider="x", model="y"),
            },
            proposal=propose_assignment(ingested),
        )

        restored = PreparedPages.from_json_dict(json.loads(json.dumps(prepared.to_json_dict())))

        assert restored.source_name == "scan.pdf"
        assert restored.proposal == {1: 1, 2: "skip", 3: "notes"}
        assert restored.transcriptions[1].text == "page one"
        assert [p.kind for p in restored.ingested.pages] == [p.kind for p in ingested.pages]


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
    name = model = "stub-reasoning"
    supports_vision = supports_structured_output = True

    def __init__(self) -> None:
        self.labels: list[str] = []

    def complete(self, request):
        self.labels.append(request.label)
        envelope = {"data": {"bio": {"characterName": "Sebastian"}}}
        return ReasoningResponse(text=json.dumps(envelope), parsed=envelope)


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    """An API with both providers stubbed, so an import costs nothing."""
    import importlib

    monkeypatch.setenv("SCRIBE40K_DATA", str(tmp_path))
    import scribe40k.api as api

    importlib.reload(api)

    reasoning = StubReasoning()
    monkeypatch.setattr(api, "build_ocr_provider", lambda *a, **k: StubOcr())
    monkeypatch.setattr(api, "build_reasoning_provider", lambda *a, **k: reasoning)

    from fastapi.testclient import TestClient

    return TestClient(api.app), api, reasoning


@pytest.fixture
def two_page_pdf(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    for index in range(2):
        page = doc.new_page(width=604, height=782)
        for row in range(18):
            for col in range(12):
                if (row * 5 + col * 3 + index * 2) % 3:
                    page.draw_rect(
                        pymupdf.Rect(24 + col * 46, 24 + row * 40, 60 + col * 46, 56 + row * 40),
                        fill=(0, 0, 0),
                    )
    path = tmp_path / "scan.pdf"
    doc.save(path)
    doc.close()
    return path


def _import(client, pdf):
    with pdf.open("rb") as handle:
        return client.post("/api/import", files={"file": ("scan.pdf", handle, "application/pdf")})


class TestTheImportStopsForConfirmation:
    def test_importing_does_not_reach_the_reasoning_model(self, stubbed, two_page_pdf) -> None:
        """The whole point: nothing is spent until the assignment is confirmed."""
        client, _api, reasoning = stubbed

        response = _import(client, two_page_pdf)

        assert response.status_code == 201
        assert response.json()["status"] == "awaiting_assignment"
        assert reasoning.labels == []

    def test_a_proposal_covers_every_page_of_the_upload(self, stubbed, two_page_pdf) -> None:
        client, _api, _ = stubbed

        pages = _import(client, two_page_pdf).json()["pages"]

        assert [p["pdfPage"] for p in pages] == [1, 2]
        assert all("proposed" in p for p in pages)

    def test_the_proposal_shows_what_was_read_on_each_page(self, stubbed, two_page_pdf) -> None:
        """A thumbnail alone does not always say which page you are looking at."""
        client, _api, _ = stubbed

        [first, _] = _import(client, two_page_pdf).json()["pages"]

        assert first["transcriptionPreview"] == "transcription of PDF page 1"

    def test_no_character_exists_until_it_is_confirmed(self, stubbed, two_page_pdf) -> None:
        client, _api, _ = stubbed
        _import(client, two_page_pdf)

        assert client.get("/api/characters").json() == []

    def test_the_proposal_survives_a_reload(self, stubbed, two_page_pdf) -> None:
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        again = client.get(f"/api/imports/{character_id}")

        assert again.status_code == 200
        assert again.json()["status"] == "awaiting_assignment"

    def test_page_images_are_available_before_confirmation(self, stubbed, two_page_pdf) -> None:
        """The assignment screen is thumbnails; it cannot wait for a character to exist."""
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        assert client.get(f"/api/characters/{character_id}/pages/1").status_code == 200

    def test_an_abandoned_import_can_be_thrown_away(self, stubbed, two_page_pdf) -> None:
        client, api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        assert client.delete(f"/api/imports/{character_id}").status_code == 204
        assert not api.store.directory(character_id).exists()


class TestConfirming:
    def test_confirming_maps_and_creates_the_character(self, stubbed, two_page_pdf) -> None:
        client, _api, reasoning = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        response = client.post(
            f"/api/imports/{character_id}/confirm",
            json={"assignment": {"1": 1, "2": "notes"}},
        )

        assert response.status_code == 200
        assert response.json()["character"]["bio"]["characterName"] == "Sebastian"
        assert reasoning.labels, "the reasoning model should have run now"

    def test_a_character_that_matched_nothing_is_still_created(self, stubbed, two_page_pdf) -> None:
        """Accepting a proposal of "all notes" is a decision too, and it must produce
        something the user can open rather than leaving the import stuck."""
        client, api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        response = client.post(f"/api/imports/{character_id}/confirm", json={})

        assert response.status_code == 200
        assert api.store.exists(character_id)
        assert len(response.json()["character"]["notePages"]) == 2

    def test_the_assignment_decides_which_section_reads_which_page(
        self, stubbed, two_page_pdf
    ) -> None:
        """Both pages onto sheet page 2 is refused, but page 1 onto sheet page 2 is not:
        it is the correction this screen exists for."""
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        payload = client.post(
            f"/api/imports/{character_id}/confirm",
            json={"assignment": {"1": 2, "2": "notes"}},
        ).json()

        pages = {p["pdfPage"]: p for p in payload["report"]["pages"]}
        assert pages[1]["sheetPage"] == 2
        assert pages[1]["matchedBy"] == "user"
        assert len(payload["character"]["notePages"]) == 1

    def test_a_page_can_be_sent_to_the_notes(self, stubbed, two_page_pdf) -> None:
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        payload = client.post(
            f"/api/imports/{character_id}/confirm",
            json={"assignment": {"1": 1, "2": "notes"}},
        ).json()

        [note] = payload["character"]["notePages"]
        assert note["sourcePdfPage"] == 2
        assert note["text"] == "transcription of PDF page 2"

    def test_a_page_can_be_left_out_entirely(self, stubbed, two_page_pdf) -> None:
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        payload = client.post(
            f"/api/imports/{character_id}/confirm",
            json={"assignment": {"1": 1, "2": "skip"}},
        ).json()

        assert payload["character"]["notePages"] == []

    def test_one_sheet_page_cannot_be_claimed_twice(self, stubbed, two_page_pdf) -> None:
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        response = client.post(
            f"/api/imports/{character_id}/confirm",
            json={"assignment": {"1": 2, "2": 2}},
        )

        assert response.status_code == 400
        assert "more than one" in response.json()["detail"]

    def test_a_page_the_upload_does_not_have_is_rejected(self, stubbed, two_page_pdf) -> None:
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        response = client.post(
            f"/api/imports/{character_id}/confirm",
            json={"assignment": {"9": 1}},
        )

        assert response.status_code == 400

    def test_confirming_twice_is_refused(self, stubbed, two_page_pdf) -> None:
        """The pending file is gone; a second confirmation would map from nothing."""
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]
        client.post(f"/api/imports/{character_id}/confirm", json={})

        assert client.post(f"/api/imports/{character_id}/confirm", json={}).status_code == 404


class TestFinishTakesTheProposalByDefault:
    def test_no_assignment_means_the_machine_was_right(self, ingested) -> None:
        prepared = PreparedPages(
            source_name="scan.pdf",
            ingested=ingested,
            transcriptions={
                1: OcrPage(pdf_page=1, sheet_page=1, text="x", provider="p", model="m")
            },
            proposal=propose_assignment(ingested),
        )

        outcome = finish(prepared, StubReasoning())

        assert {p.pdfPage: p.sheetPage for p in outcome.report.pages} == {1: 1, 2: None, 3: None}


class TestPrepareCostsNothingIrreversible:
    def test_it_does_not_touch_a_reasoning_model(self, tmp_path, two_page_pdf) -> None:
        prepared = prepare(two_page_pdf, StubOcr(), image_dir=tmp_path / "pages")

        assert set(prepared.proposal) == {1, 2}
        assert prepared.source_name == "scan.pdf"


class TestAnAbandonedImportIsNotLost:
    """A pending import is invisible to the character list by design. Without a second
    list it would be invisible full stop: uploaded, paid for, and unreachable."""

    def test_it_is_listed_while_it_waits(self, stubbed, two_page_pdf) -> None:
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]

        [entry] = client.get("/api/imports").json()

        assert entry["id"] == character_id
        assert entry["sourceName"] == "scan.pdf"
        assert entry["pageCount"] == 2

    def test_it_leaves_the_list_once_confirmed(self, stubbed, two_page_pdf) -> None:
        client, _api, _ = stubbed
        character_id = _import(client, two_page_pdf).json()["id"]
        client.post(f"/api/imports/{character_id}/confirm", json={})

        assert client.get("/api/imports").json() == []
        assert [c["id"] for c in client.get("/api/characters").json()] == [character_id]
