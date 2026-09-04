"""Record this application's own print layout as a second set of page fingerprints.

Page classification matches a scanned page against known layouts. Out of the box the only
known layout is the original Games Workshop template, which means a sheet *exported by
this tool* would not be recognised on re-import: the HTML rendering is a lookalike, not a
pixel twin, and the two correlate at only about 0.38 -- well under the 0.55 threshold.

This tool closes that loop. It exports a representative character to PDF, fingerprints the
result, and appends those vectors to ``assets/page-fingerprints.json`` as the ``html``
variant. Classification then accepts either layout.

Run after any change that moves things around on the printed page::

    python -m scribe40k.tools.record_layout

Needs Playwright, and the frontend to have been built.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf

from .. import constants as K
from ..paths import FRONTEND_DIST, PAGE_FINGERPRINTS
from ..pipeline.fingerprint import fingerprint_from_gray

#: Recorded layouts are named "html:<shape>". Two are captured, because pages whose
#: content dominates their appearance do not correlate across extremes: sheet page 3 is
#: mostly whitespace, so a blank advances page and a fully-written one score only about
#: 0.46 against each other. Recording both means either is recognised.
VARIANT_PREFIX = "html"
SHAPES = ("sparse", "full")


def _fingerprint_pdf(path: Path) -> list[np.ndarray]:
    vectors = []
    with pymupdf.open(path) as doc:
        for index in range(doc.page_count):
            pix = doc[index].get_pixmap(dpi=50, colorspace=pymupdf.csGRAY)
            gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            vectors.append(fingerprint_from_gray(gray))
    return vectors


def _representative_character(store, character_id: str, shape: str) -> None:
    """Write a character whose printed sheet exercises one end of the layout range.

    Both shapes set a Psy Rating, because pages 4 and 5 collapse entirely without one and
    would otherwise never be recorded at all.

    "sparse" is an otherwise empty sheet: what most exports look like.
    "full" fills the advance blocks, which is the one thing that changes a page's
    appearance enough to defeat matching -- page 3 is mostly whitespace, so blank and
    written-up versions of it correlate at only about 0.46.

    Deliberately *not* filled: gear and talents. Filling those pushes page 2 onto a
    continuation page, and then the Nth PDF page is no longer the Nth sheet page, which
    would record the wrong vectors against the wrong pages.
    """
    from ..blank import blank_character

    document = blank_character().to_json_dict()
    document["bio"]["characterName"] = "Layout Reference"
    document["psychic"]["psyRating"] = 1

    if shape == "full":
        document["advances"]["totalExperience"] = 5000
        document["advances"]["spentExperience"] = 4800
        document["advances"]["rankAdvances"] = [
            {
                "rank": rank,
                "entries": [{"advance": "Advance", "cost": 100} for _ in range(6)],
            }
            for rank in range(1, 9)
        ]
        document["advances"]["eliteAdvances"] = [
            {"advance": "Elite", "cost": 200} for _ in range(6)
        ]

    store.save(character_id, document)


def main(argv: list[str] | None = None) -> int:
    del argv

    if not FRONTEND_DIST.exists():
        print(
            "The frontend has not been built, so there is no layout to record. Run:\n"
            "    cd frontend && npm install && npm run build",
            file=sys.stderr,
        )
        return 1

    if not PAGE_FINGERPRINTS.exists():
        print(
            f"{PAGE_FINGERPRINTS} is missing. Generate it first with:\n"
            "    python -m scribe40k.tools.build_assets path/to/blank-template.pdf",
            file=sys.stderr,
        )
        return 1

    from ..export.pdf import ExportError, export_character
    from ..store import CharacterStore

    recorded: dict[str, list[np.ndarray]] = {}

    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace)
        store = CharacterStore(root / "characters")

        # The exporter starts a server, which reads the store through the module-level
        # instance in scribe40k.api. Point that at this temporary store for the run.
        from .. import api

        original_store = api.store
        api.store = store
        try:
            for shape in SHAPES:
                character_id = f"layout-{shape}"
                _representative_character(store, character_id, shape)
                destination = root / f"layout-{shape}.pdf"
                export_character(character_id, destination)

                vectors = _fingerprint_pdf(destination)
                if len(vectors) != K.SHEET_PAGE_COUNT:
                    print(
                        f"The '{shape}' reference exported {len(vectors)} pages, not "
                        f"{K.SHEET_PAGE_COUNT}. Its content must be spilling onto a "
                        f"continuation page, which would record each vector against the "
                        f"wrong sheet page. Adjust _representative_character.",
                        file=sys.stderr,
                    )
                    return 1
                recorded[shape] = vectors
        except ExportError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        finally:
            api.store = original_store

    data = json.loads(PAGE_FINGERPRINTS.read_text(encoding="utf-8"))
    # Replace any previous recording rather than accumulating stale layouts.
    data["pages"] = [
        page
        for page in data["pages"]
        if not str(page.get("variant", "")).startswith(VARIANT_PREFIX)
    ]
    for page in data["pages"]:
        page.setdefault("variant", "template")

    total = 0
    for shape, vectors in recorded.items():
        for index, vector in enumerate(vectors, start=1):
            data["pages"].append(
                {
                    "sheetPage": index,
                    "variant": f"{VARIANT_PREFIX}:{shape}",
                    "vector": [round(value, 4) for value in vector.tolist()],
                }
            )
            total += 1

    data["pages"].sort(key=lambda page: (page["sheetPage"], page.get("variant", "")))
    PAGE_FINGERPRINTS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    print(
        f"Recorded {total} page fingerprint(s) across {len(recorded)} layout shape(s) "
        f"in {PAGE_FINGERPRINTS}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
