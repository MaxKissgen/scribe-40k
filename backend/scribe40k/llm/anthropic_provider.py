"""Anthropic provider, via the official ``anthropic`` SDK.

Kept separate from the OpenAI-compatible path deliberately: the Messages API has its own
request shape (``output_config.format`` for structured output, adaptive thinking, image
blocks with an explicit ``media_type``), and going through the SDK gets retries, typed
errors and streaming without reimplementing them.

The SDK is an optional dependency -- install with ``pip install -e ".[anthropic]"`` -- so
that a Mistral-only or local-model deployment need not pull it in.
"""

from __future__ import annotations

from collections.abc import Sequence

from .base import (
    MissingCredentialError,
    OcrPage,
    PageImage,
    ProviderError,
    ReasoningRequest,
    ReasoningResponse,
)
from .config import StageConfig
from .json_utils import extract_json
from .openai_compat import TRANSCRIBE_SYSTEM

DEFAULT_MODEL = "claude-opus-5"

#: Above this, the SDK wants streaming so a long reply cannot hit the HTTP timeout.
STREAMING_THRESHOLD = 16000


def _require_sdk():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on the install extras
        raise ProviderError(
            "The Anthropic provider needs the official SDK. Install it with:\n"
            '    pip install -e ".[anthropic]"'
        ) from exc
    return anthropic


class _AnthropicBase:
    def __init__(self, config: StageConfig) -> None:
        anthropic = _require_sdk()

        self.name = "anthropic"
        self.model = config.model or DEFAULT_MODEL
        self._config = config
        self._sdk = anthropic

        key = config.api_key()
        # A bare client also resolves an `ant auth login` profile, so an unset env var is
        # not on its own a failure. Only bail if neither is available.
        try:
            self._client = (
                anthropic.Anthropic(api_key=key, timeout=config.timeout)
                if key
                else anthropic.Anthropic(timeout=config.timeout)
            )
        except Exception as exc:
            raise MissingCredentialError("anthropic", config.env_var) from exc

    def _image_block(self, page: PageImage) -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": page.media_type,
                "data": page.as_base64(),
            },
        }

    def _send(self, *, system: str, content: list[dict], max_tokens: int) -> tuple[str, dict]:
        """One Messages API call, streaming when the reply could be long."""
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }

        if max_tokens > STREAMING_THRESHOLD:
            with self._client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        else:
            message = self._client.messages.create(**kwargs)

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            raise ProviderError(f"the model declined this request (category: {category})")

        text = "".join(block.text for block in message.content if block.type == "text")
        usage = {
            "prompt_tokens": message.usage.input_tokens,
            "completion_tokens": message.usage.output_tokens,
        }
        return text, usage


class AnthropicOcr(_AnthropicBase):
    """Transcription by prompting Claude's vision."""

    def transcribe(self, pages: Sequence[PageImage]) -> list[OcrPage]:
        results: list[OcrPage] = []
        for page in pages:
            try:
                text, _ = self._send(
                    system=TRANSCRIBE_SYSTEM,
                    content=[
                        self._image_block(page),
                        {"type": "text", "text": f"Transcribe this page ({page.label})."},
                    ],
                    max_tokens=8192,
                )
                error = None
            except Exception as exc:
                text, error = "", str(exc)

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


class AnthropicReasoning(_AnthropicBase):
    """Mapping via the Messages API."""

    def __init__(self, config: StageConfig) -> None:
        super().__init__(config)
        self.supports_vision = True
        # The Messages API does support constrained output via `output_config.format`,
        # but this provider does not use it: the mapping prompts already carry the schema
        # as text, and `extract_json` recovers the payload from a fenced or prefaced
        # reply. Declaring False means the schema is included in the prompt, which is the
        # behaviour we want. Worth revisiting if reply-shape errors show up in practice.
        self.supports_structured_output = False

    def complete(self, request: ReasoningRequest) -> ReasoningResponse:
        content: list[dict] = [self._image_block(page) for page in request.images]
        content.append({"type": "text", "text": request.user})

        try:
            text, usage = self._send(
                system=request.system,
                content=content,
                max_tokens=request.max_tokens,
            )
        except Exception as exc:
            return ReasoningResponse(text="", provider=self.name, model=self.model, error=str(exc))

        return ReasoningResponse(
            text=text,
            parsed=extract_json(text),
            provider=self.name,
            model=self.model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
