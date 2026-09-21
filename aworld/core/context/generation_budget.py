"""Typed, task-generic deadlines for one Agent model-generation turn.

The provider request/response records remain the semantic truth.  These
contracts only explain why AWorld stopped waiting and whether a bounded
continuation was scheduled; they deliberately retain no raw response text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Callable

from aworld.core.exceptions import AWorldRuntimeException


class GenerationPhase(str, Enum):
    PRIMARY = "primary"
    ACTION_REPAIR = "action_repair"


class GenerationStopReason(str, Enum):
    """Why a provider wait ended before a normal response was assembled."""

    CALLER_CANCELLED = "caller_cancelled"
    PROVIDER_CANCELLED = "provider_cancelled"
    PROVIDER_TIMEOUT = "provider_timeout"
    IDLE_TIMEOUT = "idle_timeout"
    # Explicit stream-oriented alias for call sites that need that precision.
    STREAM_IDLE_TIMEOUT = "idle_timeout"
    ACTIVE_STREAM_OVER_BUDGET = "active_stream_over_budget"
    CALL_DEADLINE_EXCEEDED = "call_deadline_exceeded"
    ACTION_REPAIR_TIMEOUT = "action_repair_timeout"
    ACTION_REPAIR_EXHAUSTED = "action_repair_exhausted"


@dataclass(frozen=True, slots=True)
class GenerationBudgetPolicy:
    """Composable wall, liveness, and action budgets for one model turn.

    ``None`` disables an individual deadline.  The total deadline covers
    Tool discovery, provider attempts, backoff, and the optional repair.  The
    active Tool-free deadline begins only after the first meaningful stream
    chunk, so a slow time-to-first-byte is classified as idle/total rather
    than as active generation.
    """

    total_timeout_seconds: float | None = 360.0
    stream_idle_timeout_seconds: float | None = 120.0
    active_tool_free_timeout_seconds: float | None = 240.0
    action_repair_timeout_seconds: float | None = 90.0
    action_repair_max_output_tokens: int = 1024
    partial_response_context_chars: int = 8192
    action_repair_enabled: bool = True

    def __post_init__(self) -> None:
        for name in (
            "total_timeout_seconds",
            "stream_idle_timeout_seconds",
            "active_tool_free_timeout_seconds",
            "action_repair_timeout_seconds",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive or None")
        for name in (
            "action_repair_max_output_tokens",
            "partial_response_context_chars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.action_repair_enabled, bool):
            raise TypeError("action_repair_enabled must be boolean")


@dataclass(frozen=True, slots=True)
class GenerationDeadline:
    reason: GenerationStopReason
    expires_at: float


@dataclass(frozen=True, slots=True)
class GenerationBudgetReceipt:
    reason: GenerationStopReason
    phase: GenerationPhase
    elapsed_seconds: float
    phase_elapsed_seconds: float
    partial_response_available: bool
    partial_response_chars: int
    tool_call_count: int
    repair_attempted: bool
    repair_scheduled: bool = False

    SCHEMA_VERSION = "aworld.context.generation-budget/v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", GenerationStopReason(self.reason))
        object.__setattr__(self, "phase", GenerationPhase(self.phase))
        for name in ("elapsed_seconds", "phase_elapsed_seconds"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in ("partial_response_chars", "tool_call_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, str | float | int | bool]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "reason": self.reason.value,
            "phase": self.phase.value,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "phase_elapsed_seconds": round(self.phase_elapsed_seconds, 6),
            "partial_response_available": self.partial_response_available,
            "partial_response_chars": self.partial_response_chars,
            "tool_call_count": self.tool_call_count,
            "repair_attempted": self.repair_attempted,
            "repair_scheduled": self.repair_scheduled,
        }


class GenerationBudgetExceeded(AWorldRuntimeException):
    """Typed generation stop with an optional in-process partial response."""

    def __init__(
        self,
        receipt: GenerationBudgetReceipt,
        *,
        partial_response: Any = None,
        source_exception: BaseException | None = None,
    ) -> None:
        self.receipt = receipt
        self.reason = receipt.reason
        self.partial_response = partial_response
        self.source_exception = source_exception
        self.recorded = False
        super().__init__(
            f"{receipt.reason.value}: model generation stopped during "
            f"{receipt.phase.value} after {receipt.elapsed_seconds:.3f}s"
        )


class GenerationBudgetController:
    """Mutable runtime cursor over one immutable generation policy."""

    def __init__(
        self,
        policy: GenerationBudgetPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(policy, GenerationBudgetPolicy):
            raise TypeError("policy must be a GenerationBudgetPolicy")
        self.policy = policy
        self._clock = clock
        self.started_at = clock()
        self.phase = GenerationPhase.PRIMARY
        self.phase_started_at = self.started_at
        self.last_stream_activity_at = self.started_at
        self.first_stream_activity_at: float | None = None
        self.tool_call_observed = False
        self.action_available = False
        self.repair_attempted = False
        self.cancelled_partial_response: Any = None

    def now(self) -> float:
        return self._clock()

    def begin_stream(self, *, action_available: bool) -> None:
        now = self.now()
        self.phase_started_at = now
        self.last_stream_activity_at = now
        self.first_stream_activity_at = None
        self.tool_call_observed = False
        self.action_available = action_available

    def observe_stream_activity(
        self,
        *,
        meaningful_content_observed: bool,
        tool_call_observed: bool,
    ) -> None:
        now = self.now()
        self.last_stream_activity_at = now
        if meaningful_content_observed and self.first_stream_activity_at is None:
            self.first_stream_activity_at = now
        self.tool_call_observed = self.tool_call_observed or tool_call_observed

    def begin_action_repair(self) -> bool:
        if self.repair_attempted or not self.policy.action_repair_enabled:
            return False
        self.repair_attempted = True
        self.phase = GenerationPhase.ACTION_REPAIR
        self.phase_started_at = self.now()
        self.last_stream_activity_at = self.phase_started_at
        self.first_stream_activity_at = None
        self.tool_call_observed = False
        return True

    def _deadlines(self, *, streaming: bool) -> tuple[GenerationDeadline, ...]:
        deadlines: list[GenerationDeadline] = []
        if self.policy.total_timeout_seconds is not None:
            deadlines.append(
                GenerationDeadline(
                    GenerationStopReason.CALL_DEADLINE_EXCEEDED,
                    self.started_at + self.policy.total_timeout_seconds,
                )
            )
        if streaming and self.policy.stream_idle_timeout_seconds is not None:
            deadlines.append(
                GenerationDeadline(
                    GenerationStopReason.STREAM_IDLE_TIMEOUT,
                    self.last_stream_activity_at
                    + self.policy.stream_idle_timeout_seconds,
                )
            )
        if (
            streaming
            and self.phase is GenerationPhase.PRIMARY
            and self.action_available
            and self.first_stream_activity_at is not None
            and not self.tool_call_observed
            and self.policy.active_tool_free_timeout_seconds is not None
        ):
            deadlines.append(
                GenerationDeadline(
                    GenerationStopReason.ACTIVE_STREAM_OVER_BUDGET,
                    self.first_stream_activity_at
                    + self.policy.active_tool_free_timeout_seconds,
                )
            )
        if (
            self.phase is GenerationPhase.ACTION_REPAIR
            and self.policy.action_repair_timeout_seconds is not None
        ):
            deadlines.append(
                GenerationDeadline(
                    GenerationStopReason.ACTION_REPAIR_TIMEOUT,
                    self.phase_started_at
                    + self.policy.action_repair_timeout_seconds,
                )
            )
        return tuple(deadlines)

    def next_deadline(self, *, streaming: bool) -> GenerationDeadline | None:
        deadlines = self._deadlines(streaming=streaming)
        if not deadlines:
            return None
        # Enum order is not a hidden policy.  The reason string makes ties
        # deterministic for replay and testing.
        return min(deadlines, key=lambda item: (item.expires_at, item.reason.value))

    def remaining_seconds(self, *, streaming: bool) -> float | None:
        deadline = self.next_deadline(streaming=streaming)
        if deadline is None:
            return None
        return max(0.0, deadline.expires_at - self.now())

    def receipt(
        self,
        reason: GenerationStopReason,
        *,
        partial_response_chars: int = 0,
        tool_call_count: int = 0,
        repair_scheduled: bool = False,
    ) -> GenerationBudgetReceipt:
        now = self.now()
        return GenerationBudgetReceipt(
            reason=reason,
            phase=self.phase,
            elapsed_seconds=max(0.0, now - self.started_at),
            phase_elapsed_seconds=max(0.0, now - self.phase_started_at),
            partial_response_available=partial_response_chars > 0
            or tool_call_count > 0,
            partial_response_chars=partial_response_chars,
            tool_call_count=tool_call_count,
            repair_attempted=self.repair_attempted,
            repair_scheduled=repair_scheduled,
        )


__all__ = [
    "GenerationBudgetController",
    "GenerationBudgetExceeded",
    "GenerationBudgetPolicy",
    "GenerationBudgetReceipt",
    "GenerationDeadline",
    "GenerationPhase",
    "GenerationStopReason",
]
