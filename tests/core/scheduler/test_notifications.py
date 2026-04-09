# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Tests for cron notification system.

Tests notification center, scheduler integration, and notification publishing.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime
import pytz

from aworld_cli.runtime.cron_notifications import CronNotification, CronNotificationCenter
from aworld.core.scheduler.scheduler import CronScheduler
from aworld.core.scheduler.store import FileBasedCronStore
from aworld.core.scheduler.executor import CronExecutor
from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload, CronJobState
from aworld.core.task import TaskResponse


@pytest.mark.asyncio
async def test_notification_center_publish_and_drain():
    """Test FIFO queue behavior of notification center."""
    center = CronNotificationCenter(max_size=10)

    # Publish 3 notifications
    await center.publish({
        'job_id': 'job1',
        'job_name': 'Job 1',
        'status': 'ok',
        'summary': 'Job 1 completed',
        'created_at': datetime.now(pytz.UTC).isoformat()
    })

    await center.publish({
        'job_id': 'job2',
        'job_name': 'Job 2',
        'status': 'error',
        'summary': 'Job 2 failed',
        'created_at': datetime.now(pytz.UTC).isoformat()
    })

    await center.publish({
        'job_id': 'job3',
        'job_name': 'Job 3',
        'status': 'timeout',
        'summary': 'Job 3 timed out',
        'created_at': datetime.now(pytz.UTC).isoformat()
    })

    # Drain and verify FIFO order
    notifications = await center.drain()

    assert len(notifications) == 3
    assert notifications[0].job_id == 'job1'
    assert notifications[1].job_id == 'job2'
    assert notifications[2].job_id == 'job3'

    # Verify queue is empty after drain
    notifications2 = await center.drain()
    assert len(notifications2) == 0


@pytest.mark.asyncio
async def test_notification_center_bounded_size():
    """Test that notification center enforces max size."""
    center = CronNotificationCenter(max_size=3)

    # Publish 5 notifications (exceeds max)
    for i in range(5):
        await center.publish({
            'job_id': f'job{i}',
            'job_name': f'Job {i}',
            'status': 'ok',
            'summary': f'Job {i} completed',
            'created_at': datetime.now(pytz.UTC).isoformat()
        })

    # Drain and verify only last 3 are kept (oldest dropped)
    notifications = await center.drain()

    # Due to FIFO dropping, we should have job2, job3, job4
    assert len(notifications) == 3
    # First two (job0, job1) should have been dropped
    assert notifications[0].job_id == 'job2'
    assert notifications[1].job_id == 'job3'
    assert notifications[2].job_id == 'job4'


@pytest.mark.asyncio
async def test_scheduler_publishes_on_success():
    """Test that scheduler publishes success notification."""
    import tempfile
    from pathlib import Path

    # Create temp store
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        # Mock executor
        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=True, msg="Success")
        )

        # Mock notification sink
        notification_sink = AsyncMock()

        # Create scheduler with notification sink
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        # Create and add a job
        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="test-job",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="test"),
            state=CronJobState(
                running=True,  # Simulate claimed job
                last_run_at=now.isoformat(),
                next_run_at=None
            )
        )

        await store.add_job(job)

        # Execute the claimed job
        await scheduler._execute_claimed_job(job)

        # Verify notification was published
        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args['job_id'] == job.id
        assert call_args['job_name'] == 'test-job'
        assert call_args['status'] == 'ok'
        assert 'completed' in call_args['summary']


@pytest.mark.asyncio
async def test_scheduler_publishes_on_error():
    """Test that scheduler publishes error notification with error message."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        # Mock executor that fails
        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=False, msg="Task failed with error")
        )

        # Mock notification sink
        notification_sink = AsyncMock()

        # Create scheduler with notification sink
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        # Create and add a job
        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="failing-job",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="test"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None
            )
        )

        await store.add_job(job)

        # Execute the claimed job
        await scheduler._execute_claimed_job(job)

        # Verify error notification was published
        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args['job_id'] == job.id
        assert call_args['status'] == 'error'
        assert 'failed' in call_args['summary']
        # Per Section 8.4, summary uses fixed template without error detail
        assert call_args['summary'] == 'Cron task "failing-job" failed'


@pytest.mark.asyncio
async def test_scheduler_publishes_on_timeout():
    """Test that scheduler publishes timeout notification."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        # Mock executor that times out
        async def timeout_executor(job):
            await asyncio.sleep(10)  # Will be cancelled by timeout
            return TaskResponse(success=True, msg="Should not reach here")

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(side_effect=timeout_executor)

        # Mock notification sink
        notification_sink = AsyncMock()

        # Create scheduler with notification sink
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        # Create and add a job with short timeout
        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="timeout-job",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="test", timeout_seconds=1),  # 1 second timeout
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None
            )
        )

        await store.add_job(job)

        # Execute the claimed job (will timeout)
        await scheduler._execute_claimed_job(job)

        # Verify timeout notification was published
        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args['job_id'] == job.id
        assert call_args['status'] == 'timeout'
        assert 'timed out' in call_args['summary']


@pytest.mark.asyncio
async def test_scheduler_no_publish_without_sink():
    """Test that scheduler handles absence of notification sink gracefully."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        # Mock executor
        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=True, msg="Success")
        )

        # Create scheduler WITHOUT notification sink
        scheduler = CronScheduler(store, executor, notification_sink=None)

        # Create and add a job
        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="test-job",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="test"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None
            )
        )

        await store.add_job(job)

        # Execute the claimed job (should not crash without sink)
        try:
            await scheduler._execute_claimed_job(job)
            # Should complete successfully without notification
            assert True
        except Exception as e:
            pytest.fail(f"Scheduler should handle missing sink gracefully, but raised: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
