"""Identifying a sheet page from its transcription when its image cannot be matched.

The case that produced this: a sheet photographed on a desk rather than scanned. Every
page was tilted, keystoned and unevenly lit, with the desk visible around the paper, so
each one correlated below 0.29 against every template page -- and the whole character
became three note pages with nothing in the sheet at all.
"""

from __future__ import annotations

import json

import pytest

from scribe40k import constants as K
from scribe40k.llm.base import OcrPage, PageImage, ReasoningResponse
from scribe40k.paths import PAGE_FINGERPRINTS
from scribe40k.pipeline.ingest import PageKind, load_page_text_signatures
from scribe40k.pipeline.page_text import build_signatures, identify_pages, score

# --------------------------------------------------------------------------------------
# The scoring itself, against a miniature template
# --------------------------------------------------------------------------------------

#: Three pages standing in for the real five. Page A has a vocabulary of its own; pages B
#: and C share theirs, and C's is a subset of B's -- which is the relationship the real
#: pages 4 and 5 are in, and the one that is easy to get wrong.
MINI = {
    1: "armour wounds fatigue insanity corruption movement",
    2: "psychic powers rating threshold focus sustain discipline "
    "minor call creatures chameleon float lucky wither",
    3: "psychic powers rating threshold focus sustain discipline",
}


@pytest.fixture(scope="module")
def mini():
    return build_signatures(MINI)


class TestSignatures:
    def test_a_word_on_every_page_carries_no_information(self) -> None:
        signatures = build_signatures({1: "shared alpha", 2: "shared beta", 3: "shared gamma"})

        assert "shared" not in signatures[1]
        assert "alpha" in signatures[1]

    def test_a_word_unique_to_one_page_outweighs_a_shared_one(self, mini) -> None:
        assert mini[2]["minor"] > mini[2]["psychic"]

    def test_recall_is_measured_against_the_page_not_the_transcription(self, mini) -> None:
        """A transcription full of other things still matches, as long as it has the words."""
        recall, _ = score(MINI[1] + " noise" * 200, mini[1])

        assert recall == 1.0


class TestIdentifyPages:
    def test_a_page_is_recognised_from_its_printed_words(self, mini) -> None:
        assert identify_pages({7: MINI[1]}, mini) == {7: (1, 1.0)}

    def test_a_page_whose_vocabulary_is_a_subset_still_wins_its_own(self, mini) -> None:
        """Page 3's words are all on page 2 as well, so only page 3 has nothing missing."""
        assert identify_pages({1: MINI[3]}, mini)[1][0] == 3

    def test_the_superset_page_is_not_lost_to_the_subset(self, mini) -> None:
        """Both score full recall on page 3, so the weight of evidence has to decide."""
        assert identify_pages({1: MINI[2]}, mini)[1][0] == 2

    def test_the_superset_page_survives_a_partial_transcription(self, mini) -> None:
        """The reason weight ranks and recall only gates: page 2 read badly enough to lose
        half its own vocabulary still has more evidence behind it than page 3 has."""
        partial = "psychic powers rating threshold focus sustain discipline minor call"

        assert identify_pages({1: partial}, mini)[1][0] == 2

    def test_unrelated_text_claims_nothing(self, mini) -> None:
        notes = "Rescued the astropath from the hab-block. Owed 200 thrones to Vex."

        assert identify_pages({1: notes}, mini) == {}

    def test_a_handful_of_coincidental_words_is_not_enough(self, mini) -> None:
        """Page 3's signature is thin -- three shared words -- so it must not be claimable
        by a note page that happens to mention psychic powers."""
        assert identify_pages({1: "psychic powers"}, mini) == {}

    def test_only_one_page_may_claim_a_sheet_page(self, mini) -> None:
        """A sheet has one of each page, so the first pass hands each out once."""
        identified = identify_pages({1: MINI[1], 7: MINI[1]}, mini)

        assert len({sheet for sheet, _ in identified.values()}) == 1

    def test_a_page_already_placed_is_not_reclaimed(self, mini) -> None:
        assert 1 not in identify_pages({1: MINI[1]}, mini, already_placed={1: 1})


