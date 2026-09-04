"""Page classification, which has to work before a single API call is worth making.

The pure functions are tested synthetically. The end-to-end test runs against the real
calibration scan and is skipped when that file is absent, because the sample is
copyrighted and therefore not committed.
"""

from __future__ import annotations

import numpy as np
import pytest

from scribe40k.paths import PAGE_FINGERPRINTS, SAMPLES
from scribe40k.pipeline.fingerprint import (
    FINGERPRINT_COLS,
    FINGERPRINT_ROWS,
    correlation,
    crop_to_content,
    fingerprint_from_gray,
    ink_coverage,
)
from scribe40k.pipeline.ingest import (
    PageKind,
    added_text_length,
    assign_sheet_pages,
    boilerplate_tokens,
    ingest,
    load_boilerplate_tokens,
)

SAMPLE_SCAN = SAMPLES / "Dark Heresy 1 PC Sheet filled.pdf"


def page_with_marks(
    coords: list[tuple[int, int]], size: tuple[int, int] = (400, 300)
) -> np.ndarray:
    """A white page with a few black blocks on it."""
    page = np.full(size, 255, dtype=np.uint8)
    for row, col in coords:
        page[row : row + 30, col : col + 30] = 0
    return page


class TestCropToContent:
    def test_margins_are_removed(self) -> None:
        page = np.full((200, 200), 255, dtype=np.uint8)
        page[80:120, 60:140] = 0

        cropped = crop_to_content(page)

        assert cropped.shape == (40, 80)

    def test_a_blank_page_is_returned_unchanged(self) -> None:
        page = np.full((50, 50), 255, dtype=np.uint8)
        assert crop_to_content(page).shape == (50, 50)

    def test_the_same_content_at_different_offsets_normalises_alike(self) -> None:
        """This is the property that makes matching a photocopy possible at all.

        The same layout, placed at a different offset on a differently sized page, must
        fingerprint identically. Two marks, because a single solid block has no internal
        structure left once it is cropped to itself.
        """
        small = np.full((300, 300), 255, dtype=np.uint8)
        small[100:150, 100:200] = 0
        small[180:200, 100:120] = 0

        shifted = np.full((400, 380), 255, dtype=np.uint8)
        shifted[20:70, 30:130] = 0
        shifted[100:120, 30:50] = 0

        assert correlation(fingerprint_from_gray(small), fingerprint_from_gray(shifted)) > 0.99


class TestFingerprint:
    def test_shape_is_the_declared_grid(self) -> None:
        fp = fingerprint_from_gray(page_with_marks([(10, 10)]))
        assert fp.shape == (FINGERPRINT_ROWS * FINGERPRINT_COLS,)

    def test_is_unit_norm(self) -> None:
        fp = fingerprint_from_gray(page_with_marks([(10, 10), (200, 150)]))
        assert np.isclose(np.linalg.norm(fp), 1.0)

    def test_identical_pages_correlate_perfectly(self) -> None:
        page = page_with_marks([(10, 10), (200, 150), (300, 40)])
        assert correlation(
            fingerprint_from_gray(page), fingerprint_from_gray(page)
        ) == pytest.approx(1.0)

    def test_different_layouts_correlate_poorly(self) -> None:
        a = fingerprint_from_gray(page_with_marks([(10, 10), (20, 200)]))
        b = fingerprint_from_gray(page_with_marks([(300, 250), (350, 30)]))
        assert correlation(a, b) < 0.5

    def test_a_featureless_page_yields_a_zero_vector(self) -> None:
        blank = np.full((200, 200), 255, dtype=np.uint8)
        assert not np.any(fingerprint_from_gray(blank))

    def test_rejects_colour_input(self) -> None:
        with pytest.raises(ValueError, match="2-D grayscale"):
            fingerprint_from_gray(np.zeros((10, 10, 3), dtype=np.uint8))


class TestInkCoverage:
    def test_blank_page_is_zero(self) -> None:
        assert ink_coverage(np.full((100, 100), 255, dtype=np.uint8)) == 0.0

    def test_half_covered_page(self) -> None:
        page = np.full((100, 100), 255, dtype=np.uint8)
        page[:50] = 0
        assert ink_coverage(page) == pytest.approx(0.5)


