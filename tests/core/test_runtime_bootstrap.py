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
        "build_runtime_skill_registry_view": [],
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
        "aworld_cli.core.runtime_skill_registry.build_runtime_skill_registry_view",
        lambda skill_paths=None, cwd=None: calls["build_runtime_skill_registry_view"].append(skill_paths)
        or FakeRegistry(),
    )
    monkeypatch.setattr(
        "aworld_cli.memory.bootstrap.register_cli_memory_provider",
        lambda: None,
    )

    def strict_init_middlewares(
        *,
        init_memory,
        init_retriever,
        custom_memory_store,
    ) -> None:
        calls["init_middlewares"].append(
            {
                "init_memory": init_memory,
                "init_retriever": init_retriever,
                "custom_memory_store": custom_memory_store,
            }
        )

    result = bootstrap_runtime(
        env_file="custom.env",
        skill_paths=["./skills"],
        show_banner=True,
        init_middlewares_fn=strict_init_middlewares,
        show_banner_fn=lambda: calls.__setitem__("show_banner", calls["show_banner"] + 1),
    )

    assert result.config_dict == {"provider": "demo"}
    assert sorted(result.skill_registry.get_all_skills()) == ["brainstorming"]
    assert calls["load_config_with_env"] == "custom.env"
    assert len(calls["init_middlewares"]) == 1
    assert calls["show_banner"] == 1
    assert calls["build_runtime_skill_registry_view"] == [["./skills"]]


def test_bootstrap_runtime_raises_when_model_config_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.runtime_bootstrap import (
        RuntimeBootstrapError,
        bootstrap_runtime,
    )

    calls = {
        "console": [],
        "register_calls": 0,
        "build_calls": 0,
        "init_middlewares": 0,
    }

    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: ({}, "env", env_file),
    )
    monkeypatch.setattr("aworld_cli.core.config.has_model_config", lambda config: False)
    monkeypatch.setattr(
        "aworld_cli.memory.bootstrap.register_cli_memory_provider",
        lambda: calls.__setitem__("register_calls", calls["register_calls"] + 1),
    )
    monkeypatch.setattr(
        "aworld_cli.memory.bootstrap.build_cli_memory_config",
        lambda: calls.__setitem__("build_calls", calls["build_calls"] + 1),
    )

    class FakeConsole:
        def print(self, message):
            calls["console"].append(message)

    with pytest.raises(RuntimeBootstrapError):
        bootstrap_runtime(
            show_banner=False,
            init_middlewares_fn=lambda **kwargs: calls.__setitem__(
                "init_middlewares", calls["init_middlewares"] + 1
            ),
            show_banner_fn=lambda: None,
            console=FakeConsole(),
        )

    assert any("No model configuration" in item for item in calls["console"])
    assert calls["register_calls"] == 0
    assert calls["build_calls"] == 0
    assert calls["init_middlewares"] == 0


def test_bootstrap_runtime_requests_hybrid_provider_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.runtime_bootstrap import bootstrap_runtime

    captured = {"register_calls": 0}
    monkeypatch.delenv("AWORLD_CLI_MEMORY_MODE", raising=False)

    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: ({"provider": "demo"}, None, None),
    )
    monkeypatch.setattr("aworld_cli.core.config.has_model_config", lambda config: True)
    monkeypatch.setattr(
        "aworld_cli.core.runtime_skill_registry.build_runtime_skill_registry_view",
        lambda skill_paths=None, cwd=None: type(
            "Registry", (), {"get_all_skills": lambda self: {}}
        )(),
    )
    monkeypatch.setattr(
        "aworld_cli.memory.bootstrap.register_cli_memory_provider",
        lambda: captured.__setitem__("register_calls", captured["register_calls"] + 1),
    )

    bootstrap_runtime(
        show_banner=False,
        init_middlewares_fn=lambda **kwargs: captured.update(kwargs),
        show_banner_fn=lambda: None,
    )

    assert captured["memory_config"].provider == "hybrid"
    assert captured["register_calls"] == 1


def test_bootstrap_runtime_honors_legacy_memory_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.runtime_bootstrap import bootstrap_runtime

    captured = {}
    monkeypatch.setenv("AWORLD_CLI_MEMORY_MODE", "legacy")

    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: ({"provider": "demo"}, None, None),
    )
    monkeypatch.setattr("aworld_cli.core.config.has_model_config", lambda config: True)
    monkeypatch.setattr(
        "aworld_cli.core.runtime_skill_registry.build_runtime_skill_registry_view",
        lambda skill_paths=None, cwd=None: type(
            "Registry", (), {"get_all_skills": lambda self: {}}
        )(),
    )
    monkeypatch.setattr(
        "aworld_cli.memory.bootstrap.register_cli_memory_provider",
        lambda: None,
    )

    bootstrap_runtime(
        show_banner=False,
        init_middlewares_fn=lambda **kwargs: captured.update(kwargs),
        show_banner_fn=lambda: None,
    )

    assert captured["memory_config"].provider == "aworld"


def test_bootstrap_runtime_passes_memory_config_when_callback_accepts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.runtime_bootstrap import bootstrap_runtime

    captured = {}
    monkeypatch.delenv("AWORLD_CLI_MEMORY_MODE", raising=False)

    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: ({"provider": "demo"}, None, None),
    )
    monkeypatch.setattr("aworld_cli.core.config.has_model_config", lambda config: True)
    monkeypatch.setattr(
        "aworld_cli.core.runtime_skill_registry.build_runtime_skill_registry_view",
        lambda skill_paths=None, cwd=None: type(
            "Registry", (), {"get_all_skills": lambda self: {}}
        )(),
    )

    def strict_init_middlewares(
        *,
        init_memory,
        init_retriever,
        custom_memory_store,
        memory_config,
    ) -> None:
        captured.update(
            {
                "init_memory": init_memory,
                "init_retriever": init_retriever,
                "custom_memory_store": custom_memory_store,
                "memory_config": memory_config,
            }
        )

    bootstrap_runtime(
        show_banner=False,
        init_middlewares_fn=strict_init_middlewares,
        show_banner_fn=lambda: None,
    )

    assert captured["memory_config"].provider == "hybrid"


def test_bootstrap_runtime_supports_real_init_middlewares_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld.core.context.amni.config import init_middlewares
    from aworld_cli.runtime_bootstrap import bootstrap_runtime

    captured = {}

    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: ({"provider": "demo"}, None, None),
    )
    monkeypatch.setattr("aworld_cli.core.config.has_model_config", lambda config: True)
    monkeypatch.setattr(
        "aworld_cli.core.runtime_skill_registry.build_runtime_skill_registry_view",
        lambda skill_paths=None, cwd=None: type(
            "Registry", (), {"get_all_skills": lambda self: {}}
        )(),
    )
    monkeypatch.setattr(
        "aworld_cli.memory.bootstrap.register_cli_memory_provider",
        lambda: None,
    )
    monkeypatch.setattr(
        "aworld.core.context.amni.config.MemoryFactory.init",
        lambda **kwargs: captured.update(kwargs),
    )

    bootstrap_runtime(
        show_banner=False,
        init_middlewares_fn=init_middlewares,
        show_banner_fn=lambda: None,
    )

    assert "config" in captured
    assert captured["config"].provider == "hybrid"
