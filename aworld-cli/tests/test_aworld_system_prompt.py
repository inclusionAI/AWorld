from datetime import datetime
from zoneinfo import ZoneInfo

from aworld_cli.builtin_agents.smllc.agents.aworld_agent import render_aworld_system_prompt


def test_render_aworld_system_prompt_injects_beijing_datetime() -> None:
    prompt = render_aworld_system_prompt(
        now=datetime(2026, 5, 10, 1, 8, 7, tzinfo=ZoneInfo("UTC"))
    )

    assert "{{current_date}}" not in prompt
    assert "{{current_datetime}}" not in prompt
    assert "Today is 2026-05-10, 2026-05-10 09:08:07" in prompt
    assert "(Beijing time)" in prompt
