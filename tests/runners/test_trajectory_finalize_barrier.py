import ast
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from aworld.config import ConfigDict
from aworld.core.common import StreamingMode
from aworld.core.common import TaskItem
from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message, TopicType
from aworld.core.task import Task, TaskResponse
from aworld.core.trajectory import (
    TrajectoryBuildStatus,
    TrajectoryDeliveryState,
    TrajectoryFidelity,
    TrajectoryReasonCode,
)
from aworld.core.trajectory_update_registry import TrajectoryUpdateOutcome
from aworld.dataset.trajectory_io import read_trajectory_records
from aworld.runners.event_runner import TaskEventRunner
from aworld.runners.handler.task import DefaultTaskHandler


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
    runner._execution_started = False
    runner._deferred_task_response = None
    runner._task_response_publish_lock = asyncio.Lock()
    runner._task_response_published = False
    runner.handlers = []
    runner.name = "runner"
    runner.start_time = 0
    runner.state_manager = type(
        "StateManager",
        (),
        {"save_message_handle_result": lambda self, **kwargs: None},
    )()
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


@pytest.mark.asyncio
async def test_dual_mode_writes_v2_only_after_finalized_snapshot(tmp_path, monkeypatch):
    runner, context = _runner()
    registry = context.trajectory_update_registry
    registry.open("task-1")
    jsonl_path = tmp_path / "trajectory.jsonl"
    runner.conf.trajectory_format = "dual"
    runner.conf.trajectory_v2_path = str(jsonl_path)
    legacy_records = []
    release = asyncio.Event()
    trajectory = []

    async def delayed_update():
        await release.wait()
        trajectory.append(_Step())
        return TrajectoryUpdateOutcome(True, True, persisted=True)

    registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=delayed_update,
    )
    monkeypatch.setattr(
        context,
        "get_task_trajectory",
        lambda task_id, **kwargs: _async_result(list(trajectory)),
    )
    monkeypatch.setattr(
        "aworld.runners.event_runner.trajectory_logger.info",
        legacy_records.append,
    )

    finalize = asyncio.create_task(runner._save_trajectories())
    await asyncio.sleep(0)
    assert not jsonl_path.exists()
    release.set()
    result = await finalize

    assert len(legacy_records) == 1
    snapshots = read_trajectory_records(jsonl_path, include_rotations=False).records
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.task_id == "task-1"
    assert snapshot.trajectory == runner._task_response.trajectory
    assert snapshot.trajectory_checksum == result.trajectory_checksum
    assert snapshot.build_result == result.to_dict()


@pytest.mark.asyncio
async def test_v2_mode_persists_typed_empty_without_legacy_record(tmp_path, monkeypatch):
    runner, context = _runner()
    context.trajectory_update_registry.open("task-1")
    jsonl_path = tmp_path / "trajectory.jsonl"
    runner.conf.trajectory_format = "jsonl_v2"
    runner.conf.trajectory_v2_path = str(jsonl_path)
    legacy_records = []
    monkeypatch.setattr(
        context,
        "get_task_trajectory",
        lambda task_id, **kwargs: _async_result([]),
    )
    monkeypatch.setattr(
        "aworld.runners.event_runner.trajectory_logger.info",
        legacy_records.append,
    )

    result = await runner._save_trajectories()

    assert result.status is TrajectoryBuildStatus.EMPTY
    assert legacy_records == []
    snapshots = read_trajectory_records(jsonl_path, include_rotations=False).records
    assert len(snapshots) == 1
    assert snapshots[0].trajectory == []
    assert snapshots[0].build_result["reason_code"] == "trajectory_storage_empty"


@pytest.mark.asyncio
async def test_default_legacy_mode_persists_typed_empty_record(monkeypatch):
    runner, context = _runner()
    context.trajectory_update_registry.open("task-1")
    records = []
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([]))
    monkeypatch.setattr("aworld.runners.event_runner.trajectory_logger.info", records.append)

    result = await runner._save_trajectories()

    assert result.status is TrajectoryBuildStatus.EMPTY
    assert len(records) == 1
    payload = ast.literal_eval(records[0])
    assert payload["trajectory"] == "[]"
    assert payload["trajectory_build_result"]["reason_code"] == "trajectory_storage_empty"
    receipt = runner._task_response.trajectory_delivery_receipt
    assert receipt.legacy.status is TrajectoryDeliveryState.EMITTED
    assert receipt.legacy.reason_code == "legacy_sink_unacknowledged"


