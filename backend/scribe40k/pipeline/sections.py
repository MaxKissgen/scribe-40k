"""The six mapping jobs, and the prompts that drive them.

The sheet is not extracted in one call. It is split into six jobs, each given a narrow
sub-schema and only the pages it needs. Three reasons:

* **Accuracy.** A prompt that has to describe all 48 skills *and* the weapon boxes *and*
  the rank advances describes each of them worse.
* **Independence.** A job that fails or returns unparseable JSON loses its own section,
  not the sheet. Page 3 being unreadable should not cost you page 1.
* **Cost.** Each job carries only its own pages, so nothing pays to look at page 5 while
  reasoning about skills.

Every job returns the same envelope shape -- ``data`` plus ``uncertain`` plus
``unmapped`` -- rather than raw schema fragments. Asking for a natural nested fragment and
a *separate* list of doubts is markedly more reliable than asking a model to emit a JSON
Pointer per field, and it is exactly what the review UI needs: values to fill in, and a
list of what to highlight.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import constants as K

ENVELOPE_INSTRUCTIONS = """\
Reply with a single JSON object and nothing else. It has exactly three keys:

{
  "data":      <the extracted values, in the shape described below>,
  "uncertain": [ ... ],
  "unmapped":  [ ... ]
}

"data" holds what you read. Use null for any field that is blank on the sheet or that you
cannot read at all. Never invent a plausible value, and never copy an example from these
instructions into your answer.

"uncertain" lists the fields you are not confident about. One entry per doubtful field:

  {
    "pointer": "/bio/career",        JSON Pointer to the field, relative to the whole sheet
    "reason": "faint pencil",        why you are unsure, in a few words
    "confidence": 0.4,               0 to 1
    "alternatives": ["Adept"],       other readings you considered, best first
    "snippet": "Career_ Adep..."     the surrounding text as you read it
  }

Be honest here. A field you flag costs the user five seconds to confirm; a wrong value you
report confidently may never be noticed. Flag anything faint, ambiguous, overwritten, or
written outside its box.

"unmapped" lists text you can read on the page but cannot place in any field:

  {
    "text": "+30 Deceive",
    "location": "left margin beside Demolition",
    "reason": "annotation with no matching field"
  }

Marginal notes, arrows, values written between boxes, and totals scribbled in white space
all belong here. Do not force them into a field they do not fit, and do not drop them.
"""

SHEET_CONTEXT = """\
You are reading a Warhammer 40,000 Dark Heresy (1st edition) character sheet. It was
filled in by hand and then scanned, so expect faint pencil, cursive, corrections, and
values written slightly outside their boxes.

Two habits matter more than anything else:

1. Distinguish a *printed* label from *handwriting*. The blank form already contains every
   field name, every skill, and the whole minor-powers table. Only handwriting is data. If
   a field shows nothing but its printed label and an empty line, the answer is null.

2. Read tick boxes carefully. A tick, cross, filled square or scribble all count as marked;
   a printed empty square does not. On this sheet the ticks carry most of the information.
