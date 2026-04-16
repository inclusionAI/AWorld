# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron job storage with atomic writes and file locking.
"""
import asyncio
import os
import fcntl
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Dict, Any, Iterator
from datetime import UTC, datetime

from aworld.logs.util import logger
from .normalization import normalize_tool_names
from .types import CronJob, CronSchedule, CronPayload, CronJobState


class CronStoreReadError(RuntimeError):
    """Raised when the persisted cron store cannot be read safely."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _coerce_tool_names(value: Any) -> List[str]:
    return normalize_tool_names(value)


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
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


def _coerce_int(value: Any, default: int = 0) -> int:
    coerced = _coerce_optional_int(value)
    return default if coerced is None else coerced


def _normalize_agent_name(value: Any) -> str:
    if value is None:
        return "Aworld"
    agent_name = str(value).strip()
    if not agent_name:
        return "Aworld"
    if agent_name.lower() == "aworld":
        return "Aworld"
    return agent_name


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
        self.lock_file_path = self.file_path.with_name(f"{self.file_path.name}.lock")
        self._lock = asyncio.Lock()  # Process-local mutex for read-modify-write
        self._ensure_file_exists()

    @contextmanager
    def _file_lock(self, exclusive: bool) -> Iterator[None]:
        """Coordinate cross-process access with a dedicated lock file."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.lock_file_path, "a+") as lock_file:
            lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(lock_file.fileno(), lock_type)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _ensure_file_exists(self):
        """Create file and parent directories if they don't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file_path.touch(exist_ok=True)

        with self._file_lock(exclusive=True):
            if not self.file_path.exists():
                self._write_data({"version": 1, "jobs": []}, lock=False)
                logger.info(f"Created cron store: {self.file_path}")

    def _read_data_unlocked(self) -> Dict[str, Any]:
        """Read cron data assuming the caller already coordinated file access."""
        with open(self.file_path, "r") as f:
            return json.load(f)

    def _read_data(self, lock: bool = True) -> Dict[str, Any]:
        """
        Read data from file.

        Returns:
            Dict with 'version' and 'jobs' keys
        """
        try:
            if lock:
                with self._file_lock(exclusive=False):
                    return self._read_data_unlocked()
            return self._read_data_unlocked()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse cron store: {e}")
            raise CronStoreReadError(
                f"Cron store is corrupted and cannot be parsed: {self.file_path}"
            ) from e
        except Exception as e:
            logger.error(f"Failed to read cron store: {e}")
            raise CronStoreReadError(
                f"Cron store could not be read safely: {self.file_path}"
            ) from e

    def _write_data_unlocked(self, data: Dict[str, Any]):
        """Write data atomically assuming the caller already coordinated file access."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=self.file_path.parent,
                prefix=f"{self.file_path.name}.",
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as temp_file:
                json.dump(data, temp_file, indent=2, ensure_ascii=False)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)

            temp_path.replace(self.file_path)
        except Exception:
            if temp_path and temp_path.exists():
                temp_path.unlink()
            raise

    def _write_data(self, data: Dict[str, Any], lock: bool = True):
        """
        Write data to file atomically.

        Args:
            data: Dict with 'version' and 'jobs' keys
        """
        try:
            if lock:
                with self._file_lock(exclusive=True):
                    self._write_data_unlocked(data)
            else:
                self._write_data_unlocked(data)
        except Exception as e:
            logger.error(f"Failed to write cron store: {e}")
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
                "max_runs": job.payload.max_runs,
            },
            "state": {
                "next_run_at": job.state.next_run_at,
                "last_run_at": job.state.last_run_at,
                "last_status": job.state.last_status,
                "last_error": job.state.last_error,
                "last_result_summary": job.state.last_result_summary,
                "running": job.state.running,
                "consecutive_errors": job.state.consecutive_errors,
                "run_count": job.state.run_count,
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
            enabled=_coerce_bool(data.get("enabled", True)),
            delete_after_run=_coerce_bool(data.get("delete_after_run", False)),
            schedule=CronSchedule(
                kind=data["schedule"]["kind"],
                at=data["schedule"].get("at"),
                every_seconds=data["schedule"].get("every_seconds"),
                cron_expr=data["schedule"].get("cron_expr"),
                timezone=data["schedule"].get("timezone", "UTC"),
            ),
            payload=CronPayload(
                message=data["payload"]["message"],
                agent_name=_normalize_agent_name(data["payload"].get("agent_name", "Aworld")),
                tool_names=_coerce_tool_names(data["payload"].get("tool_names", [])),
                timeout_seconds=data["payload"].get("timeout_seconds"),
                max_runs=_coerce_optional_int(data["payload"].get("max_runs")),
            ),
            state=CronJobState(
                next_run_at=data["state"].get("next_run_at"),
                last_run_at=data["state"].get("last_run_at"),
                last_status=data["state"].get("last_status"),
                last_error=data["state"].get("last_error"),
                last_result_summary=data["state"].get("last_result_summary"),
                running=_coerce_bool(data["state"].get("running", False)),
                consecutive_errors=_coerce_int(data["state"].get("consecutive_errors", 0)),
                run_count=_coerce_int(data["state"].get("run_count", 0)),
            ),
            created_at=data.get("created_at", _utc_now_iso()),
            updated_at=data.get("updated_at", _utc_now_iso()),
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
            with self._file_lock(exclusive=True):
                data = self._read_data(lock=False)
                data["jobs"].append(self._job_to_dict(job))
                self._write_data(data, lock=False)
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
            with self._file_lock(exclusive=True):
                data = self._read_data(lock=False)

                for job_dict in data["jobs"]:
                    if job_dict["id"] == job_id:
                        # Update fields
                        for key, value in updates.items():
                            if key == "state":
                                # Merge state updates
                                job_dict["state"].update(value)
                            else:
                                job_dict[key] = value

                        job_dict["updated_at"] = _utc_now_iso()
                        self._write_data(data, lock=False)
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
            with self._file_lock(exclusive=True):
                data = self._read_data(lock=False)
                original_count = len(data["jobs"])
                data["jobs"] = [j for j in data["jobs"] if j["id"] != job_id]

                if len(data["jobs"]) < original_count:
                    self._write_data(data, lock=False)
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
            with self._file_lock(exclusive=True):
                data = self._read_data(lock=False)

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

                        # Compare timestamps as datetime objects (handles timezone offsets correctly)
                        # ISO 8601 strings with different timezone offsets cannot be compared lexicographically
                        # Example: "09:00:00+08:00" > "02:00:00+00:00" (string) but UTC+8 09:00 < UTC 02:00 (datetime)
                        try:
                            current_next_run_dt = datetime.fromisoformat(current_next_run.replace('Z', '+00:00'))
                            now_dt = datetime.fromisoformat(now_iso.replace('Z', '+00:00'))

                            if current_next_run_dt > now_dt:
                                logger.debug(f"Cannot claim job {job_id}: not due yet ({current_next_run} > {now_iso})")
                                return None
                        except (ValueError, AttributeError) as e:
                            logger.warning(f"Invalid timestamp format for job {job_id}: {e}")
                            return None

                        # Atomically claim the job AND advance next_run_at
                        job_dict["state"]["running"] = True
                        job_dict["state"]["last_run_at"] = now_iso
                        job_dict["state"]["next_run_at"] = next_run_at  # Advance to next schedule
                        job_dict["updated_at"] = _utc_now_iso()

                        # Write atomically
                        self._write_data(data, lock=False)

                        logger.info(f"Claimed job {job_id} for execution (next_run_at: {next_run_at})")
                        return self._dict_to_job(job_dict)

            logger.warning(f"Job not found for claim: {job_id}")
            return None
