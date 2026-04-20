import pytest

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.common import ActionResult


@pytest.mark.asyncio
async def test_cron_tool_results_are_reframed_with_confirmed_next_run():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    aggregated = await agent._tools_aggregate_func([
        ActionResult(
            tool_name="cron",
            content={
                "success": True,
                "job_id": "job-123",
                "next_run": "2026-04-14T17:17:00+08:00",
                "next_run_display": "2026年4月14日（星期二）17:17",
                "message": "Created task '喝水提醒' (ID: job-123)",
            },
        )
    ])

    policy_info = aggregated[0].policy_info
    assert "next_run=2026-04-14T17:17:00+08:00" in policy_info
    assert "next_run_display=2026年4月14日（星期二）17:17" in policy_info
    assert "source of truth" in policy_info
    assert "do not reuse any earlier guessed schedule_value" in policy_info
    assert "infer the weekday yourself" in policy_info


@pytest.mark.asyncio
async def test_failed_cron_tool_results_block_false_success_claims():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    aggregated = await agent._tools_aggregate_func([
        ActionResult(
            tool_name="cron",
            content={
                "success": False,
                "error": "One-time schedule is already in the past",
            },
        )
    ])

    policy_info = aggregated[0].policy_info
    assert "Cron returned an error" in policy_info
    assert "Do not claim the reminder or scheduled task was created" in policy_info
