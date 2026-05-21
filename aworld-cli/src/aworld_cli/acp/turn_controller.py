from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal

from .errors import AcpBusyError


TurnStatus = Literal["running", "paused"]


@dataclass
class TurnRecord:
    status: TurnStatus
    task: asyncio.Task[Any] | None = None


class TurnController:
    def __init__(self) -> None:
        self._records: dict[str, TurnRecord] = {}

    async def start_turn(
        self,
        session_id: str,
        turn_coro: Awaitable[Any],
    ) -> asyncio.Task[Any]:
        record = self._records.get(session_id)
        if record is not None and record.task is not None and record.task.done():
            if record.status == "running":
                self._records.pop(session_id, None)
                record = None

        if record is not None:
            self._close_if_possible(turn_coro)
            raise AcpBusyError(session_id)

        task = asyncio.create_task(turn_coro)
        self._records[session_id] = TurnRecord(status="running", task=task)
        task.add_done_callback(lambda finished: self._cleanup_finished_task(session_id, finished))
        return task

    async def resume_turn(
        self,
        session_id: str,
        turn_coro: Awaitable[Any],
    ) -> asyncio.Task[Any]:
        record = self._records.get(session_id)
        if record is None or record.status != "paused":
            self._close_if_possible(turn_coro)
            raise AcpBusyError(session_id)

        task = asyncio.create_task(turn_coro)
        record.status = "running"
        record.task = task
        task.add_done_callback(lambda finished: self._cleanup_finished_task(session_id, finished))
        return task

    def pause_turn(self, session_id: str) -> None:
        record = self._records.get(session_id)
        if record is None:
            return
        record.status = "paused"
        record.task = None

    async def cancel_turn(self, session_id: str) -> str:
        record = self._records.get(session_id)
        if record is None:
            return "noop"
        if record.status == "paused":
            self._records.pop(session_id, None)
            return "cancelled"

        task = record.task
        if task is None or task.done():
            self._records.pop(session_id, None)
            return "noop"

        task.cancel()
        return "cancelled"

    def has_active_turn(self, session_id: str) -> bool:
        record = self._records.get(session_id)
        if record is None:
            return False
        if record.status == "paused":
            return True
        task = record.task
        if task is None:
            return False
        if task.done():
            self._records.pop(session_id, None)
            return False
        return True

    def is_paused(self, session_id: str) -> bool:
        record = self._records.get(session_id)
        return bool(record is not None and record.status == "paused")

    def _cleanup_finished_task(self, session_id: str, task: asyncio.Task[Any]) -> None:
        record = self._records.get(session_id)
        if record is None:
            return
        if record.status == "paused":
            return
        if record.task is task:
            self._records.pop(session_id, None)

    @staticmethod
    def _close_if_possible(turn_coro: Awaitable[Any]) -> None:
        close = getattr(turn_coro, "close", None)
        if callable(close):
            close()
