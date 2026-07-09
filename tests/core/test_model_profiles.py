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


def test_resolve_model_profile_accepts_env_style_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDGE_TOKEN", "judge-token")
    base_url = "https://" + "api." + "example.test"

    config = resolve_model_profile(
        "judge",
        config_dict={
            "models": {
                "judge": {
                    "PROVIDER": "anthropic",
                    "MODEL": "claude-sonnet",
                    "BASE_URL": base_url,
                    "TOKEN_ENV": "JUDGE_TOKEN",
                    "TEMPERATURE": 0,
                }
            }
        },
    )

    assert config.llm_provider == "anthropic"
    assert config.llm_model_name == "claude-sonnet"
    assert config.llm_base_url == base_url
    assert config.llm_api_key == "judge-token"
    assert config.llm_temperature == 0.0


def test_resolve_model_profile_accepts_key_and_token_aliases() -> None:
    key_config = resolve_model_profile(
        "judge",
        config_dict={
            "models": {
                "judge": {
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "key": "key-secret",
                }
            }
        },
    )
    token_config = resolve_model_profile(
        "judge",
        config_dict={
            "models": {
                "judge": {
                    "provider": "anthropic",
                    "model": "claude-sonnet",
                    "token": "token-secret",
                }
            }
        },
    )

    assert key_config.llm_api_key == "key-secret"
    assert token_config.llm_api_key == "token-secret"


def test_resolve_model_profile_raises_for_missing_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyConfig:
        def load_config(self):
            return {"models": {}}

    monkeypatch.setattr("aworld_cli.core.config.get_config", lambda: EmptyConfig())

    with pytest.raises(KeyError):
        resolve_model_profile("missing", config_dict={"models": {}})
