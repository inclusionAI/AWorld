from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from .errors import AcpBusyError


class TurnController:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def start_turn(
        self,
        session_id: str,
        turn_coro: Awaitable[Any],
    ) -> asyncio.Task[Any]:
        existing = self._tasks.get(session_id)
        if existing is not None and existing.done():
            self._tasks.pop(session_id, None)
            existing = None

        if existing is not None:
            self._close_if_possible(turn_coro)
            raise AcpBusyError(session_id)

        task = asyncio.create_task(turn_coro)
        self._tasks[session_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(session_id, None))
        return task

    async def cancel_turn(self, session_id: str) -> str:
        task = self._tasks.get(session_id)
        if task is None or task.done():
            self._tasks.pop(session_id, None)
            return "noop"

        task.cancel()
        return "cancelled"

    @staticmethod
    def _close_if_possible(turn_coro: Awaitable[Any]) -> None:
        close = getattr(turn_coro, "close", None)
        if callable(close):
            close()