"""


@dataclass(frozen=True)
class Section:
    """One mapping job."""

    name: str
    #: Which printed sheet pages this job needs.
    sheet_pages: tuple[int, ...]
    #: Human-readable summary, used in CLI progress output.
    title: str
    #: The job-specific half of the prompt: what to extract and in what shape.
    instructions: str
    #: Root keys of the character document this job may write.
    owns: tuple[str, ...] = field(default=())
    #: Replies here run long; the advances page alone has 96 rows.
    max_tokens: int = 8192

    def system_prompt(self) -> str:
        return f"{SHEET_CONTEXT}\n{ENVELOPE_INSTRUCTIONS}"

    def user_prompt(self, ocr_text: str, *, has_images: bool, continued: bool = False) -> str:
        pages = ", ".join(str(p) for p in self.sheet_pages)
        vision_note = (
            "The page image is attached. Trust the image over the transcription wherever "
            "they disagree -- especially for tick boxes, which a transcription cannot "
            "represent reliably."
            if has_images
            else "No page image is available, so you are working from the transcription "
            "alone. Tick boxes are unreliable in text form: mark a field uncertain rather "
            "than guessing which boxes were filled."
        )
        # A sheet page can arrive as several pages. Left unexplained, a model handed the
        # gear list in two slabs reports the same entries twice, or treats the second slab
        # as a different character's.
        continuation_note = (
            "\nONE OF THESE SHEET PAGES ARRIVED AS SEVERAL PAGES. They are marked "
            '"part N of M" below, and there is an image for each. A part is the *same* '
            "page continued -- a list too long for the printed lines running on -- not a "
            "second sheet and not a second character. Read them as one page: an entry that "
            "starts on one part and finishes on the next is one entry, a heading repeated "
            "at the top of a part is the same heading, and nothing should be reported "
            "twice. Where the parts disagree, later wins: it is the continuation.\n"
            if continued
            else ""
        )
        return (
            f"Extract the {self.title} from sheet page(s) {pages}.\n\n"
            f"{vision_note}\n"
            f"{continuation_note}\n"
            f"{self.instructions}\n\n"
            f"--- OCR TRANSCRIPTION (sheet page {pages}) ---\n"
            f"{ocr_text or '(the transcription is empty; work from the image)'}\n"
            f"--- END TRANSCRIPTION ---\n"
        )


def _basic_skill_list() -> str:
    """The skills whose first box is printed solid, so the model can subtract it."""
    names = [spec.printed_label for spec in K.SKILLS if spec.is_basic]
    return "  " + ", ".join(names)


def _skill_reference() -> str:
    """The printed skill list, so the model matches names instead of inventing keys."""
    lines = []
    for spec in K.SKILLS:
        kind = (
            "group skill (dagger)" if spec.is_group else ("Basic" if spec.is_basic else "Advanced")
        )
        lines.append(f"  {spec.key:<16} {spec.printed_label:<24} {spec.characteristic:<4} {kind}")
    return "\n".join(lines)


BIO_AND_CHARACTERISTICS = Section(
    name="bio_characteristics",
    sheet_pages=(1,),
    title="biography and characteristics",
    owns=("bio", "characteristics"),
    max_tokens=4096,
    instructions="""\
Return "data" shaped like this:

{
  "bio": {
    "characterName": null, "playerName": null, "career": null, "rank": null,
    "homeWorld": null, "quirk": null, "divination": null, "ordoOrFaction": null,
    "description": null
  },
  "characteristics": {
    "weaponSkill": {"base": null, "advancesTaken": null, "total": null},
    ... the same for ballisticSkill, strength, toughness, agility,
        intelligence, perception, willpower, fellowship
  }
}

The biography fields run across the top of the page, each on a printed line. "rank" is
free text -- players write both a number and a title. "description" spans three printed
lines; join them with newlines.

Each characteristic is a circle with a two-digit number written in it, and a row of four
small boxes labelled "Characteristic Advances" underneath.

  * "total" is the number written *in the circle*. This is the value that matters.
  * "advancesTaken" is how many of the four boxes below the circle are ticked, 0 to 4.
    Not the number inside them -- the count of marked ones.
  * "base" is the starting value before advances, only if it is written separately.
    Usually it is not: leave it null rather than back-calculating it.

Do not compute anything. Do not derive base from total, or total from base. Report only
what is written. If a circle is empty, its total is null.

Omit "bonus" entirely -- it is computed from the total afterwards.\
""",
)


SKILLS = Section(
    name="skills",
    sheet_pages=(1,),
    title="skills grid",
    owns=("skills",),
    max_tokens=16000,
    instructions=f"""\
The skills grid runs in three columns across the middle of page 1. Each row is a skill
name followed by four boxes, in this order:

    Basic | Trained | +10% | +20%

THE ONE THING TO GET RIGHT: the first box is not a tick box.

For every skill marked "Basic" in the list below, the paper is printed with a solid black
square in that first position. It is part of the form. The player did not mark it, and it
does not mean "Trained".

