# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron job storage with atomic writes and file locking.
"""
import asyncio
import fcntl
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from aworld.logs.util import logger
from .types import CronJob, CronSchedule, CronPayload, CronJobState


class FileBasedCronStore:
    """
    File-based cron job storage with atomic writes and locking.

    Features:
    - Atomic writes (temp file + rename)
    - File locking (fcntl)
    - Process-local mutex (asyncio.Lock) for read-modify-write operations
    - Automatic directory creation
    """

    def __init__(self, file_path: str):
        """
        Initialize store.

        Args:
            file_path: Path to cron jobs file (e.g., '.aworld/cron.json')
        """
        self.file_path = Path(file_path)
        self._lock = asyncio.Lock()  # Process-local mutex for read-modify-write
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Create file and parent directories if they don't exist."""
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_data({"version": 1, "jobs": []})
            logger.info(f"Created cron store: {self.file_path}")

    def _read_data(self) -> Dict[str, Any]:
        """
        Read data from file with shared lock.

        Returns:
            Dict with 'version' and 'jobs' keys
        """
        try:
            with open(self.file_path, "r") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse cron store: {e}")
            return {"version": 1, "jobs": []}
        except Exception as e:
            logger.error(f"Failed to read cron store: {e}")
            return {"version": 1, "jobs": []}

    def _write_data(self, data: Dict[str, Any]):
        """
        Write data to file atomically with exclusive lock.

        Args:
            data: Dict with 'version' and 'jobs' keys
        """
        temp_file = self.file_path.with_suffix('.tmp')

        try:
            with open(temp_file, "w") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock
                try:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            # Atomic replace
            temp_file.replace(self.file_path)
        except Exception as e:
            logger.error(f"Failed to write cron store: {e}")
            if temp_file.exists():
                temp_file.unlink()
            raise

    def _job_to_dict(self, job: CronJob) -> Dict[str, Any]:
        """Convert CronJob to dict for JSON serialization."""
        return {
            "id": job.id,
            "name": job.name,
            "description": job.description,
            "enabled": job.enabled,
            "delete_after_run": job.delete_after_run,
            "schedule": {
                "kind": job.schedule.kind,
                "at": job.schedule.at,
                "every_seconds": job.schedule.every_seconds,
                "cron_expr": job.schedule.cron_expr,
                "timezone": job.schedule.timezone,
            },
            "payload": {
                "message": job.payload.message,
                "agent_name": job.payload.agent_name,
                "tool_names": job.payload.tool_names,
                "timeout_seconds": job.payload.timeout_seconds,
            },
            "state": {
                "next_run_at": job.state.next_run_at,
                "last_run_at": job.state.last_run_at,
                "last_status": job.state.last_status,
                "last_error": job.state.last_error,
                "running": job.state.running,
                "consecutive_errors": job.state.consecutive_errors,
            },
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    def _dict_to_job(self, data: Dict[str, Any]) -> CronJob:
        """Convert dict to CronJob."""
        return CronJob(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            enabled=data.get("enabled", True),
            delete_after_run=data.get("delete_after_run", False),
            schedule=CronSchedule(
                kind=data["schedule"]["kind"],
                at=data["schedule"].get("at"),
                every_seconds=data["schedule"].get("every_seconds"),
                cron_expr=data["schedule"].get("cron_expr"),
                timezone=data["schedule"].get("timezone", "UTC"),
            ),
            payload=CronPayload(
                message=data["payload"]["message"],
                agent_name=data["payload"].get("agent_name", "Aworld"),
                tool_names=data["payload"].get("tool_names", []),
                timeout_seconds=data["payload"].get("timeout_seconds"),
            ),
            state=CronJobState(
                next_run_at=data["state"].get("next_run_at"),
                last_run_at=data["state"].get("last_run_at"),
                last_status=data["state"].get("last_status"),
                last_error=data["state"].get("last_error"),
                running=data["state"].get("running", False),
                consecutive_errors=data["state"].get("consecutive_errors", 0),
            ),
            created_at=data.get("created_at", datetime.utcnow().isoformat()),
            updated_at=data.get("updated_at", datetime.utcnow().isoformat()),
        )

    async def add_job(self, job: CronJob) -> CronJob:
        """
        Add a new job.

        Args:
            job: Job to add

        Returns:
            Added job
        """
        async with self._lock:
            data = self._read_data()
            data["jobs"].append(self._job_to_dict(job))
            self._write_data(data)
            logger.info(f"Added cron job: {job.id} ({job.name})")
            return job

    async def update_job(self, job_id: str, **updates) -> Optional[CronJob]:
        """
        Update job fields.

        Args:
            job_id: Job ID
            **updates: Fields to update (e.g., state={...}, enabled=False)

        Returns:
            Updated job or None if not found
        """
        async with self._lock:
            data = self._read_data()

            for job_dict in data["jobs"]:
                if job_dict["id"] == job_id:
                    # Update fields
                    for key, value in updates.items():
                        if key == "state":
                            # Merge state updates
                            job_dict["state"].update(value)
                        else:
                            job_dict[key] = value

                    job_dict["updated_at"] = datetime.utcnow().isoformat()
                    self._write_data(data)
                    logger.debug(f"Updated cron job: {job_id}")
                    return self._dict_to_job(job_dict)

            logger.warning(f"Job not found for update: {job_id}")
            return None

    async def remove_job(self, job_id: str) -> bool:
        """
        Remove a job.

        Args:
            job_id: Job ID

        Returns:
            True if removed, False if not found
        """
        async with self._lock:
            data = self._read_data()
            original_count = len(data["jobs"])
            data["jobs"] = [j for j in data["jobs"] if j["id"] != job_id]

            if len(data["jobs"]) < original_count:
                self._write_data(data)
                logger.info(f"Removed cron job: {job_id}")
                return True

            logger.warning(f"Job not found for removal: {job_id}")
            return False

    async def get_job(self, job_id: str) -> Optional[CronJob]:
        """
        Get a job by ID.

        Args:
            job_id: Job ID

        Returns:
            Job or None if not found
        """
        data = self._read_data()
        for job_dict in data["jobs"]:
            if job_dict["id"] == job_id:
                return self._dict_to_job(job_dict)
        return None

    async def list_jobs(self, enabled_only: bool = False) -> List[CronJob]:
        """
        List all jobs.

        Args:
            enabled_only: Only return enabled jobs

        Returns:
            List of jobs
        """
        data = self._read_data()
        jobs = [self._dict_to_job(j) for j in data["jobs"]]

        if enabled_only:
            jobs = [j for j in jobs if j.enabled]

        return jobs

    async def claim_due_job(self, job_id: str, now_iso: str, next_run_at: Optional[str] = None) -> Optional[CronJob]:
        """
        Atomically claim a due job for execution.

        This is the critical operation that prevents duplicate execution:
        - Check if job is enabled, not running, and due
        - If so, atomically mark as running, update last_run_at, AND advance next_run_at
        - All checks and updates happen in a single read-modify-write cycle

        Args:
            job_id: Job ID to claim
            now_iso: Current time in ISO format (for comparison and timestamp)
            next_run_at: Next scheduled run time (ISO format) or None for one-shot jobs

        Returns:
            Claimed job if successful, None if job cannot be claimed
        """
        async with self._lock:
            data = self._read_data()

            for job_dict in data["jobs"]:
                if job_dict["id"] == job_id:
                    # Check if job can be claimed
                    if not job_dict.get("enabled", True):
                        logger.debug(f"Cannot claim job {job_id}: disabled")
                        return None

                    if job_dict["state"].get("running", False):
                        logger.debug(f"Cannot claim job {job_id}: already running")
                        return None

                    current_next_run = job_dict["state"].get("next_run_at")
                    if not current_next_run:
                        logger.debug(f"Cannot claim job {job_id}: no next_run_at")
                        return None

                    # Compare timestamps (both should be ISO format)
                    if current_next_run > now_iso:
                        logger.debug(f"Cannot claim job {job_id}: not due yet ({current_next_run} > {now_iso})")
                        return None

                    # Atomically claim the job AND advance next_run_at
                    job_dict["state"]["running"] = True
                    job_dict["state"]["last_run_at"] = now_iso
                    job_dict["state"]["next_run_at"] = next_run_at  # Advance to next schedule
                    job_dict["updated_at"] = datetime.utcnow().isoformat()

                    # Write atomically
                    self._write_data(data)

                    logger.info(f"Claimed job {job_id} for execution (next_run_at: {next_run_at})")
                    return self._dict_to_job(job_dict)

            logger.warning(f"Job not found for claim: {job_id}")
            return None
