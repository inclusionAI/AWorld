# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron task notification center for TUI.

Provides lightweight in-memory notification queue for cron task completions.
Notifications are published by scheduler and drained by console for display.
"""
import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional, List, Dict, Any, Callable
import pytz

from aworld.logs.util import logger


@dataclass
class CronNotification:
    """
    Notification for a cron task terminal state.

    Fields:
        id: Unique notification ID
        job_id: ID of the job that generated this notification
        job_name: Human-readable job name
        status: Terminal status (ok/error/timeout)
        summary: One-line summary for display
        detail: Optional reminder content for display
        created_at: Notification creation timestamp (ISO 8601)
        next_run_at: Next scheduled run time if job is recurring
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str = ""
    job_name: str = ""
    status: Literal["ok", "error", "timeout"] = "ok"
    summary: str = ""
    detail: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(pytz.UTC).isoformat())
    next_run_at: Optional[str] = None


@dataclass
class CronProgressLog:
    """In-memory live execution log for a running cron job."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str = ""
    job_name: str = ""
    level: Literal["info", "warning", "error", "success"] = "info"
    message: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(pytz.UTC).isoformat())
    terminal: bool = False


class CronNotificationCenter:
    """
    In-memory notification center for cron task completions.

    Design constraints (per design doc Section 8.3):
    - In-memory only (no persistence across restarts)
    - Process-local (no cross-process coordination)
    - FIFO order
    - Bounded size (max 100 notifications)

    Thread safety:
    - Uses asyncio.Queue for thread-safe publish/drain
    - Scheduler publishes from background task
    - Console drains from main event loop
    """

    def __init__(self, max_size: int = 100):
        """
        Initialize notification center.

        Args:
            max_size: Maximum number of notifications to keep (default 100)
        """
        self._max_size = max_size
        self._queue = asyncio.Queue(maxsize=max_size)
        self._unread_count = 0
        self._progress_logs: Dict[str, deque[CronProgressLog]] = {}
        self._progress_limit = 200
        self._change_listeners: list[Callable[[], None]] = []

    async def publish(self, notification_data: dict) -> None:
        """
        Publish a notification to the queue.

        Args:
            notification_data: Notification fields as dict

        Note:
            If queue is full, oldest notification is dropped (FIFO).
            This prevents unbounded growth during long-running sessions.
        """
        try:
            # Convert dict to CronNotification
            notification = CronNotification(
                job_id=notification_data.get('job_id', ''),
                job_name=notification_data.get('job_name', ''),
                status=notification_data.get('status', 'ok'),
                summary=notification_data.get('summary', ''),
                detail=notification_data.get('detail'),
                created_at=notification_data.get('created_at', datetime.now(pytz.UTC).isoformat()),
                next_run_at=notification_data.get('next_run_at')
            )

            # Try to add to queue without blocking
            try:
                self._queue.put_nowait(notification)
            except asyncio.QueueFull:
                # Drop oldest notification to make room (FIFO)
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(notification)
                except:
                    # If we can't drop oldest, silently skip this notification
                    pass

            self._unread_count = self._queue.qsize()
            self._notify_change_listeners()

        except Exception:
            # Graceful failure - notification system should never crash scheduler
            pass

    async def drain(self, job_id: Optional[str] = None) -> List[CronNotification]:
        """
        Drain pending notifications from the queue.

        Args:
            job_id: Optional job ID filter. When provided, only matching
                notifications are drained and unmatched notifications remain unread.

        Returns:
            List of notifications in FIFO order (oldest first)

        Note:
            This is a non-blocking operation. Returns immediately with
            whatever notifications are currently queued.
        """
        notifications = []

        try:
            retained = deque()

            while not self._queue.empty():
                try:
                    notification = self._queue.get_nowait()
                    if job_id is None or notification.job_id == job_id:
                        notifications.append(notification)
                    else:
                        retained.append(notification)
                except asyncio.QueueEmpty:
                    break

            while retained:
                self._queue.put_nowait(retained.popleft())

        except Exception:
            # Graceful failure - return what we have so far
            pass

        self._unread_count = self._queue.qsize()
        self._notify_change_listeners()

        return notifications

    async def publish_progress(self, progress_data: Dict[str, Any]) -> None:
        """Store live cron execution logs for follow-mode inspection."""
        try:
            log = CronProgressLog(
                job_id=progress_data.get("job_id", ""),
                job_name=progress_data.get("job_name", ""),
                level=progress_data.get("level", "info"),
                message=progress_data.get("message", ""),
                created_at=progress_data.get("created_at", datetime.now(pytz.UTC).isoformat()),
                terminal=bool(progress_data.get("terminal", False)),
            )
            if not log.job_id:
                return

            buffer = self._progress_logs.setdefault(
                log.job_id,
                deque(maxlen=self._progress_limit),
            )
            buffer.append(log)
        except Exception as e:
            logger.warning(f"Failed to store cron progress log: {e}")

    def get_progress_logs(self, job_id: str) -> List[CronProgressLog]:
        """Return a snapshot of the in-memory live log buffer for a cron job."""
        buffer = self._progress_logs.get(job_id)
        if not buffer:
            return []
        return list(buffer)

    def get_unread_count(self) -> int:
        """Return current unread notification count without draining the queue."""
        return max(0, self._unread_count)

    def add_change_listener(self, listener: Callable[[], None]) -> None:
        """Register a callback invoked when notification queue state changes."""
        if listener not in self._change_listeners:
            self._change_listeners.append(listener)

    def remove_change_listener(self, listener: Callable[[], None]) -> None:
        """Unregister a previously registered queue-change callback."""
        self._change_listeners = [item for item in self._change_listeners if item != listener]

    def _notify_change_listeners(self) -> None:
        for listener in list(self._change_listeners):
            try:
                listener()
            except Exception:
                continue
