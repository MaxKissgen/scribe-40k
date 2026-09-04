"""scribe-40k: extract, edit and export Dark Heresy 1st Edition character sheets."""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"

#: Repository root: ``backend/scribe40k/__init__.py`` -> up three. Duplicated from
#: :mod:`scribe40k.paths` because the .env file has to be loaded *before* anything reads
#: an environment variable, and ``paths`` itself reads two at import time.
_ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None, *, override: bool = False) -> list[str]:
    """Read ``.env`` into the process environment.

    Called once at import, because every entry point -- ``scribe serve``, the CLI, the
    exporter's background server -- needs credentials, and requiring each to remember to
    load them is how a key ends up sitting in a file that nothing reads.

    A variable already set in the real environment wins by default, so an explicit
    ``MISTRAL_API_KEY=... scribe extract ...`` still overrides the file.

    Returns the names that were set, so a caller can report what was picked up. Never
    returns or logs a value.
    """
    path = path or _ROOT / ".env"
    if not path.is_file():
        return []

    applied: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Tolerate a leading `export`, as people paste these in from a shell.
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        name, separator, value = line.partition("=")
        if not separator:
            continue

        name = name.strip()
        if not name:
            continue

        value = value.strip()
        # Strip one matching pair of surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        if not value:
            continue
        if not override and name in os.environ:
            continue

        os.environ[name] = value
        applied.append(name)

    return applied


#: Names loaded from .env at import, for the health endpoint to report.
LOADED_ENV_KEYS: list[str] = load_env()
