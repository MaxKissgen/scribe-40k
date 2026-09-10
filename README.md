# scribe-40k

Get a **Dark Heresy 1st Edition** character sheet out of a scanned PDF, into structured
JSON, in front of a human to correct, and back onto paper.

1. **Extract** — OCR a sheet PDF and map it onto `dark-heresy-character-sheet.schema.json`
   with a reasoning model. Both models are swappable.
2. **Edit / review** — an HTML sheet that looks like the printed original. Fields the
   extractor was unsure about are highlighted in place; everything else behaves like an
   ordinary form.
3. **Export** — render that same sheet back to PDF at the template's own page size.

See [PLANNING.md](PLANNING.md) for the design and the reasoning behind it.

---

## Quick start

```bash
python -m pip install -e ".[dev,export,anthropic]"
python -m playwright install chromium
```

OR

```bash
uv pip install .
```

THEN

```bash
cd frontend && npm install && npm run build && cd ..
```

The application needs two derived assets generated from the blank template PDF. The
template is © Games Workshop Ltd, so supply your own copy:

```bash
python -m scribe40k.tools.build_assets path/to/dark-heresy-blank-template.pdf
```

This preserves any layout fingerprints previously recorded by `record_layout`, so it is
safe to re-run. Also tries to copy the little mannequin from the armour section

### Credentials (Only if you want to import sheets)

Copy `.env.example` to `.env` and fill in the key for whichever provider you use:

```bash
cp .env.example .env
```

`.env` is read at startup from the repository root. A variable already set in your shell
wins over the file, so `MISTRAL_API_KEY=... scribe extract ...` still overrides it. Keys
are never read from `config.toml`, which is why that file is safe to commit.

### Run it

```bash
scribe serve
```

and open <http://127.0.0.1:8000>. Startup prints the configured models and which keys were
loaded, and says plainly if one is missing. To check the same thing at any time:

```bash
curl -s http://127.0.0.1:8000/api/health
```

`status` is `"ok"` or `"missing_credentials"`, and each stage reports the environment
variable it wants and whether it was found. No key is ever included in the response.

---

## Using it

### From the browser

#### Creating a new character

***Start a blank sheet*** If you dont go off an existing sheet, you can simply use the
scribe to create an own one. Simply enter all values by yourself, everything auto-saves and 
the sheet is then ready for display in the web frontend or for pdf export

#### Character import

**Import a scanned sheet** on the front page. The pipeline classifies the pages and
transcribes them, and then stops and asks: **which page is which?**

That screen shows a thumbnail of every page of your upload, already placed where the
machine thinks it belongs — a sheet page, a note page, or left out. Drag one onto a
different slot to correct it, or use the dropdown on the card. Dropping a page onto an
occupied sheet page swaps the two, so a sheet page can never be claimed twice. **What was
read** shows the opening of the transcription, which is often quicker than squinting at a
thumbnail.

Nothing has been mapped into fields at this point and the reasoning model has not run.
**Looks right — read it** is what starts it; **Discard** deletes the upload. An import you
leave unconfirmed is listed on the front page so you can come back to it.

The step exists because the mistake it catches is not recoverable. A page filed as the
wrong one gets mapped by the wrong prompt into the wrong fields with nothing to show for
it, and a page filed as notes is not read at all — a sheet photographed at an angle came
back as three pages of notes and an empty character. You can see it in a second; the
machine cannot see it at all.

The bar at the top says how many fields need review. **Next ›** jumps to each in turn,
scrolls it into view and opens its explanation — the model's confidence, the text it read,
alternative readings you can apply with one click, and a crop of that part of the scan.
**Keep as is** settles it; **Wrong** empties the field.

Flags that are not about any one field — a page that could not be transcribed, a reading
of a row you have since deleted — are listed under the bar, where they can be dismissed.
Nothing counted can be unreachable.

**Mark all reviewed** accepts everything still open at once, for when you have gone
through the sheet and filled in the last few fields by hand. It asks first, and says how
many are errors, because an error means the sheet does not match the schema there and
accepting leaves it that way. No value is changed either way.

**The save indicator is also the save button.** Editing autosaves a moment after you stop
typing; clicking *Unsaved changes* saves at once instead of waiting, and clicking *Saved*
re-saves the sheet — useful for retrying after a failure, or just for watching it happen
before closing the tab.

**N unassigned** opens the tray: text found on the sheet that no field could hold —
marginal notes, values written outside their boxes. Give one a field to go in, send it
**to a note page** if it is prose rather than a value, or dismiss it.

**Note pages** come after the sheet: free text with no boxes, for what players write on
loose paper. Pages of the upload that are not part of the form are transcribed onto them
automatically, and you can add your own.

#### Export

