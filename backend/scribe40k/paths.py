"""Filesystem layout.

One place that knows where things live, so nothing else has to guess relative to
``__file__``.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repository root: ``backend/scribe40k/paths.py`` -> up three.
ROOT = Path(__file__).resolve().parents[2]

CHARACTER_SCHEMA = ROOT / "dark-heresy-character-sheet.schema.json"
REPORT_SCHEMA = ROOT / "extraction-report.schema.json"

ASSETS = ROOT / "assets"
ARMOUR_SILHOUETTE = ASSETS / "armour-silhouette.png"
PAGE_FINGERPRINTS = ASSETS / "page-fingerprints.json"

SAMPLES = ROOT / "samples"
BLANK_TEMPLATE = SAMPLES / "dark-heresy-blank-template.pdf"

FRONTEND_DIST = ROOT / "frontend" / "dist"


def data_root() -> Path:
    """Runtime state. Overridable so tests never touch the real data directory."""
    return Path(os.environ.get("SCRIBE40K_DATA", ROOT / "data"))


def characters_root() -> Path:
    return data_root() / "characters"


def character_dir(character_id: str) -> Path:
    return characters_root() / character_id


def cache_root() -> Path:
    return Path(os.environ.get("SCRIBE40K_CACHE", ROOT / ".cache"))
