"""Typed state for AWorld's domain-independent long-horizon protocol.

The protocol stores bounded measurements plus an explicitly typed model-owned
plan checkpoint; it never stores raw task text or raw Tool output. Plan fields
remain claims, while observed progress enters through the evidence fields on
:class:`ExecutionProtocolEvent`.
"""

from __future__ import annotations

import math
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


class ExecutionHorizon(str, Enum):
    SHORT = "short"
    LONG = "long"


class PlanUpdateDecision(str, Enum):
    CONTINUE = "continue"
    REPLAN = "replan"


class CompletionAssessment(str, Enum):
    IN_PROGRESS = "in_progress"
    UNCERTAIN = "uncertain"
    CANDIDATE_READY = "candidate_ready"


class EventKind(str, Enum):
    MODEL_EXECUTION_PROFILE = "model_execution_profile"
    MODEL_PLAN_UPDATE = "model_plan_update"
    TOOL_OBSERVATION = "tool_observation"
    REPLAN_APPLIED = "replan_applied"
    CANDIDATE_FINAL = "candidate_final"
    REVIEW_RESULT = "review_result"


class ReviewOutcome(str, Enum):
    ACCEPT = "accept"
    REPAIR = "repair"
    UNCERTAIN = "uncertain"
    # Compatibility with v1 snapshots written before the public critic used
    # the precise ``uncertain`` spelling.
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
    STOP_INCOMPLETE = "stop_incomplete"


class DecisionReason(str, Enum):
    DISABLED = "protocol_disabled"
    OBSERVATION_RECORDED = "observation_recorded"
    PROGRESS_OBSERVED = "progress_observed"
    MODEL_LONG_HORIZON = "model_long_horizon"
    MODEL_SHORT_HORIZON = "model_short_horizon"
    MODEL_PROFILE_INSUFFICIENT = "model_profile_insufficient"
    MODEL_PROFILE_ALREADY_RECORDED = "model_profile_already_recorded"
    MODEL_PLAN_CHECKPOINT = "model_plan_checkpoint"
    MODEL_REPLAN_APPLIED = "model_replan_applied"
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
    ACCEPTANCE_EVIDENCE_MISSING = "acceptance_evidence_missing"
    CONTROLLER_ERROR = "controller_error"
    INVALID_EVENT = "invalid_event"


