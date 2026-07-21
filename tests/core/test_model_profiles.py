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


def test_resolve_model_profile_from_workspace_aworld_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class EmptyConfig:
        def load_config(self):
            return {"models": {}}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aworld_cli.core.config.get_config", lambda: EmptyConfig())
    config_path = tmp_path / ".aworld" / "aworld.json"
    config_path.parent.mkdir()
    config_path.write_text(
        """
{
  "models": {
    "judge": {
      "PROVIDER": "openai",
      "MODEL": "gpt-5.5",
      "BASE_URL": "https://matrixllm.example.test/v1",
      "api_key": "judge-secret"
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    config = resolve_model_profile("judge")

    assert config.llm_provider == "openai"
    assert config.llm_model_name == "gpt-5.5"
    assert config.llm_base_url == "https://matrixllm.example.test/v1"
    assert config.llm_api_key == "judge-secret"


def test_resolve_model_profile_prefers_workspace_over_global_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class GlobalConfig:
        def load_config(self):
            return {
                "models": {
                    "judge": {
                        "provider": "anthropic",
                        "model": "global-model",
                        "api_key": "global-secret",
                    }
                }
            }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aworld_cli.core.config.get_config", lambda: GlobalConfig())
    config_path = tmp_path / ".aworld" / "aworld.json"
    config_path.parent.mkdir()
    config_path.write_text(
        """
{
  "models": {
    "judge": {
      "provider": "openai",
      "model": "workspace-model",
      "api_key": "workspace-secret"
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    config = resolve_model_profile("judge")

    assert config.llm_provider == "openai"
    assert config.llm_model_name == "workspace-model"
    assert config.llm_api_key == "workspace-secret"


def test_resolve_model_profile_accepts_unique_configured_model_name_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyConfig:
        def load_config(self):
            return {"models": {}}

    monkeypatch.setattr("aworld_cli.core.config.get_config", lambda: EmptyConfig())

    config = resolve_model_profile(
        "gpt-example",
        config_dict={
            "models": {
                "judge": {
                    "provider": "openai",
                    "model": "gpt-example",
                    "api_key": "judge-secret",
                }
            }
        },
    )

    assert config.llm_provider == "openai"
    assert config.llm_model_name == "gpt-example"
    assert config.llm_api_key == "judge-secret"


def test_resolve_model_profile_prefers_exact_name_over_model_name_alias() -> None:
    config = resolve_model_profile(
        "judge",
        config_dict={
            "models": {
                "judge": {"provider": "openai", "model": "exact-model"},
                "alias-source": {"provider": "openai", "model": "judge"},
            }
        },
    )

    assert config.llm_model_name == "exact-model"


def test_resolve_model_profile_rejects_ambiguous_model_name_alias() -> None:
    with pytest.raises(KeyError, match="model profile alias is ambiguous"):
        resolve_model_profile(
            "shared-model",
            config_dict={
                "models": {
                    "judge-a": {"provider": "openai", "model": "shared-model"},
                    "judge-b": {"provider": "openai", "model": "shared-model"},
                }
            },
        )


def test_resolve_model_profile_raises_for_missing_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyConfig:
        def load_config(self):
            return {"models": {}}

    monkeypatch.setattr("aworld_cli.core.config.get_config", lambda: EmptyConfig())

    with pytest.raises(KeyError):
        resolve_model_profile("missing", config_dict={"models": {}})
