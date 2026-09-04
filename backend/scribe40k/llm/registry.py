"""Turning configuration into provider instances.

This is the only module that knows vendor names. The pipeline asks for "the OCR provider"
and "the reasoning provider" and gets something satisfying the protocols in
:mod:`scribe40k.llm.base`.

Adding a provider is a two-line change here plus one new file.
"""

from __future__ import annotations

from .base import OcrProvider, ProviderError, ReasoningProvider
from .cache import CachedOcr, CachedReasoning
from .config import Config, StageConfig
from .offline import FixtureOcr, FixtureReasoning, PassthroughOcr

#: Provider names that route to the OpenAI-compatible implementation, with the base URL
#: each one implies. ``None`` means "take it from config, or use the OpenAI default".
OPENAI_COMPATIBLE: dict[str, str | None] = {
    "openai": None,
    "openai_compat": None,
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
    "azure": None,
}

#: Providers that make no network call, and therefore need no credential.
OFFLINE_PROVIDERS = frozenset({"passthrough", "fixture"})

OCR_PROVIDERS = ("mistral", "anthropic", "passthrough", "fixture", *OPENAI_COMPATIBLE)
REASONING_PROVIDERS = ("mistral", "anthropic", "fixture", *OPENAI_COMPATIBLE)


def _resolved(config: StageConfig) -> StageConfig:
    """Fill in the base URL implied by the provider name, if the user gave none."""
    if config.base_url is None and config.provider in OPENAI_COMPATIBLE:
        implied = OPENAI_COMPATIBLE[config.provider]
        if implied:
            config.base_url = implied
    return config


def build_ocr_provider(config: StageConfig, *, cache: bool = True) -> OcrProvider:
    provider = config.provider.lower()
    config = _resolved(config)

    if provider == "passthrough":
        # No network call, so caching would only add indirection.
        return PassthroughOcr(config)
    if provider == "fixture":
        return FixtureOcr(config)

    if provider == "mistral":
        from .mistral import MistralOcr

        built: OcrProvider = MistralOcr(config)
    elif provider == "anthropic":
        from .anthropic_provider import AnthropicOcr

        built = AnthropicOcr(config)
    elif provider in OPENAI_COMPATIBLE:
        from .openai_compat import OpenAiCompatOcr

        built = OpenAiCompatOcr(config)
    else:
        raise ProviderError(
            f"Unknown OCR provider '{config.provider}'. "
            f"Available: {', '.join(sorted(OCR_PROVIDERS))}"
        )

    return CachedOcr(built) if cache else built


def build_reasoning_provider(config: StageConfig, *, cache: bool = True) -> ReasoningProvider:
    provider = config.provider.lower()
    config = _resolved(config)

    if provider == "fixture":
        return FixtureReasoning(config)

    if provider == "mistral":
        from .mistral import MistralReasoning

        built: ReasoningProvider = MistralReasoning(config)
    elif provider == "anthropic":
        from .anthropic_provider import AnthropicReasoning

        built = AnthropicReasoning(config)
    elif provider in OPENAI_COMPATIBLE:
        from .openai_compat import OpenAiCompatReasoning

        built = OpenAiCompatReasoning(config)
    else:
        raise ProviderError(
            f"Unknown reasoning provider '{config.provider}'. "
            f"Available: {', '.join(sorted(REASONING_PROVIDERS))}"
        )

    return CachedReasoning(built) if cache else built


def build_providers(config: Config) -> tuple[OcrProvider, ReasoningProvider]:
    return (
        build_ocr_provider(config.ocr, cache=config.cache),
        build_reasoning_provider(config.reasoning, cache=config.cache),
    )