class TestTextLayerDetection:
    """Deciding whether a page needs OCR at all.

    The trap here is that the blank template carries a full text layer of its own -- 4,395
    characters on page 1 alone. Counting raw text length would classify an *empty* sheet as
    digitally filled and skip OCR entirely, extracting nothing.
    """

    def test_template_boilerplate_does_not_count_as_content(self) -> None:
        tokens = frozenset({"character", "name", "career", "rank"})
        assert added_text_length("Character Name Career Rank", tokens) == 0

    def test_only_the_players_additions_are_counted(self) -> None:
        tokens = frozenset({"career", "rank"})
        assert added_text_length("Career Assassin Rank 3", tokens) == len("assassin") + 1

    def test_tokenisation_is_case_and_whitespace_insensitive(self) -> None:
        assert boilerplate_tokens("  Foo\n\tBAR  foo ") == {"foo", "bar"}

    @pytest.mark.skipif(not PAGE_FINGERPRINTS.exists(), reason="assets not built")
    def test_the_real_template_vocabulary_cancels_itself_out(self) -> None:
        tokens = load_boilerplate_tokens()
        assert tokens, "the committed asset should carry the template's printed vocabulary"
        assert added_text_length(" ".join(sorted(tokens)), tokens) == 0


class TestAssignSheetPages:
    def _unit(self, values: list[float]) -> np.ndarray:
        vector = np.asarray(values, dtype=np.float32)
        return vector / np.linalg.norm(vector)

    def test_confident_matches_win_and_resolve_weak_ones(self) -> None:
        """The template-4-vs-5 problem, in miniature.

        Page B correlates almost equally with templates 1 and 2. Page A is decisive about
        template 1. Since a sheet has one of each page, B must be template 2.
        """
        templates = {1: self._unit([1, 0]), 2: self._unit([0, 1])}
        candidates = {
            10: self._unit([1.0, 0.02]),  # decisive: template 1
            11: self._unit([1.0, 0.95]),  # ambiguous on its own
        }

        result = assign_sheet_pages(candidates, templates)

        assert result[10][0] == 1
        assert result[11][0] == 2

    def test_a_page_matching_nothing_is_unrecognised(self) -> None:
        templates = {1: self._unit([1, 0, 0])}
        candidates = {5: self._unit([0, 0, 1])}

        sheet_page, score, note = assign_sheet_pages(candidates, templates)[5]

        assert sheet_page is None
        assert score < 0.55
        assert "matches no template page" in note

    def test_a_second_copy_of_a_page_is_reported_as_such(self) -> None:
        templates = {1: self._unit([1, 0])}
        candidates = {3: self._unit([1.0, 0.05]), 4: self._unit([1.0, 0.15])}

        result = assign_sheet_pages(candidates, templates)
        winner = 3 if result[3][0] == 1 else 4
        loser = 4 if winner == 3 else 3

        assert result[winner][0] == 1
        assert result[loser][0] is None
        assert "duplicate" in result[loser][2]

    def test_empty_input_is_not_an_error(self) -> None:
        assert assign_sheet_pages({}, {1: self._unit([1, 0])}) == {}


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory):
    """Ingest the calibration scan once for the whole module."""
    if not (SAMPLE_SCAN.exists() and PAGE_FINGERPRINTS.exists()):
        pytest.skip("calibration scan or page fingerprints unavailable")
    return ingest(SAMPLE_SCAN, tmp_path_factory.mktemp("pages"))


class TestAgainstTheCalibrationScan:
    """The sample is a 12-page duplex scan with its sheet pages out of order.

    Any regression that reintroduces the "PDF page N is sheet page N" assumption, or that
    stops tolerating the photocopy's different margins, fails here.
    """

    def test_all_five_sheet_pages_are_found(self, result) -> None:
        assert result.missing_sheet_pages == []

    def test_pages_are_matched_despite_being_out_of_order(self, result) -> None:
        found = {p.sheet_page: p.pdf_page for p in result.sheet_pages}
        assert found == {1: 1, 2: 3, 3: 9, 4: 5, 5: 7}

    def test_duplex_backs_are_discarded(self, result) -> None:
        blanks = {p.pdf_page for p in result.pages if p.kind is PageKind.BLANK}
        assert blanks == {2, 4, 6, 8, 11}

    def test_handwritten_notes_are_kept_but_not_mistaken_for_sheet_pages(self, result) -> None:
        assert {p.pdf_page for p in result.unrecognised_pages} == {10, 12}

    def test_blank_pages_are_not_rendered(self, result) -> None:
        assert all(p.image_path is None for p in result.pages if p.kind is PageKind.BLANK)
        assert all(p.image_path is not None for p in result.pages if p.kind is not PageKind.BLANK)

    def test_the_scan_has_no_text_layer_so_everything_needs_ocr(self, result) -> None:
        assert all(p.text_source == "ocr" for p in result.pages)

    def test_matches_are_confident(self, result) -> None:
        assert all(p.match_score > 0.6 for p in result.sheet_pages)