The transcription cannot tell the difference: it renders that printed square as a ticked
box, exactly like a real tick. So a Basic skill with *nothing* marked by the player shows
up in the text as one ticked box followed by three empty ones. That is the untrained
state, and such a skill must be omitted from your answer.

In the image the two look nothing alike. The printed square is a small, perfectly even,
machine-printed black block. A player's mark is a pen stroke -- a tick, a cross, a
scribble -- inside an outlined box. Use the image to decide, and count only the player's
own marks in the second, third and fourth boxes:

    one player mark   -> "Trained"
    two               -> "+10"
    three             -> "+20"

The rule in one line: for a Basic skill, subtract the printed square before counting.

Basic skills (printed square present):
{_basic_skill_list()}

Return "data" as a single "skills" object keyed by the identifiers below. Include only
skills the character has marked; omit the rest entirely.

  {{
    "skills": {{
      "dodge": {{"proficiency": {{"level": "Trained"}}}},
      "commonLore": {{"specialisations": [
        {{"subject": "Imperium", "proficiency": {{"level": "Trained"}}}},
        {{"subject": "Tech",     "proficiency": {{"level": "+10"}}}}
      ]}},
      "additionalSkills": [
        {{"name": "Interrogation (WP)", "characteristic": "WP",
         "proficiency": {{"level": "Trained"}}}}
      ]
    }}
  }}

Three shapes appear there:

* An ordinary skill carries one "proficiency". "level" is "Trained", "+10" or "+20":
  whichever is the rightmost box the *player* marked.

* A group skill (a dagger on the sheet) never has a proficiency of its own. The player
  writes a subject on a blank line beneath it and marks boxes on that line; each such
  line is one "specialisation". Every specialisation must carry a "proficiency". A line
  with a subject written but no box marked still counts -- give it "Trained" and list it
  under "uncertain".

* A skill written onto a blank line that is not a specialisation of the group skill above
  it goes in "additionalSkills". Note that "additionalSkills" sits *inside* "skills", as a
  sibling of "dodge" -- not at the top level of "data".

The printed skills, with their identifiers, governing characteristics and kind:

{_skill_reference()}

Note two quirks of the printed sheet: "Scholasic Lore" is a typo for Scholastic Lore, and
"Evaluate" is printed without its "(Int)". Use the identifiers above regardless.

Omit "characteristic", "isBasicSkill" and "modifier" -- all three are printed constants
filled in afterwards.\
""",
)


COMBAT_STATE = Section(
    name="combat_state",
    sheet_pages=(1,),
    title="wounds, fate, armour, insanity, corruption and movement",
    owns=("wounds", "fatePoints", "armour", "insanity", "corruption", "movement"),
    max_tokens=4096,
    instructions="""\
These blocks sit along the bottom of page 1. Return "data" shaped like this:

{
  "wounds": {"totalWounds": null, "currentWounds": null,
             "criticalDamage": null, "fatigue": null},
  "fatePoints": {"totalFatePoints": null, "currentFatePoints": null},
  "armour": {
    "head":     {"type": null, "armourPoints": null},
    "rightArm": {...}, "leftArm": {...}, "body": {...},
    "rightLeg": {...}, "leftLeg": {...}
  },
  "insanity":   {"currentPoints": null, "degreeOfMadness": null, "disorders": []},
  "corruption": {"currentPoints": null, "degreeOfCorruption": null, "malignancies": []},
  "movement":   {"halfAction": null, "fullAction": null, "charge": null, "run": null}
}

The armour diagram is a body silhouette with six labelled boxes. Each prints "Type:" and
its hit-location range. Players usually write a single number -- that is the Armour Points
for that location, so put it in "armourPoints" and leave "type" null. Where they name the
armour as well ("Flak Coat 4"), split it: "type": "Flak Coat", "armourPoints": 4.

"criticalDamage" is free text -- players record the effect, not a number.

Movement is four numbers in metres. Report what is written, even if it looks wrong; the
values are cross-checked against Agility afterwards and any disagreement is flagged.

