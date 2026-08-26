"""Execution-neutral protocol and result records for cloud workers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from aworld.cloud.models import ExecutorId, Run, RunFile, Workspace, as_utc
from aworld.cloud.settings import NetworkPolicy, ResourceLimits


class ExecutorStatus(str, Enum):
    RUNNING = "running"
    EXITED = "exited"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ExecutorHandle:
    executor_id: ExecutorId


@dataclass(frozen=True)
class ExecutorEvent:
    event_type: str
    payload: Mapping[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(self, "created_at", as_utc(self.created_at))


@dataclass(frozen=True)
class ExecutorRequest:
    workspace: Workspace
    run: Run
    output_directory: Path
    runtime_user: str
    resources: ResourceLimits
    network: NetworkPolicy


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    finished_at: datetime
    error_code: str | None = None
    error_message: str | None = None
    files: tuple[RunFile, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "finished_at", as_utc(self.finished_at))
        object.__setattr__(self, "files", tuple(self.files))


@dataclass(frozen=True)
class ExecutorInspection:
    status: ExecutorStatus
    result: ExecutionResult | None = None
    reattachable: bool = False


EventCallback = Callable[[ExecutorEvent], Awaitable[None]]


@runtime_checkable
class CloudExecutor(Protocol):
    """Adapter contract implemented by fake, Docker, and future executors."""

    async def start(self, request: ExecutorRequest) -> ExecutorHandle: ...

    async def wait(
        self,
        handle: ExecutorHandle,
        *,
        on_event: EventCallback,
    ) -> ExecutionResult: ...

    async def inspect(self, executor_id: ExecutorId) -> ExecutorInspection: ...

    async def cancel(
        self,
        executor_id: ExecutorId,
        *,
        grace_period: timedelta,
    ) -> None: ...
