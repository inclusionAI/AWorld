import asyncio
from contextlib import asynccontextmanager

import pytest

from aworld.config import ConfigDict
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.task import Task, TaskResponse
from aworld.core.trajectory import (
    TrajectoryBuildStatus,
    TrajectoryFidelity,
    TrajectoryReasonCode,
)
from aworld.core.trajectory_update_registry import TrajectoryUpdateOutcome
from aworld.runners.event_runner import TaskEventRunner


class _Step:
    def __init__(self, step_id="message-1"):
        self.step_id = step_id

    def to_dict(self):
        return {
            "id": self.step_id,
            "meta": {"session_id": "session", "task_id": "task-1"},
            "state": {"input": "question", "messages": [], "context": {}},
            "action": {"content": "answer", "tool_calls": [], "is_agent_finished": True},
            "reward": {"tool_outputs": [], "status": None, "score": None},
        }


class _DatasetFence:
    def __init__(self):
        self.fenced = []

    def fence_task_updates(self, task_id):
        self.fenced.append(task_id)


def _runner(*, timeout=1, sub_task=False):
    context = Context(task_id="task-1")
    context.session = type("Session", (), {"session_id": "session"})()
    conf = ConfigDict({"trajectory_finalize_timeout_seconds": timeout})
    task = Task(id="task-1", name="task", context=context, conf=conf, is_sub_task=sub_task)
    context.set_task(task)
    context.trajectory_dataset = _DatasetFence()
    runner = TaskEventRunner.__new__(TaskEventRunner)
    runner.task = task
    runner.swarm = None
    runner.context = context
    runner.conf = conf
    runner._task_response = TaskResponse(id=task.id, context=context, success=True)
    runner._trajectory_finalize_lock = asyncio.Lock()
    runner._trajectory_finalize_result = None
    return runner, context


@pytest.mark.asyncio
async def test_finalize_waits_for_high_watermark_before_storage_snapshot(monkeypatch):
    runner, context = _runner()
    registry = context.trajectory_update_registry
    registry.open("task-1")
    release = asyncio.Event()
    storage_read = asyncio.Event()
    trajectory = []

    async def delayed_update():
        await release.wait()
        trajectory.append(_Step())
        return TrajectoryUpdateOutcome(True, True, persisted=True)

    async def get_trajectory(task_id, **kwargs):
        assert kwargs == {"strict": True}
        storage_read.set()
        return list(trajectory)

    registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=delayed_update,
    )
    monkeypatch.setattr(context, "get_task_trajectory", get_trajectory)
    finalize = asyncio.create_task(runner._save_trajectories())
    await asyncio.sleep(0)
    assert not storage_read.is_set()

    release.set()
    result = await finalize
    assert storage_read.is_set()
    assert result.status is TrajectoryBuildStatus.COMPLETE
    assert result.fidelity is TrajectoryFidelity.COMPLETE
    assert result.pending_updates == 0
    assert runner._task_response.trajectory[0]["id"] == "message-1"
    assert context.trajectory_dataset.fenced == ["task-1"]


@pytest.mark.asyncio
async def test_failed_update_cannot_masquerade_as_complete(monkeypatch):
    runner, context = _runner()
    registry = context.trajectory_update_registry
    registry.open("task-1")

    async def failed_update():
        return TrajectoryUpdateOutcome(True, False, error="storage failed")

    registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=failed_update,
    )
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([]))
    result = await runner._save_trajectories()

    assert result.status is TrajectoryBuildStatus.FAILED
    assert result.fidelity is TrajectoryFidelity.BUILD_FAILED
    assert result.reason_code is TrajectoryReasonCode.TRAJECTORY_BUILD_FAILED
    assert result.failed_updates == 1


