"""Reuse the legacy profile file and field contract without its import graph."""

import json
from pathlib import Path

import pytest

from aworld.cli.main import parser
from aworld.cli.model_config import resolve_model_settings


def write_profiles(root, profiles):
    directory = root / ".aworld"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "aworld.json").write_text(json.dumps({"models": profiles}))


def settings(tmp_path, *arguments, environ=None):
    args = parser().parse_args(["--cwd", str(tmp_path / "workspace"), *arguments])
    return resolve_model_settings(args, home=tmp_path / "home", environ=environ or {})


def test_project_profile_wins_over_user_and_limits_use_standard_names(tmp_path):
    write_profiles(tmp_path / "home", {"local": {"model": "old", "max_model_len": 64000}})
    write_profiles(tmp_path / "workspace", {"local": {"llm_model_name": "deployment", "provider": "openai",
        "llm_base_url": "http://localhost:9999/v1", "api_key_env": "TEST_KEY", "max_model_len": 256000,
        "max_tokens": 16384, "params": {"temperature": .2, "top_p": .8}}})
    result = settings(tmp_path, "--model-profile", "local", environ={"TEST_KEY": "private-fixture"})
    assert (result.model, result.context_window, result.max_output_tokens) == ("deployment", 256000, 16384)
    assert result.window_source == result.output_source == "profile"
    assert result.api_key == "private-fixture" and "private-fixture" not in repr(result)
    assert result.parameters == {"temperature": .2, "top_p": .8}


def test_compiler_limit_env_and_cli_priority(tmp_path):
    write_profiles(tmp_path / "workspace", {"local": {"model": "deployment", "context_window": 200000,
        "context_compiler": {"context_limit": 100000}, "params": {"max_tokens": 8192}}})
    assert settings(tmp_path, "--model-profile", "local").context_window == 100000
    environment = {"AWORLD_CONTEXT_WINDOW_TOKENS": "64000", "AWORLD_CONTEXT_LIMIT_TOKENS": "48000",
                   "AWORLD_MAX_OUTPUT_TOKENS": "4096"}
    result = settings(tmp_path, "--model-profile", "local", environ=environment)
    assert result.context_window == 48000 and result.max_output_tokens == 4096
    result = settings(tmp_path, "--model-profile", "local", "--context-window", "32000", "--max-output-tokens", "2048", environ=environment)
    assert (result.context_window, result.max_output_tokens) == (32000, 2048)


def test_exact_registry_unknown_alias_and_default_profile(tmp_path):
    assert settings(tmp_path, "--model", "matrixllm.aisearch_dsv41flash").context_window == 1000000
    assert settings(tmp_path, "--model", "gpt-4.1").context_window == 1047576
    assert settings(tmp_path, "--model", "external-custom").window_source == "core_fallback"
    assert settings(tmp_path, "--model", "external-custom").context_window == 128000
    write_profiles(tmp_path / "workspace", {"default": {"model": "my-deployment", "context_window_tokens": 40000}})
    assert settings(tmp_path).model == "my-deployment"
    assert settings(tmp_path).context_window == 40000
    assert settings(tmp_path, "--model", "external-custom").model == "external-custom"


@pytest.mark.parametrize("value", [0, True, "NaN", -1])
def test_invalid_profile_window_never_falls_back_silently(tmp_path, value):
    write_profiles(tmp_path / "workspace", {"bad": {"model": "custom", "max_model_len": value}})
    with pytest.raises(ValueError):
        settings(tmp_path, "--model-profile", "bad")


def test_ambiguous_alias_and_unsupported_params_are_explicit_errors(tmp_path):
    write_profiles(tmp_path / "workspace", {"a": {"model": "custom"}, "b": {"model": "custom"}})
    with pytest.raises(ValueError, match="ambiguous"):
        settings(tmp_path, "--model-profile", "custom")
    write_profiles(tmp_path / "workspace", {"a": {"model": "custom", "params": {"messages": []}}})
    with pytest.raises(ValueError, match="Unsupported"):
        settings(tmp_path, "--model-profile", "a")


def test_default_environment_profile_and_oversized_output(tmp_path):
    result = settings(tmp_path, "--model-profile", "default", environ={"LLM_MODEL_NAME": "custom",
        "LLM_BASE_URL": "http://localhost/v1", "AWORLD_CONTEXT_WINDOW_TOKENS": "32000"})
    assert result.model == "custom" and result.context_window == 32000
    with pytest.raises(ValueError, match="leave room"):
        settings(tmp_path, "--context-window", "8000", "--max-output-tokens", "8000")


def test_core_cli_accepts_explicit_max_reasoning_effort(tmp_path):
    result = settings(
        tmp_path,
        "--model",
        "matrixllm.aisearch_dsv4_flash",
        "--reasoning-effort",
        "max",
    )

    assert result.parameters["reasoning_effort"] == "max"
