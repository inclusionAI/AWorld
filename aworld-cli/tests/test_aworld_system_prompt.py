from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aworld_cli.builtin_agents.smllc.agents import aworld_agent
from aworld_cli.builtin_agents.smllc.agents.aworld_agent import (
    render_aworld_system_prompt,
    resolve_aworld_max_loop_steps,
)


def test_render_aworld_system_prompt_injects_beijing_datetime() -> None:
    prompt = render_aworld_system_prompt(
        now=datetime(2026, 5, 10, 1, 8, 7, tzinfo=ZoneInfo("UTC"))
    )

    assert "{{current_date}}" not in prompt
    assert "{{current_datetime}}" not in prompt
    assert "Today is 2026-05-10, 2026-05-10 09:08:07" in prompt
    assert "(Beijing time)" in prompt


def test_render_aworld_system_prompt_uses_runtime_capabilities() -> None:
    prompt = render_aworld_system_prompt(
        available_tools=["terminal", "cron"],
        available_subagents=["developer"],
    )

    assert "Available tool capabilities: cron, terminal" in prompt
    assert "Available subagents: developer" in prompt
    assert "Use its exact listed name" in prompt
    assert "one and only one" not in prompt.lower()


def test_render_aworld_system_prompt_disables_unavailable_delegation() -> None:
    prompt = render_aworld_system_prompt(available_tools=["terminal"])

    assert "Available subagents: none" in prompt
    assert "do not attempt delegation" in prompt
    assert "developer" not in prompt


def test_aworld_max_loop_steps_defaults_to_40(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWORLD_MAX_LOOP_STEPS", raising=False)

    assert resolve_aworld_max_loop_steps() == 40


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_aworld_max_loop_steps_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("AWORLD_MAX_LOOP_STEPS", value)

    with pytest.raises(ValueError, match="positive integer"):
        resolve_aworld_max_loop_steps()


def test_optional_subagent_failures_do_not_hide_healthy_subagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAgent:
        def __init__(self, name: str) -> None:
            self._name = name

        def name(self) -> str:
            return self._name

    monkeypatch.setattr(aworld_agent, "_CAST_TOOLS_AVAILABLE", False)
    monkeypatch.setattr(
        aworld_agent,
        "extract_agents_from_swarm",
        lambda swarm: [FakeAgent(swarm)],
    )
    monkeypatch.setattr(aworld_agent, "build_diffusion_swarm", lambda: "diffusion")
    monkeypatch.setattr(
        aworld_agent,
        "build_avatar_swarm",
        lambda: (_ for _ in ()).throw(RuntimeError("not configured")),
    )
    monkeypatch.setattr(aworld_agent, "build_audio_swarm", lambda: "audio")
    monkeypatch.setattr(aworld_agent, "build_image_swarm", lambda: "image")

    agents = aworld_agent._build_aworld_sub_agents(sandbox=object())

    assert aworld_agent._subagent_names(agents) == ["audio", "diffusion", "image"]
