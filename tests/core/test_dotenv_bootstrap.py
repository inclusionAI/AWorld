from __future__ import annotations

from pathlib import Path

from aworld.cli.main import main
from aworld.cli.model_config import _fallback_dotenv_values, resolve_model_settings


_CONFIG_KEYS = (
    "AWORLD_MODEL", "OPENAI_MODEL", "LLM_MODEL_NAME", "AWORLD_MODEL_PROFILE",
    "AWORLD_BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL",
    "AWORLD_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY",
)


def _capture_settings(monkeypatch):
    captured = {}

    async def host(args, command):
        captured["settings"] = resolve_model_settings(args, home=Path("/nonexistent"))
        return 0

    monkeypatch.setattr("aworld.cli.main._host", host)
    return captured


def test_cli_loads_model_configuration_from_project_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        'LLM_MODEL_NAME="dotenv-model"\n'
        'LLM_BASE_URL="http://localhost:9000/v1"\n'
        'LLM_API_KEY="dotenv-key"\n',
        encoding="utf-8",
    )
    for key in _CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    captured = _capture_settings(monkeypatch)

    assert main(["--cwd", str(tmp_path)]) == 0

    settings = captured["settings"]
    assert (settings.model, settings.base_url, settings.api_key) == (
        "dotenv-model", "http://localhost:9000/v1", "dotenv-key"
    )


def test_explicit_model_and_exported_environment_override_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LLM_MODEL_NAME=dotenv-model\n", encoding="utf-8")
    for key in _CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWORLD_MODEL", "exported-model")
    captured = _capture_settings(monkeypatch)

    assert main(["--cwd", str(tmp_path)]) == 0
    assert captured["settings"].model == "exported-model"

    captured = _capture_settings(monkeypatch)
    assert main(["--cwd", str(tmp_path), "--model", "cli-model"]) == 0

    assert captured["settings"].model == "cli-model"


def test_cli_can_disable_automatic_dotenv_loading(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LLM_MODEL_NAME=dotenv-model\n", encoding="utf-8")
    for key in _CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWORLD_DISABLE_AUTO_DOTENV", "1")
    captured = _capture_settings(monkeypatch)

    assert main(["--cwd", str(tmp_path)]) == 0

    assert captured["settings"].model is None


def test_dependency_free_dotenv_fallback_supports_quotes_and_references(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        'LLM_MODEL_NAME="quoted-model"\n'
        "MODEL_HOST=localhost # local endpoint\n"
        "LLM_BASE_URL=http://${MODEL_HOST}:9000/v1\n",
        encoding="utf-8",
    )

    assert _fallback_dotenv_values(path, {}) == {
        "LLM_MODEL_NAME": "quoted-model",
        "MODEL_HOST": "localhost",
        "LLM_BASE_URL": "http://localhost:9000/v1",
    }
