import asyncio

import pytest

from aworld.core.trajectory_update_registry import (
    TrajectoryRegistrySealedError,
    TrajectoryRegistryState,
    TrajectoryUpdateOutcome,
    TrajectoryUpdateRegistry,
)
from aworld.dataset.trajectory_dataset import TrajectoryDataset
from aworld.dataset.trajectory_storage import InMemoryTrajectoryStorage
from aworld.dataset.trajectory_strategy import TrajectoryStrategy
from aworld.dataset.types import (
    ExpMeta,
    TrajectoryAction,
    TrajectoryItem,
    TrajectoryReward,
    TrajectoryState,
)
from aworld.core.context.base import Context
from aworld.runners.state_manager import EventRuntimeStateManager


def _success(*, persisted: bool = True) -> TrajectoryUpdateOutcome:
    return TrajectoryUpdateOutcome(
        build_succeeded=True,
        storage_acknowledged=True,
        persisted=persisted,
    )


@pytest.mark.asyncio
async def test_schedule_assigns_monotonic_sequence_and_holds_updates_until_drain():
    registry = TrajectoryUpdateRegistry()
    registry.open("task-1")
    release = asyncio.Event()

    async def delayed():
        await release.wait()
        return _success()

    first = registry.schedule(
        task_id="task-1", logical_step_id="message-1", revision=0, update_factory=delayed
    )
    second = registry.schedule(
        task_id="task-1", logical_step_id="message-1", revision=2, update_factory=delayed
    )

    assert (first.sequence, second.sequence) == (1, 2)
    assert registry.seal("task-1") == 2
    drain_task = asyncio.create_task(registry.drain("task-1", timeout=1))
    await asyncio.sleep(0)
    assert not drain_task.done()

    release.set()
    result = await drain_task
    assert result.scheduled == result.completed == 2
    assert result.failed == result.pending == 0
    assert result.logical_step_ids == ("message-1",)
    assert registry.state("task-1") is TrajectoryRegistryState.DRAINED


@pytest.mark.asyncio
async def test_seal_rejects_late_registration_and_refreshes_diagnostics():
    registry = TrajectoryUpdateRegistry()
    registry.open("task-1")
    registry.seal("task-1")
    first = await registry.drain("task-1", timeout=0)
    assert first.source_not_finalized is False

    with pytest.raises(TrajectoryRegistrySealedError):
        registry.schedule(
            task_id="task-1",
            logical_step_id="late",
            revision=2,
            update_factory=lambda: asyncio.sleep(0, result=_success()),
        )

    refreshed = await registry.drain("task-1", timeout=0)
    assert refreshed.late_registrations == 1
    assert refreshed.source_not_finalized is True


@pytest.mark.asyncio
async def test_builder_or_storage_failure_is_not_counted_completed():
    registry = TrajectoryUpdateRegistry()
    registry.open("task-1")

    async def failed_storage():
        return TrajectoryUpdateOutcome(build_succeeded=True, storage_acknowledged=False)

    registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=failed_storage,
    )
    result = await registry.drain("task-1", timeout=1)
    assert result.scheduled == result.failed == 1
    assert result.completed == result.pending == 0


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_reports_deadline_pending_even_after_cancel():
    registry = TrajectoryUpdateRegistry()
    registry.open("task-1")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    registry.schedule(
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
        update_factory=blocked,
    )
    await started.wait()
    result = await registry.drain("task-1", timeout=0.01)

    assert result.timed_out is True
    assert result.scheduled == result.pending == 1
    assert result.completed == result.failed == 0
    assert cancelled.is_set()


class _RevisionStrategy(TrajectoryStrategy):
    def __init__(self, old_started: asyncio.Event, release_old: asyncio.Event):
        self.old_started = old_started
        self.release_old = release_old

    async def generate(self, task_id, event_runner):
        return None

    async def build_trajectory_state(self, source, **kwargs):
        return TrajectoryState(input=source["value"])

    async def build_trajectory_action(self, source, **kwargs):
        return TrajectoryAction(content=source["value"])

    async def build_trajectory_reward(self, source, **kwargs):
        return TrajectoryReward()

    async def generate_item(self, source, **kwargs):
        if source["revision"] == 0:
            self.old_started.set()
            await self.release_old.wait()
        return TrajectoryItem(
            id=f'legacy-id-{source["revision"]}',
            meta=ExpMeta(session_id="session", task_id="task-1"),
            state=await self.build_trajectory_state(source),
            action=await self.build_trajectory_action(source),
            reward=await self.build_trajectory_reward(source),
        )


@pytest.mark.asyncio
async def test_dataset_revision_fence_prevents_old_completion_overwriting_new_revision():
    old_started = asyncio.Event()
    release_old = asyncio.Event()
    storage = InMemoryTrajectoryStorage()
    dataset = TrajectoryDataset(
        name="trajectory",
        data=[],
        state_manager=EventRuntimeStateManager.instance(),
        storage=storage,
        strategy=_RevisionStrategy(old_started, release_old),
    )

    old = asyncio.create_task(
        dataset.append_trajectory_tracked(
            {"revision": 0, "value": "old"},
            task_id="task-1",
            logical_step_id="message-1",
            revision=0,
        )
    )
    await old_started.wait()
    new = await dataset.append_trajectory_tracked(
        {"revision": 2, "value": "new"},
        task_id="task-1",
        logical_step_id="message-1",
        revision=2,
    )
    release_old.set()
    old_result = await old

    assert new.persisted is True
    assert old_result.superseded is True
    stored = await dataset.get_task_trajectory("task-1")
    assert len(stored) == 1
    assert stored[0].action.content == "new"


@pytest.mark.asyncio
async def test_context_batch_add_uses_shared_registry_and_is_rejected_after_seal():
    context = Context(task_id="task-1")
    child = context.deep_copy()
    calls = []

    class _Dataset:
        async def save_task_trajectory_batch_tracked(self, task_id, trajectory, *, revision):
            calls.append((task_id, trajectory, revision))
            return _success()

    context.trajectory_dataset = _Dataset()
    child.trajectory_dataset = context.trajectory_dataset
    context.trajectory_update_registry.open("task-1")
    assert child.trajectory_update_registry is context.trajectory_update_registry

    await child.add_task_trajectory("task-1", [{"id": "step-1"}], revision=2)
    result = await context.trajectory_update_registry.drain("task-1", timeout=1)
    assert result.completed == 1
    assert calls == [("task-1", [{"id": "step-1"}], 2)]

    with pytest.raises(TrajectoryRegistrySealedError):
        await child.add_task_trajectory("task-1", [{"id": "late"}], revision=3)
