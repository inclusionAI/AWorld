from pathlib import Path

import pytest


def test_bootstrap_runtime_initializes_middlewares_banner_and_skill_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.runtime_bootstrap import bootstrap_runtime

    calls = {
        "load_config_with_env": None,
        "init_middlewares": [],
        "show_banner": 0,
        "get_skill_registry": [],
    }

    class FakeRegistry:
        def get_all_skills(self):
            return {"brainstorming": object()}

    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: calls.__setitem__("load_config_with_env", env_file)
        or ({"provider": "demo"}, "env", env_file),
    )
    monkeypatch.setattr("aworld_cli.core.config.has_model_config", lambda config: True)
    monkeypatch.setattr(
        "aworld_cli.core.skill_registry.get_skill_registry",
        lambda skill_paths=None: calls["get_skill_registry"].append(skill_paths)
        or FakeRegistry(),
    )

    result = bootstrap_runtime(
        env_file="custom.env",
        skill_paths=["./skills"],
        show_banner=True,
        init_middlewares_fn=lambda **kwargs: calls["init_middlewares"].append(kwargs),
        show_banner_fn=lambda: calls.__setitem__("show_banner", calls["show_banner"] + 1),
    )

    assert result.config_dict == {"provider": "demo"}
    assert sorted(result.skill_registry.get_all_skills()) == ["brainstorming"]
    assert calls["load_config_with_env"] == "custom.env"
    assert len(calls["init_middlewares"]) == 1
    assert calls["show_banner"] == 1
    assert calls["get_skill_registry"] == [["./skills"]]


def test_bootstrap_runtime_raises_when_model_config_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.runtime_bootstrap import (
        RuntimeBootstrapError,
        bootstrap_runtime,
    )

    calls = {"console": []}

    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: ({}, "env", env_file),
    )
    monkeypatch.setattr("aworld_cli.core.config.has_model_config", lambda config: False)

    class FakeConsole:
        def print(self, message):
            calls["console"].append(message)

    with pytest.raises(RuntimeBootstrapError):
        bootstrap_runtime(
            show_banner=False,
            init_middlewares_fn=lambda **kwargs: None,
            show_banner_fn=lambda: None,
            console=FakeConsole(),
        )

    assert any("No model configuration" in item for item in calls["console"])
