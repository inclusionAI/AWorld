from dataclasses import dataclass
from typing import Iterable

from aworld.core.scheduler import CronJob, CronScheduler
from aworld_cli.runtime.cron_notifications import (
    CronNotification,
    CronNotificationCenter,
)

from .demo_setup import DemoPaths


@dataclass
class DemoRuntime:
    paths: DemoPaths
    scheduler: CronScheduler
    notification_center: CronNotificationCenter

    async def add_job(self, job: CronJob) -> CronJob:
        return await self.scheduler.add_job(job)

    async def list_jobs(self) -> list[CronJob]:
        return await self.scheduler.list_jobs(enabled_only=False)

    async def drain_notifications(self) -> list[CronNotification]:
        return await self.notification_center.drain()

    async def cleanup_jobs(self, job_ids: Iterable[str]) -> None:
        for job_id in job_ids:
            await self.scheduler.remove_job(job_id)
