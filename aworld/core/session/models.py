# coding: utf-8
# Copyright (c) 2026 inclusionAI.
"""Data contracts for the AWorld 1.0 execution entrance.

These records describe observations and requests, not live execution objects.
Input, output and event payload schemas belong to the configured executor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (self.COMPLETED, self.FAILED, self.CANCELLED)


class RunStopReason(str, Enum):
    END_TURN = "end_turn"
    ERROR = "error"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RunOptions:
    """Execution budget, measured from admission, using a monotonic clock.

    None means no deadline. Waiting or reconnecting cannot renew the budget.
    A client-side wait timeout is separate from this execution deadline.
    """

    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        value = self.timeout_seconds
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive, or None")


@dataclass(frozen=True)
class SessionSnapshot:
    """Control-plane snapshot; conversation state remains owned by the session.

    Metadata is shallowly copied and exposed read-only. Implementations must
    also detach mutable nested payloads from their owned state.
    """

    session_id: str
    context_policy_id: str
    context_policy_version: str
    active_run_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    session_id: str
    status: RunStatus


@dataclass(frozen=True)
class RunError:
    """A machine-readable execution error, independent of business success."""

    code: str
    message: str


@dataclass(frozen=True)
class RunResult:
    """A terminal execution outcome. COMPLETED does not assert task success.

    Partial output may be present on failed or cancelled executions too.
    """

    run_id: str
    session_id: str
    status: RunStatus
    stop_reason: RunStopReason
    output: object = None
    error: RunError | None = None

    def __post_init__(self) -> None:
        allowed = {
            RunStatus.COMPLETED: (RunStopReason.END_TURN,),
            RunStatus.FAILED: (RunStopReason.ERROR, RunStopReason.DEADLINE_EXCEEDED),
            RunStatus.CANCELLED: (RunStopReason.CANCELLED,),
        }
        if self.stop_reason not in allowed.get(self.status, ()):
            raise ValueError("RunResult requires a terminal status with a matching stop_reason")
        if self.status == RunStatus.FAILED and self.error is None:
            raise ValueError("A failed run requires an execution error")
        if self.status != RunStatus.FAILED and self.error is not None:
            raise ValueError("Only a failed run carries an execution error")


@dataclass(frozen=True)
class RunEvent:
    """A replayable event in one run's ordered stream.

    seq starts at 1 and increases without gaps within the run. Built-in types
    are run.started and run.finished; executor event types use another prefix.
    run.finished carries the committed RunResult as its data.
    """

    run_id: str
    session_id: str
    seq: int
    type: str
    data: object = None

    def __post_init__(self) -> None:
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 1:
            raise ValueError("Event seq must be a positive integer")
        if not isinstance(self.type, str) or not self.type.strip():
            raise ValueError("Event type must be a non-empty string")


# Execution observations use the context record; there is only one history type.
from aworld.core.context.simple import ContextEntry as SessionEntry
