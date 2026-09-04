"""Regenerate the derived assets from the blank template PDF.

The template itself is copyrighted and stays out of version control; these two derived
artefacts are what the application actually needs at runtime:

``assets/armour-silhouette.png``
    The 1-bit stencil embedded on page 1, lifted losslessly so the frontend renders the
    original art rather than a trace of it.

``assets/page-fingerprints.json``
    A coarse grayscale vector per template page, used to work out which scanned page is
    which. Far too low-resolution to reconstruct the sheet from.

Run with::

    python -m scribe40k.tools.build_assets [path/to/blank-template.pdf]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pymupdf

from .. import constants as K
from ..paths import ARMOUR_SILHOUETTE, ASSETS, BLANK_TEMPLATE, PAGE_FINGERPRINTS
from ..pipeline.fingerprint import FINGERPRINT_COLS, FINGERPRINT_ROWS, fingerprint_from_gray
from ..pipeline.ingest import boilerplate_tokens


def extract_silhouette(doc: pymupdf.Document, dest: Path) -> tuple[int, int]:
    """Pull the armour body stencil off page 1."""
    images = doc[0].get_images(full=True)
    if not images:
        raise RuntimeError("page 1 of the template has no embedded image")

    # The silhouette is the only raster on the page, and by far the largest.
    xref = max(images, key=lambda im: im[2] * im[3])[0]
    data = doc.extract_image(xref)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data["image"])
    return data["width"], data["height"]


def build_fingerprints(doc: pymupdf.Document) -> dict:
    """One coarse grayscale vector per template page, plus the printed vocabulary."""
    if doc.page_count != K.SHEET_PAGE_COUNT:
        raise RuntimeError(
            f"expected a {K.SHEET_PAGE_COUNT}-page template, got {doc.page_count} pages"
        )

    pages = []
    boilerplate: set[str] = set()
    for index in range(doc.page_count):
        page = doc[index]
        pix = page.get_pixmap(dpi=50, colorspace=pymupdf.csGRAY)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        pages.append(
            {
                "sheetPage": index + 1,
                "vector": [round(v, 4) for v in fingerprint_from_gray(gray).tolist()],
            }
        )
        boilerplate.update(boilerplate_tokens(page.get_text("text") or ""))

    return {
        "description": (
            "Coarse grayscale fingerprints of the blank Dark Heresy template, used to "
            "identify which scanned page corresponds to which sheet page, plus the "
            "template's own printed vocabulary, used to tell a digitally-filled sheet "
            "from an empty one. Neither is sufficient to reconstruct the sheet."
        ),
        "grid": {"rows": FINGERPRINT_ROWS, "cols": FINGERPRINT_COLS},
        "pages": pages,
        "boilerplateTokens": sorted(boilerplate),
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    template = Path(argv[0]) if argv else BLANK_TEMPLATE

    if not template.exists():
        print(
            f"Blank template not found at {template}.\n"
            "It is copyrighted and therefore not committed. Place your own copy at "
            f"{BLANK_TEMPLATE} or pass a path.",
            file=sys.stderr,
        )
        return 1

    ASSETS.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(template) as doc:
        width, height = extract_silhouette(doc, ARMOUR_SILHOUETTE)
        fingerprints = build_fingerprints(doc)

    PAGE_FINGERPRINTS.write_text(json.dumps(fingerprints, indent=2) + "\n", encoding="utf-8")

    print(f"armour silhouette  -> {ARMOUR_SILHOUETTE}  ({width}x{height})")
    print(f"page fingerprints  -> {PAGE_FINGERPRINTS}  ({len(fingerprints['pages'])} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
