from datetime import datetime, timedelta

import aworld_cli.builtin_agents.smllc.agents.aworld_agent as aworld_agent
from zoneinfo import ZoneInfo

from aworld_cli.builtin_agents.smllc.agents.aworld_agent import render_aworld_system_prompt


def test_beijing_timezone_falls_back_without_system_tzdata(monkeypatch) -> None:
    def missing_zone(_name: str):
        raise aworld_agent.ZoneInfoNotFoundError

    monkeypatch.setattr(aworld_agent, "ZoneInfo", missing_zone)

    fallback = aworld_agent._beijing_timezone()

    assert fallback.utcoffset(None) == timedelta(hours=8)
    assert fallback.tzname(None) == "Asia/Shanghai"


def test_render_aworld_system_prompt_injects_beijing_datetime() -> None:
    prompt = render_aworld_system_prompt(
        now=datetime(2026, 5, 10, 1, 8, 7, tzinfo=ZoneInfo("UTC"))
    )

    assert "{{current_date}}" not in prompt
    assert "{{current_datetime}}" not in prompt
    assert "Today is 2026-05-10, 2026-05-10 09:08:07" in prompt
    assert "(Beijing time)" in prompt


def test_aworld_subagents_can_be_disabled_for_constrained_runtimes(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_ENABLE_SUBAGENTS", "false")

    assert aworld_agent.aworld_subagents_enabled() is False

    monkeypatch.delenv("AWORLD_ENABLE_SUBAGENTS")
    assert aworld_agent.aworld_subagents_enabled() is True
