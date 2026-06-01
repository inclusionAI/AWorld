from pathlib import Path

from aworld.skills.execution_assets import (
    build_execution_asset_manifest,
    build_execution_assets_config,
    compute_execution_asset_digest,
)


def test_build_execution_asset_manifest_excludes_skill_markdown_by_default(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\n---\n", encoding="utf-8")
    (skill_dir / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (skill_dir / "config.json").write_text('{"ok": true}\n', encoding="utf-8")

    manifest = build_execution_asset_manifest(skill_dir, declared_assets=None)

    assert "SKILL.md" not in manifest.relative_paths
    assert "run.sh" in manifest.relative_paths
    assert "config.json" in manifest.relative_paths


def test_compute_execution_asset_digest_is_stable_for_same_content(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "run.sh").write_text("echo hi\n", encoding="utf-8")

    manifest = build_execution_asset_manifest(skill_dir, declared_assets=None)

    assert compute_execution_asset_digest(manifest) == compute_execution_asset_digest(
        manifest
    )


def test_build_execution_assets_config_supports_declared_asset_list(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\n---\n", encoding="utf-8")
    (skill_dir / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    config = build_execution_assets_config(
        skill_dir,
        declared_assets=["scripts/run", "notes.md"],
    )

    assert config["enabled"] is True
    assert config["relative_paths"] == ["notes.md", "scripts/run"]
    assert config["digest"]


def test_build_execution_assets_config_includes_scripts_directory_without_suffix(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\n---\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    config = build_execution_assets_config(skill_dir)

    assert config["enabled"] is True
    assert config["relative_paths"] == ["scripts/run"]


def test_build_execution_assets_config_infers_entrypoint_from_skill_usage_reference(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    config = build_execution_assets_config(
        skill_dir,
        usage_text="Run `python scripts/run.py` for this skill.",
        skill_name="demo",
    )

    assert config["entrypoint"] == "scripts/run.py"
    assert "scripts/run.py" in config["relative_paths"]


def test_build_execution_assets_config_resolves_virtual_skill_reference(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "lint_check.py").write_text("print('lint')\n", encoding="utf-8")

    config = build_execution_assets_config(
        skill_dir,
        usage_text="Run `python /skills/demo/lint_check.py .` before review.",
        skill_name="demo",
    )

    assert "lint_check.py" in config["relative_paths"]


def test_build_execution_assets_config_keeps_default_companion_assets_for_declared_entrypoint(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "config.json").write_text('{"debug": true}\n', encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    config = build_execution_assets_config(
        skill_dir,
        entrypoint="scripts/run.py",
    )

    assert config["enabled"] is True
    assert config["entrypoint"] == "scripts/run.py"
    assert config["relative_paths"] == ["config.json", "scripts/run.py"]


def test_build_execution_assets_config_honors_explicit_disable_flag(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "config.json").write_text('{"debug": true}\n', encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    config = build_execution_assets_config(
        skill_dir,
        declared_assets={"enabled": False},
        entrypoint="scripts/run.py",
    )

    assert config == {
        "enabled": False,
        "relative_paths": [],
        "digest": "",
    }
