from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.core.model_profiles import resolve_model_profile


def test_resolve_model_profile_from_config_dict_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")

    config = resolve_model_profile(
        "judge",
        config_dict={
            "models": {
                "judge": {
                    "provider": "anthropic",
                    "model": "claude-sonnet",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "temperature": 0.2,
                }
            }
        },
    )

    assert config.llm_provider == "anthropic"
    assert config.llm_model_name == "claude-sonnet"
    assert config.llm_api_key == "anthropic-secret"
    assert config.llm_temperature == 0.2


def test_resolve_model_profile_raises_for_missing_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyConfig:
        def load_config(self):
            return {"models": {}}

    monkeypatch.setattr("aworld_cli.core.config.get_config", lambda: EmptyConfig())

    with pytest.raises(KeyError):
        resolve_model_profile("missing", config_dict={"models": {}})
