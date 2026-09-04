"""Character storage: one directory per character, plain JSON on disk.

No database. A character is a folder you can open, read, diff, copy to another machine, or
put under version control yourself. That suits a single-user local tool, and it means the
data outlives this program.

    data/characters/<id>/
        character.json   validates against dark-heresy-character-sheet.schema.json
        report.json      validates against extraction-report.schema.json
        source.pdf       the uploaded scan, kept for the review crops
        pages/           rendered page images
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
SOURCE_FILE = "source.pdf"
PAGES_DIR = "pages"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


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

    def pages_dir(self, character_id: str) -> Path:
        return self.directory(character_id) / PAGES_DIR

    def source_path(self, character_id: str) -> Path:
        return self.directory(character_id) / SOURCE_FILE

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
