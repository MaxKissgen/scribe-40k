# scribe-40k — Planning

Toolchain for getting a **Dark Heresy 1st Edition** character sheet out of a scanned
PDF, into structured JSON, in front of a human for correction, and back onto paper.

Three components, one data model:

1. **Extract** — OCR a sheet PDF and map it onto `dark-heresy-character-sheet.schema.json`
   with a reasoning LLM. Both models are swappable.
2. **Edit / Review** — an HTML sheet that looks like the printed original, where you fix
   what the extractor got wrong. The review step *is* the editor.
3. **Export** — render that same HTML back to PDF.

---

## 1. Source material

`dark-heresy-character-sheet.schema.json` (given, **not modified by this project**) models the
official 5-page sheet, © Games Workshop Ltd 2010.

### What the blank template actually is

Probed with PyMuPDF:

| Property | Value |
|---|---|
| Pages | 5 |
| Page size | 603.78 × 782.36 pt = **213 × 276 mm** (non-standard, neither A4 nor Letter) |
| AcroForm | **None** — `is_form_pdf = False`, zero widgets |
| Checkboxes | Literal `o` / `n` glyphs in *ColumbusMT*, not form fields |
| Fonts | `ColumbusMT`, `ColumbusMT-Bold`, `ColumbusMT-Bold-SC700`, `Times-Roman`, `Wingdings` |
| Images | One: a 2519 × 3210 1-bit armour silhouette stencil on page 1 |

Consequences that shaped the design:

- There are no form fields to read or write, so extraction is genuinely an OCR problem and
  export cannot be a form-fill.
- `ColumbusMT` is a commercial Monotype face and cannot be redistributed. The frontend
  substitutes **EB Garamond** (small caps) + **Tinos** (body). Dropping a licensed
  `ColumbusMT` into `assets/fonts/` makes the output typographically identical.
- The armour silhouette is extracted losslessly from the template and reused, so that one
  piece of art is pixel-exact rather than traced.

### Sheet layout to schema

| Sheet page | Blocks | Schema targets |
|---|---|---|
| 1 | Bio; 9 characteristic circles + 4 advance ticks each; 3-column skills grid (48 printed skills, 10 dagger group-skills with write-in lines); Wounds; Fate; Armour diagram (6 locations); Insanity; Corruption; Movement | `bio`, `characteristics`, `skills`, `wounds`, `fatePoints`, `armour`, `insanity`, `corruption`, `movement` |
| 2 | 3 ranged + 4 melee weapon boxes; Talents and Traits; Gear (21 lines); Weapon Training grid (8 basic / 8 pistol / 4 melee / 4 exotic) | `weapons`, `talentsAndTraits`, `gear`, `weaponTraining` |
| 3 | Rank 1-8 advance blocks (12 rows each); Elite Advances; Total/Spent XP | `advances` |
| 4 | Psy Rating; Discipline; 6 power boxes; Minor Powers table (32 printed rows + 8 write-ins) | `psychic` |
| 5 | 12 further power boxes | `psychic.powers` |

### The calibration sample

`samples/Dark Heresy 1 PC Sheet filled.pdf` — deliberately hard, and its shape drove a
pipeline stage that a naive design would have missed:

| PDF page | Content |
|---|---|
| 1, 3, 5, 7, 9 | Sheet pages 1-5, scanned, **no text layer** |
| 2, 4, 6, 8, 11 | Blank (duplex backs) |
| 10, 12 | Handwritten German session notes — genuinely unmappable |

So **PDF page N is not sheet page N**, and half the document is noise. On top of that the
sheet is filled in faint pencil and cursive, with values written *outside* their boxes, arrows
pointing into fields, and marginal annotations (`+30 Deceive`) floating in white space.

---

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Backend | Python + FastAPI | Best-in-class PDF tooling (PyMuPDF), clean OCR SDK story |
| Frontend | React + TypeScript + Vite | The sheet is component-heavy and the same components serve edit, review and print |
| Input types | Scans **and** digitally-filled PDFs | Auto-detected per page from the text layer |
| PDF export | Render the HTML editor via headless Chromium | One layout to maintain; export can never drift from what you see |
| Storage | Local, single-user, JSON files on disk | No DB, no auth, one command to run |
| Review surface | The editor itself | Flags are highlights on real fields, not a JSON diff |

---

## 3. Architecture

