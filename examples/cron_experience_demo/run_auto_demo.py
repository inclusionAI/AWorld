import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re

from aworld.core.scheduler import CronExecutor, CronScheduler, FileBasedCronStore
from aworld.core.task import TaskResponse

from aworld_cli.runtime.cron_notifications import CronNotificationCenter

from .demo_runtime import DemoRuntime
from .demo_setup import DEMO_ROOT_MARKER, DemoPaths, ensure_demo_paths
from .demo_tasks import build_one_time_reminder_job, build_recurring_heartbeat_job

AUTO_DEMO_REMINDER_DELAY_SECONDS = 2
AUTO_DEMO_HEARTBEAT_SECONDS = 30
AUTO_DEMO_STEPS = 10
AUTO_DEMO_STEP_SLEEP_SECONDS = 1
DEMO_RUNTIME_DIRNAME = ".demo_runtime"


def default_demo_root() -> Path:
    return Path(__file__).resolve().parent / DEMO_RUNTIME_DIRNAME


def _preview_output(path: Path) -> str:
    if not path.exists():
        return "missing"

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return "empty"
    return lines[-1]


def _extract_path(pattern: str, message: str) -> Path | None:
    match = re.search(pattern, message)
    if not match:
        return None
    return Path(match.group("path"))


class DemoCronExecutor(CronExecutor):
    async def execute(self, job) -> TaskResponse:
        if "heartbeat" in job.name.lower():
            heartbeat_file = _extract_path(r"to (?P<path>/\S+?\.log)\.", job.payload.message)
            if heartbeat_file is None:
                return TaskResponse(success=False, msg="Heartbeat path missing from payload")

            heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
            line = f"{datetime.now(UTC).isoformat()} heartbeat from auto demo\n"
            with heartbeat_file.open("a", encoding="utf-8") as handle:
                handle.write(line)
            return TaskResponse(success=True, msg=f"Wrote heartbeat to {heartbeat_file}")

        if "reminder" in job.name.lower():
            outputs_dir = _extract_path(r"under (?P<path>/\S+)\.", job.payload.message)
            if outputs_dir is None:
                return TaskResponse(success=False, msg="Reminder output path missing from payload")

            outputs_dir.mkdir(parents=True, exist_ok=True)
            reminder_file = outputs_dir / "reminder.txt"
            reminder_file.write_text(
                f"{datetime.now(UTC).isoformat()} reminder fired\n",
                encoding="utf-8",
            )
            return TaskResponse(success=True, msg=f"Wrote reminder marker to {reminder_file}")

        return TaskResponse(success=False, msg=f"Unsupported demo job: {job.name}")

    async def execute_with_retry(self, job, max_retries: int = 3) -> TaskResponse:
        return await self.execute(job)


def _build_auto_demo_jobs(paths: DemoPaths):
    reminder_job = build_one_time_reminder_job(paths)
    reminder_job.schedule.at = (
        datetime.now(UTC) + timedelta(seconds=AUTO_DEMO_REMINDER_DELAY_SECONDS)
    ).isoformat()

    heartbeat_job = build_recurring_heartbeat_job(paths)
    heartbeat_job.schedule.every_seconds = AUTO_DEMO_HEARTBEAT_SECONDS
    return reminder_job, heartbeat_job


def _prepare_runtime_root(paths: DemoPaths) -> None:
    marker_path = paths.aworld_dir / DEMO_ROOT_MARKER

    if not paths.root.exists():
        ensure_demo_paths(paths)
        return

    if marker_path.is_file():
        ensure_demo_paths(paths, reset=True)
        return

    if any(paths.root.iterdir()):
        raise ValueError(
            "build_demo_runtime requires an empty directory or an initialized demo root"
        )

    ensure_demo_paths(paths)


async def build_demo_runtime(root: Path, executor: CronExecutor | None = None) -> DemoRuntime:
    paths = DemoPaths.from_root(root)
    _prepare_runtime_root(paths)

    notification_center = CronNotificationCenter()
    scheduler = CronScheduler(
        store=FileBasedCronStore(str(paths.cron_store)),
        executor=executor or CronExecutor(),
        notification_sink=notification_center.publish,
    )
    return DemoRuntime(
        paths=paths,
        scheduler=scheduler,
        notification_center=notification_center,
    )


async def main() -> None:
    runtime = await build_demo_runtime(default_demo_root(), executor=DemoCronExecutor())
    reminder_job, heartbeat_job = _build_auto_demo_jobs(runtime.paths)
    heartbeat_file = runtime.paths.outputs_dir / "heartbeat.log"
    reminder_file = runtime.paths.outputs_dir / "reminder.txt"
    added_job_ids: list[str] = []

    print("Cron experience demo started.")
    print(f"Demo root: {runtime.paths.root}")
    print(f"Cron store: {runtime.paths.cron_store}")
    print(f"Outputs dir: {runtime.paths.outputs_dir}")

    await runtime.scheduler.start()
    try:
        for job in (reminder_job, heartbeat_job):
            added_job = await runtime.add_job(job)
            added_job_ids.append(added_job.id)

        for step in range(1, AUTO_DEMO_STEPS + 1):
            jobs = await runtime.list_jobs()
            print(f"[step {step}] jobs={len(jobs)}")
            for job in jobs:
                print(
                    "  "
                    f"{job.name} next_run={job.state.next_run_at} "
                    f"last_status={job.state.last_status}"
                )

            notifications = await runtime.drain_notifications()
            if notifications:
                for notification in notifications:
                    print(
                        "[notification] "
                        f"{notification.job_name} status={notification.status} "
                        f"summary={notification.summary}"
                    )
                    if notification.detail:
                        print(f"  detail={notification.detail}")
            else:
                print("[notification] none")

            print(f"[heartbeat] {heartbeat_file}: {_preview_output(heartbeat_file)}")
            print(f"[reminder] {reminder_file}: {_preview_output(reminder_file)}")
            await asyncio.sleep(AUTO_DEMO_STEP_SLEEP_SECONDS)
    finally:
        remaining_job_ids = [job.id for job in await runtime.list_jobs()]
        await runtime.cleanup_jobs(
            [job_id for job_id in added_job_ids if job_id in remaining_job_ids]
        )
        await runtime.scheduler.stop()
        print("Cron experience demo finished.")


if __name__ == "__main__":
    asyncio.run(main())
