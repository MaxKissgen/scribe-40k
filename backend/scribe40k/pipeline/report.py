"""The extraction report: what happened, and what still needs a human.

Mirrors ``extraction-report.schema.json``. This is the document that drives the review
experience -- the editor reads ``flags`` to decide which fields render as unconfirmed
suggestions, and ``unmapped`` to populate the assignment tray.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warning", "error"]
FlagStatus = Literal["needs_review", "user_fixed", "accepted", "dismissed"]
UnmappedStatus = Literal["unresolved", "assigned", "dismissed"]

#: Below this, a model's own stated confidence earns a flag on its own.
LOW_CONFIDENCE_THRESHOLD = 0.75


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRef(Strict):
    provider: str
    model: str
    #: Recorded because a text-only reasoning model cannot see ticked boxes, and those are
    #: most of the data on this sheet. It explains an implausibly empty skills grid.
    supportsVision: bool | None = None


class SourceRef(Strict):
    filename: str
    fileHash: str
    pageCount: int


class PageRecord(Strict):
    pdfPage: int
    kind: Literal["sheet", "blank", "unrecognised"]
    sheetPage: int | None = None
    textSource: Literal["text_layer", "ocr"] = "ocr"
    matchScore: float | None = None
    #: "image" when the page fingerprint placed this page, "text" when the fingerprint
    #: failed and the transcription identified it instead.
    matchedBy: Literal["image", "text"] = "image"
    #: Weighted recall against that page's printed vocabulary; set only for "text".
    textScore: float | None = None
    ink: float | None = None
    imagePath: str | None = None
    note: str | None = None
    ocrError: str | None = None


class Evidence(Strict):
    pdfPage: int | None = None
    sheetPage: int | None = None
    snippet: str | None = None
    bbox: list[float] | None = None


class Flag(Strict):
    #: RFC 6901 pointer into character.json.
    pointer: str
    severity: Severity
    rule: str
    message: str
    status: FlagStatus = "needs_review"
    confidence: float | None = None
    expected: object = None
    actual: object = None
    alternatives: list[str] = Field(default_factory=list)
    evidence: Evidence | None = None


class UnmappedSource(Strict):
    pdfPage: int
    sheetPage: int | None = None
    location: str | None = None
    bbox: list[float] | None = None


class UnmappedItem(Strict):
    id: str = ""
    text: str
    source: UnmappedSource
    reason: str | None = None
    status: UnmappedStatus = "unresolved"
    assignedTo: str | None = None

    def ensure_id(self) -> str:
        """A stable id derived from the content, so dismissals survive a re-run."""
        if not self.id:
            seed = f"{self.source.pdfPage}:{self.text}"
            self.id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return self.id


class SectionRecord(Strict):
    name: str
    status: Literal["ok", "failed", "skipped"]
    sheetPages: list[int] = Field(default_factory=list)
    error: str | None = None
    promptTokens: int | None = None
    completionTokens: int | None = None
    cached: bool | None = None


class ExtractionReport(Strict):
    """Provenance and review state for one character.

    Every character has one, imported or not. A character created by hand carries a report
    with no source and no models, so that consistency flags behave identically whichever
    way the sheet came into existence.
    """

    version: Literal[1] = 1
    generatedAt: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    source: SourceRef | None = None
    models: dict[str, ModelRef] | None = None
    pages: list[PageRecord] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    unmapped: list[UnmappedItem] = Field(default_factory=list)
    sections: list[SectionRecord] = Field(default_factory=list)

    # -- queries the UI and CLI both want -------------------------------------------

    @property
    def open_flags(self) -> list[Flag]:
        return [f for f in self.flags if f.status == "needs_review"]

    @property
    def review_count(self) -> int:
        """What the editor's 'N fields need review' counter shows."""
        return len(self.open_flags)

    def flags_for(self, pointer: str) -> list[Flag]:
        return [f for f in self.flags if f.pointer == pointer]

    def severity_counts(self) -> dict[str, int]:
        counts = {"error": 0, "warning": 0, "info": 0}
        for flag in self.open_flags:
            counts[flag.severity] += 1
        return counts

    def to_json_dict(self) -> dict:
        for item in self.unmapped:
            item.ensure_id()
        return self.model_dump(mode="json")
