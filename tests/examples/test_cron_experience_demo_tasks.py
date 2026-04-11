from datetime import UTC, datetime, timedelta
from pathlib import Path

from aworld.core.scheduler.types import CronJob
from examples.cron_experience_demo.demo_setup import DemoPaths
from examples.cron_experience_demo.demo_tasks import (
    build_one_time_reminder_job,
    build_recurring_heartbeat_job,
)


def test_build_one_time_reminder_job_uses_at_schedule_and_auto_delete(
    tmp_path: Path,
) -> None:
    paths = DemoPaths.from_root(tmp_path / "cron-demo")

    job = build_one_time_reminder_job(paths)

    assert isinstance(job, CronJob)
    assert "reminder" in job.name.lower()
    assert job.delete_after_run is True
    assert job.schedule.kind == "at"
    assert job.schedule.at is not None
    assert job.schedule.timezone == "UTC"
    run_at = datetime.fromisoformat(job.schedule.at)
    assert run_at.tzinfo is not None
    assert run_at.utcoffset() == timedelta(0)
    assert run_at > datetime.now(UTC)


def test_build_recurring_heartbeat_job_sets_recurring_schedule_and_output_message(
    tmp_path: Path,
) -> None:
    paths = DemoPaths.from_root(tmp_path / "cron-demo")
    heartbeat_file = paths.outputs_dir / "heartbeat.log"

    job = build_recurring_heartbeat_job(paths)

    assert isinstance(job, CronJob)
    assert "heartbeat" in job.name.lower()
    assert job.schedule.kind == "every"
    assert job.schedule.every_seconds == 60
    assert job.schedule.timezone == "UTC"
    assert "bash" in job.payload.tool_names
    assert str(heartbeat_file) in job.payload.message
    assert "append exactly one heartbeat line" in job.payload.message.lower()