**Export PDF** prints the sheet, note pages included. Each export also records what its
pages looked like, which is what makes the printout readable again later.

#### Updating a sheet from a printout

Print a character, play with it, cross a talent out and add two gear lines in the margin,
scan it back. **Update** on the character — in the list, or in the editor — reads that
printout and tells you how it differs from your sheet.

It never writes to the sheet. A model reading handwriting is right most of the time, and
the times it is wrong are plausible — "Space" for "Spare parts" — so saving a re-read
document over a character would destroy work with no trace and no way to tell afterwards.
Instead every difference becomes a suggestion, shown where the value lives:

- a **changed** value flags its field, offering the printout's reading beside yours;
- an **added** row appears as a ghost line where it would go, tinted and italic, not in
  the sheet until you accept it;
- a **removed** row flags the row you already have, asking whether it was crossed out or
  simply missed by the reading.

Accepting one is a click; so is leaving it. The crop in each popover is of *that
printout*, since that is the paper somebody wrote on.

Only the pages you uploaded are compared. Scan page 2 alone and page 1 keeps every value
it had, because absent from the upload is not the same as emptied. A page that failed to
read suggests no removals at all, and a list that comes back empty raises one doubt about
the page rather than a removal per row.

A sheet with more gear than the printed lines hold prints as more pages than the form has.
That is handled: the assignment screen lets several pages of the upload cover one sheet
page — "part 1 of 2, read as one page" — and they are read together.

### From the command line

```bash
scribe extract "sheet.pdf" --name "Aldleg"   # read a scan into JSON
scribe list                                   # what is stored
scribe show aldleg                            # what still needs review
scribe export aldleg -o aldleg.pdf            # render it back to paper
scribe serve                                  # run the editor
```

`extract` takes the proposed page assignment without asking — there is nobody to ask on a
command line. Import through the browser when you want to check it first, and to update an
existing character from a printout.

Model choice can be overridden per run:

```bash
scribe extract sheet.pdf --reasoning-provider openrouter --reasoning-model openai/gpt-5
```

---

## Choosing models

Edit `config.toml`:

```toml
[ocr]
provider = "mistral"
model    = "mistral-ocr-latest"

[reasoning]
provider = "anthropic"
model    = "claude-opus-5"
```

| Provider | Role | Notes |
|---|---|---|
| `mistral` | both | `mistral-ocr-latest` is a purpose-built document endpoint returning layout-aware markdown |
| `anthropic` | both | via the official SDK |
| `openai` | both | and `openrouter`, `together`, `groq`, `azure` — one implementation, differing only in base URL |
| `ollama`, `lmstudio`, `vllm` | both | local runtimes; no API key needed |
| `passthrough` | OCR | use the PDF's own text layer, no API call |
| `fixture` | both | replay recorded responses from disk, no API call |

**Prefer a vision-capable reasoning model.** The ticked boxes in the skills grid and the
weapon-training block are most of the data on this sheet, and OCR renders them all as
identical glyphs. A text-only model will under-report them; the extraction report records
which model was used and whether it could see, so a suspiciously empty skills grid is
explainable.

OCR results are cached on disk by page content and model, so iterating on prompts costs
nothing after the first run.

---

## What comes out

One directory per character, plain JSON you can read, diff, and copy elsewhere:

```
data/characters/<id>/
├── character.json   validates against dark-heresy-character-sheet.schema.json
├── report.json      validates against extraction-report.schema.json
├── source.pdf       the uploaded scan
└── pages/           rendered page images, used for the review crops
```

`character.json` is exactly the given schema — this project never modifies it.

`report.json` is the sidecar that makes review possible: which pages were used and what
became of the rest, which fields need a human, why, and what was found on the sheet that
had nowhere to go. It is a separate file because the character schema is
`additionalProperties: false` throughout, so provenance cannot be inlined without breaking
validation.

---

## Notes on behaviour

**Page order is not assumed.** Scans can mix up page order or add new ones (e.g. notes on the back)
. Pages are therefore classified by matching
against known layouts, blanks are dropped, and anything unrecognised is surfaced rather
than discarded.

**A printout of a scribe sheet is matched against itself.** A PDF this program wrote
carries a text layer and identifies itself for nothing — but the useful path is to print
it, write on it and scan it back, and a printer does not print text layers. So every
export records a fingerprint of each of its pages, and a scan of that printout is matched
against the character's own paper rather than the blank form. On a simulated
print-and-scan the recording scores 0.85 where the blank template scores 0.23 and names
the wrong page. It also already knows which sheet page spilled onto two.

The limit is one that cannot be designed away: a recording describes the sheet as it was
printed, so editing a character afterwards makes the recording describe a page that no
longer exists. The last five printings are kept and the best is used; a poor best falls
back to the blank template, and the assignment screen says which reference matched.

