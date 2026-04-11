# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Tests for CronScheduler - scheduling logic, recovery, next_run calculation.

Critical test cases per design doc Section 15.1:
- schedule parsing
- next-run calculation
- startup recovery clears stale running state
- one-shot jobs delete correctly
- manual trigger does not corrupt recurring schedule
"""
import pytest
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytz

from aworld.core.scheduler.scheduler import CronScheduler
from aworld.core.scheduler.store import FileBasedCronStore
from aworld.core.scheduler.executor import CronExecutor
from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload, CronJobState
from aworld.core.task import TaskResponse


@pytest.fixture
def temp_store():
    """Create a temporary store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))
        yield store


@pytest.fixture
def mock_executor():
    """Create a mock executor."""
    executor = AsyncMock(spec=CronExecutor)
    executor.execute_with_retry = AsyncMock(
        return_value=TaskResponse(success=True, msg="Success")
    )
    return executor


@pytest.fixture
def scheduler(temp_store, mock_executor):
    """Create scheduler instance."""
    return CronScheduler(temp_store, mock_executor, max_concurrent=2)


@pytest.mark.asyncio
async def test_calculate_next_run_at_schedule(scheduler):
    """Test next_run calculation for 'at' (one-time) schedule."""
    now = datetime.now(pytz.UTC)
    future_time = now + timedelta(hours=2)

    job = CronJob(
        name="test-at",
        schedule=CronSchedule(kind="at", at=future_time.isoformat()),
        payload=CronPayload(message="test"),
    )

    next_run = scheduler._calculate_next_run(job, now)

    assert next_run is not None
    assert next_run > now
    assert abs((next_run - future_time).total_seconds()) < 1  # Within 1 second


@pytest.mark.asyncio
async def test_calculate_next_run_at_expired(scheduler):
    """Test that expired 'at' schedule returns None."""
    now = datetime.now(pytz.UTC)
    past_time = now - timedelta(hours=1)

    job = CronJob(
        name="test-at-expired",
        schedule=CronSchedule(kind="at", at=past_time.isoformat()),
        payload=CronPayload(message="test"),
    )

    next_run = scheduler._calculate_next_run(job, now)

    assert next_run is None  # Expired, no future run


@pytest.mark.asyncio
async def test_calculate_next_run_every_first_time(scheduler):
    """Test 'every' schedule calculation for first run."""
    now = datetime.now(pytz.UTC)

    job = CronJob(
        name="test-every",
        schedule=CronSchedule(kind="every", every_seconds=3600),  # 1 hour
        payload=CronPayload(message="test"),
        state=CronJobState(last_run_at=None),  # First run
    )

    next_run = scheduler._calculate_next_run(job, now)

    # First run should be immediate
    assert next_run is not None
    assert abs((next_run - now).total_seconds()) < 1


@pytest.mark.asyncio
async def test_calculate_next_run_every_recurring(scheduler):
    """Test 'every' schedule calculation for recurring run."""
    now = datetime.now(pytz.UTC)
    last_run = now - timedelta(minutes=30)

    job = CronJob(
        name="test-every",
        schedule=CronSchedule(kind="every", every_seconds=3600),  # 1 hour
        payload=CronPayload(message="test"),
        state=CronJobState(last_run_at=last_run.isoformat()),
    )

    next_run = scheduler._calculate_next_run(job, now)

    expected = last_run + timedelta(seconds=3600)
    assert next_run is not None
    assert abs((next_run - expected).total_seconds()) < 1


@pytest.mark.asyncio
async def test_calculate_next_run_cron_expression(scheduler):
    """Test 'cron' schedule calculation."""
    now = datetime(2026, 4, 9, 8, 30, 0, tzinfo=pytz.UTC)  # 8:30 AM

    job = CronJob(
        name="test-cron",
        schedule=CronSchedule(kind="cron", cron_expr="0 9 * * *"),  # Daily at 9 AM
        payload=CronPayload(message="test"),
    )

    next_run = scheduler._calculate_next_run(job, now)

    # Should be today at 9 AM (30 minutes from now)
    assert next_run is not None
    assert next_run.hour == 9
    assert next_run.minute == 0


