# coding: utf-8
"""
Regression tests for failed one-shot cron job retention.
"""
import pytest
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock

import pytz

from aworld.core.scheduler.scheduler import CronScheduler
from aworld.core.scheduler.store import FileBasedCronStore
from aworld.core.scheduler.executor import CronExecutor
from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload, CronJobState
from aworld.core.task import TaskResponse


@pytest.mark.asyncio
async def test_failed_one_shot_job_is_retained_for_inspection():
    """One-shot jobs should remain in store on failure so /cron list can show last_error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=False, msg="Agent not found: Aworld")
        )

        scheduler = CronScheduler(store, executor)

        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="failing-one-shot",
            delete_after_run=True,
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="提醒用户喝水"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None,
            ),
        )

        await store.add_job(job)
        await scheduler._execute_claimed_job(job)

        persisted_job = await store.get_job(job.id)
        assert persisted_job is not None
        assert persisted_job.state.running is False
        assert persisted_job.state.last_status == "error"
        assert persisted_job.state.last_error == "Agent not found: Aworld"


@pytest.mark.asyncio
async def test_legacy_stop_job_disables_target_job():
    """Legacy stop reminder jobs should disable the referenced recurring job directly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=True, msg="should not execute")
        )
        scheduler = CronScheduler(store, executor)

        recurring_job = CronJob(
            name="运动提醒",
            enabled=True,
            schedule=CronSchedule(kind="every", every_seconds=180),
            payload=CronPayload(message="提醒我运动"),
            state=CronJobState(next_run_at=datetime.now(pytz.UTC).isoformat()),
        )
        await store.add_job(recurring_job)

        stop_job = CronJob(
            name="停止运动提醒",
            delete_after_run=True,
            schedule=CronSchedule(kind="at", at=datetime.now(pytz.UTC).isoformat()),
            payload=CronPayload(
                message=f"停止运动提醒任务（ID: {recurring_job.id}）",
                tool_names=["cron"],
            ),
            state=CronJobState(
                running=True,
                last_run_at=datetime.now(pytz.UTC).isoformat(),
                next_run_at=None,
            ),
        )
        await store.add_job(stop_job)

        await scheduler._execute_claimed_job(stop_job)

        persisted_target = await store.get_job(recurring_job.id)
        persisted_stop = await store.get_job(stop_job.id)

        assert persisted_target is not None
        assert persisted_target.enabled is False
        assert persisted_target.state.next_run_at is None
        assert persisted_stop is None
        executor.execute_with_retry.assert_not_called()