**A page gets two chances to be recognised, and a note page is the last resort.** Matching
against known layouts compares page *images*, which assumes a flat rectangle photographed
square-on. A phone photo of a sheet lying on a desk is not that — tilted, keystoned, lit
from one side, with the desk visible around the paper — and every page of one scored below
0.29 against every layout. So a page the image cannot place is transcribed and identified
from the printed words on it instead: "RANGED WEAPONS", "RANK 1 ADVANCES", "MINOR PSYCHIC
POWERS" are printed on exactly one page each and survive a bad photograph intact. Only
what neither attempt can place becomes a note page, and a document where *nothing* was
recognised says so once and loudly rather than quietly becoming a pile of notes. Since
every page is transcribed anyway, the second attempt costs nothing.

**Printed information is never asked of a model.** Every skill's governing characteristic
and Basic flag, the armour hit locations, the 32-row minor-power table: all injected from
`constants.py`. Characteristic bonuses, proficiency modifiers and gear quantities are
computed. The model is only ever asked what the handwriting says.

**The printed Basic square is not a tick.** Mistral OCR renders the solid square printed
beside every Basic skill as a ticked box, and a model reading the transcription then
reports every untouched Basic skill as "Trained". The prompt explains this, and a
deterministic guard checks the model's level against the tick count on that skill's row
of the transcription: where the model claims more marks than the transcription shows, the
level is brought down and the model's reading is kept as a one-click alternative on the
flag.

**A flag has two answers.** *Keep as is* accepts the value; *Wrong* empties the
field (a text field to blank, a checkbox to unticked, a skill to its resting level). Both
take the flag out of the count. An offered alternative reading applies it instead.

**A flag finds a field even when it does not name one.** Extractors report uncertainty at
whatever granularity they read: a whole specialisation, one entry of a list, sometimes the
document. A flag is shown on the control that can answer it — the field itself, else the
nearest one above it, else the first one inside it — and what the user picks is written
there rather than at the flag's own pointer. What still matches nothing is listed under
the review bar rather than left in the count with nowhere to go.

**Flags are recomputed when a sheet is opened.** Everything the rules produce is derived
from the document, so it is regenerated rather than remembered; only a model's own
uncertainty, which cannot be recomputed, is carried forward. Your accepted and dismissed
answers survive both.

**Inconsistencies are flagged, not corrected.** A total that disagrees with base + advances
may be an implant; movement that disagrees with Agility may be a modifier. The sheet keeps
what was written on it and asks you.

**Nothing read from the paper is dropped.** Whatever the mapper cannot place goes to the
assignment tray with its source page, and a page of the upload that is not part of the
form is transcribed onto a note page rather than reported as "(PDF page 10)".

**Sections are expandable.** The printed line counts (21 gear lines, 3 ranged weapons, 18
power boxes) are a starting shape, not a limit. Content past them continues onto a
continuation page when printed.

---

## Development

```bash
python -m pytest              # 255 tests
python -m ruff check backend tests
python -m ruff format backend tests
cd frontend && npm run typecheck
```

Tests that need the calibration scan, built assets, Chromium or a built frontend skip
themselves when those are absent.

After changing anything that moves things around on the printed page, re-record the layout
fingerprints so that exported sheets can still be imported back:

```bash
cd frontend && npm run build && cd ..
python -m scribe40k.tools.record_layout
```

### Recording fixtures

To make the pipeline reproducible offline, run it once against live models with recording
on; `RecordingReasoning` writes each reply to `tests/fixtures/reasoning/`. The `fixture`
provider then replays them with no API calls, which is how the mapping stage is tested.

---

## Known limitations

- **Typography is a near-match.** The sheet uses Columbus MT, a commercial Monotype face
  that cannot be redistributed; EB Garamond and Tinos stand in. Drop a licensed
  `ColumbusMT` into `assets/fonts/` and point `--font-display` at it for an exact match.
- **Checkbox recall depends on a vision-capable reasoning model**, as above.
- **A photographed sheet is recognised, but read less well than a scanned one.** Page
  identification recovers, and the values do come through; the handwriting simply reads
  worse off a photo, so expect more fields flagged as uncertain. A flat scan is still
  worth the trouble.
- **Values are fitted to the paper by shrinking, down to 5.5pt.** A gear name longer than
  its printed line gets smaller rather than clipped, which is what a person with a pen
  would do; past the floor it is clipped after all.
- Copyrighted source PDFs are gitignored. Only derived, non-reproducible assets (the
  armour stencil, the page fingerprints) are committed.

---

Sheet layout © Games Workshop Ltd 2010, reproduced under the "permission granted to
photocopy for personal use" notice printed on it. This project is unofficial and
unaffiliated.
