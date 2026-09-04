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

    def user_prompt(self, ocr_text: str, *, has_images: bool) -> str:
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
        return (
            f"Extract the {self.title} from sheet page(s) {pages}.\n\n"
            f"{vision_note}\n\n"
            f"{self.instructions}\n\n"
            f"--- OCR TRANSCRIPTION (sheet page {pages}) ---\n"
            f"{ocr_text or '(the transcription is empty; work from the image)'}\n"
            f"--- END TRANSCRIPTION ---\n"
        )


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
  * "advancesTaken" is how many of the four boxes are ticked, 0 to 4. Not the number
    inside them -- the count of marked ones.
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
name followed by four tick boxes, in this order:

    Basic | Trained | +10% | +20%

The first box is printed *filled* for Basic Skills and is not a player mark -- ignore it.
Only the player's own ticks in Trained, +10% and +20% matter.

Return "data" as a "skills" object keyed by the identifiers below. Include only skills the
character actually has; omit untouched ones entirely.

For an ordinary skill:

  "dodge": {{"proficiency": {{"level": "Trained"}}}}

where "level" is one of "Trained", "+10" or "+20" -- whichever is the *rightmost* ticked
box. If only the printed Basic square is filled and the player ticked nothing, omit the
skill.

For a group skill (marked with a dagger on the sheet), the player writes a subject on a
blank line and ticks boxes on that line:

  "commonLore": {{"specialisations": [
      {{"subject": "Imperium", "proficiency": {{"level": "Trained"}}}},
      {{"subject": "Tech", "proficiency": {{"level": "+10"}}}}
  ]}}

A group skill never has a proficiency of its own -- only specialisations. A written line
with no ticks still counts: report it at level "Trained" and flag it as uncertain.

Any skill written onto a blank line that is not a specialisation of a printed group skill
goes into "additionalSkills":

  "additionalSkills": [{{"name": "Interrogation (WP)", "characteristic": "WP",
                        "proficiency": {{"level": "Trained"}}}}]

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
{K.PRINTED_CAPACITY["gear"]} gear lines. Include only boxes and lines that have something
written in them; skip the empty ones rather than emitting nulls for them.

"damageType" is a single letter: E energy, I impact, R rending, X explosive.
"rateOfFire" stays as text, because of notation like "S/3/10".
"specialRules" is the comma-separated text split into separate strings.

For talents, put a parenthesised qualifier in "specialisation": "Weapon Training (Las)"
becomes name "Weapon Training", specialisation "Las".

For gear, keep the item name as written; a leading count like "3 x frag grenade" is split
out afterwards, so you may leave "quantity" null.

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
with something written in them.

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
