from pathlib import Path

import pytest

from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.plugin_framework.registry import PluginCapabilityRegistry


def test_registry_indexes_plugins_by_capability():
    roots = [
        Path("tests/fixtures/plugins/code_review_like").resolve(),
        Path("tests/fixtures/plugins/hud_like").resolve(),
        Path("tests/fixtures/plugins/ralph_like").resolve(),
        Path("tests/fixtures/plugins/context_like").resolve(),
    ]
    plugins = discover_plugins(roots)

    registry = PluginCapabilityRegistry(plugins)

    assert [plugin.manifest.plugin_id for plugin in registry.get_plugins("commands")] == ["code-review-like"]
    assert [plugin.manifest.plugin_id for plugin in registry.get_plugins("hud")] == ["hud-like"]
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
