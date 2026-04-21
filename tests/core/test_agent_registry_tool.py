import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.core.agent_registry_tool import list_built_in_resources
from aworld_cli.core.plugin_manager import PluginManager


@pytest.mark.asyncio
async def test_list_built_in_resources_excludes_skill_managed_plugin_agents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plugin_dir = tmp_path / ".aworld" / "plugins"
    skill_managed_root = tmp_path / "skill-managed-root"
    agents_dir = skill_managed_root / "agents" / "demo-agent"
    manifest_dir = skill_managed_root / ".aworld-plugin"
    agents_dir.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        (
            '{"id":"skill-managed-plugin","name":"Skill Managed","version":"0.1.0",'
            '"entrypoints":{"agents":[],"skills":[],"commands":[],"hud":[]}}'
        ),
        encoding="utf-8",
    )

    manager = PluginManager(plugin_dir=plugin_dir)
    manager.upsert_manifest_record(
        "skill-package",
        plugin_path=skill_managed_root,
        source="skill-install",
        package_kind="skill",
        managed_by="skill",
        activation_scope="global",
    )

    monkeypatch.setattr("aworld_cli.core.plugin_manager.DEFAULT_PLUGIN_DIR", plugin_dir)
    monkeypatch.setattr(
        "aworld_cli.core.agent_registry.LocalAgentRegistry.list_agents",
        lambda: [
            SimpleNamespace(
                name="demo-agent",
                desc="demo",
                path=str(agents_dir),
                register_dir=str(agents_dir),
            )
        ],
    )

    resources = await list_built_in_resources()

    assert all(resource[0] != "demo-agent" for resource in resources)
