from __future__ import annotations

from pathlib import Path


def test_build_runtime_skill_registry_view_merges_plugin_and_filesystem_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aworld_cli.core.runtime_skill_registry import build_runtime_skill_registry_view

    plugin_root = tmp_path / "plugin_root"
    plugin_manifest_dir = plugin_root / ".aworld-plugin"
    plugin_skills_dir = plugin_root / "skills" / "brainstorming"
    plugin_manifest_dir.mkdir(parents=True)
    plugin_skills_dir.mkdir(parents=True)
    (plugin_manifest_dir / "plugin.json").write_text(
        """
{
  "id": "demo-skill-plugin",
  "name": "demo-skill-plugin",
  "version": "1.0.0",
  "entrypoints": {
    "skills": [
      {
        "id": "brainstorming",
        "name": "brainstorming",
        "target": "skills/brainstorming/SKILL.md",
        "scope": "workspace"
      }
    ]
  }
}
""".strip(),
        encoding="utf-8",
    )
    (plugin_skills_dir / "SKILL.md").write_text(
        "---\nname: Brainstorming\ndescription: Plugin skill\n---\nUse plugin skill.\n",
        encoding="utf-8",
    )

    compat_root = tmp_path / "compat_skills" / "browser-use"
    compat_root.mkdir(parents=True)
    (compat_root / "SKILL.md").write_text(
        "---\nname: Browser Use\ndescription: Filesystem skill\n---\nUse compat skill.\n",
        encoding="utf-8",
    )

    class FakePluginManager:
        def get_runtime_plugin_roots(self):
            return [plugin_root]

    monkeypatch.setattr(
        "aworld_cli.core.runtime_skill_registry.PluginManager",
        FakePluginManager,
    )
    monkeypatch.setattr(
        "aworld_cli.core.runtime_skill_registry.get_user_skills_paths",
        lambda: [compat_root.parent],
    )

    registry_view = build_runtime_skill_registry_view(skill_paths=None, cwd=tmp_path)
    all_skills = registry_view.get_all_skills()

    assert "brainstorming" in all_skills
    assert "browser-use" in all_skills
