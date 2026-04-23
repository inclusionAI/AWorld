from pathlib import Path

from aworld.agents.swarm_composer_agent import SwarmComposerAgent


def test_load_skills_info_uses_descriptor_data_without_loading_body(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "browser-use"
    skill_dir.mkdir(parents=True)
    with (skill_dir / "SKILL.md").open("wb") as handle:
        handle.write(
            b"---\n"
            b"name: browser-use\n"
            b"description: Browser automation\n"
            b'tool_list: {"browser": {"desc": "Browser MCP"}}\n'
            b"---\n\n"
        )
        handle.write(b"\xff\xfe\xfa")

    agent = SwarmComposerAgent.__new__(SwarmComposerAgent)

    skills_info = agent._load_skills_info(skills_root)

    assert skills_info["browser-use"]["description"] == "Browser automation"
    assert skills_info["browser-use"]["tool_list"] == {
        "browser": {"desc": "Browser MCP"}
    }
    assert skills_info["browser-use"]["asset_root"] == str(skill_dir.resolve())
