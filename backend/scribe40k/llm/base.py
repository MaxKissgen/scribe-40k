"""Provider-agnostic types for the two model roles.

The pipeline uses models for two different jobs and keeps them apart, because they have
different requirements and are worth swapping independently:

**OCR** turns a page image into text. Accuracy on handwriting is what matters.

**Reasoning** turns that text -- plus, where the model can see, the page image itself --
into structured JSON. Instruction-following and structured output are what matter.

A provider is anything satisfying :class:`OcrProvider` or :class:`ReasoningProvider`.
Adding one means writing a single file; nothing in the pipeline names a vendor.
"""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PageImage:
    """A rendered page, ready to hand to a model."""

    pdf_page: int
    path: Path
    #: 1-5 when the page was matched to a template page, else ``None``.
    sheet_page: int | None = None
    width: int = 0
    height: int = 0

    @property
    def media_type(self) -> str:
        guessed, _ = mimetypes.guess_type(self.path.name)
        return guessed or "image/png"

    def as_base64(self) -> str:
        return base64.b64encode(self.path.read_bytes()).decode("ascii")

    def as_data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.as_base64()}"

    @property
    def label(self) -> str:
        if self.sheet_page is not None:
            return f"sheet page {self.sheet_page}"
        return f"PDF page {self.pdf_page} (unmatched)"


@dataclass
class OcrPage:
    """One page's transcription."""

    pdf_page: int
    sheet_page: int | None
    #: Layout-preserving text. Markdown where the provider produces it.
    text: str
    provider: str = ""
    model: str = ""
    #: True when this came from the cache rather than a fresh call.
    cached: bool = False
    #: Populated when a provider fails on a single page; the rest still proceed.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ReasoningRequest:
    """One mapping job: a section of the sheet, and the evidence for it."""

    system: str
    user: str
    #: Attached when the provider reports vision support. On this sheet the ticked boxes
    #: carry most of the data, and OCR renders them all as identical glyphs, so a
    #: text-only provider will under-report them.
    images: Sequence[PageImage] = field(default_factory=tuple)
    #: JSON Schema for the expected reply, used for structured output where supported and
    #: as prompt text otherwise.
    json_schema: dict | None = None
    #: Identifies the job in logs and cache keys.
    label: str = ""
    max_tokens: int = 8192
    temperature: float = 0.0


@dataclass
class ReasoningResponse:
    text: str
    #: The reply parsed as JSON, when it could be.
    parsed: dict | list | None = None
    provider: str = ""
    model: str = ""
    cached: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.parsed is not None


@runtime_checkable
class OcrProvider(Protocol):
    """Turns page images into text."""

    name: str
    model: str

    def transcribe(self, pages: Sequence[PageImage]) -> list[OcrPage]:
        """Transcribe pages, one result per input, in the same order.

        A provider must not raise for a single bad page: it returns an ``OcrPage`` with
        ``error`` set, so one unreadable page cannot lose the other four.
        """
        ...


@runtime_checkable
class ReasoningProvider(Protocol):
    """Turns text and images into structured JSON."""

    name: str
    model: str
    #: Whether page images can be attached to a request.
    supports_vision: bool
    #: Whether the provider can be constrained to a JSON Schema natively.
    supports_structured_output: bool

    def complete(self, request: ReasoningRequest) -> ReasoningResponse: ...


class ProviderError(RuntimeError):
    """A provider could not be constructed or configured."""


class MissingCredentialError(ProviderError):
    """A provider needs an API key that is not in the environment."""

    def __init__(self, provider: str, env_var: str) -> None:
        super().__init__(
            f"The '{provider}' provider needs an API key. Set {env_var} in your environment "
            f"or in a .env file. Keys are never read from config.toml."
        )
        self.provider = provider
        self.env_var = env_var
