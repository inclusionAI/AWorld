# coding: utf-8
"""
End-to-end regression for natural-language reminders routed through cron.
"""
import json

import pytest

import aworld.core.scheduler as scheduler_module
import aworld.memory.main as memory_main
import aworld.tools.cron_tool as cron_tool_module
import aworld.utils.run_util as run_util
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.scheduler.types import CronJobState
from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.runner import Runners
from aworld.tools import LOCAL_TOOLS_ENV_VAR


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
async def test_natural_language_reminder_runs_through_cron_end_to_end(monkeypatch):
    """The agent should schedule reminders via cron and never execute any other tool."""

    class FakeScheduler:
        def __init__(self):
            self.last_job = None

        async def add_job(self, job):
            self.last_job = job
            job.state = CronJobState(next_run_at="2026-04-11T23:22:00+08:00")
            return job

    fake_scheduler = FakeScheduler()
    executed_tools = []
    llm_call_count = 0

    original_memory_instance = memory_main.MEMORY_HOLDER.get("instance")
    memory_main.MEMORY_HOLDER.clear()
    memory_main.MemoryFactory.init(custom_memory_store=memory_main.InMemoryMemoryStore())

    monkeypatch.setenv(LOCAL_TOOLS_ENV_VAR, "")
    monkeypatch.setattr(scheduler_module, "get_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr(
        cron_tool_module,
        "_parse_natural_language_add_request",
        lambda request, now=None: {
            "name": "提醒：喝水",
            "message": "提醒我喝水",
            "schedule_type": "at",
            "schedule_value": "2026-04-11T23:22:00+08:00",
            "delete_after_run": True,
        },
    )

    original_exec_tool = run_util.exec_tool

    async def recording_exec_tool(*args, **kwargs):
        tool_name = kwargs.get("tool_name") if kwargs else args[0]
        executed_tools.append(tool_name)
        return await original_exec_tool(*args, **kwargs)

    async def fake_acall_llm_model(_llm, messages, model, temperature, tools, stream, context, **kwargs):
        nonlocal llm_call_count
        llm_call_count += 1

        if llm_call_count == 1:
            return ModelResponse(
                id="resp-1",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="call-cron-1",
                        function=Function(
                            name="cron__cron_tool",
                            arguments=json.dumps(
                                {"action": "add", "request": "一分钟后提醒我喝水"},
                                ensure_ascii=False,
                            ),
                        ),
                    )
                ],
            )

        if llm_call_count == 2:
            return ModelResponse(
                id="resp-2",
                model="fake-model",
                content="已为你创建喝水提醒。",
            )

        raise AssertionError(f"Unexpected extra LLM call: {llm_call_count}")

    monkeypatch.setattr(run_util, "exec_tool", recording_exec_tool)
    monkeypatch.setattr("aworld.agents.llm_agent.acall_llm_model", fake_acall_llm_model)

    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
        tool_names=["cron"],
        wait_tool_result=True,
        feedback_tool_result=True,
        system_prompt="For reminder requests, use cron.",
        llm_max_attempts=1,
        llm_retry_delay=0.01,
    )
    agent._llm = object()

    try:
        response = await Runners.run(
            input="一分钟后提醒我喝水",
            agent=agent,
        )

        assert response.success is True
        assert "喝水提醒" in response.answer
        assert fake_scheduler.last_job is not None
        assert fake_scheduler.last_job.name == "提醒：喝水"
        assert fake_scheduler.last_job.payload.message == "提醒我喝水"
        assert fake_scheduler.last_job.schedule.kind == "at"
        assert fake_scheduler.last_job.schedule.at == "2026-04-11T23:22:00+08:00"
        assert fake_scheduler.last_job.delete_after_run is True
        assert executed_tools == ["cron"]
        assert llm_call_count == 2
    finally:
        memory_main.MEMORY_HOLDER.clear()
        if original_memory_instance is not None:
            memory_main.MEMORY_HOLDER["instance"] = original_memory_instance
