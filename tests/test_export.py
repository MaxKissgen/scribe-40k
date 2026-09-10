"""PDF export, and the round trip back through the extractor.

The export tests need Chromium and a built frontend, so they skip when either is absent --
but when they do run they are the strongest check in the suite, because they exercise the
real components, the real stylesheet, and the real classifier together.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest

from scribe40k.blank import blank_character
from scribe40k.export.pdf import PAGE_SIZES, ExportError, ExportOptions, render_url_to_pdf
from scribe40k.paths import FRONTEND_DIST, PAGE_FINGERPRINTS
from scribe40k.pipeline.ingest import assign_sheet_pages, load_template_fingerprints
from scribe40k.store import CharacterStore

pymupdf = pytest.importorskip("pymupdf")


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


needs_export = pytest.mark.skipif(
    not (_playwright_available() and FRONTEND_DIST.exists()),
    reason="PDF export needs Playwright and a built frontend",
)


class TestOptions:
    def test_the_native_size_is_the_templates_own(self) -> None:
        assert PAGE_SIZES["native"] == ("213.0mm", "276.0mm")

    def test_a4_and_letter_are_offered_too(self) -> None:
        assert set(PAGE_SIZES) == {"native", "a4", "letter"}

    def test_an_unknown_size_is_rejected_with_the_alternatives(self, tmp_path) -> None:
        with pytest.raises(ExportError, match="unknown page size"):
            render_url_to_pdf(
                "http://127.0.0.1:1/print/x",
                tmp_path / "out.pdf",
                ExportOptions(page_size="a3"),
            )


class TestLayoutVariants:
    """A sheet page can be recognised in more than one layout.

    The original template and this application's rendering of the same page are lookalikes
    rather than pixel twins, and correlate at only about 0.38. Without support for several
    variants per page, a sheet exported by this tool could not be imported back into it.
    """

    def _unit(self, values: list[float]) -> np.ndarray:
        vector = np.asarray(values, dtype=np.float32)
        return vector / np.linalg.norm(vector)

    def test_matching_any_variant_is_enough(self) -> None:
        templates = {1: [self._unit([1, 0, 0]), self._unit([0, 0, 1])]}
        candidates = {5: self._unit([0, 0.02, 1])}

        sheet_page, score, _ = assign_sheet_pages(candidates, templates)[5]

        assert sheet_page == 1
        assert score > 0.9

    def test_a_single_vector_still_works(self) -> None:
        """Backwards compatible with a fingerprint file that predates variants."""
        templates = {1: self._unit([1, 0])}
        candidates = {2: self._unit([1, 0.05])}

        assert assign_sheet_pages(candidates, templates)[2][0] == 1

    @pytest.mark.skipif(not PAGE_FINGERPRINTS.exists(), reason="assets not built")
    def test_the_committed_assets_carry_both_layouts(self) -> None:
        variants = load_template_fingerprints()

        assert set(variants) == {1, 2, 3, 4, 5}
        assert all(len(vectors) >= 2 for vectors in variants.values()), (
            "run scribe40k.tools.record_layout so exported sheets can be re-imported"
        )


@needs_export
class TestRoundTrip:
    """Fill a sheet, export it, and read it back in."""

    @pytest.fixture(scope="class")
    def exported(self, tmp_path_factory):
        from scribe40k import api
        from scribe40k.export.pdf import export_character

        root = tmp_path_factory.mktemp("roundtrip")
        store = CharacterStore(root / "characters")

        document = blank_character().to_json_dict()
        document["bio"]["characterName"] = "Round Trip"
        document["bio"]["career"] = "Assassin"
        document["characteristics"]["agility"]["total"] = 45
        document["skills"]["dodge"]["proficiency"] = {"level": "+10"}
        document["gear"] = [{"name": "autogun", "quantity": None, "notes": None}]
        store.save("round-trip", document)

        original = api.store
        api.store = store
        try:
            destination = root / "round-trip.pdf"
            export_character("round-trip", destination)
        finally:
            api.store = original

        return destination

    def test_the_pdf_is_the_templates_page_size(self, exported) -> None:
        with pymupdf.open(exported) as doc:
            page = doc[0]
            width_mm = page.rect.width / 72 * 25.4
            height_mm = page.rect.height / 72 * 25.4

        assert width_mm == pytest.approx(213, abs=0.5)
        assert height_mm == pytest.approx(276, abs=0.5)

    def test_the_values_are_selectable_text_not_a_picture(self, exported) -> None:
        with pymupdf.open(exported) as doc:
            text = "\n".join(page.get_text("text") for page in doc)

        assert "Round Trip" in text
        assert "Assassin" in text
        assert "autogun" in text

    def test_editor_chrome_does_not_reach_the_paper(self, exported) -> None:
        with pymupdf.open(exported) as doc:
            text = "\n".join(page.get_text("text") for page in doc).lower()

        for chrome in ("need review", "add gear line", "unassigned", "export pdf"):
            assert chrome not in text, f"{chrome!r} leaked into the printed sheet"

    def test_an_empty_psychic_section_costs_no_pages(self, exported) -> None:
        with pymupdf.open(exported) as doc:
            assert doc.page_count == 3

    @pytest.mark.skipif(not PAGE_FINGERPRINTS.exists(), reason="assets not built")
    def test_the_export_can_be_read_back_in(self, exported, tmp_path) -> None:
        from scribe40k.pipeline.ingest import ingest

        result = ingest(exported, tmp_path / "pages")
        found = {page.sheet_page for page in result.sheet_pages}

        assert found == {1, 2, 3}, f"classified as {result.summary()}"

    @pytest.mark.skipif(not PAGE_FINGERPRINTS.exists(), reason="assets not built")
    def test_the_export_needs_no_ocr(self, exported, tmp_path) -> None:
        """It carries a real text layer, so re-importing it should cost nothing."""
        from scribe40k.pipeline.ingest import ingest

        result = ingest(exported, tmp_path / "pages")

        assert all(page.text_source == "text_layer" for page in result.sheet_pages)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
class TestFrontendBuild:
    def test_the_built_frontend_is_where_the_server_expects_it(self) -> None:
        if not FRONTEND_DIST.exists():
            pytest.skip("frontend not built")
        assert (FRONTEND_DIST / "index.html").exists()


class TestRebuildingAssetsPreservesLayouts:
    """`build_assets` is a documented setup step, and it used to clobber the recordings.

    Losing the `html:*` variants silently breaks re-importing an exported sheet, with
    nothing in the output to say why -- exactly the sort of failure that looks like a bug
    in the classifier rather than a missing asset.
    """

    def test_recorded_layouts_survive_a_rebuild(self, tmp_path, monkeypatch) -> None:
        import json

        from scribe40k.tools import build_assets

        assets = tmp_path / "page-fingerprints.json"
        assets.write_text(
            json.dumps(
                {
                    "pages": [
                        {"sheetPage": 1, "variant": "template", "vector": [0.0]},
                        {"sheetPage": 1, "variant": "html:sparse", "vector": [1.0]},
                        {"sheetPage": 2, "variant": "html:full", "vector": [2.0]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(build_assets, "PAGE_FINGERPRINTS", assets)

        carried = build_assets._preserve_recorded_layouts()

        assert {page["variant"] for page in carried} == {"html:sparse", "html:full"}
        assert all(page["variant"] != "template" for page in carried), (
            "the template variant is rebuilt from the PDF and must not be duplicated"
        )

    def test_a_first_run_with_no_assets_file_is_fine(self, tmp_path, monkeypatch) -> None:
        from scribe40k.tools import build_assets

        monkeypatch.setattr(build_assets, "PAGE_FINGERPRINTS", tmp_path / "absent.json")

        assert build_assets._preserve_recorded_layouts() == []

    def test_a_corrupt_assets_file_does_not_stop_the_rebuild(self, tmp_path, monkeypatch) -> None:
        from scribe40k.tools import build_assets

        broken = tmp_path / "page-fingerprints.json"
        broken.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(build_assets, "PAGE_FINGERPRINTS", broken)

        assert build_assets._preserve_recorded_layouts() == []


class TestAnOverflowingSheetRoundTrips:
    """A sheet with more content than the printed lines hold does not export as five pages
    and import as five pages -- it exports as more pages than the form has, and every one
    of them has to find its way back to the page it came from.

    Before continuations existed this was the worst case in the project: a filled export
    came back with one page of five recognised, because each spilled half looked like its
    sheet page without looking like it *more* than the other half did.
    """

    @pytest.fixture(scope="class")
    def exported(self, tmp_path_factory):
        from scribe40k import api
        from scribe40k.export.pdf import export_character

        root = tmp_path_factory.mktemp("overflow")
        store = CharacterStore(root / "characters")

        document = blank_character().to_json_dict()
        document["bio"]["characterName"] = "Overflow"
        document["bio"]["description"] = "\n".join(
            f"A long line of description number {i}" for i in range(20)
        )
        document["gear"] = [
            {"name": f"gear item number {i}", "quantity": i % 5 or None, "notes": None}
            for i in range(45)
        ]
        document["talentsAndTraits"]["advancesTalentsAndTraits"] = [
            {"name": f"Talent {i}", "specialisation": None, "notes": None} for i in range(35)
        ]
        store.save("overflow", document)

        original = api.store
        api.store = store
        try:
            destination = root / "overflow.pdf"
            export_character("overflow", destination)
        finally:
            api.store = original

        return destination, root

    def test_it_really_does_spill(self, exported) -> None:
        """If this stops being true the test below is proving nothing."""
        destination, _ = exported
        with pymupdf.open(destination) as doc:
            assert doc.page_count > 3, "the fixture is meant to outgrow its printed lines"

    @pytest.mark.skipif(not PAGE_FINGERPRINTS.exists(), reason="assets not built")
    def test_every_page_finds_the_sheet_page_it_came_from(self, exported) -> None:
        from scribe40k.pipeline.run import prepare

        destination, root = exported

        class RefuseOcr:
            name = model = "refuse"

            def transcribe(self, pages):
                raise AssertionError(f"an export carries its own text; OCR was called for {pages}")

        prepared = prepare(destination, RefuseOcr(), image_dir=root / "pages")

        assert sorted(set(prepared.proposal.values())) == [1, 2, 3]
        assert all(isinstance(target, int) for target in prepared.proposal.values()), (
            f"something was filed as notes: {prepared.proposal}"
        )

    @pytest.mark.skipif(not PAGE_FINGERPRINTS.exists(), reason="assets not built")
    def test_the_spilled_half_joins_the_half_it_spilled_from(self, exported) -> None:
        from scribe40k.pipeline.run import prepare

        destination, root = exported

        class NoOcr:
            name = model = "none"

            def transcribe(self, pages):
                return []

        prepared = prepare(destination, NoOcr(), image_dir=root / "pages2")
        by_sheet_page: dict[int, list[int]] = {}
        for pdf_page, target in sorted(prepared.proposal.items()):
            if isinstance(target, int):
                by_sheet_page.setdefault(target, []).append(pdf_page)

        parted = {sheet: pages for sheet, pages in by_sheet_page.items() if len(pages) > 1}
        assert parted, "the fixture spills, so some sheet page must be covered twice"
        for pages in parted.values():
            assert pages == sorted(pages), "parts stay in the order they were printed"
            assert pages[-1] - pages[0] == len(pages) - 1, "a spill is the very next page"
