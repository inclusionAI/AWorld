from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp import bootstrap as bootstrap_module
from aworld_cli.acp.bootstrap import bootstrap_acp_plugins


def test_bootstrap_acp_plugins_returns_runtime_plugin_roots(monkeypatch, tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin-alpha"
    plugin_root.mkdir()

    class FakePluginManager:
        def get_runtime_plugin_roots(self):
            return [plugin_root]

    monkeypatch.setattr(bootstrap_module, "PluginManager", FakePluginManager)

    payload = bootstrap_acp_plugins(tmp_path)

    assert payload["plugin_roots"] == [plugin_root]
    assert payload["warnings"] == []
    assert payload["command_sync_enabled"] is False
    assert payload["interactive_refresh_enabled"] is False
    assert payload["base_dir"] == tmp_path


def test_bootstrap_acp_plugins_degrades_when_plugin_manager_fails(monkeypatch, tmp_path: Path) -> None:
    class FakePluginManager:
        def get_runtime_plugin_roots(self):
            raise RuntimeError("plugin manager unavailable")

    monkeypatch.setattr(bootstrap_module, "PluginManager", FakePluginManager)

    payload = bootstrap_acp_plugins(tmp_path)

    assert payload["plugin_roots"] == []
    assert payload["command_sync_enabled"] is False
    assert payload["interactive_refresh_enabled"] is False
    assert payload["base_dir"] == tmp_path
    assert len(payload["warnings"]) == 1
    assert "plugin manager unavailable" in payload["warnings"][0]