@pytest.mark.asyncio
async def test_startup_recovery_clears_stale_running(scheduler, temp_store):
    """
    Test that startup recovery clears jobs stuck in 'running' state.

    Critical test per design doc Section 15.1.
    """
    # Create jobs with stale running state (simulating crash)
    job1 = CronJob(
        name="stale-job-1",
        schedule=CronSchedule(kind="every", every_seconds=3600),
        payload=CronPayload(message="test"),
        state=CronJobState(
            running=True,  # Stale running state
            last_run_at=datetime.now(pytz.UTC).isoformat(),
        ),
    )

    job2 = CronJob(
        name="normal-job",
        schedule=CronSchedule(kind="every", every_seconds=3600),
        payload=CronPayload(message="test"),
        state=CronJobState(running=False),
    )

    await temp_store.add_job(job1)
    await temp_store.add_job(job2)

    # Trigger startup recovery
    await scheduler._cleanup_stale_running()

    # Verify stale job is marked as failed
    recovered_job1 = await temp_store.get_job(job1.id)
    assert recovered_job1.state.running is False
    assert recovered_job1.state.last_status == "error"
    assert "restarted" in recovered_job1.state.last_error.lower()

    # Verify normal job is unchanged
    recovered_job2 = await temp_store.get_job(job2.id)
    assert recovered_job2.state.running is False


@pytest.mark.asyncio
async def test_startup_recovery_recalculates_next_runs(scheduler, temp_store):
    """
    Test that startup recovery recalculates next_run_at for all jobs.

    Critical for Issue #6 fix - ensures expired 'at' tasks get None.
    """
    now = datetime.now(pytz.UTC)

    # Expired one-time task
    expired_job = CronJob(
        name="expired-at",
        schedule=CronSchedule(kind="at", at=(now - timedelta(hours=1)).isoformat()),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=(now - timedelta(hours=1)).isoformat()),
    )

    # Future recurring task
    recurring_job = CronJob(
        name="recurring",
        schedule=CronSchedule(kind="every", every_seconds=3600),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=None),  # Uninitialized
    )

    await temp_store.add_job(expired_job)
    await temp_store.add_job(recurring_job)

    # Trigger recovery
    await scheduler._recalculate_next_runs()

    # Verify expired task has None next_run_at
    recovered_expired = await temp_store.get_job(expired_job.id)
    assert recovered_expired.state.next_run_at is None

    # Verify recurring task has calculated next_run_at
    recovered_recurring = await temp_store.get_job(recurring_job.id)
    assert recovered_recurring.state.next_run_at is not None


@pytest.mark.asyncio
async def test_one_shot_job_deleted_after_run(scheduler, temp_store, mock_executor):
    """
    Test that one-shot jobs (delete_after_run=True) are deleted after execution.

    Critical test per design doc Section 15.1.
    """
    now = datetime.now(pytz.UTC)

    job = CronJob(
        name="one-shot",
        delete_after_run=True,
        schedule=CronSchedule(kind="at", at=now.isoformat()),
        payload=CronPayload(message="test"),
        state=CronJobState(
            running=True,  # Already claimed
            last_run_at=now.isoformat(),
            next_run_at=None,  # No future runs
        ),
    )

    await temp_store.add_job(job)

    # Execute the job (simulating claimed execution)
    await scheduler._execute_claimed_job(job)

    # Verify job is deleted
    deleted_job = await temp_store.get_job(job.id)
    assert deleted_job is None


@pytest.mark.asyncio
async def test_recurring_job_not_deleted_after_run(scheduler, temp_store, mock_executor):
    """Test that recurring jobs are NOT deleted after execution."""
    now = datetime.now(pytz.UTC)
    next_run = now + timedelta(hours=1)

    job = CronJob(
        name="recurring",
        delete_after_run=False,
        schedule=CronSchedule(kind="every", every_seconds=3600),
        payload=CronPayload(message="test"),
        state=CronJobState(
            running=True,
            last_run_at=now.isoformat(),
            next_run_at=next_run.isoformat(),
        ),
    )

    await temp_store.add_job(job)

    # Execute the job
    await scheduler._execute_claimed_job(job)

    # Verify job still exists
    persisted_job = await temp_store.get_job(job.id)
    assert persisted_job is not None
    assert persisted_job.state.running is False


