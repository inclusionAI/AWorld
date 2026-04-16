import sys
from pathlib import Path


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
                "path": "/repo/aworld-cli/src/aworld_cli/plugins/smllc",
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
