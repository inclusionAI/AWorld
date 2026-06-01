from pathlib import Path

import pytest

from aworld.config.task_loader import _load_skill_agent


@pytest.mark.asyncio
async def test_load_skill_agent_uses_descriptor_lookup_before_loading_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL_NAME", "gpt-4")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    skills_root = tmp_path / "skills"
    good_skill_dir = skills_root / "planner"
    good_skill_dir.mkdir(parents=True)
    (good_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: planner\n"
        "description: Planning skill\n"
        'tool_list: {"browser": {"desc": "Browser MCP"}}\n'
        "type: agent\n"
        "---\n\n"
        "# Planner\n"
        "Use this skill for planning.\n",
        encoding="utf-8",
    )

    broken_skill_dir = skills_root / "broken-skill"
    broken_skill_dir.mkdir(parents=True)
    with (broken_skill_dir / "SKILL.md").open("wb") as handle:
        handle.write(
            b"---\n"
            b"name: broken-skill\n"
            b"description: Broken skill\n"
            b"type: agent\n"
            b"---\n\n"
        )
        handle.write(b"\xff\xfe\xfa")

    agent = await _load_skill_agent(
        agent_id="planner-agent",
        agent_def={
            "skill_name": "planner",
            "config": {
                "skill_configs": {
                    "browser-use": {
                        "name": "browser-use",
                        "description": "Browser automation",
                        "tool_list": {"browser": {"desc": "Browser MCP"}},
                        "usage": "Use browser skill.",
                        "type": "agent",
                        "active": False,
                        "skill_path": "/tmp/browser/SKILL.md",
                        "asset_root": "/tmp/browser",
                        "path_aliases": ["/skills/browser-use"],
                        "execution_assets": {
                            "enabled": True,
                            "relative_paths": ["run.sh"],
                            "digest": "feed1234feed1234",
                        },
                    }
                }
            },
        },
        skills_path=skills_root,
        global_mcp_config=None,
    )

    assert agent.name() == "planner-agent"
    assert agent.desc() == "Planning skill"
    assert "browser" in agent.mcp_servers
    assert "Use this skill for planning." in agent.system_prompt
    assert "browser-use" in agent.conf.skill_configs
    assert agent.conf.skill_configs["planner"]["asset_root"] == str(good_skill_dir.resolve())
    assert agent.conf.skill_configs["planner"]["execution_assets"]["enabled"] is False
    assert agent.conf.skill_configs["browser-use"]["asset_root"] == "/tmp/browser"
    assert agent.skill_configs["planner"]["asset_root"] == str(good_skill_dir.resolve())
    assert agent.skill_configs["browser-use"]["asset_root"] == "/tmp/browser"
    assert agent.sandbox.skill_configs["planner"]["asset_root"] == str(good_skill_dir.resolve())
    assert agent.sandbox.skill_configs["browser-use"]["asset_root"] == "/tmp/browser"
