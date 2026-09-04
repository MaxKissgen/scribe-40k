"""Coarse page fingerprints, for deciding which scanned page is which.

A scan of a filled sheet differs from the blank template in the handwriting, the scanner's
tone curve and a few degrees of skew -- but the *printed furniture* dominates: rules,
boxes, headings, the three-column skills grid. Downsampling to a small grid throws away
the handwriting and keeps the furniture, which makes a plain correlation good enough to
identify pages without spending a model call on it.

Deliberately simple. If this ever proves insufficient, the fallback is to ask the vision
model which sheet page it is looking at, not to build a template matcher.
"""

from __future__ import annotations

import numpy as np

#: Grid the page is reduced to. Small enough that handwriting averages out, large enough
#: that the five template pages stay clearly distinguishable from one another.
FINGERPRINT_ROWS = 40
FINGERPRINT_COLS = 30


#: Fraction of the peak row/column ink density that still counts as content, when
#: locating a page's printed area.
CONTENT_EDGE_FRACTION = 0.02


def crop_to_content(gray: np.ndarray, frac: float = CONTENT_EDGE_FRACTION) -> np.ndarray:
    """Trim a page to its inked area.

    Without this, matching fails outright. The calibration sample is a 213x276 mm sheet
    photocopied onto A4 and scanned, so its printed area sits at a different offset and
    scale from the template's. Cropping both to their content boxes puts them back in the
    same frame and lifts the correct match from 0.59 to 0.96.
    """
    ink = 255.0 - gray.astype(np.float32)
    rows, cols = ink.mean(axis=1), ink.mean(axis=0)

    def span(profile: np.ndarray) -> tuple[int, int]:
        peak = float(profile.max())
        if peak <= 0:
            return 0, len(profile)
        marked = np.flatnonzero(profile > peak * frac)
        return (int(marked[0]), int(marked[-1]) + 1) if marked.size else (0, len(profile))

    r0, r1 = span(rows)
    c0, c1 = span(cols)
    cropped = gray[r0:r1, c0:c1]
    return cropped if cropped.size else gray


def fingerprint_from_gray(gray: np.ndarray) -> np.ndarray:
    """Reduce a grayscale page to a normalised ink-density vector.

    The page is first cropped to its content box, then reduced to a fixed grid. The result
    is mean-centred and scaled to unit norm, so a dot product between two fingerprints is
    their correlation and scanner brightness drops out.
    """
    if gray.ndim != 2:
        raise ValueError(f"expected a 2-D grayscale array, got shape {gray.shape}")

    gray = crop_to_content(gray)
    height, width = gray.shape
    row_edges = np.linspace(0, height, FINGERPRINT_ROWS + 1, dtype=int)
    col_edges = np.linspace(0, width, FINGERPRINT_COLS + 1, dtype=int)

    # Work in "ink" (255 - value) so that an empty page is zero rather than saturated.
    ink = 255.0 - gray.astype(np.float32)

    cells = np.empty((FINGERPRINT_ROWS, FINGERPRINT_COLS), dtype=np.float32)
    for r in range(FINGERPRINT_ROWS):
        r0, r1 = row_edges[r], row_edges[r + 1]
        for c in range(FINGERPRINT_COLS):
            c0, c1 = col_edges[c], col_edges[c + 1]
            block = ink[r0:r1, c0:c1]
            cells[r, c] = block.mean() if block.size else 0.0

    return _normalise(cells.reshape(-1))


def _normalise(vector: np.ndarray) -> np.ndarray:
    centred = vector - vector.mean()
    norm = float(np.linalg.norm(centred))
    if norm < 1e-6:
        # A uniformly blank page has no structure to correlate against.
        return np.zeros_like(centred)
    return centred / norm


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation of two fingerprints, in [-1, 1]."""
    if a.shape != b.shape:
        raise ValueError(f"fingerprint shapes differ: {a.shape} vs {b.shape}")
    return float(np.dot(a, b))


def ink_coverage(gray: np.ndarray, threshold: int = 240) -> float:
    """Fraction of pixels darker than ``threshold``.

    On the calibration sample, content pages land at 0.15-0.25 and duplex backs at
    0.002 or less, so the two populations are separated by two orders of magnitude.
    """
    return float((gray < threshold).mean())