```
scribe-40k/
├── dark-heresy-character-sheet.schema.json    given, untouched
├── extraction-report.schema.json              the flagging sidecar
├── config.toml                                provider / model selection
├── assets/
│   ├── armour-silhouette.png                  extracted from the template
│   └── page-fingerprints.json                 coarse grayscale vectors, 5 template pages
├── data/characters/<id>/                      gitignored runtime state
│   ├── character.json                         validates against the schema
│   ├── report.json                            validates against the report schema
│   ├── source.pdf
│   └── pages/<n>.png
├── backend/scribe40k/
│   ├── models.py         pydantic mirror of the schema
│   ├── constants.py      printed-truth tables
│   ├── derive.py         computed fields + consistency rules
│   ├── blank.py          blank-character factory
│   ├── llm/              base · mistral · openai_compat · anthropic · registry
│   ├── pipeline/         ingest · classify · ocr · map · validate · report
│   ├── export/pdf.py     Playwright print pipeline
│   ├── store.py          JSON-file repository
│   ├── api.py            FastAPI
│   └── cli.py            scribe extract | serve | export
├── frontend/src/sheet/   Page1..Page5, sheet.css, print.css
└── tests/
```

---

## 4. Extraction pipeline

### Stage 0 · Ingest

PyMuPDF opens the PDF, normalises page rotation (the sample's pages are all `rotation=270`),
and rasterises every page at 200 dpi. Page images are always produced — the review UI needs
crops regardless of how the text was obtained.

### Stage 1 · Classify  *(added after probing the sample)*

Deterministic, no LLM spend:

1. **Blank detection** by ink coverage. In the sample, content pages sit at 15-25% of pixels
   below the 240 threshold; duplex backs at 0.2% or less.
2. **Template matching** — each candidate page is downscaled to a coarse grayscale vector and
   correlated against `assets/page-fingerprints.json`. Best match above threshold assigns the
   sheet page number.
3. Anything left over becomes an **unrecognised page**, surfaced in the UI rather than
   silently dropped. The sample's two notes pages land here.

### Stage 2 · OCR (pluggable)

`OcrProvider` protocol returns `list[OcrPage{page, markdown, blocks, bboxes}]`.

| Provider | Notes |
|---|---|
| `mistral` | `mistral-ocr-latest`, layout-aware markdown |
| `openai_compat` | Any OpenAI-shaped vision endpoint — OpenAI, OpenRouter, Azure, Ollama, vLLM, LM Studio |
| `anthropic` | Claude vision |
| `passthrough` | Use the PDF's own text layer, no API call |

Per-page classification decides the route: a page with a real text layer skips OCR entirely.
Results are cached on disk keyed by `(file_hash, provider, model)`, so re-running the mapper
costs nothing.

### Stage 3 · Map (pluggable reasoning LLM)

Six independent section jobs, each given a narrow sub-schema and only its own pages. They run
in parallel and fail independently:

`bio+characteristics` (p1) · `skills` (p1) · `combat-state` (p1) · `page2` (p2) ·
`advances` (p3) · `psychic` (p4-5)

Each returns an **envelope**, not the target schema directly, so uncertainty survives the hop:

```json
{"pointer": "/bio/career", "value": "Adeptus Arbites",
 "confidence": 0.42, "evidence": {"page": 1, "snippet": "Career_ Adeptus Arb...", "bbox": [0, 0, 0, 0]}}
```

Structured-output / JSON-schema mode where the provider supports it; prompt + repair loop
where it does not.

**Page images go to the reasoning model alongside the OCR text** whenever the model is
vision-capable. The ticks in the skills grid and the weapon-training block are most of the
data on this sheet, and OCR renders them all as identical `o` glyphs. Text-only models stay
usable, with a warning that checkbox recall will be poor.

### Stage 4 · Deterministic fill

Roughly two-thirds of the schema is *printed information* and the LLM is never asked for it.

`constants.py` supplies every skill's governing characteristic and `isBasicSkill`, the
characteristic abbreviations, armour `location` / `hitRoll` / `hitRollMin` / `hitRollMax`,
and the 32-row minor-power table (name / threshold / focus / sustain).

`derive.py` computes `bonus = floor(total / 10)`, `proficiency.modifier` from `level` per the
schema's `$defs` rules, and gear `quantity` from a leading `"3 x ..."`.

### Stage 5 · Validate and flag

`jsonschema` 2020-12, plus rule checks that emit **flags rather than errors**:

- `total != base + 5 × advancesTaken`
- `bonus != floor(total / 10)`
- movement vs Agility bonus (half = AgB, full = 2×, charge = 3×, run = 6×)
- `currentWounds > totalWounds`, `currentFatePoints > totalFatePoints`,
  `spentExperience > totalExperience`
- a group-skill specialisation carrying a proficiency but no `subject`
- `level: "Basic"` on a skill whose printed `isBasicSkill` is false
- required-but-null leaves; low model confidence

### Stage 6 · Report

`report.json` is a **separate file**: the character schema is `additionalProperties: false`
throughout, so confidence data cannot be inlined without breaking validation.

Per field: `status` in `{ok, needs_review, unresolved, user_fixed}`, confidence, evidence
(page, snippet, bbox), and alternative readings. Plus `unmapped[]` — text found on the sheet
that the mapper could not place.

---

## 5. Review, in the editor

There is no separate review screen. Confirmed behaviour:

- **Ghost only when flagged.** High-confidence values are committed as ordinary content.
  Only flagged fields render their value as an italic suggestion with accept / reject
  controls, so you touch only what is actually doubtful.
- **Crop on demand.** Clicking a flag opens a popover with a cropped image of that region of
  the scan, the OCR snippet, the confidence, and alternative readings.
- **Counter + jump-to-next.** A persistent "N fields need review" bar with next / previous
  navigation. Export still works with flags open, but warns first.
- **Assignment tray.** A docked "found on the sheet, not assigned" panel lists each stray
  fragment with its source crop. Drag onto a field to use it, or dismiss it. Whatever is left
  stays in the report sidecar. The sample's marginal `+30 Deceive` and its two notes pages
  land here.

---

## 6. Frontend

Five page components mirroring the printed sheet, with two CSS modes over identical markup:

- **screen** — inputs, live-derived values (bonus, movement, per-skill test targets), flag
  badges, autosave `PATCH` with server-side re-validation
- **print** — form chrome stripped, values as static text, `@page { size: 213mm 276mm }`,
  fixed page breaks

### Expandable sections

The printed sheet has fixed line counts; the schema does not (`gear`, `talentsAndTraits.*`,
`weapons.*` and `psychic.powers` are all unbounded arrays). The UI therefore treats the
printed counts as a *starting* size, not a limit:

| Section | Printed | Behaviour |
|---|---|---|
| Gear | 21 lines | Add / remove rows freely |
| Talents and Traits | 3 + 15 lines | Add / remove rows freely |
| Ranged weapons | 3 boxes | Add / remove boxes |
| Melee weapons | 4 boxes | Add / remove boxes |
| Psychic powers | 18 boxes | Add / remove boxes |
| Minor powers | 32 + 8 rows | 32 printed rows fixed; custom rows unbounded |
| Advances | 12 rows × 8 ranks | Add / remove rows per rank |

On export, content beyond the printed capacity **continues onto an appended page** rather than
being clipped, so an expanded sheet always prints in full.

---

## 7. PDF export

`POST /api/characters/{id}/export.pdf` drives Playwright (headless Chromium) to the app's own
`/print/{id}` route and calls `page.pdf()` with `preferCSSPageSize`. Same DOM as the editor,
so export cannot drift from what you see.

Page-size options: native 213 × 276 mm (default), A4, Letter.

---

## 8. Configuration

```toml
[ocr]
provider = "mistral"
model    = "mistral-ocr-latest"

[reasoning]
provider = "openai"
model    = "gpt-5"
base_url = "https://api.openai.com/v1"
```

API keys come from environment variables, never the config file. Every stage is overridable
on the CLI. Adding a provider means one new file implementing two protocols.

---

## 9. Build order

1. Schema to pydantic models, `constants.py`, `derive.py`, blank-character factory, tests
2. Ingest + classify + provider abstraction (Mistral, OpenAI-compatible), fixture-based
   offline mode so the pipeline is testable without API spend
3. Mapping, validation, report generation; CLI `scribe extract`
4. FastAPI + JSON store
5. React sheet — page 1 first (hardest layout), then 2-5
6. Flag / review affordances layered onto the editor
7. Playwright export + print CSS tuned against the original render
8. End-to-end: fill a sheet in the UI, export, re-ingest, compare

---

## 10. Known limitations

- **Typography** is a near-match, not identical, until a licensed `ColumbusMT` is supplied.
- **Checkbox recall depends on a vision-capable reasoning model.** Text-only configurations
  will under-report ticked skills.
- The **schema is treated as fixed**. Content with nowhere to go (the sample's session notes)
  lives in the report sidecar, not in `character.json`.
- Copyrighted source PDFs are **gitignored**; only derived, non-reproducible assets are
  committed.
