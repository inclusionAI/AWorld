# coding: utf-8
"""
End-to-end regression for natural-language reminders routed through cron.
"""
import json
from io import StringIO
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

import aworld.core.scheduler as scheduler_module
import aworld.memory.main as memory_main
import aworld.tools.cron_tool as cron_tool_module
import aworld.utils.run_util as run_util
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.scheduler import CronScheduler, FileBasedCronStore
from aworld.core.scheduler.executor import CronExecutor
from aworld.core.scheduler.types import CronJob, CronJobState, CronPayload, CronSchedule
from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.runner import Runners
from aworld.core.task import TaskResponse
from aworld.tools import LOCAL_TOOLS_ENV_VAR
from aworld_cli.console import AWorldCLI
from aworld_cli.runtime.cron_notifications import CronNotificationCenter


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


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
async def test_bounded_recurring_reminder_uses_single_cron_job(monkeypatch):
    """Fixed-count recurring reminders should map to one bounded cron job."""

    class FakeScheduler:
        def __init__(self):
            self.last_job = None

        async def add_job(self, job):
            self.last_job = job
            job.state = CronJobState(next_run_at="2026-04-12T17:48:00+08:00")
            return job

    fake_scheduler = FakeScheduler()
    executed_tools = []
    llm_call_count = 0

    original_memory_instance = memory_main.MEMORY_HOLDER.get("instance")
    memory_main.MEMORY_HOLDER.clear()
    memory_main.MemoryFactory.init(custom_memory_store=memory_main.InMemoryMemoryStore())

    monkeypatch.setenv(LOCAL_TOOLS_ENV_VAR, "")
    monkeypatch.setattr(scheduler_module, "get_scheduler", lambda: fake_scheduler)

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
                id="resp-bounded-1",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="call-cron-bounded-1",
                        function=Function(
                            name="cron__cron_tool",
                            arguments=json.dumps(
                                {"action": "add", "request": "每3分钟给我发一次运动通知，一共发送三次就可以"},
                                ensure_ascii=False,
                            ),
                        ),
                    )
                ],
            )

        if llm_call_count == 2:
            return ModelResponse(
                id="resp-bounded-2",
                model="fake-model",
                content="已为你创建限次运动提醒。",
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
            input="每3分钟给我发一次运动通知，一共发送三次就可以",
            agent=agent,
        )

        assert response.success is True
        assert "限次运动提醒" in response.answer
        assert fake_scheduler.last_job is not None
        assert fake_scheduler.last_job.schedule.kind == "every"
        assert fake_scheduler.last_job.schedule.every_seconds == 180
        assert fake_scheduler.last_job.payload.message == "提醒我运动"
        assert fake_scheduler.last_job.payload.max_runs == 3
        assert executed_tools == ["cron"]
        assert llm_call_count == 2
    finally:
        memory_main.MEMORY_HOLDER.clear()
        if original_memory_instance is not None:
            memory_main.MEMORY_HOLDER["instance"] = original_memory_instance


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
async def test_reminder_cron_execution_renders_reminder_detail_in_cli(monkeypatch, tmp_path):
    """Reminder requests should render explicit reminder content in CLI after cron execution."""
    scheduler = CronScheduler(
        store=FileBasedCronStore(str(tmp_path / "cron.json")),
        executor=AsyncMock(spec=CronExecutor),
    )
    scheduler.executor.execute_with_retry = AsyncMock(
        return_value=TaskResponse(success=True, msg="Success")
    )

    notification_center = CronNotificationCenter()
    scheduler.notification_sink = notification_center.publish

    executed_tools = []
    llm_call_count = 0

    original_memory_instance = memory_main.MEMORY_HOLDER.get("instance")
    memory_main.MEMORY_HOLDER.clear()
    memory_main.MemoryFactory.init(custom_memory_store=memory_main.InMemoryMemoryStore())

    monkeypatch.setenv(LOCAL_TOOLS_ENV_VAR, "")
    monkeypatch.setattr(scheduler_module, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(
        cron_tool_module,
        "_parse_natural_language_add_request",
        lambda request, now=None: {
            "name": "喝水提醒",
            "message": "提醒我喝水",
            "schedule_type": "at",
            "schedule_value": "2026-04-12T16:33:25+08:00",
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

        jobs = await scheduler.list_jobs(enabled_only=False)
        assert len(jobs) == 1
        job = jobs[0]

        result = await scheduler.run_job(job.id, force=True)
        assert result.success is True

        notifications = await notification_center.drain()
        assert len(notifications) == 1
        assert notifications[0].summary == 'Cron task "喝水提醒" completed'
        assert notifications[0].detail == "提醒我喝水"

        cli = AWorldCLI()
        buffer = StringIO()
        cli.console = Console(file=buffer, force_terminal=False, color_system=None)
        cli.render_cron_notifications(notifications)
        rendered_output = buffer.getvalue()

        assert response.success is True
        assert "喝水提醒" in response.answer
        assert executed_tools == ["cron"]
        assert 'Cron task "喝水提醒" completed' in rendered_output
        assert '提醒内容：提醒我喝水' in rendered_output
        assert notification_center.get_unread_count() == 0
    finally:
        memory_main.MEMORY_HOLDER.clear()
        if original_memory_instance is not None:
            memory_main.MEMORY_HOLDER["instance"] = original_memory_instance


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
async def test_reminder_execution_updates_toolbar_unread_count_without_inline_render(monkeypatch, tmp_path):
    """Reminder execution should leave unread notifications for toolbar display."""
    scheduler = CronScheduler(
        store=FileBasedCronStore(str(tmp_path / "cron.json")),
        executor=AsyncMock(spec=CronExecutor),
    )
    scheduler.executor.execute_with_retry = AsyncMock(
        return_value=TaskResponse(success=True, msg="Success")
    )

    notification_center = CronNotificationCenter()
    scheduler.notification_sink = notification_center.publish

    job = await scheduler.add_job(
        CronJob(
            name="喝水提醒",
            schedule=CronSchedule(kind="at", at="2026-04-12T16:33:25+08:00"),
            payload=CronPayload(message="提醒我喝水"),
            delete_after_run=True,
        )
    )

    result = await scheduler.run_job(job.id, force=True)
    assert result.success is True
    assert notification_center.get_unread_count() == 1

    class FakeRuntime:
        def __init__(self, center):
            self._notification_center = center

    cli = AWorldCLI()
    toolbar = cli._build_status_bar_text(FakeRuntime(notification_center), agent_name="Aworld", mode="Chat")

    assert toolbar is not None
    assert "Cron: 1 unread" in toolbar
    assert "Hint: /cron inbox" in toolbar
