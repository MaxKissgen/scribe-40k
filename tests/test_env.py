"""Loading credentials from `.env`.

This exists because it was missing: `.env.example` was shipped, the README said to copy
it, and an error message even mentioned "a .env file" -- but nothing ever read one, so a
key sat in the file and the tool reported no credentials.
"""

from __future__ import annotations

import os

import pytest

from scribe40k import load_env
from scribe40k.llm.config import StageConfig


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("MISTRAL_API_KEY", "OPENAI_API_KEY", "SOME_VAR", "QUOTED", "EXPORTED"):
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path, body: str):
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


class TestLoadEnv:
    def test_a_plain_assignment_reaches_the_environment(self, tmp_path) -> None:
        applied = load_env(write_env(tmp_path, "MISTRAL_API_KEY=abc123\n"))

        assert applied == ["MISTRAL_API_KEY"]
        assert os.environ["MISTRAL_API_KEY"] == "abc123"

    def test_the_provider_then_finds_it(self, tmp_path) -> None:
        """The whole point: config.api_key() is what actually gates a provider."""
        load_env(write_env(tmp_path, "MISTRAL_API_KEY=abc123\n"))

        assert StageConfig(provider="mistral", model="m").api_key() == "abc123"

    def test_comments_and_blank_lines_are_skipped(self, tmp_path) -> None:
        applied = load_env(
            write_env(tmp_path, "# a comment\n\n   \nMISTRAL_API_KEY=abc\n# another\n")
        )

        assert applied == ["MISTRAL_API_KEY"]

    def test_surrounding_quotes_are_stripped(self, tmp_path) -> None:
        load_env(write_env(tmp_path, 'QUOTED="wrapped in quotes"\n'))

        assert os.environ["QUOTED"] == "wrapped in quotes"

    def test_a_leading_export_is_tolerated(self, tmp_path) -> None:
        """People paste these straight out of a shell."""
        load_env(write_env(tmp_path, "export EXPORTED=yes\n"))

        assert os.environ["EXPORTED"] == "yes"

    def test_an_empty_value_is_ignored(self, tmp_path) -> None:
        """.env.example ships with every key blank; those must not mask a real one."""
        applied = load_env(write_env(tmp_path, "MISTRAL_API_KEY=\nOPENAI_API_KEY=real\n"))

        assert applied == ["OPENAI_API_KEY"]
        assert "MISTRAL_API_KEY" not in os.environ

    def test_the_real_environment_wins_by_default(self, tmp_path, monkeypatch) -> None:
        """So `MISTRAL_API_KEY=... scribe extract ...` still overrides the file."""
        monkeypatch.setenv("MISTRAL_API_KEY", "from-the-shell")

        applied = load_env(write_env(tmp_path, "MISTRAL_API_KEY=from-the-file\n"))

        assert applied == []
        assert os.environ["MISTRAL_API_KEY"] == "from-the-shell"

    def test_override_is_available_when_asked_for(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "from-the-shell")

        load_env(write_env(tmp_path, "MISTRAL_API_KEY=from-the-file\n"), override=True)

        assert os.environ["MISTRAL_API_KEY"] == "from-the-file"

    def test_a_missing_file_is_not_an_error(self, tmp_path) -> None:
        assert load_env(tmp_path / "absent.env") == []

    def test_a_malformed_line_does_not_stop_the_rest(self, tmp_path) -> None:
        applied = load_env(write_env(tmp_path, "this line has no equals\nMISTRAL_API_KEY=abc\n"))

        assert applied == ["MISTRAL_API_KEY"]

    def test_values_containing_equals_survive_intact(self, tmp_path) -> None:
        load_env(write_env(tmp_path, "SOME_VAR=a=b=c\n"))

        assert os.environ["SOME_VAR"] == "a=b=c"


class TestHealthReportsCredentials:
    """A key that is absent should say so, not fail silently four steps later."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        import importlib

        monkeypatch.setenv("SCRIBE40K_DATA", str(tmp_path))
        import scribe40k.api as api

        importlib.reload(api)
        from fastapi.testclient import TestClient

        return TestClient(api.app)

    def test_a_missing_key_is_reported(self, client, tmp_path, monkeypatch) -> None:
        config = tmp_path / "config.toml"
        config.write_text('[ocr]\nprovider = "mistral"\nmodel = "m"\n', encoding="utf-8")
        monkeypatch.setattr("scribe40k.llm.config.CONFIG_FILE", config)
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

        health = client.get("/api/health").json()

        assert health["status"] == "missing_credentials"
        assert health["ocr"]["credentialFound"] is False
        assert health["ocr"]["envVar"] == "MISTRAL_API_KEY"

    def test_a_present_key_is_reported_without_leaking_it(
        self, client, tmp_path, monkeypatch
    ) -> None:
        config = tmp_path / "config.toml"
        config.write_text(
            '[ocr]\nprovider = "mistral"\nmodel = "m"\n'
            '[reasoning]\nprovider = "mistral"\nmodel = "m"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("scribe40k.llm.config.CONFIG_FILE", config)
        monkeypatch.setenv("MISTRAL_API_KEY", "super-secret")

        health = client.get("/api/health").json()

        assert health["status"] == "ok"
        assert health["ocr"]["credentialFound"] is True
        assert "super-secret" not in client.get("/api/health").text

    def test_offline_providers_need_no_credential(self, client, tmp_path, monkeypatch) -> None:
        config = tmp_path / "config.toml"
        config.write_text(
            '[ocr]\nprovider = "fixture"\n[reasoning]\nprovider = "fixture"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("scribe40k.llm.config.CONFIG_FILE", config)

        health = client.get("/api/health").json()

        assert health["status"] == "ok"
        assert health["ocr"]["envVar"] is None
