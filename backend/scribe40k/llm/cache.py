"""On-disk caching for both model roles.

Transcribing five scanned pages is the expensive part of a run, and it is also the part
least likely to need repeating: prompts get iterated on far more often than the OCR under
them. Caching by ``(page content, provider, model)`` means re-running the mapper after a
prompt change costs nothing.

The cache is keyed by content, not filename, so re-uploading the same scan under a
different name is still a hit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from ..paths import cache_root
from .base import OcrPage, PageImage, ReasoningRequest, ReasoningResponse


def _digest(*parts: str) -> str:
    joined = "\x00".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


class CachedOcr:
    """Wraps an OCR provider with a content-addressed disk cache."""

    def __init__(self, inner, directory: Path | None = None) -> None:
        self._inner = inner
        self._dir = Path(directory or cache_root() / "ocr")
        self.name = inner.name
        self.model = inner.model

    def _key(self, page: PageImage) -> Path:
        image_hash = hashlib.sha256(page.path.read_bytes()).hexdigest()[:32]
        return self._dir / f"{_digest(image_hash, self.name, self.model)}.json"

    def transcribe(self, pages: Sequence[PageImage]) -> list[OcrPage]:
        results: dict[int, OcrPage] = {}
        misses: list[PageImage] = []

        for page in pages:
            path = self._key(page)
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                results[page.pdf_page] = OcrPage(
                    pdf_page=page.pdf_page,
                    sheet_page=page.sheet_page,
                    text=payload["text"],
                    provider=payload.get("provider", self.name),
                    model=payload.get("model", self.model),
                    cached=True,
                )
            else:
                misses.append(page)

        for fresh in self._inner.transcribe(misses) if misses else []:
            results[fresh.pdf_page] = fresh
            # Never cache a failure: the next run should get another chance.
            if fresh.ok:
                path = self._key(next(p for p in misses if p.pdf_page == fresh.pdf_page))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {"text": fresh.text, "provider": fresh.provider, "model": fresh.model},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

        return [results[page.pdf_page] for page in pages]


class CachedReasoning:
    """Wraps a reasoning provider, keyed by the full prompt and the attached images."""

    def __init__(self, inner, directory: Path | None = None) -> None:
        self._inner = inner
        self._dir = Path(directory or cache_root() / "reasoning")
        self.name = inner.name
        self.model = inner.model
        self.supports_vision = inner.supports_vision
        self.supports_structured_output = inner.supports_structured_output

    def _key(self, request: ReasoningRequest) -> Path:
        image_hashes = [
            hashlib.sha256(page.path.read_bytes()).hexdigest()[:16] for page in request.images
        ]
        return self._dir / (
            _digest(
                self.name,
                self.model,
                request.label,
                request.system,
                request.user,
                *image_hashes,
            )
            + ".json"
        )

    def complete(self, request: ReasoningRequest) -> ReasoningResponse:
        path = self._key(request)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return ReasoningResponse(
                text=payload["text"],
                parsed=payload.get("parsed"),
                provider=self.name,
                model=self.model,
                cached=True,
            )

        response = self._inner.complete(request)
        if response.ok:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"text": response.text, "parsed": response.parsed}, ensure_ascii=False),
                encoding="utf-8",
            )
        return response