class TestContinuations:
    """A sheet page that outgrew its printed lines occupies two pages of the upload.

    On a real overflowing export the spill page scored 0.52 against sheet page 2 -- ample
    -- and became a note page anyway, because one-to-one claiming had already given page 2
    to the half that came first.
    """

    #: What the rest of a spilled page looks like: the rows that did not fit, and none of
    #: the headings. Below the threshold to claim a page outright -- which is the whole
    #: difficulty, since it is still unmistakably part of that page.
    SPILL = "creatures chameleon float"

    def test_the_rest_of_a_page_joins_it(self, mini) -> None:
        identified = identify_pages({4: MINI[2], 5: self.SPILL}, mini)

        assert identified[4][0] == 2
        assert identified[5][0] == 2, "the spill belongs to the page it spilled from"

    def test_it_can_join_a_page_the_image_matcher_placed(self, mini) -> None:
        """The usual shape: the first half is recognisable enough for the fingerprint and
        the second half is not."""
        identified = identify_pages({5: self.SPILL}, mini, already_placed={4: 2})

        assert identified == {5: (2, pytest.approx(0.29, abs=0.05))}

    def test_it_must_directly_follow_the_page_it_continues(self, mini) -> None:
        """Spill is a property of printing: the overflow of a page is the next page. A
        page further along is something else, whatever words are on it."""
        identified = identify_pages({9: self.SPILL}, mini, already_placed={4: 2, 5: 1})

        assert identified == {}

    def test_nothing_starts_a_claim_this_way(self, mini) -> None:
        """A continuation only makes sense as the continuation of something."""
        assert identify_pages({1: self.SPILL}, mini) == {}

    def test_unrelated_text_does_not_attach_to_its_neighbour(self, mini) -> None:
        notes = "Owed 200 thrones to Vex; do not go back to the Fifth Quadrant."

        assert identify_pages({5: notes}, mini, already_placed={4: 2}) == {}


# --------------------------------------------------------------------------------------
# The signatures actually shipped
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def signatures():
    if not PAGE_FINGERPRINTS.exists():
        pytest.skip("page fingerprints unavailable")
    shipped = load_page_text_signatures()
    if not shipped:
        pytest.skip("assets predate text signatures; rebuild with build_assets")
    return shipped


#: Printed headings only -- no handwriting -- as OCR reads them off each page. Every word
#: here is already in the committed `boilerplateTokens`. Page 1 is assembled from the
#: skill and armour tables in `constants`, because that is most of what is printed on it
#: and a hand-picked excerpt would be testing the excerpt rather than the page.
PRINTED = {
    1: "CHARACTER NAME PLAYER NAME CAREER RANK HOME WORLD QUIRK DIVINATION ORDO "
    "DESCRIPTION SKILLS ARMOUR WOUNDS TOTAL WOUNDS CURRENT WOUNDS CRITICAL DAMAGE "
    "FATIGUE FATE POINTS INSANITY DEGREE OF MADNESS DISORDERS CORRUPTION "
    "MALIGNANCIES MOVEMENT Half Action Charge Run "
    + " ".join(s.printed_label for s in K.SKILLS)
    + " "
    + " ".join(a.location for a in K.ARMOUR_LOCATIONS),
    2: "RANGED WEAPONS NAME CLASS DAMAGE TYPE PEN RANGE ROF CLIP RLD SPECIAL RULES "
    "MELEE WEAPONS TALENTS AND TRAITS HOMEWORLD BACKGROUND GEAR WEAPON TRAINING TALENTS",
    3: "RANK 1 ADVANCES ADVANCE COST RANK 2 ADVANCES ELITE ADVANCES TOTAL EXPERIENCE "
    "SPENT EXPERIENCE",
    4: "PSYCHIC POWERS Psy Rating Psychic Discipline MINOR PSYCHIC POWERS Threshold "
    "Focus Sustain Call Creatures Call Item Chameleon Distort Vision Dull Pain "
    "Fearful Aura Flash Bang Float Forget Me Healer Inflict Pain Knack Lucky "
    "Precognition Spasm Torch Trick Wall Walk Warp Howl Wither",
    5: "PSYCHIC POWERS Psy Rating Psychic Discipline Power Threshold Focus Time "
    "Sustained Range Description Power Threshold Focus Time Sustained Range Description",
}


class TestTheShippedSignatures:
    @pytest.mark.parametrize("sheet_page", [1, 2, 3, 4, 5])
    def test_each_page_is_identified_from_its_printed_headings(
        self, signatures, sheet_page
    ) -> None:
        identified = identify_pages({1: PRINTED[sheet_page]}, signatures)

        assert identified and identified[1][0] == sheet_page

    def test_the_two_psychic_pages_are_told_apart(self, signatures) -> None:
        """They share nearly all their furniture; only the minor-powers table separates
        them, and the image matcher finds this pair hard for the same reason."""
        identified = identify_pages({4: PRINTED[4], 5: PRINTED[5]}, signatures)

        assert identified[4][0] == 4
        assert identified[5][0] == 5

    def test_session_notes_are_not_mistaken_for_a_sheet_page(self, signatures) -> None:
        notes = (
            "Session notes. Rescued the astropath from the hab-block on Sepheris "
            "Secundus. Owed 200 thrones to Vex; do not go back to the Fifth Quadrant. "
            "Contact: Magos Herrick, Lathe Worlds. The seal is NOT Ecclesiarchy issue."
        )

        assert identify_pages({6: notes}, signatures) == {}

    def test_the_signatures_are_in_the_committed_asset(self) -> None:
        """They have to survive without the copyrighted template being present."""
        data = json.loads(PAGE_FINGERPRINTS.read_text(encoding="utf-8"))

        assert set(data["textSignatures"]) == {"1", "2", "3", "4", "5"}


