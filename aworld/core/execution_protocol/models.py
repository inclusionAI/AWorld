"""Typed state for AWorld's domain-independent long-horizon protocol.

The protocol stores bounded measurements plus an explicitly typed model-owned
plan checkpoint; it never stores raw task text or raw Tool output. Plan fields
remain claims, while observed progress enters through the evidence fields on
:class:`ExecutionProtocolEvent`.
"""

from __future__ import annotations

import math
import hashlib
import json
import re
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
    UNKNOWN = "unknown"
    SHORT = "short"
    LONG = "long"


class PlanUpdateDecision(str, Enum):
    CONTINUE = "continue"
    REPLAN = "replan"


class CompletionAssessment(str, Enum):
    IN_PROGRESS = "in_progress"
    UNCERTAIN = "uncertain"
    CANDIDATE_READY = "candidate_ready"


class DeliveryIntent(str, Enum):
    """Model-owned delivery choice at a bounded planning checkpoint."""

    # Compatibility value for checkpoints persisted before the delivery
    # contract existed. New model-facing schemas do not offer it.
    UNKNOWN = "unknown"
    CONTINUE_EXPLORATION = "continue_exploration"
    PRODUCE_CANDIDATE = "produce_candidate"
    VALIDATE_CANDIDATE = "validate_candidate"
    SUBMIT_CURRENT = "submit_current"
    SUBMIT_UNCERTAIN = "submit_uncertain"


class NextActionAlignment(str, Enum):
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNOBSERVABLE = "unobservable"


_ACTION_SEMANTIC_EFFECTS = frozenset({"read_only", "mutating", "validation", "unknown"})
_SHA256_ID = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ActionSemanticReceipt:
    """Bounded, path-free semantics for one planned or observed Tool action.

    Planned receipts leave execution outcome fields unset.  Observed receipts
    carry the Sandbox-authoritative outcome.  Target identities are hashes of
    normalized paths; raw Tool arguments and paths never cross this boundary.
    """

    SCHEMA_VERSION: ClassVar[str] = "aworld.action-semantic-receipt/v1"

    capability_aliases: tuple[str, ...]
    effect: str
    target_ids: tuple[str, ...] = field(default_factory=tuple)
    executed: bool | None = None
    succeeded: bool | None = None
    timed_out: bool | None = None
    validation_kind: str | None = None
    declared_deliverable_targeted: bool | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        aliases = _bounded_text_tuple(
            self.capability_aliases,
            name="capability_aliases",
            maximum_items=8,
            maximum_chars=128,
        )
        if any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", item) is None
            for item in aliases
        ):
            raise ValueError("capability_aliases must contain stable identities")
        object.__setattr__(self, "capability_aliases", tuple(dict.fromkeys(aliases)))
        if self.effect not in _ACTION_SEMANTIC_EFFECTS:
            raise ValueError("unsupported action semantic effect")
        targets = self.target_ids
        if (
            not isinstance(targets, (list, tuple))
            or len(targets) > 16
            or any(
                not isinstance(item, str) or _SHA256_ID.fullmatch(item) is None
                for item in targets
            )
        ):
            raise ValueError(
                "target_ids must contain at most 16 canonical sha256 identities"
            )
        object.__setattr__(self, "target_ids", tuple(dict.fromkeys(targets)))
        outcomes = (self.executed, self.succeeded, self.timed_out)
        if any(value is not None and not isinstance(value, bool) for value in outcomes):
            raise ValueError("execution outcome fields must be booleans or null")
        if self.executed is None and any(value is not None for value in outcomes[1:]):
            raise ValueError("planned semantics cannot claim an execution outcome")
        if self.executed is not None and any(value is None for value in outcomes[1:]):
            raise ValueError("observed semantics require a complete execution outcome")
        if self.succeeded is True and self.executed is not True:
            raise ValueError("a successful action must have executed")
        if self.succeeded is True and self.timed_out is True:
            raise ValueError("a timed-out action cannot be successful")
        validation_kind = self.validation_kind
        if validation_kind is not None and (
            not isinstance(validation_kind, str)
            or not validation_kind.strip()
            or len(validation_kind.strip()) > 128
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", validation_kind.strip()
            )
            is None
        ):
            raise ValueError(
                "validation_kind must be null or a nonempty string of at most 128 characters"
            )
        if validation_kind is not None:
            object.__setattr__(self, "validation_kind", validation_kind.strip())
        declared = self.declared_deliverable_targeted
        if declared is not None and not isinstance(declared, bool):
            raise ValueError("declared_deliverable_targeted must be boolean or null")
        call_id = self.tool_call_id
        if call_id is not None and (
            not isinstance(call_id, str)
            or not call_id.strip()
            or len(call_id.strip()) > 256
        ):
            raise ValueError("tool_call_id must be null or a bounded nonempty string")
        if call_id is not None:
            object.__setattr__(self, "tool_call_id", call_id.strip())

    @property
    def observable(self) -> bool:
        return self.effect != "unknown" and self.executed is not False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "capability_aliases": list(self.capability_aliases),
            "effect": self.effect,
            "target_ids": list(self.target_ids),
            "executed": self.executed,
            "succeeded": self.succeeded,
            "timed_out": self.timed_out,
            "validation_kind": self.validation_kind,
            "declared_deliverable_targeted": self.declared_deliverable_targeted,
            "tool_call_id": self.tool_call_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionSemanticReceipt":
        if not isinstance(value, Mapping):
            raise ValueError("action semantic receipt must be an object")
        expected = {
            "schema_version",
            "capability_aliases",
            "effect",
            "target_ids",
            "executed",
            "succeeded",
            "timed_out",
            "validation_kind",
            "declared_deliverable_targeted",
            "tool_call_id",
        }
        if set(value) - expected:
            raise ValueError("action semantic receipt contains unknown fields")
        if value.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported action semantic receipt schema")
        return cls(
            capability_aliases=value.get("capability_aliases"),
            effect=value.get("effect"),
            target_ids=value.get("target_ids") or (),
            executed=value.get("executed"),
            succeeded=value.get("succeeded"),
            timed_out=value.get("timed_out"),
            validation_kind=value.get("validation_kind"),
            declared_deliverable_targeted=value.get("declared_deliverable_targeted"),
            tool_call_id=value.get("tool_call_id"),
        )