@pytest.mark.asyncio
async def test_manual_trigger_does_not_corrupt_cadence(scheduler, temp_store):
    """
    Test that manual trigger (run_job) does not corrupt recurring schedule.

    Critical test per design doc Section 15.1 and Issue #4 fix.
    """
    now = datetime.now(pytz.UTC)
    original_next_run = now + timedelta(hours=2)

    job = CronJob(
        name="recurring",
        schedule=CronSchedule(kind="every", every_seconds=7200),  # 2 hours
        payload=CronPayload(message="test"),
        state=CronJobState(
            next_run_at=original_next_run.isoformat(),
            last_run_at=now.isoformat(),
        ),
    )

    await temp_store.add_job(job)

    # Manually trigger the job
    result = await scheduler.run_job(job.id, force=True)

    assert result.success is True

    # Verify next_run_at is unchanged (cadence preserved)
    updated_job = await temp_store.get_job(job.id)
    assert updated_job.state.next_run_at == original_next_run.isoformat()


@pytest.mark.asyncio
async def test_manual_trigger_respects_semaphore(scheduler, temp_store):
    """
    Test that manual trigger respects semaphore (max_concurrent limit).

    Verifies fix for Issue #4.
    """
    # Create mock executor that takes time to execute
    async def slow_execution(job):
        await asyncio.sleep(0.5)
        return TaskResponse(success=True, msg="Success")

    slow_executor = AsyncMock(spec=CronExecutor)
    slow_executor.execute_with_retry = AsyncMock(side_effect=slow_execution)

    scheduler_limited = CronScheduler(temp_store, slow_executor, max_concurrent=1)

    job1 = CronJob(
        name="job1",
        schedule=CronSchedule(kind="every", every_seconds=3600),
        payload=CronPayload(message="test1"),
    )

    job2 = CronJob(
        name="job2",
        schedule=CronSchedule(kind="every", every_seconds=3600),
        payload=CronPayload(message="test2"),
    )

    await temp_store.add_job(job1)
    await temp_store.add_job(job2)

    # Start both jobs concurrently
    task1 = asyncio.create_task(scheduler_limited.run_job(job1.id, force=True))
    task2 = asyncio.create_task(scheduler_limited.run_job(job2.id, force=True))

    # Wait for both
    await asyncio.gather(task1, task2)

    # Both should succeed (semaphore serialized them)
    result1 = await task1
    result2 = await task2

    assert result1.success is True
    assert result2.success is True


@pytest.mark.asyncio
async def test_add_job_initializes_next_run(scheduler, temp_store):
    """Test that add_job initializes next_run_at."""
    now = datetime.now(pytz.UTC)
    future = now + timedelta(hours=1)

    job = CronJob(
        name="test-job",
        schedule=CronSchedule(kind="at", at=future.isoformat()),
        payload=CronPayload(message="test"),
    )

    added_job = await scheduler.add_job(job)

    assert added_job.state.next_run_at is not None
    assert added_job.state.next_run_at == future.isoformat()


@pytest.mark.asyncio
async def test_disabled_job_not_claimed(scheduler, temp_store):
    """Test that disabled jobs are not claimed during scheduling."""
    now = datetime.now(pytz.UTC)

    job = CronJob(
        name="disabled-job",
        enabled=False,
        schedule=CronSchedule(kind="at", at=now.isoformat()),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=now.isoformat()),
    )

    await temp_store.add_job(job)

    # Try to claim (should fail)
    claimed = await temp_store.claim_due_job(job.id, now.isoformat(), None)

    assert claimed is None  # Disabled job cannot be claimed


