from pathlib import Path

from aworld.skills.compat_provider import build_compat_registry
from aworld.skills.filesystem_provider import FilesystemSkillProvider
from aworld.utils.skill_loader import collect_skill_docs


def test_filesystem_provider_lists_descriptor_without_usage(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Browser automation\n---\n\n# Usage\nUse browser tools.\n",
        encoding="utf-8",
    )

    provider = FilesystemSkillProvider(provider_id="local", root=tmp_path / "skills")
    descriptor = provider.list_descriptors()[0]

    assert descriptor.skill_id == "local:browser-use"
    assert descriptor.description == "Browser automation"
    assert not hasattr(descriptor, "usage")


def test_collect_skill_docs_uses_framework_adapter(tmp_path: Path):
    skill_dir = tmp_path / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Browser automation\n---\n\n# Usage\nUse browser tools.\n",
        encoding="utf-8",
    )
    (skill_dir / "run.sh").write_text("echo browser\n", encoding="utf-8")

    docs = collect_skill_docs(tmp_path)

    assert docs["browser-use"]["description"] == "Browser automation"
    assert docs["browser-use"]["usage"] == "# Usage\nUse browser tools."
    assert docs["browser-use"]["asset_root"] == str(skill_dir.resolve())
    assert docs["browser-use"]["execution_assets"]["enabled"] is True
    assert docs["browser-use"]["execution_assets"]["relative_paths"] == ["run.sh"]


def test_filesystem_provider_parses_declared_execution_assets(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            'description: Browser automation\n'
            'execution_assets: ["notes.md", "scripts/run"]\n'
            "---\n\n"
            "# Usage\nUse browser tools.\n"
        ),
        encoding="utf-8",
    )
    (skill_dir / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run").write_text("#!/bin/sh\necho browser\n", encoding="utf-8")

    provider = FilesystemSkillProvider(provider_id="local", root=tmp_path / "skills")
    descriptor = provider.list_descriptors()[0]

    assert descriptor.execution_assets["enabled"] is True
    assert descriptor.execution_assets["relative_paths"] == ["notes.md", "scripts/run"]


def test_filesystem_provider_exposes_self_evolve_release_state(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            "description: Browser automation\n"
            "self_evolve:\n"
            "  release_state: candidate\n"
            "  run_id: run-1\n"
            "---\n\n"
            "# Usage\nUse browser tools.\n"
        ),
        encoding="utf-8",
    )

    provider = FilesystemSkillProvider(provider_id="local", root=tmp_path / "skills")
    descriptor = provider.list_descriptors()[0]

    assert descriptor.metadata["self_evolve"]["release_state"] == "candidate"
    assert descriptor.metadata["self_evolve"]["run_id"] == "run-1"


def test_collect_skill_docs_exposes_execution_entrypoint_from_usage_reference(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            "description: Browser automation\n"
            "---\n\n"
            "Run `python scripts/run.py` to launch the workflow.\n"
        ),
        encoding="utf-8",
    )
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("print('browser')\n", encoding="utf-8")

    docs = collect_skill_docs(tmp_path)

    assert docs["browser-use"]["execution_assets"]["entrypoint"] == "scripts/run.py"


def test_collect_skill_docs_exposes_execution_entrypoint_from_nested_yaml_metadata(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            "description: Browser automation\n"
            "metadata:\n"
            "  entrypoint: scripts/index.ts\n"
            "---\n\n"
            "Run the browser workflow.\n"
        ),
        encoding="utf-8",
    )
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "index.ts").write_text("console.log('browser');\n", encoding="utf-8")

    docs = collect_skill_docs(tmp_path)

    assert docs["browser-use"]["execution_assets"]["entrypoint"] == "scripts/index.ts"


def test_build_compat_registry_lists_descriptors(tmp_path: Path):
    skill_dir = tmp_path / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Browser automation\n---\n\n# Usage\nUse browser tools.\n",
        encoding="utf-8",
    )

    registry = build_compat_registry(tmp_path)
    descriptors = registry.list_descriptors()

    assert len(descriptors) == 1
    assert descriptors[0].skill_name == "browser-use"