@pytest.mark.asyncio
async def test_snapshot_storage_failure_is_typed_failed(monkeypatch):
    runner, context = _runner()
    context.trajectory_update_registry.open("task-1")

    async def failed_read(task_id, **kwargs):
        assert kwargs == {"strict": True}
        raise RuntimeError("storage read failed")

    monkeypatch.setattr(context, "get_task_trajectory", failed_read)
    result = await runner._save_trajectories()

    assert result.status is TrajectoryBuildStatus.FAILED
    assert result.fidelity is TrajectoryFidelity.BUILD_FAILED
    assert result.reason_code is TrajectoryReasonCode.TRAJECTORY_BUILD_FAILED
    assert result.persisted_items == 0


@pytest.mark.asyncio
async def test_untracked_snapshot_cannot_masquerade_as_complete(monkeypatch):
    runner, context = _runner()
    context.trajectory_update_registry.open("task-1")
    monkeypatch.setattr(
        context,
        "get_task_trajectory",
        lambda task_id, **kwargs: _async_result([_Step()]),
    )

    result = await runner._save_trajectories()

    assert result.status is TrajectoryBuildStatus.PARTIAL
    assert result.fidelity is TrajectoryFidelity.PARTIAL
    assert result.reason_code is TrajectoryReasonCode.SOURCE_NOT_FINALIZED
    assert result.scheduled_updates == 0


async def _async_result(value):
    return value


@pytest.mark.asyncio
async def test_finalize_timeout_is_typed_partial_and_snapshot_is_fenced(monkeypatch):
    runner, context = _runner(timeout=0.01)
    registry = context.trajectory_update_registry
    registry.open("task-1")
    started = asyncio.Event()

    async def blocked_update():
        started.set()
        await asyncio.Event().wait()

    registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=blocked_update,
    )
    await started.wait()
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([]))
    result = await runner._save_trajectories()

    assert result.status is TrajectoryBuildStatus.PARTIAL
    assert result.fidelity is TrajectoryFidelity.PARTIAL
    assert result.reason_code is TrajectoryReasonCode.TRAJECTORY_UPDATE_TIMEOUT
    assert result.pending_updates == 1
    assert context.trajectory_dataset.fenced == ["task-1"]


@pytest.mark.asyncio
async def test_finalize_once_is_idempotent(monkeypatch):
    runner, context = _runner()
    context.trajectory_update_registry.open("task-1")
    reads = 0

    async def get_trajectory(task_id, **kwargs):
        nonlocal reads
        reads += 1
        return [_Step()]

    monkeypatch.setattr(context, "get_task_trajectory", get_trajectory)
    first, second = await asyncio.gather(runner._save_trajectories(), runner._save_trajectories())
    assert first is second
    assert reads == 1


def test_response_creates_missing_taskresponse_before_stripping_context():
    runner, context = _runner()
    runner._task_response = None
    runner.task.conf.resp_carry_context = False

    response = runner._response()
    assert response.success is False
    assert response.context is None


@pytest.mark.asyncio
async def test_runner_cancellation_finalizes_before_reraising(monkeypatch):
    runner, context = _runner(sub_task=True)
    context.trajectory_update_registry.open("task-1")
    runner.init_messages = [Message(payload="start", headers={"context": context})]
    runner.start_time = 0
    entered = asyncio.Event()

    class _Events:
        async def emit_message(self, message):
            return True

    runner.event_mng = _Events()

    @asynccontextmanager
    async def no_trace_span(*args, **kwargs):
        yield

    monkeypatch.setattr("aworld.runners.event_runner.trace.task_span", no_trace_span)

    async def blocked_run():
        entered.set()
        await asyncio.Event().wait()

    async def get_trajectory(task_id, **kwargs):
        return []

    monkeypatch.setattr(runner, "_do_run", blocked_run)
    monkeypatch.setattr(context, "get_task_trajectory", get_trajectory)
    run_task = asyncio.create_task(runner.do_run())
    await entered.wait()
    run_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_task, timeout=2)
    assert runner._task_response.trajectory_build_result is not None
    assert runner._task_response.trajectory_build_result.status is TrajectoryBuildStatus.EMPTY