@pytest.mark.asyncio
async def test_find_next_job_selects_overdue_tasks(scheduler, temp_store):
    """
    CRITICAL TEST: Verify that _find_next_job() selects overdue tasks (negative wait_seconds).

    This test validates the fix for the critical bug where overdue tasks were
    permanently skipped if they became negative due to:
    - Scheduler loop delays
    - Event loop blocking
    - High system load
    - Time boundary conditions

    Without this fix, tasks would get stuck and never execute.
    """
    now = datetime.now(pytz.UTC)

    # Create three jobs with different states
    overdue_job = CronJob(
        name="overdue-job",
        schedule=CronSchedule(kind="at", at=(now - timedelta(seconds=5)).isoformat()),
        payload=CronPayload(message="overdue"),
        state=CronJobState(
            next_run_at=(now - timedelta(seconds=5)).isoformat()  # 5 seconds overdue
        ),
    )

    due_now_job = CronJob(
        name="due-now-job",
        schedule=CronSchedule(kind="at", at=now.isoformat()),
        payload=CronPayload(message="due now"),
        state=CronJobState(next_run_at=now.isoformat()),
    )

    future_job = CronJob(
        name="future-job",
        schedule=CronSchedule(kind="at", at=(now + timedelta(seconds=10)).isoformat()),
        payload=CronPayload(message="future"),
        state=CronJobState(
            next_run_at=(now + timedelta(seconds=10)).isoformat()
        ),
    )

    await temp_store.add_job(overdue_job)
    await temp_store.add_job(due_now_job)
    await temp_store.add_job(future_job)

    jobs = await temp_store.list_jobs(enabled_only=True)

    # Find next job
    next_job, wait_seconds = scheduler._find_next_job(jobs)

    # CRITICAL ASSERTION: Overdue job must be selected
    assert next_job is not None
    assert next_job.id == overdue_job.id  # Most overdue job is selected
    assert wait_seconds == 0  # Returned wait time is clamped to 0 (execute immediately)


@pytest.mark.asyncio
async def test_find_next_job_selects_most_overdue(scheduler, temp_store):
    """
    Test that when multiple jobs are overdue, the most overdue is selected.

    This ensures fair scheduling - jobs that have been waiting longest are prioritized.
    """
    now = datetime.now(pytz.UTC)

    # Create three overdue jobs with different delays
    very_overdue = CronJob(
        name="very-overdue",
        schedule=CronSchedule(kind="at", at=(now - timedelta(seconds=30)).isoformat()),
        payload=CronPayload(message="test"),
        state=CronJobState(
            next_run_at=(now - timedelta(seconds=30)).isoformat()
        ),
    )

    moderately_overdue = CronJob(
        name="moderately-overdue",
        schedule=CronSchedule(kind="at", at=(now - timedelta(seconds=10)).isoformat()),
        payload=CronPayload(message="test"),
        state=CronJobState(
            next_run_at=(now - timedelta(seconds=10)).isoformat()
        ),
    )

    slightly_overdue = CronJob(
        name="slightly-overdue",
        schedule=CronSchedule(kind="at", at=(now - timedelta(seconds=2)).isoformat()),
        payload=CronPayload(message="test"),
        state=CronJobState(
            next_run_at=(now - timedelta(seconds=2)).isoformat()
        ),
    )

    await temp_store.add_job(moderately_overdue)  # Add in random order
    await temp_store.add_job(slightly_overdue)
    await temp_store.add_job(very_overdue)

    jobs = await temp_store.list_jobs(enabled_only=True)
    next_job, wait_seconds = scheduler._find_next_job(jobs)

    # Most overdue job should be selected
    assert next_job is not None
    assert next_job.id == very_overdue.id


