# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Tests for FileBasedCronStore, especially claim_due_job atomicity.
"""
import asyncio
import threading
import time
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import pytz

from aworld.core.scheduler.store import FileBasedCronStore, CronStoreReadError
from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload, CronJobState


@pytest.fixture
def temp_store():
    """Create a temporary store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))
        yield store


def test_store_creates_dedicated_lock_file(tmp_path):
    """The store should create a dedicated cross-process lock file next to cron.json."""
    store_path = tmp_path / "cron.json"

    FileBasedCronStore(str(store_path))

    assert (tmp_path / "cron.json.lock").exists()


@pytest.mark.asyncio
async def test_list_jobs_raises_on_corrupted_store_file(tmp_path):
    """Corrupted JSON must fail loudly instead of being treated as an empty store."""
    store_path = tmp_path / "cron.json"
    store_path.write_text("{invalid-json", encoding="utf-8")

    store = FileBasedCronStore(str(store_path))

    with pytest.raises(CronStoreReadError):
        await store.list_jobs(enabled_only=False)


@pytest.mark.asyncio
async def test_add_job_does_not_overwrite_corrupted_store_file(tmp_path):
    """Write operations must not replace a corrupted store with an empty state."""
    corrupted_content = "{invalid-json"
    store_path = tmp_path / "cron.json"
    store_path.write_text(corrupted_content, encoding="utf-8")

    store = FileBasedCronStore(str(store_path))
    job = CronJob(
        name="job-after-corruption",
        schedule=CronSchedule(kind="every", every_seconds=60),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=None),
    )

    with pytest.raises(CronStoreReadError):
        await store.add_job(job)

    assert store_path.read_text(encoding="utf-8") == corrupted_content


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
async def test_add_job_concurrent_store_instances_do_not_lose_updates(tmp_path):
    """Concurrent writers from distinct store instances should preserve both jobs."""
    store_path = tmp_path / "cron.json"
    read_barrier = threading.Barrier(2)

    class SlowReadStore(FileBasedCronStore):
        def _read_data(self, *args, **kwargs):
            data = super()._read_data(*args, **kwargs)
            try:
                read_barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            time.sleep(0.05)
            return data

    def add_job(job_name: str):
        store = SlowReadStore(str(store_path))
        job = CronJob(
            name=job_name,
            schedule=CronSchedule(kind="every", every_seconds=60),
            payload=CronPayload(message=job_name),
            state=CronJobState(next_run_at=None),
        )
        asyncio.run(store.add_job(job))

    await asyncio.gather(
        asyncio.to_thread(add_job, "job-a"),
        asyncio.to_thread(add_job, "job-b"),
    )

    final_store = FileBasedCronStore(str(store_path))
    jobs = await final_store.list_jobs(enabled_only=False)

    assert sorted(job.name for job in jobs) == ["job-a", "job-b"]


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


@pytest.mark.asyncio
async def test_claim_due_job_with_timezone_offset(temp_store):
    """
    Test that claim_due_job correctly handles timezone offsets in ISO timestamps.

    Bug scenario: Job scheduled at UTC+8 09:00 (= UTC 01:00) should be claimable
    when current time is UTC 02:00. String comparison incorrectly treats
    "09:00:00+08:00" > "02:00:00+00:00" as True (not due yet).
    """
    # Job scheduled at UTC+8 09:00, which is UTC 01:00
    job_next_run_utc8 = "2026-04-09T09:00:00+08:00"

    # Current time is UTC 02:00 (1 hour after job's UTC time)
    current_time_utc = "2026-04-09T02:00:00+00:00"

    # Create a job with timezone-aware next_run_at
    job = CronJob(
        name="Timezone Test Job",
        schedule=CronSchedule(kind="cron", cron_expr="0 9 * * *"),  # Every day at 9 AM
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at=job_next_run_utc8),
    )

    await temp_store.add_job(job)

    # Attempt to claim with UTC current time
    # Should succeed because UTC+8 09:00 (01:00 UTC) < 02:00 UTC
    claimed = await temp_store.claim_due_job(job.id, current_time_utc)

    # EXPECTED: Job should be claimed (it's past due)
    # ACTUAL (with bug): Job not claimed (string comparison fails)
    assert claimed is not None, (
        f"Job should be claimable: {job_next_run_utc8} (UTC 01:00) is before "
        f"{current_time_utc} (UTC 02:00)"
    )
    assert claimed.state.running is True


