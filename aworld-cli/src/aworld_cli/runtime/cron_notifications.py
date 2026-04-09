# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron task notification center for TUI.

Provides lightweight in-memory notification queue for cron task completions.
Notifications are published by scheduler and drained by console for display.
"""
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional, List
import pytz


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
        created_at: Notification creation timestamp (ISO 8601)
        next_run_at: Next scheduled run time if job is recurring
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str = ""
    job_name: str = ""
    status: Literal["ok", "error", "timeout"] = "ok"
    summary: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(pytz.UTC).isoformat())
    next_run_at: Optional[str] = None


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

        except Exception:
            # Graceful failure - notification system should never crash scheduler
            pass

    async def drain(self) -> List[CronNotification]:
        """
        Drain all pending notifications from the queue.

        Returns:
            List of notifications in FIFO order (oldest first)

        Note:
            This is a non-blocking operation. Returns immediately with
            whatever notifications are currently queued.
        """
        notifications = []

        try:
            # Drain all pending notifications (non-blocking)
            while not self._queue.empty():
                try:
                    notification = self._queue.get_nowait()
                    notifications.append(notification)
                except asyncio.QueueEmpty:
                    break

        except Exception:
            # Graceful failure - return what we have so far
            pass

        return notifications
