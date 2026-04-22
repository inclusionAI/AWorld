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
