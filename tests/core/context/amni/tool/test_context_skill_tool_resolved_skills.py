from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.config import ToolConfig
from aworld.core.context.amni.tool.context_skill_tool import ContextSkillTool


def _build_context_with_skills(skills: dict[str, dict[str, object]]):
    class _Context:
        async def get_skill(self, skill_name: str, namespace: str):
            return skills.get(skill_name, {})

    return _Context()


@pytest.mark.asyncio
async def test_context_skill_tool_reads_resolved_skill_files(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: browser-use\ndescription: browser\n---\n",
        encoding="utf-8",
    )
    (skill_root / "notes.txt").write_text("open page", encoding="utf-8")

    context = _build_context_with_skills(
        {
            "browser-use": {
                "name": "browser-use",
                "usage": "Use browser tools",
                "skill_path": str(skill_root / "SKILL.md"),
                "active": True,
            }
        }
    )

    tool = ContextSkillTool(ToolConfig(name="SKILL"))
    result = await tool._read_skill_file(
        "browser-use",
        "notes.txt",
        namespace="agent-1",
        context=context,
    )

    assert "open page" in result


@pytest.mark.asyncio
async def test_context_skill_tool_prefers_asset_root_for_reading_files(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    asset_root = tmp_path / "plugin-assets" / "browser-use"
    skill_root.mkdir(parents=True)
    asset_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: browser-use\ndescription: browser\n---\n",
        encoding="utf-8",
    )
    (asset_root / "notes.txt").write_text("asset-root content", encoding="utf-8")

    context = _build_context_with_skills(
        {
            "browser-use": {
                "name": "browser-use",
                "usage": "Use browser tools",
                "skill_path": str(skill_root / "SKILL.md"),
                "asset_root": str(asset_root),
                "active": True,
            }
        }
    )

    tool = ContextSkillTool(ToolConfig(name="SKILL"))
    result = await tool._read_skill_file(
        "browser-use",
        "notes.txt",
        namespace="agent-1",
        context=context,
    )

    assert result == "asset-root content"


@pytest.mark.asyncio
async def test_context_skill_tool_prefers_asset_root_for_directory_views(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    asset_root = tmp_path / "plugin-assets" / "browser-use"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: browser-use\ndescription: browser\n---\n",
        encoding="utf-8",
    )
    (skill_root / "skill-only.txt").write_text("wrong root", encoding="utf-8")

    forms_dir = asset_root / "forms"
    forms_dir.mkdir(parents=True)
    (forms_dir / "style_map.json").write_text('{"theme":"ocean"}', encoding="utf-8")

    context = _build_context_with_skills(
        {
            "browser-use": {
                "name": "browser-use",
                "usage": "Use browser tools",
                "skill_path": str(skill_root / "SKILL.md"),
                "asset_root": str(asset_root),
                "active": True,
            }
        }
    )

    tool = ContextSkillTool(ToolConfig(name="SKILL"))
    directory_listing = await tool._list_skill_directory(
        "browser-use",
        "",
        namespace="agent-1",
        context=context,
    )
    tree_listing = await tool._list_skill_file_tree(
        "browser-use",
        namespace="agent-1",
        context=context,
    )

    assert "forms/" in directory_listing
    assert "skill-only.txt" not in directory_listing
    assert "forms/" in tree_listing
    assert "skill-only.txt" not in tree_listing