@pytest.mark.asyncio
async def test_claim_due_job_various_timezones(temp_store):
    """Test claim_due_job with various timezone offsets."""
    test_cases = [
        # (job_next_run, current_time, should_claim, description)
        ("2026-04-09T23:00:00+09:00", "2026-04-09T15:00:00+00:00", True, "JST 23:00 = UTC 14:00 < UTC 15:00"),
        ("2026-04-09T08:00:00-05:00", "2026-04-09T14:00:00+00:00", True, "EST 08:00 = UTC 13:00 < UTC 14:00"),
        ("2026-04-09T05:00:00+05:30", "2026-04-09T00:30:00+00:00", True, "IST 05:00 = UTC 23:30 (prev day) < UTC 00:30"),
        ("2026-04-09T10:00:00+00:00", "2026-04-09T09:00:00+00:00", False, "UTC 10:00 > UTC 09:00 (not due)"),
    ]

    for idx, (next_run, current, should_claim, desc) in enumerate(test_cases):
        job = CronJob(
            name=f"TZ Test {idx}",
            schedule=CronSchedule(kind="cron", cron_expr="0 * * * *"),
            payload=CronPayload(message="test"),
            state=CronJobState(next_run_at=next_run),
        )

        await temp_store.add_job(job)

        claimed = await temp_store.claim_due_job(job.id, current)

        if should_claim:
            assert claimed is not None, f"Failed: {desc}"
            assert claimed.state.running is True
        else:
            assert claimed is None, f"Failed (should not claim): {desc}"


@pytest.mark.asyncio
async def test_store_coerces_legacy_string_max_runs_and_tool_fields(temp_store):
    """Legacy cron.json rows with string fields should deserialize to runtime-safe types."""
    job = CronJob(
        name="legacy-bounded-job",
        schedule=CronSchedule(kind="every", every_seconds=180),
        payload=CronPayload(
            message="提醒用户进行运动",
            agent_name="aworld",
            tool_names=["cron"],
            max_runs=3,
        ),
        state=CronJobState(next_run_at="2026-04-12T10:32:00+00:00"),
    )

    await temp_store.add_job(job)

    data = temp_store._read_data()
    data["jobs"][0]["payload"]["max_runs"] = "3"
    data["jobs"][0]["payload"]["tool_names"] = "cron"
    data["jobs"][0]["enabled"] = "true"
    data["jobs"][0]["delete_after_run"] = "false"
    temp_store._write_data(data)

    restored = await temp_store.get_job(job.id)

    assert restored is not None
    assert restored.payload.max_runs == 3
    assert restored.payload.tool_names == ["cron"]
    assert restored.enabled is True
    assert restored.delete_after_run is False


@pytest.mark.asyncio
async def test_store_splits_comma_delimited_tool_names(temp_store):
    """Comma-delimited legacy tool fields should be split into individual tool names."""
    job = CronJob(
        name="legacy-tool-list",
        schedule=CronSchedule(kind="every", every_seconds=180),
        payload=CronPayload(
            message="run task",
            agent_name="Aworld",
            tool_names=["bash"],
        ),
        state=CronJobState(next_run_at="2026-04-12T10:32:00+00:00"),
    )

    await temp_store.add_job(job)

    data = temp_store._read_data()
    data["jobs"][0]["payload"]["tool_names"] = "CAST_SEARCH,bash,SKILL"
    temp_store._write_data(data)

    restored = await temp_store.get_job(job.id)

    assert restored is not None
    assert restored.payload.tool_names == ["CAST_SEARCH", "bash", "SKILL"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
