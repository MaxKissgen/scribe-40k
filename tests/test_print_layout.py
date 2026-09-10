"""Reading back a sheet this program printed, after somebody wrote on it.

A scribe PDF re-imported as a file is easy: it carries its own text layer and identifies
itself for nothing. The path people actually take is the other one -- print it, cross a
talent out, add a gear line in the margin, scan it back -- and a printer does not print
text layers. What returns is a photograph of a page the blank template does not describe,
least of all the page a long gear list spilled onto.

So each export records what its pages looked like, and a scan is matched against the
character's own paper instead.
"""

from __future__ import annotations

import numpy as np
import pytest

from scribe40k.blank import blank_character
from scribe40k.paths import PAGE_FINGERPRINTS, SAMPLES
from scribe40k.pipeline.fingerprint import fingerprint_from_gray
from scribe40k.pipeline.ingest import CLASSIFY_DPI
from scribe40k.pipeline.print_layout import PrintLayout, match_against
from scribe40k.store import CharacterStore

pymupdf = pytest.importorskip("pymupdf")
PIL = pytest.importorskip("PIL")


def _overflowing_character() -> dict:
    """More gear than the printed lines hold, so the export spills onto a further page."""
    document = blank_character().to_json_dict()
    document["bio"]["characterName"] = "Overflow"
    document["gear"] = [
        {"name": f"gear item number {i}", "quantity": None, "notes": None} for i in range(45)
    ]
    return document


@pytest.fixture(scope="module")
def printed(tmp_path_factory):
    """Export a character that spills, and record what came out."""
    playwright = pytest.importorskip("playwright")
    assert playwright
    if not PAGE_FINGERPRINTS.exists():
        pytest.skip("assets not built")

    from scribe40k import api
    from scribe40k.export.pdf import ExportError, export_character

    root = tmp_path_factory.mktemp("printed")
    store = CharacterStore(root / "characters")
    store.save("overflow", _overflowing_character())

    original = api.store
    api.store = store
    try:
        destination = root / "overflow.pdf"
        try:
            export_character("overflow", destination)
        except ExportError as exc:
            pytest.skip(f"cannot export: {exc}")
        layouts = store.load_print_layouts("overflow")
    finally:
        api.store = original

    return destination, layouts, root


def _pages_of(pdf_path, degrade=None) -> dict:
    """Fingerprints of a PDF, optionally put through a printer, a pen and a scanner.

    Rendered at a different resolution from the recording, as a scanner would be.
    """
    from PIL import Image

    vectors: dict[int, np.ndarray] = {}
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            pix = doc[index].get_pixmap(dpi=CLASSIFY_DPI + 17, colorspace=pymupdf.csGRAY)
            image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            gray = np.asarray(degrade(image) if degrade else image)
            vectors[index + 1] = fingerprint_from_gray(gray)
    return vectors


def _through_a_scanner(*, skew: float, marks: int, seed: int = 7):
    """Skew, somebody's biro, a little blur, and a scanner's tone curve."""
    from PIL import Image, ImageDraw, ImageFilter

    rng = np.random.default_rng(seed)

    def degrade(image):
        if skew:
            image = image.rotate(skew, resample=Image.BILINEAR, fillcolor=255)
        pen = ImageDraw.Draw(image)
        for _ in range(marks):
            x = int(rng.integers(120, image.width - 120))
            y = int(rng.integers(120, image.height - 120))
            pen.line(
                [(x, y), (x + int(rng.integers(20, 90)), y + int(rng.integers(-8, 8)))],
                fill=40,
                width=3,
            )
        image = image.filter(ImageFilter.GaussianBlur(0.8))
        array = np.asarray(image).astype(np.float32)
        array = array * 0.86 + 14 + rng.normal(0, 4, array.shape)
        return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))

    return degrade


