from datetime import UTC, datetime, timedelta

from aworld.core.scheduler.types import CronJob, CronPayload, CronSchedule

from .demo_setup import DemoPaths


def build_one_time_reminder_job(paths: DemoPaths) -> CronJob:
    run_at = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

    return CronJob(
        name="Demo reminder: review cron outputs",
        delete_after_run=True,
        schedule=CronSchedule(kind="at", at=run_at, timezone="UTC"),
        payload=CronPayload(
            message=(
                f"One-time reminder: check demo output files under {paths.outputs_dir}."
            )
        ),
    )


def build_recurring_heartbeat_job(paths: DemoPaths) -> CronJob:
    heartbeat_file = paths.outputs_dir / "heartbeat.log"

    return CronJob(
        name="Demo heartbeat writer",
        schedule=CronSchedule(kind="every", every_seconds=60, timezone="UTC"),
        payload=CronPayload(
            message=(
                "Heartbeat task: append exactly one heartbeat line with a UTC "
                f"timestamp to {heartbeat_file}."
            ),
            tool_names=["bash"],
        ),
    )
