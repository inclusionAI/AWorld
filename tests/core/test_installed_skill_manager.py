import os
import json
import sys
import subprocess
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.core.installed_skill_manager import InstalledSkillManager
from aworld_cli.core.plugin_manager import PluginManager


def _write_skill(root: Path, skill_name: str) -> None:
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: test\n---\n\n# Test\n",
        encoding="utf-8",
    )


def _write_framework_plugin(root: Path, plugin_id: str, plugin_name: str) -> None:
    plugin_manifest_dir = root / ".aworld-plugin"
    plugin_manifest_dir.mkdir(parents=True, exist_ok=True)
    commands_dir = root / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    (commands_dir / "echo.md").write_text("# Echo\n", encoding="utf-8")
    (plugin_manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "name": plugin_name,
                "version": "0.1.0",
                "entrypoints": {
                    "commands": [
                        {
                            "id": "echo",
                            "name": "Echo",
                            "target": "commands/echo.md",
                        }
                    ],
                    "hud": [],
                },
            }
        ),
        encoding="utf-8",
    )


def _write_skill_package_with_framework_manifest(
    root: Path, skill_name: str, plugin_id: str, plugin_name: str
) -> None:
    _write_skill(root / "skills", skill_name)
    _write_framework_plugin(root, plugin_id=plugin_id, plugin_name=plugin_name)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _commit_all(repo: Path, message: str) -> None:
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", message)


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


def test_install_local_copy_creates_managed_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source = tmp_path / "local-source"
    _write_skill(source / "skills", "brainstorming")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    record = manager.install(
        source=source,
        mode="copy",
        scope="project",
        install_id="copied-skill",
    )

    installed_entry = installed_root / "copied-skill"

    assert installed_entry.is_dir() is True
    assert (installed_entry / "skills" / "brainstorming" / "SKILL.md").exists() is True
    assert record["install_id"] == "copied-skill"
    assert record["name"] == "copied-skill"
    assert record["source"] == str(source)
    assert Path(record["installed_path"]) == installed_entry
    assert Path(record["resolved_skill_source_path"]) == installed_entry / "skills"
    assert record["install_mode"] == "copy"
    assert record["scope"] == "project"
    assert manager.load_manifest() == [record]


def test_install_rolls_back_filesystem_and_manifest_when_manifest_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    existing_entry = installed_root / "existing-skill"
    source = tmp_path / "local-source"
    _write_skill(existing_entry, "optimizer")
    _write_skill(source / "skills", "brainstorming")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    existing_record = manager.import_entry(existing_entry, scope="global")
    plugin_manifest_path = tmp_path / ".aworld" / "plugins" / ".manifest.json"
    original_manifest = plugin_manifest_path.read_text(encoding="utf-8")

    def _raise_on_save_manifest() -> None:
        raise OSError("manifest replace failed")

    monkeypatch.setattr(manager.plugin_manager, "_save_manifest", _raise_on_save_manifest)

    with pytest.raises(OSError, match="manifest replace failed"):
        manager.install(
            source=source,
            mode="copy",
            scope="project",
            install_id="copied-skill",
        )

    installed_entry = installed_root / "copied-skill"
    assert installed_entry.exists() is False
    assert installed_entry.is_symlink() is False
    assert plugin_manifest_path.read_text(encoding="utf-8") == original_manifest
    assert manager.load_manifest() == [existing_record]


def test_save_manifest_uses_unique_temp_path_when_default_tmp_name_is_occupied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    occupied_temp_path = manifest_path.with_suffix(f"{manifest_path.suffix}.tmp")
    occupied_temp_path.mkdir(parents=True, exist_ok=True)

    manager.save_manifest(
        [
            {
                "install_id": "demo",
                "name": "demo",
                "source": "/tmp/demo",
                "installed_path": "/tmp/demo",
                "resolved_skill_source_path": "/tmp/demo/skills",
                "install_mode": "manual",
                "scope": "global",
                "installed_at": "2026-04-15T00:00:00+00:00",
            }
        ]
    )

    assert occupied_temp_path.is_dir() is True
    assert manager.load_manifest()[0]["install_id"] == "demo"


