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

```bash
cd frontend && npm install && npm run build && cd ..
```

The application needs two derived assets generated from the blank template PDF. The
template is © Games Workshop Ltd and is **not** committed, so supply your own copy:

```bash
python -m scribe40k.tools.build_assets path/to/dark-heresy-blank-template.pdf
```

This preserves any layout fingerprints previously recorded by `record_layout`, so it is
safe to re-run.

### Credentials

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

**Import a scanned sheet** on the front page. The pipeline classifies the pages,
transcribes them, maps six sections in parallel and opens the result in the editor.

The bar at the top says how many fields need review. **Next ›** jumps to each in turn,
scrolls it into view and opens its explanation — the model's confidence, the text it read,
alternative readings you can apply with one click, and a crop of that part of the scan.
**Keep as is** settles it; **Wrong — clear it** empties the field.

Flags that are not about any one field — a page that could not be transcribed, a reading
of a row you have since deleted — are listed under the bar, where they can be dismissed.
Nothing counted can be unreachable.

**N unassigned** opens the tray: text found on the sheet that no field could hold —
marginal notes, values written outside their boxes. Give one a field to go in, send it
**to a note page** if it is prose rather than a value, or dismiss it.

**Note pages** come after the sheet: free text with no boxes, for what players write on
loose paper. Pages of the upload that are not part of the form are transcribed onto them
automatically, and you can add your own.

**Export PDF** prints the sheet, note pages included.

### From the command line

```bash
scribe extract "sheet.pdf" --name "Aldleg"   # read a scan into JSON
scribe list                                   # what is stored
scribe show aldleg                            # what still needs review
scribe export aldleg -o aldleg.pdf            # render it back to paper
scribe serve                                  # run the editor
```

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

**Page order is not assumed.** The calibration scan is a 12-page duplex scan whose sheet
pages are 1, 3, 5, 7 and 9 — in the order 1, 2, 4, 5, 3 — with blank backs between them
and two pages of handwritten session notes at the end. Pages are classified by matching
against known layouts, blanks are dropped, and anything unrecognised is surfaced rather
than discarded.

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

**A flag has two answers.** *Keep as is* accepts the value; *Wrong — clear it* empties the
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
- **Values are fitted to the paper by shrinking, down to 5.5pt.** A gear name longer than
  its printed line gets smaller rather than clipped, which is what a person with a pen
  would do; past the floor it is clipped after all.
- Copyrighted source PDFs are gitignored. Only derived, non-reproducible assets (the
  armour stencil, the page fingerprints) are committed.

---

Sheet layout © Games Workshop Ltd 2010, reproduced under the "permission granted to
photocopy for personal use" notice printed on it. This project is unofficial and
unaffiliated.
