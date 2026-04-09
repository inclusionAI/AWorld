# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Tests for FileBasedCronStore, especially claim_due_job atomicity.
"""
import asyncio
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import pytz

from aworld.core.scheduler.store import FileBasedCronStore
from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload, CronJobState


@pytest.fixture
def temp_store():
    """Create a temporary store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))
        yield store


@pytest.mark.asyncio
async def test_claim_due_job_single_fire(temp_store):
    """
    Test that claim_due_job can only be claimed once.

    This is the critical test for preventing duplicate execution.
    """
    # Create a job that is due now
    now = datetime.now(pytz.UTC)
    job = CronJob(
        name="test-job",
        schedule=CronSchedule(kind="every", every_seconds=3600),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=now.isoformat()),
    )

    await temp_store.add_job(job)

    # First claim should succeed
    now_iso = now.isoformat()
    claimed1 = await temp_store.claim_due_job(job.id, now_iso)

    assert claimed1 is not None
    assert claimed1.state.running is True
    assert claimed1.state.last_run_at == now_iso

    # Second claim should fail (job is running)
    claimed2 = await temp_store.claim_due_job(job.id, now_iso)

    assert claimed2 is None  # Cannot claim twice


@pytest.mark.asyncio
async def test_claim_due_job_not_due_yet(temp_store):
    """Test that claim_due_job fails for jobs not yet due."""
    now = datetime.now(pytz.UTC)
    future = now + timedelta(hours=1)

    job = CronJob(
        name="future-job",
        schedule=CronSchedule(kind="at", at=future.isoformat()),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=future.isoformat()),
    )

    await temp_store.add_job(job)

    # Try to claim before it's due
    claimed = await temp_store.claim_due_job(job.id, now.isoformat())

    assert claimed is None  # Should fail - not due yet


@pytest.mark.asyncio
async def test_claim_due_job_disabled(temp_store):
    """Test that claim_due_job fails for disabled jobs."""
    now = datetime.now(pytz.UTC)

    job = CronJob(
        name="disabled-job",
        enabled=False,
        schedule=CronSchedule(kind="every", every_seconds=60),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=now.isoformat()),
    )

    await temp_store.add_job(job)

    # Try to claim disabled job
    claimed = await temp_store.claim_due_job(job.id, now.isoformat())

    assert claimed is None  # Should fail - disabled


@pytest.mark.asyncio
async def test_claim_due_job_concurrent_attempts(temp_store):
    """
    Test concurrent claim attempts (simulating multiple scheduler ticks).

    Only one claim should succeed even if multiple tasks try simultaneously.
    """
    now = datetime.now(pytz.UTC)
    job = CronJob(
        name="concurrent-test",
        schedule=CronSchedule(kind="every", every_seconds=60),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=now.isoformat()),
    )

    await temp_store.add_job(job)

    # Simulate 5 concurrent claim attempts
    now_iso = now.isoformat()
    tasks = [
        temp_store.claim_due_job(job.id, now_iso)
        for _ in range(5)
    ]

    results = await asyncio.gather(*tasks)

    # Exactly one should succeed
    successful_claims = [r for r in results if r is not None]
    assert len(successful_claims) == 1

    # All others should be None
    failed_claims = [r for r in results if r is None]
    assert len(failed_claims) == 4


@pytest.mark.asyncio
async def test_claim_due_job_no_next_run(temp_store):
    """Test that claim fails if next_run_at is None."""
    job = CronJob(
        name="no-next-run",
        schedule=CronSchedule(kind="at", at="2026-04-09T12:00:00+00:00"),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=None),  # Already executed
    )

    await temp_store.add_job(job)

    now = datetime.now(pytz.UTC)
    claimed = await temp_store.claim_due_job(job.id, now.isoformat())

    assert claimed is None  # Should fail - no next run scheduled


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
