"""The provider layer: configuration, JSON recovery, caching, and the registry.

No test here makes a network call. The point of the abstraction is that the pipeline can
be exercised without one.
"""

from __future__ import annotations

import json

import pytest

from scribe40k.llm.base import (
    MissingCredentialError,
    OcrPage,
    PageImage,
    ProviderError,
    ReasoningRequest,
    ReasoningResponse,
)
from scribe40k.llm.cache import CachedOcr, CachedReasoning
from scribe40k.llm.config import Config, StageConfig, apply_overrides, load_config
from scribe40k.llm.json_utils import extract_json
from scribe40k.llm.offline import FixtureOcr, FixtureReasoning, PassthroughOcr, RecordingReasoning
from scribe40k.llm.registry import build_ocr_provider, build_reasoning_provider


@pytest.fixture
def page(tmp_path):
    path = tmp_path / "page-01.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pretend image bytes")
    return PageImage(pdf_page=1, path=path, sheet_page=1, width=100, height=200)


class TestConfig:
    def test_defaults_when_no_file(self, tmp_path) -> None:
        config = load_config(tmp_path / "absent.toml")
        assert config.ocr.provider == "mistral"
        assert config.reasoning.model == "claude-opus-5"

    def test_reads_a_file(self, tmp_path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(
            '[ocr]\nprovider = "openai"\nmodel = "gpt-5"\n'
            '[reasoning]\nprovider = "ollama"\nmodel = "llama3.2-vision"\n'
            "[cache]\nenabled = false\n",
            encoding="utf-8",
        )

        config = load_config(path)

        assert (config.ocr.provider, config.ocr.model) == ("openai", "gpt-5")
        assert config.reasoning.provider == "ollama"
        assert config.cache is False

    def test_unknown_keys_become_provider_options(self, tmp_path) -> None:
        path = tmp_path / "config.toml"
        path.write_text('[reasoning]\nprovider = "openai"\nvision = false\n', encoding="utf-8")

        assert load_config(path).reasoning.options == {"vision": False}

    def test_cli_overrides_beat_the_file(self) -> None:
        config = apply_overrides(Config.defaults(), reasoning_model="gpt-5", cache=False)
        assert config.reasoning.model == "gpt-5"
        assert config.cache is False

    def test_keys_come_from_the_environment_only(self, monkeypatch) -> None:
        config = StageConfig(provider="mistral", model="m")
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        assert config.api_key() is None

        monkeypatch.setenv("MISTRAL_API_KEY", "secret")
        assert config.api_key() == "secret"

    def test_a_custom_env_var_can_be_named(self, monkeypatch) -> None:
        monkeypatch.setenv("MY_OWN_KEY", "abc")
        config = StageConfig(provider="openai", api_key_env="MY_OWN_KEY")
        assert config.api_key() == "abc"


class TestExtractJson:
    def test_plain_json(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_block(self) -> None:
        assert extract_json('Here you go:\n```json\n{"a": 1}\n```\nHope that helps.') == {"a": 1}

    def test_unlabelled_fence(self) -> None:
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_prose_around_bare_json(self) -> None:
        assert extract_json('Sure. {"a": [1, 2]} Done.') == {"a": [1, 2]}

    def test_arrays(self) -> None:
        assert extract_json("[1, 2, 3]") == [1, 2, 3]

    def test_braces_inside_strings_do_not_confuse_it(self) -> None:
        assert extract_json('{"note": "a } brace"}') == {"note": "a } brace"}

    def test_nested_objects(self) -> None:
        assert extract_json('prefix {"a": {"b": {"c": 1}}} suffix') == {"a": {"b": {"c": 1}}}

    @pytest.mark.parametrize("text", ["", "   ", "no json here", "{unclosed"])
    def test_returns_none_when_nothing_parses(self, text: str) -> None:
        assert extract_json(text) is None


class TestPassthroughOcr:
    def test_uses_the_embedded_text(self, page) -> None:
        provider = PassthroughOcr()

        class FakeIngested:
            pdf_page = 1
            embedded_text = "Character Name: Aldleg"

        provider.load_from_ingest([FakeIngested()])
        [result] = provider.transcribe([page])

        assert result.text == "Character Name: Aldleg"
        assert result.ok

    def test_a_page_with_no_text_layer_reports_an_error(self, page) -> None:
        [result] = PassthroughOcr().transcribe([page])
        assert not result.ok
        assert "no text layer" in result.error


class TestFixtureProviders:
    def test_ocr_replays_from_disk(self, tmp_path, page) -> None:
        fixtures = tmp_path / "ocr"
        fixtures.mkdir()
        (fixtures / "page-01.txt").write_text("transcribed", encoding="utf-8")

        provider = FixtureOcr(StageConfig(provider="fixture", options={"path": str(fixtures)}))
        [result] = provider.transcribe([page])

        assert result.text == "transcribed"

    def test_reasoning_replays_by_label(self, tmp_path) -> None:
        fixtures = tmp_path / "reasoning"
        fixtures.mkdir()
        (fixtures / "skills.json").write_text('{"fields": []}', encoding="utf-8")

        provider = FixtureReasoning(
            StageConfig(provider="fixture", options={"path": str(fixtures)})
        )
        response = provider.complete(ReasoningRequest(system="s", user="u", label="skills"))

        assert response.parsed == {"fields": []}

    def test_a_missing_fixture_is_an_error_not_a_crash(self, tmp_path) -> None:
        provider = FixtureReasoning(
            StageConfig(provider="fixture", options={"path": str(tmp_path)})
        )
        response = provider.complete(ReasoningRequest(system="s", user="u", label="absent"))

        assert not response.ok
        assert "no fixture" in response.error


class CountingOcr:
    """Records how many pages it was actually asked to transcribe."""

    name = "counting"
    model = "test"

    def __init__(self) -> None:
        self.calls: list[list[int]] = []

    def transcribe(self, pages):
        self.calls.append([p.pdf_page for p in pages])
        return [
            OcrPage(
                pdf_page=p.pdf_page,
                sheet_page=p.sheet_page,
                text=f"page {p.pdf_page}",
                provider=self.name,
                model=self.model,
            )
            for p in pages
        ]


class FailingOcr:
    name = "failing"
    model = "test"

    def transcribe(self, pages):
        return [
            OcrPage(
                pdf_page=p.pdf_page,
                sheet_page=p.sheet_page,
                text="",
                provider=self.name,
                model=self.model,
                error="upstream exploded",
            )
            for p in pages
        ]


class TestOcrCache:
    def test_second_run_makes_no_call(self, tmp_path, page) -> None:
        inner = CountingOcr()
        cached = CachedOcr(inner, tmp_path / "cache")

        first = cached.transcribe([page])
        second = cached.transcribe([page])

        assert first[0].text == second[0].text == "page 1"
        assert first[0].cached is False
        assert second[0].cached is True
        assert inner.calls == [[1]], "the provider should have been asked exactly once"

    def test_only_uncached_pages_are_requested(self, tmp_path, page) -> None:
        second_page = PageImage(pdf_page=3, path=tmp_path / "p3.png", sheet_page=2)
        second_page.path.write_bytes(b"different bytes")

        inner = CountingOcr()
        cached = CachedOcr(inner, tmp_path / "cache")

        cached.transcribe([page])
        cached.transcribe([page, second_page])

        assert inner.calls == [[1], [3]]

    def test_results_keep_the_requested_order(self, tmp_path, page) -> None:
        second_page = PageImage(pdf_page=3, path=tmp_path / "p3.png", sheet_page=2)
        second_page.path.write_bytes(b"different bytes")
        cached = CachedOcr(CountingOcr(), tmp_path / "cache")

        cached.transcribe([page])
        results = cached.transcribe([second_page, page])

        assert [r.pdf_page for r in results] == [3, 1]

    def test_failures_are_not_cached(self, tmp_path, page) -> None:
        """A transient outage must not poison the cache for every later run."""
        cached = CachedOcr(FailingOcr(), tmp_path / "cache")

        cached.transcribe([page])

        assert not list((tmp_path / "cache").glob("*.json"))

    def test_identical_content_under_a_new_name_still_hits(self, tmp_path, page) -> None:
        inner = CountingOcr()
        cached = CachedOcr(inner, tmp_path / "cache")
        cached.transcribe([page])

        renamed = tmp_path / "renamed.png"
        renamed.write_bytes(page.path.read_bytes())
        [result] = cached.transcribe([PageImage(pdf_page=1, path=renamed, sheet_page=1)])

        assert result.cached is True
        assert inner.calls == [[1]]


class ScriptedReasoning:
    name = "scripted"
    model = "test"
    supports_vision = True
    supports_structured_output = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        text = json.dumps(self.payload)
        return ReasoningResponse(
            text=text, parsed=self.payload, provider=self.name, model=self.model
        )


class TestReasoningCache:
    def test_identical_requests_hit(self, tmp_path) -> None:
        inner = ScriptedReasoning({"ok": True})
        cached = CachedReasoning(inner, tmp_path / "cache")
        request = ReasoningRequest(system="s", user="u", label="bio")

        cached.complete(request)
        second = cached.complete(request)

        assert second.cached is True
        assert inner.calls == 1

    def test_a_changed_prompt_misses(self, tmp_path) -> None:
        inner = ScriptedReasoning({"ok": True})
        cached = CachedReasoning(inner, tmp_path / "cache")

        cached.complete(ReasoningRequest(system="s", user="u", label="bio"))
        cached.complete(ReasoningRequest(system="s", user="different", label="bio"))

        assert inner.calls == 2

    def test_a_changed_image_misses(self, tmp_path, page) -> None:
        other = tmp_path / "other.png"
        other.write_bytes(b"other bytes")
        inner = ScriptedReasoning({"ok": True})
        cached = CachedReasoning(inner, tmp_path / "cache")

        cached.complete(ReasoningRequest(system="s", user="u", label="bio", images=[page]))
        cached.complete(
            ReasoningRequest(
                system="s",
                user="u",
                label="bio",
                images=[PageImage(pdf_page=1, path=other)],
            )
        )

        assert inner.calls == 2


class TestRecording:
    def test_writes_a_replayable_fixture(self, tmp_path) -> None:
        recorder = RecordingReasoning(ScriptedReasoning({"value": 42}), tmp_path)

        recorder.complete(ReasoningRequest(system="s", user="u", label="advances"))

        written = tmp_path / "advances.json"
        assert json.loads(written.read_text(encoding="utf-8")) == {"value": 42}

        replayed = FixtureReasoning(
            StageConfig(provider="fixture", options={"path": str(tmp_path)})
        )
        assert replayed.complete(
            ReasoningRequest(system="s", user="u", label="advances")
        ).parsed == {"value": 42}


class TestRegistry:
    def test_builds_offline_providers(self) -> None:
        assert build_ocr_provider(StageConfig(provider="fixture"), cache=False).name == "fixture"
        assert build_ocr_provider(StageConfig(provider="passthrough")).name == "passthrough"

    def test_unknown_provider_lists_the_alternatives(self) -> None:
        with pytest.raises(ProviderError, match="Unknown OCR provider"):
            build_ocr_provider(StageConfig(provider="nonesuch"))

    def test_a_missing_key_names_the_variable_to_set(self, monkeypatch) -> None:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

        with pytest.raises(MissingCredentialError) as excinfo:
            build_ocr_provider(StageConfig(provider="mistral", model="mistral-ocr-latest"))

        assert "MISTRAL_API_KEY" in str(excinfo.value)
        assert "config.toml" in str(excinfo.value)

    def test_local_runtimes_need_no_key(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        provider = build_reasoning_provider(
            StageConfig(provider="ollama", model="llama3.2-vision"), cache=False
        )

        assert provider.name == "ollama"

    def test_provider_names_imply_a_base_url(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        config = StageConfig(provider="openrouter", model="openai/gpt-5")

        build_reasoning_provider(config, cache=False)

        assert config.base_url == "https://openrouter.ai/api/v1"

    def test_caching_can_be_turned_off(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        stage = StageConfig(provider="openai", model="gpt-5")

        assert isinstance(build_reasoning_provider(stage, cache=True), CachedReasoning)
        assert not isinstance(build_reasoning_provider(stage, cache=False), CachedReasoning)


class TestPageImage:
    def test_data_url_carries_the_media_type(self, page) -> None:
        assert page.as_data_url().startswith("data:image/png;base64,")

    def test_label_distinguishes_matched_from_unmatched(self, tmp_path) -> None:
        path = tmp_path / "x.png"
        path.write_bytes(b"x")
        assert PageImage(pdf_page=3, path=path, sheet_page=2).label == "sheet page 2"
        assert "unmatched" in PageImage(pdf_page=7, path=path).label
