import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.config.conf import ModelConfig
from aworld_cli.core.markdown_agent_loader import parse_markdown_agent


def _write_skill(root: Path, skill_name: str) -> None:
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: {skill_name}\n---\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_markdown_loader_stores_resolver_inputs_in_agent_ext(tmp_path: Path) -> None:
    agent_md = tmp_path / "developer.md"
    agent_md.write_text(
        "---\nname: DemoAgent\nskills_path: ./skills\nskill_names: browser-use;regex:^code-.*\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    _write_skill(tmp_path / "skills", "browser-use")
    _write_skill(tmp_path / "skills", "code-review")

    agent = parse_markdown_agent(agent_md)

    assert agent is not None
    swarm = await agent.get_swarm()
    loaded_agent = swarm.ordered_agents[0]

    assert loaded_agent.conf.skill_configs == {}
    assert loaded_agent.conf.ext["skill_resolver_inputs"]["compatibility_sources"] == [
        str((tmp_path / "skills").resolve())
    ]
    assert loaded_agent.conf.ext["skill_resolver_inputs"]["compatibility_skill_patterns"] == [
        "browser-use",
        "regex:^code-.*",
    ]


@pytest.mark.asyncio
async def test_markdown_loader_resolves_model_profile_front_matter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent_md = tmp_path / "judge.md"
    agent_md.write_text(
        "---\nname: JudgeAgent\nmodel_profile: judge\n---\n\n# Judge\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "aworld_cli.core.model_profiles.resolve_model_profile",
        lambda profile: ModelConfig(
            llm_provider="anthropic",
            llm_model_name=f"{profile}-model",
            llm_api_key="profile-key",
        ),
    )

    agent = parse_markdown_agent(agent_md)

    assert agent is not None
    swarm = await agent.get_swarm()
    loaded_agent = swarm.ordered_agents[0]
    assert loaded_agent.conf.llm_config.llm_provider == "anthropic"
    assert loaded_agent.conf.llm_config.llm_model_name == "judge-model"
    assert loaded_agent.conf.llm_config.llm_api_key == "profile-key"