Omit "location", "hitRoll", "hitRollMin" and "hitRollMax" -- they are printed constants.\
""",
)


PAGE_TWO = Section(
    name="equipment",
    sheet_pages=(2,),
    title="weapons, talents, gear and weapon training",
    owns=("weapons", "talentsAndTraits", "gear", "weaponTraining"),
    max_tokens=16000,
    instructions=f"""\
Page 2 holds four blocks. Return "data" shaped like this:

{{
  "weapons": {{
    "ranged": [{{"name": null, "class": null, "damage": null, "damageType": null,
                "penetration": null, "range": null, "rateOfFire": null,
                "clip": null, "reload": null, "specialRules": []}}],
    "melee":  [{{"name": null, "class": null, "damage": null, "damageType": null,
                "penetration": null, "specialRules": []}}]
  }},
  "talentsAndTraits": {{
    "homeworldBackground": [{{"name": "...", "specialisation": null}}],
    "advancesTalentsAndTraits": [{{"name": "...", "specialisation": null}}]
  }},
  "gear": [{{"name": "...", "quantity": null}}],
  "weaponTraining": {{
    "basic":  {{"bolt": false, "flame": false, "las": false, "launcher": false,
               "melta": false, "plasma": false, "primitive": false, "sp": false}},
    "pistol": {{... the same eight keys ...}},
    "melee":  {{"primitive": false, "chain": false, "shock": false, "power": false}},
    "exotic": [{{"weapon": "..."}}]
  }}
}}

The sheet prints {K.PRINTED_CAPACITY["weapons.ranged"]} ranged and
{K.PRINTED_CAPACITY["weapons.melee"]} melee weapon boxes, and
{K.PRINTED_CAPACITY["gear"]} gear rows. Include only boxes and rows that have something
written in them; skip the empty ones rather than emitting nulls for them.

WEAPONS. Three fields are numbers and the rest are text. "range", "penetration" and
"clip" are plain integers: a sheet reading "60m" gives 60, and one reading "-" or "N/A"
gives null. Everything else is a string, even when only a number is written.

"damage" is the dice expression as written -- a number of dice, a die size and any bonus,
e.g. "1d10+3" or "2d10+2". If the transcription lost the "d" ("7 to 8", "7d/0", "1010+3"),
read it from the image; a damage field that is not of that shape is almost always a
misread. Keep the damage type out of it: "1d10+3 E" is "damage": "1d10+3" with
"damageType": "E". "damageType" is a single letter: E energy, I impact, R rending,
X explosive. "rateOfFire" stays text because of notation like "S/3/10", and it is a
separate field from "damage" -- if you find yourself putting "S/2/4" in "damage", it
belongs in "rateOfFire". "specialRules" is the comma-separated text split into separate
strings.
Players often add a count or a weight after a weapon's name -- "(x5)", "(wt 0.5)". Those
are not part of the name: give the clean name, and put each such annotation in
"unmapped" with the weapon it belongs to.

TALENTS AND GEAR share one layout, and it is the part of this page most often read
wrongly. Each printed row is a pair of write-in lines side by side. The pair is ONE
entry, not two: the left line is the name, and the right line -- when the player used it
-- is a note about that same entry, such as an effect ("+10 BS"), a purpose
("(crafting)"), a weight ("(wt 3)") or a count. Put the right-hand text in that entry's
"notes". Never report it as a separate entry, and never as unmapped.

  "gear": [{{"name": "Red-dot laser sight", "quantity": null, "notes": "+10 BS"}}]
  "advancesTalentsAndTraits": [{{"name": "Electro-graft", "specialisation": null,
                                "notes": "+10 Tech-Use"}}]

The transcription of these two blocks is unreliable on a handwritten sheet: it may repeat
one entry many times, invent entries, or drop the right-hand column altogether. Read the
rows from the image and use the transcription only as a hint. Each row of the image is
one entry; do not emit more entries than there are written rows.

Every gear and talent entry MUST have a "name". If a row's only legible text is on the
right-hand line, that text is the name. Never emit an entry whose name is null.