# --------------------------------------------------------------------------------------
# End to end: a page the fingerprint cannot place reaches the mapper anyway
# --------------------------------------------------------------------------------------


def _noise_pdf(path, pages: int = 1):
    """A PDF whose pages have ink but resemble no template page.

    Stands in for a photograph: the point is not that it looks like a photo, but that the
    image matcher fails on it exactly as it fails on one.
    """
    import pymupdf

    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=604, height=782)
        for row in range(20):
            for col in range(14):
                if (row * 7 + col * 3 + index) % 3:
                    page.draw_rect(
                        pymupdf.Rect(20 + col * 40, 20 + row * 36, 52 + col * 40, 48 + row * 36),
                        fill=(0, 0, 0),
                    )
    doc.save(path)
    doc.close()


class StubOcr:
    """Returns one canned transcription for every page it is given."""

    name = "stub"
    model = "stub"

    def __init__(self, text: str) -> None:
        self.text = text
        self.seen: list[PageImage] = []

    def transcribe(self, pages):
        self.seen.extend(pages)
        return [
            OcrPage(pdf_page=p.pdf_page, sheet_page=p.sheet_page, text=self.text, provider="stub")
            for p in pages
        ]


class StubReasoning:
    name = model = "stub"
    supports_vision = supports_structured_output = True

    def complete(self, request):
        envelope = {"data": {}}
        return ReasoningResponse(text=json.dumps(envelope), parsed=envelope)


@pytest.fixture(scope="module")
def photographed(tmp_path_factory):
    """Run the pipeline over a page the fingerprint cannot place but OCR can read."""
    if not PAGE_FINGERPRINTS.exists() or not load_page_text_signatures():
        pytest.skip("page fingerprints unavailable or predate text signatures")

    from scribe40k.pipeline.run import extract

    directory = tmp_path_factory.mktemp("photo")
    source = directory / "photo.pdf"
    _noise_pdf(source)

    return extract(
        source,
        StubOcr(PRINTED[2]),
        StubReasoning(),
        image_dir=directory / "pages",
    )


class TestAPhotographedPageReachesTheMapper:
    def test_the_page_is_classified_as_a_sheet_page(self, photographed) -> None:
        [record] = photographed.report.pages

        assert record.kind == "sheet"
        assert record.sheetPage == 2

    def test_the_report_says_the_transcription_placed_it(self, photographed) -> None:
        """Not the image: the difference matters when the user is deciding whether to
        re-scan."""
        [record] = photographed.report.pages

        assert record.matchedBy == "text"
        assert record.matchScore < 0.55, "the image match must genuinely have failed"
        assert record.textScore is not None and record.textScore > 0.5

    def test_it_does_not_become_a_note_page(self, photographed) -> None:
        assert photographed.character["notePages"] == []

    def test_the_section_that_owns_that_page_ran(self, photographed) -> None:
        """Being classified is not the point; being mapped is."""
        equipment = next(s for s in photographed.report.sections if s.name == "equipment")

        assert equipment.status == "ok"
        assert equipment.sheetPages == [2]


class TestWhenNothingCanBePlaced:
    """The failure this whole path exists to make visible."""

    @pytest.fixture(scope="class")
    @classmethod
    def unreadable(cls, tmp_path_factory):
        if not PAGE_FINGERPRINTS.exists():
            pytest.skip("page fingerprints unavailable")
        from scribe40k.pipeline.run import extract

        directory = tmp_path_factory.mktemp("unreadable")
        source = directory / "photo.pdf"
        _noise_pdf(source, pages=2)

        return extract(
            source,
            StubOcr("nothing here resembles a character sheet at all"),
            StubReasoning(),
            image_dir=directory / "pages",
        )

    def test_it_is_reported_once_and_loudly(self, unreadable) -> None:
        errors = [f for f in unreadable.report.flags if f.rule == "ingest.nothing_recognised"]

        assert len(errors) == 1
        assert errors[0].severity == "error"

    def test_the_pages_still_become_note_pages(self, unreadable) -> None:
        """Last resort, not first: nothing read from the paper is thrown away."""
        assert len(unreadable.character["notePages"]) == 2

    def test_every_missing_sheet_page_is_named(self, unreadable) -> None:
        """All five, not just the first: they share a pointer and a rule, and used to
        collapse into one another."""
        missing = [f for f in unreadable.report.flags if f.rule == "ingest.missing_sheet_page"]

        assert len(missing) == 5


def test_a_page_recovered_by_text_is_not_also_left_unrecognised(photographed) -> None:
    kinds = {p.kind for p in photographed.report.pages}

    assert PageKind.UNRECOGNISED.value not in kinds
