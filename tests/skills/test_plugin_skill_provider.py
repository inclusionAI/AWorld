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