For talents, a parenthesised qualifier on the name goes in "specialisation", without the
brackets: "Weapon Training (Las)" becomes name "Weapon Training", specialisation "Las".

For gear, keep the item name as written. "quantity" is an integer or null, nothing else:
a count written as "(3)" or "x3" beside the name is the integer 3; if there is no clear
count, leave it null and keep the text in "notes". Never put a bracketed string, a range
or a unit into "quantity".

The Weapon Training block at the foot of the page is a grid of tick boxes in three
columns. Report true only where the box is genuinely marked. The four "Exotic Weapon
Training (____)" lines take a written-in weapon name; list only ones that are both ticked
and filled in.\
""",
)


ADVANCES = Section(
    name="advances",
    sheet_pages=(3,),
    title="rank advances and experience",
    owns=("advances",),
    max_tokens=16000,
    instructions=f"""\
Page 3 is {K.PRINTED_RANK_BLOCKS} blocks headed "RANK 1 ADVANCES" through
"RANK {K.PRINTED_RANK_BLOCKS} ADVANCES", plus an "ELITE ADVANCES" block. Each block is a
two-column table of ADVANCE and COST, {K.PRINTED_CAPACITY["advances.rankAdvances.entries"]}
rows in all. Read left column then right column.

Return "data" shaped like this:

{{
  "advances": {{
    "rankAdvances": [
      {{"rank": 1, "entries": [{{"advance": "Dodge", "cost": 100}}]}}
    ],
    "eliteAdvances": [{{"advance": null, "cost": null}}],
    "totalExperience": null,
    "spentExperience": null
  }}
}}

Include only rank blocks that have something written in them -- most sheets use the first
two or three. Include only filled rows within a block. A row with an advance but no cost
is fine: report the advance and leave cost null.

"TOTAL EXPERIENCE" and "SPENT EXPERIENCE" are printed at the top of the page.\
""",
)


PSYCHIC = Section(
    name="psychic",
    sheet_pages=(4, 5),
    title="psychic powers",
    owns=("psychic",),
    max_tokens=16000,
    instructions=f"""\
Pages 4 and 5 cover psychic powers. Return "data" shaped like this:

{{
  "psychic": {{
    "psyRating": null,
    "psychicDiscipline": null,
    "minorPowers": [{{"name": "Dull Pain", "isCustom": false}}],
    "powers": [{{"name": null, "threshold": null, "focusTime": null,
                "sustained": null, "range": null, "description": null}}]
  }}
}}

**Most characters are not psykers and these pages are blank.** If so, return psyRating and
psychicDiscipline as null and both arrays empty. Do not manufacture entries.

The "Minor Psychic Powers" table on page 4 has {len(K.MINOR_POWERS)} pre-printed rows, each
with a tick box on the left. List *only the ticked rows*, by their printed name, with
"isCustom": false. Their threshold, focus and sustain values are printed on the form and
are filled in afterwards -- do not copy them. The eight blank lines below the table take
write-in powers: those get "isCustom": true and whatever values are written.

The "POWER" boxes -- six on page 4, twelve on page 5 -- are free-form. Include only boxes
with something written in them. Two of their fields are numbers: "threshold" and "range",
both plain integers ("30m" gives 30). A range written as a formula rather than a distance
-- "10 x PR metres" is the common one -- has no integer to record: leave "range" null and
put the formula in "description".

One warning from a real sheet: players sometimes reuse these boxes for something else
entirely, such as a list of implants, and write their own heading above the block. If the
boxes clearly hold something other than psychic powers, put each one in "unmapped" with
the heading you saw, rather than filing them as powers.\
""",
)


#: Every job, in the order they are reported. They are independent and may run in any
#: order; this is just what reads sensibly in a progress log.
ALL_SECTIONS: tuple[Section, ...] = (
    BIO_AND_CHARACTERISTICS,
    SKILLS,
    COMBAT_STATE,
    PAGE_TWO,
    ADVANCES,
    PSYCHIC,
)

SECTION_BY_NAME: dict[str, Section] = {s.name: s for s in ALL_SECTIONS}
