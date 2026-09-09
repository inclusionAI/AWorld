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


def test_compat_registry_excludes_unreleased_self_evolve_candidate_skills(
    tmp_path: Path,
) -> None:
    stable_dir = tmp_path / "stable"
    stable_dir.mkdir(parents=True)
    (stable_dir / "SKILL.md").write_text(
        "---\ndescription: Stable skill\n---\n\n# Stable\n",
        encoding="utf-8",
    )
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "SKILL.md").write_text(
        (
            "---\n"
            "description: Candidate skill\n"
            "self_evolve:\n"
            "  release_state: candidate\n"
            "---\n\n"
            "# Candidate\n"
        ),
        encoding="utf-8",
    )

    registry = CompatSkillRegistry()
    count = registry.register_source(tmp_path)

    assert count == 1
    assert registry.get_skill("stable") is not None
    assert registry.get_skill("candidate") is None


def test_compat_registry_excludes_self_evolve_draft_release_state(
    tmp_path: Path,
) -> None:
    draft_dir = tmp_path / "draft"
    draft_dir.mkdir(parents=True)
    (draft_dir / "SKILL.md").write_text(
        (
            "---\n"
            "description: Draft skill\n"
            "self_evolve:\n"
            "  release_state: draft\n"
            "---\n\n"
            "# Draft\n"
        ),
        encoding="utf-8",
    )

    registry = CompatSkillRegistry()
    count = registry.register_source(tmp_path)

    assert count == 0
    assert registry.get_skill("draft") is None


def test_skill_loader_re_exports_framework_compat_registry_without_legacy_class() -> None:
    assert LegacySkillRegistry is CompatSkillRegistry
    assert "class SkillRegistry" not in inspect.getsource(skill_loader_module)