@pytest.mark.asyncio
async def test_raw_handler_task_response_is_published_once_only_after_final_revision(monkeypatch):
    runner, context = _runner(sub_task=True)
    context.trajectory_update_registry.open("task-1")
    runner.init_messages = [Message(payload="start", headers={"context": context})]
    runner.start_time = 0
    emitted = []
    trajectory = []
    final_revision_written = asyncio.Event()

    class _Events:
        async def emit_message(self, event):
            emitted.append(event)
            return True

    runner.event_mng = _Events()

    async def no_stop(message):
        return False

    async def inner(results, handlers):
        yield Message(
            payload=runner._task_response,
            topic=TopicType.TASK_RESPONSE,
            session_id="session",
            headers={"context": context},
        )

    async def lifecycle():
        await runner._raw_task([Message(category=Constants.TASK, headers={"context": context})])
        assert not [event for event in emitted if event.topic == TopicType.TASK_RESPONSE]

        async def write_final_revision():
            await asyncio.sleep(0)
            trajectory.append(_Step("final-revision"))
            final_revision_written.set()
            return TrajectoryUpdateOutcome(True, True, persisted=True)

        context.trajectory_update_registry.schedule(
            task_id="task-1",
            logical_step_id="message-1",
            revision=2,
            update_factory=write_final_revision,
        )

    @asynccontextmanager
    async def no_trace_span(*args, **kwargs):
        yield

    monkeypatch.setattr(runner, "should_stop_task", no_stop)
    monkeypatch.setattr(runner, "_inner_handler_process", inner)
    monkeypatch.setattr(runner, "_do_run", lifecycle)
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result(list(trajectory)))
    monkeypatch.setattr("aworld.runners.event_runner.trace.task_span", no_trace_span)

    await runner.do_run()

    assert final_revision_written.is_set()
    responses = [event for event in emitted if event.topic == TopicType.TASK_RESPONSE]
    assert len(responses) == 1
    delivered = responses[0].payload
    assert delivered.trajectory_build_result is not None
    assert delivered.trajectory_checksum is not None
    assert delivered.trajectory[0]["id"] == "final-revision"


@pytest.mark.asyncio
async def test_handle_task_response_uses_same_deferred_delivery_path(monkeypatch):
    runner, context = _runner(sub_task=True)
    emitted = []

    class _Events:
        async def emit_message(self, event):
            emitted.append(event)

    runner.event_mng = _Events()

    async def handler(message):
        return Message(
            payload=runner._task_response,
            topic=TopicType.TASK_RESPONSE,
            headers={"context": context},
        )

    async def inner(results, handlers):
        yield results[0]

    monkeypatch.setattr(runner, "_inner_handler_process", inner)
    await runner._handle_task(Message(headers={"context": context}), handler)

    assert emitted == []
    assert runner._deferred_task_response is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("topic", "payload", "expected_status"),
    [
        (TopicType.FINISHED, "answer", "finished"),
        (TopicType.ERROR, TaskItem(msg="failure", data=None, stop=True), "failed"),
        (TopicType.CANCEL, TaskItem(msg="cancel", data=None, stop=True), "cancelled"),
        (TopicType.INTERRUPT, TaskItem(msg="interrupt", data=None, stop=True), "interrupted"),
    ],
)
async def test_terminal_handler_outcomes_share_finalize_then_publish_order(
    topic, payload, expected_status, monkeypatch
):
    runner, context = _runner(sub_task=True)
    runner._stopped = asyncio.Event()
    runner.hooks = {}
    context.trajectory_update_registry.open("task-1")
    emitted = []

    class _Events:
        async def emit_message(self, event):
            emitted.append(event)

    runner.event_mng = _Events()
    runner.handlers = [DefaultTaskHandler(runner)]
    monkeypatch.setattr(runner, "should_stop_task", lambda message: _async_result(False))
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([]))

    await runner._raw_task(
        [
            Message(
                category=Constants.TASK,
                topic=topic,
                payload=payload,
                session_id="session",
                headers={"context": context},
            )
        ]
    )

    assert not [event for event in emitted if event.topic == TopicType.TASK_RESPONSE]
    await runner._save_trajectories()
    await runner._publish_task_response_once()
    responses = [event for event in emitted if event.topic == TopicType.TASK_RESPONSE]
    assert len(responses) == 1
    assert responses[0].payload.status == expected_status
    assert responses[0].payload.trajectory_build_result is not None


