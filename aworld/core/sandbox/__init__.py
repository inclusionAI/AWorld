"""Physical execution carriers for tools. No global setup or auto installation."""

from typing import Protocol
from .local import LocalSandbox


class Sandbox(Protocol):
    async def read(self, path: str, offset: int, limit: int) -> object: ...
    async def write(self, path: str, content: str) -> object: ...
    async def bash(self, command: str, timeout: float) -> object: ...


__all__ = ["Sandbox", "LocalSandbox"]
