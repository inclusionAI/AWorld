import sys
from pathlib import Path

import pytest


def test_plugins_command_is_registered_via_plugin_registry():
    from aworld_cli import main as main_module

    registry = main_module._build_top_level_command_registry()
    command = registry.get("plugins")

    assert command is not None
    assert registry.get("plugin") is command


def test_batch_command_is_registered_via_plugin_registry():
    from aworld_cli import main as main_module

    registry = main_module._build_top_level_command_registry()
    command = registry.get("batch-job")

    assert command is not None
    assert registry.get("batch") is command


def test_gateway_command_is_registered_via_plugin_registry():
    from aworld_cli import main as main_module

    registry = main_module._build_top_level_command_registry()
    command = registry.get("gateway")

    assert command is not None


def test_list_command_is_registered_via_plugin_registry():
    from aworld_cli import main as main_module

    registry = main_module._build_top_level_command_registry()
    command = registry.get("list")

    assert command is not None


def test_serve_command_is_registered_via_plugin_registry():
    from aworld_cli import main as main_module

    registry = main_module._build_top_level_command_registry()
    command = registry.get("serve")

    assert command is not None


def test_interactive_command_is_registered_via_plugin_registry():
    from aworld_cli import main as main_module

    registry = main_module._build_top_level_command_registry()
    command = registry.get("interactive")

    assert command is not None


def test_list_command_dispatches_before_global_config_loading(monkeypatch, capsys):
    from aworld_cli.main import main

    calls = {"display_agents": None}

    class FakeCLI:
        def display_agents(self, agents):
            calls["display_agents"] = agents

    async def fake_load_all_agents(**kwargs):
        return [{"name": "DemoAgent"}]

    monkeypatch.setattr("aworld_cli.main.AWorldCLI", FakeCLI)
    monkeypatch.setattr("aworld_cli.main.load_all_agents", fake_load_all_agents)
    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: (_ for _ in ()).throw(
            AssertionError("list should dispatch before config loading")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "list"])

    main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert calls["display_agents"] == [{"name": "DemoAgent"}]


def test_list_command_preserves_global_agent_dir_before_command(monkeypatch, capsys):
    from aworld_cli.main import main

    calls = {"load_all_agents": None}

    class FakeCLI:
        def display_agents(self, agents):
            return None

    async def fake_load_all_agents(**kwargs):
        calls["load_all_agents"] = kwargs
        return [{"name": "DemoAgent"}]

    monkeypatch.setattr("aworld_cli.main.AWorldCLI", FakeCLI)
    monkeypatch.setattr("aworld_cli.main.load_all_agents", fake_load_all_agents)
    monkeypatch.setattr(
        "aworld_cli.core.config.load_config_with_env",
        lambda env_file: (_ for _ in ()).throw(
            AssertionError("list should dispatch before config loading")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["aworld-cli", "--agent-dir", "./agents", "list"],
    )

    main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert calls["load_all_agents"] == {
        "remote_backends": None,
        "local_dirs": ["./agents"],
        "agent_files": None,
    }


def test_serve_command_dispatches_with_bootstrap_and_global_options(
    monkeypatch,
    capsys,
):
    from aworld_cli import main as main_module

    calls = {
        "load_config_with_env": None,
        "init_middlewares": [],
        "show_banner": 0,
        "get_skill_registry": [],
        "resolve_agent_dirs": [],
        "run_serve_mode": [],
    }

    class FakeRegistry:
        def get_all_skills(self):
            return {"demo": object()}

    async def fake_run_serve_mode(**kwargs):
        calls["run_serve_mode"].append(kwargs)

    monkeypatch.setattr(
        "aworld_cli.main._show_banner",
        lambda: calls.__setitem__("show_banner", calls["show_banner"] + 1),
    )
    monkeypatch.setattr(
        "aworld_cli.main.init_middlewares",
        lambda **kwargs: calls["init_middlewares"].append(kwargs),
    )
    monkeypatch.setattr(
        "aworld_cli.main._resolve_agent_dirs",
        lambda agent_dirs: calls["resolve_agent_dirs"].append(agent_dirs)
        or ["./resolved-agents"],
    )
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
    monkeypatch.setattr("aworld_cli.main._run_serve_mode", fake_run_serve_mode)

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "--env-file",
            "custom.env",
            "--skill-path",
            "./skills",
            "--agent-dir",
            "./agents",
            "--remote-backend",
            "http://backend",
            "--http",
            "serve",
        ]
    )

    captured = capsys.readouterr()
    assert handled is True
    assert captured.out == ""
    assert calls["load_config_with_env"] == "custom.env"
    assert calls["show_banner"] == 1
    assert len(calls["init_middlewares"]) == 1
    assert calls["get_skill_registry"] == [["./skills"]]
    assert calls["resolve_agent_dirs"] == [["./agents"]]
    assert calls["run_serve_mode"] == [
        {
            "http": True,
            "http_host": "0.0.0.0",
            "http_port": 8000,
            "mcp": False,
            "mcp_name": "AWorldAgent",
            "mcp_transport": "stdio",
            "mcp_host": "0.0.0.0",
            "mcp_port": 8001,
            "remote_backends": ["http://backend"],
            "local_dirs": ["./resolved-agents"],
            "agent_files": None,
        }
    ]