@pytest.mark.asyncio
async def test_run_no_init_message_is_typed_execution_not_started_and_post_run_cannot_mask(monkeypatch):
    runner, context = _runner(sub_task=True)

    async def pre_run():
        runner.init_messages = []

    async def broken_post_run():
        raise AttributeError("post-run fixture missing state")

    monkeypatch.setattr(runner, "pre_run", pre_run)
    monkeypatch.setattr(runner, "post_run", broken_post_run)

    with pytest.raises(Exception, match="no question event"):
        await runner.run()

    result = runner._task_response.trajectory_build_result
    assert result.status is TrajectoryBuildStatus.EMPTY
    assert result.fidelity is TrajectoryFidelity.UNAVAILABLE
    assert result.reason_code is TrajectoryReasonCode.EXECUTION_NOT_STARTED


@pytest.mark.asyncio
async def test_pre_run_failure_before_context_setup_preserves_primary_and_binds_typed_result(monkeypatch):
    runner, _ = _runner(sub_task=True)
    runner.task.context = None
    runner._task_response = None
    del runner.context

    async def broken_pre_run():
        raise RuntimeError("pre-run boom")

    async def broken_post_run():
        raise AttributeError("post-run must not mask")

    monkeypatch.setattr(runner, "pre_run", broken_pre_run)
    monkeypatch.setattr(runner, "post_run", broken_post_run)

    with pytest.raises(RuntimeError, match="pre-run boom"):
        await runner.run()

    result = runner._task_response.trajectory_build_result
    assert result.status is TrajectoryBuildStatus.EMPTY
    assert result.reason_code is TrajectoryReasonCode.EXECUTION_NOT_STARTED
    assert runner._task_response.context is None


@pytest.mark.asyncio
async def test_task_epoch_drives_v2_revision_and_reader_selects_latest(tmp_path, monkeypatch):
    path = tmp_path / "trajectory.jsonl"
    for epoch in (0, 1):
        runner, context = _runner()
        runner.task.trajectory_task_epoch = epoch
        runner.conf.trajectory_format = "jsonl_v2"
        runner.conf.trajectory_v2_path = str(path)
        context.trajectory_update_registry.open("task-1")
        context.trajectory_update_registry.schedule(
            task_id="task-1",
            logical_step_id=f"message-{epoch}",
            revision=2,
            update_factory=lambda: _async_result(TrajectoryUpdateOutcome(True, True, persisted=True)),
        )
        monkeypatch.setattr(
            context,
            "get_task_trajectory",
            lambda task_id, epoch=epoch, **kwargs: _async_result([_Step(f"epoch-{epoch}")]),
        )
        result = await runner._save_trajectories()
        assert result.task_epoch == epoch

    records = read_trajectory_records(path, include_rotations=False).records
    assert len(records) == 1
    assert records[0].revision == 2
    assert records[0].build_result["task_epoch"] == 1
    assert records[0].trajectory[0]["id"] == "epoch-1"


@pytest.mark.asyncio
async def test_export_failure_is_observability_only_and_binds_diagnostic_receipt(monkeypatch):
    runner, context = _runner()
    context.trajectory_update_registry.open("task-1")
    context.trajectory_update_registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=lambda: _async_result(TrajectoryUpdateOutcome(True, True, persisted=True)),
    )
    runner.conf.trajectory_format = "jsonl_v2"
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([_Step()]))

    def fail_append(self, envelope):
        raise OSError("/secret/absolute/path must not escape")

    monkeypatch.setattr("aworld.runners.event_runner.TrajectoryJsonlSink.append", fail_append)
    result = await runner._save_trajectories()

    assert result.status is TrajectoryBuildStatus.COMPLETE
    receipt = runner._task_response.trajectory_delivery_receipt
    assert receipt.v2.status is TrajectoryDeliveryState.FAILED
    assert receipt.v2.error_code == "v2_append_failed"
    assert receipt.v2.record_checksum is not None
    assert "/secret" not in str(receipt.to_dict())