def test_save_manifest_updates_symlink_target_without_replacing_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_target = tmp_path / "shared-manifest.json"
    manifest_target.write_text("[]", encoding="utf-8")
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.symlink_to(manifest_target)

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.save_manifest(
        [
            {
                "install_id": "demo",
                "name": "demo",
                "source": "/tmp/demo",
                "installed_path": "/tmp/demo",
                "resolved_skill_source_path": "/tmp/demo/skills",
                "install_mode": "manual",
                "scope": "global",
                "installed_at": "2026-04-15T00:00:00+00:00",
            }
        ]
    )

    assert manifest_path.is_symlink() is True
    assert json.loads(manifest_target.read_text(encoding="utf-8"))[0]["install_id"] == "demo"
    assert manifest_path.resolve() == manifest_target.resolve()


def test_save_manifest_preserves_existing_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.save_manifest([])
    manifest_path.chmod(0o640)
    original_mode = manifest_path.stat().st_mode & 0o777

    manager.save_manifest(
        [
            {
                "install_id": "demo",
                "name": "demo",
                "source": "/tmp/demo",
                "installed_path": "/tmp/demo",
                "resolved_skill_source_path": "/tmp/demo/skills",
                "install_mode": "manual",
                "scope": "global",
                "installed_at": "2026-04-15T00:00:00+00:00",
            }
        ]
    )

    assert (manifest_path.stat().st_mode & 0o777) == original_mode


def test_save_manifest_respects_umask_for_new_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    original_umask = os.umask(0o027)
    try:
        manager.save_manifest([])
    finally:
        os.umask(original_umask)

    assert (manifest_path.stat().st_mode & 0o777) == 0o640


def test_list_installs_reports_skill_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    managed_entry = installed_root / "managed-entry"
    manual_entry = installed_root / "manual-entry"
    _write_skill(managed_entry / "skills", "brainstorming")
    _write_skill(managed_entry / "skills", "writing-plans")
    _write_skill(manual_entry, "agent-browser")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.import_entry(managed_entry, scope="project")

    installs = sorted(manager.list_installs(), key=lambda item: item["install_id"])

    assert [
        (
            item["install_id"],
            item["install_mode"],
            item["scope"],
            item["skill_count"],
        )
        for item in installs
    ] == [
        ("managed-entry", "manual", "project", 2),
        ("manual-entry", "manual", "global", 1),
    ]

    second_listing = sorted(manager.list_installs(), key=lambda item: item["install_id"])

    assert second_listing == installs
    assert [item["install_id"] for item in manager.load_manifest()] == [
        "managed-entry",
        "manual-entry",
    ]


def test_update_git_install_pulls_existing_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source_repo = tmp_path / "git-source"
    source_repo.mkdir()
    _run_git(source_repo, "init")
    _run_git(source_repo, "config", "user.name", "Test User")
    _run_git(source_repo, "config", "user.email", "test@example.com")
    _write_skill(source_repo / "skills", "brainstorming")
    _commit_all(source_repo, "initial commit")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.install(
        source=source_repo,
        mode="clone",
        scope="global",
        install_id="git-skill",
    )

    _write_skill(source_repo / "skills", "writing-plans")
    _commit_all(source_repo, "add another skill")

    updated = manager.update_install("git-skill")
    clone_entry = installed_root / "git-skill"

    assert (clone_entry / "skills" / "writing-plans" / "SKILL.md").exists() is True
    assert updated["install_id"] == "git-skill"
    assert updated["install_mode"] == "clone"
    assert Path(updated["installed_path"]) == clone_entry
    assert Path(updated["resolved_skill_source_path"]) == clone_entry / "skills"
    assert next(
        item for item in manager.list_installs() if item["install_id"] == "git-skill"
    )["skill_count"] == 2


def test_update_install_allows_same_skill_package_with_embedded_framework_plugin_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source_repo = tmp_path / "git-source-with-manifest"
    source_repo.mkdir()
    _run_git(source_repo, "init")
    _run_git(source_repo, "config", "user.name", "Test User")
    _run_git(source_repo, "config", "user.email", "test@example.com")
    _write_skill_package_with_framework_manifest(
        source_repo,
        skill_name="brainstorming",
        plugin_id="embedded-framework-id",
        plugin_name="embedded-framework-name",
    )
    _commit_all(source_repo, "initial commit")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.install(
        source=source_repo,
        mode="clone",
        scope="global",
        install_id="git-skill",
    )

    _write_skill(source_repo / "skills", "writing-plans")
    _commit_all(source_repo, "add another skill")

    updated = manager.update_install("git-skill")

    assert updated["install_id"] == "git-skill"
    assert (installed_root / "git-skill" / "skills" / "writing-plans" / "SKILL.md").exists() is True