class TestRecording:
    def test_it_knows_which_sheet_page_each_printed_page_was(self, printed) -> None:
        _, layouts, _ = printed
        [layout] = layouts

        assert [page.sheet_page for page in layout.pages] == sorted(
            page.sheet_page for page in layout.pages
        ), "printed pages come out in sheet order"
        assert set(page.sheet_page for page in layout.pages) == {1, 2, 3}

    def test_it_knows_a_page_was_printed_in_parts(self, printed) -> None:
        """Which is the fact the blank template can never supply."""
        _, layouts, _ = printed
        [layout] = layouts

        spilled = [sheet for sheet in (1, 2, 3) if layout.parts_of(sheet) > 1]
        assert spilled, "the fixture outgrows its printed lines, so something must spill"

    def test_exporting_twice_remembers_both(self, printed, tmp_path) -> None:
        from scribe40k import api
        from scribe40k.export.pdf import export_character

        _, _, root = printed
        store = CharacterStore(root / "characters")
        original = api.store
        api.store = store
        try:
            export_character("overflow", tmp_path / "again.pdf")
        finally:
            api.store = original

        assert len(store.load_print_layouts("overflow")) == 2

    def test_it_survives_a_round_trip_through_disk(self, printed) -> None:
        import json

        _, layouts, _ = printed
        [layout] = layouts

        restored = PrintLayout.from_json_dict(json.loads(json.dumps(layout.to_json_dict())))

        assert [p.sheet_page for p in restored.pages] == [p.sheet_page for p in layout.pages]
        assert restored.pages[0].vector == layout.pages[0].vector


class TestMatchingAScanBackToIt:
    def test_a_clean_re_render_matches_exactly(self, printed) -> None:
        destination, layouts, _ = printed

        found = match_against(_pages_of(destination), layouts)

        assert len(found) == len(layouts[0].pages)
        assert all(match.score > 0.9 for match in found.values())

    def test_a_marked_up_scan_still_matches(self, printed) -> None:
        """Skew, blur, a scanner's tone curve, and somebody's biro."""
        destination, layouts, _ = printed

        found = match_against(
            _pages_of(destination, _through_a_scanner(skew=0.4, marks=6)), layouts
        )

        expected = {page.index: page.sheet_page for page in layouts[0].pages}
        assert {page: match.sheet_page for page, match in found.items()} == expected

    def test_the_spilled_half_is_recognised_as_a_part(self, printed) -> None:
        destination, layouts, _ = printed

        found = match_against(
            _pages_of(destination, _through_a_scanner(skew=0.4, marks=6)), layouts
        )

        assert any(match.part > 1 for match in found.values())

    def test_a_different_document_matches_nothing(self, printed) -> None:
        """The guard that lets the weak matches be trusted at all: two strong ones, or
        the whole layout is thrown out."""
        destination, layouts, _ = printed
        other = SAMPLES / "Dark Heresy 1 PC Sheet filled.pdf"
        if not other.exists():
            pytest.skip("calibration scan unavailable")
        assert destination.exists()

        assert match_against(_pages_of(other), layouts) == {}

    def test_nothing_recorded_means_nothing_claimed(self, printed) -> None:
        destination, _, _ = printed

        assert match_against(_pages_of(destination), []) == {}


class TestThroughPrepare:
    def test_a_scan_of_the_printout_is_classified_from_it(self, printed, tmp_path) -> None:
        """The end of the story: pages the blank template cannot place, placed."""
        from scribe40k.pipeline.run import prepare

        destination, layouts, _ = printed

        class NoOcr:
            name = model = "none"

            def transcribe(self, pages):
                return []

        prepared = prepare(
            destination,
            NoOcr(),
            image_dir=tmp_path / "pages",
            printed_as=layouts,
        )

        assert sorted(set(prepared.proposal.values())) == [1, 2, 3]
        placed = [p for p in prepared.ingested.pages if p.matched_by == "print"]
        assert len(placed) == len(prepared.ingested.pages)
        assert "own printout" in placed[0].note

    def test_without_a_recording_nothing_changes(self, printed, tmp_path) -> None:
        from scribe40k.pipeline.run import prepare

        destination, _, _ = printed

        class NoOcr:
            name = model = "none"

            def transcribe(self, pages):
                return []

        prepared = prepare(destination, NoOcr(), image_dir=tmp_path / "pages2")

        assert not any(p.matched_by == "print" for p in prepared.ingested.pages)