@pytest.mark.asyncio
async def test_sink_config_failure_is_observability_only(monkeypatch):
    runner, context = _runner()
    context.trajectory_update_registry.open("task-1")
    context.trajectory_update_registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=lambda: _async_result(TrajectoryUpdateOutcome(True, True, persisted=True)),
    )
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([_Step()]))

    def fail_config(*args, **kwargs):
        raise ValueError("invalid sink config")

    monkeypatch.setattr(
        "aworld.runners.event_runner.TrajectorySinkConfig.from_sources",
        fail_config,
    )
    result = await runner._save_trajectories()

    assert result.status is TrajectoryBuildStatus.COMPLETE
    receipt = runner._task_response.trajectory_delivery_receipt
    assert receipt.requested_format == "invalid"
    assert receipt.legacy.error_code == "sink_config_invalid"
    assert receipt.v2.error_code == "sink_config_invalid"


@pytest.mark.asyncio
async def test_v2_delivery_uses_ref_only_envelope_when_build_result_has_artifact_ref(
    tmp_path, monkeypatch
):
    runner, context = _runner()
    context.trajectory_update_registry.open("task-1")
    context.trajectory_update_registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=lambda: _async_result(TrajectoryUpdateOutcome(True, True, persisted=True)),
    )
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([_Step()]))
    result = await runner._save_trajectories()
    referenced = replace(result, trajectory_ref="artifact://trajectory/task-1")
    runner.conf.trajectory_format = "jsonl_v2"
    runner.conf.trajectory_v2_path = str(tmp_path / "trajectory.jsonl")
    captured = []

    def capture(self, envelope):
        captured.append(envelope)
        return tmp_path / "trajectory.jsonl"

    monkeypatch.setattr("aworld.runners.event_runner.TrajectoryJsonlSink.append", capture)
    receipt = await runner._deliver_trajectory(
        build_result=referenced,
        inline_trajectory=runner._task_response.trajectory,
        llm_calls=[],
        runner_conf=runner.conf,
    )

    assert captured[0].trajectory is None
    assert receipt.v2.status is TrajectoryDeliveryState.PERSISTED


@pytest.mark.asyncio
async def test_streaming_waiter_terminates_on_finalized_response_without_deadlock(monkeypatch):
    runner, context = _runner(sub_task=True)
    runner.task.streaming_mode = StreamingMode.ALL
    runner.inited = True
    context.trajectory_update_registry.open("task-1")
    context.trajectory_update_registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=lambda: _async_result(TrajectoryUpdateOutcome(True, True, persisted=True)),
    )
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([_Step()]))
    queue = asyncio.Queue()

    class _StreamBus:
        async def get(self, task_id):
            return await queue.get()

    class _Events:
        streaming_eventbus = _StreamBus()

        async def emit_message(self, event):
            if event.topic == TopicType.TASK_RESPONSE:
                await queue.put(event)

    runner.event_mng = _Events()
    await runner._emit_or_defer_task_response(
        Message(
            payload=runner._task_response,
            topic=TopicType.TASK_RESPONSE,
            headers={"context": context},
        )
    )
    waiter = asyncio.create_task(anext(runner.streaming()))
    await runner._save_trajectories()
    await runner._publish_task_response_once()

    event = await asyncio.wait_for(waiter, timeout=1)
    assert event.payload.trajectory_build_result is not None
    assert event.payload.trajectory_checksum is not None


def test_task_rejects_negative_trajectory_epoch():
    with pytest.raises(ValueError, match="non-negative"):
        Task(id="task-1", trajectory_task_epoch=-1)


