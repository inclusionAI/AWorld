import json
from pathlib import Path

from aworld.plugins.discovery import discover_plugins
from aworld.skills.plugin_provider import PluginSkillProvider


def test_plugin_provider_preserves_plugin_identity(tmp_path: Path):
    plugin_root = tmp_path / "plugin-skill"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    (plugin_root / "skills" / "brainstorming").mkdir(parents=True)
    (plugin_root / "skills" / "brainstorming" / "SKILL.md").write_text(
        "---\ndescription: Design before implementation\n---\n\n# Brainstorming\n",
        encoding="utf-8",
    )
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "id": "plugin-skill",
                "version": "0.1.0",
                "entrypoints": {
                    "skills": [
                        {
                            "id": "brainstorming",
                            "target": "skills/brainstorming/SKILL.md",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    plugin = discover_plugins([plugin_root])[0]
    provider = PluginSkillProvider(plugin)
    descriptors = provider.list_descriptors()

    assert len(descriptors) == 1
    assert descriptors[0].skill_id == "plugin-skill:brainstorming"


def test_plugin_provider_lists_descriptor_without_decoding_invalid_body(
    tmp_path: Path,
):
    plugin_root = tmp_path / "plugin-skill"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    skill_dir = plugin_root / "skills" / "brainstorming"
    skill_dir.mkdir(parents=True)
    with (skill_dir / "SKILL.md").open("wb") as handle:
        handle.write(b"---\ndescription: Design before implementation\n---\n\n")
        handle.write(b"\xff\xfe\xfa")
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "id": "plugin-skill",
                "version": "0.1.0",
                "entrypoints": {
                    "skills": [
                        {
                            "id": "brainstorming",
                            "target": "skills/brainstorming/SKILL.md",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    plugin = discover_plugins([plugin_root])[0]
    provider = PluginSkillProvider(plugin)

    descriptors = provider.list_descriptors()

    assert len(descriptors) == 1
    assert descriptors[0].description == "Design before implementation"


def test_plugin_provider_reads_entrypoint_from_plugin_metadata(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin-skill"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    skill_dir = plugin_root / "skills" / "swarm"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Run swarm\n---\n\n# Swarm\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "index.ts").write_text("export {};\n", encoding="utf-8")
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "id": "plugin-skill",
                "version": "0.1.0",
                "entrypoints": {
                    "skills": [
                        {
                            "id": "swarm",
                            "target": "skills/swarm/SKILL.md",
                            "metadata": {"entrypoint": "scripts/index.ts"},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    plugin = discover_plugins([plugin_root])[0]
    provider = PluginSkillProvider(plugin)
    descriptor = provider.list_descriptors()[0]

    assert descriptor.execution_assets["entrypoint"] == "scripts/index.ts"
