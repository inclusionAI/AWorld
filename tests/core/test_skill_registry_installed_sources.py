import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.core.installed_skill_manager import InstalledSkillManager
from aworld_cli.core.skill_registry import get_skill_registry, reset_skill_registry


def _write_skill(root: Path, skill_name: str, description: str) -> None:
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (
            f"---\nname: {skill_name}\ndescription: {description}\n---\n\n"
            f"# {skill_name}\n"
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_skill_registry()
    yield
    reset_skill_registry()


def test_get_skill_registry_auto_registers_installed_global_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_PATH", raising=False)
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    monkeypatch.delenv("SKILLS_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    manager = InstalledSkillManager()
    global_entry = manager.installed_root / "global-skill-pack"
    project_entry = manager.installed_root / "project-skill-pack"
    _write_skill(global_entry / "skills", "global-only", "global installed version")
    _write_skill(project_entry / "skills", "project-only", "project installed version")
    manager.import_entry(global_entry, scope="global")
    manager.import_entry(project_entry, scope="project")

    registry = get_skill_registry()
    all_skills = registry.get_all_skills()

    assert "global-only" in all_skills
    assert all_skills["global-only"]["description"] == "global installed version"
    assert "project-only" not in all_skills


def test_explicit_skill_path_overrides_installed_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_PATH", raising=False)
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    monkeypatch.delenv("SKILLS_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    manager = InstalledSkillManager()
    installed_entry = manager.installed_root / "installed-shared-pack"
    _write_skill(installed_entry / "skills", "shared-skill", "installed version")
    manager.import_entry(installed_entry, scope="global")

    explicit_source = tmp_path / "explicit-skills"
    _write_skill(explicit_source, "shared-skill", "explicit version")

    registry = get_skill_registry(skill_paths=[str(explicit_source)])
    shared_skill = registry.get_skill("shared-skill")

    assert shared_skill is not None
    assert shared_skill["description"] == "explicit version"
