"""Any OpenAI-shaped ``/chat/completions`` endpoint.

One implementation covers OpenAI, OpenRouter, Azure OpenAI, Together, Groq, and local
runtimes such as Ollama, vLLM and LM Studio. They differ only in ``base_url``, the model
name, and which env var holds the key -- all of which are configuration.

Serves both roles: as an OCR provider it prompts a vision model to transcribe the page; as
a reasoning provider it does the mapping.
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

DEFAULT_BASE_URL = "https://api.openai.com/v1"

TRANSCRIBE_SYSTEM = """\
You transcribe scanned tabletop RPG character sheets.

Reproduce everything you can read on the page, preserving the layout as closely as plain \
text allows. Rules:

* Keep the reading order of each column. Where the page has several columns, transcribe \
  one column fully before moving to the next, and mark each with a heading.
* Reproduce printed labels as well as handwriting, so that a value can be tied to its field.
* For checkboxes and tick boxes, write [x] for marked and [ ] for unmarked. This matters \
  more than anything else on the page.
* Where handwriting is unclear, give your best reading followed by (?).
* Include text written in the margins or outside its box, and say where it sits.
* Do not summarise, correct, complete or interpret. Transcribe only what is there.
"""

#: Substrings that identify a model as unable to accept images. Everything current is
#: multimodal, so an allowlist would go stale faster than this does.
TEXT_ONLY_HINTS = ("instruct", "-text", "embed", "davinci", "babbage")


def _looks_vision_capable(model: str) -> bool:
    lowered = model.lower()
    return not any(hint in lowered for hint in TEXT_ONLY_HINTS)


class _OpenAiCompatBase:
    def __init__(self, config: StageConfig, *, role: str) -> None:
        self.name = config.provider or "openai_compat"
        self.model = config.model
        self._base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self._config = config

        if not self.model:
            raise ValueError(f"the {role} stage needs a model name for provider '{self.name}'")

        key = config.api_key()
        # Local runtimes (Ollama, LM Studio) accept any token, so a missing key is only
        # fatal when talking to something that is not localhost.
        if not key:
            if not self._is_local():
                raise MissingCredentialError(self.name, config.env_var)
            key = "local"

        self._headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _is_local(self) -> bool:
        return any(host in self._base_url for host in ("localhost", "127.0.0.1", "0.0.0.0"))

    def _chat(self, payload: dict) -> dict:
        return post_json(
            f"{self._base_url}/chat/completions",
            payload,
            headers=self._headers,
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
        )


class OpenAiCompatOcr(_OpenAiCompatBase):
    """Transcription by prompting a vision model."""

    def __init__(self, config: StageConfig) -> None:
        super().__init__(config, role="ocr")

    def transcribe(self, pages: Sequence[PageImage]) -> list[OcrPage]:
        results: list[OcrPage] = []
        for page in pages:
            try:
                text = self._transcribe_one(page)
                error = None
            except (HttpProviderError, KeyError, ValueError) as exc:
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
        data = self._chat(
            {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 8192,
                "messages": [
                    {"role": "system", "content": TRANSCRIBE_SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": page.as_data_url(), "detail": "high"},
                            },
                            {
                                "type": "text",
                                "text": f"Transcribe this page ({page.label}).",
                            },
                        ],
                    },
                ],
            }
        )
        return (data["choices"][0]["message"]["content"] or "").strip()


class OpenAiCompatReasoning(_OpenAiCompatBase):
    """Mapping via chat completions, with structured output where available."""

    def __init__(self, config: StageConfig) -> None:
        super().__init__(config, role="reasoning")
        self.supports_vision = bool(config.options.get("vision", _looks_vision_capable(self.model)))
        self.supports_structured_output = bool(config.options.get("structured_output", True))

    def complete(self, request: ReasoningRequest) -> ReasoningResponse:
        content: list[dict] = []
        if self.supports_vision:
            content += [
                {"type": "image_url", "image_url": {"url": page.as_data_url(), "detail": "high"}}
                for page in request.images
            ]
        content.append({"type": "text", "text": request.user})

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
            if self.supports_structured_output:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.label or "extraction",
                        "strict": False,
                        "schema": request.json_schema,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}

        try:
            data = self._chat(payload)
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
