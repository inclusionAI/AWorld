"""Typed state for AWorld's domain-independent long-horizon protocol.

The protocol intentionally stores measurements rather than task or tool text.
Plans and reviewer outputs remain model claims; observed progress enters through
the numeric and boolean fields on :class:`ExecutionProtocolEvent`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, ClassVar, Mapping


class ProtocolMode(str, Enum):
    OFF = "off"
    OBSERVE = "observe"
    GUIDE = "guide"


class ProtocolPhase(str, Enum):
    EXECUTE = "execute"
    FINALIZE = "finalize"
    REVIEW = "review"
    REPAIR = "repair"
    COMPLETE = "complete"


class EventKind(str, Enum):
    TOOL_OBSERVATION = "tool_observation"
    REPLAN_APPLIED = "replan_applied"
    CANDIDATE_FINAL = "candidate_final"
    REVIEW_RESULT = "review_result"


class ReviewOutcome(str, Enum):
    ACCEPT = "accept"
    REPAIR = "repair"
    UNKNOWN = "unknown"
    ERROR = "error"


class ControllerAction(str, Enum):
    CONTINUE = "continue"
    WOULD_REQUEST_REPLAN = "would_request_replan"
    REQUEST_REPLAN = "request_replan"
    WOULD_ENTER_FINALIZATION = "would_enter_finalization"
    ENTER_FINALIZATION = "enter_finalization"
    WOULD_REQUEST_FINAL_REVIEW = "would_request_final_review"
    REQUEST_FINAL_REVIEW = "request_final_review"
    WOULD_REQUEST_REPAIR = "would_request_repair"
    REQUEST_REPAIR = "request_repair"
    SUBMIT_CURRENT_RESULT = "submit_current_result"


class DecisionReason(str, Enum):
    DISABLED = "protocol_disabled"
    OBSERVATION_RECORDED = "observation_recorded"
    PROGRESS_OBSERVED = "progress_observed"
    STAGNATION_DETECTED = "stagnation_detected"
    REPLAN_LIMIT_REACHED = "replan_limit_reached"
    REPLAN_APPLIED = "replan_applied"
    FINALIZATION_RESERVE = "finalization_reserve"
    FINAL_REVIEW_REQUIRED = "final_review_required"
    FINAL_REVIEW_ALREADY_USED = "final_review_already_used"
    SHORT_TASK_BYPASS = "short_task_bypass"
    REVIEW_ACCEPTED = "review_accepted"
    REVIEW_REPAIR_REQUESTED = "review_repair_requested"
    REVIEW_UNCERTAIN = "review_uncertain"
    REVIEW_ERROR = "review_error"
    REPAIR_LIMIT_REACHED = "repair_limit_reached"
    CONTROLLER_ERROR = "controller_error"
    INVALID_EVENT = "invalid_event"


@dataclass(frozen=True, slots=True)
class ExecutionProtocolPolicy:
    """Validated limits for one task-scoped execution protocol."""

    SCHEMA_VERSION: ClassVar[str] = "aworld.execution-protocol-policy/v1"

    mode: ProtocolMode = ProtocolMode.OFF
    history_limit: int = 32
    activation_event_threshold: int = 6
    stagnation_event_threshold: int = 6
    repetition_threshold: int = 3
    low_information_gain_threshold: int = 3
    no_goal_progress_threshold: int = 6
    max_replans: int = 2
    max_final_reviews: int = 1
    max_repairs: int = 1
    finalization_reserve_seconds: float = 60.0
    final_review_timeout_seconds: float = 45.0

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ProtocolMode):
            try:
                object.__setattr__(self, "mode", ProtocolMode(self.mode))
            except (TypeError, ValueError) as exc:
                raise ValueError("mode must be off, observe, or guide") from exc
        for name in (
            "history_limit",
            "activation_event_threshold",
            "stagnation_event_threshold",
            "repetition_threshold",
            "low_information_gain_threshold",
            "no_goal_progress_threshold",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.history_limit > 256:
            raise ValueError("history_limit must not exceed 256")
        for name in ("max_replans", "max_final_reviews", "max_repairs"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.max_replans > 16:
            raise ValueError("max_replans must not exceed 16")
        if self.max_final_reviews > 1:
            raise ValueError("max_final_reviews must not exceed 1")
        if self.max_repairs > 1:
            raise ValueError("max_repairs must not exceed 1")
        reserve = self.finalization_reserve_seconds
        if (
            isinstance(reserve, bool)
            or not isinstance(reserve, (int, float))
            or reserve < 0
        ):
            raise ValueError("finalization_reserve_seconds must be non-negative")
        review_timeout = self.final_review_timeout_seconds
        if (
            isinstance(review_timeout, bool)
            or not isinstance(review_timeout, (int, float))
            or review_timeout <= 0
            or review_timeout > 3600
        ):
            raise ValueError(
                "final_review_timeout_seconds must be in the range (0, 3600]"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "mode": self.mode.value,
            "history_limit": self.history_limit,
            "activation_event_threshold": self.activation_event_threshold,
            "stagnation_event_threshold": self.stagnation_event_threshold,
            "repetition_threshold": self.repetition_threshold,
            "low_information_gain_threshold": self.low_information_gain_threshold,
            "no_goal_progress_threshold": self.no_goal_progress_threshold,
            "max_replans": self.max_replans,
            "max_final_reviews": self.max_final_reviews,
            "max_repairs": self.max_repairs,
            "finalization_reserve_seconds": self.finalization_reserve_seconds,
            "final_review_timeout_seconds": self.final_review_timeout_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionProtocolPolicy":
        if not isinstance(value, Mapping):
            raise ValueError("execution protocol policy must be a mapping")
        if value.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported execution protocol policy schema")
        return cls(
            mode=value.get("mode"),
            history_limit=value.get("history_limit"),
            # Additive v1 field: older serialized v1 policies use the safe
            # short-task bypass default when restored.
            activation_event_threshold=value.get("activation_event_threshold", 6),
            stagnation_event_threshold=value.get("stagnation_event_threshold"),
            repetition_threshold=value.get("repetition_threshold"),
            low_information_gain_threshold=value.get("low_information_gain_threshold"),
            no_goal_progress_threshold=value.get("no_goal_progress_threshold"),
            max_replans=value.get("max_replans"),
            max_final_reviews=value.get("max_final_reviews"),
            max_repairs=value.get("max_repairs"),
            finalization_reserve_seconds=value.get("finalization_reserve_seconds"),
            # Additive v1 field retained for compatibility with persisted
            # policies written before bounded final review timeouts existed.
            final_review_timeout_seconds=value.get(
                "final_review_timeout_seconds", 45.0
            ),
        )


@dataclass(frozen=True, slots=True)
class ProtocolScope:
    task_id: str | None
    task_epoch: str | int | None
    agent_id: str

    def __post_init__(self) -> None:
        if self.task_id is not None and not isinstance(self.task_id, str):
            raise ValueError("task_id must be a string or None")
        if self.task_epoch is not None and (
            isinstance(self.task_epoch, bool)
            or not isinstance(self.task_epoch, (str, int))
        ):
            raise ValueError("task_epoch must be a string, integer, or None")
        if (
            not isinstance(self.agent_id, str)
            or not self.agent_id
            or len(self.agent_id) > 256
        ):
            raise ValueError("agent_id must be a non-empty bounded string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_epoch": self.task_epoch,
            "agent_id": self.agent_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProtocolScope":
        return cls(
            task_id=value.get("task_id"),
            task_epoch=value.get("task_epoch"),
            agent_id=value.get("agent_id"),
        )


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class ExecutionProtocolEvent:
    """One bounded, text-free signal consumed by the controller."""

    kind: EventKind
    repetition_count: int = 0
    low_information_gain_count: int = 0
    no_goal_progress_count: int = 0
    goal_progress_observable: bool | None = None
    goal_progress: bool | None = None
    evidence_advanced: bool = False
    current_step: int = 0
    remaining_seconds: float | None = None
    operation_hash: str | None = None
    result_hash: str | None = None
    review_outcome: ReviewOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EventKind):
            try:
                object.__setattr__(self, "kind", EventKind(self.kind))
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported execution protocol event") from exc
        for name in (
            "repetition_count",
            "low_information_gain_count",
            "no_goal_progress_count",
            "current_step",
        ):
            _non_negative_int(getattr(self, name), name)
        for name in ("goal_progress_observable", "goal_progress"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean or None")
        if not isinstance(self.evidence_advanced, bool):
            raise ValueError("evidence_advanced must be a boolean")
        if self.remaining_seconds is not None:
            value = self.remaining_seconds
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError("remaining_seconds must be non-negative or None")
        for name in ("operation_hash", "result_hash"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or len(value) > 256):
                raise ValueError(f"{name} must be a bounded string or None")
        if self.review_outcome is not None and not isinstance(
            self.review_outcome, ReviewOutcome
        ):
            try:
                object.__setattr__(
                    self, "review_outcome", ReviewOutcome(self.review_outcome)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported review outcome") from exc
        if self.kind is EventKind.REVIEW_RESULT and self.review_outcome is None:
            raise ValueError("review_result requires review_outcome")
        if self.kind is not EventKind.REVIEW_RESULT and self.review_outcome is not None:
            raise ValueError("review_outcome is valid only for review_result")

    def to_record(self, sequence: int) -> "ProtocolEventRecord":
        return ProtocolEventRecord(
            sequence=sequence,
            kind=self.kind,
            repetition_count=self.repetition_count,
            low_information_gain_count=self.low_information_gain_count,
            no_goal_progress_count=self.no_goal_progress_count,
            goal_progress_observable=self.goal_progress_observable,
            goal_progress=self.goal_progress,
            evidence_advanced=self.evidence_advanced,
            current_step=self.current_step,
            remaining_seconds=self.remaining_seconds,
            operation_hash=self.operation_hash,
            result_hash=self.result_hash,
            review_outcome=self.review_outcome,
        )


@dataclass(frozen=True, slots=True)
class ProtocolEventRecord:
    sequence: int
    kind: EventKind
    repetition_count: int = 0
    low_information_gain_count: int = 0
    no_goal_progress_count: int = 0
    goal_progress_observable: bool | None = None
    goal_progress: bool | None = None
    evidence_advanced: bool = False
    current_step: int = 0
    remaining_seconds: float | None = None
    operation_hash: str | None = None
    result_hash: str | None = None
    review_outcome: ReviewOutcome | None = None

    def __post_init__(self) -> None:
        for name in (
            "sequence",
            "repetition_count",
            "low_information_gain_count",
            "no_goal_progress_count",
            "current_step",
        ):
            _non_negative_int(getattr(self, name), name)
        if not isinstance(self.kind, EventKind):
            raise ValueError("record kind must be EventKind")
        for name in ("goal_progress_observable", "goal_progress"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean or None")
        if not isinstance(self.evidence_advanced, bool):
            raise ValueError("evidence_advanced must be a boolean")
        if self.remaining_seconds is not None and (
            isinstance(self.remaining_seconds, bool)
            or not isinstance(self.remaining_seconds, (int, float))
            or self.remaining_seconds < 0
        ):
            raise ValueError("remaining_seconds must be non-negative or None")
        for name in ("operation_hash", "result_hash"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or len(value) > 256):
                raise ValueError(f"{name} must be a bounded string or None")
        if self.review_outcome is not None and not isinstance(
            self.review_outcome, ReviewOutcome
        ):
            raise ValueError("record review_outcome must be ReviewOutcome or None")
        if self.kind is EventKind.REVIEW_RESULT and self.review_outcome is None:
            raise ValueError("review_result record requires review_outcome")
        if self.kind is not EventKind.REVIEW_RESULT and self.review_outcome is not None:
            raise ValueError("review_outcome is valid only for review_result records")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind.value,
            "repetition_count": self.repetition_count,
            "low_information_gain_count": self.low_information_gain_count,
            "no_goal_progress_count": self.no_goal_progress_count,
            "goal_progress_observable": self.goal_progress_observable,
            "goal_progress": self.goal_progress,
            "evidence_advanced": self.evidence_advanced,
            "current_step": self.current_step,
            "remaining_seconds": self.remaining_seconds,
            "operation_hash": self.operation_hash,
            "result_hash": self.result_hash,
            "review_outcome": self.review_outcome.value
            if self.review_outcome
            else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProtocolEventRecord":
        outcome = value.get("review_outcome")
        return cls(
            sequence=_non_negative_int(value.get("sequence"), "sequence"),
            kind=EventKind(value.get("kind")),
            repetition_count=_non_negative_int(
                value.get("repetition_count", 0), "repetition_count"
            ),
            low_information_gain_count=_non_negative_int(
                value.get("low_information_gain_count", 0), "low_information_gain_count"
            ),
            no_goal_progress_count=_non_negative_int(
                value.get("no_goal_progress_count", 0), "no_goal_progress_count"
            ),
            goal_progress_observable=value.get("goal_progress_observable"),
            goal_progress=value.get("goal_progress"),
            evidence_advanced=value.get("evidence_advanced", False),
            current_step=_non_negative_int(
                value.get("current_step", 0), "current_step"
            ),
            remaining_seconds=value.get("remaining_seconds"),
            operation_hash=value.get("operation_hash"),
            result_hash=value.get("result_hash"),
            review_outcome=ReviewOutcome(outcome) if outcome is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ExecutionProtocolState:
    SCHEMA_VERSION: ClassVar[str] = "aworld.execution-protocol-state/v1"

    scope: ProtocolScope
    phase: ProtocolPhase = ProtocolPhase.EXECUTE
    revision: int = 0
    event_count: int = 0
    attempt_epoch: int = 0
    stagnant_observations: int = 0
    replan_count: int = 0
    last_replan_attempt_epoch: int | None = None
    final_review_count: int = 0
    repair_count: int = 0
    candidate_final_count: int = 0
    review_pending: bool = False
    finalization_entered: bool = False
    long_horizon_armed: bool = False
    history: tuple[ProtocolEventRecord, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ProtocolScope):
            raise ValueError("scope must be ProtocolScope")
        if not isinstance(self.phase, ProtocolPhase):
            try:
                object.__setattr__(self, "phase", ProtocolPhase(self.phase))
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported protocol phase") from exc
        for name in (
            "revision",
            "event_count",
            "attempt_epoch",
            "stagnant_observations",
            "replan_count",
            "final_review_count",
            "repair_count",
            "candidate_final_count",
        ):
            _non_negative_int(getattr(self, name), name)
        if self.last_replan_attempt_epoch is not None:
            _non_negative_int(
                self.last_replan_attempt_epoch, "last_replan_attempt_epoch"
            )
        if (
            not isinstance(self.review_pending, bool)
            or not isinstance(self.finalization_entered, bool)
            or not isinstance(self.long_horizon_armed, bool)
        ):
            raise ValueError("state flags must be booleans")
        if not isinstance(self.history, tuple) or not all(
            isinstance(item, ProtocolEventRecord) for item in self.history
        ):
            raise ValueError("history must contain protocol event records")
        if self.history:
            sequences = tuple(item.sequence for item in self.history)
            if sequences != tuple(range(sequences[0], sequences[0] + len(sequences))):
                raise ValueError("history sequence must be contiguous")
            if sequences[-1] != self.event_count:
                raise ValueError("history must end at event_count")

    @classmethod
    def initial(cls, scope: ProtocolScope) -> "ExecutionProtocolState":
        return cls(scope=scope)

    def append_event(
        self, event: ExecutionProtocolEvent, *, history_limit: int
    ) -> "ExecutionProtocolState":
        record = event.to_record(self.event_count + 1)
        return replace(
            self,
            revision=self.revision + 1,
            event_count=self.event_count + 1,
            history=(*self.history, record)[-history_limit:],
        )

    def bounded(self, history_limit: int) -> "ExecutionProtocolState":
        return replace(self, history=self.history[-history_limit:])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "scope": self.scope.to_dict(),
            "phase": self.phase.value,
            "revision": self.revision,
            "event_count": self.event_count,
            "attempt_epoch": self.attempt_epoch,
            "stagnant_observations": self.stagnant_observations,
            "replan_count": self.replan_count,
            "last_replan_attempt_epoch": self.last_replan_attempt_epoch,
            "final_review_count": self.final_review_count,
            "repair_count": self.repair_count,
            "candidate_final_count": self.candidate_final_count,
            "review_pending": self.review_pending,
            "finalization_entered": self.finalization_entered,
            "long_horizon_armed": self.long_horizon_armed,
            "history": [item.to_dict() for item in self.history],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionProtocolState":
        if value.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported execution protocol state schema")
        history = value.get("history", [])
        if not isinstance(history, list):
            raise ValueError("history must be a list")
        return cls(
            scope=ProtocolScope.from_dict(value.get("scope", {})),
            phase=ProtocolPhase(value.get("phase")),
            revision=_non_negative_int(value.get("revision"), "revision"),
            event_count=_non_negative_int(value.get("event_count"), "event_count"),
            attempt_epoch=_non_negative_int(
                value.get("attempt_epoch", 0), "attempt_epoch"
            ),
            stagnant_observations=_non_negative_int(
                value.get("stagnant_observations", 0), "stagnant_observations"
            ),
            replan_count=_non_negative_int(
                value.get("replan_count", 0), "replan_count"
            ),
            last_replan_attempt_epoch=value.get("last_replan_attempt_epoch"),
            final_review_count=_non_negative_int(
                value.get("final_review_count", 0), "final_review_count"
            ),
            repair_count=_non_negative_int(
                value.get("repair_count", 0), "repair_count"
            ),
            candidate_final_count=_non_negative_int(
                value.get("candidate_final_count", 0), "candidate_final_count"
            ),
            review_pending=value.get("review_pending", False),
            finalization_entered=value.get("finalization_entered", False),
            long_horizon_armed=value.get("long_horizon_armed", False),
            history=tuple(ProtocolEventRecord.from_dict(item) for item in history),
        )


@dataclass(frozen=True, slots=True)
class ControllerDecision:
    action: ControllerAction
    reason: DecisionReason


@dataclass(frozen=True, slots=True)
class ProtocolTransition:
    state: ExecutionProtocolState
    decision: ControllerDecision


__all__ = [
    "ControllerAction",
    "ControllerDecision",
    "DecisionReason",
    "EventKind",
    "ExecutionProtocolEvent",
    "ExecutionProtocolPolicy",
    "ExecutionProtocolState",
    "ProtocolEventRecord",
    "ProtocolMode",
    "ProtocolPhase",
    "ProtocolScope",
    "ProtocolTransition",
    "ReviewOutcome",
]
