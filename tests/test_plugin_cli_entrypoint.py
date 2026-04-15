import sys
from pathlib import Path


def test_plugin_list_subcommand_parses_without_repeating_plugin_token(monkeypatch, capsys):
    from aworld_cli.main import main

    class FakePluginManager:
        def __init__(self):
            self.plugin_dir = Path("/tmp/plugins")

        def list_plugins(self):
            return []

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "plugin", "list"])

    main()

    captured = capsys.readouterr()
    assert "No plugins installed" in captured.out
    assert "invalid choice" not in captured.err
