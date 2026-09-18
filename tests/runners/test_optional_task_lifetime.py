import asyncio
import json
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aworld.core.task import Task, TaskStatusValue
from aworld.runners.event_runner import TaskEventRunner


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(wall=10000.0, monotonic=50.0)
    monkeypatch.setattr("aworld.core.task.time", SimpleNamespace(
        time=lambda: clock.wall, monotonic=lambda: clock.monotonic))
    return clock


def advance(clock, seconds):
    clock.wall += seconds
    clock.monotonic += seconds


def test_default_and_none_are_unbounded_beyond_an_hour(clock):
    for task in (Task(), Task(timeout=None)):
        advance(clock, 7200)
        assert task.remaining_seconds() is None
        assert task.to_dict()["deadline_epoch_seconds"] is None


@pytest.mark.parametrize("value", [0, -1, True, "60", float("nan"), float("inf")])
def test_invalid_explicit_duration_fails_closed(value):
    with pytest.raises(ValueError, match="timeout"):
        Task(timeout=value)


@pytest.mark.parametrize("value", [-1, True, "60", float("nan"), float("inf")])
def test_invalid_explicit_epoch_fails_closed(value):
    with pytest.raises(ValueError, match="deadline_epoch_seconds"):
        Task(deadline_epoch_seconds=value)


def test_duration_is_not_renewed_by_retry_serialization_or_child(clock):
    task = Task(timeout=100)
    advance(clock, 70)
    assert task.remaining_seconds() == 30
    task.timeout = 1000  # Retry/reset code cannot renew the original budget.
    task.deadline_epoch_seconds = clock.wall + 1000
    assert task.bind_deadline() == 10100
    serialized = json.loads(json.dumps(task.to_dict()))
    restored = Task(timeout=serialized["timeout"], deadline_epoch_seconds=serialized["deadline_epoch_seconds"])
    child = Task(timeout=500, parent_task=restored)
    assert child.remaining_seconds() == restored.remaining_seconds() == 30
    advance(clock, 30)
    assert restored.remaining_seconds() == child.remaining_seconds() == 0


def test_live_budget_does_not_extend_on_wall_clock_rollback(clock):
    task = Task(timeout=100)
    clock.wall -= 1000
    clock.monotonic += 80
    assert task.remaining_seconds() == 20
    child = Task(timeout=100, parent_task=task)
    assert child.remaining_seconds() == 20


def runner_for(task):
    runner = TaskEventRunner(task, agent_oriented=False)
    runner.context = SimpleNamespace(
        get_task_status=AsyncMock(return_value=TaskStatusValue.RUNNING),
        update_task_status=AsyncMock(),
    )
    return runner


@pytest.mark.asyncio
@pytest.mark.parametrize("action,status", [("request_cancel", TaskStatusValue.CANCELLED), ("request_pause", TaskStatusValue.INTERRUPTED)])
async def test_unbounded_runner_stops_promptly_on_control_during_bootstrap(clock, action, status):
    task = Task()
    runner = runner_for(task)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_bootstrap():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runner._run_lifecycle = blocked_bootstrap
    running = asyncio.create_task(runner.run())
    await entered.wait()
    advance(clock, 7200)
    await asyncio.sleep(0.12)
    assert not running.done()
    getattr(task, action)()
    response = await asyncio.wait_for(running, 1)
    assert cancelled.is_set()
    assert response.status == status
    assert response.success is False
    assert response.recoverable is False


@pytest.mark.asyncio
async def test_explicit_deadline_cancels_work_even_when_no_events_arrive(clock):
    runner = runner_for(Task(timeout=100))
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_work():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runner._run_lifecycle = blocked_work
    running = asyncio.create_task(runner.run())
    await entered.wait()
    advance(clock, 101)
    response = await asyncio.wait_for(running, 1)
    assert cancelled.is_set()
    assert response.status == TaskStatusValue.TIMEOUT
    assert response.semantic_status == "budget_exhausted"
    assert response.failure_code == "task_timeout"


@pytest.mark.asyncio
async def test_expired_task_does_not_start_and_closes_output(clock):
    task = Task(deadline_epoch_seconds=clock.wall - 1)
    task.outputs = SimpleNamespace(mark_completed=AsyncMock())
    runner = runner_for(task)
    runner._run_lifecycle = AsyncMock()
    runner._finalize_execution_not_started_for_delivery = AsyncMock()
    response = await runner.run()
    runner._run_lifecycle.assert_not_called()
    task.outputs.mark_completed.assert_awaited_once_with(response)
    assert response.success is False
    assert response.status == TaskStatusValue.TIMEOUT


