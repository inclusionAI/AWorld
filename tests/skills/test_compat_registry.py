import inspect
from pathlib import Path

import aworld.utils.skill_loader as skill_loader_module
from aworld.skills.compat_registry import CompatSkillRegistry
from aworld.utils.skill_loader import SkillRegistry as LegacySkillRegistry


def test_compat_registry_registers_source_and_exposes_skill_configs(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "description: Browser automation\n"
        'tool_list: {"browser": {"desc": "Browser MCP"}}\n'
        "type: agent\n"
        "---\n\n"
        "# Usage\n"
        "Use browser tools.\n",
        encoding="utf-8",
    )

    registry = CompatSkillRegistry()
    count = registry.register_source(tmp_path)

    assert count == 1
    assert registry.get_skill("browser-use")["description"] == "Browser automation"
    assert registry.get_skill_configs()["browser-use"]["desc"] == "Browser automation"


def test_legacy_skill_registry_uses_same_compat_behavior(tmp_path: Path) -> None:
    skill_dir = tmp_path / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "description: Browser automation\n"
        "---\n\n"
        "# Usage\n"
        "Use browser tools.\n",
        encoding="utf-8",
    )

    registry = LegacySkillRegistry()
    count = registry.register_source(tmp_path)

    assert count == 1
    assert registry.get_skill("browser-use")["description"] == "Browser automation"


def test_skill_loader_re_exports_framework_compat_registry_without_legacy_class() -> None:
    assert LegacySkillRegistry is CompatSkillRegistry
    assert "class SkillRegistry" not in inspect.getsource(skill_loader_module)