def test_update_install_rejects_symlink_backed_installed_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source_repo = tmp_path / "git-source"
    source_repo.mkdir()
    _run_git(source_repo, "init")
    _run_git(source_repo, "config", "user.name", "Test User")
    _run_git(source_repo, "config", "user.email", "test@example.com")
    _write_skill(source_repo / "skills", "brainstorming")
    _commit_all(source_repo, "initial commit")

    linked_entry = installed_root / "git-symlink"
    linked_entry.parent.mkdir(parents=True, exist_ok=True)
    linked_entry.symlink_to(source_repo, target_is_directory=True)

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.save_manifest(
        [
            {
                "install_id": "git-symlink",
                "name": "git-symlink",
                "source": str(source_repo),
                "installed_path": str(linked_entry),
                "resolved_skill_source_path": str(source_repo / "skills"),
                "install_mode": "clone",
                "scope": "global",
                "installed_at": "2026-04-15T00:00:00+00:00",
            }
        ]
    )

    def _unexpected_git_pull(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("git pull should not run for symlink-backed installs")

    monkeypatch.setattr(subprocess, "run", _unexpected_git_pull)

    with pytest.raises(ValueError, match="symlink-backed installed entries cannot be updated"):
        manager.update_install("git-symlink")

    assert manager.load_manifest()[0]["installed_path"] == str(linked_entry)


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


def test_remove_install_unlinks_broken_managed_symlink(
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

    for child in external.iterdir():
        if child.is_file():
            child.unlink()
        else:
            for nested in child.iterdir():
                nested.unlink()
            child.rmdir()
    external.rmdir()

    manager.remove_install("linked")

    assert managed_entry.exists() is False
    assert manager.load_manifest() == []


def test_remove_install_rejects_manifest_path_outside_installed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    external = tmp_path / "external-skills"
    _write_skill(external, "rogue-skill")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.save_manifest(
        [
            {
                "install_id": "rogue",
                "name": "rogue",
                "source": str(external),
                "installed_path": str(external),
                "resolved_skill_source_path": str(external),
                "install_mode": "manual",
                "scope": "global",
                "installed_at": "2026-04-15T00:00:00+00:00",
            }
        ]
    )

    with pytest.raises(ValueError, match="outside the installed root"):
        manager.remove_install("rogue")

    assert external.exists() is True
    assert manager.load_manifest()[0]["installed_path"] == str(external)


def test_import_entry_accepts_noncanonical_path_within_installed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry = installed_root / "demo"
    _write_skill(entry, "optimizer")
    (installed_root / "alias").mkdir(parents=True)

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    record = manager.import_entry(installed_root / "alias" / ".." / "demo", scope="global")

    assert record["install_id"] == "demo"
    assert Path(record["installed_path"]).resolve() == entry.resolve()


def test_import_entry_rejects_path_that_canonicalizes_to_installed_root(
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

    with pytest.raises(ValueError, match="installed root itself"):
        manager.import_entry(installed_root / "demo" / "..", scope="global")


@pytest.mark.parametrize("raw_manifest", ["{}", "[1, 2]"])
def test_load_manifest_rejects_structurally_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_manifest: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manifest_path.write_text(raw_manifest, encoding="utf-8")

    assert manager.load_manifest() == []


def test_remove_install_handles_malformed_manifest_entries_as_unknown_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manifest_path.write_text('[{"broken": true}]', encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown installed skill entry"):
        manager.remove_install("missing")


def test_list_installs_excluding_disabled_does_not_mutate_disabled_scope_or_enablement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry = installed_root / "disabled-pack"
    _write_skill(entry / "skills", "optimizer")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.import_entry(entry, scope="agent:developer")
    manager.plugin_manager.disable("disabled-pack")

    before = dict(manager.plugin_manager._manifest["disabled-pack"])
    before_metadata = dict(before.get("metadata", {}))

    assert manager.list_installs(include_disabled=False) == []

    after = manager.plugin_manager._manifest["disabled-pack"]
    assert after.get("enabled") is False
    assert after.get("metadata", {}).get("scope") == "agent:developer"
    assert after.get("metadata") == before_metadata


def test_load_manifest_skips_malformed_records_with_bad_field_types(
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
    valid_record = manager.import_entry(entry, scope="global")
    manager.save_manifest(
        [
            valid_record,
            {
                "install_id": "bad",
                "name": "bad",
                "source": str(entry),
                "installed_path": 123,
                "resolved_skill_source_path": str(entry),
                "install_mode": "manual",
                "scope": "global",
                "installed_at": "2026-04-15T00:00:00+00:00",
            },
        ]
    )

    manifest = manager.load_manifest()

    assert [item["install_id"] for item in manifest] == ["demo"]
    with pytest.raises(ValueError, match="Unknown installed skill entry"):
        manager.remove_install("bad")


def test_remove_install_can_remove_stale_plugin_managed_record_after_path_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source = tmp_path / "source-skills"
    _write_skill(source / "skills", "optimizer")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.install(source=source, mode="copy", scope="global", install_id="stale-pack")

    stale_entry = installed_root / "stale-pack"
    assert stale_entry.exists() is True
    shutil.rmtree(stale_entry)
    assert stale_entry.exists() is False

    manager.remove_install("stale-pack")

    assert "stale-pack" not in manager.plugin_manager._manifest


def test_update_install_reports_missing_path_for_stale_plugin_managed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source_repo = tmp_path / "git-source"
    source_repo.mkdir()
    _run_git(source_repo, "init")
    _run_git(source_repo, "config", "user.name", "Test User")
    _run_git(source_repo, "config", "user.email", "test@example.com")
    _write_skill(source_repo / "skills", "brainstorming")
    _commit_all(source_repo, "initial commit")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.install(
        source=source_repo,
        mode="clone",
        scope="global",
        install_id="stale-clone",
    )

    stale_clone = installed_root / "stale-clone"
    shutil.rmtree(stale_clone)
    assert stale_clone.exists() is False

    with pytest.raises(ValueError, match="Installed path does not exist"):
        manager.update_install("stale-clone")


def test_install_registers_skill_as_plugin_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source = tmp_path / "source-skills"
    _write_skill(source / "skills", "optimizer")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.install(source=source, mode="copy", scope="global")

    plugins = PluginManager(plugin_dir=tmp_path / ".aworld" / "plugins").list_plugins()
    skill_plugin = next(plugin for plugin in plugins if plugin["name"] == "source-skills")

    assert skill_plugin["package_kind"] == "skill"
    assert skill_plugin["managed_by"] == "skill"
    assert skill_plugin["activation_scope"] == "global"


def test_list_installs_migrates_legacy_manifest_once_and_preserves_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    legacy_manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry = installed_root / "developer-skills"
    _write_skill(entry, "optimizer")
    installed_root.mkdir(parents=True, exist_ok=True)
    legacy_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_manifest_path.write_text(
        json.dumps(
            [
                {
                    "install_id": "developer-skills",
                    "name": "developer-skills",
                    "source": str(entry),
                    "installed_path": str(entry),
                    "resolved_skill_source_path": str(entry),
                    "install_mode": "manual",
                    "scope": "agent:developer",
                    "installed_at": "2026-04-15T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=legacy_manifest_path
    )
    installs = manager.list_installs()

    assert len(installs) == 1
    assert installs[0]["install_id"] == "developer-skills"
    assert installs[0]["scope"] == "agent:developer"
    assert legacy_manifest_path.exists() is False
    assert legacy_manifest_path.with_suffix(".json.migrated").exists() is True

    second = manager.list_installs()
    assert second == installs


def test_load_manifest_merges_plugin_records_with_existing_legacy_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    legacy_manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"

    legacy_entry = installed_root / "legacy-pack"
    plugin_entry = installed_root / "plugin-pack"
    _write_skill(legacy_entry, "legacy-skill")
    _write_skill(plugin_entry, "plugin-skill")
    installed_root.mkdir(parents=True, exist_ok=True)
    legacy_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_manifest_path.write_text(
        json.dumps(
            [
                {
                    "install_id": "legacy-pack",
                    "name": "legacy-pack",
                    "source": str(legacy_entry),
                    "installed_path": str(legacy_entry),
                    "resolved_skill_source_path": str(legacy_entry),
                    "install_mode": "manual",
                    "scope": "global",
                    "installed_at": "2026-04-15T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=legacy_manifest_path
    )
    manager.import_entry(plugin_entry, scope="agent:developer")

    manifest = manager.load_manifest()

    assert [item["install_id"] for item in manifest] == ["legacy-pack", "plugin-pack"]
    plugin_record = next(item for item in manifest if item["install_id"] == "plugin-pack")
    assert plugin_record["scope"] == "agent:developer"


def test_save_manifest_updates_plugin_backed_state_after_legacy_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    legacy_manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry = installed_root / "developer-pack"
    _write_skill(entry, "optimizer")
    installed_root.mkdir(parents=True, exist_ok=True)
    legacy_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_manifest_path.write_text(
        json.dumps(
            [
                {
                    "install_id": "developer-pack",
                    "name": "developer-pack",
                    "source": str(entry),
                    "installed_path": str(entry),
                    "resolved_skill_source_path": str(entry),
                    "install_mode": "manual",
                    "scope": "global",
                    "installed_at": "2026-04-15T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=legacy_manifest_path
    )
    migrated = manager.list_installs()
    assert migrated[0]["scope"] == "global"

    updated_record = {
        "install_id": "developer-pack",
        "name": "developer-pack",
        "source": str(entry),
        "installed_path": str(entry),
        "resolved_skill_source_path": str(entry),
        "install_mode": "manual",
        "scope": "agent:developer",
        "installed_at": "2026-04-15T00:00:00+00:00",
    }
    manager.save_manifest([updated_record])

    manifest = manager.load_manifest()
    installs = manager.list_installs()

    assert manifest == [updated_record]
    assert len(installs) == 1
    assert installs[0]["install_id"] == "developer-pack"
    assert installs[0]["scope"] == "agent:developer"


def test_save_manifest_preserves_legacy_when_plugin_manifest_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry_a = installed_root / "pack-a"
    entry_b = installed_root / "pack-b"
    _write_skill(entry_a, "alpha")
    _write_skill(entry_b, "beta")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.import_entry(entry_a, scope="global")
    manager.import_entry(entry_b, scope="global")

    original_records = sorted(
        manager.load_manifest(), key=lambda item: item["install_id"]
    )
    updated_records = [
        record for record in original_records if record["install_id"] == "pack-a"
    ]
    plugin_manifest_path = tmp_path / ".aworld" / "plugins" / ".manifest.json"
    original_legacy_exists = manifest_path.exists()
    original_legacy_manifest = (
        manifest_path.read_text(encoding="utf-8") if original_legacy_exists else None
    )
    original_plugin_manifest = plugin_manifest_path.read_text(encoding="utf-8")

    def _raise_on_save_manifest() -> None:
        raise RuntimeError("plugin manifest write failed")

    monkeypatch.setattr(manager.plugin_manager, "_save_manifest", _raise_on_save_manifest)

    with pytest.raises(RuntimeError, match="plugin manifest write failed"):
        manager.save_manifest(updated_records)

    assert manifest_path.exists() is original_legacy_exists
    if original_legacy_exists:
        assert manifest_path.read_text(encoding="utf-8") == original_legacy_manifest
    assert plugin_manifest_path.read_text(encoding="utf-8") == original_plugin_manifest
    assert sorted(manager.load_manifest(), key=lambda item: item["install_id"]) == original_records


def test_install_rejects_manifest_key_collision_with_non_skill_plugin_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source = tmp_path / "source-skills"
    _write_skill(source / "skills", "optimizer")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    existing_plugin_path = tmp_path / "existing-plugin"
    existing_plugin_path.mkdir(parents=True, exist_ok=True)
    manager.plugin_manager.upsert_manifest_record(
        "source-skills",
        plugin_path=existing_plugin_path,
        source="manual-plugin",
        package_kind="plugin",
        managed_by="plugin",
        activation_scope="workspace",
    )
    original_plugins = json.loads(
        manager.plugin_manager.manifest_file.read_text(encoding="utf-8")
    )

    with pytest.raises(ValueError, match="conflicts with an existing non-skill plugin"):
        manager.install(
            source=source,
            mode="copy",
            scope="global",
            install_id="source-skills",
        )

    assert (installed_root / "source-skills").exists() is False
    assert json.loads(
        manager.plugin_manager.manifest_file.read_text(encoding="utf-8")
    ) == original_plugins


def test_install_rejects_unmanaged_plugin_directory_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source = tmp_path / "source-skills"
    _write_skill(source / "skills", "optimizer")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    unmanaged_plugin_path = tmp_path / ".aworld" / "plugins" / "source-skills"
    unmanaged_plugin_path.mkdir(parents=True, exist_ok=True)
    original_plugins = (
        json.loads(manager.plugin_manager.manifest_file.read_text(encoding="utf-8"))
        if manager.plugin_manager.manifest_file.exists()
        else {}
    )

    with pytest.raises(ValueError, match="unmanaged plugin directory"):
        manager.install(
            source=source,
            mode="copy",
            scope="global",
            install_id="source-skills",
        )

    assert (installed_root / "source-skills").exists() is False
    persisted_plugins = (
        json.loads(manager.plugin_manager.manifest_file.read_text(encoding="utf-8"))
        if manager.plugin_manager.manifest_file.exists()
        else {}
    )
    assert persisted_plugins == original_plugins


def test_install_rejects_conflict_with_existing_framework_plugin_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source = tmp_path / "source-skills"
    _write_skill(source / "skills", "optimizer")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    plugin_root = tmp_path / ".aworld" / "plugins" / "custom-plugin-root"
    _write_framework_plugin(
        plugin_root,
        plugin_id="framework-plugin-id",
        plugin_name="custom-plugin-name",
    )
    manager.plugin_manager.upsert_manifest_record(
        "custom-plugin-key",
        plugin_path=plugin_root,
        source="manual-plugin",
        package_kind="plugin",
        managed_by="plugin",
        activation_scope="workspace",
    )

    with pytest.raises(ValueError, match="plugin id 'framework-plugin-id'"):
        manager.install(
            source=source,
            mode="copy",
            scope="global",
            install_id="framework-plugin-id",
        )

    assert (installed_root / "framework-plugin-id").exists() is False


def test_install_rejects_conflict_with_builtin_framework_plugin_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source = tmp_path / "source-skills"
    _write_skill(source / "skills", "optimizer")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )

    with pytest.raises(ValueError, match="plugin id 'aworld-hud'"):
        manager.install(
            source=source,
            mode="copy",
            scope="global",
            install_id="aworld-hud",
        )

    assert (installed_root / "aworld-hud").exists() is False


def test_install_rejects_embedded_manifest_plugin_id_collision_with_existing_framework_plugin_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source = tmp_path / "source-skills"
    _write_skill_package_with_framework_manifest(
        source,
        skill_name="optimizer",
        plugin_id="framework-plugin-id",
        plugin_name="embedded-skill-plugin-name",
    )

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    plugin_root = tmp_path / ".aworld" / "plugins" / "custom-plugin-root"
    _write_framework_plugin(
        plugin_root,
        plugin_id="framework-plugin-id",
        plugin_name="custom-plugin-name",
    )
    manager.plugin_manager.upsert_manifest_record(
        "custom-plugin-key",
        plugin_path=plugin_root,
        source="manual-plugin",
        package_kind="plugin",
        managed_by="plugin",
        activation_scope="workspace",
    )

    with pytest.raises(ValueError, match="embedded plugin id 'framework-plugin-id'"):
        manager.install(
            source=source,
            mode="copy",
            scope="global",
            install_id="different-install-id",
        )

    assert (installed_root / "different-install-id").exists() is False


def test_install_rejects_embedded_manifest_plugin_id_collision_with_builtin_framework_plugin_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    source = tmp_path / "source-skills"
    _write_skill_package_with_framework_manifest(
        source,
        skill_name="optimizer",
        plugin_id="aworld-hud",
        plugin_name="embedded-skill-plugin-name",
    )

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )

    with pytest.raises(ValueError, match="embedded plugin id 'aworld-hud'"):
        manager.install(
            source=source,
            mode="copy",
            scope="global",
            install_id="not-aworld-hud",
        )

    assert (installed_root / "not-aworld-hud").exists() is False


def test_legacy_migration_rolls_back_on_later_record_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    legacy_manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    installed_root.mkdir(parents=True, exist_ok=True)
    legacy_manifest_path.parent.mkdir(parents=True, exist_ok=True)

    first_entry = installed_root / "first-pack"
    colliding_entry = installed_root / "colliding-pack"
    _write_skill(first_entry, "alpha")
    _write_skill(colliding_entry, "beta")

    legacy_manifest_path.write_text(
        json.dumps(
            [
                {
                    "install_id": "first-pack",
                    "name": "first-pack",
                    "source": str(first_entry),
                    "installed_path": str(first_entry),
                    "resolved_skill_source_path": str(first_entry),
                    "install_mode": "manual",
                    "scope": "global",
                    "installed_at": "2026-04-15T00:00:00+00:00",
                },
                {
                    "install_id": "colliding-pack",
                    "name": "colliding-pack",
                    "source": str(colliding_entry),
                    "installed_path": str(colliding_entry),
                    "resolved_skill_source_path": str(colliding_entry),
                    "install_mode": "manual",
                    "scope": "global",
                    "installed_at": "2026-04-15T00:00:00+00:00",
                },
            ]
        ),
        encoding="utf-8",
    )

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=legacy_manifest_path
    )
    existing_plugin_path = tmp_path / "existing-plugin"
    existing_plugin_path.mkdir(parents=True, exist_ok=True)
    manager.plugin_manager.upsert_manifest_record(
        "colliding-pack",
        plugin_path=existing_plugin_path,
        source="manual-plugin",
        package_kind="plugin",
        managed_by="plugin",
        activation_scope="workspace",
    )
    original_plugin_manifest = manager.plugin_manager.manifest_file.read_text(
        encoding="utf-8"
    )
    original_legacy_manifest = legacy_manifest_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="conflicts with an existing non-skill plugin"):
        manager.list_installs()

    assert legacy_manifest_path.exists() is True
    assert legacy_manifest_path.read_text(encoding="utf-8") == original_legacy_manifest
    assert legacy_manifest_path.with_suffix(".json.migrated").exists() is False
    assert (
        manager.plugin_manager.manifest_file.read_text(encoding="utf-8")
        == original_plugin_manifest
    )
    plugin_manifest = json.loads(original_plugin_manifest)
    assert "first-pack" not in plugin_manifest


def test_save_manifest_rolls_back_plugin_state_when_legacy_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installed_root = tmp_path / ".aworld" / "skills" / "installed"
    manifest_path = tmp_path / ".aworld" / "skills" / ".manifest.json"
    entry_a = installed_root / "pack-a"
    entry_b = installed_root / "pack-b"
    _write_skill(entry_a, "alpha")
    _write_skill(entry_b, "beta")

    manager = InstalledSkillManager(
        installed_root=installed_root, manifest_path=manifest_path
    )
    manager.import_entry(entry_a, scope="global")
    manager.import_entry(entry_b, scope="global")

    original_records = sorted(
        manager.load_manifest(), key=lambda item: item["install_id"]
    )
    updated_records = [
        record for record in original_records if record["install_id"] == "pack-a"
    ]
    plugin_manifest_path = tmp_path / ".aworld" / "plugins" / ".manifest.json"
    original_plugin_manifest = plugin_manifest_path.read_text(encoding="utf-8")
    original_plugin_memory = dict(manager.plugin_manager._manifest)

    original_write_atomic = manager._write_text_file_atomic

    def _raise_on_legacy_write(path: Path, content: str, *, mode: int | None = None) -> None:
        if path.resolve(strict=False) == manifest_path.resolve(strict=False):
            raise OSError("legacy manifest write failed")
        original_write_atomic(path, content, mode=mode)

    monkeypatch.setattr(manager, "_write_text_file_atomic", _raise_on_legacy_write)

    with pytest.raises(OSError, match="legacy manifest write failed"):
        manager.save_manifest(updated_records)

    assert plugin_manifest_path.read_text(encoding="utf-8") == original_plugin_manifest
    assert manager.plugin_manager._manifest == original_plugin_memory
    assert sorted(manager.load_manifest(), key=lambda item: item["install_id"]) == original_records
