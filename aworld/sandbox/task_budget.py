"""Framework-owned snapshots and leases for one Tool execution.

The payload emitted by this module is hidden from model-visible Tool schemas.
It is deliberately small and content-free so it can cross local, Docker and
MCP transports without copying the full :class:`~aworld.core.task.Task`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import Any, Mapping


TASK_BUDGET_AUTHORITY = "aworld_task"
TASK_BUDGET_SCHEMA = "aworld.task-budget/v1"
DEFAULT_COMPLETION_RESERVE_SECONDS = 15.0


class ToolLeaseStage(str, Enum):
    """Generic execution stages that may tighten a single Tool lease."""

    EXECUTE = "execute"
    CONVERGENCE = "convergence"
    DEADLINE = "deadline"


@dataclass(frozen=True, slots=True)
class FrameworkTaskBudget:
    bounded: bool = False
    stage: ToolLeaseStage = ToolLeaseStage.EXECUTE
    deadline_epoch_seconds: float | None = None
    remaining_seconds: float | None = None
    completion_reserve_seconds: float = 0.0
    captured_at_epoch_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stage, ToolLeaseStage):
            object.__setattr__(self, "stage", ToolLeaseStage(self.stage))
        if not isinstance(self.bounded, bool):
            raise ValueError("bounded must be a boolean")
        for name in (
            "deadline_epoch_seconds",
            "remaining_seconds",
            "completion_reserve_seconds",
            "captured_at_epoch_seconds",
        ):
            value = getattr(self, name)
            if value is None and name != "completion_reserve_seconds":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number")
            object.__setattr__(self, name, float(value))
        if self.bounded and (
            self.deadline_epoch_seconds is None or self.remaining_seconds is None
        ):
            raise ValueError("bounded task budget requires deadline and remaining time")

    def to_hidden_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "authority": TASK_BUDGET_AUTHORITY,
            "schema_version": TASK_BUDGET_SCHEMA,
            "bounded": self.bounded,
            "stage": self.stage.value,
        }
        if self.bounded:
            payload.update(
                {
                    "deadline_epoch_seconds": self.deadline_epoch_seconds,
                    "remaining_seconds": self.remaining_seconds,
                    "completion_reserve_seconds": self.completion_reserve_seconds,
                    "captured_at_epoch_seconds": self.captured_at_epoch_seconds,
                }
            )
        return payload

    @classmethod
    def from_hidden_dict(cls, value: Any) -> "FrameworkTaskBudget | None":
        """Decode only a framework-authored hidden payload.

        ``None`` means no authoritative payload reached this boundary and lets
        legacy direct Terminal calls retain their process-environment policy.
        A valid unbounded sentinel is different: it explicitly prevents a
        caller or stale Sandbox value from inventing a deadline.
        """

        if not isinstance(value, Mapping):
            return None
        if (
            value.get("authority") != TASK_BUDGET_AUTHORITY
            or value.get("schema_version") != TASK_BUDGET_SCHEMA
            or not isinstance(value.get("bounded"), bool)
        ):
            return None
        try:
            return cls(
                bounded=value["bounded"],
                stage=value.get("stage", ToolLeaseStage.EXECUTE.value),
                deadline_epoch_seconds=value.get("deadline_epoch_seconds"),
                remaining_seconds=value.get("remaining_seconds"),
                completion_reserve_seconds=value.get(
                    "completion_reserve_seconds", 0.0
                ),
                captured_at_epoch_seconds=value.get("captured_at_epoch_seconds"),
            )
        except (TypeError, ValueError):
            return None

    def remaining_at(self, now_epoch: float | None = None) -> float | None:
        if not self.bounded:
            return None
        now = time.time() if now_epoch is None else float(now_epoch)
        epoch_remaining = max(0.0, float(self.deadline_epoch_seconds) - now)
        snapshot_remaining = float(self.remaining_seconds)
        if self.captured_at_epoch_seconds is not None:
            elapsed = max(0.0, now - float(self.captured_at_epoch_seconds))
            snapshot_remaining = max(0.0, snapshot_remaining - elapsed)
        # The snapshot side never increases when wall time moves backwards.
        return min(epoch_remaining, snapshot_remaining)


def _finite_non_negative(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _task_completion_reserve(task: Any) -> float:
    current = task
    seen: set[int] = set()
    reserve: float | None = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidate = _finite_non_negative(
            getattr(current, "completion_reserve_seconds", None)
        )
        if candidate is not None:
            reserve = candidate if reserve is None else max(reserve, candidate)
        current = getattr(current, "parent_task", None)
    return (
        DEFAULT_COMPLETION_RESERVE_SECONDS if reserve is None else reserve
    )


def snapshot_task_budget(
    task: Any,
    *,
    stage: ToolLeaseStage = ToolLeaseStage.EXECUTE,
    now_epoch: float | None = None,
) -> FrameworkTaskBudget:
    """Capture a monotonic-tight Task budget without serializing Task state."""

    if task is None:
        return FrameworkTaskBudget(stage=stage)
    try:
        binder = getattr(task, "bind_deadline", None)
        if callable(binder):
            binder()
        remaining_getter = getattr(task, "remaining_seconds", None)
        remaining = remaining_getter() if callable(remaining_getter) else None
        remaining = _finite_non_negative(remaining)
        declared_deadline = _finite_non_negative(
            getattr(task, "deadline_epoch_seconds", None)
        )
    except Exception:
        return FrameworkTaskBudget(stage=stage)
    if remaining is None and declared_deadline is None:
        return FrameworkTaskBudget(stage=stage)
    now = time.time() if now_epoch is None else float(now_epoch)
    if remaining is None:
        remaining = max(0.0, float(declared_deadline) - now)
    snapshot_deadline = now + remaining
    if declared_deadline is not None:
        snapshot_deadline = min(declared_deadline, snapshot_deadline)
        remaining = min(remaining, max(0.0, declared_deadline - now))
    return FrameworkTaskBudget(
        bounded=True,
        stage=stage,
        deadline_epoch_seconds=snapshot_deadline,
        remaining_seconds=remaining,
        completion_reserve_seconds=_task_completion_reserve(task),
        captured_at_epoch_seconds=now,
    )


@dataclass(frozen=True, slots=True)
class ToolLeaseDecision:
    requested_seconds: float
    policy_seconds: float
    effective_seconds: float
    remaining_task_seconds: float | None
    limited_by: str | None
    policy_override: str | None = None


def resolve_tool_lease(
    requested_seconds: float,
    *,
    budget: FrameworkTaskBudget | None = None,
    maximum_seconds: float,
    policy_seconds: float | None = None,
    policy_override: str | None = None,
    now_epoch: float | None = None,
    constrained_fraction: float = 0.25,
    constrained_floor_seconds: float = 15.0,
    explicit_fraction_policy: bool = False,
) -> ToolLeaseDecision:
    """Resolve one authoritative lease for execution and transport boundaries."""

    requested = float(requested_seconds)
    policy = requested if policy_seconds is None else float(policy_seconds)
    maximum = float(maximum_seconds)
    for name, value in (
        ("requested timeout", requested),
        ("policy timeout", policy),
        ("maximum timeout", maximum),
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number")
    effective = min(policy, maximum)
    limited_by = "tool_maximum" if effective < policy else None
    if policy < requested and effective == policy:
        limited_by = policy_override or "tool_timeout_policy"

    remaining = budget.remaining_at(now_epoch) if budget is not None else None
    if remaining is not None:
        available = max(0.0, remaining - budget.completion_reserve_seconds)
        if available <= 0:
            return ToolLeaseDecision(
                requested,
                policy,
                0.0,
                remaining,
                "task_deadline_exhausted",
                policy_override,
            )
        if available < effective:
            effective = available
            limited_by = "task_deadline"
        constrained = budget.stage in {
            ToolLeaseStage.CONVERGENCE,
            ToolLeaseStage.DEADLINE,
        }
        if constrained or explicit_fraction_policy:
            fraction = float(constrained_fraction)
            if not math.isfinite(fraction) or not 0 < fraction <= 1:
                fraction = 0.25
            floor = max(0.0, float(constrained_floor_seconds))
            lease_cap = min(available, max(floor, available * fraction))
            if lease_cap < effective:
                effective = lease_cap
                limited_by = "task_lease"
    return ToolLeaseDecision(
        requested,
        policy,
        effective,
        remaining,
        limited_by,
        policy_override,
    )


__all__ = [
    "FrameworkTaskBudget",
    "DEFAULT_COMPLETION_RESERVE_SECONDS",
    "TASK_BUDGET_AUTHORITY",
    "TASK_BUDGET_SCHEMA",
    "ToolLeaseDecision",
    "ToolLeaseStage",
    "resolve_tool_lease",
    "snapshot_task_budget",
]
