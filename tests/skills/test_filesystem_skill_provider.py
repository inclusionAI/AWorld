from pathlib import Path

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

    docs = collect_skill_docs(tmp_path)

    assert docs["browser-use"]["description"] == "Browser automation"
    assert docs["browser-use"]["usage"] == "# Usage\nUse browser tools."
    assert docs["browser-use"]["asset_root"] == str(skill_dir.resolve())
