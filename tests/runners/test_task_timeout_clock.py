import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message, TopicType
from aworld.core.task import Task, TaskStatusValue
from aworld.runners.event_runner import TaskEventRunner
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.task_runner import TaskRunner
from aworld.self_evolve.replay import _trajectory_task_completion_established
from aworld_cli.executors.continuous import ContinuousExecutor


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(wall=10_000.0, elapsed=100.0)
    source = SimpleNamespace(
        time=lambda: clock.wall,
        monotonic=lambda: clock.elapsed,
    )
    # Patch module references without changing asyncio's own monotonic clock.
    monkeypatch.setattr("aworld.runners.task_runner.time", source)
    monkeypatch.setattr("aworld.runners.event_runner.time", source)
    return clock


def _runner(timeout=60):
    context = Context(task_id="timeout-task")
    context.post_init = AsyncMock()
    context.get_task_status = AsyncMock(return_value=TaskStatusValue.RUNNING)
    context.update_task_status = AsyncMock()
    task = Task(id=context.task_id, input="question", timeout=timeout, context=context)
    runner = TaskEventRunner(task, agent_oriented=False)
    runner._load_tool_module = lambda: None
    runner._stopped = asyncio.Event()
    runner.task_flag = "main"
    return runner, context


def _message(context):
    return Message(
        category=Constants.AGENT,
        payload="partial synthesis",
        headers={"context": context},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wall_jump", [-7200.0, 7200.0])
async def test_wall_clock_jump_does_not_expire_runner_or_handler(clock, wall_jump):
    runner, context = _runner()
    await TaskRunner.pre_run(runner)
    started_at = runner.start_time
    clock.wall += wall_jump
    clock.elapsed += 30

    assert await runner.should_stop_task(None) is False

    # A transported message context must still use the runner's execution clock.
    message_context = Context(task_id=context.task_id)
    message_context.set_task(runner.task)
    message = _message(message_context)
    handler = DefaultHandler(runner)
    for _ in range(2):
        assert [event async for event in handler.handle(message)] == [message]

    assert runner.start_time == started_at
    assert runner.timeout_elapsed_seconds() == 30
    assert runner._task_response is None
    context.update_task_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_execution_entry_rebases_worker_clock_without_changing_report_start(clock):
    runner, context = _runner()
    started_at = runner.start_time

    async def bootstrap():
        clock.elapsed += 5

    context.post_init.side_effect = bootstrap
    # Simulate dispatch to a worker with a different monotonic epoch.
    clock.elapsed = -500.0
    clock.wall += 120
    await TaskRunner.pre_run(runner)
    assert runner.timeout_elapsed_seconds() == 5  # Bootstrap consumes the budget.
    clock.elapsed += 15
    assert runner.timeout_elapsed_seconds() == 20

    # A new execution gets its own budget even when it reuses the task ID.
    next_runner, _ = _runner()
    await TaskRunner.pre_run(next_runner)
    assert next_runner.timeout_elapsed_seconds() == 0
    assert runner.timeout_elapsed_seconds() == 20
    assert runner.start_time == started_at


@pytest.mark.asyncio
async def test_handler_context_timeout_uses_shared_monotonic_budget(clock):
    runner, _ = _runner(timeout=600)
    await TaskRunner.pre_run(runner)
    message_context = Context(task_id="shorter-context-task")
    message_context.set_task(Task(id=message_context.task_id, timeout=10))
    message = _message(message_context)
    clock.elapsed += 11

    assert await runner.should_stop_task(message) is False
    events = [event async for event in DefaultHandler(runner).handle(message)]
    assert len(events) == 1
    assert events[0].topic == TopicType.CANCEL
    assert events[0].payload.stop is True


@pytest.mark.asyncio
async def test_monotonic_expiry_stays_timeout_and_unfinished_synthesis_is_not_completion(clock):
    runner, context = _runner()
    await TaskRunner.pre_run(runner)
    clock.elapsed += 30
    assert await runner.should_stop_task(None) is False
    clock.elapsed += 31

    # Repeated checks must not restart the budget, even if wall time is frozen.
    assert await runner.should_stop_task(None) is True
    response = runner._task_response
    assert response.status == TaskStatusValue.TIMEOUT
    assert response.success is False
    assert response.answer == ""
    assert response.time_cost == 0  # Existing wall-clock reporting is preserved.
    context.update_task_status.assert_awaited_once_with(
        runner.task.id, TaskStatusValue.TIMEOUT
    )

    response.trajectory = [
        {
            "action": {
                "content": "Evidence is ready; the answer is partially streamed",
                "is_agent_finished": False,
                "tool_calls": [],
            }
        },
        {
            "action": {
                "content": "None",
                "is_agent_finished": False,
                "tool_calls": [],
            }
        },
    ]
    executor = SimpleNamespace(
        session_id="timeout-session",
        last_task_response=response,
        chat=AsyncMock(return_value="Task completed. The answer is partially streamed"),
    )
    continuous = ContinuousExecutor(
        executor, console=Console(file=StringIO(), force_terminal=False)
    )
    result = await continuous.run_iteration(1, "question", non_interactive=True)

    assert result["completed"] is False
    assert result["immediate_stop"] is False
    assert _trajectory_task_completion_established(
        response.trajectory, capture_mode="task_response"
    ) is False
