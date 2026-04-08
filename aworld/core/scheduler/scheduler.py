# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron scheduler - timer loop with startup recovery.
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Tuple, List
import pytz

from aworld.logs.util import logger
from .types import CronJob
from .store import FileBasedCronStore
from .executor import CronExecutor


class CronScheduler:
    """
    Cron scheduler with reliable timer loop.

    Features:
    - Startup recovery (cleanup stale running, recalculate next_run)
    - Concurrent execution limits
    - Timeout protection
    - Exponential backoff retry
    """

    def __init__(
        self,
        store: FileBasedCronStore,
        executor: CronExecutor,
        max_concurrent: int = 5
    ):
        """
        Initialize scheduler.

        Args:
            store: Job storage
            executor: Job executor
            max_concurrent: Maximum concurrent job executions
        """
        self.store = store
        self.executor = executor
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running = False
        self._timer_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start scheduler with recovery."""
        if self.running:
            logger.warning("Scheduler already running")
            return

        logger.info("Starting cron scheduler...")

        # Startup recovery
        await self._cleanup_stale_running()
        await self._recalculate_next_runs()

        # Start timer loop
        self.running = True
        self._timer_task = asyncio.create_task(self._schedule_loop())

        logger.info("Cron scheduler started")

    async def stop(self):
        """Stop scheduler."""
        if not self.running:
            return

        logger.info("Stopping cron scheduler...")
        self.running = False

        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass

        logger.info("Cron scheduler stopped")

    async def _cleanup_stale_running(self):
        """Clean up jobs that were running when scheduler crashed."""
        jobs = await self.store.list_jobs()
        stale_count = 0

        for job in jobs:
            if job.state.running:
                await self.store.update_job(
                    job.id,
                    state={
                        "running": False,
                        "last_status": "error",
                        "last_error": "Scheduler restarted while job was running",
                    }
                )
                stale_count += 1

        if stale_count > 0:
            logger.info(f"Cleaned up {stale_count} stale running jobs")

    async def _recalculate_next_runs(self):
        """Recalculate next_run_time for all enabled jobs."""
        jobs = await self.store.list_jobs(enabled_only=True)
        now = datetime.now(pytz.UTC)

        for job in jobs:
            next_run = self._calculate_next_run(job, now)
            if next_run:
                await self.store.update_job(
                    job.id,
                    state={"next_run_at": next_run.isoformat()}
                )

        logger.info(f"Recalculated next_run for {len(jobs)} enabled jobs")

    async def _schedule_loop(self):
        """Main scheduling loop."""
        logger.info("Scheduler loop started")

        while self.running:
            try:
                jobs = await self.store.list_jobs(enabled_only=True)

                # Find next job to run
                next_job, wait_seconds = self._find_next_job(jobs)

                if next_job and wait_seconds <= 0:
                    # Job is due - trigger execution (non-blocking)
                    asyncio.create_task(self._trigger_job(next_job))
                    await asyncio.sleep(1)  # Prevent tight loop
                else:
                    # Wait until next job or check interval
                    sleep_time = min(wait_seconds, 60) if next_job else 60
                    await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Scheduler loop error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Brief pause before retry

    def _find_next_job(self, jobs: List[CronJob]) -> Tuple[Optional[CronJob], float]:
        """
        Find the next job to run.

        Args:
            jobs: List of enabled jobs

        Returns:
            Tuple of (next_job, wait_seconds)
        """
        now = datetime.now(pytz.UTC)
        next_job = None
        min_wait = float('inf')

        for job in jobs:
            # Skip if already running
            if job.state.running:
                continue

            # Get next run time
            if job.state.next_run_at:
                try:
                    next_run = datetime.fromisoformat(job.state.next_run_at.replace('Z', '+00:00'))
                except ValueError:
                    logger.warning(f"Invalid next_run_at for job {job.id}: {job.state.next_run_at}")
                    continue
            else:
                # Calculate if not set
                next_run = self._calculate_next_run(job, now)
                if not next_run:
                    continue

            wait_seconds = (next_run - now).total_seconds()

            if 0 <= wait_seconds < min_wait:
                min_wait = wait_seconds
                next_job = job

        return next_job, max(0, min_wait) if next_job else 60

    def _calculate_next_run(self, job: CronJob, now: datetime) -> Optional[datetime]:
        """
        Calculate next run time for a job.

        Args:
            job: Job to calculate for
            now: Current time (UTC)

        Returns:
            Next run time or None if no more runs
        """
        schedule = job.schedule

        try:
            if schedule.kind == "at":
                # One-time task
                if not schedule.at:
                    return None
                at_time = datetime.fromisoformat(schedule.at.replace('Z', '+00:00'))
                return at_time if at_time > now else None

            elif schedule.kind == "every":
                # Interval repetition
                if not schedule.every_seconds:
                    return None

                if job.state.last_run_at:
                    last_run = datetime.fromisoformat(job.state.last_run_at.replace('Z', '+00:00'))
                    return last_run + timedelta(seconds=schedule.every_seconds)
                else:
                    # First run - execute immediately
                    return now

            elif schedule.kind == "cron":
                # Cron expression
                if not schedule.cron_expr:
                    return None

                from croniter import croniter

                # Parse timezone
                tz = pytz.timezone(schedule.timezone)
                now_in_tz = now.astimezone(tz)

                # Calculate next run in user timezone
                cron = croniter(schedule.cron_expr, now_in_tz)
                next_run_in_tz = cron.get_next(datetime)

                # Convert to UTC
                next_run_utc = next_run_in_tz.astimezone(pytz.UTC)
                return next_run_utc

        except Exception as e:
            logger.error(f"Failed to calculate next_run for job {job.id}: {e}")
            return None

        return None

    async def _trigger_job(self, job: CronJob):
        """
        Trigger job execution with concurrency control and timeout.

        Args:
            job: Job to execute
        """
        async with self.semaphore:  # Concurrency control
            try:
                logger.info(f"Triggering cron job: {job.id} ({job.name})")

                # Mark as running
                await self.store.update_job(
                    job.id,
                    state={
                        "running": True,
                        "last_run_at": datetime.utcnow().isoformat()
                    }
                )

                # Execute with timeout
                timeout = job.payload.timeout_seconds or 600
                result = await asyncio.wait_for(
                    self.executor.execute_with_retry(job),
                    timeout=timeout
                )

                # Update state
                await self.store.update_job(
                    job.id,
                    state={
                        "running": False,
                        "last_status": "ok" if result.success else "error",
                        "last_error": result.msg if not result.success else None,
                        "consecutive_errors": 0 if result.success else job.state.consecutive_errors + 1,
                    }
                )

                # Calculate next run time
                next_run = self._calculate_next_run(job, datetime.now(pytz.UTC))
                if next_run:
                    await self.store.update_job(
                        job.id,
                        state={"next_run_at": next_run.isoformat()}
                    )

                # Delete one-time jobs
                if job.delete_after_run:
                    await self.store.remove_job(job.id)
                    logger.info(f"Deleted one-time job: {job.id}")

            except asyncio.TimeoutError:
                logger.error(f"Job {job.id} execution timeout")
                await self.store.update_job(
                    job.id,
                    state={
                        "running": False,
                        "last_status": "timeout",
                        "last_error": f"Execution timeout after {job.payload.timeout_seconds or 600}s",
                        "consecutive_errors": job.state.consecutive_errors + 1,
                    }
                )

            except Exception as e:
                logger.error(f"Job {job.id} trigger error: {e}", exc_info=True)
                await self.store.update_job(
                    job.id,
                    state={
                        "running": False,
                        "last_status": "error",
                        "last_error": str(e),
                        "consecutive_errors": job.state.consecutive_errors + 1,
                    }
                )

    # Public API

    async def add_job(self, job: CronJob) -> CronJob:
        """
        Add a new job.

        Args:
            job: Job to add

        Returns:
            Added job with calculated next_run_time
        """
        # Calculate initial next_run
        now = datetime.now(pytz.UTC)
        next_run = self._calculate_next_run(job, now)
        if next_run:
            job.state.next_run_at = next_run.isoformat()

        return await self.store.add_job(job)

    async def update_job(self, job_id: str, **updates):
        """Update job fields."""
        return await self.store.update_job(job_id, **updates)

    async def remove_job(self, job_id: str) -> bool:
        """Remove a job."""
        return await self.store.remove_job(job_id)

    async def run_job(self, job_id: str, force: bool = False):
        """
        Manually trigger a job.

        Args:
            job_id: Job ID
            force: If True, run even if disabled

        Returns:
            Task response
        """
        job = await self.store.get_job(job_id)
        if not job:
            from aworld.core.task import TaskResponse
            return TaskResponse(success=False, msg=f"Job not found: {job_id}")

        if not job.enabled and not force:
            from aworld.core.task import TaskResponse
            return TaskResponse(success=False, msg=f"Job is disabled: {job_id}")

        # Execute directly (bypass scheduler)
        logger.info(f"Manually running job: {job_id}")
        result = await self.executor.execute_with_retry(job)

        # Update state
        await self.store.update_job(
            job_id,
            state={
                "last_run_at": datetime.utcnow().isoformat(),
                "last_status": "ok" if result.success else "error",
                "last_error": result.msg if not result.success else None,
            }
        )

        return result

    async def list_jobs(self, enabled_only: bool = False) -> List[CronJob]:
        """List all jobs."""
        return await self.store.list_jobs(enabled_only=enabled_only)

    async def get_status(self):
        """Get scheduler status."""
        jobs = await self.store.list_jobs()
        return {
            "running": self.running,
            "total_jobs": len(jobs),
            "enabled_jobs": len([j for j in jobs if j.enabled]),
        }
