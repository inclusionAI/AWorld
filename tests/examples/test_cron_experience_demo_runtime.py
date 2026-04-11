from pathlib import Path

import pytest

from aworld.core.scheduler import CronExecutor, CronScheduler, FileBasedCronStore
from aworld_cli.runtime.cron_notifications import CronNotificationCenter
from examples.cron_experience_demo.demo_runtime import DemoRuntime
from examples.cron_experience_demo.demo_setup import DemoPaths, ensure_demo_paths
from examples.cron_experience_demo.demo_tasks import build_one_time_reminder_job


@pytest.mark.asyncio
async def test_demo_runtime_add_list_and_cleanup_jobs(tmp_path: Path) -> None:
    paths = DemoPaths.from_root(tmp_path / "cron-demo")
    ensure_demo_paths(paths)
    runtime = DemoRuntime(
        paths=paths,
        scheduler=CronScheduler(
            store=FileBasedCronStore(str(paths.cron_store)),
            executor=CronExecutor(),
        ),
        notification_center=CronNotificationCenter(),
    )

    first_job = await runtime.add_job(build_one_time_reminder_job(paths))
    second_job = await runtime.add_job(build_one_time_reminder_job(paths))

    jobs = await runtime.list_jobs()
    assert {job.id for job in jobs} >= {first_job.id, second_job.id}

    await runtime.cleanup_jobs((first_job.id, second_job.id))

    jobs_after_cleanup = await runtime.list_jobs()
    remaining_ids = {job.id for job in jobs_after_cleanup}
    assert first_job.id not in remaining_ids
    assert second_job.id not in remaining_ids


@pytest.mark.asyncio
async def test_demo_runtime_drain_notifications(tmp_path: Path) -> None:
    paths = DemoPaths.from_root(tmp_path / "cron-demo")
    ensure_demo_paths(paths)
    runtime = DemoRuntime(
        paths=paths,
        scheduler=CronScheduler(
            store=FileBasedCronStore(str(paths.cron_store)),
            executor=CronExecutor(),
        ),
        notification_center=CronNotificationCenter(),
    )

    await runtime.notification_center.publish(
        {
            "job_id": "job-123",
            "job_name": "demo-job",
            "status": "ok",
            "summary": "done",
        }
    )

    notifications = await runtime.drain_notifications()
    assert len(notifications) == 1
    assert notifications[0].job_id == "job-123"
    assert notifications[0].job_name == "demo-job"

    assert await runtime.drain_notifications() == []