def compare_action_semantic_shape(
    expected: ActionSemanticReceipt,
    observed: ActionSemanticReceipt,
    *,
    intent: DeliveryIntent,
) -> NextActionAlignment:
    """Compare action meaning without execution outcome or raw arguments."""

    if not expected.observable or observed.effect == "unknown":
        return NextActionAlignment.UNOBSERVABLE
    if not set(expected.capability_aliases).intersection(
        observed.capability_aliases
    ):
        return NextActionAlignment.MISMATCHED
    if expected.effect != observed.effect:
        return NextActionAlignment.MISMATCHED
    expected_targets = set(expected.target_ids)
    observed_targets = set(observed.target_ids)
    if expected_targets and not observed_targets:
        return NextActionAlignment.UNOBSERVABLE
    if expected_targets != observed_targets:
        return NextActionAlignment.MISMATCHED
    if expected.declared_deliverable_targeted is True:
        if observed.declared_deliverable_targeted is None:
            return NextActionAlignment.UNOBSERVABLE
        if observed.declared_deliverable_targeted is not True:
            return NextActionAlignment.MISMATCHED
    if intent is DeliveryIntent.PRODUCE_CANDIDATE:
        if expected.effect != "mutating":
            return NextActionAlignment.UNOBSERVABLE
    elif intent is DeliveryIntent.VALIDATE_CANDIDATE:
        if expected.validation_kind is None or observed.validation_kind is None:
            return NextActionAlignment.UNOBSERVABLE
        if expected.validation_kind != observed.validation_kind:
            return NextActionAlignment.MISMATCHED
    return NextActionAlignment.MATCHED


class ConvergenceStage(str, Enum):
    """Framework-owned phase constraint after advisory replanning stalls.

    The stage deliberately describes only the delivery lifecycle.  It does not
    select a command, infer task correctness, or contain benchmark-specific
    semantics.
    """

    PRODUCE_CANDIDATE = "produce_candidate"
    VALIDATE_REPAIR_OR_SUBMIT = "validate_repair_or_submit"


class EventKind(str, Enum):
    MODEL_EXECUTION_PROFILE = "model_execution_profile"
    MODEL_PLAN_UPDATE = "model_plan_update"
    NEXT_ACTION_BOUND = "next_action_bound"
    TOOL_OBSERVATION = "tool_observation"
    DELIVERY_STATUS = "delivery_status"
    REPLAN_APPLIED = "replan_applied"
    REPLAN_UNACKNOWLEDGED = "replan_unacknowledged"
    CONVERGENCE_EXHAUSTED = "convergence_exhausted"
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
    WOULD_APPLY_CONVERGENCE_CONSTRAINT = "would_apply_convergence_constraint"
    APPLY_CONVERGENCE_CONSTRAINT = "apply_convergence_constraint"
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
    MODEL_REPLAN_UNACKNOWLEDGED = "model_replan_unacknowledged"
    REPLAN_ACK_LIMIT_REACHED = "replan_ack_limit_reached"
    POST_CANDIDATE_STAGNATION = "post_candidate_stagnation"
    CONVERGENCE_CONSTRAINT_ACTIVE = "convergence_constraint_active"
    OBSERVED_LONG_HORIZON = "observed_long_horizon"
    STAGNATION_DETECTED = "stagnation_detected"
    DELIVERY_DEBT_DETECTED = "delivery_debt_detected"
    NEXT_ACTION_MISMATCH = "next_action_mismatch"
    CANDIDATE_DECISION_RESERVE = "candidate_decision_reserve"
    REPLAN_LIMIT_REACHED = "replan_limit_reached"
    REPLAN_APPLIED = "replan_applied"
    FINALIZATION_RESERVE = "finalization_reserve"
    CONVERGENCE_REJECTION_LIMIT = "convergence_rejection_limit"
    FINAL_REVIEW_REQUIRED = "final_review_required"
    FINAL_REVIEW_ALREADY_USED = "final_review_already_used"
    SHORT_TASK_BYPASS = "short_task_bypass"
    REVIEW_ACCEPTED = "review_accepted"
    REVIEW_REPAIR_REQUESTED = "review_repair_requested"
    REVIEW_UNCERTAIN = "review_uncertain"
    REVIEW_ERROR = "review_error"
    REPAIR_LIMIT_REACHED = "repair_limit_reached"
    REVIEW_BASIS_UNCHANGED = "review_basis_unchanged"
    REVIEW_BOUNDARY_UNAVAILABLE = "review_boundary_unavailable"
    ACCEPTANCE_EVIDENCE_MISSING = "acceptance_evidence_missing"
    CONTROLLER_ERROR = "controller_error"
    PERSISTENCE_ERROR = "protocol_persistence_error"
    INVALID_EVENT = "invalid_event"


