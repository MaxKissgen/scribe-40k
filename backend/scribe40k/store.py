"""Character storage: one directory per character, plain JSON on disk.

No database. A character is a folder you can open, read, diff, copy to another machine, or
put under version control yourself. That suits a single-user local tool, and it means the
data outlives this program.

    data/characters/<id>/
        character.json   validates against dark-heresy-character-sheet.schema.json
        report.json      validates against extraction-report.schema.json
        pending.json     an import read but not yet confirmed; deleted once it is
        source.pdf       the uploaded scan, kept for the review crops
        pages/           rendered page images
        prints/          what each exported PDF looked like, for reading a marked-up
                         printout of it back in
        updates/<uid>/   one re-reading of the character from such a printout: its own
                         source.pdf, pages/ and pending.json, kept because the suggestions
                         it raised need its crops long after it was read

A directory with a ``pending.json`` and no ``character.json`` is an import waiting for
someone to confirm which page is which. It does not appear in the character list, because
it is not a character yet.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .blank import blank_character
from .paths import characters_root
from .pipeline.report import ExtractionReport

CHARACTER_FILE = "character.json"
REPORT_FILE = "report.json"
PENDING_FILE = "pending.json"
PRINTS_DIR = "prints"
UPDATES_DIR = "updates"

#: Ids that may appear in a path. Anything else is a caller trying to escape the store.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


SOURCE_FILE = "source.pdf"
PAGES_DIR = "pages"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def safe_id(value: str) -> str:
    """Reject an id that could reach outside the data directory."""
    if not _ID_RE.match(value or ""):
        raise ValueError(f"not a valid id: {value!r}")
    return value


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:48] or "unnamed"


@dataclass
class CharacterSummary:
    id: str
    name: str
    career: str | None
    updatedAt: str
    reviewCount: int = 0
    hasReport: bool = False


class CharacterStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or characters_root())

    # -- paths ------------------------------------------------------------------

    def directory(self, character_id: str) -> Path:
        return self.root / character_id

    def character_path(self, character_id: str) -> Path:
        return self.directory(character_id) / CHARACTER_FILE

    def report_path(self, character_id: str) -> Path:
        return self.directory(character_id) / REPORT_FILE

    def pending_path(self, character_id: str) -> Path:
        return self.directory(character_id) / PENDING_FILE

    def pages_dir(self, character_id: str) -> Path:
        return self.directory(character_id) / PAGES_DIR

    def prints_dir(self, character_id: str) -> Path:
        return self.directory(character_id) / PRINTS_DIR

    def source_path(self, character_id: str) -> Path:
        return self.directory(character_id) / SOURCE_FILE

    def update_key(self, character_id: str, update_id: str) -> str:
        """An update is stored as a nested pseudo-character.

        It has a source PDF, rendered pages and a pending file, and wants exactly the same
        handling as an import for all three -- so it gets the same methods, one directory
        down, rather than a parallel set of them.
        """
        return f"{safe_id(character_id)}/{UPDATES_DIR}/{safe_id(update_id)}"

    def list_updates(self, character_id: str) -> list[str]:
        """Update ids with a pending file: read, but not yet turned into suggestions."""
        directory = self.directory(character_id) / UPDATES_DIR
        if not directory.is_dir():
            return []
        return sorted(
            child.name for child in directory.iterdir() if (child / PENDING_FILE).is_file()
        )

    # -- identity ---------------------------------------------------------------

    def new_id(self, name: str | None = None) -> str:
        """A readable, unique directory name."""
        base = slugify(name) if name else "character"
        candidate = base
        suffix = 2
        while self.directory(candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
            if suffix > 99:  # pragma: no cover - pathological
                candidate = f"{base}-{uuid.uuid4().hex[:8]}"
                break
        return candidate

    def exists(self, character_id: str) -> bool:
        return self.character_path(character_id).exists()

    # -- reads ------------------------------------------------------------------

    def load(self, character_id: str) -> dict:
        path = self.character_path(character_id)
        if not path.exists():
            raise FileNotFoundError(f"no character '{character_id}' in {self.root}")
        return json.loads(path.read_text(encoding="utf-8"))

    def load_report(self, character_id: str) -> ExtractionReport | None:
        path = self.report_path(character_id)
        if not path.exists():
            return None
        return ExtractionReport.model_validate_json(path.read_text(encoding="utf-8"))

    def list_characters(self) -> list[CharacterSummary]:
        if not self.root.exists():
            return []

        summaries = []
        for directory in sorted(self.root.iterdir()):
            path = directory / CHARACTER_FILE
            if not path.is_file():
                continue
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            report = self.load_report(directory.name)
            bio = document.get("bio") or {}
            summaries.append(
                CharacterSummary(
                    id=directory.name,
                    name=bio.get("characterName") or directory.name,
                    career=bio.get("career"),
                    updatedAt=datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                    reviewCount=report.review_count if report else 0,
                    hasReport=report is not None,
                )
            )

        return sorted(summaries, key=lambda s: s.updatedAt, reverse=True)

    # -- writes -----------------------------------------------------------------

    def save(self, character_id: str, document: dict) -> Path:
        path = self.character_path(character_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        return path

    def save_report(self, character_id: str, report: ExtractionReport) -> Path:
        path = self.report_path(character_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(report.to_json_dict(), indent=2, ensure_ascii=False) + "\n")
        return path

    def save_pending(self, character_id: str, prepared) -> Path:
        """Park a read-but-unconfirmed import between the two halves of the pipeline."""
        path = self.pending_path(character_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(prepared.to_json_dict(), indent=2, ensure_ascii=False)
        _atomic_write(path, payload + "\n")
        return path

    def save_print_layout(self, character_id: str, layout) -> Path:
        """Remember what a printing of this character looked like.

        Only the most recent few are kept. Older ones describe a character that has since
        been edited, so they get less useful the further back they go, and a scan is
        matched against all of them anyway.
        """
        directory = self.prints_dir(character_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(layout.to_json_dict(), indent=2, ensure_ascii=False)
        path = directory / f"{layout.recorded_at.replace(':', '')}-{layout.id}.json"
        _atomic_write(path, payload + "\n")

        from .pipeline.print_layout import KEEP

        for stale in sorted(directory.glob("*.json"))[:-KEEP]:
            stale.unlink(missing_ok=True)
        return path

    def load_print_layouts(self, character_id: str) -> list:
        """Every remembered printing, newest last."""
        from .pipeline.print_layout import PrintLayout

        directory = self.prints_dir(character_id)
        if not directory.is_dir():
            return []

        layouts = []
        for path in sorted(directory.glob("*.json")):
            try:
                layouts.append(PrintLayout.from_json_dict(json.loads(path.read_text("utf-8"))))
            except (json.JSONDecodeError, OSError, KeyError):
                continue
        return layouts

    def list_pending(self) -> list[dict]:
        """Imports read but never confirmed.

        They are invisible to :meth:`list_characters` by design -- they are not characters
        yet -- which would make an abandoned one invisible full stop. The front page lists
        these separately so it can be resumed or thrown away.
        """
        if not self.root.exists():
            return []

        pending = []
        for directory in sorted(self.root.iterdir()):
            path = directory / PENDING_FILE
            if not path.is_file() or (directory / CHARACTER_FILE).is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            pending.append(
                {
                    "id": directory.name,
                    "sourceName": data.get("sourceName") or directory.name,
                    "pageCount": len(data.get("pages") or []),
                    "startedAt": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                }
            )
        return sorted(pending, key=lambda entry: entry["startedAt"], reverse=True)

    def load_pending(self, character_id: str):
        """The parked import, or None. Returns a ``PreparedPages``."""
        from .pipeline.run import PreparedPages

        path = self.pending_path(character_id)
        if not path.exists():
            return None
        return PreparedPages.from_json_dict(json.loads(path.read_text(encoding="utf-8")))

    def clear_pending(self, character_id: str) -> None:
        self.pending_path(character_id).unlink(missing_ok=True)

    def store_source(self, character_id: str, pdf_path: Path) -> Path:
        dest = self.source_path(character_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, dest)
        return dest

    def create_blank(self, name: str | None = None) -> str:
        character_id = self.new_id(name)
        document = blank_character().to_json_dict()
        if name:
            document["bio"]["characterName"] = name
        self.save(character_id, document)
        return character_id

    def delete(self, character_id: str) -> None:
        directory = self.directory(character_id)
        if directory.exists():
            shutil.rmtree(directory)


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temporary file, so an interrupted save cannot truncate the original."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
