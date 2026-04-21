# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron scheduler - timer loop with startup recovery.
"""
import asyncio
import re
import traceback
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Callable, Any, Awaitable, Literal
import pytz

from aworld.logs.util import logger
from .types import CronJob
from .store import FileBasedCronStore
from .executor import CronExecutor

_LEGACY_STOP_JOB_RE = re.compile(
    r"停止.+?任务[（(]ID:\s*(?P<job_id>[0-9a-fA-F-]{36})[)）]"
)


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
        max_concurrent: int = 5,
        notification_sink: Optional[Callable[[Any], Awaitable[None]]] = None,
        progress_sink: Optional[Callable[[Any], Awaitable[None]]] = None,
    ):
        """
        Initialize scheduler.

        Args:
            store: Job storage
            executor: Job executor
            max_concurrent: Maximum concurrent job executions
            notification_sink: Optional callback for publishing notifications
        """
        self.store = store
        self.executor = executor
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running = False
        self._timer_task: Optional[asyncio.Task] = None
        self.notification_sink = notification_sink
        self.progress_sink = progress_sink

    async def _publish_progress(
        self,
        job: CronJob,
        level: Literal["info", "warning", "error", "success"],
        message: str,
        terminal: bool = False,
    ) -> None:
        """Publish live execution logs for `/cron show` follow mode."""
        if not self.progress_sink:
            return

        try:
            await self.progress_sink({
                "job_id": job.id,
                "job_name": job.name,
                "level": level,
                "message": message,
                "terminal": terminal,
                "created_at": datetime.now(pytz.UTC).isoformat(),
            })
        except Exception as e:
            logger.warning(f"Failed to publish progress for job {job.id}: {e}")

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
            if self._has_reached_max_runs(job):
                await self.store.update_job(
                    job.id,
                    enabled=False,
                    state={"next_run_at": None, "running": False}
                )
                continue

            next_run = self._calculate_next_run(job, now)
            # Always update next_run_at (even if None for expired one-time tasks)
            await self.store.update_job(
                job.id,
                state={"next_run_at": next_run.isoformat() if next_run else None}
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
                    # Job is due - calculate next run time and claim atomically
                    now = datetime.now(pytz.UTC)
                    now_iso = now.isoformat()
                    next_run = self._calculate_claim_next_run(next_job, now)
                    next_run_iso = next_run.isoformat() if next_run else None

                    claimed_job = await self.store.claim_due_job(next_job.id, now_iso, next_run_iso)

                    if claimed_job:
                        # Successfully claimed - trigger execution (non-blocking)
                        asyncio.create_task(self._execute_claimed_job(claimed_job))
                    else:
                        logger.debug(f"Failed to claim job {next_job.id} (may be claimed by another tick)")

                    await asyncio.sleep(0.1)  # Brief pause before next check
                else:
                    # Wait until next job or check interval
                    sleep_time = min(wait_seconds, 60) if next_job else 60
                    await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Scheduler loop error\n{traceback.format_exc()}")
                await asyncio.sleep(5)  # Brief pause before retry

    def _find_next_job(self, jobs: List[CronJob]) -> Tuple[Optional[CronJob], float]:
        """
        Find the next job to run.

        Critical: This method MUST select overdue jobs (negative wait_seconds).
        Jobs can become overdue due to:
        - Scheduler loop delays (processing other jobs)
        - Event loop blocking (I/O operations)
        - High system load
        - Time boundary conditions

        Args:
            jobs: List of enabled jobs

        Returns:
            Tuple of (next_job, wait_seconds)
            - If overdue jobs exist, returns most overdue (most negative wait_seconds)
            - Otherwise returns next upcoming job
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

            # CRITICAL FIX: Select ALL jobs with wait_seconds < min_wait
            # This includes overdue jobs (negative wait_seconds)
            # Overdue jobs will naturally have the smallest (most negative) wait_seconds
            if wait_seconds < min_wait:
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
            if self._has_reached_max_runs(job):
                return None

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
                    next_run = last_run + timedelta(seconds=schedule.every_seconds)

                    # FIX: Skip missed periods if offline duration > every_seconds
                    # Design requirement: Offline missed runs should NOT be replayed
                    # Ensure next_run is always in the future (no catchup execution)
                    while next_run <= now:
                        next_run += timedelta(seconds=schedule.every_seconds)

                    return next_run
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
            # Runtime error handling: allows recovery during schedule loop and startup
            # Input validation should happen at tool layer (cron_tool._parse_schedule)
            # to prevent creating jobs with invalid schedules
            logger.error(f"Failed to calculate next_run for job {job.id}: {e}")
            return None

        return None

    def _calculate_claim_next_run(self, job: CronJob, claimed_at: datetime) -> Optional[datetime]:
        """Calculate the next persisted run time at claim time."""
        if self._has_reached_max_runs(job):
            return None

        if job.schedule.kind == "every" and job.schedule.every_seconds:
            return claimed_at + timedelta(seconds=job.schedule.every_seconds)

        return self._calculate_next_run(job, claimed_at)

    def _has_reached_max_runs(self, job: CronJob, run_count: Optional[int] = None) -> bool:
        """Return True when a bounded recurring job has exhausted its run budget."""
        max_runs = self._coerce_max_runs(job.payload.max_runs)
        current_runs = job.state.run_count if run_count is None else run_count
        return bool(max_runs and max_runs > 0 and current_runs >= max_runs)

    def _coerce_max_runs(self, value: Any) -> Optional[int]:
        """Best-effort integer coercion for runtime safety with legacy persisted data."""
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return int(stripped)
        return int(value)

    def _extract_legacy_stop_target_id(self, message: str) -> Optional[str]:
        """Extract target job ID from legacy 'stop reminder' control messages."""
        match = _LEGACY_STOP_JOB_RE.search((message or "").strip())
        return match.group("job_id") if match else None

    def _get_reminder_detail(self, job: CronJob) -> Optional[str]:
        """
        Return user-facing reminder content for notification-only reminder jobs.

        Reminder jobs are notification-style tasks, not autonomous agent work.
        They should surface their payload directly in the CLI instead of
        re-entering the agent and recursively scheduling more cron jobs.
        """
        message = (job.payload.message or "").strip()
        if not message:
            return None

        job_name = (job.name or "").strip()
        message_lower = message.lower()
        name_lower = job_name.lower()

        is_reminder = (
            "提醒" in message
            or "提醒" in job_name
            or "remind" in message_lower
            or "remind" in name_lower
        )
        if not is_reminder:
            return None

        # Tool-backed jobs are automation tasks even if their name mentions reminders.
        if job.payload.tool_names:
            return None

        normalized_message = re.sub(r"^提醒(?:用户|您)", "提醒我", message)
        normalized_message = re.sub(r"^提醒我进行(?=\S)", "提醒我", normalized_message)
        return normalized_message

    def _stringify_result_value(self, value: Any) -> Optional[str]:
        """Best-effort conversion of task output to a concise user-facing string."""
        if value is None:
            return None

        if isinstance(value, str):
            text = value.strip()
        else:
            text = str(value).strip()

        if not text:
            return None

        text = re.sub(r"\s+", " ", text)
        if len(text) > 280:
            return f"{text[:277]}..."
        return text

    def _stringify_result_detail(self, value: Any, limit: int = 4000) -> Optional[str]:
        """Best-effort conversion of task output to a richer multiline detail string."""
        if value is None:
            return None

        text = value if isinstance(value, str) else str(value)
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return None

        if len(text) > limit:
            return f"{text[:limit - 3]}..."
        return text

    def _get_result_summary(self, result: Any) -> Optional[str]:
        """Extract a short execution summary from TaskResponse-like objects."""
        if result is None:
            return None

        answer_text = self._stringify_result_value(getattr(result, "answer", None))
        if answer_text:
            return answer_text

        return self._stringify_result_value(getattr(result, "msg", None))

    def _get_result_detail(self, result: Any) -> Optional[str]:
        """Extract a richer notification detail from TaskResponse-like objects."""
        if result is None:
            return None

        answer_text = self._stringify_result_detail(getattr(result, "answer", None))
        msg_text = self._stringify_result_detail(getattr(result, "msg", None), limit=1000)
        generic_msgs = {"ok", "success", "task completed", "执行完成"}

        if answer_text:
            normalized_answer = answer_text.strip().lower()
            normalized_msg = (msg_text or "").strip().lower()
            if (
                msg_text
                and normalized_msg
                and normalized_msg not in generic_msgs
                and normalized_msg not in normalized_answer
            ):
                return f"最终回答：\n{answer_text}\n\n结果消息：\n{msg_text}"
            return f"最终回答：\n{answer_text}"

        if msg_text:
            return msg_text

        return None

    async def _execute_job_payload(self, job: CronJob):
        """Execute a job or short-circuit legacy stop-task payloads."""
        from aworld.core.task import TaskResponse

        target_job_id = self._extract_legacy_stop_target_id(job.payload.message)
        if target_job_id:
            target_job = await self.store.get_job(target_job_id)
            if target_job:
                await self.store.update_job(
                    target_job_id,
                    enabled=False,
                    state={"next_run_at": None, "running": False}
                )
                logger.info(f"Disabled legacy stop-job target: {target_job_id}")
            else:
                logger.info(f"Legacy stop-job target already absent: {target_job_id}")

            return TaskResponse(success=True, msg=f"Stopped job {target_job_id}")

        reminder_detail = self._get_reminder_detail(job)
        if reminder_detail:
            await self._publish_progress(job, "info", "识别为提醒类任务，直接生成提醒内容")
            return TaskResponse(success=True, msg=reminder_detail, answer=reminder_detail)

        return await self.executor.execute_with_retry(
            job,
            progress_callback=lambda level, message: self._publish_progress(job, level, message),
        )

    async def _persist_job_result(self, job: CronJob, result) -> tuple[Optional[CronJob], bool]:
        """Persist terminal execution state and return the updated job."""
        run_count = job.state.run_count + (1 if result.success else 0)
        next_run_at = job.state.next_run_at
        result_summary = self._get_result_summary(result) if result.success else None
        updates = {
            "running": False,
            "last_status": "ok" if result.success else "error",
            "last_error": None if result.success else result.msg,
            "last_result_summary": result_summary,
            "consecutive_errors": 0 if result.success else job.state.consecutive_errors + 1,
            "run_count": run_count,
        }
        job_updates = {}

        if result.success and self._has_reached_max_runs(job, run_count):
            updates["next_run_at"] = None
            next_run_at = None
            job_updates["enabled"] = False

        persisted_job = await self.store.update_job(job.id, state=updates, **job_updates)
        should_remove = result.success and job.delete_after_run and next_run_at is None
        if should_remove:
            await self.store.remove_job(job.id)
            return None, True

        if persisted_job:
            return persisted_job, False

        return await self.store.get_job(job.id), False

    async def _publish_notification(
        self,
        job: CronJob,
        status: Literal["ok", "error", "timeout"],
        result: Optional[Any] = None,
        user_visible: bool = True,
    ):
        """
        Publish notification if sink is configured.

        Args:
            job: Job that reached terminal state
            status: Terminal status (ok/error/timeout)

        Note:
            Per design doc Section 8.4, notification summary uses fixed templates
            without embedding raw error text. Full error detail is persisted in
            CronJob.state.last_error and available via /cron list.

            Per design doc Section 8.5, this is called AFTER state persistence
            and job deletion (if applicable) to ensure /cron list reflects truth
            when user sees the notification.
        """
        if not self.notification_sink:
            return

        try:
            # Build summary using fixed templates (Section 8.4)
            if status == "ok":
                summary = f'Cron task "{job.name}" completed'
            elif status == "timeout":
                timeout_seconds = job.payload.timeout_seconds or 600
                summary = f'Cron task "{job.name}" timed out after {timeout_seconds}s'
            else:  # error
                # Fixed template - do NOT include raw error text in notification
                summary = f'Cron task "{job.name}" failed'

            detail = self._build_notification_detail(job, status, result=result)

            notification_data = {
                'job_id': job.id,
                'job_name': job.name,
                'status': status,
                'summary': summary,
                'detail': detail,
                'created_at': datetime.now(pytz.UTC).isoformat(),
                'next_run_at': job.state.next_run_at,
                'user_visible': user_visible,
            }

            await self.notification_sink(notification_data)

        except Exception as e:
            # Graceful failure - notification system should never crash scheduler
            logger.warning(f"Failed to publish notification for job {job.id}: {e}")

    def _build_notification_detail(
        self,
        job: CronJob,
        status: Literal["ok", "error", "timeout"],
        result: Optional[Any] = None,
    ) -> Optional[str]:
        """
        Build optional user-facing detail for notifications.

        Only successful reminder-like jobs include reminder content so the CLI
        can show the actual reminder text, not just the terminal state.
        """
        if status != "ok":
            return None

        reminder_detail = self._get_reminder_detail(job)
        if reminder_detail:
            return reminder_detail

        result_detail = self._get_result_detail(result)
        if result_detail:
            return result_detail

        return job.state.last_result_summary

    async def _execute_claimed_job(self, job: CronJob):
        """
        Execute a job that has already been claimed.

        This method assumes the job has been atomically claimed via store.claim_due_job(),
        so it skips the claim logic and proceeds directly to execution.

        Args:
            job: Already claimed job (running=True, last_run_at set)
        """
        async with self.semaphore:  # Concurrency control
            try:
                logger.info(f"Executing claimed cron job: {job.id} ({job.name})")
                tools_text = job.payload.tool_names if job.payload.tool_names else "auto"
                await self._publish_progress(
                    job,
                    "info",
                    f"任务开始执行，agent={job.payload.agent_name}，tools={tools_text}",
                )

                # Execute with timeout
                timeout = job.payload.timeout_seconds or 600
                result = await asyncio.wait_for(
                    self._execute_job_payload(job),
                    timeout=timeout
                )

                persisted_job, removed = await self._persist_job_result(job, result)
                notification_job = persisted_job or job
                user_visible = bool(getattr(result, "user_visible", True))

                # Publish notification (after state persistence and deletion)
                if result.success:
                    success_summary = self._get_result_summary(result)
                    if user_visible:
                        success_message = (
                            f"任务执行完成：{success_summary}"
                            if success_summary else
                            "任务执行完成"
                        )
                        await self._publish_progress(notification_job, "success", success_message, terminal=True)
                        await self._publish_notification(
                            notification_job,
                            "ok",
                            result=result,
                            user_visible=True,
                        )
                    else:
                        quiet_message = success_summary or getattr(result, "msg", None) or "本次检查未触发通知"
                        await self._publish_progress(
                            notification_job,
                            "info",
                            quiet_message,
                            terminal=bool(notification_job.state.next_run_at is None),
                        )
                        if notification_job.state.next_run_at is None:
                            await self._publish_notification(
                                notification_job,
                                "ok",
                                result=result,
                                user_visible=False,
                            )
                    if removed:
                        logger.info(f"Deleted one-time job: {job.id}")
                else:
                    await self._publish_progress(
                        notification_job,
                        "error",
                        f"任务执行失败：{result.msg}",
                        terminal=True,
                    )
                    await self._publish_notification(notification_job, "error", result=result)

            except asyncio.TimeoutError:
                logger.error(f"Job {job.id} execution timeout")
                await self.store.update_job(
                    job.id,
                    state={
                        "running": False,
                        "last_status": "timeout",
                        "last_error": f"Execution timeout after {job.payload.timeout_seconds or 600}s",
                        "last_result_summary": None,
                        "consecutive_errors": job.state.consecutive_errors + 1,
                    }
                )
                await self._publish_progress(
                    job,
                    "error",
                    f"任务执行超时：{job.payload.timeout_seconds or 600}s",
                    terminal=True,
                )

                # Publish timeout notification
                await self._publish_notification(job, "timeout")

            except Exception as e:
                logger.error(f"Job {job.id} trigger error\n{traceback.format_exc()}")
                await self.store.update_job(
                    job.id,
                    state={
                        "running": False,
                        "last_status": "error",
                        "last_error": str(e),
                        "last_result_summary": None,
                        "consecutive_errors": job.state.consecutive_errors + 1,
                    }
                )
                await self._publish_progress(
                    job,
                    "error",
                    f"任务执行异常：{e}",
                    terminal=True,
                )

                # Publish error notification
                await self._publish_notification(job, "error")

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
        current_job = await self.store.get_job(job_id)
        if not current_job:
            return None

        if updates.get("enabled") is True and not current_job.enabled:
            now = datetime.now(pytz.UTC)
            recalculated_next_run = self._calculate_next_run(current_job, now)
            state_updates = dict(updates.get("state") or {})
            state_updates["next_run_at"] = recalculated_next_run.isoformat() if recalculated_next_run else None
            updates["state"] = state_updates

        return await self.store.update_job(job_id, **updates)

    async def remove_job(self, job_id: str) -> bool:
        """Remove a job."""
        return await self.store.remove_job(job_id)

    async def run_job(self, job_id: str, force: bool = False):
        """
        Manually trigger a job.

        Respects semaphore, timeout, and state update logic like scheduled execution,
        but does NOT advance next_run_at (preserves recurring cadence).

        Args:
            job_id: Job ID
            force: If True, run even if disabled

        Returns:
            Task response
        """
        from aworld.core.task import TaskResponse

        job = await self.store.get_job(job_id)
        if not job:
            return TaskResponse(success=False, msg=f"Job not found: {job_id}")

        if not job.enabled and not force:
            return TaskResponse(success=False, msg=f"Job is disabled: {job_id}")

        # Check if already running (prevent concurrent execution)
        if job.state.running:
            return TaskResponse(success=False, msg=f"Job is already running: {job_id}")

        # Mark as running (without advancing next_run_at)
        updated_job = await self.store.update_job(
            job_id,
            state={"running": True, "last_run_at": datetime.now(pytz.UTC).isoformat()}
        )
        if not updated_job:
            return TaskResponse(success=False, msg=f"Failed to mark job as running: {job_id}")

        # Execute with semaphore and timeout (same as scheduled execution)
        async with self.semaphore:
            try:
                logger.info(f"Manually running job: {job_id}")
                tools_text = updated_job.payload.tool_names if updated_job.payload.tool_names else "auto"
                await self._publish_progress(
                    updated_job,
                    "info",
                    f"手动触发执行，agent={updated_job.payload.agent_name}，tools={tools_text}",
                )
                timeout = job.payload.timeout_seconds or 600
                result = await asyncio.wait_for(
                    self._execute_job_payload(updated_job),
                    timeout=timeout
                )

                persisted_job, _ = await self._persist_job_result(updated_job, result)
                notification_job = persisted_job or updated_job

                # Publish notification for manual run
                if result.success:
                    success_summary = self._get_result_summary(result)
                    success_message = (
                        f"任务执行完成：{success_summary}"
                        if success_summary else
                        "任务执行完成"
                    )
                    await self._publish_progress(notification_job, "success", success_message, terminal=True)
                    await self._publish_notification(notification_job, "ok", result=result)
                else:
                    await self._publish_progress(
                        notification_job,
                        "error",
                        f"任务执行失败：{result.msg}",
                        terminal=True,
                    )
                    await self._publish_notification(notification_job, "error")

                return result

            except asyncio.TimeoutError:
                logger.error(f"Manual job {job_id} execution timeout")
                await self.store.update_job(
                    job_id,
                    state={
                        "running": False,
                        "last_status": "timeout",
                        "last_error": f"Execution timeout after {job.payload.timeout_seconds or 600}s",
                        "last_result_summary": None,
                        "consecutive_errors": job.state.consecutive_errors + 1,
                    }
                )
                await self._publish_progress(
                    updated_job,
                    "error",
                    f"任务执行超时：{job.payload.timeout_seconds or 600}s",
                    terminal=True,
                )

                # Publish timeout notification for manual run
                await self._publish_notification(job, "timeout")

                return TaskResponse(success=False, msg="Execution timeout")

            except Exception as e:
                logger.error(
                    f"Manual job {job_id} execution error\n{traceback.format_exc()}"
                )
                await self.store.update_job(
                    job_id,
                    state={
                        "running": False,
                        "last_status": "error",
                        "last_error": str(e),
                        "last_result_summary": None,
                        "consecutive_errors": job.state.consecutive_errors + 1,
                    }
                )
                await self._publish_progress(
                    updated_job,
                    "error",
                    f"任务执行异常：{e}",
                    terminal=True,
                )

                # Publish error notification for manual run
                await self._publish_notification(job, "error")

                return TaskResponse(success=False, msg=f"Execution error: {str(e)}")

    async def list_jobs(self, enabled_only: bool = False) -> List[CronJob]:
        """List all jobs."""
        return await self.store.list_jobs(enabled_only=enabled_only)

    async def get_job(self, job_id: str) -> Optional[CronJob]:
        """Get a single job by ID."""
        return await self.store.get_job(job_id)

    async def get_status(self):
        """Get scheduler status."""
        jobs = await self.store.list_jobs()
        return {
            "running": self.running,
            "total_jobs": len(jobs),
            "enabled_jobs": len([j for j in jobs if j.enabled]),
        }