@dataclass(frozen=True, slots=True)
class ModelExecutionProfile:
    """Bounded, content-free model assessment from an ordinary Tool turn."""

    horizon: ExecutionHorizon
    confidence: float
    milestone_count: int
    expected_tool_actions: int
    verification_required: bool
    workspace_mutation_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.horizon, ExecutionHorizon):
            try:
                object.__setattr__(self, "horizon", ExecutionHorizon(self.horizon))
            except (TypeError, ValueError) as exc:
                raise ValueError("horizon must be unknown, short, or long") from exc
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
        for name in ("verification_required", "workspace_mutation_required"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon.value,
            "confidence": self.confidence,
            "milestone_count": self.milestone_count,
            "expected_tool_actions": self.expected_tool_actions,
            "verification_required": self.verification_required,
            "workspace_mutation_required": self.workspace_mutation_required,
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
            "workspace_mutation_required",
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
            workspace_mutation_required=value.get("workspace_mutation_required", False),
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


def action_signature(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """Return a bounded canonical signature for one exact model-selected action."""

    if (
        not isinstance(tool_name, str)
        or not tool_name.strip()
        or len(tool_name.strip()) > 256
        or not isinstance(arguments, Mapping)
    ):
        raise ValueError("action signature requires a bounded Tool name and arguments")
    try:
        canonical_arguments = json.dumps(
            dict(arguments),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("action arguments must be canonical JSON") from exc
    if len(canonical_arguments) > 4096:
        raise ValueError("action arguments must not exceed 4096 characters")
    payload = json.dumps(
        {
            "tool": tool_name.strip(),
            "arguments": json.loads(canonical_arguments),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    delivery_intent: DeliveryIntent = DeliveryIntent.UNKNOWN
    delivery_rationale: str = ""
    next_action_tool: str | None = None
    next_action_signature: str | None = None
    next_action_semantics: ActionSemanticReceipt | None = None
    decision_call_id: str | None = None

    def __post_init__(self) -> None:
        for name, enum_type in (
            ("decision", PlanUpdateDecision),
            ("horizon", ExecutionHorizon),
            ("completion_assessment", CompletionAssessment),
            ("delivery_intent", DeliveryIntent),
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
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value.strip()) > maximum
            ):
                raise ValueError(
                    f"{name} must be a nonempty string of at most {maximum} characters"
                )
            object.__setattr__(self, name, value.strip())
        next_action_tool = self.next_action_tool
        if next_action_tool is not None:
            if (
                not isinstance(next_action_tool, str)
                or not next_action_tool.strip()
                or len(next_action_tool.strip()) > 256
            ):
                raise ValueError(
                    "next_action_tool must be null or a nonempty string of at most 256 characters"
                )
            object.__setattr__(self, "next_action_tool", next_action_tool.strip())
        next_action_signature = self.next_action_signature
        if next_action_signature is not None and (
            not isinstance(next_action_signature, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", next_action_signature) is None
        ):
            raise ValueError(
                "next_action_signature must be a sha256 fingerprint or null"
            )
        next_action_semantics = self.next_action_semantics
        if next_action_semantics is not None and not isinstance(
            next_action_semantics, ActionSemanticReceipt
        ):
            if not isinstance(next_action_semantics, Mapping):
                raise ValueError(
                    "next_action_semantics must be ActionSemanticReceipt or null"
                )
            try:
                next_action_semantics = ActionSemanticReceipt.from_dict(
                    next_action_semantics
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid next_action_semantics") from exc
            object.__setattr__(self, "next_action_semantics", next_action_semantics)
        decision_call_id = self.decision_call_id
        if decision_call_id is not None and (
            not isinstance(decision_call_id, str)
            or not decision_call_id.strip()
            or len(decision_call_id.strip()) > 256
        ):
            raise ValueError("decision_call_id must be null or a bounded string")
        if decision_call_id is not None:
            object.__setattr__(self, "decision_call_id", decision_call_id.strip())
        tool_required_intents = {
            DeliveryIntent.CONTINUE_EXPLORATION,
            DeliveryIntent.PRODUCE_CANDIDATE,
            DeliveryIntent.VALIDATE_CANDIDATE,
        }
        if self.delivery_intent in tool_required_intents and (
            next_action_tool is None or next_action_signature is None
        ):
            raise ValueError(
                "next_action_tool and next_action_signature are required for a Tool-backed delivery intent"
            )
        terminal_intents = {
            DeliveryIntent.SUBMIT_CURRENT,
            DeliveryIntent.SUBMIT_UNCERTAIN,
        }
        if self.delivery_intent in terminal_intents and (
            next_action_tool is not None
            or next_action_signature is not None
            or next_action_semantics is not None
        ):
            raise ValueError(
                "next_action_tool, next_action_signature, and next_action_semantics "
                "must be null for a terminal delivery intent"
            )
        rationale = self.delivery_rationale
        if not isinstance(rationale, str) or len(rationale.strip()) > 1024:
            raise ValueError(
                "delivery_rationale must be a string of at most 1024 characters"
            )
        rationale = rationale.strip()
        if self.delivery_intent is not DeliveryIntent.UNKNOWN and not rationale:
            raise ValueError(
                "delivery_rationale must be nonempty for a declared delivery intent"
            )
        object.__setattr__(self, "delivery_rationale", rationale)
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
            "next_action_tool": self.next_action_tool,
            "next_action_signature": self.next_action_signature,
            "next_action_semantics": (
                self.next_action_semantics.to_dict()
                if self.next_action_semantics is not None
                else None
            ),
            "decision_call_id": self.decision_call_id,
            "verification_plan": self.verification_plan,
            "completion_assessment": self.completion_assessment.value,
            "delivery_intent": self.delivery_intent.value,
            "delivery_rationale": self.delivery_rationale,
            "assumptions": list(self.assumptions),
            "retired_approaches": list(self.retired_approaches),
            "evidence_refs": list(self.evidence_refs),
            "selected_candidate_id": self.selected_candidate_id,
        }

    @classmethod
    def _from_validated_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        next_action_signature: str | None,
    ) -> "ModelPlanUpdate":
        return cls(
            decision=value.get("decision"),
            horizon=value.get("horizon"),
            milestone=value.get("milestone"),
            next_action=value.get("next_action"),
            next_action_tool=value.get("next_action_tool"),
            next_action_signature=next_action_signature,
            next_action_semantics=value.get("next_action_semantics"),
            decision_call_id=value.get("decision_call_id"),
            verification_plan=value.get("verification_plan"),
            completion_assessment=value.get("completion_assessment"),
            delivery_intent=value.get("delivery_intent", DeliveryIntent.UNKNOWN.value),
            delivery_rationale=value.get("delivery_rationale", ""),
            assumptions=value.get("assumptions"),
            retired_approaches=value.get("retired_approaches"),
            evidence_refs=value.get("evidence_refs"),
            selected_candidate_id=value.get("selected_candidate_id"),
        )

    @classmethod
    def from_model_mapping(cls, value: Mapping[str, Any]) -> "ModelPlanUpdate":
        """Parse an untrusted model response and derive its action fingerprint.

        Models must provide the exact Tool arguments as a JSON object string.
        A precomputed signature is deliberately outside this input schema so a
        model cannot claim alignment with arguments it did not actually name.
        """
        if not isinstance(value, Mapping):
            raise ValueError("model plan update must be a mapping")
        required = {
            "decision",
            "horizon",
            "milestone",
            "next_action",
            "next_action_tool",
            "next_action_arguments",
            "verification_plan",
            "completion_assessment",
            "delivery_intent",
            "delivery_rationale",
            "assumptions",
            "retired_approaches",
            "evidence_refs",
            "selected_candidate_id",
        }
        unknown = set(value) - required
        if unknown:
            raise ValueError("model plan update contains unknown fields")
        if not required.issubset(value):
            raise ValueError("model plan update is missing required fields")
        try:
            delivery_intent = DeliveryIntent(value.get("delivery_intent"))
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported delivery_intent") from exc
        if delivery_intent is DeliveryIntent.UNKNOWN:
            raise ValueError("model delivery_intent must be explicit")
        next_action_arguments = value.get("next_action_arguments")
        if next_action_arguments is not None:
            if (
                not isinstance(next_action_arguments, str)
                or len(next_action_arguments) > 4096
            ):
                raise ValueError(
                    "next_action_arguments must be a JSON object string of at most 4096 characters"
                )
            try:
                parsed_arguments = json.loads(next_action_arguments)
            except json.JSONDecodeError as exc:
                raise ValueError("next_action_arguments must be valid JSON") from exc
            if not isinstance(parsed_arguments, dict):
                raise ValueError("next_action_arguments must encode a JSON object")
            next_action_signature = action_signature(
                value.get("next_action_tool"),
                parsed_arguments,
            )
        else:
            next_action_signature = None
        return cls._from_validated_mapping(
            value,
            next_action_signature=next_action_signature,
        )

    @classmethod
    def from_persisted_mapping(cls, value: Mapping[str, Any]) -> "ModelPlanUpdate":
        """Restore a trusted checkpoint without accepting raw Tool arguments."""
        if not isinstance(value, Mapping):
            raise ValueError("persisted model plan update must be a mapping")
        required = {
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
        optional = {
            "delivery_intent",
            "delivery_rationale",
            "next_action_tool",
            "next_action_signature",
            "next_action_semantics",
            "decision_call_id",
        }
        unknown = set(value) - required - optional
        if unknown:
            raise ValueError("persisted model plan update contains unknown fields")
        if not required.issubset(value):
            raise ValueError("persisted model plan update is missing required fields")
        return cls._from_validated_mapping(
            value,
            next_action_signature=value.get("next_action_signature"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelPlanUpdate":
        """Compatibility alias for the safe, untrusted model parser."""
        return cls.from_model_mapping(value)


@dataclass(frozen=True, slots=True)
class ExecutionProtocolPolicy:
    """Validated internal controls for one task-scoped execution protocol.

    LLMAgent supplies AWorld's active default when callers omit this object.
    ``mode`` remains available for framework tests, shadow evaluation, and
    rollback; it is not a required end-user choice.
    """

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
    # Runtime observation thresholds provide an operational fallback when an
    # initially short/unknown model estimate grows into sustained tool work.
    # Explicit model-owned long-horizon classification may still arm earlier.
    activation_event_threshold: int = 6
    model_activation_confidence_threshold: float = 0.7
    model_activation_min_milestones: int = 2
    model_activation_min_tool_actions: int = 6
    stagnation_event_threshold: int = 6
    repetition_threshold: int = 3
    low_information_gain_threshold: int = 3
    no_goal_progress_threshold: int = 6
    delivery_debt_observation_threshold: int = 3
    # Once a candidate exists, repeated provably read-only exploration must
    # converge to validation, an evidence-driven repair, or submission.
    post_candidate_read_only_threshold: int = 3
    # Replanning remains model-owned, while completion review/repair has a
    # small generic safety bound.  Explicit callers may still tune these
    # limits (or use ``None`` for compatibility experiments).
    max_replans: int | None = None
    max_final_reviews: int | None = 2
    max_repairs: int | None = 1
    finalization_reserve_seconds: float = 60.0
    # The earlier reserve exposes a model-owned delivery choice while ordinary
    # Tools are still available. It does not itself revoke Tools or select an
    # action. Callers may tune it together with the finalization reserve.
    candidate_decision_reserve_seconds: float = 240.0
    # ``None`` is resolved by runtime configuration to a finite task-budget
    # fraction shared by every continuation within one review episode.
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
            "delivery_debt_observation_threshold",
            "post_candidate_read_only_threshold",
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
            or not math.isfinite(float(reserve))
            or reserve < 0
        ):
            raise ValueError(
                "finalization_reserve_seconds must be finite and non-negative"
            )
        candidate_reserve = self.candidate_decision_reserve_seconds
        if (
            isinstance(candidate_reserve, bool)
            or not isinstance(candidate_reserve, (int, float))
            or not math.isfinite(float(candidate_reserve))
            or candidate_reserve < 0
        ):
            raise ValueError(
                "candidate_decision_reserve_seconds must be finite and non-negative"
            )
        if candidate_reserve and candidate_reserve < reserve:
            raise ValueError(
                "candidate_decision_reserve_seconds must be zero or at least finalization_reserve_seconds"
            )
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
            "delivery_debt_observation_threshold": (
                self.delivery_debt_observation_threshold
            ),
            "post_candidate_read_only_threshold": (
                self.post_candidate_read_only_threshold
            ),
            "max_replans": self.max_replans,
            "max_final_reviews": self.max_final_reviews,
            "max_repairs": self.max_repairs,
            "finalization_reserve_seconds": self.finalization_reserve_seconds,
            "candidate_decision_reserve_seconds": (
                self.candidate_decision_reserve_seconds
            ),
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
            delivery_debt_observation_threshold=value.get(
                "delivery_debt_observation_threshold", 3
            ),
            post_candidate_read_only_threshold=value.get(
                "post_candidate_read_only_threshold", 3
            ),
            max_replans=value.get("max_replans"),
            max_final_reviews=value.get("max_final_reviews", 2),
            max_repairs=value.get("max_repairs", 1),
            finalization_reserve_seconds=value.get("finalization_reserve_seconds"),
            candidate_decision_reserve_seconds=value.get(
                "candidate_decision_reserve_seconds", 240.0
            ),
            # Additive v1 field retained for compatibility with persisted
            # policies written before bounded final review timeouts existed.
            final_review_timeout_seconds=value.get("final_review_timeout_seconds"),
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
    public_deliverable_declared: bool = False
    missing_public_deliverable_count: int = 0
    candidate_present: bool | None = None
    candidate_advanced: bool = False
    # Monotonic, framework-observed delivery high-water advance.  Unlike
    # ``candidate_advanced`` this is false for a changed-but-previously-seen
    # candidate, a failed Tool result, or untyped workspace churn.
    delivery_progress_advanced: bool = False
    public_candidate_mutated: bool = False
    workspace_mutated: bool = False
    read_only_observed: bool = False
    known_mutation_executed: bool = False
    validation_observed: bool = False
    new_information_observed: bool = False
    observed_action_names: tuple[str, ...] = field(default_factory=tuple)
    observed_action_signatures: tuple[str, ...] = field(default_factory=tuple)
    observed_action_semantics: tuple[ActionSemanticReceipt, ...] = field(
        default_factory=tuple
    )
    bound_tool_call_id: str | None = None
    current_step: int = 0
    remaining_seconds: float | None = None
    operation_hash: str | None = None
    result_hash: str | None = None
    review_boundary_available: bool | None = None
    review_outcome: ReviewOutcome | None = None
    model_execution_profile: ModelExecutionProfile | None = None
    model_plan_update: ModelPlanUpdate | None = None
    convergence_stage: ConvergenceStage | None = None

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
            "missing_public_deliverable_count",
        ):
            _non_negative_int(getattr(self, name), name)
        for name in ("goal_progress_observable", "goal_progress"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean or None")
        for name in (
            "evidence_advanced",
            "public_deliverable_declared",
            "workspace_mutated",
            "read_only_observed",
            "known_mutation_executed",
            "candidate_advanced",
            "delivery_progress_advanced",
            "public_candidate_mutated",
            "validation_observed",
            "new_information_observed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if self.candidate_present is not None and not isinstance(
            self.candidate_present, bool
        ):
            raise ValueError("candidate_present must be a boolean or None")
        object.__setattr__(
            self,
            "observed_action_names",
            _bounded_text_tuple(
                self.observed_action_names,
                name="observed_action_names",
                maximum_items=32,
                maximum_chars=256,
            ),
        )
        signatures = self.observed_action_signatures
        if (
            not isinstance(signatures, (list, tuple))
            or len(signatures) > 32
            or any(
                not isinstance(item, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
                for item in signatures
            )
        ):
            raise ValueError(
                "observed_action_signatures must contain at most 32 sha256 fingerprints"
            )
        object.__setattr__(self, "observed_action_signatures", tuple(signatures))
        raw_semantics = self.observed_action_semantics
        if not isinstance(raw_semantics, (list, tuple)) or len(raw_semantics) > 16:
            raise ValueError(
                "observed_action_semantics must contain at most 16 receipts"
            )
        semantics: list[ActionSemanticReceipt] = []
        for value in raw_semantics:
            try:
                receipt = (
                    value
                    if isinstance(value, ActionSemanticReceipt)
                    else ActionSemanticReceipt.from_dict(value)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid observed action semantic receipt") from exc
            semantics.append(receipt)
        object.__setattr__(self, "observed_action_semantics", tuple(semantics))
        bound_tool_call_id = self.bound_tool_call_id
        if bound_tool_call_id is not None and (
            not isinstance(bound_tool_call_id, str)
            or not bound_tool_call_id.strip()
            or len(bound_tool_call_id.strip()) > 256
        ):
            raise ValueError("bound_tool_call_id must be null or a bounded string")
        if self.kind is EventKind.NEXT_ACTION_BOUND and bound_tool_call_id is None:
            raise ValueError("next_action_bound requires bound_tool_call_id")
        if self.kind is not EventKind.NEXT_ACTION_BOUND and bound_tool_call_id is not None:
            raise ValueError("bound_tool_call_id is valid only for next_action_bound")
        if bound_tool_call_id is not None:
            object.__setattr__(self, "bound_tool_call_id", bound_tool_call_id.strip())
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
        if self.review_boundary_available is not None and not isinstance(
            self.review_boundary_available, bool
        ):
            raise ValueError("review_boundary_available must be a boolean or None")
        if (
            self.kind is not EventKind.CANDIDATE_FINAL
            and self.review_boundary_available is not None
        ):
            raise ValueError(
                "review_boundary_available is valid only for candidate_final"
            )
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
        if (
            self.kind is not EventKind.MODEL_PLAN_UPDATE
            and self.model_plan_update is not None
        ):
            raise ValueError(
                "model_plan_update is valid only for model_plan_update events"
            )
        if self.model_plan_update is not None and not isinstance(
            self.model_plan_update, ModelPlanUpdate
        ):
            raise ValueError("model_plan_update must be ModelPlanUpdate or None")
        if self.convergence_stage is not None and not isinstance(
            self.convergence_stage, ConvergenceStage
        ):
            try:
                object.__setattr__(
                    self, "convergence_stage", ConvergenceStage(self.convergence_stage)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported convergence_stage") from exc
        if (
            self.kind is not EventKind.REPLAN_UNACKNOWLEDGED
            and self.convergence_stage is not None
        ):
            raise ValueError(
                "convergence_stage is valid only for replan_unacknowledged"
            )

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
            public_deliverable_declared=self.public_deliverable_declared,
            missing_public_deliverable_count=self.missing_public_deliverable_count,
            candidate_present=self.candidate_present,
            candidate_advanced=self.candidate_advanced,
            delivery_progress_advanced=self.delivery_progress_advanced,
            public_candidate_mutated=self.public_candidate_mutated,
            workspace_mutated=self.workspace_mutated,
            read_only_observed=self.read_only_observed,
            known_mutation_executed=self.known_mutation_executed,
            validation_observed=self.validation_observed,
            new_information_observed=self.new_information_observed,
            observed_action_names=self.observed_action_names,
            observed_action_signatures=self.observed_action_signatures,
            observed_action_semantics=self.observed_action_semantics,
            bound_tool_call_id=self.bound_tool_call_id,
            current_step=self.current_step,
            remaining_seconds=self.remaining_seconds,
            operation_hash=self.operation_hash,
            result_hash=self.result_hash,
            review_boundary_available=self.review_boundary_available,
            review_outcome=self.review_outcome,
            model_execution_profile=self.model_execution_profile,
            model_plan_update=self.model_plan_update,
            convergence_stage=self.convergence_stage,
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
    public_deliverable_declared: bool = False
    missing_public_deliverable_count: int = 0
    candidate_present: bool | None = None
    candidate_advanced: bool = False
    delivery_progress_advanced: bool = False
    public_candidate_mutated: bool = False
    workspace_mutated: bool = False
    read_only_observed: bool = False
    known_mutation_executed: bool = False
    validation_observed: bool = False
    new_information_observed: bool = False
    observed_action_names: tuple[str, ...] = field(default_factory=tuple)
    observed_action_signatures: tuple[str, ...] = field(default_factory=tuple)
    observed_action_semantics: tuple[ActionSemanticReceipt, ...] = field(
        default_factory=tuple
    )
    bound_tool_call_id: str | None = None
    current_step: int = 0
    remaining_seconds: float | None = None
    operation_hash: str | None = None
    result_hash: str | None = None
    review_boundary_available: bool | None = None
    review_outcome: ReviewOutcome | None = None
    model_execution_profile: ModelExecutionProfile | None = None
    model_plan_update: ModelPlanUpdate | None = None
    convergence_stage: ConvergenceStage | None = None

    def __post_init__(self) -> None:
        for name in (
            "sequence",
            "repetition_count",
            "low_information_gain_count",
            "no_goal_progress_count",
            "current_step",
            "missing_public_deliverable_count",
        ):
            _non_negative_int(getattr(self, name), name)
        if not isinstance(self.kind, EventKind):
            raise ValueError("record kind must be EventKind")
        for name in ("goal_progress_observable", "goal_progress"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean or None")
        for name in (
            "evidence_advanced",
            "public_deliverable_declared",
            "workspace_mutated",
            "read_only_observed",
            "known_mutation_executed",
            "candidate_advanced",
            "delivery_progress_advanced",
            "public_candidate_mutated",
            "validation_observed",
            "new_information_observed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if self.candidate_present is not None and not isinstance(
            self.candidate_present, bool
        ):
            raise ValueError("candidate_present must be a boolean or None")
        object.__setattr__(
            self,
            "observed_action_names",
            _bounded_text_tuple(
                self.observed_action_names,
                name="observed_action_names",
                maximum_items=32,
                maximum_chars=256,
            ),
        )
        signatures = self.observed_action_signatures
        if (
            not isinstance(signatures, (list, tuple))
            or len(signatures) > 32
            or any(
                not isinstance(item, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
                for item in signatures
            )
        ):
            raise ValueError(
                "observed_action_signatures must contain at most 32 sha256 fingerprints"
            )
        object.__setattr__(self, "observed_action_signatures", tuple(signatures))
        raw_semantics = self.observed_action_semantics
        if not isinstance(raw_semantics, (list, tuple)) or len(raw_semantics) > 16:
            raise ValueError(
                "observed_action_semantics must contain at most 16 receipts"
            )
        semantics: list[ActionSemanticReceipt] = []
        for value in raw_semantics:
            try:
                receipt = (
                    value
                    if isinstance(value, ActionSemanticReceipt)
                    else ActionSemanticReceipt.from_dict(value)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid observed action semantic receipt") from exc
            semantics.append(receipt)
        object.__setattr__(self, "observed_action_semantics", tuple(semantics))
        bound_tool_call_id = self.bound_tool_call_id
        if bound_tool_call_id is not None and (
            not isinstance(bound_tool_call_id, str)
            or not bound_tool_call_id.strip()
            or len(bound_tool_call_id.strip()) > 256
        ):
            raise ValueError("bound_tool_call_id must be null or a bounded string")
        if self.kind is EventKind.NEXT_ACTION_BOUND and bound_tool_call_id is None:
            raise ValueError("next_action_bound record requires bound_tool_call_id")
        if self.kind is not EventKind.NEXT_ACTION_BOUND and bound_tool_call_id is not None:
            raise ValueError(
                "bound_tool_call_id is valid only for next_action_bound records"
            )
        if bound_tool_call_id is not None:
            object.__setattr__(self, "bound_tool_call_id", bound_tool_call_id.strip())
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
        if self.review_boundary_available is not None and not isinstance(
            self.review_boundary_available, bool
        ):
            raise ValueError("review_boundary_available must be a boolean or None")
        if (
            self.kind is not EventKind.CANDIDATE_FINAL
            and self.review_boundary_available is not None
        ):
            raise ValueError(
                "review_boundary_available is valid only for candidate_final records"
            )
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
        if (
            self.kind is not EventKind.MODEL_PLAN_UPDATE
            and self.model_plan_update is not None
        ):
            raise ValueError(
                "model_plan_update is valid only for model plan update records"
            )
        if self.model_plan_update is not None and not isinstance(
            self.model_plan_update, ModelPlanUpdate
        ):
            raise ValueError("record model_plan_update has invalid type")
        if self.convergence_stage is not None and not isinstance(
            self.convergence_stage, ConvergenceStage
        ):
            raise ValueError("record convergence_stage has invalid type")
        if (
            self.kind is not EventKind.REPLAN_UNACKNOWLEDGED
            and self.convergence_stage is not None
        ):
            raise ValueError(
                "convergence_stage is valid only for replan-unacknowledged records"
            )

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
            "public_deliverable_declared": self.public_deliverable_declared,
            "missing_public_deliverable_count": self.missing_public_deliverable_count,
            "candidate_present": self.candidate_present,
            "candidate_advanced": self.candidate_advanced,
            "delivery_progress_advanced": self.delivery_progress_advanced,
            "public_candidate_mutated": self.public_candidate_mutated,
            "workspace_mutated": self.workspace_mutated,
            "read_only_observed": self.read_only_observed,
            "known_mutation_executed": self.known_mutation_executed,
            "validation_observed": self.validation_observed,
            "new_information_observed": self.new_information_observed,
            "observed_action_names": list(self.observed_action_names),
            "observed_action_signatures": list(self.observed_action_signatures),
            "observed_action_semantics": [
                receipt.to_dict() for receipt in self.observed_action_semantics
            ],
            "bound_tool_call_id": self.bound_tool_call_id,
            "current_step": self.current_step,
            "remaining_seconds": self.remaining_seconds,
            "operation_hash": self.operation_hash,
            "result_hash": self.result_hash,
            "review_boundary_available": self.review_boundary_available,
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
            "convergence_stage": (
                self.convergence_stage.value
                if self.convergence_stage is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProtocolEventRecord":
        outcome = value.get("review_outcome")
        profile = value.get("model_execution_profile")
        plan_update = value.get("model_plan_update")
        convergence_stage = value.get("convergence_stage")
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
            public_deliverable_declared=value.get("public_deliverable_declared", False),
            missing_public_deliverable_count=_non_negative_int(
                value.get("missing_public_deliverable_count", 0),
                "missing_public_deliverable_count",
            ),
            candidate_present=value.get("candidate_present"),
            candidate_advanced=value.get("candidate_advanced", False),
            delivery_progress_advanced=value.get(
                "delivery_progress_advanced", False
            ),
            public_candidate_mutated=value.get("public_candidate_mutated", False),
            workspace_mutated=value.get("workspace_mutated", False),
            read_only_observed=value.get("read_only_observed", False),
            known_mutation_executed=value.get("known_mutation_executed", False),
            validation_observed=value.get("validation_observed", False),
            new_information_observed=value.get("new_information_observed", False),
            observed_action_names=value.get("observed_action_names") or (),
            observed_action_signatures=value.get("observed_action_signatures") or (),
            observed_action_semantics=value.get("observed_action_semantics") or (),
            bound_tool_call_id=value.get("bound_tool_call_id"),
            current_step=_non_negative_int(
                value.get("current_step", 0), "current_step"
            ),
            remaining_seconds=value.get("remaining_seconds"),
            operation_hash=value.get("operation_hash"),
            result_hash=value.get("result_hash"),
            review_boundary_available=value.get("review_boundary_available"),
            review_outcome=ReviewOutcome(outcome) if outcome is not None else None,
            model_execution_profile=(
                ModelExecutionProfile.from_mapping(profile)
                if profile is not None
                else None
            ),
            model_plan_update=(
                ModelPlanUpdate.from_persisted_mapping(plan_update)
                if plan_update is not None
                else None
            ),
            convergence_stage=(
                ConvergenceStage(convergence_stage)
                if convergence_stage is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ExecutionProtocolState:
    SCHEMA_VERSION: ClassVar[str] = "aworld.execution-protocol-state/v2"
    LEGACY_SCHEMA_VERSION: ClassVar[str] = "aworld.execution-protocol-state/v1"

    scope: ProtocolScope
    phase: ProtocolPhase = ProtocolPhase.EXECUTE
    revision: int = 0
    event_count: int = 0
    tool_observation_count: int = 0
    attempt_epoch: int = 0
    stagnant_observations: int = 0
    replan_count: int = 0
    # ``replan_count`` is the v1 compatibility projection of requested
    # checkpoints.  Keep explicit requested/applied counters so telemetry never
    # claims that advisory guidance changed the model's plan.
    replan_requested_count: int = 0
    replan_applied_count: int = 0
    decision_checkpoint_pending: bool = False
    decision_checkpoint_reason: DecisionReason | None = None
    decision_checkpoint_candidate_present: bool | None = None
    last_replan_attempt_epoch: int | None = None
    last_delivery_debt_attempt_epoch: int | None = None
    delivery_debt_observations: int = 0
    workspace_mutation_absent_observations: int = 0
    delivery_checkpoint_count: int = 0
    candidate_decision_count: int = 0
    candidate_decision_recorded: bool = False
    next_action_alignment_pending: bool = False
    pending_next_action_plan_sequence: int | None = None
    pending_next_action_call_id: str | None = None
    last_action_alignment: NextActionAlignment | None = None
    last_action_alignment_plan_sequence: int | None = None
    last_action_alignment_observation_sequence: int | None = None
    action_alignment_match_count: int = 0
    action_alignment_mismatch_count: int = 0
    candidate_present: bool | None = None
    public_deliverable_declared: bool = False
    public_candidate_mutated: bool = False
    candidate_epoch_advanced: bool = False
    candidate_checkpoint_recorded: bool = False
    post_candidate_read_only_observations: int = 0
    # Authoritative counter.  The read-only name above remains a one-release
    # serialization/telemetry alias for older readers.
    post_candidate_no_delivery_progress_observations: int = 0
    convergence_constraint_active: bool = False
    convergence_stage: ConvergenceStage | None = None
    convergence_constraint_activation_count: int = 0
    final_review_count: int = 0
    repair_count: int = 0
    candidate_final_count: int = 0
    review_pending: bool = False
    # Additive v2 flag.  ``phase=review`` is the legacy-safe wire projection
    # for a terminal unverified outcome; older readers ignore this field but
    # still never observe ``phase=complete``.
    terminal_incomplete: bool = False
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
            "replan_requested_count",
            "replan_applied_count",
            "final_review_count",
            "repair_count",
            "candidate_final_count",
            "delivery_debt_observations",
            "workspace_mutation_absent_observations",
            "delivery_checkpoint_count",
            "candidate_decision_count",
            "action_alignment_match_count",
            "action_alignment_mismatch_count",
            "post_candidate_read_only_observations",
            "post_candidate_no_delivery_progress_observations",
            "convergence_constraint_activation_count",
        ):
            _non_negative_int(getattr(self, name), name)
        if self.last_replan_attempt_epoch is not None:
            _non_negative_int(
                self.last_replan_attempt_epoch, "last_replan_attempt_epoch"
            )
        for name in (
            "last_delivery_debt_attempt_epoch",
            "pending_next_action_plan_sequence",
            "last_action_alignment_plan_sequence",
            "last_action_alignment_observation_sequence",
        ):
            value = getattr(self, name)
            if value is not None:
                _non_negative_int(value, name)
        pending_call_id = self.pending_next_action_call_id
        if pending_call_id is not None and (
            not isinstance(pending_call_id, str)
            or not pending_call_id.strip()
            or len(pending_call_id.strip()) > 256
        ):
            raise ValueError(
                "pending_next_action_call_id must be null or a bounded string"
            )
        if pending_call_id is not None:
            object.__setattr__(
                self, "pending_next_action_call_id", pending_call_id.strip()
            )
            if not self.next_action_alignment_pending:
                raise ValueError(
                    "pending_next_action_call_id requires pending alignment"
                )
        if (
            not isinstance(self.review_pending, bool)
            or not isinstance(self.terminal_incomplete, bool)
            or not isinstance(self.decision_checkpoint_pending, bool)
            or not isinstance(self.finalization_entered, bool)
            or not isinstance(self.long_horizon_armed, bool)
            or not isinstance(self.acceptance_confirmed, bool)
            or not isinstance(self.candidate_decision_recorded, bool)
            or not isinstance(self.next_action_alignment_pending, bool)
            or not isinstance(self.public_deliverable_declared, bool)
            or not isinstance(self.public_candidate_mutated, bool)
            or not isinstance(self.candidate_epoch_advanced, bool)
            or not isinstance(self.candidate_checkpoint_recorded, bool)
            or not isinstance(self.convergence_constraint_active, bool)
        ):
            raise ValueError("state flags must be booleans")
        if self.candidate_present is not None and not isinstance(
            self.candidate_present, bool
        ):
            raise ValueError("candidate_present must be a boolean or None")
        if self.convergence_stage is not None and not isinstance(
            self.convergence_stage, ConvergenceStage
        ):
            try:
                object.__setattr__(
                    self, "convergence_stage", ConvergenceStage(self.convergence_stage)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported convergence_stage") from exc
        if self.convergence_constraint_active != (self.convergence_stage is not None):
            raise ValueError(
                "active convergence constraint requires exactly one convergence stage"
            )
        if self.decision_checkpoint_reason is not None and not isinstance(
            self.decision_checkpoint_reason, DecisionReason
        ):
            try:
                object.__setattr__(
                    self,
                    "decision_checkpoint_reason",
                    DecisionReason(self.decision_checkpoint_reason),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported decision_checkpoint_reason") from exc
        if self.decision_checkpoint_candidate_present is not None and not isinstance(
            self.decision_checkpoint_candidate_present, bool
        ):
            raise ValueError(
                "decision_checkpoint_candidate_present must be a boolean or None"
            )
        if self.last_action_alignment is not None and not isinstance(
            self.last_action_alignment, NextActionAlignment
        ):
            try:
                object.__setattr__(
                    self,
                    "last_action_alignment",
                    NextActionAlignment(self.last_action_alignment),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported last_action_alignment") from exc
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
            "replan_requested_count": self.replan_requested_count,
            "replan_applied_count": self.replan_applied_count,
            "decision_checkpoint_pending": self.decision_checkpoint_pending,
            "decision_checkpoint_reason": (
                self.decision_checkpoint_reason.value
                if self.decision_checkpoint_reason is not None
                else None
            ),
            "decision_checkpoint_candidate_present": (
                self.decision_checkpoint_candidate_present
            ),
            "last_replan_attempt_epoch": self.last_replan_attempt_epoch,
            "last_delivery_debt_attempt_epoch": self.last_delivery_debt_attempt_epoch,
            "delivery_debt_observations": self.delivery_debt_observations,
            "workspace_mutation_absent_observations": (
                self.workspace_mutation_absent_observations
            ),
            "delivery_checkpoint_count": self.delivery_checkpoint_count,
            "candidate_decision_count": self.candidate_decision_count,
            "candidate_decision_recorded": self.candidate_decision_recorded,
            "next_action_alignment_pending": self.next_action_alignment_pending,
            "pending_next_action_plan_sequence": self.pending_next_action_plan_sequence,
            "pending_next_action_call_id": self.pending_next_action_call_id,
            "last_action_alignment": (
                self.last_action_alignment.value
                if self.last_action_alignment is not None
                else None
            ),
            "last_action_alignment_plan_sequence": (
                self.last_action_alignment_plan_sequence
            ),
            "last_action_alignment_observation_sequence": (
                self.last_action_alignment_observation_sequence
            ),
            "action_alignment_match_count": self.action_alignment_match_count,
            "action_alignment_mismatch_count": self.action_alignment_mismatch_count,
            "candidate_present": self.candidate_present,
            "public_deliverable_declared": self.public_deliverable_declared,
            "public_candidate_mutated": self.public_candidate_mutated,
            "candidate_epoch_advanced": self.candidate_epoch_advanced,
            "candidate_checkpoint_recorded": self.candidate_checkpoint_recorded,
            "post_candidate_read_only_observations": (
                self.post_candidate_read_only_observations
            ),
            "post_candidate_no_delivery_progress_observations": (
                self.post_candidate_no_delivery_progress_observations
            ),
            "convergence_constraint_active": self.convergence_constraint_active,
            "convergence_stage": (
                self.convergence_stage.value
                if self.convergence_stage is not None
                else None
            ),
            "convergence_constraint_activation_count": (
                self.convergence_constraint_activation_count
            ),
            "final_review_count": self.final_review_count,
            "repair_count": self.repair_count,
            "candidate_final_count": self.candidate_final_count,
            "review_pending": self.review_pending,
            "terminal_incomplete": self.terminal_incomplete,
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
        if value.get("schema_version") not in {
            cls.LEGACY_SCHEMA_VERSION,
            cls.SCHEMA_VERSION,
        }:
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
            replan_requested_count=_non_negative_int(
                value.get("replan_requested_count", value.get("replan_count", 0)),
                "replan_requested_count",
            ),
            replan_applied_count=_non_negative_int(
                value.get("replan_applied_count", 0), "replan_applied_count"
            ),
            decision_checkpoint_pending=value.get("decision_checkpoint_pending", False),
            decision_checkpoint_reason=value.get("decision_checkpoint_reason"),
            decision_checkpoint_candidate_present=value.get(
                "decision_checkpoint_candidate_present"
            ),
            last_replan_attempt_epoch=value.get("last_replan_attempt_epoch"),
            last_delivery_debt_attempt_epoch=value.get(
                "last_delivery_debt_attempt_epoch"
            ),
            delivery_debt_observations=_non_negative_int(
                value.get("delivery_debt_observations", 0),
                "delivery_debt_observations",
            ),
            workspace_mutation_absent_observations=_non_negative_int(
                value.get("workspace_mutation_absent_observations", 0),
                "workspace_mutation_absent_observations",
            ),
            delivery_checkpoint_count=_non_negative_int(
                value.get("delivery_checkpoint_count", 0),
                "delivery_checkpoint_count",
            ),
            candidate_decision_count=_non_negative_int(
                value.get("candidate_decision_count", 0),
                "candidate_decision_count",
            ),
            candidate_decision_recorded=value.get("candidate_decision_recorded", False),
            next_action_alignment_pending=value.get(
                "next_action_alignment_pending", False
            ),
            pending_next_action_plan_sequence=value.get(
                "pending_next_action_plan_sequence"
            ),
            pending_next_action_call_id=value.get("pending_next_action_call_id"),
            last_action_alignment=value.get("last_action_alignment"),
            last_action_alignment_plan_sequence=value.get(
                "last_action_alignment_plan_sequence"
            ),
            last_action_alignment_observation_sequence=value.get(
                "last_action_alignment_observation_sequence"
            ),
            action_alignment_match_count=_non_negative_int(
                value.get("action_alignment_match_count", 0),
                "action_alignment_match_count",
            ),
            action_alignment_mismatch_count=_non_negative_int(
                value.get("action_alignment_mismatch_count", 0),
                "action_alignment_mismatch_count",
            ),
            candidate_present=value.get("candidate_present"),
            public_deliverable_declared=value.get(
                "public_deliverable_declared", False
            ),
            public_candidate_mutated=value.get("public_candidate_mutated", False),
            candidate_epoch_advanced=value.get("candidate_epoch_advanced", False),
            candidate_checkpoint_recorded=value.get(
                "candidate_checkpoint_recorded", False
            ),
            post_candidate_read_only_observations=_non_negative_int(
                value.get(
                    "post_candidate_read_only_observations",
                    value.get(
                        "post_candidate_no_delivery_progress_observations", 0
                    ),
                ),
                "post_candidate_read_only_observations",
            ),
            post_candidate_no_delivery_progress_observations=_non_negative_int(
                value.get(
                    "post_candidate_no_delivery_progress_observations",
                    value.get("post_candidate_read_only_observations", 0),
                ),
                "post_candidate_no_delivery_progress_observations",
            ),
            convergence_constraint_active=value.get(
                "convergence_constraint_active", False
            ),
            convergence_stage=value.get("convergence_stage"),
            convergence_constraint_activation_count=_non_negative_int(
                value.get("convergence_constraint_activation_count", 0),
                "convergence_constraint_activation_count",
            ),
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
            terminal_incomplete=value.get("terminal_incomplete", False),
            finalization_entered=value.get("finalization_entered", False),
            long_horizon_armed=value.get("long_horizon_armed", False),
            acceptance_confirmed=value.get("acceptance_confirmed", False),
            model_execution_profile=(
                ModelExecutionProfile.from_mapping(profile)
                if profile is not None
                else None
            ),
            model_plan_update=(
                ModelPlanUpdate.from_persisted_mapping(plan_update)
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


def execution_protocol_eligible(
    state: ExecutionProtocolState,
    *,
    public_deliverable_declared: bool | None = None,
) -> bool:
    """Return whether framework convergence control may affect this task.

    Operationally observed long work is sufficient on its own.  Before that
    point, a model-owned execution profile must be paired with a public
    deliverable contract.  The explicit override lets callers project a
    freshly extracted contract before the first Tool observation has copied it
    into protocol state; it may only strengthen the public-contract fact, not
    replace the model profile.
    """

    if not isinstance(state, ExecutionProtocolState):
        raise TypeError("state must be ExecutionProtocolState")
    declared = state.public_deliverable_declared
    if public_deliverable_declared is not None:
        if not isinstance(public_deliverable_declared, bool):
            raise TypeError("public_deliverable_declared must be boolean or None")
        declared = declared or public_deliverable_declared
    return bool(
        state.long_horizon_armed
        or (state.model_execution_profile is not None and declared)
    )


__all__ = [
    "ActionSemanticReceipt",
    "CompletionAssessment",
    "compare_action_semantic_shape",
    "ConvergenceStage",
    "ControllerAction",
    "action_signature",
    "ControllerDecision",
    "DeliveryIntent",
    "DecisionReason",
    "EventKind",
    "ExecutionProtocolEvent",
    "ExecutionHorizon",
    "ExecutionProtocolPolicy",
    "ExecutionProtocolState",
    "execution_protocol_eligible",
    "ModelExecutionProfile",
    "ModelPlanUpdate",
    "NextActionAlignment",
    "PlanUpdateDecision",
    "ProtocolEventRecord",
    "ProtocolMode",
    "ProtocolPhase",
    "ProtocolScope",
    "ProtocolTransition",
    "ReviewOutcome",
]
