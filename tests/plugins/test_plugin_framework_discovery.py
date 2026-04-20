from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from aworld.plugins.discovery import discover_plugins
from aworld_cli.core.plugin_manager import PluginManager
from aworld_cli.runtime.loaders import PluginLoader


def test_discover_manifest_and_legacy_plugins():
    roots = [
        Path("tests/fixtures/plugins/framework_minimal").resolve(),
        Path("tests/fixtures/plugins/legacy_agents_only").resolve(),
    ]

    discovered = discover_plugins(roots)
    plugin_ids = {plugin.manifest.plugin_id for plugin in discovered}

    assert "framework-minimal" in plugin_ids
    assert "legacy_agents_only" in plugin_ids

    legacy_root = roots[1]
    legacy = next(p for p in discovered if p.manifest.plugin_id == "legacy_agents_only")
    legacy_manifest = legacy.manifest

    assert legacy_manifest.plugin_root == str(legacy_root.resolve())
    assert legacy_manifest.capabilities == frozenset({"agents", "skills"})
    assert isinstance(legacy_manifest.entrypoints, MappingProxyType)
    assert set(legacy_manifest.entrypoints.keys()) == {"agents", "skills"}
    assert legacy_manifest.entrypoints["agents"] == ()
    assert legacy_manifest.entrypoints["skills"] == ()


def test_get_plugin_roots_includes_skill_only_plugins(tmp_path):
    plugin_root = tmp_path / "skills_only"
    skill_dir = plugin_root / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo\n---\n",
        encoding="utf-8",
    )

    manager = PluginManager(plugin_dir=tmp_path)
    roots = manager.get_plugin_roots()

    assert plugin_root in roots


@pytest.mark.asyncio
async def test_plugin_loader_accepts_non_builtin_plugin_agents(tmp_path, monkeypatch):
    plugin_root = tmp_path / "custom_plugin"
    agents_dir = plugin_root / "agents"
    agents_dir.mkdir(parents=True)

    def fake_init_agents(_path: str) -> None:
        return None

    class DummyAgent:
        def __init__(self, name: str, register_dir: str):
            self.name = name
            self.register_dir = register_dir

    dummy_agent = DummyAgent("demo-agent", str(agents_dir))

    def fake_list_agents():
        return [dummy_agent]

    def fake_from_local_agent(agent, source_location: str):
        return SimpleNamespace(name=agent.name, source_location=source_location)

    monkeypatch.setattr("aworld_cli.core.loader.init_agents", fake_init_agents)
    monkeypatch.setattr("aworld_cli.core.agent_registry.LocalAgentRegistry.list_agents", fake_list_agents)
    monkeypatch.setattr("aworld_cli.models.AgentInfo.from_local_agent", fake_from_local_agent)

    loader = PluginLoader(plugin_root)
    agents = await loader._load_agents_from_plugin()

    assert len(agents) == 1
    assert agents[0].name == "demo-agent"
