from __future__ import annotations

import asyncio

import pytest

from aworld.core.task import Task, TaskResponse
from aworld.runners.batch import (
    DeterministicTaskBatchExecutor,
    TaskBatchItem,
    TaskResourceClaim,
)


def _item(
    index: int,
    *,
    claims: tuple[TaskResourceClaim, ...] = (),
) -> TaskBatchItem:
    return TaskBatchItem(
        index=index,
        task=Task(id=f"task-{index}", input=index),
        resource_claims=claims,
    )


@pytest.mark.asyncio
async def test_batch_preserves_index_order_and_bounds_concurrency() -> None:
    release = asyncio.Event()
    two_started = asyncio.Event()
    active = 0
    max_active = 0

    async def run_task(task: Task):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            two_started.set()
        await release.wait()
        active -= 1
        return {
            task.id: TaskResponse(
                id=task.id,
                answer=task.input,
                success=True,
            )
        }

    executor = DeterministicTaskBatchExecutor(run_task=run_task)
    running = asyncio.create_task(
        executor.run(
            [_item(2), _item(0), _item(1)],
            max_concurrency=2,
            failure_policy="collect_all",
        )
    )
    await asyncio.wait_for(two_started.wait(), timeout=1)
    assert max_active == 2
    release.set()

    results = await running

    assert [result.index for result in results] == [0, 1, 2]
    assert [result.response.answer for result in results] == [0, 1, 2]
    assert executor.last_run_observability["max_observed_concurrency"] == 2


@pytest.mark.asyncio
async def test_collect_all_returns_failed_responses_in_index_order() -> None:
    async def run_task(task: Task):
        return {
            task.id: TaskResponse(
                id=task.id,
                answer=task.input,
                success=task.input != 1,
                msg="failed" if task.input == 1 else None,
            )
        }

    results = await DeterministicTaskBatchExecutor(run_task=run_task).run(
        [_item(2), _item(1), _item(0)],
        max_concurrency=3,
        failure_policy="collect_all",
    )

    assert [result.index for result in results] == [0, 1, 2]
    assert [result.status for result in results] == ["succeeded", "failed", "succeeded"]


@pytest.mark.asyncio
async def test_indexed_fail_fast_discards_higher_indexes() -> None:
    index_zero_release = asyncio.Event()
    index_zero_started = asyncio.Event()

    async def run_task(task: Task):
        if task.input == 0:
            index_zero_started.set()
            await index_zero_release.wait()
            return {task.id: TaskResponse(id=task.id, success=True, answer=0)}
        if task.input == 1:
            await index_zero_started.wait()
            return {task.id: TaskResponse(id=task.id, success=False, msg="boom")}
        await asyncio.sleep(30)
        return {task.id: TaskResponse(id=task.id, success=True, answer=task.input)}

    executor = DeterministicTaskBatchExecutor(run_task=run_task)
    running = asyncio.create_task(
        executor.run(
            [_item(0), _item(1), _item(2)],
            max_concurrency=2,
            failure_policy="indexed_fail_fast",
        )
    )
    await index_zero_started.wait()
    await asyncio.sleep(0)
    index_zero_release.set()

    results = await running

    assert [result.status for result in results] == ["succeeded", "failed", "discarded"]
    assert results[2].response is None
    assert executor.last_run_observability["failure_cutoff_index"] == 1


@pytest.mark.asyncio
async def test_discarded_completed_result_retains_usage_without_response() -> None:
    higher_index_completed = asyncio.Event()

    async def run_task(task: Task):
        if task.input == 1:
            await higher_index_completed.wait()
            return {
                task.id: TaskResponse(
                    id=task.id,
                    success=False,
                    msg="boom",
                    usage={"total_tokens": 20},
                )
            }
        if task.input == 2:
            higher_index_completed.set()
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer=task.input,
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                    "provider_payload": "must-not-be-retained",
                },
            )
        }

    results = await DeterministicTaskBatchExecutor(run_task=run_task).run(
        [_item(0), _item(1), _item(2)],
        max_concurrency=3,
        failure_policy="indexed_fail_fast",
    )

    discarded = results[2]
    assert discarded.status == "discarded"
    assert discarded.response is None
    assert discarded.usage_metadata == {
        "completion_tokens": 20,
        "prompt_tokens": 10,
        "total_tokens": 30,
    }


@pytest.mark.asyncio
async def test_exclusive_resource_claims_serialize_same_key() -> None:
    active_by_key: dict[str, int] = {}
    max_by_key: dict[str, int] = {}
    overall_active = 0
    max_overall = 0

    async def run_task(task: Task):
        nonlocal overall_active, max_overall
        key = "browser" if task.input in {0, 1} else "filesystem"
        active_by_key[key] = active_by_key.get(key, 0) + 1
        max_by_key[key] = max(max_by_key.get(key, 0), active_by_key[key])
        overall_active += 1
        max_overall = max(max_overall, overall_active)
        await asyncio.sleep(0.02)
        overall_active -= 1
        active_by_key[key] -= 1
        return {task.id: TaskResponse(id=task.id, success=True, answer=task.input)}

    exclusive_browser = TaskResourceClaim(key="browser", exclusive=True)
    results = await DeterministicTaskBatchExecutor(run_task=run_task).run(
        [
            _item(0, claims=(exclusive_browser,)),
            _item(1, claims=(exclusive_browser,)),
            _item(2, claims=(TaskResourceClaim(key="filesystem"),)),
        ],
        max_concurrency=3,
        failure_policy="collect_all",
    )

    assert all(result.status == "succeeded" for result in results)
    assert max_by_key["browser"] == 1
    assert max_overall == 2
    assert any(result.serialized_by_resource for result in results[:2])


@pytest.mark.asyncio
async def test_nonexclusive_claims_for_same_key_can_overlap() -> None:
    release = asyncio.Event()
    both_started = asyncio.Event()
    active = 0

    async def run_task(task: Task):
        nonlocal active
        active += 1
        if active == 2:
            both_started.set()
        await release.wait()
        active -= 1
        return {task.id: TaskResponse(id=task.id, success=True, answer=task.input)}

    shared = TaskResourceClaim(key="recording", exclusive=False)
    running = asyncio.create_task(
        DeterministicTaskBatchExecutor(run_task=run_task).run(
            [_item(0, claims=(shared,)), _item(1, claims=(shared,))],
            max_concurrency=2,
            failure_policy="collect_all",
        )
    )
    await asyncio.wait_for(both_started.wait(), timeout=1)
    release.set()

    results = await running

    assert [result.status for result in results] == ["succeeded", "succeeded"]
