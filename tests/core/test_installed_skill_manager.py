from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.core.installed_skill_manager import InstalledSkillManager


def _write_skill(root: Path, skill_name: str) -> None:
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: test\n---\n\n# Test\n",
        encoding="utf-8",
    )


def test_resolve_source_prefers_nested_skills_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry = installed_root / "demo"
    _write_skill(entry / "skills", "brainstorming")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )

    assert manager.resolve_entry_source(entry) == entry / "skills"


def test_resolve_source_falls_back_to_root_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry = installed_root / "direct"
    _write_skill(entry, "writing-plans")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )

    assert manager.resolve_entry_source(entry) == entry


def test_resolve_source_rejects_invalid_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry = installed_root / "broken"
    entry.mkdir(parents=True)

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )

    with pytest.raises(ValueError, match="No skill directories found"):
        manager.resolve_entry_source(entry)


def test_manifest_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry = installed_root / "demo"
    _write_skill(entry, "optimizer")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    record = manager.import_entry(entry, scope="global")

    manifest = manager.load_manifest()

    assert manifest[0]["install_id"] == record["install_id"]
    assert manifest[0]["scope"] == "global"
    assert Path(manifest[0]["resolved_skill_source_path"]) == entry


def test_remove_symlink_only_unlinks_managed_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    external = tmp_path / "external-skills"
    _write_skill(external, "agent-browser")
    managed_entry = installed_root / "linked"
    managed_entry.parent.mkdir(parents=True, exist_ok=True)
    managed_entry.symlink_to(external, target_is_directory=True)

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.import_entry(managed_entry, scope="global")

    manager.remove_install("linked")

    assert external.exists() is True
    assert managed_entry.exists() is False
    assert manager.load_manifest() == []
