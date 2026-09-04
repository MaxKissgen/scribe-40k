"""Mistral provider: the dedicated OCR endpoint, and chat for reasoning.

Mistral is the only vendor here with a purpose-built document-understanding endpoint
(``/v1/ocr``), which returns layout-aware markdown per page rather than a flat transcript.
That is worth more on this sheet than a general vision model's prose description, because
the three-column skills grid loses its meaning once the columns are interleaved.
"""

from __future__ import annotations

from collections.abc import Sequence

from ._http import HttpProviderError, post_json
from .base import (
    MissingCredentialError,
    OcrPage,
    PageImage,
    ReasoningRequest,
    ReasoningResponse,
)
from .config import StageConfig
from .json_utils import extract_json

DEFAULT_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT_OCR_MODEL = "mistral-ocr-latest"
DEFAULT_CHAT_MODEL = "mistral-large-latest"


class MistralOcr:
    """Transcription via Mistral's document OCR endpoint."""

    def __init__(self, config: StageConfig) -> None:
        self.name = "mistral"
        self.model = config.model or DEFAULT_OCR_MODEL
        self._base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self._config = config

        key = config.api_key()
        if not key:
            raise MissingCredentialError("mistral", config.env_var)
        self._headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def transcribe(self, pages: Sequence[PageImage]) -> list[OcrPage]:
        results: list[OcrPage] = []
        for page in pages:
            try:
                text = self._transcribe_one(page)
                error = None
            except (HttpProviderError, KeyError, ValueError) as exc:
                # One unreadable page must not lose the other four.
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

    def _transcribe_one(self, page: PageImage) -> str:
        payload = {
            "model": self.model,
            "document": {"type": "image_url", "image_url": page.as_data_url()},
            "include_image_base64": False,
        }
        data = post_json(
            f"{self._base_url}/ocr",
            payload,
            headers=self._headers,
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
        )
        return "\n\n".join(p.get("markdown", "") for p in data.get("pages", [])).strip()


class MistralReasoning:
    """Chat completions, used for the mapping stage."""

    def __init__(self, config: StageConfig) -> None:
        self.name = "mistral"
        self.model = config.model or DEFAULT_CHAT_MODEL
        self.supports_vision = "pixtral" in self.model or "medium" in self.model
        self.supports_structured_output = True
        self._base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self._config = config

        key = config.api_key()
        if not key:
            raise MissingCredentialError("mistral", config.env_var)
        self._headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def complete(self, request: ReasoningRequest) -> ReasoningResponse:
        content: list[dict] = [{"type": "text", "text": request.user}]
        if self.supports_vision:
            content = [
                {"type": "image_url", "image_url": page.as_data_url()} for page in request.images
            ] + content

        payload: dict = {
            "model": self.model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ],
        }
        if request.json_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        try:
            data = post_json(
                f"{self._base_url}/chat/completions",
                payload,
                headers=self._headers,
                timeout=self._config.timeout,
                max_retries=self._config.max_retries,
            )
        except HttpProviderError as exc:
            return ReasoningResponse(text="", provider=self.name, model=self.model, error=str(exc))

        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        return ReasoningResponse(
            text=text,
            parsed=extract_json(text),
            provider=self.name,
            model=self.model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
