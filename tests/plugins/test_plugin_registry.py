from pathlib import Path

import pytest

from aworld_cli.core.plugin_manager import get_builtin_plugin_roots
from aworld.plugins.discovery import discover_plugins
from aworld.plugins.registry import PluginCapabilityRegistry


def _get_builtin_aworld_hud_root() -> Path:
    for root in get_builtin_plugin_roots():
        if root.name == "aworld_hud":
            return root
    raise AssertionError("built-in aworld_hud plugin root not found")


def test_registry_indexes_plugins_by_capability():
    roots = [
        Path("tests/fixtures/plugins/code_review_like").resolve(),
        _get_builtin_aworld_hud_root(),
        Path("tests/fixtures/plugins/ralph_like").resolve(),
        Path("tests/fixtures/plugins/context_like").resolve(),
    ]
    plugins = discover_plugins(roots)

    registry = PluginCapabilityRegistry(plugins)

    assert [plugin.manifest.plugin_id for plugin in registry.get_plugins("commands")] == ["code-review-like"]
    assert [plugin.manifest.plugin_id for plugin in registry.get_plugins("hud")] == ["aworld-hud"]
    assert [plugin.manifest.plugin_id for plugin in registry.get_plugins("hooks")] == ["ralph-like"]
    assert [plugin.manifest.plugin_id for plugin in registry.get_plugins("contexts")] == ["context-like"]


def test_registry_exposes_sorted_entrypoints_with_plugin_identity():
    plugin = discover_plugins([Path("tests/fixtures/plugins/context_like").resolve()])[0]

    registry = PluginCapabilityRegistry([plugin])
    entrypoints = registry.get_entrypoints("contexts")

    assert len(entrypoints) == 1
    assert entrypoints[0].plugin.manifest.plugin_id == "context-like"
    assert entrypoints[0].entrypoint.entrypoint_id == "workspace-memory"


def test_registry_rejects_duplicate_plugin_ids():
    plugin = discover_plugins([Path("tests/fixtures/plugins/context_like").resolve()])[0]

    with pytest.raises(ValueError, match="duplicate plugin id"):
        PluginCapabilityRegistry([plugin, plugin])


def test_registry_indexes_full_framework_capability_set(tmp_path):
    plugin_root = tmp_path / "full_capability"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "full-capability",
          "name": "full-capability",
          "version": "1.0.0",
          "entrypoints": {
            "agents": [{"id": "agent", "target": "agents/agent.py"}],
            "swarms": [{"id": "swarm", "target": "swarms/swarm.py"}],
            "tools": [{"id": "tool", "target": "tools/tool.py"}],
            "mcp_servers": [{"id": "mcp", "target": "mcp/server.json"}],
            "runners": [{"id": "runner", "target": "runners/runner.py"}],
            "hooks": [{"id": "hook", "target": "hooks/hook.py", "metadata": {"hook_point": "stop"}}],
            "contexts": [{"id": "context", "target": "contexts/context.py"}],
            "hud": [{"id": "hud", "target": "hud/status.py"}],
            "skills": [{"id": "skill", "target": "skills/demo/SKILL.md"}],
            "commands": [{"id": "command", "target": "commands/command.md"}]
          }
        }
        ''',
        encoding="utf-8",
    )

    plugin = discover_plugins([plugin_root])[0]
    registry = PluginCapabilityRegistry([plugin])

    assert set(registry.capabilities()) == {
        "agents",
        "commands",
        "contexts",
        "hooks",
        "hud",
        "mcp_servers",
        "runners",
        "skills",
        "swarms",
        "tools",
    }
    assert registry.get_entrypoints("tools")[0].entrypoint.entrypoint_id == "tool"
    assert registry.get_entrypoints("mcp_servers")[0].entrypoint.entrypoint_id == "mcp"


def test_registry_tracks_plugin_lifecycle_phase():
    plugin = discover_plugins([Path("tests/fixtures/plugins/context_like").resolve()])[0]

    registry = PluginCapabilityRegistry([plugin])

    assert registry.lifecycle_phase("context-like") == "activate"

    registry.deactivate("context-like")
    assert registry.lifecycle_phase("context-like") == "deactivate"

    registry.unload("context-like")
    assert registry.lifecycle_phase("context-like") == "unload"
