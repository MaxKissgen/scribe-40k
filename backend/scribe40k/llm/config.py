"""Provider selection, from ``config.toml`` plus the environment.

Two rules:

* Which model to use is configuration, and lives in ``config.toml``.
* API keys are secrets, and live only in the environment. Nothing here ever reads a key
  out of the config file, so the file is safe to commit.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import ROOT

CONFIG_FILE = ROOT / "config.toml"

#: Where each provider's key is expected to live.
ENV_VARS: dict[str, str] = {
    "mistral": "MISTRAL_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai_compat": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}


@dataclass
class StageConfig:
    """How one pipeline stage should call a model."""

    provider: str
    model: str = ""
    base_url: str | None = None
    #: Overrides the default env var for this provider.
    api_key_env: str | None = None
    timeout: float = 180.0
    max_retries: int = 3
    #: Provider-specific extras, passed through untouched.
    options: dict = field(default_factory=dict)

    @property
    def env_var(self) -> str:
        return self.api_key_env or ENV_VARS.get(self.provider, "SCRIBE40K_API_KEY")

    def api_key(self) -> str | None:
        return os.environ.get(self.env_var) or None


@dataclass
class Config:
    ocr: StageConfig
    reasoning: StageConfig
    #: Cache OCR and mapping results on disk, keyed by input and model.
    cache: bool = True

    @classmethod
    def defaults(cls) -> Config:
        return cls(
            ocr=StageConfig(provider="mistral", model="mistral-ocr-latest"),
            reasoning=StageConfig(provider="anthropic", model="claude-opus-5"),
        )


def _stage_from_table(table: dict, fallback: StageConfig) -> StageConfig:
    known = {"provider", "model", "base_url", "api_key_env", "timeout", "max_retries"}
    return StageConfig(
        provider=table.get("provider", fallback.provider),
        model=table.get("model", fallback.model),
        base_url=table.get("base_url", fallback.base_url),
        api_key_env=table.get("api_key_env", fallback.api_key_env),
        timeout=float(table.get("timeout", fallback.timeout)),
        max_retries=int(table.get("max_retries", fallback.max_retries)),
        options={k: v for k, v in table.items() if k not in known},
    )


def load_config(path: Path | None = None) -> Config:
    """Read ``config.toml``, falling back to defaults for anything absent."""
    config = Config.defaults()
    path = path or CONFIG_FILE
    if not path.exists():
        return config

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    config.ocr = _stage_from_table(data.get("ocr", {}), config.ocr)
    config.reasoning = _stage_from_table(data.get("reasoning", {}), config.reasoning)
    config.cache = bool(data.get("cache", {}).get("enabled", config.cache))
    return config


def apply_overrides(
    config: Config,
    *,
    ocr_provider: str | None = None,
    ocr_model: str | None = None,
    reasoning_provider: str | None = None,
    reasoning_model: str | None = None,
    cache: bool | None = None,
) -> Config:
    """Apply command-line overrides on top of the file."""
    if ocr_provider:
        config.ocr.provider = ocr_provider
    if ocr_model:
        config.ocr.model = ocr_model
    if reasoning_provider:
        config.reasoning.provider = reasoning_provider
    if reasoning_model:
        config.reasoning.model = reasoning_model
    if cache is not None:
        config.cache = cache
    return config