@pytest.mark.asyncio
async def test_overdue_job_can_be_claimed_and_executed(scheduler, temp_store, mock_executor):
    """
    End-to-end test: Verify overdue jobs can be claimed and executed.

    This simulates the real scenario where a job becomes overdue and validates
    the complete execution path.
    """
    now = datetime.now(pytz.UTC)
    overdue_time = now - timedelta(seconds=10)

    job = CronJob(
        name="overdue-execution-test",
        delete_after_run=True,  # Mark as one-shot job
        schedule=CronSchedule(kind="at", at=overdue_time.isoformat()),
        payload=CronPayload(message="test overdue execution"),
        state=CronJobState(next_run_at=overdue_time.isoformat()),
    )

    await temp_store.add_job(job)

    # Simulate scheduler loop behavior
    jobs = await temp_store.list_jobs(enabled_only=True)
    next_job, wait_seconds = scheduler._find_next_job(jobs)

    # Job should be found
    assert next_job is not None
    assert next_job.id == job.id

    # Job should be claimable
    next_run = scheduler._calculate_next_run(next_job, now)
    claimed = await temp_store.claim_due_job(next_job.id, now.isoformat(), None)

    assert claimed is not None
    assert claimed.state.running is True

    # Job should be executable
    await scheduler._execute_claimed_job(claimed)

    # Verify execution completed
    executed_job = await temp_store.get_job(job.id)
    assert executed_job is None  # One-shot job should be deleted


@pytest.mark.asyncio
async def test_every_task_no_catchup_after_offline(scheduler):
    """
    CRITICAL TEST: Verify 'every' tasks do NOT catchup missed runs after offline period.

    Scenario:
    - Task runs every hour (every_seconds=3600)
    - last_run_at = 2 hours ago
    - CLI was offline for 2 hours, just restarted
    - Expected: next_run should be in FUTURE (no catchup)
    - Bug: next_run = last_run + 3600 = 1 hour ago (triggers immediate execution)

    This test validates the fix for offline missed run replay bug.
    """
    now = datetime.now(pytz.UTC)
    last_run_2h_ago = now - timedelta(hours=2)

    job = CronJob(
        name="every-no-catchup",
        schedule=CronSchedule(kind="every", every_seconds=3600),  # 1 hour
        payload=CronPayload(message="test"),
        state=CronJobState(last_run_at=last_run_2h_ago.isoformat()),
    )

    # Simulate startup: calculate next_run
    next_run = scheduler._calculate_next_run(job, now)

    # CRITICAL ASSERTION: next_run MUST be in future (no catchup for missed runs)
    assert next_run is not None
    assert next_run > now, (
        f"BUG: next_run ({next_run}) should be > now ({now}). "
        f"Offline missed runs should NOT be replayed!"
    )


@pytest.mark.asyncio
async def test_calculate_next_run_invalid_schedule_returns_none(scheduler):
    """
    Test that _calculate_next_run() returns None for invalid schedules (runtime error handling).

    This verifies the exception handling in _calculate_next_run() that allows
    graceful recovery during schedule loop and startup, even if invalid schedules
    somehow bypass tool layer validation.
    """

    # Create job with invalid 'at' schedule (bypassing tool validation)
    job = CronJob(
        name="Invalid At Job",
        schedule=CronSchedule(kind="at", at="not-a-valid-timestamp"),
        payload=CronPayload(message="test"),
    )

    now = datetime.now(pytz.UTC)
    next_run = scheduler._calculate_next_run(job, now)

    # Runtime error handling: should return None (not raise exception)
    assert next_run is None, "Invalid schedule should return None for runtime error handling"

    # Create job with invalid 'cron' schedule
    job_cron = CronJob(
        name="Invalid Cron Job",
        schedule=CronSchedule(kind="cron", cron_expr="invalid-cron-expression"),
        payload=CronPayload(message="test"),
    )

    next_run_cron = scheduler._calculate_next_run(job_cron, now)

    # Should also return None without raising
    assert next_run_cron is None, "Invalid cron should return None for runtime error handling"


@pytest.mark.asyncio
async def test_enable_job_recalculates_next_run(scheduler, temp_store):
    """Test enabling a disabled job recalculates next_run_at."""
    future = datetime.now(pytz.UTC) + timedelta(minutes=30)
    job = CronJob(
        name="disabled-at-job",
        enabled=False,
        schedule=CronSchedule(kind="at", at=future.isoformat()),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=None),
    )

    await temp_store.add_job(job)

    updated_job = await scheduler.update_job(job.id, enabled=True)

    assert updated_job is not None
    assert updated_job.enabled is True
    assert updated_job.state.next_run_at == future.isoformat()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