def test_interactive_command_dispatches_with_bootstrap_and_global_options(
    monkeypatch,
    capsys,
):
    from aworld_cli import main as main_module

    calls = {
        "load_config_with_env": None,
        "init_middlewares": [],
        "show_banner": 0,
        "get_skill_registry": [],
        "resolve_agent_dirs": [],
        "run_interactive_mode": [],
    }

    class FakeRegistry:
        def get_all_skills(self):
            return {"brainstorming": object()}

    async def fake_run_interactive_mode(**kwargs):
        calls["run_interactive_mode"].append(kwargs)

    monkeypatch.setattr(
        "aworld_cli.main._show_banner",
        lambda: calls.__setitem__("show_banner", calls["show_banner"] + 1),
    )
    monkeypatch.setattr(
        "aworld_cli.main.init_middlewares",
        lambda **kwargs: calls["init_middlewares"].append(kwargs),
    )
    monkeypatch.setattr(
        "aworld_cli.main._resolve_agent_dirs",
        lambda agent_dirs: calls["resolve_agent_dirs"].append(agent_dirs)
        or ["./resolved-agents"],
    )
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
    monkeypatch.setattr(
        "aworld_cli.main._run_interactive_mode",
        fake_run_interactive_mode,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "--env-file",
            "custom.env",
            "--skill-path",
            "./skills",
            "--agent-dir",
            "./agents",
            "--remote-backend",
            "http://backend",
            "--skill",
            "brainstorming",
            "interactive",
            "--agent",
            "Developer",
        ]
    )

    captured = capsys.readouterr()
    assert handled is True
    assert captured.out == ""
    assert calls["load_config_with_env"] == "custom.env"
    assert calls["show_banner"] == 1
    assert len(calls["init_middlewares"]) == 1
    assert calls["get_skill_registry"] == [["./skills"]]
    assert calls["resolve_agent_dirs"] == [["./agents"]]
    assert calls["run_interactive_mode"] == [
        {
            "agent_name": "Developer",
            "requested_skill_names": ["brainstorming"],
            "remote_backends": ["http://backend"],
            "local_dirs": ["./resolved-agents"],
            "agent_files": None,
        }
    ]


def test_global_parser_help_excludes_serve_specific_flags() -> None:
    from aworld_cli.main import build_parser

    help_text = build_parser().format_help()

    assert "--http" not in help_text
    assert "--mcp" not in help_text


def test_main_routes_default_interactive_through_registered_command(
    monkeypatch,
) -> None:
    from aworld_cli import main as main_module

    calls = {"run": 0}

    def fake_run(self, args, context):
        calls["run"] += 1
        assert tuple(context.argv) == ("aworld-cli",)
        return 0

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.interactive_cmd.InteractiveTopLevelCommand.run",
        fake_run,
    )
    monkeypatch.setattr(sys, "argv", ["aworld-cli"])

    main_module.main()

    assert calls["run"] == 1


def test_main_routes_task_mode_through_hidden_run_command(
    monkeypatch,
) -> None:
    from aworld_cli import main as main_module

    calls = {"run": 0}

    def fake_run(self, args, context):
        calls["run"] += 1
        assert args.task == "write tests"
        return 0

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.RunTopLevelCommand.run",
        fake_run,
    )
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "--task", "write tests"])

    main_module.main()

    assert calls["run"] == 1


def test_main_routes_config_flag_through_hidden_config_command(
    monkeypatch,
) -> None:
    from aworld_cli import main as main_module

    calls = {"run": 0}

    def fake_run(self, args, context):
        calls["run"] += 1
        assert args.config is True
        return 0

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.config_cmd.ConfigTopLevelCommand.run",
        fake_run,
    )
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "--config"])

    main_module.main()

    assert calls["run"] == 1


def test_plugins_without_subcommand_defaults_to_list(monkeypatch, capsys):
    from aworld_cli.main import main

    class FakePluginManager:
        def __init__(self):
            self.plugin_dir = Path("/tmp/plugins")

        def list_plugins(self):
            return []

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)
    monkeypatch.setattr("aworld_cli.core.plugin_manager.list_builtin_plugins", lambda: [])
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "plugins"])

    main()

    captured = capsys.readouterr()
    assert "No plugins available" in captured.out
    assert "invalid choice" not in captured.err
    assert "the following arguments are required" not in captured.err


def test_plugins_list_subcommand_parses_without_repeating_plugin_token(monkeypatch, capsys):
    from aworld_cli.main import main

    class FakePluginManager:
        def __init__(self):
            self.plugin_dir = Path("/tmp/plugins")

        def list_plugins(self):
            return []

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)
    monkeypatch.setattr("aworld_cli.core.plugin_manager.list_builtin_plugins", lambda: [])
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "plugins", "list"])

    main()

    captured = capsys.readouterr()
    assert "No plugins available" in captured.out
    assert "invalid choice" not in captured.err