@pytest.mark.asyncio
async def test_expired_task_real_streamed_runner_finalizes_without_calling_agent():
    from aworld.agents.llm_agent import Agent
    from aworld.config import AgentConfig
    from aworld.runner import Runners

    agent = Agent(name="expired-no-model-call", conf=AgentConfig())
    agent.async_run = AsyncMock()
    task = Task(agent=agent, input="must not run", deadline_epoch_seconds=0)
    outputs = Runners.streamed_run_task(task)
    async def drain():
        return [event async for event in outputs.stream_events()]
    await asyncio.wait_for(drain(), 2)
    result = await outputs._run_impl_task
    response = result[task.id]
    assert not response.success and response.status == TaskStatusValue.TIMEOUT
    assert response.trajectory_build_result.reason_code.value == "execution_not_started"
    agent.async_run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic,expected", [("incomplete", TaskStatusValue.INCOMPLETE), ("budget_exhausted", TaskStatusValue.BUDGET_EXHAUSTED), ("running", TaskStatusValue.INCOMPLETE)])
async def test_finished_event_cannot_promote_scoped_incomplete_work(semantic, expected):
    from aworld.core.event.base import Message, Constants, TopicType
    from aworld.runners.handler.task import DefaultTaskHandler

    task = Task(id="same-task")
    context = SimpleNamespace(
        task_id=task.id, task_epoch=None, token_usage={},
        context_info={"agent_execution_state": {
            "schema_version":"aworld.agent.execution-state/v1", "task_id":task.id,
            "task_epoch":None, "status":semantic, "reason":"needs_more_work", "recoverable":True,
        }},
        merge_context=lambda _:None,
        assess_completion_contract=lambda **_:None,
    )
    runner = SimpleNamespace(task=task, context=context, start_time=0, stop=AsyncMock())
    handler = DefaultTaskHandler(runner)
    message = Message(category=Constants.TASK, topic=TopicType.FINISHED,
                      payload="I finished", headers={"context":context})
    events = [event async for event in handler._do_handle(message)]
    response = events[-1].payload
    assert not response.success
    assert response.status == expected
    assert response.recoverable
    assert response.to_dict()["semantic_status"] in {"incomplete", "budget_exhausted"}


@pytest.mark.asyncio
async def test_process_runtime_explicit_budget_cancels_owned_workers(monkeypatch):
    from aworld.config import RunConfig
    from aworld.runners.runtime_engine import LocalRuntime

    future=Future()
    worker=MagicMock()
    pool=MagicMock()
    pool._processes={1:worker}
    pool.submit.return_value=future
    monkeypatch.setattr("aworld.runners.runtime_engine.ProcessPoolExecutor", lambda _:pool)
    runtime=LocalRuntime(RunConfig(reuse_process=False,worker_num=1))
    runtime.conf["timeout"]=0.01
    with pytest.raises(asyncio.TimeoutError):
        await runtime.execute([lambda:None])
    worker.terminate.assert_called_once()
    pool.shutdown.assert_called_once_with(wait=False,cancel_futures=True)


@pytest.mark.asyncio
async def test_process_runtime_no_default_cap_and_caller_cancel_is_prompt(monkeypatch, clock):
    from aworld.config import RunConfig
    from aworld.runners.runtime_engine import LocalRuntime

    future=Future()
    worker=MagicMock()
    pool=MagicMock()
    pool._processes={1:worker}
    pool.submit.return_value=future
    monkeypatch.setattr("aworld.runners.runtime_engine.ProcessPoolExecutor", lambda _:pool)
    runtime=LocalRuntime(RunConfig(reuse_process=False,worker_num=1))
    running=asyncio.create_task(runtime.execute([lambda:None]))
    await asyncio.sleep(0)
    advance(clock,7200)
    await asyncio.sleep(0)
    assert not running.done()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running,1)
    worker.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_yaml_reload_retains_absolute_budget_despite_override(monkeypatch, clock, tmp_path):
    from aworld.config.task_loader import load_task_from_yaml
    task=Task(id="saved", timeout=100)
    plan=tmp_path/"task.yaml"
    plan.write_text("task:\n  task_id: saved\n  query: work\n  timeout: 100\n  deadline_epoch_seconds: 10100\n")
    monkeypatch.setattr("aworld.config.task_loader._load_agents", AsyncMock(return_value={}))
    monkeypatch.setattr("aworld.config.task_loader._build_swarm", lambda *_:SimpleNamespace(build_type="test",agents={}))
    advance(clock,90)
    restored=await load_task_from_yaml(plan, deadline_epoch_seconds=clock.wall+1000)
    assert restored.id == task.id
    assert restored.remaining_seconds() == 10