@pytest.mark.asyncio
async def test_concurrent_streaming_bootstrap_failure_yields_typed_terminal(monkeypatch):
    runner, _ = _runner(sub_task=True)
    runner.task.streaming_mode = StreamingMode.ALL
    runner.task.context = None
    runner._task_response = None
    del runner.context

    async def broken_pre_run():
        await asyncio.sleep(0)
        raise RuntimeError("bootstrap failed")

    async def post_run():
        return None

    monkeypatch.setattr(runner, "pre_run", broken_pre_run)
    monkeypatch.setattr(runner, "post_run", post_run)

    stream_task = asyncio.create_task(anext(runner.streaming()))
    run_task = asyncio.create_task(runner.run())

    with pytest.raises(RuntimeError, match="bootstrap failed"):
        await run_task
    terminal = await asyncio.wait_for(stream_task, timeout=1)
    assert terminal.topic == TopicType.TASK_RESPONSE
    assert terminal.payload.trajectory_build_result.reason_code is TrajectoryReasonCode.EXECUTION_NOT_STARTED


@pytest.mark.asyncio
async def test_emit_failure_before_publish_uses_fallback_and_is_attempted_once(monkeypatch):
    runner, context = _runner(sub_task=True)
    runner.task.streaming_mode = StreamingMode.ALL
    runner.inited = True
    context.trajectory_update_registry.open("task-1")
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([]))
    await runner._save_trajectories()
    bus_queue = asyncio.Queue()
    attempts = 0

    class _Bus:
        async def get(self, task_id):
            return await bus_queue.get()

    class _Events:
        streaming_eventbus = _Bus()

        async def emit_message(self, event):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("emit failed before publish")

    runner.event_mng = _Events()
    waiter = asyncio.create_task(anext(runner.streaming()))

    assert await runner._publish_task_response_once() is False
    assert await runner._publish_task_response_once() is False
    terminal = await asyncio.wait_for(waiter, timeout=1)
    assert attempts == 1
    assert terminal.payload is runner._task_response
    assert terminal.payload.trajectory_build_result is not None


@pytest.mark.asyncio
async def test_partial_publish_failure_prefers_bus_terminal_over_fallback(monkeypatch):
    runner, context = _runner(sub_task=True)
    runner.task.streaming_mode = StreamingMode.ALL
    runner.inited = True
    context.trajectory_update_registry.open("task-1")
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([]))
    await runner._save_trajectories()
    bus_queue = asyncio.Queue()
    attempts = 0

    class _Bus:
        async def get(self, task_id):
            return await bus_queue.get()

    class _Events:
        streaming_eventbus = _Bus()

        async def emit_message(self, event):
            nonlocal attempts
            attempts += 1
            bus_event = Message(
                payload=event.payload,
                category=event.category,
                topic=event.topic,
                headers={"context": context, "delivery_source": "bus"},
            )
            await bus_queue.put(bus_event)
            raise RuntimeError("emit failed after partial publish")

    runner.event_mng = _Events()
    waiter = asyncio.create_task(anext(runner.streaming()))

    assert await runner._publish_task_response_once() is False
    assert await runner._publish_task_response_once() is False
    terminal = await asyncio.wait_for(waiter, timeout=1)
    assert attempts == 1
    assert terminal.headers["delivery_source"] == "bus"


@pytest.mark.asyncio
@pytest.mark.parametrize("business_error", [None, ValueError("business failed")])
async def test_emit_failure_never_overwrites_business_result_or_exception(
    business_error, monkeypatch
):
    runner, context = _runner(sub_task=True)
    context.trajectory_update_registry.open("task-1")
    runner.init_messages = [Message(payload="start", headers={"context": context})]
    monkeypatch.setattr(context, "get_task_trajectory", lambda task_id, **kwargs: _async_result([]))
    response_attempts = 0

    class _Events:
        async def emit_message(self, event):
            nonlocal response_attempts
            if event.topic == TopicType.TASK_RESPONSE:
                response_attempts += 1
                raise RuntimeError("terminal emit failed")
            return True

    runner.event_mng = _Events()

    async def business_run():
        if business_error is not None:
            raise business_error
        runner._task_response.answer = "business answer"

    @asynccontextmanager
    async def no_trace_span(*args, **kwargs):
        yield

    monkeypatch.setattr(runner, "_do_run", business_run)
    monkeypatch.setattr("aworld.runners.event_runner.trace.task_span", no_trace_span)

    if business_error is None:
        result = await runner.do_run()
        assert result.answer == "business answer"
    else:
        with pytest.raises(ValueError, match="business failed"):
            await runner.do_run()
    assert response_attempts == 1
