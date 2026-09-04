"""Providers that make no network calls.

Two of them, for two different reasons.

:class:`PassthroughOcr` is a real production path: when a PDF carries its own text layer
there is nothing to transcribe, and paying an OCR provider to re-read text we already have
would be waste.

:class:`FixtureOcr` and :class:`FixtureReasoning` replay recorded responses from disk. They
make the whole pipeline testable end to end without an API key and without spend, which is
what keeps the mapping stage under test at all.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from .base import OcrPage, PageImage, ReasoningRequest, ReasoningResponse
from .config import StageConfig
from .json_utils import extract_json


class PassthroughOcr:
    """Uses text already embedded in the PDF. No API call, no cost."""

    def __init__(self, config: StageConfig | None = None) -> None:
        self.name = "passthrough"
        self.model = "pdf-text-layer"
        self._texts: dict[int, str] = {}

    def load_from_ingest(self, pages) -> None:
        """Take the embedded text captured during ingest, keyed by PDF page."""
        self._texts = {p.pdf_page: p.embedded_text for p in pages}

    def transcribe(self, pages: Sequence[PageImage]) -> list[OcrPage]:
        results = []
        for page in pages:
            text = self._texts.get(page.pdf_page, "")
            results.append(
                OcrPage(
                    pdf_page=page.pdf_page,
                    sheet_page=page.sheet_page,
                    text=text,
                    provider=self.name,
                    model=self.model,
                    error=None if text.strip() else "no text layer on this page",
                )
            )
        return results


class FixtureOcr:
    """Replays transcriptions from a directory of ``page-NN.txt`` files."""

    def __init__(self, config: StageConfig) -> None:
        self.name = "fixture"
        self.model = config.model or "fixture"
        self._dir = Path(config.options.get("path", "tests/fixtures/ocr"))

    def transcribe(self, pages: Sequence[PageImage]) -> list[OcrPage]:
        results = []
        for page in pages:
            candidate = self._dir / f"page-{page.pdf_page:02d}.txt"
            if candidate.exists():
                text, error = candidate.read_text(encoding="utf-8"), None
            else:
                text, error = "", f"no fixture at {candidate}"

            results.append(
                OcrPage(
                    pdf_page=page.pdf_page,
                    sheet_page=page.sheet_page,
                    text=text,
                    provider=self.name,
                    model=self.model,
                    error=error,
                )
            )
        return results


class FixtureReasoning:
    """Replays mapping replies from ``<label>.json`` files."""

    def __init__(self, config: StageConfig) -> None:
        self.name = "fixture"
        self.model = config.model or "fixture"
        self.supports_vision = True
        self.supports_structured_output = True
        self._dir = Path(config.options.get("path", "tests/fixtures/reasoning"))

    def complete(self, request: ReasoningRequest) -> ReasoningResponse:
        candidate = self._dir / f"{request.label or 'response'}.json"
        if not candidate.exists():
            return ReasoningResponse(
                text="",
                provider=self.name,
                model=self.model,
                error=f"no fixture at {candidate}",
            )

        text = candidate.read_text(encoding="utf-8")
        return ReasoningResponse(
            text=text,
            parsed=extract_json(text),
            provider=self.name,
            model=self.model,
            cached=True,
        )


class RecordingReasoning:
    """Wraps a real provider and writes every reply to disk as a fixture.

    Run the pipeline once against live models with recording on, and the whole mapping
    stage becomes reproducible offline from then on.
    """

    def __init__(self, inner, directory: Path) -> None:
        self._inner = inner
        self._dir = Path(directory)
        self.name = f"recording:{inner.name}"
        self.model = inner.model
        self.supports_vision = inner.supports_vision
        self.supports_structured_output = inner.supports_structured_output

    def complete(self, request: ReasoningRequest) -> ReasoningResponse:
        response = self._inner.complete(request)
        if response.ok:
            self._dir.mkdir(parents=True, exist_ok=True)
            dest = self._dir / f"{request.label or 'response'}.json"
            dest.write_text(
                json.dumps(response.parsed, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return response
