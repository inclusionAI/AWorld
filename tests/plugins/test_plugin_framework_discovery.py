import sys
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.plugins.discovery import discover_plugins
from aworld.plugins.models import PluginEntrypoint
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
    assert isinstance(legacy_manifest.entrypoints["skills"][0], PluginEntrypoint)
    assert tuple(item.entrypoint_id for item in legacy_manifest.entrypoints["skills"]) == (
        "demo",
    )
    assert tuple(item.name for item in legacy_manifest.entrypoints["skills"]) == ("demo",)


def test_discover_legacy_skill_only_plugin_synthesizes_skill_entrypoints(tmp_path):
    legacy_root = tmp_path / "legacy-skill-only"
    (legacy_root / "skills" / "zeta").mkdir(parents=True)
    (legacy_root / "skills" / "alpha").mkdir(parents=True)
    (legacy_root / "skills" / "zeta" / "SKILL.md").write_text(
        "---\nname: zeta\ndescription: zeta\n---\n", encoding="utf-8"
    )
    (legacy_root / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha\n---\n", encoding="utf-8"
    )

    discovered = discover_plugins([legacy_root])
    assert len(discovered) == 1

    skills = discovered[0].manifest.entrypoints["skills"]
    assert tuple(item.entrypoint_id for item in skills) == ("alpha", "zeta")
    assert all(isinstance(item, PluginEntrypoint) for item in skills)


def test_discover_legacy_direct_root_skill_collection_synthesizes_entrypoints(tmp_path):
    legacy_root = tmp_path / "legacy-direct-root"
    (legacy_root / "zeta").mkdir(parents=True)
    (legacy_root / "alpha").mkdir(parents=True)
    (legacy_root / "zeta" / "SKILL.md").write_text(
        "---\nname: zeta\ndescription: zeta\n---\n", encoding="utf-8"
    )
    (legacy_root / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha\n---\n", encoding="utf-8"
    )

    discovered = discover_plugins([legacy_root])
    assert len(discovered) == 1
    assert discovered[0].source == "legacy"
    assert discovered[0].manifest.capabilities == frozenset({"skills"})
    assert tuple(item.entrypoint_id for item in discovered[0].manifest.entrypoints["skills"]) == (
        "alpha",
        "zeta",
    )


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


def test_list_plugins_marks_direct_root_skill_packages_with_framework_metadata(tmp_path):
    plugin_root = tmp_path / "direct-skill-pack"
    skill_dir = plugin_root / "optimizer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: optimizer\ndescription: demo\n---\n",
        encoding="utf-8",
    )

    manager = PluginManager(plugin_dir=tmp_path / "plugin-manifest")
    manager.upsert_manifest_record(
        "direct-skill-pack",
        plugin_path=plugin_root,
        source="manual",
        package_kind="skill",
        managed_by="skill",
        activation_scope="global",
    )

    plugin = next(
        item for item in manager.list_plugins() if item["name"] == "direct-skill-pack"
    )

    assert plugin["has_skills"] is True
    assert plugin["framework_source"] != "unknown"
    assert "skills" in plugin["capabilities"]


def test_discovery_and_listing_ignore_file_based_plugin_paths(tmp_path):
    stale_manifest_path = tmp_path / "stale-plugin-path.txt"
    stale_manifest_path.write_text("stale entry", encoding="utf-8")

    assert discover_plugins([stale_manifest_path]) == []

    manager = PluginManager(plugin_dir=tmp_path / "plugin-manifest")
    manager.upsert_manifest_record(
        "stale-file-plugin",
        plugin_path=stale_manifest_path,
        source="manual",
        package_kind="skill",
        managed_by="skill",
        activation_scope="global",
    )

    plugin = next(
        item for item in manager.list_plugins() if item["name"] == "stale-file-plugin"
    )
    assert plugin["framework_source"] == "unknown"
    assert plugin["has_skills"] is False


def test_skill_managed_packages_are_excluded_from_framework_runtime_roots_and_registry(
    tmp_path,
):
    skill_managed_root = tmp_path / "skill-managed-root"
    skill_managed_root.mkdir(parents=True)
    manifest_dir = skill_managed_root / ".aworld-plugin"
    manifest_dir.mkdir(parents=True)
    (skill_managed_root / "agents").mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        (
            '{"id":"skill-managed-plugin","name":"Skill Managed","version":"0.1.0",'
            '"entrypoints":{"agents":[],"skills":[],"commands":[],"hud":[]}}'
        ),
        encoding="utf-8",
    )

    manager = PluginManager(plugin_dir=tmp_path / "plugin-manifest")
    manager.upsert_manifest_record(
        "skill-package",
        plugin_path=skill_managed_root,
        source="skill-install",
        package_kind="skill",
        managed_by="skill",
        activation_scope="global",
    )

    runtime_roots = manager.get_runtime_plugin_roots()
    framework_roots = manager.get_plugin_roots()
    framework_registry_plugins = {
        plugin.manifest.plugin_id for plugin in manager.get_framework_registry().plugins()
    }

    assert skill_managed_root.resolve() not in runtime_roots
    assert skill_managed_root not in framework_roots
    assert "skill-managed-plugin" not in framework_registry_plugins


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
