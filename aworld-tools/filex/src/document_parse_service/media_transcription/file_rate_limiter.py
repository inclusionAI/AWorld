"""Cross-process rate limiter for model calls in one FileX container."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import time
from pathlib import Path
from typing import Awaitable, Callable

from ..paths import FS_WORKSPACE_ROOT


class FileBackedRateLimiter:
    """Reserve request slots through a small file shared by FileX CLI processes."""

    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._state_dir = state_dir or FS_WORKSPACE_ROOT / ".filex_rate_limits"
        self._clock = clock or time.time
        self._sleep = sleep or asyncio.sleep

    async def acquire(self, *, key: str, requests_per_minute: int) -> int:
        """Wait for and reserve one request slot, returning cumulative wait milliseconds."""

        if requests_per_minute <= 0:
            return 0
        interval_seconds = 60.0 / requests_per_minute
        state_path = self._state_path(key)
        waited_seconds = 0.0
        while True:
            wait_seconds = self._reserve_or_get_wait(
                state_path=state_path,
                interval_seconds=interval_seconds,
            )
            if wait_seconds <= 0:
                return round(waited_seconds * 1000)
            await self._sleep(wait_seconds)
            waited_seconds += wait_seconds

    def _state_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._state_dir / f"{digest}.slot"

    def _reserve_or_get_wait(self, *, state_path: Path, interval_seconds: float) -> float:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with state_path.open("a+", encoding="utf-8") as state_file:
            fcntl.flock(state_file.fileno(), fcntl.LOCK_EX)
            try:
                state_file.seek(0)
                raw_next_slot = state_file.read().strip()
                try:
                    next_slot = float(raw_next_slot or 0)
                except ValueError:
                    next_slot = 0.0
                now = self._clock()
                wait_seconds = max(next_slot - now, 0.0)
                if wait_seconds > 0:
                    return wait_seconds
                state_file.seek(0)
                state_file.truncate()
                state_file.write(str(now + interval_seconds))
                state_file.flush()
                return 0.0
            finally:
                fcntl.flock(state_file.fileno(), fcntl.LOCK_UN)