def test_legacy_plugin_alias_still_parses(monkeypatch, capsys):
    from aworld_cli.main import main

    class FakePluginManager:
        def __init__(self):
            self.plugin_dir = Path("/tmp/plugins")

        def list_plugins(self):
            return []

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)
    monkeypatch.setattr("aworld_cli.core.plugin_manager.list_builtin_plugins", lambda: [])
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "plugin", "list"])

    main()

    captured = capsys.readouterr()
    assert "No plugins available" in captured.out
    assert "invalid choice" not in captured.err


def test_plugins_list_includes_builtin_plugins(monkeypatch, capsys):
    from aworld_cli.main import main

    class FakePluginManager:
        def __init__(self):
            self.plugin_dir = Path("/tmp/plugins")

        def list_plugins(self):
            return []

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)
    monkeypatch.setattr(
        "aworld_cli.core.plugin_manager.list_builtin_plugins",
        lambda: [
            {
                "name": "aworld-hud",
                "plugin_id": "aworld-hud",
                "enabled": True,
                "lifecycle_phase": "activate",
                "framework_source": "manifest",
                "capabilities": ["hud"],
                "source": "built-in",
                "has_agents": False,
                "has_skills": False,
                "path": "/repo/aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud",
            }
        ],
    )
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "plugins", "list"])

    main()

    captured = capsys.readouterr()
    assert "Available plugins (1)" in captured.out
    assert "No plugins available" not in captured.out


def test_plugins_list_hides_legacy_builtin_plugins(monkeypatch, capsys):
    from aworld_cli.main import main

    class FakePluginManager:
        def __init__(self):
            self.plugin_dir = Path("/tmp/plugins")

        def list_plugins(self):
            return []

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)
    monkeypatch.setattr(
        "aworld_cli.core.plugin_manager.list_builtin_plugins",
        lambda: [
            {
                "name": "smllc",
                "plugin_id": "smllc",
                "enabled": True,
                "lifecycle_phase": "activate",
                "framework_source": "legacy",
                "capabilities": ["agents"],
                "source": "built-in",
                "has_agents": True,
                "has_skills": False,
                "path": "/repo/aworld-cli/src/aworld_cli/builtin_agents/smllc",
            },
            {
                "name": "aworld-hud",
                "plugin_id": "aworld-hud",
                "enabled": True,
                "lifecycle_phase": "activate",
                "framework_source": "manifest",
                "capabilities": ["hud"],
                "source": "built-in",
                "has_agents": False,
                "has_skills": False,
                "path": "/repo/aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud",
            },
        ],
    )
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "plugins", "list"])

    main()

    captured = capsys.readouterr()
    assert "Available plugins (1)" in captured.out


def test_plugins_validate_subcommand_uses_installed_plugin_name(monkeypatch, capsys):
    from aworld_cli.main import main

    class FakePluginManager:
        def __init__(self):
            self.plugin_dir = Path("/tmp/plugins")

        def validate(self, plugin_name):
            assert plugin_name == "aworld-hud"
            return {
                "valid": True,
                "plugin_id": "aworld-hud",
                "framework_source": "manifest",
                "capabilities": ["hud"],
                "path": "/tmp/plugins/aworld-hud",
            }

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "plugins", "validate", "aworld-hud"])

    main()

    captured = capsys.readouterr()
    assert "valid" in captured.out.lower()
    assert "aworld-hud" in captured.out


def test_plugins_validate_subcommand_accepts_explicit_path(monkeypatch, capsys):
    from aworld_cli.main import main

    def fake_validate_plugin_path(path):
        assert path == Path("/tmp/demo-plugin")
        return {
            "valid": True,
            "plugin_id": "demo-plugin",
            "framework_source": "manifest",
            "capabilities": ["commands"],
            "path": "/tmp/demo-plugin",
        }

    monkeypatch.setattr("aworld_cli.core.plugin_manager.validate_plugin_path", fake_validate_plugin_path)
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "plugins", "validate", "--path", "/tmp/demo-plugin"])

    main()

    captured = capsys.readouterr()
    assert "demo-plugin" in captured.out
    assert "valid" in captured.out.lower()


def test_batch_alias_dispatches_via_top_level_plugin_command(
    monkeypatch,
    capsys,
):
    from aworld_cli import main as main_module

    called = {}

    async def fake_run_batch_job(config_path, remote_backend):
        called["config_path"] = config_path
        called["remote_backend"] = remote_backend

    monkeypatch.setattr("aworld_cli.plugins.batch.cli.run_batch_job", fake_run_batch_job)

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "batch", "batch.yaml", "--remote-backend", "http://demo"]
    )

    captured = capsys.readouterr()

    assert handled is True
    assert captured.out == ""
    assert called == {
        "config_path": "batch.yaml",
        "remote_backend": "http://demo",
    }