@dataclass(frozen=True, slots=True)
class ModelExecutionProfile:
    """Bounded, content-free model assessment from an ordinary Tool turn."""

    horizon: ExecutionHorizon
    confidence: float
    milestone_count: int
    expected_tool_actions: int
    verification_required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.horizon, ExecutionHorizon):
            try:
                object.__setattr__(self, "horizon", ExecutionHorizon(self.horizon))
            except (TypeError, ValueError) as exc:
                raise ValueError("horizon must be short or long") from exc
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise ValueError("confidence must be finite and in the range [0, 1]")
        object.__setattr__(self, "confidence", float(self.confidence))
        for name, maximum in (
            ("milestone_count", 64),
            ("expected_tool_actions", 1024),
        ):
            value = getattr(self, name)
            minimum = 1 if name == "milestone_count" else 0
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
                or value > maximum
            ):
                raise ValueError(
                    f"{name} must be an integer in the range [{minimum}, {maximum}]"
                )
        if not isinstance(self.verification_required, bool):
            raise ValueError("verification_required must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon.value,
            "confidence": self.confidence,
            "milestone_count": self.milestone_count,
            "expected_tool_actions": self.expected_tool_actions,
            "verification_required": self.verification_required,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelExecutionProfile":
        if not isinstance(value, Mapping):
            raise ValueError("model execution profile must be a mapping")
        expected = {
            "horizon",
            "confidence",
            "milestone_count",
            "expected_tool_actions",
            "verification_required",
        }
        unknown = set(value) - expected
        if unknown:
            raise ValueError("model execution profile contains unknown fields")
        return cls(
            horizon=value.get("horizon"),
            confidence=value.get("confidence"),
            milestone_count=value.get("milestone_count"),
            expected_tool_actions=value.get("expected_tool_actions"),
            verification_required=value.get("verification_required"),
        )


def _bounded_text_tuple(
    value: Any,
    *,
    name: str,
    maximum_items: int,
    maximum_chars: int,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum_items:
        raise ValueError(f"{name} must be a list with at most {maximum_items} items")
    normalized: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item.strip()) > maximum_chars
        ):
            raise ValueError(
                f"{name} items must be nonempty strings of at most {maximum_chars} characters"
            )
        normalized.append(item.strip())
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class ModelPlanUpdate:
    """Bounded model-owned checkpoint claim attached to a real Tool call.

    This record is intentionally separate from observed Tool evidence.  It
    captures the model's current milestone, verification intent, and selected
    candidate without allowing those claims to become completion evidence.
    """

    decision: PlanUpdateDecision
    horizon: ExecutionHorizon
    milestone: str
    next_action: str
    verification_plan: str
    completion_assessment: CompletionAssessment
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    retired_approaches: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    selected_candidate_id: str | None = None

    def __post_init__(self) -> None:
        for name, enum_type in (
            ("decision", PlanUpdateDecision),
            ("horizon", ExecutionHorizon),
            ("completion_assessment", CompletionAssessment),
        ):
            value = getattr(self, name)
            if not isinstance(value, enum_type):
                try:
                    object.__setattr__(self, name, enum_type(value))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"unsupported {name}") from exc
        for name, maximum in (
            ("milestone", 512),
            ("next_action", 1024),
            ("verification_plan", 1024),
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
                raise ValueError(
                    f"{name} must be a nonempty string of at most {maximum} characters"
                )
            object.__setattr__(self, name, value.strip())
        for name, maximum_items, maximum_chars in (
            ("assumptions", 8, 512),
            ("retired_approaches", 8, 512),
            ("evidence_refs", 16, 256),
        ):
            object.__setattr__(
                self,
                name,
                _bounded_text_tuple(
                    getattr(self, name),
                    name=name,
                    maximum_items=maximum_items,
                    maximum_chars=maximum_chars,
                ),
            )
        candidate_id = self.selected_candidate_id
        if candidate_id is not None:
            if (
                not isinstance(candidate_id, str)
                or not candidate_id.strip()
                or len(candidate_id.strip()) > 128
            ):
                raise ValueError(
                    "selected_candidate_id must be null or a nonempty string of at most 128 characters"
                )
            object.__setattr__(self, "selected_candidate_id", candidate_id.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "horizon": self.horizon.value,
            "milestone": self.milestone,
            "next_action": self.next_action,
            "verification_plan": self.verification_plan,
            "completion_assessment": self.completion_assessment.value,
            "assumptions": list(self.assumptions),
            "retired_approaches": list(self.retired_approaches),
            "evidence_refs": list(self.evidence_refs),
            "selected_candidate_id": self.selected_candidate_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelPlanUpdate":
        if not isinstance(value, Mapping):
            raise ValueError("model plan update must be a mapping")
        expected = {
            "decision",
            "horizon",
            "milestone",
            "next_action",
            "verification_plan",
            "completion_assessment",
            "assumptions",
            "retired_approaches",
            "evidence_refs",
            "selected_candidate_id",
        }
        unknown = set(value) - expected
        if unknown:
            raise ValueError("model plan update contains unknown fields")
        if set(value) != expected:
            raise ValueError("model plan update is missing required fields")
        return cls(
            decision=value.get("decision"),
            horizon=value.get("horizon"),
            milestone=value.get("milestone"),
            next_action=value.get("next_action"),
            verification_plan=value.get("verification_plan"),
            completion_assessment=value.get("completion_assessment"),
            assumptions=value.get("assumptions"),
            retired_approaches=value.get("retired_approaches"),
            evidence_refs=value.get("evidence_refs"),
            selected_candidate_id=value.get("selected_candidate_id"),
        )


@dataclass(frozen=True, slots=True)
class ExecutionProtocolPolicy:
    """Validated limits for one task-scoped execution protocol."""

    SCHEMA_VERSION: ClassVar[str] = "aworld.execution-protocol-policy/v1"

    mode: ProtocolMode = ProtocolMode.OFF
    # Some supervised runtimes want every candidate completion to receive one
    # model-owned review, even when the task did not emit enough Tool events to
    # satisfy the generic long-horizon activation heuristic.  The controller
    # still makes no semantic judgement: the review model accepts by returning
    # a final response or requests repair by using Tools.
    review_unarmed_candidates: bool = False
    independent_acceptance_enabled: bool = True
    semantic_progress_enabled: bool = True
    history_limit: int = 32
    # Legacy compatibility knobs retained in the v1 wire format. They no
    # longer classify or arm a task: horizon ownership belongs to the model's
    # execution profile and later plan updates.
    activation_event_threshold: int = 6
    model_activation_confidence_threshold: float = 0.7
    model_activation_min_milestones: int = 2
    model_activation_min_tool_actions: int = 6
    stagnation_event_threshold: int = 6
    repetition_threshold: int = 3
    low_information_gain_threshold: int = 3
    no_goal_progress_threshold: int = 6
    # Semantic loop counts are model-owned. ``None`` leaves replanning,
    # reviewing, and repair bounded by the caller's task deadline instead of a
    # framework-selected number of attempts. Explicit callers may still set a
    # finite compatibility limit for controlled experiments.
    max_replans: int | None = None
    max_final_reviews: int | None = None
    max_repairs: int | None = None
    finalization_reserve_seconds: float = 60.0
    # ``None`` lets review consume the caller's remaining task deadline. A
    # finite value is retained only for explicit compatibility experiments.
    final_review_timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ProtocolMode):
            try:
                object.__setattr__(self, "mode", ProtocolMode(self.mode))
            except (TypeError, ValueError) as exc:
                raise ValueError("mode must be off, observe, or guide") from exc
        for name in (
            "review_unarmed_candidates",
            "independent_acceptance_enabled",
            "semantic_progress_enabled",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        for name in (
            "history_limit",
            "activation_event_threshold",
            "model_activation_min_milestones",
            "model_activation_min_tool_actions",
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
        if self.model_activation_min_milestones > 64:
            raise ValueError("model_activation_min_milestones must not exceed 64")
        if self.model_activation_min_tool_actions > 1024:
            raise ValueError("model_activation_min_tool_actions must not exceed 1024")
        confidence = self.model_activation_confidence_threshold
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise ValueError(
                "model_activation_confidence_threshold must be finite and in "
                "the range [0, 1]"
            )
        for name in ("max_replans", "max_final_reviews", "max_repairs"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        reserve = self.finalization_reserve_seconds
        if (
            isinstance(reserve, bool)
            or not isinstance(reserve, (int, float))
            or reserve < 0
        ):
            raise ValueError("finalization_reserve_seconds must be non-negative")
        review_timeout = self.final_review_timeout_seconds
        if review_timeout is not None and (
            isinstance(review_timeout, bool)
            or not isinstance(review_timeout, (int, float))
            or review_timeout <= 0
            or review_timeout > 86_400
        ):
            raise ValueError(
                "final_review_timeout_seconds must be None or in the range (0, 86400]"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "mode": self.mode.value,
            "review_unarmed_candidates": self.review_unarmed_candidates,
            "independent_acceptance_enabled": self.independent_acceptance_enabled,
            "semantic_progress_enabled": self.semantic_progress_enabled,
            "history_limit": self.history_limit,
            "activation_event_threshold": self.activation_event_threshold,
            "model_activation_confidence_threshold": (
                self.model_activation_confidence_threshold
            ),
            "model_activation_min_milestones": self.model_activation_min_milestones,
            "model_activation_min_tool_actions": (
                self.model_activation_min_tool_actions
            ),
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
            # Additive v1 field.  Older policies retain the short-task bypass.
            review_unarmed_candidates=value.get("review_unarmed_candidates", False),
            independent_acceptance_enabled=value.get(
                "independent_acceptance_enabled", True
            ),
            semantic_progress_enabled=value.get("semantic_progress_enabled", True),
            history_limit=value.get("history_limit"),
            # Additive v1 field: older serialized v1 policies use the safe
            # short-task bypass default when restored.
            activation_event_threshold=value.get("activation_event_threshold", 6),
            model_activation_confidence_threshold=value.get(
                "model_activation_confidence_threshold", 0.7
            ),
            model_activation_min_milestones=value.get(
                "model_activation_min_milestones", 2
            ),
            model_activation_min_tool_actions=value.get(
                "model_activation_min_tool_actions", 6
            ),
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
                "final_review_timeout_seconds"
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
    """One bounded signal consumed by the controller."""

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
    model_execution_profile: ModelExecutionProfile | None = None
    model_plan_update: ModelPlanUpdate | None = None

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
        if (
            self.kind is EventKind.MODEL_EXECUTION_PROFILE
            and self.model_execution_profile is None
        ):
            raise ValueError(
                "model_execution_profile event requires model_execution_profile"
            )
        if (
            self.kind is not EventKind.MODEL_EXECUTION_PROFILE
            and self.model_execution_profile is not None
        ):
            raise ValueError(
                "model_execution_profile is valid only for model_execution_profile events"
            )
        if self.model_execution_profile is not None and not isinstance(
            self.model_execution_profile, ModelExecutionProfile
        ):
            raise ValueError(
                "model_execution_profile must be ModelExecutionProfile or None"
            )
        if self.kind is EventKind.MODEL_PLAN_UPDATE and self.model_plan_update is None:
            raise ValueError("model_plan_update event requires model_plan_update")
        if self.kind is not EventKind.MODEL_PLAN_UPDATE and self.model_plan_update is not None:
            raise ValueError("model_plan_update is valid only for model_plan_update events")
        if self.model_plan_update is not None and not isinstance(
            self.model_plan_update, ModelPlanUpdate
        ):
            raise ValueError("model_plan_update must be ModelPlanUpdate or None")

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
            model_execution_profile=self.model_execution_profile,
            model_plan_update=self.model_plan_update,
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
    model_execution_profile: ModelExecutionProfile | None = None
    model_plan_update: ModelPlanUpdate | None = None

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
        if (
            self.kind is EventKind.MODEL_EXECUTION_PROFILE
            and self.model_execution_profile is None
        ):
            raise ValueError(
                "model_execution_profile record requires model_execution_profile"
            )
        if (
            self.kind is not EventKind.MODEL_EXECUTION_PROFILE
            and self.model_execution_profile is not None
        ):
            raise ValueError(
                "model_execution_profile is valid only for model profile records"
            )
        if self.model_execution_profile is not None and not isinstance(
            self.model_execution_profile, ModelExecutionProfile
        ):
            raise ValueError("record model_execution_profile has invalid type")
        if self.kind is EventKind.MODEL_PLAN_UPDATE and self.model_plan_update is None:
            raise ValueError("model_plan_update record requires model_plan_update")
        if self.kind is not EventKind.MODEL_PLAN_UPDATE and self.model_plan_update is not None:
            raise ValueError("model_plan_update is valid only for model plan update records")
        if self.model_plan_update is not None and not isinstance(
            self.model_plan_update, ModelPlanUpdate
        ):
            raise ValueError("record model_plan_update has invalid type")

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
            "model_execution_profile": (
                self.model_execution_profile.to_dict()
                if self.model_execution_profile is not None
                else None
            ),
            "model_plan_update": (
                self.model_plan_update.to_dict()
                if self.model_plan_update is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProtocolEventRecord":
        outcome = value.get("review_outcome")
        profile = value.get("model_execution_profile")
        plan_update = value.get("model_plan_update")
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
            model_execution_profile=(
                ModelExecutionProfile.from_mapping(profile)
                if profile is not None
                else None
            ),
            model_plan_update=(
                ModelPlanUpdate.from_mapping(plan_update)
                if plan_update is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ExecutionProtocolState:
    SCHEMA_VERSION: ClassVar[str] = "aworld.execution-protocol-state/v1"

    scope: ProtocolScope
    phase: ProtocolPhase = ProtocolPhase.EXECUTE
    revision: int = 0
    event_count: int = 0
    tool_observation_count: int = 0
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
    acceptance_confirmed: bool = False
    model_execution_profile: ModelExecutionProfile | None = None
    model_plan_update: ModelPlanUpdate | None = None
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
            "tool_observation_count",
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
            or not isinstance(self.acceptance_confirmed, bool)
        ):
            raise ValueError("state flags must be booleans")
        if not isinstance(self.history, tuple) or not all(
            isinstance(item, ProtocolEventRecord) for item in self.history
        ):
            raise ValueError("history must contain protocol event records")
        if self.model_execution_profile is not None and not isinstance(
            self.model_execution_profile, ModelExecutionProfile
        ):
            raise ValueError(
                "model_execution_profile must be ModelExecutionProfile or None"
            )
        if self.model_plan_update is not None and not isinstance(
            self.model_plan_update, ModelPlanUpdate
        ):
            raise ValueError("model_plan_update must be ModelPlanUpdate or None")
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
            tool_observation_count=(
                self.tool_observation_count + 1
                if event.kind is EventKind.TOOL_OBSERVATION
                else self.tool_observation_count
            ),
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
            "tool_observation_count": self.tool_observation_count,
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
            "acceptance_confirmed": self.acceptance_confirmed,
            "model_execution_profile": (
                self.model_execution_profile.to_dict()
                if self.model_execution_profile is not None
                else None
            ),
            "model_plan_update": (
                self.model_plan_update.to_dict()
                if self.model_plan_update is not None
                else None
            ),
            "history": [item.to_dict() for item in self.history],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionProtocolState":
        if value.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported execution protocol state schema")
        history = value.get("history", [])
        if not isinstance(history, list):
            raise ValueError("history must be a list")
        profile = value.get("model_execution_profile")
        plan_update = value.get("model_plan_update")
        event_count = _non_negative_int(value.get("event_count"), "event_count")
        return cls(
            scope=ProtocolScope.from_dict(value.get("scope", {})),
            phase=ProtocolPhase(value.get("phase")),
            revision=_non_negative_int(value.get("revision"), "revision"),
            event_count=event_count,
            tool_observation_count=_non_negative_int(
                value.get("tool_observation_count", event_count),
                "tool_observation_count",
            ),
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
            acceptance_confirmed=value.get("acceptance_confirmed", False),
            model_execution_profile=(
                ModelExecutionProfile.from_mapping(profile)
                if profile is not None
                else None
            ),
            model_plan_update=(
                ModelPlanUpdate.from_mapping(plan_update)
                if plan_update is not None
                else None
            ),
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
    "CompletionAssessment",
    "ControllerAction",
    "ControllerDecision",
    "DecisionReason",
    "EventKind",
    "ExecutionProtocolEvent",
    "ExecutionHorizon",
    "ExecutionProtocolPolicy",
    "ExecutionProtocolState",
    "ModelExecutionProfile",
    "ModelPlanUpdate",
    "PlanUpdateDecision",
    "ProtocolEventRecord",
    "ProtocolMode",
    "ProtocolPhase",
    "ProtocolScope",
    "ProtocolTransition",
    "ReviewOutcome",
]
