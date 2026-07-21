from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any


_LOCAL_MEMORY: ContextVar[Any | None] = ContextVar(
    "aworld_local_memory",
    default=None,
)


class LocalMemoryScope:
    """Override MemoryFactory resolution within one local async task tree."""

    def __init__(self, memory: Any) -> None:
        if memory is None:
            raise ValueError("local memory must not be None")
        self.memory = memory
        self._token: Token[Any | None] | None = None

    async def __aenter__(self) -> Any:
        if self._token is not None:
            raise RuntimeError("local memory scope is already active")
        self._token = _LOCAL_MEMORY.set(self.memory)
        return self.memory

    async def __aexit__(self, exc_type, exc, tb) -> None:
        token = self._token
        if token is None:
            return
        self._token = None
        _LOCAL_MEMORY.reset(token)

    @classmethod
    def current(cls) -> Any | None:
        return _LOCAL_MEMORY.get()
