"""Runtime integration helpers for the domain-independent execution protocol.

This module converts existing framework observations into text-free protocol
events.  It never decides whether the user's task is correct and it never
writes control state into the user's workspace.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from enum import Enum
import hashlib
import json
import os
import re
import secrets
import shlex
from typing import Any, Mapping, Sequence

from aworld.core.context.execution_state import state_context
from aworld.core.execution_protocol import (
    ActionSemanticReceipt,
    ConvergenceStage,
    ControllerAction,
    DeliveryIntent,
    DecisionReason,
    EventKind,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolStore,
    HARD_CONVERGENCE_MIN_DEADLINE_FRACTION,
    ModelExecutionProfile,
    ModelPlanUpdate,
    NextActionAlignment,
    ProtocolMode,
    ProtocolTransition,
    ReviewOutcome,
    action_signature,
    compare_action_semantic_shape,
    execution_protocol_eligible,
)
from aworld.sandbox.tool_observation import (
    actions_are_provably_read_only,
    build_preflight_action_semantic_receipt,
    canonical_invocation_cwd,
    canonical_tool_identity,
    classify_tool_effect,
    declared_action_target_ids,
)


EXECUTION_PROTOCOL_POLICY_KEY = "execution_protocol_policy"
EXECUTION_PROTOCOL_PENDING_KEY = "execution_protocol_pending_guidance"
EXECUTION_PROTOCOL_METRICS_KEY = "execution_protocol_metrics"
EXECUTION_PROTOCOL_FALLBACK_KEY = "execution_protocol_candidate_fallback"
EXECUTION_PROTOCOL_MODEL_PROFILE_KEY = "execution_protocol_model_profile_attempt"
EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY = "execution_protocol_model_decisions"
EXECUTION_PROTOCOL_HYPOTHESES_KEY = "execution_protocol_hypotheses"
EXECUTION_PROTOCOL_CRITIC_KEY = "execution_protocol_acceptance_critic"
EXECUTION_PROTOCOL_PUBLIC_PROBES_KEY = "execution_protocol_public_probes"
EXECUTION_PROTOCOL_DEADLINE_GUIDANCE_KEY = "execution_protocol_deadline_guidance"
EXECUTION_PROTOCOL_CONVERGENCE_GUIDANCE_KEY = (
    "execution_protocol_convergence_guidance"
)
MUTATION_GATE_SCHEMA = "aworld.mutation-gate/v4"
_LEGACY_MUTATION_GATE_SCHEMAS = frozenset(
    {
        "aworld.mutation-gate/v1",
        "aworld.mutation-gate/v2",
        "aworld.mutation-gate/v3",
    }
)
MUTATION_GATE_STATE_KEY = "execution_protocol_mutation_gate"
MUTATION_GATE_ACTIVE_INDEX_KEY = "execution_protocol_mutation_gate_active_index"
_MUTATION_GATE_INDEX_NAMESPACE = "__task_convergence__"
INDEPENDENT_ACCEPTANCE_CRITIC_ENV = "AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC"
SEMANTIC_PROGRESS_LEDGER_ENV = "AWORLD_SEMANTIC_PROGRESS_LEDGER"
_MAX_FALLBACK_CHARS = 64_000
_MAX_TELEMETRY_COUNTER = 1_000_000
_PUBLIC_DELIVERABLE_SCHEMA = "aworld.public-deliverables/v1"
_PUBLIC_DELIVERABLE_AUTHORITY = "public_task_advisory"
_MAX_DECISION_ATTEMPTS = 2
_MAX_CONSECUTIVE_UNAPPLIED_REPLANS = 2
_MUTATION_GATE_READ_ONLY_THRESHOLD = 8
_MUTATION_GATE_DEADLINE_MIN_READS = 3
_MUTATION_GATE_DEADLINE_FRACTION = 0.20
# Between the 40% candidate checkpoint and the 65% validation checkpoint,
# recent framework-observed analysis progress may defer hard convergence for a
# small number of non-progress observations.  The absolute 65% ceiling keeps
# this from becoming an unbounded exploration escape hatch.
_ANALYSIS_RUNWAY_STAGNATION_THRESHOLD = 3
_ANALYSIS_RUNWAY_MAX_DEADLINE_FRACTION = 0.65
_ANALYSIS_RUNWAY_MAX_PROGRESS_RESETS = 8
_MAX_CANDIDATE_DIAGNOSTIC_READS = 3
_MAX_PRE_CANDIDATE_REJECTED_CALLS = 2
_MAX_EXHAUSTED_REJECTED_BATCHES = 2
_MAX_POST_CANDIDATE_PURE_REJECTED_BATCHES = 4
_MAX_MUTATION_GATE_CALL_REJECTIONS = 32
_SAFE_RECEIPT_TOOL_CALL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_REPAIR_AUTHORIZATION_SCHEMA = "aworld.repair-authorization/v2"
_REPAIR_SOURCE_VALIDATION_FAILURE = "validation_failure"
_REPAIR_SOURCE_MODEL_REVIEW = "model_review_repair"
_REPAIR_SOURCE_ACCEPTANCE_CRITIC = "acceptance_critic_repair"
_REPAIR_AUTHORIZATION_SOURCES = frozenset(
    {
        _REPAIR_SOURCE_VALIDATION_FAILURE,
        _REPAIR_SOURCE_MODEL_REVIEW,
        _REPAIR_SOURCE_ACCEPTANCE_CRITIC,
    }
)
_REVIEW_REPAIR_AUTHORIZATION_SOURCES = frozenset(
    {
        _REPAIR_SOURCE_MODEL_REVIEW,
        _REPAIR_SOURCE_ACCEPTANCE_CRITIC,
    }
)
_REPAIR_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema_version",
        "scope_hash",
        "candidate_fingerprint",
        "failure_evidence_hash",
        "source",
        "used",
    }
)
_DEADLINE_STAGE_THRESHOLDS = (
    ("candidate_due", 0.40),
    ("validation_due", 0.65),
    ("delivery_only", 0.80),
)
_REPAIR_EVIDENCE_HIGH_WATER_BITS = 512
_CANDIDATE_DIAGNOSTIC_HIGH_WATER_FULL = (
    1 << _REPAIR_EVIDENCE_HIGH_WATER_BITS
) - 1


def _repair_evidence_positions(fingerprint: str) -> tuple[int, ...]:
    digest = hashlib.sha256(fingerprint.encode("utf-8")).digest()
    return tuple(
        int.from_bytes(digest[index : index + 2], "big")
        % _REPAIR_EVIDENCE_HIGH_WATER_BITS
        for index in range(0, 8, 2)
    )


class MutationGateRejectionReason(str, Enum):
    """Finite, path-free reasons for rejecting one converged Tool call."""

    EFFECT_UNKNOWN = "effect_unknown"
    DIAGNOSTIC_QUOTA_EXHAUSTED = "diagnostic_quota_exhausted"
    DIAGNOSTIC_BATCH_LIMIT = "diagnostic_batch_limit"
    UNDECLARED_HELPER = "undeclared_helper"
    REPLAYED_REVISION = "replayed_revision"
    VALIDATION_UNREGISTERED = "validation_unregistered"
    REVISION_BATCH_LIMIT = "revision_batch_limit"
    REPAIR_UNAUTHORIZED = "repair_unauthorized"
    CANDIDATE_PLAN_MISMATCH = "candidate_plan_mismatch"
    CALL_IDENTITY_INVALID = "call_identity_invalid"
    FINALIZATION_LATCHED = "finalization_latched"
    SCOPE_AMBIGUOUS = "scope_ambiguous"


def _serialized_call_rejections(
    values: Sequence[tuple[str | None, MutationGateRejectionReason]],
) -> tuple[list[dict[str, str | None]], int]:
    """Return a bounded receipt projection without Tool arguments or paths."""

    retained = [
        {
            "tool_call_id": (
                call_id
                if isinstance(call_id, str)
                and _SAFE_RECEIPT_TOOL_CALL_ID.fullmatch(call_id) is not None
                else None
            ),
            "reason": reason.value,
        }
        for call_id, reason in values[:_MAX_MUTATION_GATE_CALL_REJECTIONS]
    ]
    return retained, max(0, len(values) - len(retained))


def _repair_evidence_mask(value: Any) -> int:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-fA-F]{1,128}", value) is None
    ):
        return 0
    return int(value, 16)


def _declared_mutation_attempt_mask(value: Any) -> int:
    """Parse only a bounded, unsigned hexadecimal mutation-attempt mask."""

    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-fA-F]{1,128}", value) is None
    ):
        return 0
    return int(value, 16)


def _serialized_declared_mutation_attempt_high_water(value: Any) -> str:
    return format(_declared_mutation_attempt_mask(value), "0128x")


def _repair_evidence_seen(mask: int, fingerprint: str) -> bool:
    return all(mask & (1 << bit) for bit in _repair_evidence_positions(fingerprint))


def _repair_evidence_add(mask: int, fingerprint: str) -> int:
    for bit in _repair_evidence_positions(fingerprint):
        mask |= 1 << bit
    return mask


def _hashed_action_signature(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> str:
    """Hash exact canonical Tool arguments without retaining or size-capping them."""

    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool or len(normalized_tool) > 256:
        raise ValueError("action signature requires a bounded Tool name")
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256()
    try:
        for chunk in encoder.iterencode(
            {"arguments": dict(arguments), "tool": normalized_tool}
        ):
            digest.update(chunk.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("action arguments must be canonical JSON") from exc
    return "sha256:" + digest.hexdigest()


def _bounded_counter(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(_MAX_TELEMETRY_COUNTER, max(0, parsed))


def _is_canonical_candidate_diagnostic_read_count(value: Any) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, int)
        and 0 <= value <= _MAX_CANDIDATE_DIAGNOSTIC_READS
    )


def _is_canonical_semantic_fingerprint(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
    )


def _candidate_diagnostic_high_water(value: Any) -> tuple[int, bool]:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{128}", value) is None
    ):
        return 0, False
    return int(value, 16), True


def _candidate_diagnostic_slot_fingerprint(
    candidate_fingerprint: str,
    slot: int,
) -> str:
    from aworld.core.context.compiler import semantic_fingerprint

    return semantic_fingerprint(
        {
            "candidate_fingerprint": candidate_fingerprint,
            "kind": "candidate_diagnostic",
            "slot": slot,
        }
    )


def _candidate_diagnostic_count(mask: int, candidate_fingerprint: str) -> int:
    return sum(
        _repair_evidence_seen(
            mask,
            _candidate_diagnostic_slot_fingerprint(candidate_fingerprint, slot),
        )
        for slot in range(_MAX_CANDIDATE_DIAGNOSTIC_READS)
    )


def _candidate_diagnostic_fail_closed(
    mask: int,
    candidate_fingerprint: str,
) -> int:
    for slot in range(_MAX_CANDIDATE_DIAGNOSTIC_READS):
        mask = _repair_evidence_add(
            mask,
            _candidate_diagnostic_slot_fingerprint(candidate_fingerprint, slot),
        )
    return mask


def _normalized_candidate_diagnostic_state(
    gate: Mapping[str, Any],
) -> tuple[int, int, bool]:
    candidate_fingerprint = gate.get("candidate_fingerprint")
    mask, high_water_is_canonical = _candidate_diagnostic_high_water(
        gate.get("candidate_diagnostic_high_water")
    )
    candidate_binding_is_canonical = _is_canonical_semantic_fingerprint(
        candidate_fingerprint
    )
    expected_count = (
        _candidate_diagnostic_count(mask, candidate_fingerprint)
        if candidate_binding_is_canonical
        else _MAX_CANDIDATE_DIAGNOSTIC_READS
    )
    state_is_canonical = bool(
        gate.get("schema_version") == MUTATION_GATE_SCHEMA
        and high_water_is_canonical
        and candidate_binding_is_canonical
        and gate.get("candidate_binding_unresolved") is not True
        and _is_canonical_candidate_diagnostic_read_count(
            gate.get("candidate_diagnostic_read_count")
        )
        and gate.get("candidate_diagnostic_read_count") == expected_count
    )
    if state_is_canonical:
        return mask, expected_count, True
    if (
        gate.get("schema_version") != MUTATION_GATE_SCHEMA
        or not high_water_is_canonical
    ):
        return (
            _CANDIDATE_DIAGNOSTIC_HIGH_WATER_FULL,
            _MAX_CANDIDATE_DIAGNOSTIC_READS,
            False,
        )
    if candidate_binding_is_canonical:
        mask = _candidate_diagnostic_fail_closed(mask, candidate_fingerprint)
    return mask, _MAX_CANDIDATE_DIAGNOSTIC_READS, False


def _normalized_exhausted_rejection_state(
    gate: Mapping[str, Any],
    candidate_fingerprint: Any,
) -> tuple[int, int, bool, bool]:
    """Return candidate-bound rejected-batch state without minting runway."""

    if not _is_canonical_semantic_fingerprint(candidate_fingerprint):
        raw_high_water = gate.get("candidate_rejection_high_water")
        high_water, canonical = _candidate_diagnostic_high_water(raw_high_water)
        if raw_high_water is None:
            high_water, canonical = 0, True
        elif not canonical:
            high_water = _CANDIDATE_DIAGNOSTIC_HIGH_WATER_FULL
        return (
            high_water,
            _MAX_EXHAUSTED_REJECTED_BATCHES,
            True,
            False,
        )
    raw_high_water = gate.get("candidate_rejection_high_water")
    high_water, high_water_is_canonical = _candidate_diagnostic_high_water(
        raw_high_water
    )
    binding = gate.get("candidate_exhausted_rejection_fingerprint")
    count = gate.get("candidate_exhausted_rejection_count")
    latched = gate.get("candidate_tool_free_latched")
    if (
        raw_high_water is None
        and binding is None
        and count is None
        and latched is None
    ):
        # Additive migration from the first v4 release.  This grants no Tool
        # execution authority; it only preserves the intended two rejected
        # attempts before bounded Tool-free convergence.
        return 0, 0, False, True
    if not high_water_is_canonical:
        return (
            _CANDIDATE_DIAGNOSTIC_HIGH_WATER_FULL,
            _MAX_EXHAUSTED_REJECTED_BATCHES,
            True,
            False,
        )
    expected_count = _candidate_rejection_count(
        high_water,
        candidate_fingerprint,
    )
    if binding != candidate_fingerprint:
        return (
            high_water,
            expected_count,
            expected_count >= _MAX_EXHAUSTED_REJECTED_BATCHES,
            True,
        )
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 0 <= count <= _MAX_EXHAUSTED_REJECTED_BATCHES
        or not isinstance(latched, bool)
        or count != expected_count
        or latched != (count >= _MAX_EXHAUSTED_REJECTED_BATCHES)
    ):
        high_water = _candidate_rejection_fail_closed(
            high_water,
            candidate_fingerprint,
        )
        return high_water, _MAX_EXHAUSTED_REJECTED_BATCHES, True, False
    return high_water, count, latched, True


def _normalized_pre_candidate_rejection_state(
    gate: Mapping[str, Any],
) -> tuple[int, bool, bool]:
    """Return the bounded pre-candidate rejected-batch latch state."""

    count = gate.get("pre_candidate_rejected_batch_count")
    latched = gate.get("pre_candidate_tool_free_latched")
    required_public_candidate_missing = bool(
        gate.get("public_deliverable_declared") is True
        and gate.get("candidate_present") is False
    )
    expected_latched = bool(
        isinstance(count, int)
        and not isinstance(count, bool)
        and count >= _MAX_PRE_CANDIDATE_REJECTED_CALLS
        and not required_public_candidate_missing
    )
    if count is None and latched is None:
        return 0, False, True
    if (
        isinstance(count, int)
        and not isinstance(count, bool)
        and 0 <= count <= _MAX_PRE_CANDIDATE_REJECTED_CALLS
        and isinstance(latched, bool)
        and latched == expected_latched
    ):
        return count, latched, True
    # Corrupted framework state cannot mint fresh Tool runway.
    return _MAX_PRE_CANDIDATE_REJECTED_CALLS, True, False


def _candidate_rejection_slot_fingerprint(
    candidate_fingerprint: str,
    slot: int,
) -> str:
    from aworld.core.context.compiler import semantic_fingerprint

    return semantic_fingerprint(
        {
            "candidate_fingerprint": candidate_fingerprint,
            "kind": "candidate_exhausted_rejection",
            "slot": slot,
        }
    )


def _candidate_rejection_count(mask: int, candidate_fingerprint: str) -> int:
    return sum(
        _repair_evidence_seen(
            mask,
            _candidate_rejection_slot_fingerprint(candidate_fingerprint, slot),
        )
        for slot in range(_MAX_EXHAUSTED_REJECTED_BATCHES)
    )


def _candidate_rejection_fail_closed(
    mask: int,
    candidate_fingerprint: str,
) -> int:
    for slot in range(_MAX_EXHAUSTED_REJECTED_BATCHES):
        mask = _repair_evidence_add(
            mask,
            _candidate_rejection_slot_fingerprint(candidate_fingerprint, slot),
        )
    return mask


def _normalized_pure_rejection_state(
    gate: Mapping[str, Any],
    candidate_fingerprint: Any,
) -> tuple[int, bool, bool]:
    """Return consecutive diagnostic-independent rejected-batch state."""

    if not _is_canonical_semantic_fingerprint(candidate_fingerprint):
        return 0, False, False
    binding = gate.get("candidate_pure_rejection_fingerprint")
    count = gate.get("candidate_pure_rejection_count")
    latched = gate.get("candidate_pure_rejection_latched")
    if binding is None and count is None and latched is None:
        return 0, False, True
    if binding != candidate_fingerprint:
        return 0, False, True
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 0 <= count <= _MAX_POST_CANDIDATE_PURE_REJECTED_BATCHES
        or not isinstance(latched, bool)
        or latched
        != (count >= _MAX_POST_CANDIDATE_PURE_REJECTED_BATCHES)
    ):
        return (
            _MAX_POST_CANDIDATE_PURE_REJECTED_BATCHES,
            True,
            False,
        )
    return count, latched, True


def constrain_candidate_convergence_tool_catalog(
    context,
    agent_id: str,
    tools: Sequence[Mapping[str, Any]] | None,
    *,
    tool_identity_mapping: Mapping[str, str] | None = None,
) -> list[Mapping[str, Any]] | None:
    """Read durable protocol state without creating a late finalization race.

    The sole latch-to-FINALIZE transition runs at the earlier pre-generation
    boundary.  Tool discovery may execute arbitrary framework code and race
    with another provider's gate projection, so this late catalog pass never
    mutates protocol state or trusts a gate latch by itself.
    """

    del tool_identity_mapping
    if tools is None:
        return None
    original = list(tools)
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is not ProtocolMode.GUIDE:
        return original
    from aworld.core.execution_protocol import ProtocolPhase

    state = ExecutionProtocolStore(context, agent_id, policy).load()
    return [] if state.phase is ProtocolPhase.FINALIZE else original


def _normalized_repair_authorization(
    value: Any,
    *,
    scope_hash: Any,
    candidate_fingerprint: Any,
) -> dict[str, Any] | None:
    """Return only exact, current, typed repair authority.

    Repair authorization crosses the Tool admission boundary. Legacy, partial,
    or internally inconsistent dictionaries therefore fail closed rather than
    gaining authority through additive normalization.
    """

    if (
        not isinstance(value, Mapping)
        or set(value) != _REPAIR_AUTHORIZATION_FIELDS
        or value.get("schema_version") != _REPAIR_AUTHORIZATION_SCHEMA
        or value.get("source") not in _REPAIR_AUTHORIZATION_SOURCES
        or value.get("used") is not False
        or not _is_canonical_semantic_fingerprint(scope_hash)
        or not _is_canonical_semantic_fingerprint(candidate_fingerprint)
        or not _is_canonical_semantic_fingerprint(
            value.get("failure_evidence_hash")
        )
        or value.get("scope_hash") != scope_hash
        or value.get("candidate_fingerprint") != candidate_fingerprint
    ):
        return None
    return {key: value[key] for key in _REPAIR_AUTHORIZATION_FIELDS}


def _gate_matches_current_scope(
    context,
    agent_id: str,
    gate: Any,
) -> bool:
    if not isinstance(gate, Mapping) or gate.get("agent_id") != agent_id:
        return False
    schema = gate.get("schema_version")
    if schema in {MUTATION_GATE_SCHEMA, "aworld.mutation-gate/v3"}:
        from aworld.core.context.compiler import semantic_fingerprint

        return gate.get("scope_hash") == semantic_fingerprint(
            _model_decision_scope(context, agent_id)
        )
    if schema in {"aworld.mutation-gate/v1", "aworld.mutation-gate/v2"}:
        owner = state_context(context)
        return bool(
            gate.get("task_id") == getattr(owner, "task_id", None)
            and gate.get("task_epoch") == getattr(owner, "task_epoch", None)
        )
    return False


def _gate_index_scope_hash(context) -> str:
    from aworld.core.context.compiler import semantic_fingerprint

    owner = state_context(context)
    return semantic_fingerprint(
        {
            "task_id": getattr(owner, "task_id", None),
            "task_epoch": getattr(owner, "task_epoch", None),
        }
    )


def _update_active_gate_index(context, agent_id: str, *, active: bool) -> None:
    """Maintain a bounded task-level index for identity-ambiguous batches."""

    owner = state_context(context)
    if owner is None:
        return
    scope_hash = _gate_index_scope_hash(context)

    def update(current):
        active_ids = []
        overflow = False
        if (
            isinstance(current, Mapping)
            and current.get("schema_version") == "aworld.mutation-gate-index/v1"
            and current.get("scope_hash") == scope_hash
        ):
            active_ids = [
                value
                for value in (current.get("active_agent_ids") or ())
                if isinstance(value, str) and value
            ][:32]
            overflow = current.get("overflow_active") is True
        if active:
            if agent_id not in active_ids:
                if len(active_ids) < 32:
                    active_ids.append(agent_id)
                else:
                    overflow = True
        else:
            active_ids = [value for value in active_ids if value != agent_id]
            # Overflow is conservative: an unindexed active agent may exist.
        return {
            "schema_version": "aworld.mutation-gate-index/v1",
            "scope_hash": scope_hash,
            "active_agent_ids": active_ids,
            "overflow_active": overflow,
        }

    _update_runtime_value(
        context,
        _MUTATION_GATE_INDEX_NAMESPACE,
        MUTATION_GATE_ACTIVE_INDEX_KEY,
        update,
    )


def _active_gate_index(context) -> Mapping[str, Any] | None:
    current = _read_runtime_value(
        context,
        _MUTATION_GATE_INDEX_NAMESPACE,
        MUTATION_GATE_ACTIVE_INDEX_KEY,
    )
    if (
        not isinstance(current, Mapping)
        or current.get("schema_version") != "aworld.mutation-gate-index/v1"
        or current.get("scope_hash") != _gate_index_scope_hash(context)
    ):
        return None
    return current


def _indexed_active_gate_agents(context) -> tuple[str, ...]:
    current = _active_gate_index(context)
    if current is None:
        return ()
    return tuple(
        value
        for value in (current.get("active_agent_ids") or ())
        if isinstance(value, str) and value
    )[:32]


def _indexed_active_gate_overflow(context) -> bool:
    current = _active_gate_index(context)
    return bool(current is not None and current.get("overflow_active") is True)


def _model_decision_scope(context, agent_id: str) -> dict[str, Any]:
    owner = state_context(context)
    return {
        "task_id": getattr(owner, "task_id", None),
        "task_epoch": getattr(owner, "task_epoch", None),
        "agent_id": agent_id,
    }


def _normalized_decision_attempts(
    value: Any, *, expected_scope: Mapping[str, Any]
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != "aworld.model-decision-attempts/v1"
        or value.get("scope") != dict(expected_scope)
    ):
        return {
            "schema_version": "aworld.model-decision-attempts/v1",
            "scope": dict(expected_scope),
            "initial": {},
            "replan": {},
            "consecutive_unapplied_replans": 0,
            "suppressed_replan_boundaries": 0,
            "planning_semantic_high_water": [],
        }
    planning_semantic_high_water = [
        item
        for item in (value.get("planning_semantic_high_water") or ())
        if _is_canonical_semantic_fingerprint(item)
    ][:16]
    return {
        "schema_version": "aworld.model-decision-attempts/v1",
        "scope": dict(expected_scope),
        "initial": dict(value.get("initial") or {}),
        "replan": dict(value.get("replan") or {}),
        "consecutive_unapplied_replans": _bounded_counter(
            value.get("consecutive_unapplied_replans")
        ),
        "suppressed_replan_boundaries": _bounded_counter(
            value.get("suppressed_replan_boundaries")
        ),
        "planning_semantic_high_water": list(
            dict.fromkeys(planning_semantic_high_water)
        ),
    }


def _planning_semantic_fingerprint(update: ModelPlanUpdate) -> str | None:
    """Identify an executable plan shape without raw Tool arguments.

    Exact argument signatures are intentionally excluded: changing a flag or
    spelling the same command differently is not planning progress.  The
    framework credits only a previously unseen capability/effect/target/intent
    shape, and retains a bounded high-water set so alternating old shapes
    cannot reset the convergence counter forever.
    """

    receipt = update.next_action_semantics
    if receipt is None or not receipt.observable or not receipt.capability_aliases:
        return None
    if update.delivery_intent is DeliveryIntent.PRODUCE_CANDIDATE:
        if receipt.effect != "mutating":
            return None
    elif update.delivery_intent is DeliveryIntent.VALIDATE_CANDIDATE:
        if receipt.effect != "validation" or receipt.validation_kind is None:
            return None
    elif update.delivery_intent is not DeliveryIntent.CONTINUE_EXPLORATION:
        return None
    payload = json.dumps(
        {
            "capability_aliases": sorted(set(receipt.capability_aliases)),
            "declared_deliverable_targeted": (
                receipt.declared_deliverable_targeted
            ),
            "delivery_intent": update.delivery_intent.value,
            "effect": receipt.effect,
            "target_ids": sorted(set(receipt.target_ids)),
            "validation_kind": receipt.validation_kind,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _missing_public_deliverable_names(context) -> tuple[str, ...]:
    from aworld.runners.public_deliverables import inspect_public_deliverable

    value = getattr(context, "context_info", {}).get("public_deliverable_contract")
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != _PUBLIC_DELIVERABLE_SCHEMA
        or value.get("authority") != _PUBLIC_DELIVERABLE_AUTHORITY
        or value.get("source") != "public_task_text"
    ):
        return ()
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) > 16:
        return ()
    missing = []
    for item in artifacts:
        if (
            not isinstance(item, Mapping)
            or item.get("kind") != "file"
            or item.get("authority") != _PUBLIC_DELIVERABLE_AUTHORITY
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("display_path"), str)
        ):
            return ()
        if not inspect_public_deliverable(item["path"]).candidate_eligible:
            missing.append(item["display_path"])
    return tuple(missing)


def _public_delivery_status(context) -> dict[str, Any]:
    """Return bounded, minimally inspectable public candidate presence."""

    from aworld.runners.public_deliverables import inspect_public_deliverable

    value = getattr(context, "context_info", {}).get("public_deliverable_contract")
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != _PUBLIC_DELIVERABLE_SCHEMA
        or value.get("authority") != _PUBLIC_DELIVERABLE_AUTHORITY
        or value.get("source") != "public_task_text"
    ):
        return {
            "public_deliverable_declared": False,
            "missing_public_deliverable_count": 0,
            "candidate_present": None,
        }
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 16:
        return {
            "public_deliverable_declared": False,
            "missing_public_deliverable_count": 0,
            "candidate_present": None,
        }
    existing = 0
    for item in artifacts:
        if (
            not isinstance(item, Mapping)
            or item.get("kind") != "file"
            or item.get("authority") != _PUBLIC_DELIVERABLE_AUTHORITY
            or not isinstance(item.get("path"), str)
        ):
            return {
                "public_deliverable_declared": False,
                "missing_public_deliverable_count": 0,
                "candidate_present": None,
            }
        existing += int(
            inspect_public_deliverable(item["path"]).candidate_eligible
        )
    return {
        "public_deliverable_declared": True,
        "missing_public_deliverable_count": len(artifacts) - existing,
        # One real named output is sufficient to establish an inspectable
        # candidate. It is not evidence that every deliverable is complete.
        "candidate_present": existing > 0,
    }


def _context_key(base: str, agent_id: str) -> str:
    return f"{base}:{agent_id}"


def _project_runtime_value(owner, agent_id: str, key: str, value: Any) -> None:
    """Project one bounded runtime value into checkpointed task state."""
    scoped_key = _context_key(key, agent_id)
    context_info = getattr(owner, "context_info", None)
    if isinstance(context_info, dict):
        if value is None:
            context_info.pop(scoped_key, None)
        else:
            context_info[scoped_key] = deepcopy(value)
    task_state = getattr(owner, "task_state", None)
    working_state = getattr(task_state, "working_state", None)
    kv_store = getattr(working_state, "kv_store", None)
    if isinstance(kv_store, dict):
        if value is None:
            kv_store.pop(scoped_key, None)
        else:
            kv_store[scoped_key] = deepcopy(value)
        return
    put_working_state = getattr(owner, "put", None)
    if callable(put_working_state):
        put_working_state(scoped_key, deepcopy(value))


def _read_projected_runtime_value(owner, agent_id: str, key: str) -> Any:
    """Read checkpointed state without consulting the in-process registry."""
    scoped_key = _context_key(key, agent_id)
    task_state = getattr(owner, "task_state", None)
    working_state = getattr(task_state, "working_state", None)
    kv_store = getattr(working_state, "kv_store", None)
    if isinstance(kv_store, Mapping) and scoped_key in kv_store:
        return deepcopy(kv_store.get(scoped_key))
    context_info = getattr(owner, "context_info", None)
    if isinstance(context_info, Mapping) and scoped_key in context_info:
        return deepcopy(context_info.get(scoped_key))
    return None


def _write_runtime_value(context, agent_id: str, key: str, value: Any) -> None:
    owner = state_context(context)
    if owner is None:
        return
    writer = getattr(owner, "write_task_runtime_state", None)
    if callable(writer):
        try:
            writer(agent_id, key, deepcopy(value))
        except Exception:
            pass
    try:
        _project_runtime_value(owner, agent_id, key, value)
    except Exception:
        pass


def _read_runtime_value(context, agent_id: str, key: str) -> Any:
    owner = state_context(context)
    if owner is None:
        return None
    reader = getattr(owner, "read_task_runtime_state", None)
    if callable(reader):
        try:
            value = reader(agent_id, key)
        except Exception:
            value = None
        if value is not None:
            return value
    context_info = getattr(owner, "context_info", None)
    value = (
        context_info.get(_context_key(key, agent_id))
        if isinstance(context_info, Mapping)
        else None
    )
    if value is not None:
        return value
    # Checkpoint restore must not instantiate ApplicationContext services just
    # to read its serialized WorkingState. Service initialization can import
    # optional MCP dependencies that are intentionally absent in core-only
    # environments.
    task_state = getattr(owner, "task_state", None)
    working_state = getattr(task_state, "working_state", None)
    kv_store = getattr(working_state, "kv_store", None)
    scoped_key = _context_key(key, agent_id)
    if isinstance(kv_store, Mapping) and scoped_key in kv_store:
        return kv_store.get(scoped_key)
    get_working_state = getattr(owner, "get", None)
    if callable(get_working_state):
        try:
            return get_working_state(scoped_key)
        except Exception:
            pass
    return None


def _update_runtime_value(context, agent_id: str, key: str, update) -> Any:
    """Atomically fan in one bounded runtime-state mutation when supported."""
    owner = state_context(context)
    if owner is None:
        return None
    registry_owner_resolver = getattr(owner, "_task_runtime_registry_owner", None)
    durable_owner = (
        registry_owner_resolver() if callable(registry_owner_resolver) else owner
    )
    atomic_updater = getattr(owner, "update_and_project_task_runtime_state", None)
    if callable(atomic_updater):
        durable_seed = _read_projected_runtime_value(durable_owner, agent_id, key)

        def seeded_update(current):
            return update(deepcopy(durable_seed) if current is None else current)

        try:
            value = atomic_updater(
                agent_id,
                key,
                seeded_update,
                lambda projected: _project_runtime_value(
                    durable_owner, agent_id, key, projected
                ),
            )
            if owner is not durable_owner:
                _project_runtime_value(owner, agent_id, key, value)
            return value
        except Exception:
            return _read_runtime_value(owner, agent_id, key)
    updater = getattr(owner, "update_task_runtime_state", None)
    if callable(updater):
        try:
            value = updater(agent_id, key, update)
            _project_runtime_value(durable_owner, agent_id, key, value)
            if owner is not durable_owner:
                _project_runtime_value(owner, agent_id, key, value)
            return value
        except Exception:
            pass
    value = update(_read_runtime_value(owner, agent_id, key))
    _write_runtime_value(owner, agent_id, key, value)
    return value


def configure_execution_protocol(
    context, agent_id: str, policy: ExecutionProtocolPolicy
) -> None:
    """Publish a validated policy for Tool-boundary transport copies."""
    if not isinstance(policy, ExecutionProtocolPolicy):
        raise TypeError("policy must be ExecutionProtocolPolicy")
    acceptance_flag = os.environ.get(INDEPENDENT_ACCEPTANCE_CRITIC_ENV)
    semantic_flag = os.environ.get(SEMANTIC_PROGRESS_LEDGER_ENV)
    owner = state_context(context)
    contract = (
        getattr(owner, "completion_contract", None) if owner is not None else None
    )
    trusted_validation_available = bool(
        getattr(contract, "validation_commands", ()) or ()
    )
    review_timeout = policy.final_review_timeout_seconds
    if review_timeout is None:
        get_task = getattr(owner, "get_task", None) if owner is not None else None
        try:
            task = get_task() if callable(get_task) else None
            total = getattr(task, "timeout", None) if task is not None else None
            external_reserve = (
                getattr(task, "completion_reserve_seconds", 0.0)
                if task is not None
                else 0.0
            )
        except Exception:
            total = None
            external_reserve = 0.0
        if (
            isinstance(total, (int, float))
            and not isinstance(total, bool)
            and total > 0
        ):
            if (
                not isinstance(external_reserve, (int, float))
                or isinstance(external_reserve, bool)
                or external_reserve < 0
            ):
                external_reserve = 0.0
            caller_available = max(0.0, float(total) - float(external_reserve))
            review_timeout = min(180.0, max(60.0, caller_available * 0.05))
        else:
            # Contexts without a typed Task deadline still need a finite review
            # episode.  Explicit caller policy continues to win above.
            review_timeout = 120.0
    policy = replace(
        policy,
        independent_acceptance_enabled=(
            policy.independent_acceptance_enabled
            and trusted_validation_available
            and not (
                acceptance_flag is not None
                and acceptance_flag.strip().lower() in {"0", "false", "no", "off"}
            )
        ),
        semantic_progress_enabled=(
            policy.semantic_progress_enabled
            and not (
                semantic_flag is not None
                and semantic_flag.strip().lower() in {"0", "false", "no", "off"}
            )
        ),
        final_review_timeout_seconds=review_timeout,
    )
    _write_runtime_value(
        context, agent_id, EXECUTION_PROTOCOL_POLICY_KEY, policy.to_dict()
    )


def record_tool_hypotheses(
    context, agent_id: str, hypotheses: Mapping[str, str]
) -> None:
    """Retain bounded, hashed hypothesis identities by tool call id."""
    from aworld.core.context.compiler import semantic_fingerprint

    bounded = {
        call_id: semantic_fingerprint(hypothesis)
        for call_id, hypothesis in hypotheses.items()
        if isinstance(call_id, str)
        and 0 < len(call_id) <= 256
        and isinstance(hypothesis, str)
        and hypothesis.strip()
    }
    bounded = dict(list(bounded.items())[-32:])
    _write_runtime_value(context, agent_id, EXECUTION_PROTOCOL_HYPOTHESES_KEY, bounded)
    owner = state_context(context)
    if owner is not None:
        owner.context_info[f"execution_protocol_hypotheses:{agent_id}"] = bounded


def _public_request_hash(context) -> str:
    from aworld.core.context.compiler import semantic_fingerprint

    owner = state_context(context)
    task = None
    getter = getattr(owner, "get_task", None) if owner is not None else None
    try:
        task = getter() if callable(getter) else None
    except Exception:
        task = None
    request = getattr(task, "input", None) or getattr(owner, "task_input", None) or ""
    return semantic_fingerprint(str(request))


def _public_candidate_binding(context, agent_id: str) -> tuple[str, str | None]:
    from aworld.core.context.compiler import semantic_fingerprint

    fallback = load_candidate_fallback(context, agent_id) or ()
    candidate = str(getattr(fallback[0], "policy_info", "") or "") if fallback else ""
    policy = execution_protocol_policy(context, agent_id)
    protocol_state = ExecutionProtocolStore(context, agent_id, policy).load()
    selected_candidate_id = (
        protocol_state.model_plan_update.selected_candidate_id
        if protocol_state.model_plan_update is not None
        else None
    )
    return (
        semantic_fingerprint(
            {
                "candidate_response_hash": semantic_fingerprint(candidate),
                "selected_candidate_id": selected_candidate_id,
            }
        ),
        selected_candidate_id,
    )


def _public_probe_scope(context, agent_id: str) -> dict[str, Any]:
    owner = state_context(context)
    return {
        "task_id": str(getattr(owner, "task_id", "") or ""),
        "task_epoch": int(getattr(owner, "task_epoch", 0) or 0),
        "agent_id": agent_id,
    }


def _normalize_public_probe_state(
    value: Any, *, expected_scope: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != "aworld.public-probe-ledger/v1"
        or (expected_scope is not None and value.get("scope") != dict(expected_scope))
    ):
        return {
            "schema_version": "aworld.public-probe-ledger/v1",
            "scope": dict(expected_scope or {}),
            "revision": 0,
            "plans": {},
            "receipts": [],
        }
    plans = value.get("plans")
    receipts = value.get("receipts")
    return {
        "schema_version": "aworld.public-probe-ledger/v1",
        "scope": dict(value.get("scope") or expected_scope or {}),
        "revision": int(value.get("revision", 0) or 0),
        "plans": dict(plans) if isinstance(plans, Mapping) else {},
        "receipts": list(receipts) if isinstance(receipts, list) else [],
    }


def _public_probe_state(context, agent_id: str) -> dict[str, Any]:
    return _normalize_public_probe_state(
        _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_PUBLIC_PROBES_KEY),
        expected_scope=_public_probe_scope(context, agent_id),
    )


def record_public_probe_plan(
    context,
    agent_id: str,
    *,
    tool_call_id: str,
    tool_identity: str,
    arguments_projection: Mapping[str, Any],
    value: Mapping[str, Any],
) -> bool:
    """Bind one model-designed public self-check to a real Tool call."""
    from aworld.core.context.compiler import semantic_fingerprint

    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return False
    if (
        not isinstance(value, Mapping)
        or set(value)
        != {
            "hypothesis_id",
            "highest_risk_counterexample",
            "probe_kind",
        }
        or not isinstance(tool_call_id, str)
        or not tool_call_id
        or len(tool_call_id) > 256
        or not isinstance(tool_identity, str)
        or not tool_identity.strip()
        or len(tool_identity) > 256
        or not isinstance(arguments_projection, Mapping)
    ):
        return False
    hypothesis_id = value.get("hypothesis_id")
    counterexample = value.get("highest_risk_counterexample")
    probe_kind = value.get("probe_kind")
    if (
        not isinstance(hypothesis_id, str)
        or not hypothesis_id.strip()
        or len(hypothesis_id.strip()) > 128
        or not isinstance(counterexample, str)
        or not counterexample.strip()
        or len(counterexample.strip()) > 1024
        or probe_kind not in {"smoke", "regression", "counterexample", "invariant"}
    ):
        return False
    normalized_tool_identity = ":".join(
        part.strip().casefold() for part in tool_identity.split(":", 1)
    )
    candidate_hash, selected_candidate_id = _public_candidate_binding(context, agent_id)
    plan = {
        "tool_call_id": tool_call_id,
        "tool_identity": normalized_tool_identity,
        "arguments_projection": _bounded_probe_value(arguments_projection),
        "arguments_hash": semantic_fingerprint(arguments_projection),
        "hypothesis_id": hypothesis_id.strip(),
        "highest_risk_counterexample": counterexample.strip(),
        "probe_kind": probe_kind,
        "selected_candidate_id": selected_candidate_id,
        "request_hash": _public_request_hash(context),
        "candidate_hash": candidate_hash,
    }
    recorded = False
    expected_scope = _public_probe_scope(context, agent_id)

    def add_plan(current):
        nonlocal recorded
        state = _normalize_public_probe_state(current, expected_scope=expected_scope)
        plans = state["plans"]
        if tool_call_id in plans:
            return state
        plans[tool_call_id] = plan
        state["plans"] = dict(list(plans.items())[-16:])
        state["revision"] = int(state.get("revision", 0) or 0) + 1
        recorded = True
        return state

    _update_runtime_value(
        context,
        agent_id,
        EXECUTION_PROTOCOL_PUBLIC_PROBES_KEY,
        add_plan,
    )
    return recorded


def record_public_probe_observations(
    context,
    agent_id: str,
    *,
    actions: Any,
    result_projections: Any,
    artifact_after: Any = None,
) -> int:
    """Create advisory receipts from separately observed Tool results."""
    from aworld.core.context.compiler import semantic_fingerprint

    action_by_id = {
        getattr(action, "tool_call_id", None): action
        for action in actions or ()
        if isinstance(getattr(action, "tool_call_id", None), str)
    }
    result_by_id = {
        item.get("tool_call_id"): item
        for item in result_projections or ()
        if isinstance(item, Mapping) and isinstance(item.get("tool_call_id"), str)
    }
    observed_ids: set[str] = set()
    expected_scope = _public_probe_scope(context, agent_id)

    def add_observations(current):
        state = _normalize_public_probe_state(current, expected_scope=expected_scope)
        plans = state["plans"]
        receipts = [item for item in state["receipts"] if isinstance(item, Mapping)]
        observed_ids.clear()
        for tool_call_id, plan in list(plans.items()):
            action = action_by_id.get(tool_call_id)
            result = result_by_id.get(tool_call_id)
            if action is None or result is None or not isinstance(plan, Mapping):
                continue
            actual_identity = ":".join(
                part.strip().casefold()
                for part in (
                    str(getattr(action, "tool_name", "") or ""),
                    str(getattr(action, "action_name", "") or ""),
                )
            )
            actual_arguments = getattr(action, "params", None)
            if (
                actual_identity != plan.get("tool_identity")
                or not isinstance(actual_arguments, Mapping)
                or semantic_fingerprint(actual_arguments) != plan.get("arguments_hash")
            ):
                continue
            artifact_after_hash = semantic_fingerprint(artifact_after)
            receipt_core = {
                "schema_version": "aworld.public-probe-receipt/v1",
                "authority": "agent_self_check",
                "task_reward": "not_assessed",
                "hypothesis_id": plan.get("hypothesis_id"),
                "highest_risk_counterexample": plan.get("highest_risk_counterexample"),
                "probe_kind": plan.get("probe_kind"),
                "selected_candidate_id": plan.get("selected_candidate_id"),
                "tool_identity": plan.get("tool_identity"),
                "arguments_hash": plan.get("arguments_hash"),
                "request_hash": plan.get("request_hash"),
                "candidate_hash": plan.get("candidate_hash"),
                "artifact_after_hash": artifact_after_hash,
                "artifact_bound": artifact_after is not None,
                "result_hash": semantic_fingerprint(result),
                "tool_execution_succeeded": result.get("success") is True,
                "probe_assessment": "unassessed",
                "failure_code": (
                    str(result.get("failure_code"))[:256]
                    if result.get("failure_code") is not None
                    else None
                ),
            }
            receipt_core["receipt_id"] = semantic_fingerprint(receipt_core)
            receipts.append(receipt_core)
            plans.pop(tool_call_id, None)
            observed_ids.add(tool_call_id)
        state["plans"] = dict(list(plans.items())[-16:])
        state["receipts"] = receipts[-16:]
        state["revision"] = int(state.get("revision", 0) or 0) + 1
        return state

    _update_runtime_value(
        context,
        agent_id,
        EXECUTION_PROTOCOL_PUBLIC_PROBES_KEY,
        add_observations,
    )
    return len(observed_ids)


def load_public_probe_receipts(context, agent_id: str) -> list[dict[str, Any]]:
    """Return bounded advisory receipts with current-staleness projections."""
    from aworld.core.context.compiler import semantic_fingerprint

    state = _public_probe_state(context, agent_id)
    request_hash = _public_request_hash(context)
    candidate_hash, _ = _public_candidate_binding(context, agent_id)
    try:
        from aworld.runners.post_tool_progress import semantic_progress_for_agent

        semantic_state = semantic_progress_for_agent(context, agent_id=agent_id)
    except Exception:
        semantic_state = {}
    current_artifact = semantic_state.get("artifact_fingerprint")
    current_artifact_hash = semantic_fingerprint(current_artifact)
    output = []
    for raw in state["receipts"][-16:]:
        if not isinstance(raw, Mapping):
            continue
        receipt = dict(raw)
        request_current = receipt.get("request_hash") == request_hash
        candidate_current = receipt.get("candidate_hash") == candidate_hash
        artifact_bound = receipt.get("artifact_bound") is True
        artifact_current = not artifact_bound or (
            current_artifact is not None
            and receipt.get("artifact_after_hash") == current_artifact_hash
        )
        receipt.update(
            {
                "request_current": request_current,
                "candidate_current": candidate_current,
                "artifact_current": artifact_current,
                "stale": not (
                    request_current and candidate_current and artifact_current
                ),
            }
        )
        output.append(receipt)
    return output


def acceptance_critic_active(context, agent_id: str) -> bool:
    raw = os.environ.get(INDEPENDENT_ACCEPTANCE_CRITIC_ENV)
    if raw is not None and raw.strip().lower() in {"0", "false", "no", "off"}:
        return False
    policy = execution_protocol_policy(context, agent_id)
    if not policy.independent_acceptance_enabled or policy.mode is ProtocolMode.OFF:
        return False
    return ExecutionProtocolStore(context, agent_id, policy).load().review_pending


def model_owned_review_active(context, agent_id: str) -> bool:
    """Return whether a non-critic model-owned review is currently pending."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF or policy.independent_acceptance_enabled:
        return False
    return ExecutionProtocolStore(context, agent_id, policy).load().review_pending


def _bounded_probe_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        from aworld.core.context.compiler import semantic_fingerprint

        return {"value_hash": semantic_fingerprint(value)}
    if isinstance(value, Mapping):
        return {
            str(key): (
                "<redacted>"
                if any(
                    token in str(key).lower()
                    for token in ("secret", "token", "password", "api_key")
                )
                else _bounded_probe_value(item, depth=depth + 1)
            )
            for key, item in list(value.items())[:24]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_probe_value(item, depth=depth + 1) for item in value[:16]]
    if isinstance(value, str):
        bounded = value[:2048]
        bounded = re.sub(
            r"(?i)\b(api[_-]?key|token|password|secret)\s*=\s*[^\s;&|]+",
            r"\1=<redacted>",
            bounded,
        )
        return re.sub(r"(?i)\bBearer\s+[^\s]+", "Bearer <redacted>", bounded)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:512]


def _acceptance_candidate_and_evidence(
    context, agent_id: str
) -> tuple[str, dict[str, Any]]:
    fallback = load_candidate_fallback(context, agent_id) or ()
    candidate = str(getattr(fallback[0], "policy_info", "") or "") if fallback else ""
    try:
        from aworld.runners.post_tool_progress import semantic_progress_for_agent

        state = semantic_progress_for_agent(context, agent_id=agent_id)
    except Exception:
        state = {}
    evidence = {
        key: state.get(key)
        for key in (
            "artifact_fingerprint",
            "failure_signature",
            "completion_evidence_fingerprint",
            "goal_progress_count",
            "last_meaningful_progress_agent_step",
        )
        if state.get(key) is not None
    }
    return candidate, evidence


def _candidate_review_basis(
    context, agent_id: str, actions: Any = None
) -> str:
    """Hash only the candidate and durable validation basis for review loops."""

    from aworld.core.context.compiler import semantic_fingerprint

    candidate = ""
    for action in actions or ():
        text = str(_action_value(action, "policy_info") or "").strip()
        if text:
            candidate = text[:_MAX_FALLBACK_CHARS]
            break
    if not candidate:
        fallback = load_candidate_fallback(context, agent_id) or ()
        candidate = (
            str(getattr(fallback[0], "policy_info", "") or "")[:_MAX_FALLBACK_CHARS]
            if fallback
            else ""
        )
    try:
        from aworld.runners.post_tool_progress import semantic_progress_for_agent

        state = semantic_progress_for_agent(context, agent_id=agent_id)
    except Exception:
        state = {}
    evidence = {
        key: state.get(key)
        for key in (
            "artifact_fingerprint",
            "completion_evidence_fingerprint",
            "failure_signature",
            "public_delivery_versions",
            "public_probe_receipts",
        )
        if state.get(key) is not None
    }
    return semantic_fingerprint({"candidate": candidate, "evidence": evidence})


def _matching_completion_validation_id(
    context,
    arguments: Mapping[str, Any],
    *,
    tool_identity: str | None = None,
) -> str | None:
    if tool_identity is not None:
        if not isinstance(tool_identity, str) or ":" not in tool_identity:
            return None
        tool, operation = tool_identity.split(":", 1)
        normalized_tool = tool.strip().casefold().replace("_", "-")
        if normalized_tool == "mcp" and "__" in operation:
            tool, operation = operation.split("__", 1)
            normalized_tool = tool.strip().casefold().replace("_", "-")
        if normalized_tool not in {
            "terminal",
            "terminal-server",
            "docker",
            "docker-sandbox",
            "docker-sandbox-server",
        } or operation.strip().casefold() not in {"run_code", "execute"}:
            return None
    command_text = arguments.get("command") or arguments.get("code")
    if not isinstance(command_text, str) or not command_text.strip():
        return None
    owner = state_context(context)
    contract = (
        getattr(owner, "completion_contract", None) if owner is not None else None
    )
    for validation in getattr(contract, "validation_commands", ()) or ():
        argv = tuple(getattr(validation, "argv", ()) or ())
        if not argv:
            continue
        registered = (
            str(argv[-1])
            if len(argv) >= 2 and argv[-2] == "-c"
            else shlex.join(str(item) for item in argv)
        )
        command_id = getattr(validation, "command_id", None)
        cwd_matches = canonical_invocation_cwd(
            owner, arguments.get("cwd")
        ) == canonical_invocation_cwd(owner, getattr(validation, "cwd", None))
        if (
            command_text.strip() == registered.strip()
            and cwd_matches
            and isinstance(command_id, str)
        ):
            return command_id
    return None


def _action_value(action: Any, name: str) -> Any:
    return action.get(name) if isinstance(action, Mapping) else getattr(action, name, None)


def _action_identity(action: Any) -> str:
    return ":".join(
        part.strip().casefold()
        for part in (
            str(_action_value(action, "tool_name") or ""),
            str(_action_value(action, "action_name") or ""),
        )
    )


def _canonical_action_identity(action: Any) -> str:
    tool, operation = canonical_tool_identity(action)
    return f"{tool}:{operation}"


def _action_arguments(action: Any) -> Mapping[str, Any] | None:
    value = _action_value(action, "params")
    return value if isinstance(value, Mapping) else None


def _action_matches_bound_plan(action: Any, plan: Mapping[str, Any]) -> bool:
    """Match a framework-recorded plan to one exact provider Tool call."""

    from aworld.core.context.compiler import semantic_fingerprint

    arguments = _action_arguments(action)
    call_id = _action_value(action, "tool_call_id")
    return bool(
        isinstance(call_id, str)
        and call_id
        and call_id == plan.get("tool_call_id")
        and arguments is not None
        and _action_identity(action) == plan.get("tool_identity")
        and semantic_fingerprint(arguments) == plan.get("arguments_hash")
    )


def _action_matches_typed_validation_plan(
    context, agent_id: str, action: Any
) -> bool:
    """Match the current typed validate-candidate intent without plan prose."""

    state = load_execution_protocol_state(context, agent_id)
    update = state.model_plan_update
    arguments = _action_arguments(action)
    if (
        not state.next_action_alignment_pending
        or update is None
        or update.delivery_intent is not DeliveryIntent.VALIDATE_CANDIDATE
        or update.next_action_tool is None
        or update.next_action_signature is None
        or arguments is None
    ):
        return False
    call_id = _action_value(action, "tool_call_id")
    if (
        state.pending_next_action_call_id is not None
        and call_id != state.pending_next_action_call_id
    ):
        return False
    if update.next_action_semantics is not None:
        from aworld.sandbox.tool_observation import (
            build_preflight_action_semantic_receipt,
        )

        try:
            observed = build_preflight_action_semantic_receipt(
                context=context,
                action=action,
                delivery_intent=update.delivery_intent.value,
            )
        except (TypeError, ValueError):
            return False
        return (
            compare_action_semantic_shape(
                update.next_action_semantics,
                observed,
                intent=update.delivery_intent,
            )
            is NextActionAlignment.MATCHED
        )
    # Compatibility for persisted v1/v2 plan snapshots that predate semantic
    # receipts. New plans never use exact raw-argument signatures as policy.
    tool = str(_action_value(action, "tool_name") or "").strip()
    operation = str(_action_value(action, "action_name") or "").strip()
    model_visible = str(
        _action_value(action, "model_visible_tool_name") or ""
    ).strip()
    identities = {value for value in (model_visible, tool, operation) if value}
    if tool and operation:
        identities.add(f"{tool}__{operation}")
    if update.next_action_tool not in identities:
        return False
    try:
        observed_signature = action_signature(update.next_action_tool, arguments)
    except ValueError:
        return False
    return observed_signature == update.next_action_signature


def _action_matches_typed_candidate_plan(
    context,
    agent_id: str,
    action: Any,
    observed: ActionSemanticReceipt,
) -> bool:
    """Authorize a contractless mutation only from its exact model checkpoint.

    Without a trusted named target the framework cannot infer that an arbitrary
    write is a deliverable.  Require the pending model-owned produce-candidate
    semantics and the exact bound call ID; raw arguments are never persisted.
    """

    state = load_execution_protocol_state(context, agent_id)
    update = state.model_plan_update
    call_id = _action_value(action, "tool_call_id")
    if (
        update is None
        or update.delivery_intent is not DeliveryIntent.PRODUCE_CANDIDATE
        or update.next_action_semantics is None
        or not state.next_action_alignment_pending
        or not isinstance(call_id, str)
        or not call_id
        or state.pending_next_action_call_id != call_id
    ):
        return False
    expected_targets = update.next_action_semantics.target_ids
    observed_targets = observed.target_ids
    if not expected_targets or not observed_targets:
        if bool(expected_targets) != bool(observed_targets):
            return False
        if observed.effect == "unknown":
            try:
                if classify_tool_effect(action).effect != "mutating":
                    return False
            except (TypeError, ValueError):
                return False
        elif observed.effect != "mutating":
            return False
        arguments = _action_arguments(action)
        if arguments is None or update.next_action_signature is None:
            return False
        tool = str(_action_value(action, "tool_name") or "").strip()
        operation = str(_action_value(action, "action_name") or "").strip()
        model_visible = str(
            _action_value(action, "model_visible_tool_name") or ""
        ).strip()
        identities = {value for value in (model_visible, tool, operation) if value}
        if tool and operation:
            identities.add(f"{tool}__{operation}")
        if update.next_action_tool not in identities:
            return False
        try:
            observed_signature = action_signature(
                update.next_action_tool, arguments
            )
        except ValueError:
            return False
        return observed_signature == update.next_action_signature
    if observed.effect != "mutating":
        return False
    return (
        compare_action_semantic_shape(
            update.next_action_semantics,
            observed,
            intent=DeliveryIntent.PRODUCE_CANDIDATE,
        )
        is NextActionAlignment.MATCHED
    )


def framework_observable_validation_kind(
    context, agent_id: str, action: Any
) -> str | None:
    """Classify one exact read-only action that may cross an active gate.

    This is deliberately narrower than generic read-only classification.  A
    validation is authorized only when the framework can bind the eventual
    observation to a caller-registered command, a pending critic/public probe,
    or the exact typed ``validate_candidate`` action signature.
    """

    if not actions_are_provably_read_only([action]):
        return None
    arguments = _action_arguments(action)
    if arguments is None:
        return None

    critic = _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_CRITIC_KEY)
    if (
        isinstance(critic, Mapping)
        and critic.get("status") == "planned"
        and _action_matches_bound_plan(action, critic)
    ):
        return "pending_acceptance_critic_probe"

    public_state = _public_probe_state(context, agent_id)
    call_id = _action_value(action, "tool_call_id")
    public_plan = (
        public_state.get("plans", {}).get(call_id)
        if isinstance(call_id, str)
        and isinstance(public_state.get("plans"), Mapping)
        else None
    )
    if isinstance(public_plan, Mapping) and _action_matches_bound_plan(
        action, public_plan
    ):
        candidate_hash, _ = _public_candidate_binding(context, agent_id)
        if (
            public_plan.get("request_hash") == _public_request_hash(context)
            and public_plan.get("candidate_hash") == candidate_hash
        ):
            return "pending_public_validation_probe"

    if _action_matches_typed_validation_plan(context, agent_id, action):
        return "typed_validation_plan"
    if (
        _matching_completion_validation_id(
            context,
            arguments,
            tool_identity=_canonical_action_identity(action),
        )
        is not None
    ):
        return "registered_completion_validation"
    return None


def record_acceptance_probe_plan(
    context,
    agent_id: str,
    *,
    tool_call_id: str,
    hypothesis_id: str,
    highest_risk_counterexample: str,
    tool_identity: str,
    arguments_projection: Mapping[str, Any],
    probe_kind: str,
) -> bool:
    """Bind exactly one critic-selected probe to the pending review."""
    from aworld.core.context.compiler import semantic_fingerprint

    if not acceptance_critic_active(context, agent_id):
        return False
    if not all(
        isinstance(value, str) and value.strip()
        for value in (tool_call_id, hypothesis_id, highest_risk_counterexample)
    ):
        return False
    if not isinstance(tool_identity, str) or not tool_identity.strip():
        return False
    if not isinstance(arguments_projection, Mapping):
        return False
    from aworld.core.execution_protocol.acceptance import (
        probe_plan_is_framework_observable,
    )

    framework_validation_id = _matching_completion_validation_id(
        context,
        arguments_projection,
        tool_identity=tool_identity,
    )
    if not probe_plan_is_framework_observable(
        probe_kind=probe_kind,
        tool_identity=tool_identity,
        arguments=arguments_projection,
        trusted_validation_id=framework_validation_id,
    ):
        return False
    current = _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_CRITIC_KEY)
    if isinstance(current, Mapping) and current.get("status") in {
        "planned",
        "observed",
    }:
        return False
    candidate, evidence = _acceptance_candidate_and_evidence(context, agent_id)
    bounded_arguments = _bounded_probe_value(arguments_projection)
    normalized_tool_identity = ":".join(
        part.strip().casefold() for part in tool_identity.split(":", 1)
    )
    _write_runtime_value(
        context,
        agent_id,
        EXECUTION_PROTOCOL_CRITIC_KEY,
        {
            "schema_version": "aworld.acceptance-critic-state/v1",
            "status": "planned",
            "tool_call_id": tool_call_id[:256],
            "hypothesis_id": hypothesis_id.strip()[:128],
            "highest_risk_counterexample": highest_risk_counterexample.strip()[:1024],
            "tool_identity": normalized_tool_identity[:256],
            "arguments_projection": bounded_arguments,
            "arguments_hash": semantic_fingerprint(arguments_projection),
            "candidate": candidate[:64_000],
            "evidence": evidence,
            "artifact_before": evidence.get("artifact_fingerprint"),
            "probe_kind": probe_kind,
            "challenge": secrets.token_hex(16),
            "framework_validation_id": framework_validation_id,
        },
    )
    return True


def record_acceptance_probe_observation(
    context,
    agent_id: str,
    *,
    actions: Any,
    result_projection: Any,
    success: bool,
    failure_code: str | None,
    artifact_after: Any = None,
) -> bool:
    """Create a typed receipt from the separately observed Tool boundary."""
    from aworld.core.execution_protocol import AcceptanceProbeReceipt

    current = _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_CRITIC_KEY)
    if not isinstance(current, Mapping) or current.get("status") != "planned":
        return False
    actual_action = next(
        (
            action
            for action in actions or ()
            if getattr(action, "tool_call_id", None) == current.get("tool_call_id")
        ),
        None,
    )
    if actual_action is None:
        return False
    actual_identity = ":".join(
        part.strip().casefold()
        for part in (
            str(getattr(actual_action, "tool_name", "") or ""),
            str(getattr(actual_action, "action_name", "") or ""),
        )
    )
    actual_arguments = getattr(actual_action, "params", None)
    from aworld.core.context.compiler import semantic_fingerprint

    if (
        actual_identity != current.get("tool_identity")
        or not isinstance(actual_arguments, Mapping)
        or semantic_fingerprint(actual_arguments) != current.get("arguments_hash")
    ):
        return False
    if not isinstance(result_projection, Mapping) or result_projection.get(
        "tool_call_id"
    ) != current.get("tool_call_id"):
        return False
    from aworld.core.execution_protocol.acceptance import validate_probe_result

    framework_result_projection = dict(result_projection)
    framework_validation_id = current.get("framework_validation_id")
    if isinstance(framework_validation_id, str) and framework_validation_id:
        framework_result_projection["framework_validation_id"] = framework_validation_id
    validated, validation_code = validate_probe_result(
        probe_kind=current["probe_kind"],
        tool_identity=current["tool_identity"],
        arguments=current["arguments_projection"],
        result=framework_result_projection,
        artifact_after=artifact_after,
        evidence=current["evidence"],
    )
    receipt = AcceptanceProbeReceipt.build(
        hypothesis_id=current["hypothesis_id"],
        highest_risk_counterexample=current["highest_risk_counterexample"],
        tool_identity=current["tool_identity"],
        arguments_projection=current["arguments_projection"],
        candidate=current["candidate"],
        evidence=current["evidence"],
        artifact_before=current.get("artifact_before"),
        artifact_after=artifact_after,
        probe_kind=current["probe_kind"],
        challenge=current["challenge"],
        validation_code=validation_code,
        validated=validated,
        result_projection=framework_result_projection,
        success=success,
        failure_code=failure_code,
    )
    _write_runtime_value(
        context,
        agent_id,
        EXECUTION_PROTOCOL_CRITIC_KEY,
        {**dict(current), "status": "observed", "receipt": receipt.to_dict()},
    )
    return True


def load_acceptance_critic_state(context, agent_id: str) -> dict[str, Any]:
    value = _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_CRITIC_KEY)
    return dict(value) if isinstance(value, Mapping) else {}


def clear_acceptance_critic_state(context, agent_id: str) -> None:
    _write_runtime_value(context, agent_id, EXECUTION_PROTOCOL_CRITIC_KEY, None)


def _mint_review_repair_authorization(
    context,
    agent_id: str,
    *,
    source: str,
    evidence: Mapping[str, Any],
) -> None:
    """Serialize review-auth minting with diagnostic admission."""

    owner = state_context(context)
    transaction = getattr(owner, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            _mint_review_repair_authorization_locked(
                context,
                agent_id,
                source=source,
                evidence=evidence,
            )
        return
    _mint_review_repair_authorization_locked(
        context,
        agent_id,
        source=source,
        evidence=evidence,
    )


def _mint_review_repair_authorization_locked(
    context,
    agent_id: str,
    *,
    source: str,
    evidence: Mapping[str, Any],
) -> None:
    """Bind one repair use to fresh typed reviewer evidence and candidate."""

    gate = _read_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY)
    if (
        not isinstance(gate, Mapping)
        or gate.get("schema_version") != MUTATION_GATE_SCHEMA
        or gate.get("active") is not True
        or gate.get("convergence_stage")
        != ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT.value
        or not _gate_matches_current_scope(context, agent_id, gate)
        or source not in _REVIEW_REPAIR_AUTHORIZATION_SOURCES
    ):
        return
    from aworld.core.context.compiler import semantic_fingerprint

    candidate_fingerprint = gate.get("candidate_fingerprint")
    if not _is_canonical_semantic_fingerprint(candidate_fingerprint):
        return
    scope_hash = semantic_fingerprint(_model_decision_scope(context, agent_id))
    failure_evidence_hash = semantic_fingerprint(
        {
            "scope_hash": scope_hash,
            "candidate_fingerprint": candidate_fingerprint,
            "source": source,
            "review_evidence": dict(evidence),
        }
    )
    high_water = _repair_evidence_mask(
        gate.get("repair_failure_evidence_high_water")
    )
    if _repair_evidence_seen(high_water, failure_evidence_hash):
        return
    high_water = _repair_evidence_add(high_water, failure_evidence_hash)
    updated = dict(gate)
    updated["repair_authorization"] = {
        "schema_version": _REPAIR_AUTHORIZATION_SCHEMA,
        "scope_hash": scope_hash,
        "candidate_fingerprint": candidate_fingerprint,
        "failure_evidence_hash": failure_evidence_hash,
        "source": source,
        "used": False,
    }
    updated["repair_failure_evidence_high_water"] = format(high_water, "0128x")
    updated["declared_mutation_attempt_high_water"] = (
        _serialized_declared_mutation_attempt_high_water(
            gate.get("declared_mutation_attempt_high_water")
        )
    )
    updated["validation_window_open"] = True
    owner = state_context(context)
    if owner is not None:
        owner.context_info[MUTATION_GATE_STATE_KEY] = updated
    _write_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY, updated)


def record_acceptance_critic_decision(
    context, agent_id: str, value: Any
) -> tuple[ProtocolTransition | None, Any, bool]:
    """Serialize critic decision validation, apply, mint, and cleanup."""

    owner = state_context(context)
    transaction = getattr(owner, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            return _record_acceptance_critic_decision_locked(
                context, agent_id, value
            )
    return _record_acceptance_critic_decision_locked(context, agent_id, value)


def _record_acceptance_critic_decision_locked(
    context, agent_id: str, value: Any
) -> tuple[ProtocolTransition | None, Any, bool]:
    """Validate the typed decision and enforce independent probe evidence."""
    from aworld.core.execution_protocol import (
        AcceptanceCriticDecision,
        AcceptanceDecision,
        AcceptanceProbeReceipt,
    )

    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None, None, False
    try:
        decision = AcceptanceCriticDecision.from_value(value)
    except (TypeError, ValueError):
        decision = None
    critic_state = load_acceptance_critic_state(context, agent_id)
    receipt_value = critic_state.get("receipt")
    try:
        receipt = AcceptanceProbeReceipt.from_dict(receipt_value)
    except (TypeError, ValueError):
        receipt = None
    independently_supported = bool(
        decision is not None
        and decision.decision is AcceptanceDecision.ACCEPT
        and receipt is not None
        and receipt.supports(decision)
        and receipt.has_valid_framework_attestation(critic_state.get("challenge", ""))
    )
    if independently_supported:
        candidate, _ = _acceptance_candidate_and_evidence(context, agent_id)
        from aworld.core.context.compiler import semantic_fingerprint

        independently_supported = bool(
            receipt.candidate_hash == semantic_fingerprint(candidate)
            and receipt.candidate_hash
            == semantic_fingerprint(critic_state.get("candidate"))
            and receipt.evidence_hash
            == semantic_fingerprint(critic_state.get("evidence"))
            and receipt.artifact_before_hash
            == semantic_fingerprint(critic_state.get("artifact_before"))
        )
    if independently_supported:
        outcome = ReviewOutcome.ACCEPT
    elif decision is not None and decision.decision is AcceptanceDecision.REPAIR:
        outcome = ReviewOutcome.REPAIR
    else:
        outcome = ReviewOutcome.UNCERTAIN
    store = ExecutionProtocolStore(context, agent_id, policy)
    review_pending_before = store.load().review_pending
    transition = store.apply(
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=outcome,
        )
    )
    _record_transition_metrics(context, transition)
    if transition.decision.reason is DecisionReason.PERSISTENCE_ERROR:
        independently_supported = False
        clear_acceptance_critic_state(context, agent_id)
        return transition, decision, False
    if (
        review_pending_before
        and outcome is ReviewOutcome.REPAIR
        and decision is not None
        and transition.decision.action is ControllerAction.REQUEST_REPAIR
    ):
        _mint_review_repair_authorization(
            context,
            agent_id,
            source=_REPAIR_SOURCE_ACCEPTANCE_CRITIC,
            evidence={
                "review_decision": ReviewOutcome.REPAIR.value,
                "critic_candidate_hash": critic_state.get("candidate_hash"),
                "critic_evidence_hash": critic_state.get("evidence_hash"),
                "critic_receipt_id": getattr(receipt, "receipt_id", None),
            },
        )
    clear_acceptance_critic_state(context, agent_id)
    return transition, decision, independently_supported


def execution_protocol_policy(context, agent_id: str) -> ExecutionProtocolPolicy:
    value = _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_POLICY_KEY)
    try:
        return ExecutionProtocolPolicy.from_dict(value)
    except (TypeError, ValueError, KeyError):
        return ExecutionProtocolPolicy()


def load_execution_protocol_state(context, agent_id: str):
    """Return the validated state for this exact task/epoch/agent scope.

    Callers must not read the transport/runtime dictionaries directly: the
    store rejects stale state copied from another task or epoch and supplies a
    fresh, correctly scoped state when no record exists.
    """
    policy = execution_protocol_policy(context, agent_id)
    return ExecutionProtocolStore(context, agent_id, policy).load()


def execution_protocol_control_eligible(context, agent_id: str) -> bool:
    """Project the core eligibility predicate against the current contract."""

    state = load_execution_protocol_state(context, agent_id)
    status = _public_delivery_status(state_context(context))
    return execution_protocol_eligible(
        state,
        public_deliverable_declared=bool(
            status.get("public_deliverable_declared")
        ),
    )


def project_execution_protocol_telemetry(value: Any) -> dict[str, Any] | None:
    """Validate and bound the only public execution-protocol projection."""
    if not isinstance(value, Mapping) or value.get("schema_version") not in {
        "aworld.execution-protocol-telemetry/v1",
        "aworld.execution-protocol-telemetry/v2",
    }:
        return None
    required = {"mode", "phase", "armed"}
    if not required.issubset(value):
        return None
    enums = {
        "mode": {"off", "observe", "guide"},
        "phase": {"execute", "finalize", "review", "repair", "complete"},
        "model_horizon": {"unknown", "short", "long"},
        "long_horizon_activation_source": {
            "model_declared_long",
            "model_estimate_overrun",
            "generic_observation_threshold",
        },
        "initial_decision_status": {
            "retry_required",
            "acknowledged",
            "fail_open_unknown",
        },
        "replan_decision_status": {
            "retry_required",
            "acknowledged",
            "fail_open_unacknowledged",
            "convergence_constraint",
        },
        "initial_decision_fail_open_reason": {
            "provider_unavailable",
            "model_response_incomplete",
            "decision_schema_overflow",
        },
        "replan_decision_fail_open_reason": {
            "provider_unavailable",
            "model_response_incomplete",
            "decision_schema_overflow",
        },
        "decision_checkpoint_reason": {
            "stagnation_detected",
            "delivery_debt_detected",
            "next_action_mismatch",
            "candidate_decision_reserve",
        },
        "last_action_alignment": {"matched", "mismatched", "unobservable"},
        "last_delivery_intent": {
            "unknown",
            "continue_exploration",
            "produce_candidate",
            "validate_candidate",
            "submit_current",
            "submit_uncertain",
        },
        "convergence_stage": {
            stage.value for stage in ConvergenceStage
        },
        "acceptance_disposition": {"complete", "continue", "limit_reached"},
        "acceptance_reason": {
            "acceptance_satisfied",
            "acceptance_unsatisfied",
            "semantic_incomplete",
            "completion_claim_missing",
            "verification_missing",
            "verification_failed",
            "iteration_limit_reached",
        },
        "acceptance_continuation_suppressed": {
            "deadline_reserve",
            "protocol_convergence",
            "protocol_finalization",
        },
    }
    booleans = {
        "armed",
        "finalization_entered",
        "implicit_acceptance_created",
        "acceptance_satisfied",
        "legacy_activation_fields_ignored",
        "decision_checkpoint_pending",
        "candidate_decision_recorded",
        "terminal_incomplete",
        "decision_checkpoint_candidate_present",
        "mutation_gate_active",
        "mutation_gate_validation_window_open",
        "candidate_epoch_advanced",
        "candidate_checkpoint_recorded",
        "public_deliverable_declared",
        "public_candidate_mutated",
        "convergence_constraint_active",
        "analysis_runway_open",
    }
    counters = {
        "event_count",
        "tool_observation_count",
        "stagnant_observations",
        "replan_count",
        "replan_requested_count",
        "replan_applied_count",
        "initial_decision_attempt_count",
        "initial_decision_unavailable_count",
        "replan_decision_attempt_count",
        "replan_decision_unavailable_count",
        "consecutive_unapplied_replans",
        "suppressed_replan_boundaries",
        "candidate_final_count",
        "final_review_count",
        "repair_count",
        "delivery_debt_observations",
        "workspace_mutation_absent_observations",
        "delivery_checkpoint_count",
        "candidate_decision_count",
        "action_alignment_match_count",
        "action_alignment_mismatch_count",
        "acceptance_attempt",
        "acceptance_continuation_count",
        "acceptance_controller_error_count",
        "mutation_gate_activation_count",
        "mutation_gate_blocked_read_only_call_count",
        "convergence_gate_blocked_call_count",
        "consecutive_read_only_observations",
        "post_candidate_read_only_observations",
        "post_candidate_no_delivery_progress_observations",
        "convergence_constraint_activation_count",
        "model_expected_tool_actions",
        "model_expected_tool_actions_overrun_count",
        "analysis_progress_count",
        "analysis_stagnation_count",
        "analysis_runway_reset_count",
    }
    allowed = {"schema_version", *enums, *booleans, *counters}
    if set(value) - allowed:
        return None
    v2_fields = {
        "candidate_epoch_advanced",
        "candidate_checkpoint_recorded",
        "public_deliverable_declared",
        "public_candidate_mutated",
        "convergence_constraint_active",
        "convergence_stage",
        "convergence_constraint_activation_count",
        "post_candidate_read_only_observations",
        "post_candidate_no_delivery_progress_observations",
        "mutation_gate_validation_window_open",
        "long_horizon_activation_source",
        "model_expected_tool_actions",
        "model_expected_tool_actions_overrun_count",
        "terminal_incomplete",
        "analysis_runway_open",
        "analysis_progress_count",
        "analysis_stagnation_count",
        "analysis_runway_reset_count",
    }
    if (
        value.get("schema_version") == "aworld.execution-protocol-telemetry/v1"
        and set(value).intersection(v2_fields)
    ):
        return None
    projected = {"schema_version": value["schema_version"]}
    for key, item in value.items():
        if key == "schema_version" or item is None:
            continue
        if key in booleans:
            if not isinstance(item, bool):
                return None
        elif key in counters:
            if (
                isinstance(item, bool)
                or not isinstance(item, int)
                or not 0 <= item <= _MAX_TELEMETRY_COUNTER
            ):
                return None
        elif key in enums:
            if item not in enums[key]:
                return None
        else:
            return None
        projected[key] = item
    return projected


def build_execution_protocol_telemetry(context, agent_id: str) -> dict[str, Any]:
    """Project bounded, content-free protocol state for run diagnostics."""
    policy = execution_protocol_policy(context, agent_id)
    state = ExecutionProtocolStore(context, agent_id, policy).load()
    decisions = _normalized_decision_attempts(
        _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY),
        expected_scope=_model_decision_scope(context, agent_id),
    )
    mutation_gate = _read_runtime_value(
        context, agent_id, MUTATION_GATE_STATE_KEY
    )
    if not isinstance(mutation_gate, Mapping):
        mutation_gate = {}
    owner = state_context(context)
    runtime_metrics = getattr(owner, "context_info", {}).get(
        EXECUTION_PROTOCOL_METRICS_KEY
    )
    if not isinstance(runtime_metrics, Mapping):
        runtime_metrics = {}
    telemetry = {
        "schema_version": "aworld.execution-protocol-telemetry/v2",
        "mode": policy.mode.value,
        "phase": state.phase.value,
        "armed": state.long_horizon_armed,
        "long_horizon_activation_source": runtime_metrics.get(
            "long_horizon_activation_source"
        ),
        "model_expected_tool_actions": runtime_metrics.get(
            "model_expected_tool_actions"
        ),
        "model_expected_tool_actions_overrun_count": runtime_metrics.get(
            "model_expected_tool_actions_overrun_count"
        ),
        "legacy_activation_fields_ignored": False,
        "event_count": state.event_count,
        "tool_observation_count": state.tool_observation_count,
        "stagnant_observations": state.stagnant_observations,
        "replan_count": state.replan_count,
        "replan_requested_count": state.replan_requested_count,
        "replan_applied_count": state.replan_applied_count,
        "decision_checkpoint_pending": state.decision_checkpoint_pending,
        "decision_checkpoint_reason": (
            state.decision_checkpoint_reason.value
            if state.decision_checkpoint_reason is not None
            else None
        ),
        "decision_checkpoint_candidate_present": (
            state.decision_checkpoint_candidate_present
        ),
        "candidate_decision_recorded": state.candidate_decision_recorded,
        "terminal_incomplete": state.terminal_incomplete,
        "candidate_epoch_advanced": state.candidate_epoch_advanced,
        "candidate_checkpoint_recorded": state.candidate_checkpoint_recorded,
        "public_deliverable_declared": state.public_deliverable_declared,
        "public_candidate_mutated": state.public_candidate_mutated,
        "delivery_debt_observations": state.delivery_debt_observations,
        "workspace_mutation_absent_observations": (
            state.workspace_mutation_absent_observations
        ),
        "delivery_checkpoint_count": state.delivery_checkpoint_count,
        "candidate_decision_count": state.candidate_decision_count,
        "last_action_alignment": (
            state.last_action_alignment.value
            if state.last_action_alignment is not None
            else None
        ),
        "action_alignment_match_count": state.action_alignment_match_count,
        "action_alignment_mismatch_count": state.action_alignment_mismatch_count,
        "initial_decision_status": decisions["initial"].get("status"),
        "initial_decision_attempt_count": int(
            decisions["initial"].get("attempt_count", 0) or 0
        ),
        "initial_decision_unavailable_count": int(
            decisions["initial"].get("unavailable_count", 0) or 0
        ),
        "initial_decision_fail_open_reason": decisions["initial"].get(
            "fail_open_reason"
        ),
        "replan_decision_status": decisions["replan"].get("status"),
        "replan_decision_attempt_count": int(
            decisions["replan"].get("attempt_count", 0) or 0
        ),
        "replan_decision_unavailable_count": int(
            decisions["replan"].get("unavailable_count", 0) or 0
        ),
        "replan_decision_fail_open_reason": decisions["replan"].get("fail_open_reason"),
        "consecutive_unapplied_replans": decisions[
            "consecutive_unapplied_replans"
        ],
        "suppressed_replan_boundaries": decisions[
            "suppressed_replan_boundaries"
        ],
        "candidate_final_count": state.candidate_final_count,
        "final_review_count": state.final_review_count,
        "repair_count": state.repair_count,
        "finalization_entered": state.finalization_entered,
        "mutation_gate_active": mutation_gate.get("active") is True,
        "mutation_gate_validation_window_open": (
            mutation_gate.get("validation_window_open") is True
        ),
        "mutation_gate_activation_count": _bounded_counter(
            mutation_gate.get("activation_count")
        ),
        "mutation_gate_blocked_read_only_call_count": _bounded_counter(
            mutation_gate.get(
                "blocked_read_only_call_count", mutation_gate.get("blocked_call_count")
            )
        ),
        "convergence_gate_blocked_call_count": _bounded_counter(
            mutation_gate.get(
                "blocked_call_count", mutation_gate.get("blocked_read_only_call_count")
            )
        ),
        "consecutive_read_only_observations": _bounded_counter(
            mutation_gate.get("consecutive_read_only_observations")
        ),
        "analysis_runway_open": mutation_gate.get("analysis_runway_open") is True,
        "analysis_progress_count": _bounded_counter(
            mutation_gate.get("analysis_progress_count")
        ),
        "analysis_stagnation_count": _bounded_counter(
            mutation_gate.get("analysis_stagnation_count")
        ),
        "analysis_runway_reset_count": _bounded_counter(
            mutation_gate.get("analysis_runway_reset_count")
        ),
        "post_candidate_read_only_observations": (
            max(
                state.post_candidate_read_only_observations,
                state.post_candidate_no_delivery_progress_observations,
            )
        ),
        "post_candidate_no_delivery_progress_observations": (
            max(
                state.post_candidate_read_only_observations,
                state.post_candidate_no_delivery_progress_observations,
            )
        ),
        "convergence_constraint_active": state.convergence_constraint_active,
        "convergence_stage": (
            state.convergence_stage.value
            if state.convergence_stage is not None
            else None
        ),
        "convergence_constraint_activation_count": (
            state.convergence_constraint_activation_count
        ),
        "model_horizon": (
            state.model_plan_update.horizon.value
            if state.model_plan_update is not None
            else state.model_execution_profile.horizon.value
            if state.model_execution_profile is not None
            else None
        ),
        "last_delivery_intent": (
            state.model_plan_update.delivery_intent.value
            if state.model_plan_update is not None
            else None
        ),
    }
    projected = project_execution_protocol_telemetry(telemetry)
    if projected is None:  # pragma: no cover - values above are framework-owned
        raise ValueError("invalid framework execution protocol telemetry")
    return projected


def _remaining_task_seconds(context) -> float | None:
    owner = state_context(context)
    get_task = getattr(owner, "get_task", None)
    if not callable(get_task):
        return None
    try:
        task = get_task()
        remaining = task.remaining_seconds() if task is not None else None
    except Exception:
        return None
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
        return None
    return max(0.0, float(remaining))


def _task_deadline_progress(context) -> tuple[float, float, float] | None:
    """Return ``(total, remaining, consumed_fraction)`` for caller-owned time."""

    owner = state_context(context)
    get_task = getattr(owner, "get_task", None)
    if not callable(get_task):
        return None
    try:
        task = get_task()
        total = getattr(task, "timeout", None) if task is not None else None
        remaining = task.remaining_seconds() if task is not None else None
    except Exception:
        return None
    if (
        isinstance(total, bool)
        or not isinstance(total, (int, float))
        or total <= 0
        or isinstance(remaining, bool)
        or not isinstance(remaining, (int, float))
    ):
        return None
    bounded_remaining = min(float(total), max(0.0, float(remaining)))
    consumed = min(1.0, max(0.0, 1.0 - bounded_remaining / float(total)))
    return float(total), bounded_remaining, consumed


def _bounded_analysis_runway_open(
    consumed_fraction: float,
    semantic_state: Mapping[str, Any] | None,
) -> bool:
    if not (
        HARD_CONVERGENCE_MIN_DEADLINE_FRACTION
        <= consumed_fraction
        < _ANALYSIS_RUNWAY_MAX_DEADLINE_FRACTION
    ) or not isinstance(semantic_state, Mapping):
        return False
    progress_count = _bounded_counter(
        semantic_state.get("analysis_progress_count")
    )
    stagnation_count = _bounded_counter(
        semantic_state.get("analysis_stagnation_count")
    )
    reset_count = _bounded_counter(
        semantic_state.get("analysis_runway_reset_count")
    )
    return bool(
        (
            progress_count > 0
            or semantic_state.get("analysis_progress_advanced") is True
        )
        and stagnation_count < _ANALYSIS_RUNWAY_STAGNATION_THRESHOLD
        and reset_count <= _ANALYSIS_RUNWAY_MAX_PROGRESS_RESETS
    )


def _hard_convergence_deadline_reached(
    context,
    agent_id: str | None = None,
    *,
    semantic_state: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether a hard convergence phase may become active.

    The 40% checkpoint starts a *bounded* candidate runway, rather than
    immediately revoking analysis Tools, when the Sandbox has recently
    observed a changed intermediate artifact or novel read-only evidence.
    Three subsequent observations without such progress or more than eight
    progress resets exhaust that runway; 65% of the caller deadline is an
    unconditional ceiling. Older/unscoped semantic state receives no implicit
    allowance.
    """

    progress = _task_deadline_progress(context)
    if progress is None:
        return True
    consumed_fraction = progress[2]
    if consumed_fraction < HARD_CONVERGENCE_MIN_DEADLINE_FRACTION:
        return False
    if consumed_fraction >= _ANALYSIS_RUNWAY_MAX_DEADLINE_FRACTION:
        return True
    if semantic_state is None and isinstance(agent_id, str) and agent_id:
        try:
            from aworld.runners.post_tool_progress import semantic_progress_for_agent

            semantic_state = semantic_progress_for_agent(context, agent_id=agent_id)
        except Exception:
            semantic_state = None
    if not isinstance(semantic_state, Mapping):
        return True
    return not _bounded_analysis_runway_open(consumed_fraction, semantic_state)


def _update_mutation_gate(
    context,
    agent_id: str,
    transition: ProtocolTransition,
    semantic_state: Mapping[str, Any],
) -> None:
    """Serialize projection with concurrent Tool admission for this task."""

    owner = state_context(context)
    if owner is None:
        return
    transaction = getattr(owner, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            _update_mutation_gate_locked(
                context,
                agent_id,
                transition,
                semantic_state,
            )
        return
    _update_mutation_gate_locked(
        context,
        agent_id,
        transition,
        semantic_state,
    )


def _update_mutation_gate_locked(
    context,
    agent_id: str,
    transition: ProtocolTransition,
    semantic_state: Mapping[str, Any],
) -> None:
    """Project a persistent, task-scoped convergence admission state.

    The projection contains only hashes, booleans, counters, and enums.  Raw
    paths and Tool arguments remain transient at Sandbox preflight.
    """

    owner = state_context(context)
    if owner is None:
        return
    profile = transition.state.model_execution_profile
    delivery_status = _public_delivery_status(owner)
    public_deliverable_declared = bool(
        delivery_status["public_deliverable_declared"]
    )
    protocol_eligible = execution_protocol_eligible(
        transition.state,
        public_deliverable_declared=public_deliverable_declared,
    )
    mutation_required = bool(
        protocol_eligible
        and (
            public_deliverable_declared
            or (profile is not None and profile.workspace_mutation_required)
        )
    )
    candidate_present = transition.state.candidate_present is True
    public_candidate_mutated = bool(
        semantic_state.get("public_candidate_mutated")
    )
    mutation_observed = bool(
        semantic_state.get("workspace_mutation_observed")
    )
    read_only_count = _bounded_counter(
        semantic_state.get("consecutive_read_only_observations")
    )
    progress = _task_deadline_progress(context)
    consumed_fraction = progress[2] if progress is not None else None
    due_to_read_limit = read_only_count >= _MUTATION_GATE_READ_ONLY_THRESHOLD
    due_to_deadline = bool(
        consumed_fraction is not None
        and consumed_fraction >= _MUTATION_GATE_DEADLINE_FRACTION
        and read_only_count >= _MUTATION_GATE_DEADLINE_MIN_READS
    )
    previous = _read_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY)
    if not _gate_matches_current_scope(context, agent_id, previous):
        previous = {}
    previous_active = bool(
        isinstance(previous, Mapping)
        and previous.get("active") is True
    )
    previous_pre_candidate_latched = bool(
        isinstance(previous, Mapping)
        and previous.get("reason") not in {
            "post_candidate_read_only_limit",
            "post_candidate_no_delivery_progress_limit",
        }
        and (previous_active or previous.get("pre_candidate_latched") is True)
    )
    # When the public task names a concrete output, an unrelated setup write
    # must not discharge delivery debt. For mutation-required tasks without a
    # named artifact, the first observed mutation is the strongest generic
    # candidate signal available to the controller.
    resolved = (
        public_candidate_mutated
        if public_deliverable_declared
        else candidate_present or mutation_observed
    )
    activation_count = _bounded_counter(
        previous.get("activation_count") if isinstance(previous, Mapping) else 0
    )
    convergence_stage = transition.state.convergence_stage
    convergence_produce_due = bool(
        transition.state.convergence_constraint_active
        and convergence_stage is ConvergenceStage.PRODUCE_CANDIDATE
        and not resolved
    )
    no_delivery_progress_count = max(
        transition.state.post_candidate_no_delivery_progress_observations,
        transition.state.post_candidate_read_only_observations,
    )
    post_candidate_convergence_due = bool(
        protocol_eligible
        and transition.state.convergence_constraint_active
        and convergence_stage is ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
        and no_delivery_progress_count
        >= execution_protocol_policy(
            context, agent_id
        ).post_candidate_read_only_threshold
    )
    pre_candidate_latched = bool(
        mutation_required
        and not resolved
        and (
            previous_pre_candidate_latched
            or due_to_read_limit
            or due_to_deadline
            or convergence_produce_due
        )
    )
    from aworld.core.context.compiler import semantic_fingerprint

    scope_hash = semantic_fingerprint(_model_decision_scope(context, agent_id))
    previous_candidate_fingerprint = (
        previous.get("candidate_fingerprint")
        if isinstance(previous, Mapping)
        and _is_canonical_semantic_fingerprint(
            previous.get("candidate_fingerprint")
        )
        else None
    )
    previous_diagnostic_read_count = (
        previous.get("candidate_diagnostic_read_count")
        if isinstance(previous, Mapping)
        else None
    )
    if isinstance(previous, Mapping) and previous:
        candidate_diagnostic_high_water, high_water_is_canonical = (
            _candidate_diagnostic_high_water(
                previous.get("candidate_diagnostic_high_water")
            )
        )
        diagnostic_state_invalid = bool(
            previous.get("schema_version") != MUTATION_GATE_SCHEMA
            or not high_water_is_canonical
        )
        if diagnostic_state_invalid:
            candidate_diagnostic_high_water = (
                _CANDIDATE_DIAGNOSTIC_HIGH_WATER_FULL
            )
        elif previous_candidate_fingerprint is not None:
            expected_previous_count = _candidate_diagnostic_count(
                candidate_diagnostic_high_water,
                previous_candidate_fingerprint,
            )
            if (
                not _is_canonical_candidate_diagnostic_read_count(
                    previous_diagnostic_read_count
                )
                or previous_diagnostic_read_count != expected_previous_count
            ):
                candidate_diagnostic_high_water = (
                    _candidate_diagnostic_fail_closed(
                        candidate_diagnostic_high_water,
                        previous_candidate_fingerprint,
                    )
                )
    else:
        candidate_diagnostic_high_water = 0
        diagnostic_state_invalid = False
    observed_candidate_fingerprint = semantic_state.get(
        "public_delivery_fingerprint"
    )
    observed_candidate_fingerprint_is_canonical = (
        _is_canonical_semantic_fingerprint(observed_candidate_fingerprint)
    )
    candidate_advanced_without_binding = bool(
        semantic_state.get("candidate_advanced") is True
        and not observed_candidate_fingerprint_is_canonical
    )
    candidate_binding_unresolved = bool(
        candidate_advanced_without_binding
        or (
            isinstance(previous, Mapping)
            and previous.get("candidate_binding_unresolved") is True
            and not observed_candidate_fingerprint_is_canonical
        )
    )
    if candidate_binding_unresolved:
        candidate_fingerprint = None
    elif observed_candidate_fingerprint_is_canonical:
        candidate_fingerprint = observed_candidate_fingerprint
    else:
        candidate_fingerprint = previous_candidate_fingerprint
    if candidate_present and candidate_fingerprint is None:
        derived_candidate_fingerprint, _ = _public_candidate_binding(
            context, agent_id
        )
        if (
            not candidate_binding_unresolved
            and _is_canonical_semantic_fingerprint(
                derived_candidate_fingerprint
            )
        ):
            candidate_fingerprint = derived_candidate_fingerprint
    if not candidate_present and convergence_stage is ConvergenceStage.PRODUCE_CANDIDATE:
        candidate_fingerprint = None

    candidate_binding_unchanged = bool(
        _is_canonical_semantic_fingerprint(candidate_fingerprint)
        and previous_candidate_fingerprint == candidate_fingerprint
    )
    if (
        diagnostic_state_invalid
        and _is_canonical_semantic_fingerprint(candidate_fingerprint)
    ):
        candidate_diagnostic_high_water = _candidate_diagnostic_fail_closed(
            candidate_diagnostic_high_water,
            candidate_fingerprint,
        )
    if candidate_binding_unresolved:
        candidate_diagnostic_read_count = _MAX_CANDIDATE_DIAGNOSTIC_READS
    elif _is_canonical_semantic_fingerprint(candidate_fingerprint):
        candidate_diagnostic_read_count = _candidate_diagnostic_count(
            candidate_diagnostic_high_water,
            candidate_fingerprint,
        )
    else:
        candidate_diagnostic_read_count = 0
    rejection_latch_applicable = bool(
        convergence_stage is ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
        and _is_canonical_semantic_fingerprint(candidate_fingerprint)
        and candidate_diagnostic_read_count >= _MAX_CANDIDATE_DIAGNOSTIC_READS
    )
    (
        candidate_rejection_high_water,
        candidate_exhausted_rejection_count,
        candidate_tool_free_latched,
        _rejection_state_is_canonical,
    ) = _normalized_exhausted_rejection_state(
        previous,
        candidate_fingerprint,
    )
    if not rejection_latch_applicable:
        candidate_tool_free_latched = False
    (
        candidate_pure_rejection_count,
        candidate_pure_rejection_latched,
        _pure_rejection_state_is_canonical,
    ) = _normalized_pure_rejection_state(previous, candidate_fingerprint)
    if convergence_stage is not ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT:
        candidate_pure_rejection_count = 0
        candidate_pure_rejection_latched = False
    (
        pre_candidate_rejected_batch_count,
        pre_candidate_tool_free_latched,
        _pre_candidate_rejection_state_is_canonical,
    ) = _normalized_pre_candidate_rejection_state(previous)
    if (
        convergence_stage is not ConvergenceStage.PRODUCE_CANDIDATE
        or resolved
    ):
        pre_candidate_rejected_batch_count = 0
        pre_candidate_tool_free_latched = False

    repair_authorization = None
    if candidate_binding_unchanged:
        repair_authorization = _normalized_repair_authorization(
            previous.get("repair_authorization"),
            scope_hash=scope_hash,
            candidate_fingerprint=candidate_fingerprint,
        )
    repair_evidence_high_water = _repair_evidence_mask(
        previous.get("repair_failure_evidence_high_water")
        if isinstance(previous, Mapping)
        else None
    )
    failed_validations = []
    for value in semantic_state.get("observed_action_semantics") or ():
        try:
            receipt = (
                value
                if isinstance(value, ActionSemanticReceipt)
                else ActionSemanticReceipt.from_dict(value)
            )
        except (TypeError, ValueError):
            continue
        if (
            receipt.validation_kind is not None
            and receipt.executed is True
            and receipt.succeeded is False
        ):
            failed_validations.append(receipt)
    if (
        _is_canonical_semantic_fingerprint(candidate_fingerprint)
        and failed_validations
    ):
        receipt = failed_validations[0]
        failure_evidence_hash = semantic_fingerprint(
            {
                "scope_hash": scope_hash,
                "candidate_fingerprint": candidate_fingerprint,
                "source": _REPAIR_SOURCE_VALIDATION_FAILURE,
                "validation_kind": receipt.validation_kind,
                "target_ids": receipt.target_ids,
                "result_hash": semantic_state.get("result_hash"),
                "failure_signature": semantic_state.get("failure_signature"),
            }
        )
        if not _repair_evidence_seen(
            repair_evidence_high_water, failure_evidence_hash
        ):
            repair_evidence_high_water = _repair_evidence_add(
                repair_evidence_high_water, failure_evidence_hash
            )
            repair_authorization = {
                "schema_version": _REPAIR_AUTHORIZATION_SCHEMA,
                "scope_hash": scope_hash,
                "candidate_fingerprint": candidate_fingerprint,
                "failure_evidence_hash": failure_evidence_hash,
                "source": _REPAIR_SOURCE_VALIDATION_FAILURE,
                "used": False,
            }
    validation_window_open = repair_authorization is not None
    active = bool(
        execution_protocol_policy(context, agent_id).mode is ProtocolMode.GUIDE
        and protocol_eligible
        and transition.state.convergence_constraint_active
        and convergence_stage is not None
    )
    if active and not previous_active:
        activation_count = min(_MAX_TELEMETRY_COUNTER, activation_count + 1)
    reason = (
        "post_candidate_no_delivery_progress_limit"
        if post_candidate_convergence_due
        else "convergence_candidate_required"
        if convergence_produce_due
        else "deadline_without_mutation"
        if due_to_deadline
        else "read_only_limit"
        if due_to_read_limit
        else previous.get("reason")
        if isinstance(previous, Mapping)
        and (previous_active or previous_pre_candidate_latched)
        else None
    )
    payload = {
        "schema_version": MUTATION_GATE_SCHEMA,
        "scope_hash": scope_hash,
        "agent_id": agent_id,
        "active": active,
        "activation_count": activation_count,
        "reason": reason,
        "workspace_mutation_required": mutation_required,
        "public_deliverable_declared": public_deliverable_declared,
        "candidate_present": candidate_present,
        "public_candidate_mutated": public_candidate_mutated,
        "workspace_mutation_observed": mutation_observed,
        "validation_window_open": validation_window_open,
        "candidate_fingerprint": candidate_fingerprint,
        "candidate_binding_unresolved": candidate_binding_unresolved,
        "repair_authorization": repair_authorization,
        "candidate_diagnostic_read_count": candidate_diagnostic_read_count,
        "candidate_diagnostic_high_water": format(
            candidate_diagnostic_high_water, "0128x"
        ),
        "candidate_exhausted_rejection_fingerprint": (
            candidate_fingerprint
            if _is_canonical_semantic_fingerprint(candidate_fingerprint)
            else None
        ),
        "candidate_exhausted_rejection_count": (
            candidate_exhausted_rejection_count
        ),
        "candidate_rejection_high_water": format(
            candidate_rejection_high_water, "0128x"
        ),
        "candidate_tool_free_latched": candidate_tool_free_latched,
        "candidate_pure_rejection_fingerprint": (
            candidate_fingerprint
            if _is_canonical_semantic_fingerprint(candidate_fingerprint)
            else None
        ),
        "candidate_pure_rejection_count": candidate_pure_rejection_count,
        "candidate_pure_rejection_latched": candidate_pure_rejection_latched,
        "pre_candidate_rejected_batch_count": (
            pre_candidate_rejected_batch_count
        ),
        "pre_candidate_tool_free_latched": pre_candidate_tool_free_latched,
        "repair_failure_evidence_high_water": format(
            repair_evidence_high_water, "0128x"
        ),
        "declared_mutation_attempt_high_water": (
            _serialized_declared_mutation_attempt_high_water(
                previous.get("declared_mutation_attempt_high_water")
                if isinstance(previous, Mapping)
                else None
            )
        ),
        "declared_target_contract": bool(declared_action_target_ids(owner)),
        "pre_candidate_latched": pre_candidate_latched,
        "consecutive_read_only_observations": read_only_count,
        "post_candidate_no_delivery_progress_observations": (
            no_delivery_progress_count
        ),
        # One-release legacy alias.
        "post_candidate_read_only_observations": no_delivery_progress_count,
        "read_only_threshold": _MUTATION_GATE_READ_ONLY_THRESHOLD,
        "deadline_consumed_fraction": consumed_fraction,
        "analysis_progress_count": _bounded_counter(
            semantic_state.get("analysis_progress_count")
        ),
        "analysis_stagnation_count": _bounded_counter(
            semantic_state.get("analysis_stagnation_count")
        ),
        "analysis_runway_reset_count": _bounded_counter(
            semantic_state.get("analysis_runway_reset_count")
        ),
        "analysis_runway_open": bool(
            consumed_fraction is not None
            and _bounded_analysis_runway_open(
                consumed_fraction,
                semantic_state,
            )
        ),
        "analysis_runway_stagnation_threshold": (
            _ANALYSIS_RUNWAY_STAGNATION_THRESHOLD
        ),
        "analysis_runway_max_progress_resets": (
            _ANALYSIS_RUNWAY_MAX_PROGRESS_RESETS
        ),
        "analysis_runway_deadline_ceiling_fraction": (
            _ANALYSIS_RUNWAY_MAX_DEADLINE_FRACTION
        ),
        "structured_quality_open": (
            semantic_state.get("structured_quality_open") is True
        ),
        "structured_quality_required_table_count": _bounded_counter(
            semantic_state.get("structured_quality_required_table_count")
        ),
        "structured_quality_usable_table_count": _bounded_counter(
            semantic_state.get("structured_quality_usable_table_count")
        ),
        "structured_quality_repair_attempt_count": _bounded_counter(
            semantic_state.get("structured_quality_repair_attempt_count")
        ),
        "structured_quality_reason_codes": [
            value
            for value in (
                semantic_state.get("structured_quality_reason_codes") or ()
            )
            if isinstance(value, str)
        ][:16],
        "convergence_stage": (
            convergence_stage.value if convergence_stage is not None else None
        ),
        # Preserve interception evidence across subsequent observation
        # projections; previously this counter was reset on the next Tool
        # result, making successful gate blocks disappear from telemetry.
        "blocked_call_count": _bounded_counter(
            previous.get(
                "blocked_call_count", previous.get("blocked_read_only_call_count")
            )
            if isinstance(previous, Mapping)
            else 0
        ),
        # One-release legacy alias.
        "blocked_read_only_call_count": _bounded_counter(
            previous.get(
                "blocked_call_count", previous.get("blocked_read_only_call_count")
            )
            if isinstance(previous, Mapping)
            else 0
        ),
        "last_interception_kind": (
            previous.get("last_interception_kind")
            if isinstance(previous, Mapping)
            else None
        ),
    }
    owner.context_info[MUTATION_GATE_STATE_KEY] = payload
    _write_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY, payload)
    _update_active_gate_index(context, agent_id, active=active)
    metrics = owner.context_info.get(EXECUTION_PROTOCOL_METRICS_KEY)
    if isinstance(metrics, dict):
        if active and not previous_active:
            metrics["mutation_gate_activation_count"] = activation_count
        metrics["mutation_gate_active"] = active
        metrics["mutation_gate_validation_window_open"] = payload[
            "validation_window_open"
        ]
        metrics["consecutive_read_only_observations"] = read_only_count
        metrics["post_candidate_no_delivery_progress_observations"] = (
            no_delivery_progress_count
        )
        metrics["post_candidate_read_only_observations"] = no_delivery_progress_count
        metrics["convergence_gate_blocked_call_count"] = payload[
            "blocked_call_count"
        ]
        metrics["mutation_gate_blocked_read_only_call_count"] = payload[
            "blocked_read_only_call_count"
        ]


def mutation_gate_interception(
    context,
    actions: list[Any],
) -> dict[str, Any] | None:
    """Serialize gate discovery, admission, and persistence for one batch."""

    if context is None or not actions:
        return None
    owner = state_context(context)
    transaction = getattr(owner, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            return _mutation_gate_interception_locked(context, actions)
    return _mutation_gate_interception_locked(context, actions)


def _mutation_gate_interception_locked(
    context,
    actions: list[Any],
) -> dict[str, Any] | None:
    """Apply per-call semantic admission after a typed convergence boundary.

    Ordinary execution remains fail-open before convergence.  Once constrained,
    unknown or unrelated actions fail closed while a mixed batch still runs
    every individually admitted call.
    """

    if context is None or not actions:
        return None

    raw_action_call_ids = [
        _action_value(action, "tool_call_id") for action in actions
    ]
    action_call_ids = [
        value
        if isinstance(value, str) and value.strip()
        else ""
        for value in raw_action_call_ids
    ]
    unique_call_ids = list(
        dict.fromkeys(call_id for call_id in action_call_ids if call_id)
    )
    invalid_call_ids = len(unique_call_ids) != len(actions)
    action_agent_ids = [
        str(_action_value(action, "agent_name") or "") for action in actions
    ]
    agent_ids = {value for value in action_agent_ids if value}
    ambiguous_agent_scope = len(agent_ids) != 1 or any(
        not value for value in action_agent_ids
    )
    candidate_gates: list[tuple[str, Mapping[str, Any]]] = []
    for candidate_agent_id in sorted(agent_ids):
        candidate_gate = _read_runtime_value(
            context, candidate_agent_id, MUTATION_GATE_STATE_KEY
        )
        if (
            isinstance(candidate_gate, Mapping)
            and candidate_gate.get("active") is True
            and _gate_matches_current_scope(
                context, candidate_agent_id, candidate_gate
            )
            and execution_protocol_policy(context, candidate_agent_id).mode
            is ProtocolMode.GUIDE
        ):
            candidate_gates.append((candidate_agent_id, candidate_gate))
    if ambiguous_agent_scope:
        known_gate_agents = {value for value, _ in candidate_gates}
        for candidate_agent_id in _indexed_active_gate_agents(context):
            if candidate_agent_id in known_gate_agents:
                continue
            candidate_gate = _read_runtime_value(
                context, candidate_agent_id, MUTATION_GATE_STATE_KEY
            )
            if (
                isinstance(candidate_gate, Mapping)
                and candidate_gate.get("active") is True
                and _gate_matches_current_scope(
                    context, candidate_agent_id, candidate_gate
                )
                and execution_protocol_policy(context, candidate_agent_id).mode
                is ProtocolMode.GUIDE
            ):
                candidate_gates.append((candidate_agent_id, candidate_gate))
                known_gate_agents.add(candidate_agent_id)
    if ambiguous_agent_scope and not candidate_gates:
        owner = state_context(context)
        context_info = getattr(owner, "context_info", None)
        latest_gate = (
            context_info.get(MUTATION_GATE_STATE_KEY)
            if hasattr(context_info, "get")
            else None
        )
        latest_agent_id = (
            latest_gate.get("agent_id")
            if isinstance(latest_gate, Mapping)
            else None
        )
        if (
            isinstance(latest_agent_id, str)
            and latest_agent_id
            and latest_gate.get("active") is True
            and _gate_matches_current_scope(context, latest_agent_id, latest_gate)
            and execution_protocol_policy(context, latest_agent_id).mode
            is ProtocolMode.GUIDE
        ):
            candidate_gates.append((latest_agent_id, latest_gate))
    if ambiguous_agent_scope:
        if not candidate_gates:
            if not _indexed_active_gate_overflow(context):
                return None
            # More than the bounded retained identities were active in this
            # exact task epoch. Even when every retained gate later becomes
            # inactive, an unindexed active gate may remain; ambiguity must
            # therefore stay fail-closed until the task scope changes.
            call_rejections, truncated = _serialized_call_rejections(
                [
                    (
                        call_id,
                        MutationGateRejectionReason.SCOPE_AMBIGUOUS,
                    )
                    for call_id in action_call_ids
                ]
            )
            return {
                "schema_version": MUTATION_GATE_SCHEMA,
                "kind": "convergence_scope_ambiguous",
                "agent_id": _MUTATION_GATE_INDEX_NAMESPACE,
                "tool_call_ids": unique_call_ids,
                "block_all": True,
                "reason": "active_gate_index_overflow",
                "convergence_stage": None,
                "blocked_call_count": len(actions),
                "blocked_read_only_call_count": len(actions),
                "call_rejections": call_rejections,
                "call_rejections_truncated": truncated,
            }
        agent_id, gate = candidate_gates[0]
        updated = dict(gate)
        updated["blocked_call_count"] = min(
            _MAX_TELEMETRY_COUNTER,
            _bounded_counter(
                gate.get("blocked_call_count", gate.get("blocked_read_only_call_count"))
            )
            + len(actions),
        )
        updated["blocked_read_only_call_count"] = updated["blocked_call_count"]
        pre_count, pre_latched, _pre_state_is_canonical = (
            _normalized_pre_candidate_rejection_state(gate)
        )
        if gate.get("convergence_stage") == ConvergenceStage.PRODUCE_CANDIDATE.value:
            pre_count = min(
                _MAX_PRE_CANDIDATE_REJECTED_CALLS,
                pre_count + 1,
            )
            pre_latched = bool(
                pre_count >= _MAX_PRE_CANDIDATE_REJECTED_CALLS
                and not (
                    gate.get("public_deliverable_declared") is True
                    and gate.get("candidate_present") is False
                )
            )
        else:
            pre_count = 0
            pre_latched = False
        updated["pre_candidate_rejected_batch_count"] = pre_count
        updated["pre_candidate_tool_free_latched"] = pre_latched
        updated["repair_failure_evidence_high_water"] = format(
            _repair_evidence_mask(gate.get("repair_failure_evidence_high_water")),
            "0128x",
        )
        updated["declared_mutation_attempt_high_water"] = (
            _serialized_declared_mutation_attempt_high_water(
                gate.get("declared_mutation_attempt_high_water")
            )
        )
        updated["last_interception_kind"] = "convergence_scope_ambiguous"
        owner = state_context(context)
        if owner is not None:
            owner.context_info[MUTATION_GATE_STATE_KEY] = updated
        _write_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY, updated)
        call_rejections, truncated = _serialized_call_rejections(
            [
                (call_id, MutationGateRejectionReason.SCOPE_AMBIGUOUS)
                for call_id in action_call_ids
            ]
        )
        return {
            "schema_version": MUTATION_GATE_SCHEMA,
            "kind": "convergence_scope_ambiguous",
            "agent_id": agent_id,
            "tool_call_ids": unique_call_ids,
            "block_all": True,
            "reason": "ambiguous_agent_scope",
            "convergence_stage": updated.get("convergence_stage"),
            "blocked_call_count": updated["blocked_call_count"],
            "blocked_read_only_call_count": updated[
                "blocked_read_only_call_count"
            ],
            "pre_candidate_rejected_batch_count": updated[
                "pre_candidate_rejected_batch_count"
            ],
            "pre_candidate_tool_free_latched": updated[
                "pre_candidate_tool_free_latched"
            ],
            "call_rejections": call_rejections,
            "call_rejections_truncated": truncated,
        }
    agent_id = next(iter(agent_ids))
    if execution_protocol_policy(context, agent_id).mode is not ProtocolMode.GUIDE:
        return None
    gate = _read_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY)
    if (
        not isinstance(gate, Mapping)
        or gate.get("schema_version")
        not in {MUTATION_GATE_SCHEMA, *_LEGACY_MUTATION_GATE_SCHEMAS}
        or gate.get("active") is not True
        or not _gate_matches_current_scope(context, agent_id, gate)
    ):
        return None
    stage_value = gate.get("convergence_stage")
    try:
        stage = ConvergenceStage(stage_value)
    except (TypeError, ValueError):
        # A legacy active read gate has no typed convergence boundary and must
        # retain the pre-convergence fail-open guarantee.
        return None
    state = load_execution_protocol_state(context, agent_id)
    declared_targets = declared_action_target_ids(state_context(context))
    candidate_fingerprint = gate.get("candidate_fingerprint")
    (
        candidate_diagnostic_high_water,
        candidate_diagnostic_read_count,
        diagnostic_state_is_canonical,
    ) = _normalized_candidate_diagnostic_state(gate)
    candidate_binding_is_canonical = _is_canonical_semantic_fingerprint(
        candidate_fingerprint
    )
    (
        candidate_rejection_high_water,
        candidate_exhausted_rejection_count,
        candidate_tool_free_latched,
        _rejection_state_is_canonical,
    ) = _normalized_exhausted_rejection_state(gate, candidate_fingerprint)
    (
        candidate_pure_rejection_count,
        candidate_pure_rejection_latched,
        _pure_rejection_state_is_canonical,
    ) = _normalized_pure_rejection_state(gate, candidate_fingerprint)
    (
        pre_candidate_rejected_batch_count,
        pre_candidate_tool_free_latched,
        _pre_candidate_rejection_state_is_canonical,
    ) = _normalized_pre_candidate_rejection_state(gate)
    rejection_latch_applicable = bool(
        stage is ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
        and candidate_binding_is_canonical
        and candidate_diagnostic_read_count >= _MAX_CANDIDATE_DIAGNOSTIC_READS
    )
    if not rejection_latch_applicable:
        candidate_exhausted_rejection_count = 0
        candidate_tool_free_latched = False
    repair_authorization = _normalized_repair_authorization(
        gate.get("repair_authorization"),
        scope_hash=gate.get("scope_hash"),
        candidate_fingerprint=candidate_fingerprint,
    )
    initial_candidate_diagnostic_read_count = candidate_diagnostic_read_count
    initial_candidate_diagnostic_high_water = candidate_diagnostic_high_water
    blocked_call_ids: list[str] = list(unique_call_ids) if invalid_call_ids else []
    call_rejections: list[
        tuple[str | None, MutationGateRejectionReason]
    ] = []
    consumed_repair = False
    admitted_diagnostic_read = False
    admitted_declared_mutation = False
    admitted_candidate_mutation = False
    declared_mutation_attempts = _declared_mutation_attempt_mask(
        gate.get("declared_mutation_attempt_high_water")
    )
    initial_declared_mutation_attempts = declared_mutation_attempts
    block_all = bool(
        invalid_call_ids
        or candidate_tool_free_latched
        or candidate_pure_rejection_latched
        or pre_candidate_tool_free_latched
    )
    if invalid_call_ids:
        call_rejections.extend(
            (call_id or None, MutationGateRejectionReason.CALL_IDENTITY_INVALID)
            for call_id in action_call_ids
        )
    elif (
        candidate_tool_free_latched
        or candidate_pure_rejection_latched
        or pre_candidate_tool_free_latched
    ):
        blocked_call_ids = list(unique_call_ids)
        call_rejections.extend(
            (call_id, MutationGateRejectionReason.FINALIZATION_LATCHED)
            for call_id in action_call_ids
        )
    for action, call_id in (
        zip(actions, action_call_ids) if not block_all else ()
    ):
        try:
            semantics = build_preflight_action_semantic_receipt(
                context=state_context(context),
                action=action,
                delivery_intent=(
                    state.model_plan_update.delivery_intent.value
                    if state.model_plan_update is not None
                    else DeliveryIntent.UNKNOWN.value
                ),
            )
        except (TypeError, ValueError):
            semantics = None
        admitted = False
        rejection_reason: MutationGateRejectionReason | None = None
        if stage is ConvergenceStage.PRODUCE_CANDIDATE:
            if semantics is None:
                rejection_reason = MutationGateRejectionReason.EFFECT_UNKNOWN
            else:
                targets = set(semantics.target_ids)
                if declared_targets:
                    admitted = bool(
                        semantics.effect == "mutating"
                        and targets
                        and targets.issubset(declared_targets)
                    )
                    if not admitted:
                        if semantics.effect == "unknown":
                            rejection_reason = (
                                MutationGateRejectionReason.EFFECT_UNKNOWN
                            )
                        elif semantics.effect != "mutating":
                            rejection_reason = (
                                MutationGateRejectionReason.VALIDATION_UNREGISTERED
                            )
                        else:
                            rejection_reason = (
                                MutationGateRejectionReason.UNDECLARED_HELPER
                            )
                elif semantics.effect in {"mutating", "unknown"}:
                    admitted = _action_matches_typed_candidate_plan(
                        context, agent_id, action, semantics
                    )
                    if not admitted:
                        rejection_reason = (
                            MutationGateRejectionReason.EFFECT_UNKNOWN
                            if semantics.effect == "unknown"
                            else MutationGateRejectionReason.CANDIDATE_PLAN_MISMATCH
                        )
                else:
                    rejection_reason = (
                        MutationGateRejectionReason.VALIDATION_UNREGISTERED
                    )
            if admitted:
                admitted_candidate_mutation = True
        elif stage is ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT:
            registered_validation_kind = framework_observable_validation_kind(
                context, agent_id, action
            )
            admitted = registered_validation_kind is not None
            provably_read_only = actions_are_provably_read_only([action])
            live_repair = bool(
                not consumed_repair
                and repair_authorization is not None
            )
            if (
                not admitted
                and not admitted_diagnostic_read
                and gate.get("candidate_present") is True
                and gate.get("candidate_binding_unresolved") is not True
                and candidate_binding_is_canonical
                and candidate_diagnostic_read_count
                < _MAX_CANDIDATE_DIAGNOSTIC_READS
                and provably_read_only
            ):
                for slot in range(_MAX_CANDIDATE_DIAGNOSTIC_READS):
                    slot_fingerprint = _candidate_diagnostic_slot_fingerprint(
                        candidate_fingerprint,
                        slot,
                    )
                    if not _repair_evidence_seen(
                        candidate_diagnostic_high_water,
                        slot_fingerprint,
                    ):
                        candidate_diagnostic_high_water = _repair_evidence_add(
                            candidate_diagnostic_high_water,
                            slot_fingerprint,
                        )
                        candidate_diagnostic_read_count = (
                            _candidate_diagnostic_count(
                                candidate_diagnostic_high_water,
                                candidate_fingerprint,
                            )
                        )
                        admitted = True
                        admitted_diagnostic_read = True
                        break
            if not admitted and semantics is not None:
                targets = set(semantics.target_ids)
                if semantics.effect == "mutating" and declared_targets:
                    try:
                        tool, operation = canonical_tool_identity(action)
                        arguments = _action_arguments(action)
                        if arguments is None:
                            raise ValueError("declared revision requires Tool arguments")
                        mutation_signature = _hashed_action_signature(
                            f"{tool}__{operation}", arguments
                        )
                        from aworld.core.context.compiler import semantic_fingerprint

                        attempt_fingerprint = semantic_fingerprint(
                            {
                                "candidate_fingerprint": gate.get(
                                    "candidate_fingerprint"
                                ),
                                "mutation_signature": mutation_signature,
                                "target_ids": sorted(targets),
                            }
                        )
                    except (TypeError, ValueError):
                        attempt_fingerprint = None
                    attempt_replayed = bool(
                        isinstance(attempt_fingerprint, str)
                        and _repair_evidence_seen(
                            declared_mutation_attempts, attempt_fingerprint
                        )
                    )
                    admitted = bool(
                        not admitted_declared_mutation
                        and targets
                        and targets.issubset(declared_targets)
                        and isinstance(attempt_fingerprint, str)
                        and not attempt_replayed
                    )
                    if admitted:
                        declared_mutation_attempts = _repair_evidence_add(
                            declared_mutation_attempts, attempt_fingerprint
                        )
                        admitted_declared_mutation = True
                    elif not targets or not targets.issubset(declared_targets):
                        rejection_reason = (
                            MutationGateRejectionReason.UNDECLARED_HELPER
                        )
                    elif attempt_replayed:
                        rejection_reason = (
                            MutationGateRejectionReason.REPLAYED_REVISION
                        )
                    elif admitted_declared_mutation:
                        rejection_reason = (
                            MutationGateRejectionReason.REVISION_BATCH_LIMIT
                        )
                    else:
                        rejection_reason = (
                            MutationGateRejectionReason.EFFECT_UNKNOWN
                        )
                elif (
                    live_repair
                    and semantics.effect in {"mutating", "unknown"}
                    and not declared_targets
                ):
                    admitted = _action_matches_typed_candidate_plan(
                        context, agent_id, action, semantics
                    )
                    if not admitted:
                        rejection_reason = (
                            MutationGateRejectionReason.EFFECT_UNKNOWN
                            if semantics.effect == "unknown"
                            else MutationGateRejectionReason.CANDIDATE_PLAN_MISMATCH
                        )
                if admitted and live_repair and not declared_targets:
                    consumed_repair = True
            if not admitted and rejection_reason is None:
                if provably_read_only:
                    if (
                        candidate_diagnostic_read_count
                        >= _MAX_CANDIDATE_DIAGNOSTIC_READS
                    ):
                        rejection_reason = (
                            MutationGateRejectionReason.DIAGNOSTIC_QUOTA_EXHAUSTED
                        )
                    elif admitted_diagnostic_read:
                        rejection_reason = (
                            MutationGateRejectionReason.DIAGNOSTIC_BATCH_LIMIT
                        )
                    else:
                        rejection_reason = (
                            MutationGateRejectionReason.VALIDATION_UNREGISTERED
                        )
                elif semantics is None or semantics.effect == "unknown":
                    rejection_reason = MutationGateRejectionReason.EFFECT_UNKNOWN
                elif semantics.effect == "mutating":
                    rejection_reason = (
                        MutationGateRejectionReason.REPAIR_UNAUTHORIZED
                    )
                else:
                    rejection_reason = (
                        MutationGateRejectionReason.VALIDATION_UNREGISTERED
                    )
        if not admitted:
            blocked_call_ids.append(call_id)
            call_rejections.append(
                (
                    call_id,
                    rejection_reason
                    or MutationGateRejectionReason.EFFECT_UNKNOWN,
                )
            )
    if block_all:
        declared_mutation_attempts = initial_declared_mutation_attempts
        candidate_diagnostic_read_count = initial_candidate_diagnostic_read_count
        candidate_diagnostic_high_water = (
            initial_candidate_diagnostic_high_water
        )
        admitted_diagnostic_read = False
        admitted_declared_mutation = False
        admitted_candidate_mutation = False
    pure_post_candidate_rejected_batch = bool(
        block_all
        or (
            actions
            and len(blocked_call_ids) == len(actions)
        )
    )
    if stage is ConvergenceStage.PRODUCE_CANDIDATE:
        if pre_candidate_tool_free_latched:
            pre_candidate_rejected_batch_count = (
                _MAX_PRE_CANDIDATE_REJECTED_CALLS
            )
        elif admitted_candidate_mutation:
            # An admitted production call owns the next observation. Do not
            # let unrelated calls in the same mixed batch manufacture a
            # terminal latch before that candidate attempt is observed.
            pre_candidate_rejected_batch_count = 0
        elif pure_post_candidate_rejected_batch:
            pre_candidate_rejected_batch_count = min(
                _MAX_PRE_CANDIDATE_REJECTED_CALLS,
                pre_candidate_rejected_batch_count + 1,
            )
        pre_candidate_tool_free_latched = bool(
            pre_candidate_rejected_batch_count
            >= _MAX_PRE_CANDIDATE_REJECTED_CALLS
            and not (
                gate.get("public_deliverable_declared") is True
                and gate.get("candidate_present") is False
            )
        )
    else:
        pre_candidate_rejected_batch_count = 0
        pre_candidate_tool_free_latched = False
    diagnostics_exhausted = bool(
        stage is ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
        and candidate_binding_is_canonical
        and initial_candidate_diagnostic_read_count
        >= _MAX_CANDIDATE_DIAGNOSTIC_READS
    )
    if diagnostics_exhausted:
        if candidate_tool_free_latched:
            candidate_exhausted_rejection_count = (
                _MAX_EXHAUSTED_REJECTED_BATCHES
            )
        elif bool(block_all or blocked_call_ids):
            for slot in range(_MAX_EXHAUSTED_REJECTED_BATCHES):
                slot_fingerprint = _candidate_rejection_slot_fingerprint(
                    candidate_fingerprint,
                    slot,
                )
                if not _repair_evidence_seen(
                    candidate_rejection_high_water,
                    slot_fingerprint,
                ):
                    candidate_rejection_high_water = _repair_evidence_add(
                        candidate_rejection_high_water,
                        slot_fingerprint,
                    )
                    break
            candidate_exhausted_rejection_count = _candidate_rejection_count(
                candidate_rejection_high_water,
                candidate_fingerprint,
            )
        candidate_tool_free_latched = bool(
            candidate_exhausted_rejection_count
            >= _MAX_EXHAUSTED_REJECTED_BATCHES
        )
    else:
        candidate_exhausted_rejection_count = 0
        candidate_tool_free_latched = False
    pure_rejection_budget_active = bool(
        stage is ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
        and candidate_binding_is_canonical
    )
    if pure_rejection_budget_active:
        if candidate_pure_rejection_latched:
            candidate_pure_rejection_count = (
                _MAX_POST_CANDIDATE_PURE_REJECTED_BATCHES
            )
        elif pure_post_candidate_rejected_batch:
            candidate_pure_rejection_count = min(
                _MAX_POST_CANDIDATE_PURE_REJECTED_BATCHES,
                candidate_pure_rejection_count + 1,
            )
        elif not block_all and not blocked_call_ids:
            candidate_pure_rejection_count = 0
        candidate_pure_rejection_latched = bool(
            candidate_pure_rejection_count
            >= _MAX_POST_CANDIDATE_PURE_REJECTED_BATCHES
        )
    else:
        candidate_pure_rejection_count = 0
        candidate_pure_rejection_latched = False
    owner = state_context(context)
    updated = dict(gate)
    updated["blocked_call_count"] = min(
        _MAX_TELEMETRY_COUNTER,
        _bounded_counter(
            gate.get("blocked_call_count", gate.get("blocked_read_only_call_count"))
        )
        + (len(actions) if block_all else len(blocked_call_ids)),
    )
    updated["blocked_read_only_call_count"] = updated["blocked_call_count"]
    updated["repair_failure_evidence_high_water"] = format(
        _repair_evidence_mask(gate.get("repair_failure_evidence_high_water")),
        "0128x",
    )
    updated["declared_mutation_attempt_high_water"] = format(
        declared_mutation_attempts, "0128x"
    )
    updated["candidate_diagnostic_read_count"] = candidate_diagnostic_read_count
    updated["candidate_diagnostic_high_water"] = format(
        candidate_diagnostic_high_water, "0128x"
    )
    updated["candidate_exhausted_rejection_fingerprint"] = (
        candidate_fingerprint if candidate_binding_is_canonical else None
    )
    updated["candidate_exhausted_rejection_count"] = (
        candidate_exhausted_rejection_count
    )
    updated["candidate_rejection_high_water"] = format(
        candidate_rejection_high_water, "0128x"
    )
    updated["candidate_tool_free_latched"] = candidate_tool_free_latched
    updated["candidate_pure_rejection_fingerprint"] = (
        candidate_fingerprint if candidate_binding_is_canonical else None
    )
    updated["candidate_pure_rejection_count"] = candidate_pure_rejection_count
    updated["candidate_pure_rejection_latched"] = (
        candidate_pure_rejection_latched
    )
    updated["pre_candidate_rejected_batch_count"] = (
        pre_candidate_rejected_batch_count
    )
    updated["pre_candidate_tool_free_latched"] = (
        pre_candidate_tool_free_latched
    )
    updated["repair_authorization"] = repair_authorization
    updated["validation_window_open"] = repair_authorization is not None
    if consumed_repair:
        updated["repair_authorization"] = None
        updated["validation_window_open"] = False
    if not blocked_call_ids and not block_all:
        if owner is not None:
            owner.context_info[MUTATION_GATE_STATE_KEY] = updated
        _write_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY, updated)
        return None
    interception_kind = (
        "candidate_convergence_required"
        if stage is ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
        else "candidate_mutation_required"
    )
    updated["last_interception_kind"] = interception_kind
    if owner is not None:
        owner.context_info[MUTATION_GATE_STATE_KEY] = updated
        metrics = owner.context_info.get(EXECUTION_PROTOCOL_METRICS_KEY)
        if isinstance(metrics, dict):
            metrics["mutation_gate_blocked_read_only_call_count"] = updated[
                "blocked_read_only_call_count"
            ]
            metrics["convergence_gate_blocked_call_count"] = updated[
                "blocked_call_count"
            ]
    _write_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY, updated)
    serialized_rejections, truncated_rejections = _serialized_call_rejections(
        call_rejections
    )
    return {
        "schema_version": MUTATION_GATE_SCHEMA,
        "kind": interception_kind,
        "agent_id": agent_id,
        "tool_call_ids": blocked_call_ids,
        "block_all": block_all,
        "reason": updated.get("reason"),
        "consecutive_read_only_observations": updated.get(
            "consecutive_read_only_observations", 0
        ),
        "post_candidate_read_only_observations": updated.get(
            "post_candidate_read_only_observations", 0
        ),
        "post_candidate_no_delivery_progress_observations": updated.get(
            "post_candidate_no_delivery_progress_observations", 0
        ),
        "convergence_stage": updated.get("convergence_stage"),
        "candidate_diagnostic_read_count": updated[
            "candidate_diagnostic_read_count"
        ],
        "candidate_exhausted_rejection_count": updated[
            "candidate_exhausted_rejection_count"
        ],
        "candidate_tool_free_latched": updated[
            "candidate_tool_free_latched"
        ],
        "candidate_pure_rejection_count": updated[
            "candidate_pure_rejection_count"
        ],
        "candidate_pure_rejection_latched": updated[
            "candidate_pure_rejection_latched"
        ],
        "pre_candidate_rejected_batch_count": updated[
            "pre_candidate_rejected_batch_count"
        ],
        "pre_candidate_tool_free_latched": updated[
            "pre_candidate_tool_free_latched"
        ],
        "blocked_read_only_call_count": updated[
            "blocked_read_only_call_count"
        ],
        "blocked_call_count": updated["blocked_call_count"],
        "call_rejections": serialized_rejections,
        "call_rejections_truncated": truncated_rejections,
    }


def _record_deadline_guidance(
    context,
    agent_id: str,
    transition: ProtocolTransition,
) -> None:
    """Publish each generic convergence stage at most once per task."""

    if execution_protocol_policy(context, agent_id).mode is not ProtocolMode.GUIDE:
        return
    if not execution_protocol_control_eligible(context, agent_id):
        return
    progress = _task_deadline_progress(context)
    if progress is None:
        return
    total, remaining, consumed = progress
    selected = None
    for stage, threshold in _DEADLINE_STAGE_THRESHOLDS:
        if consumed >= threshold:
            selected = stage
    if selected is None:
        return
    if (
        selected == "candidate_due"
        and not (
            transition.state.workspace_mutation_absent_observations > 0
            or transition.state.decision_checkpoint_candidate_present is False
        )
    ):
        return
    rank = {stage: index for index, (stage, _) in enumerate(_DEADLINE_STAGE_THRESHOLDS)}
    current = _read_runtime_value(
        context, agent_id, EXECUTION_PROTOCOL_DEADLINE_GUIDANCE_KEY
    )
    previous_stage = current.get("last_stage") if isinstance(current, Mapping) else None
    if previous_stage in rank and rank[previous_stage] >= rank[selected]:
        return
    _write_runtime_value(
        context,
        agent_id,
        EXECUTION_PROTOCOL_DEADLINE_GUIDANCE_KEY,
        {
            "schema_version": "aworld.deadline-guidance/v1",
            "last_stage": selected,
            "pending_stage": selected,
            "total_seconds": total,
            "remaining_seconds": remaining,
            "consumed_fraction": consumed,
        },
    )
    owner = state_context(context)
    metrics = getattr(owner, "context_info", {}).get(EXECUTION_PROTOCOL_METRICS_KEY)
    if isinstance(metrics, dict):
        metrics["deadline_stage"] = selected
        metrics["deadline_guidance_count"] = min(
            _MAX_TELEMETRY_COUNTER,
            _bounded_counter(metrics.get("deadline_guidance_count")) + 1,
        )


def _record_transition_metrics(context, transition: ProtocolTransition) -> None:
    owner = state_context(context)
    if owner is None:
        return
    metrics = owner.context_info.get(EXECUTION_PROTOCOL_METRICS_KEY)
    if not isinstance(metrics, dict):
        metrics = {}
    previously_armed = metrics.get("long_horizon_armed") is True
    action = transition.decision.action.value
    reason = transition.decision.reason.value
    metrics["event_count"] = transition.state.event_count
    metrics["tool_observation_count"] = transition.state.tool_observation_count
    metrics["replan_count"] = transition.state.replan_count
    metrics["replan_requested_count"] = transition.state.replan_requested_count
    metrics["replan_applied_count"] = transition.state.replan_applied_count
    metrics["decision_checkpoint_pending"] = (
        transition.state.decision_checkpoint_pending
    )
    metrics["decision_checkpoint_reason"] = (
        transition.state.decision_checkpoint_reason.value
        if transition.state.decision_checkpoint_reason is not None
        else None
    )
    metrics["delivery_debt_observations"] = transition.state.delivery_debt_observations
    metrics["workspace_mutation_absent_observations"] = (
        transition.state.workspace_mutation_absent_observations
    )
    metrics["delivery_checkpoint_count"] = transition.state.delivery_checkpoint_count
    metrics["candidate_decision_count"] = transition.state.candidate_decision_count
    metrics["candidate_decision_recorded"] = (
        transition.state.candidate_decision_recorded
    )
    metrics["candidate_epoch_advanced"] = transition.state.candidate_epoch_advanced
    metrics["candidate_checkpoint_recorded"] = (
        transition.state.candidate_checkpoint_recorded
    )
    metrics["public_deliverable_declared"] = (
        transition.state.public_deliverable_declared
    )
    metrics["public_candidate_mutated"] = (
        transition.state.public_candidate_mutated
    )
    metrics["last_action_alignment"] = (
        transition.state.last_action_alignment.value
        if transition.state.last_action_alignment is not None
        else None
    )
    metrics["action_alignment_match_count"] = (
        transition.state.action_alignment_match_count
    )
    metrics["action_alignment_mismatch_count"] = (
        transition.state.action_alignment_mismatch_count
    )
    metrics["post_candidate_read_only_observations"] = (
        transition.state.post_candidate_read_only_observations
    )
    metrics["convergence_constraint_active"] = (
        transition.state.convergence_constraint_active
    )
    metrics["convergence_stage"] = (
        transition.state.convergence_stage.value
        if transition.state.convergence_stage is not None
        else None
    )
    metrics["convergence_constraint_activation_count"] = (
        transition.state.convergence_constraint_activation_count
    )
    metrics["final_review_count"] = transition.state.final_review_count
    metrics["repair_count"] = transition.state.repair_count
    metrics["long_horizon_armed"] = transition.state.long_horizon_armed
    if transition.state.model_execution_profile is not None:
        expected_tool_actions = (
            transition.state.model_execution_profile.expected_tool_actions
        )
        metrics["model_expected_tool_actions"] = expected_tool_actions
        metrics["model_expected_tool_actions_overrun_count"] = max(
            0,
            transition.state.tool_observation_count - expected_tool_actions,
        )
        metrics["model_horizon"] = (
            transition.state.model_execution_profile.horizon.value
        )
        metrics["model_confidence"] = (
            transition.state.model_execution_profile.confidence
        )
    if transition.state.model_plan_update is not None:
        metrics["model_horizon"] = transition.state.model_plan_update.horizon.value
        metrics["model_plan_decision"] = (
            transition.state.model_plan_update.decision.value
        )
        metrics["model_completion_assessment"] = (
            transition.state.model_plan_update.completion_assessment.value
        )
        metrics["last_delivery_intent"] = (
            transition.state.model_plan_update.delivery_intent.value
        )
    if transition.state.long_horizon_armed and not previously_armed:
        latest_horizon = (
            transition.state.model_plan_update.horizon
            if transition.state.model_plan_update is not None
            else transition.state.model_execution_profile.horizon
            if transition.state.model_execution_profile is not None
            else None
        )
        if latest_horizon is not None and latest_horizon.value == "long":
            activation_source = "model_declared_long"
        elif (
            latest_horizon is not None
            and latest_horizon.value == "short"
            and transition.state.model_execution_profile is not None
        ):
            activation_source = "model_estimate_overrun"
        else:
            activation_source = "generic_observation_threshold"
        metrics["long_horizon_activation_source"] = activation_source
    metrics["last_action"] = action
    metrics["last_reason"] = reason
    metrics[f"action:{action}"] = int(metrics.get(f"action:{action}", 0) or 0) + 1
    metrics[f"reason:{reason}"] = int(metrics.get(f"reason:{reason}", 0) or 0) + 1
    owner.context_info[EXECUTION_PROTOCOL_METRICS_KEY] = metrics


def _apply_event(
    context, agent_id: str, event: ExecutionProtocolEvent
) -> ProtocolTransition:
    policy = execution_protocol_policy(context, agent_id)
    transition = ExecutionProtocolStore(context, agent_id, policy).apply(event)
    _record_transition_metrics(context, transition)
    return transition


def _activate_convergence_constraint(
    context,
    agent_id: str,
    *,
    semantic_state: Mapping[str, Any] | None = None,
) -> ProtocolTransition | None:
    """Serialize convergence activation through its gate projection."""

    owner = state_context(context)
    transaction = getattr(owner, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            return _activate_convergence_constraint_locked(
                context,
                agent_id,
                semantic_state=semantic_state,
            )
    return _activate_convergence_constraint_locked(
        context,
        agent_id,
        semantic_state=semantic_state,
    )


def _activate_convergence_constraint_locked(
    context,
    agent_id: str,
    *,
    semantic_state: Mapping[str, Any] | None = None,
) -> ProtocolTransition | None:
    """Replace repeated unacknowledged replans with one executable phase.

    The phase is intentionally generic: produce an inspectable candidate when
    none is public, otherwise validate, repair from validation evidence, or
    submit.  Once active the core controller no longer emits replan requests.
    """

    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
    if not _hard_convergence_deadline_reached(
        context,
        agent_id,
        semantic_state=semantic_state,
    ):
        return None
    store = ExecutionProtocolStore(context, agent_id, policy)
    state = store.load()
    if not execution_protocol_control_eligible(context, agent_id):
        return None
    if state.convergence_constraint_active:
        return None
    delivery = _public_delivery_status(state_context(context))
    candidate_present = (
        delivery.get("candidate_present")
        if delivery.get("candidate_present") is not None
        else state.candidate_present
    )
    # Presence can select the bounded validation convergence stage without
    # manufacturing a candidate checkpoint or acceptance claim.
    candidate_convergence_ready = bool(
        state.candidate_checkpoint_recorded
        or (
            candidate_present is True
            and (
                state.public_deliverable_declared
                or state.candidate_epoch_advanced
            )
        )
    )
    stage = (
        ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
        if candidate_convergence_ready
        else ConvergenceStage.PRODUCE_CANDIDATE
    )
    progress = _task_deadline_progress(context)
    transition = _apply_event(
        context,
        agent_id,
        ExecutionProtocolEvent(
            # Keep the persisted v1 history kind readable by older runtimes;
            # convergence_stage is an additive field ignored by their reader.
            kind=EventKind.REPLAN_UNACKNOWLEDGED,
            convergence_stage=stage,
            deadline_consumed_fraction=(
                progress[2] if progress is not None else None
            ),
        ),
    )
    if transition.decision.reason is DecisionReason.PERSISTENCE_ERROR:
        return transition
    expected_scope = _model_decision_scope(context, agent_id)

    def mark_constraint(current):
        attempts = _normalized_decision_attempts(
            current, expected_scope=expected_scope
        )
        replan = dict(attempts.get("replan") or {})
        replan["status"] = "convergence_constraint"
        attempts["replan"] = replan
        return attempts

    _update_runtime_value(
        context,
        agent_id,
        EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY,
        mark_constraint,
    )
    _write_runtime_value(context, agent_id, EXECUTION_PROTOCOL_PENDING_KEY, None)
    previous_gate = _read_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY)
    if not _gate_matches_current_scope(context, agent_id, previous_gate):
        previous_gate = {}
    _update_mutation_gate(
        context,
        agent_id,
        transition,
        {
            "candidate_present": candidate_present,
            "public_candidate_mutated": bool(
                isinstance(previous_gate, Mapping)
                and previous_gate.get("public_candidate_mutated")
            ),
            "workspace_mutation_observed": bool(
                isinstance(previous_gate, Mapping)
                and previous_gate.get("workspace_mutation_observed")
            ),
            "consecutive_read_only_observations": _bounded_counter(
                previous_gate.get("consecutive_read_only_observations")
                if isinstance(previous_gate, Mapping)
                else 0
            ),
            "analysis_progress_count": _bounded_counter(
                semantic_state.get("analysis_progress_count")
                if isinstance(semantic_state, Mapping)
                else previous_gate.get("analysis_progress_count")
                if isinstance(previous_gate, Mapping)
                else 0
            ),
            "analysis_stagnation_count": _bounded_counter(
                semantic_state.get("analysis_stagnation_count")
                if isinstance(semantic_state, Mapping)
                else previous_gate.get("analysis_stagnation_count")
                if isinstance(previous_gate, Mapping)
                else 0
            ),
            "analysis_progress_advanced": bool(
                isinstance(semantic_state, Mapping)
                and semantic_state.get("analysis_progress_advanced") is True
            ),
            "analysis_runway_reset_count": _bounded_counter(
                semantic_state.get("analysis_runway_reset_count")
                if isinstance(semantic_state, Mapping)
                else previous_gate.get("analysis_runway_reset_count")
                if isinstance(previous_gate, Mapping)
                else 0
            ),
        },
    )
    return transition


def _activate_convergence_after_unapplied_limit(
    context,
    agent_id: str,
    *,
    semantic_state: Mapping[str, Any] | None = None,
) -> ProtocolTransition | None:
    attempts = _normalized_decision_attempts(
        _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY),
        expected_scope=_model_decision_scope(context, agent_id),
    )
    if (
        attempts["consecutive_unapplied_replans"]
        < _MAX_CONSECUTIVE_UNAPPLIED_REPLANS
    ):
        return None
    if not _hard_convergence_deadline_reached(
        context,
        agent_id,
        semantic_state=semantic_state,
    ):
        return None
    return _activate_convergence_constraint(
        context,
        agent_id,
        semantic_state=semantic_state,
    )


def record_tool_protocol_event(
    context, agent_id: str, semantic_state: Mapping[str, Any]
) -> ProtocolTransition | None:
    """Serialize one protocol event through every derived gate projection."""

    owner = state_context(context)
    transaction = getattr(owner, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            return _record_tool_protocol_event_locked(
                context, agent_id, semantic_state
            )
    return _record_tool_protocol_event_locked(context, agent_id, semantic_state)


def _record_tool_protocol_event_locked(
    context, agent_id: str, semantic_state: Mapping[str, Any]
) -> ProtocolTransition | None:
    """Project one existing semantic Tool observation into protocol state."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
    # Upgrade persisted v1 fail-open counters before processing another Tool
    # observation so a legacy task cannot increment requested replans again.
    _activate_convergence_after_unapplied_limit(
        context,
        agent_id,
        semantic_state=semantic_state,
    )
    observed_action_semantics: list[ActionSemanticReceipt] = []
    raw_action_semantics = semantic_state.get("observed_action_semantics") or ()
    if isinstance(raw_action_semantics, (list, tuple)):
        for value in raw_action_semantics[:16]:
            try:
                receipt = (
                    value
                    if isinstance(value, ActionSemanticReceipt)
                    else ActionSemanticReceipt.from_dict(value)
                )
            except (TypeError, ValueError):
                continue
            if receipt.executed is None:
                continue
            observed_action_semantics.append(receipt)
    deadline_progress = _task_deadline_progress(context)
    event = ExecutionProtocolEvent(
        kind=EventKind.TOOL_OBSERVATION,
        repetition_count=int(semantic_state.get("repetition_count", 0) or 0),
        low_information_gain_count=int(
            semantic_state.get("low_information_gain_count", 0) or 0
        ),
        no_goal_progress_count=int(
            semantic_state.get("no_goal_progress_count", 0) or 0
        ),
        goal_progress_observable=semantic_state.get("goal_progress_observable"),
        goal_progress=semantic_state.get("goal_progress"),
        # A changed evidence fingerprint may be a regression or oscillation.
        # Only monotonic completion/goal progress resets long-horizon
        # stagnation; novelty remains available in the semantic ledger.
        evidence_advanced=bool(
            semantic_state.get("completion_advanced")
            or semantic_state.get("goal_progress") is True
        ),
        public_deliverable_declared=bool(
            semantic_state.get("public_deliverable_declared")
        ),
        missing_public_deliverable_count=int(
            semantic_state.get("missing_public_deliverable_count", 0) or 0
        ),
        candidate_present=semantic_state.get("candidate_present"),
        # ``candidate_advanced`` is a successful public-delivery high-water
        # advance. ``public_candidate_mutated`` is only the current level
        # relative to the task-start baseline. Folding the latter into the
        # former would let failed writes and repeated states evade convergence.
        candidate_advanced=bool(semantic_state.get("candidate_advanced")),
        delivery_progress_advanced=bool(
            semantic_state.get("delivery_progress_advanced")
        ),
        public_candidate_mutated=bool(
            semantic_state.get("public_candidate_mutated")
        ),
        workspace_mutated=bool(semantic_state.get("workspace_mutated")),
        read_only_observed=bool(semantic_state.get("read_only_observed")),
        known_mutation_executed=bool(
            semantic_state.get("known_mutation_executed")
        ),
        validation_observed=bool(semantic_state.get("validation_observed")),
        new_information_observed=bool(semantic_state.get("new_information_observed")),
        observed_action_names=tuple(semantic_state.get("observed_action_names") or ()),
        observed_action_signatures=tuple(
            semantic_state.get("observed_action_signatures") or ()
        ),
        observed_action_semantics=tuple(observed_action_semantics),
        current_step=int(semantic_state.get("current_agent_step", 0) or 0),
        remaining_seconds=(
            deadline_progress[1]
            if deadline_progress is not None
            else _remaining_task_seconds(context)
        ),
        deadline_consumed_fraction=(
            deadline_progress[2] if deadline_progress is not None else None
        ),
        operation_hash=semantic_state.get("operation_hash"),
        result_hash=semantic_state.get("result_hash"),
    )
    transition = _apply_event(context, agent_id, event)
    if transition.decision.reason is DecisionReason.PERSISTENCE_ERROR:
        return transition
    deadline_candidate_due = bool(
        deadline_progress is not None
        and _hard_convergence_deadline_reached(
            context,
            agent_id,
            semantic_state=semantic_state,
        )
        and event.candidate_present is False
        and not transition.state.convergence_constraint_active
        and execution_protocol_eligible(
            transition.state,
            public_deliverable_declared=event.public_deliverable_declared,
        )
    )
    if deadline_candidate_due:
        activated = _activate_convergence_constraint_locked(
            context,
            agent_id,
            semantic_state=semantic_state,
        )
        if (
            activated is not None
            and activated.state.convergence_constraint_active
        ):
            transition = activated
    _update_mutation_gate(context, agent_id, transition, semantic_state)
    _record_pending_checkpoint(context, agent_id, transition)
    _record_deadline_guidance(context, agent_id, transition)
    return transition


def bind_pending_next_action_call(
    context,
    agent_id: str,
    actions: list[Any],
) -> bool:
    """Bind one declared next action to the first call of its continuation.

    A checkpoint declares exactly one *next* action. Later calls in the same
    model batch remain executable but cannot satisfy its alignment receipt.
    """

    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF or not actions:
        return False
    state = load_execution_protocol_state(context, agent_id)
    if (
        not state.next_action_alignment_pending
        or state.pending_next_action_call_id is not None
    ):
        return False
    first_call_id = _action_value(actions[0], "tool_call_id")
    if not isinstance(first_call_id, str) or not first_call_id.strip():
        return False
    transition = _apply_event(
        context,
        agent_id,
        ExecutionProtocolEvent(
            kind=EventKind.NEXT_ACTION_BOUND,
            bound_tool_call_id=first_call_id,
        ),
    )
    return transition.decision.reason not in {
        DecisionReason.INVALID_EVENT,
        DecisionReason.PERSISTENCE_ERROR,
    }


def _record_pending_checkpoint(
    context,
    agent_id: str,
    transition: ProtocolTransition,
) -> None:
    """Project one controller checkpoint into the bounded model boundary."""

    if transition.decision.action is ControllerAction.REQUEST_REPLAN:
        expected_scope = _model_decision_scope(context, agent_id)
        attempts = _normalized_decision_attempts(
            _read_runtime_value(
                context, agent_id, EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY
            ),
            expected_scope=expected_scope,
        )
        if (
            attempts["consecutive_unapplied_replans"]
            >= _MAX_CONSECUTIVE_UNAPPLIED_REPLANS
        ):
            # Defensive migration path. Normal Tool observations activate the
            # constraint before entering the controller, so requested counters
            # do not grow. Never silently suppress another boundary.
            activated = _activate_convergence_constraint(context, agent_id)
            if (
                activated is not None
                and activated.state.convergence_constraint_active
            ):
                return

        def begin_replan_decision(current):
            attempts = _normalized_decision_attempts(
                current, expected_scope=expected_scope
            )
            attempts["replan"] = {
                "attempt_count": 0,
                "request_sequence": transition.state.replan_requested_count,
            }
            return attempts

        _update_runtime_value(
            context,
            agent_id,
            EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY,
            begin_replan_decision,
        )
    if transition.decision.action in {
        ControllerAction.REQUEST_REPLAN,
        ControllerAction.ENTER_FINALIZATION,
    }:
        _write_runtime_value(
            context,
            agent_id,
            EXECUTION_PROTOCOL_PENDING_KEY,
            {
                "action": transition.decision.action.value,
                "reason": transition.decision.reason.value,
                "revision": transition.state.revision,
                "candidate_present": (
                    transition.state.decision_checkpoint_candidate_present
                ),
                "delivery_debt_observations": (
                    transition.state.delivery_debt_observations
                ),
                "workspace_mutation_absent_observations": (
                    transition.state.workspace_mutation_absent_observations
                ),
            },
        )


def record_pre_generation_delivery_decision(
    context,
    agent_id: str,
    *,
    policy: ExecutionProtocolPolicy | None = None,
) -> ProtocolTransition | None:
    """Request one typed delivery choice before Tool-free finalization.

    This is AWorld-owned deadline mechanics.  It observes only caller time and
    public candidate presence, and leaves every semantic option to the model.
    """

    policy = policy or execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
    state = ExecutionProtocolStore(context, agent_id, policy).load()
    remaining = _remaining_task_seconds(context)
    status = _public_delivery_status(state_context(context))
    delivery_reserve_armed = execution_protocol_eligible(
        state,
        public_deliverable_declared=bool(
            status["public_deliverable_declared"]
        ),
    )
    if (
        not delivery_reserve_armed
        or state.phase.value not in {"execute", "repair"}
        or state.decision_checkpoint_pending
        or state.candidate_decision_count > 0
        or remaining is None
        or remaining > policy.candidate_decision_reserve_seconds
    ):
        return None
    transition = _apply_event(
        context,
        agent_id,
        ExecutionProtocolEvent(
            kind=EventKind.DELIVERY_STATUS,
            remaining_seconds=remaining,
            public_deliverable_declared=status["public_deliverable_declared"],
            missing_public_deliverable_count=status["missing_public_deliverable_count"],
            candidate_present=status["candidate_present"],
        ),
    )
    _record_pending_checkpoint(context, agent_id, transition)
    return (
        transition
        if transition.decision.action
        in {ControllerAction.REQUEST_REPLAN, ControllerAction.ENTER_FINALIZATION}
        else None
    )


def execution_protocol_accepts_model_profile(context, agent_id: str) -> bool:
    """Return whether the initial model-owned decision is still unresolved."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return False
    state = ExecutionProtocolStore(context, agent_id, policy).load()
    return state.model_execution_profile is None


def execution_protocol_model_decision_boundary(context, agent_id: str) -> str | None:
    """Return the pending framework boundary, never a semantic decision."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is not ProtocolMode.GUIDE:
        return None
    state = ExecutionProtocolStore(context, agent_id, policy).load()
    if state.convergence_constraint_active:
        return None
    attempts = _normalized_decision_attempts(
        _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY),
        expected_scope=_model_decision_scope(context, agent_id),
    )
    if state.model_execution_profile is None:
        if attempts["initial"].get("status") == "fail_open_unknown":
            return None
        return "initial"
    if state.decision_checkpoint_pending:
        replan = attempts["replan"]
        if (
            replan.get("request_sequence") == state.replan_requested_count
            and replan.get("status") == "fail_open_unacknowledged"
        ):
            return None
        return "replan"
    return None


def record_model_decision_attempt_failure(
    context, agent_id: str, *, boundary: str
) -> bool:
    """Record a malformed decision and return whether one retry remains."""
    if boundary not in {"initial", "replan"}:
        return False
    if execution_protocol_model_decision_boundary(context, agent_id) != boundary:
        return False
    state = load_execution_protocol_state(context, agent_id)
    request_sequence = state.replan_requested_count if boundary == "replan" else 0
    expected_scope = _model_decision_scope(context, agent_id)
    retry = False

    def update(current):
        nonlocal retry
        attempts = _normalized_decision_attempts(current, expected_scope=expected_scope)
        previous = attempts[boundary]
        if (
            boundary == "replan"
            and previous.get("request_sequence") != request_sequence
        ):
            previous = {}
        count = min(
            _MAX_DECISION_ATTEMPTS,
            int(previous.get("attempt_count", 0) or 0) + 1,
        )
        retry = count < _MAX_DECISION_ATTEMPTS
        attempts[boundary] = {
            "attempt_count": count,
            "request_sequence": request_sequence,
            "status": (
                "retry_required"
                if retry
                else "fail_open_unknown"
                if boundary == "initial"
                else "fail_open_unacknowledged"
            ),
        }
        if boundary == "replan" and not retry:
            attempts["consecutive_unapplied_replans"] = min(
                _MAX_TELEMETRY_COUNTER,
                attempts["consecutive_unapplied_replans"] + 1,
            )
        return attempts

    _update_runtime_value(
        context, agent_id, EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY, update
    )
    if not retry and boundary == "replan":
        _apply_event(
            context,
            agent_id,
            ExecutionProtocolEvent(kind=EventKind.REPLAN_UNACKNOWLEDGED),
        )
        _activate_convergence_after_unapplied_limit(context, agent_id)
    return retry


def record_model_decision_unavailable(
    context,
    agent_id: str,
    *,
    boundary: str,
    reason: str,
) -> bool:
    """Fail open when infrastructure cannot carry a decision boundary.

    Provider transport failures and exhausted incomplete-response recovery are
    not model-owned semantic choices.  They therefore must not consume the
    malformed-decision retry loop or keep ordinary task Tools hidden.  The
    boundary remains explicitly unacknowledged/unknown and never becomes
    evidence for completion or a short-task bypass.
    """
    if boundary not in {"initial", "replan"}:
        return False
    if reason not in {
        "provider_unavailable",
        "model_response_incomplete",
        "decision_schema_overflow",
    }:
        return False
    if execution_protocol_model_decision_boundary(context, agent_id) != boundary:
        return False
    state = load_execution_protocol_state(context, agent_id)
    request_sequence = state.replan_requested_count if boundary == "replan" else 0
    expected_scope = _model_decision_scope(context, agent_id)

    def update(current):
        attempts = _normalized_decision_attempts(current, expected_scope=expected_scope)
        previous = attempts[boundary]
        if (
            boundary == "replan"
            and previous.get("request_sequence") != request_sequence
        ):
            previous = {}
        attempts[boundary] = {
            # Only complete-but-malformed structured decisions consume the
            # bounded semantic retry counter. Transport and incomplete-output
            # failures have their own causal counter.
            "attempt_count": min(
                _MAX_DECISION_ATTEMPTS,
                int(previous.get("attempt_count", 0) or 0),
            ),
            "unavailable_count": min(
                _MAX_TELEMETRY_COUNTER,
                int(previous.get("unavailable_count", 0) or 0) + 1,
            ),
            "request_sequence": request_sequence,
            "status": (
                "fail_open_unknown"
                if boundary == "initial"
                else "fail_open_unacknowledged"
            ),
            "fail_open_reason": reason,
        }
        if boundary == "replan":
            attempts["consecutive_unapplied_replans"] = min(
                _MAX_TELEMETRY_COUNTER,
                attempts["consecutive_unapplied_replans"] + 1,
            )
        return attempts

    _update_runtime_value(
        context, agent_id, EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY, update
    )
    if boundary == "replan":
        _apply_event(
            context,
            agent_id,
            ExecutionProtocolEvent(kind=EventKind.REPLAN_UNACKNOWLEDGED),
        )
        _activate_convergence_after_unapplied_limit(context, agent_id)
    return True


def record_model_execution_profile(
    context,
    agent_id: str,
    value: Mapping[str, Any],
) -> ProtocolTransition | None:
    """Validate and record one content-free model activation assessment.

    Malformed or repeated assessments never become a short classification.
    The explicit decision boundary may offer the schema again until a valid
    model-owned choice is recorded or the caller deadline enters finalization.
    """
    if not execution_protocol_accepts_model_profile(context, agent_id):
        return None
    try:
        profile = ModelExecutionProfile.from_mapping(value)
    except (TypeError, ValueError, KeyError):
        _write_runtime_value(
            context,
            agent_id,
            EXECUTION_PROTOCOL_MODEL_PROFILE_KEY,
            {"status": "invalid", "classification": "unknown"},
        )
        return None
    _write_runtime_value(
        context,
        agent_id,
        EXECUTION_PROTOCOL_MODEL_PROFILE_KEY,
        {"status": "recorded"},
    )
    return _apply_event(
        context,
        agent_id,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_EXECUTION_PROFILE,
            model_execution_profile=profile,
        ),
    )


def record_model_plan_update(
    context,
    agent_id: str,
    value: Mapping[str, Any],
) -> ProtocolTransition | None:
    """Record one strict model-owned checkpoint from a real Tool turn.

    The update is a bounded claim, not observed evidence. It can classify or
    reclassify the task horizon and acknowledge an advisory checkpoint, but it
    cannot accept completion or manufacture verifier evidence.
    """
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
    try:
        update = _model_plan_update_with_semantics(context, value)
    except (TypeError, ValueError, KeyError):
        return None
    return _record_validated_model_plan_update(context, agent_id, update)


def _model_plan_update_with_semantics(
    context,
    value: Mapping[str, Any],
    *,
    tool_identity_aliases: Mapping[str, str] | None = None,
    decision_call_id: str | None = None,
) -> ModelPlanUpdate:
    """Validate model data, then discard raw args behind a typed receipt."""

    update = ModelPlanUpdate.from_model_mapping(value)
    if decision_call_id is not None:
        update = replace(update, decision_call_id=decision_call_id)
    tool_name = update.next_action_tool
    raw_arguments = value.get("next_action_arguments")
    if tool_name is None or not isinstance(raw_arguments, str):
        return update
    parsed_arguments = json.loads(raw_arguments)
    if not isinstance(parsed_arguments, dict):
        return update
    from aworld.sandbox.tool_observation import (
        build_planned_action_semantic_receipt,
    )

    aliases = tool_identity_aliases or {}
    resolved_tool_name = aliases.get(tool_name, tool_name)
    if resolved_tool_name.startswith("mcp__"):
        resolved_tool_name = resolved_tool_name[len("mcp__") :]
    semantics = build_planned_action_semantic_receipt(
        context=context,
        tool_name=resolved_tool_name,
        arguments=parsed_arguments,
        delivery_intent=update.delivery_intent.value,
    )
    return replace(update, next_action_semantics=semantics)


def _record_validated_model_plan_update(
    context,
    agent_id: str,
    update: ModelPlanUpdate,
) -> ProtocolTransition | None:
    """Record an already validated model update without reparsing persisted data."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
    transition = _apply_event(
        context,
        agent_id,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=update,
        ),
    )
    if transition.decision.reason in {
        DecisionReason.INVALID_EVENT,
        DecisionReason.PERSISTENCE_ERROR,
    }:
        return transition
    try:
        from aworld.core.context.work_progress import retain_model_work_checkpoint

        retain_model_work_checkpoint(context, agent_id, update.to_dict())
    except Exception:
        # Recovery bookkeeping is advisory and must not revoke normal tools.
        pass
    try:
        from aworld.runners.post_tool_progress import (
            acknowledge_semantic_checkpoint,
            refresh_public_probe_receipt_projection,
        )

        acknowledge_semantic_checkpoint(context, agent_id=agent_id)
        refresh_public_probe_receipt_projection(context, agent_id=agent_id)
    except Exception:
        pass
    return transition


def record_model_decision_boundary(
    context,
    agent_id: str,
    *,
    boundary: str,
    execution_profile: Mapping[str, Any] | None,
    plan_update: Mapping[str, Any] | None,
    available_tool_names: frozenset[str] | None = None,
    available_tool_aliases: Mapping[str, str] | None = None,
    decision_tool_call_id: str | None = None,
) -> bool:
    """Validate and record an explicit model-owned decision acknowledgement.

    The framework chooses neither the horizon nor the plan action.  It only
    validates a bounded structured response for the currently pending boundary.
    A malformed or contradictory response leaves the boundary pending and the
    classification unknown.
    """
    if boundary not in {"initial", "replan"}:
        return False
    if execution_protocol_model_decision_boundary(context, agent_id) != boundary:
        return False
    boundary_state = load_execution_protocol_state(context, agent_id)
    request_sequence = (
        boundary_state.replan_requested_count if boundary == "replan" else 0
    )
    try:
        raw_aliases = available_tool_aliases or {}
        if not isinstance(raw_aliases, Mapping) or len(raw_aliases) > 256:
            raise ValueError("tool alias catalog must be a bounded mapping")
        trusted_names = available_tool_names or frozenset()
        if any(
            not isinstance(key, str)
            or key not in trusted_names
            or not isinstance(target, str)
            or not target.strip()
            or len(target.strip()) > 256
            for key, target in raw_aliases.items()
        ):
            raise ValueError("tool alias catalog is not bound to offered Tools")
        aliases = {key: target.strip() for key, target in raw_aliases.items()}
        update = _model_plan_update_with_semantics(
            context,
            plan_update,
            tool_identity_aliases=aliases,
            decision_call_id=decision_tool_call_id,
        )
        profile = (
            ModelExecutionProfile.from_mapping(execution_profile)
            if boundary == "initial"
            else None
        )
    except (TypeError, ValueError, KeyError):
        if boundary == "initial":
            _write_runtime_value(
                context,
                agent_id,
                EXECUTION_PROTOCOL_MODEL_PROFILE_KEY,
                {"status": "invalid", "classification": "unknown"},
            )
        return False
    trusted_tool_names = available_tool_names or frozenset()
    if (
        update.next_action_tool is not None
        and update.next_action_tool not in trusted_tool_names
    ):
        if boundary == "initial":
            _write_runtime_value(
                context,
                agent_id,
                EXECUTION_PROTOCOL_MODEL_PROFILE_KEY,
                {"status": "invalid", "classification": "unknown"},
            )
        return False
    if profile is not None and profile.horizon is not update.horizon:
        _write_runtime_value(
            context,
            agent_id,
            EXECUTION_PROTOCOL_MODEL_PROFILE_KEY,
            {"status": "invalid", "classification": "unknown"},
        )
        return False
    if profile is not None:
        profile_transition = record_model_execution_profile(
            context, agent_id, profile.to_dict()
        )
        if profile_transition is None:
            return False
    update_transition = _record_validated_model_plan_update(context, agent_id, update)
    acknowledged = bool(
        update_transition is not None
        and update_transition.decision.reason
        not in {
            DecisionReason.INVALID_EVENT,
            DecisionReason.PERSISTENCE_ERROR,
        }
        and execution_protocol_model_decision_boundary(context, agent_id) is None
    )
    if acknowledged:
        expected_scope = _model_decision_scope(context, agent_id)
        planning_semantic_fingerprint = _planning_semantic_fingerprint(update)

        def mark_acknowledged(current):
            attempts = _normalized_decision_attempts(
                current, expected_scope=expected_scope
            )
            previous = attempts[boundary]
            attempts[boundary] = {
                "attempt_count": min(
                    _MAX_DECISION_ATTEMPTS,
                    max(1, int(previous.get("attempt_count", 0) or 0) + 1),
                ),
                "request_sequence": request_sequence,
                "status": "acknowledged",
            }
            semantic_high_water = list(
                attempts.get("planning_semantic_high_water") or ()
            )
            materially_new_semantic = bool(
                planning_semantic_fingerprint is not None
                and planning_semantic_fingerprint not in semantic_high_water
                and len(semantic_high_water) < 16
            )
            if materially_new_semantic:
                semantic_high_water.append(planning_semantic_fingerprint)
            attempts["planning_semantic_high_water"] = semantic_high_water
            if boundary == "replan":
                if materially_new_semantic:
                    attempts["consecutive_unapplied_replans"] = 0
                else:
                    attempts["consecutive_unapplied_replans"] = min(
                        _MAX_TELEMETRY_COUNTER,
                        attempts["consecutive_unapplied_replans"] + 1,
                    )
            return attempts

        _update_runtime_value(
            context,
            agent_id,
            EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY,
            mark_acknowledged,
        )
        if boundary == "replan":
            _activate_convergence_after_unapplied_limit(context, agent_id)
    return acknowledged


def load_model_plan_update(context, agent_id: str) -> dict[str, Any]:
    """Return the latest validated model claim for this exact task scope."""
    update = load_execution_protocol_state(context, agent_id).model_plan_update
    return update.to_dict() if update is not None else {}


def consume_execution_protocol_guidance(context, agent_id: str) -> str | None:
    """Return one bounded control message, consuming it exactly once."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is not ProtocolMode.GUIDE:
        return None
    deadline_progress = _task_deadline_progress(context)

    def deadline_suffix(*, produce_candidate: bool) -> str:
        if deadline_progress is None:
            return ""
        _total, remaining, consumed = deadline_progress
        if consumed >= 0.80:
            return (
                " The caller deadline has crossed its 80% delivery-only "
                "checkpoint; use remaining actions only for delivery-impacting "
                f"validation, repair, or submission ({remaining:.0f}s remaining)."
            )
        if consumed >= 0.65:
            return (
                " The caller deadline has crossed its 65% validation "
                "checkpoint; do not expand the search space "
                f"({remaining:.0f}s remaining)."
            )
        if consumed >= HARD_CONVERGENCE_MIN_DEADLINE_FRACTION:
            return (
                (
                    " The caller deadline has crossed its 40% candidate "
                    "checkpoint; produce the smallest honest candidate now "
                )
                if produce_candidate
                else (
                    " The caller deadline has crossed its 40% convergence "
                    "checkpoint; stop broad exploration and validate, repair, "
                    "or submit the current candidate "
                )
            ) + f"({remaining:.0f}s remaining)."
        return ""

    mutation_gate = _read_runtime_value(
        context, agent_id, MUTATION_GATE_STATE_KEY
    )
    candidate_diagnostic_read_count = (
        _normalized_candidate_diagnostic_state(mutation_gate)[1]
        if isinstance(mutation_gate, Mapping)
        else _MAX_CANDIDATE_DIAGNOSTIC_READS
    )
    if (
        isinstance(mutation_gate, Mapping)
        and mutation_gate.get("active") is True
        and mutation_gate.get("convergence_stage")
        == ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT.value
        and mutation_gate.get("candidate_present") is True
        and mutation_gate.get("structured_quality_open") is True
    ):
        required = _bounded_counter(
            mutation_gate.get("structured_quality_required_table_count")
        )
        usable = _bounded_counter(
            mutation_gate.get("structured_quality_usable_table_count")
        )
        attempts = _bounded_counter(
            mutation_gate.get("structured_quality_repair_attempt_count")
        )
        reasons = [
            value
            for value in (
                mutation_gate.get("structured_quality_reason_codes") or ()
            )
            if isinstance(value, str)
        ][:3]
        reason_suffix = (
            " Observed reason(s): " + ", ".join(reasons) + "."
            if reasons
            else ""
        )
        if attempts < 1:
            return (
                "AWorld structured-quality debt: the current public candidate "
                f"represents {usable} of {required} declared table(s) as a "
                "usable cell structure. Make one bounded repair of the declared "
                "public deliverables now. Preserve source wording and geometry; "
                "replace descriptive prose with an explicit JSON cell grid and "
                "matching Markdown or HTML table, then validate the revised "
                "candidate. Do not restart source discovery or claim completion "
                "while this debt is open."
                + reason_suffix
                + deadline_suffix(produce_candidate=False)
            )
        return (
            "AWorld structured-quality debt remains after the bounded repair "
            f"attempt ({usable} of {required} declared table(s) usable). Use a "
            "registered validation if it can resolve the evidence; otherwise "
            "submit the limitation accurately without claiming complete table "
            "recovery. Do not loop through additional speculative repairs."
            + reason_suffix
            + deadline_suffix(produce_candidate=False)
        )
    if (
        isinstance(mutation_gate, Mapping)
        and mutation_gate.get("active") is True
        and mutation_gate.get("convergence_stage")
        == ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT.value
        and mutation_gate.get("candidate_present") is True
        and mutation_gate.get("candidate_binding_unresolved") is not True
        and _is_canonical_semantic_fingerprint(
            mutation_gate.get("candidate_fingerprint")
        )
    ):
        diagnostic_reads_remaining = max(
            0,
            _MAX_CANDIDATE_DIAGNOSTIC_READS
            - candidate_diagnostic_read_count,
        )
        exhausted_suffix = (
            " Ordinary exploration-only Tools are no longer admissible; use "
            "a declared-deliverable revision surface, registered validation, "
            "or submit without Tools."
            if diagnostic_reads_remaining == 0
            else ""
        )
        return (
            "AWorld mutation validation window: each current candidate permits "
            "up to three bounded, mechanically read-only diagnostic Tool calls, "
            "one per batch. No verifier or repair authorization is required for "
            "these diagnostics. Registered validation remains admitted and does "
            "not spend this allowance. "
            f"{diagnostic_reads_remaining} diagnostic call(s) remain; then make "
            "a declared revision, use an evidence-backed repair, or submit the "
            "current result accurately. Unknown, helper, and unrelated mutations "
            "remain blocked."
            + exhausted_suffix
            + deadline_suffix(produce_candidate=False)
        )
    state = load_execution_protocol_state(context, agent_id)
    if state.convergence_constraint_active:
        deadline_stage = None
        if deadline_progress is not None:
            consumed = deadline_progress[2]
            deadline_stage = (
                "delivery_only"
                if consumed >= 0.80
                else "validation_due"
                if consumed >= 0.65
                else "candidate_due"
                if consumed >= HARD_CONVERGENCE_MIN_DEADLINE_FRACTION
                else None
            )
        convergence_guidance_id = {
            "schema_version": "aworld.convergence-guidance/v1",
            "scope": _model_decision_scope(context, agent_id),
            "activation_count": state.convergence_constraint_activation_count,
            "stage": (
                state.convergence_stage.value
                if state.convergence_stage is not None
                else None
            ),
            "deadline_stage": deadline_stage,
        }
        if (
            _read_runtime_value(
                context,
                agent_id,
                EXECUTION_PROTOCOL_CONVERGENCE_GUIDANCE_KEY,
            )
            == convergence_guidance_id
        ):
            return None
        _write_runtime_value(
            context,
            agent_id,
            EXECUTION_PROTOCOL_CONVERGENCE_GUIDANCE_KEY,
            convergence_guidance_id,
        )
        convergence_deadline_suffix = deadline_suffix(
            produce_candidate=(
                state.convergence_stage is ConvergenceStage.PRODUCE_CANDIDATE
            )
        )
        if state.convergence_stage is ConvergenceStage.PRODUCE_CANDIDATE:
            return (
                "AWorld convergence constraint: repeated planning checkpoints "
                "did not produce an applied replan. Ordinary Tools remain "
                "available, but the execution phase is now constrained: create "
                "or modify the smallest honest inspectable candidate next. Do "
                "not resume broad read-only exploration. If no safe candidate "
                "can be produced from current evidence, submit uncertainty "
                "accurately instead of continuing reconnaissance."
                + convergence_deadline_suffix
            )
        return (
            "AWorld convergence constraint: an inspectable candidate exists. "
            "The next action may be one bounded validation against the public "
            "contract, one bounded revision targeting only declared public "
            "deliverables with an exact Tool-argument signature that is new for "
            "the current candidate, a repair directly supported by failed "
            "validation, or an accurate submission (current or uncertain). Each "
            "current candidate also permits up to three mechanically read-only "
            "diagnostic Tool calls, one per batch, without verifier or repair "
            "authorization; registered validation does not spend that allowance. "
            "After the quota is exhausted, revise a declared deliverable, use an "
            "evidence-backed repair, or submit. Each "
            "candidate-bound declared-revision signature is admitted once. Mixed "
            "batches do not widen admission: repeated revisions, additional "
            "declared revisions in the same batch, helper or unrelated mutations, "
            "and semantically unknown mutations remain blocked. Do not return to "
            "broad source, environment, or capability exploration. For a named "
            "output file, use a direct file write or an exact-file copy primitive; "
            "directory-ambiguous and recursive writers cannot cross this gate. "
            "Reuse retained evidence and unchanged observations."
            + convergence_deadline_suffix
        )
    if isinstance(mutation_gate, Mapping) and mutation_gate.get("active") is True:
        count = _bounded_counter(
            mutation_gate.get("consecutive_read_only_observations")
        )
        return (
            "AWorld mutation gate: this task was classified as requiring a "
            "workspace mutation, but no inspectable candidate or mutation has "
            f"been observed after {count} consecutive read-only observations. "
            "Create or modify the smallest relevant candidate now. Further "
            "provably read-only calls are gated until a mutation or candidate "
            "is observed; do not restart broad exploration."
        )
    deadline = _read_runtime_value(
        context, agent_id, EXECUTION_PROTOCOL_DEADLINE_GUIDANCE_KEY
    )
    if isinstance(deadline, Mapping) and deadline.get("pending_stage"):
        stage = deadline.get("pending_stage")
        _write_runtime_value(
            context,
            agent_id,
            EXECUTION_PROTOCOL_DEADLINE_GUIDANCE_KEY,
            {**dict(deadline), "pending_stage": None},
        )
        remaining = float(deadline.get("remaining_seconds", 0.0) or 0.0)
        if stage == "candidate_due":
            return (
                "AWorld deadline checkpoint: about 40% of caller-owned task time "
                "has elapsed without durable delivery progress. Stop broad source "
                "discovery and create the smallest honest inspectable candidate "
                "before further optimization. Reuse existing observations instead "
                f"of rereading unchanged inputs. Remaining time: {remaining:.0f}s."
            )
        if stage == "validation_due":
            return (
                "AWorld deadline checkpoint: about 65% of caller-owned task time "
                "has elapsed. Converge now: stop expanding exploration, validate "
                "the best current candidate against the public contract, and make "
                f"only evidence-driven repairs. Remaining time: {remaining:.0f}s."
            )
        if stage == "delivery_only":
            return (
                "AWorld deadline checkpoint: about 80% of caller-owned task time "
                "has elapsed. Use remaining actions only for delivery-impacting "
                "repairs and final validation; do not restart discovery or reread "
                f"unchanged inputs. Remaining time: {remaining:.0f}s."
            )
    pending = _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_PENDING_KEY)
    if not isinstance(pending, Mapping):
        return None
    _write_runtime_value(context, agent_id, EXECUTION_PROTOCOL_PENDING_KEY, None)
    action = pending.get("action")
    if action == ControllerAction.REQUEST_REPLAN.value:
        reason = pending.get("reason")
        missing_delivery_guidance = ""
        missing = _missing_public_deliverable_names(context)
        if missing:
            missing_delivery_guidance = (
                " The public task still has missing, blank, or placeholder "
                "named output file(s): "
                + ", ".join(missing)
                + ". Choose the next delivery intent explicitly; this signal "
                "does not force a command or claim that a write is safe."
            )
        if reason == "candidate_decision_reserve":
            candidate_state = (
                "present"
                if pending.get("candidate_present") is True
                else "absent"
                if pending.get("candidate_present") is False
                else "not publicly observable"
            )
            return (
                "AWorld candidate-decision reserve: ordinary Tools are still "
                "available, but the caller deadline is approaching the later "
                "tool-free finalization boundary. The observed candidate state "
                f"is {candidate_state}. Choose and justify one typed delivery "
                "intent: continue_exploration, produce_candidate, "
                "validate_candidate, submit_current, or submit_uncertain. For a "
                "Tool-backed choice, state a bounded next action and expected "
                "public observation; a submit choice enters Tool-free finalization. "
                "AWorld records whether the next observed action aligns with a "
                "Tool-backed choice; "
                "it does not select a command or decide correctness."
                + missing_delivery_guidance
            )
        if reason == "next_action_mismatch":
            return (
                "AWorld plan/action alignment checkpoint: the last observed Tool "
                "outcome did not implement the typed delivery intent from the "
                "previous plan. Reassess the evidence, then keep or revise the "
                "intent and declare one new bounded next action. This receipt does "
                "not judge task correctness or prohibit the action that ran."
                + missing_delivery_guidance
            )
        if reason == "delivery_debt_detected":
            return (
                "AWorld delivery-debt checkpoint: a concrete public deliverable "
                "remains absent across bounded Tool observations. Choose the next "
                "delivery intent explicitly: continue_exploration when one more "
                "discriminating observation is justified, produce_candidate when "
                "an honest inspectable candidate is possible, validate_candidate "
                "when a candidate exists, submit_current when the best current "
                "result is ready, or submit_uncertain when a material gap cannot "
                "be resolved safely. Include an evidence-linked rationale and, "
                "for a Tool-backed choice, one bounded next action. Submit choices "
                "enter Tool-free finalization. This is an advisory accounting boundary and "
                "does not force a command." + missing_delivery_guidance
            )
        return (
            "AWorld long-horizon checkpoint: the framework observed a bounded "
            "repetition or low-evidence signal. Judge from the actual task and "
            "observations whether the current approach is still making useful "
            "progress. Continue it when warranted only with a bounded next action "
            "that is expected to change a concrete decision. Otherwise revise the "
            "approach and prioritize creating, updating, or validating inspectable "
            "milestone evidence. Choose the next delivery intent explicitly when "
            "a concrete deliverable is pending; continue_exploration remains a "
            "valid evidence-linked model choice. This checkpoint is advisory, "
            "keeps all normal Tools available, does not force a command, and is "
            "not evidence of task completion." + missing_delivery_guidance
        )
    if action == ControllerAction.ENTER_FINALIZATION.value:
        return (
            "AWorld long-horizon finalization reserve: preserve the best current "
            "work and stop open-ended exploration. Use the remaining budget only "
            "for bounded verification, a concrete repair supported by observed "
            "evidence, or an accurate final response. Internal uncertainty must "
            "not be presented as verified success."
        )
    return None


def record_candidate_final(
    context,
    agent_id: str,
    actions: Any = None,
    *,
    review_boundary_available: bool | None = None,
) -> ProtocolTransition | None:
    """Record a candidate final result and decide whether one review is due."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
    store = ExecutionProtocolStore(context, agent_id, policy)
    if store.load().review_pending:
        transition = store.apply(
            ExecutionProtocolEvent(
                kind=EventKind.REVIEW_RESULT,
                # Reaching this boundary means the model-owned reviewer
                # returned a complete candidate response.  Infrastructure
                # errors and budget stops are handled before this call and may
                # never be reclassified as acceptance.  Independent review
                # still requires its separate probe-backed typed decision.
                review_outcome=(
                    ReviewOutcome.UNKNOWN
                    if policy.independent_acceptance_enabled
                    else ReviewOutcome.ACCEPT
                ),
            )
        )
        _record_transition_metrics(context, transition)
        return transition
    transition = store.apply(
        ExecutionProtocolEvent(
            kind=EventKind.CANDIDATE_FINAL,
            result_hash=_candidate_review_basis(context, agent_id, actions),
            review_boundary_available=review_boundary_available,
            public_deliverable_declared=bool(
                _public_delivery_status(state_context(context)).get(
                    "public_deliverable_declared"
                )
            ),
        )
    )
    _record_transition_metrics(context, transition)
    return transition


def record_review_repair_decision(
    context, agent_id: str, value: Any
) -> ProtocolTransition | None:
    """Serialize model-review validation, apply, and repair-auth minting."""

    owner = state_context(context)
    transaction = getattr(owner, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            return _record_review_repair_decision_locked(
                context, agent_id, value
            )
    return _record_review_repair_decision_locked(context, agent_id, value)


def _record_review_repair_decision_locked(
    context, agent_id: str, value: Any
) -> ProtocolTransition | None:
    """Apply one explicit, strictly structured non-critic repair decision.

    Ordinary Tool use during model-owned reflection is not a repair decision.
    The caller must strip this control object before dispatching the Tool.
    """
    policy = execution_protocol_policy(context, agent_id)
    if (
        policy.mode is ProtocolMode.OFF
        or policy.independent_acceptance_enabled
        or not isinstance(value, Mapping)
        or set(value) != {"decision", "reason"}
        or value.get("decision") != ReviewOutcome.REPAIR.value
        or not isinstance(value.get("reason"), str)
        or not value["reason"].strip()
        or len(value["reason"].strip()) > 1024
    ):
        return None
    store = ExecutionProtocolStore(context, agent_id, policy)
    review_pending_before = store.load().review_pending
    if not review_pending_before:
        return None
    transition = store.apply(
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.REPAIR,
        )
    )
    _record_transition_metrics(context, transition)
    if transition.decision.reason is DecisionReason.PERSISTENCE_ERROR:
        return transition
    if (
        review_pending_before
        and transition.decision.action is ControllerAction.REQUEST_REPAIR
    ):
        from aworld.core.context.compiler import semantic_fingerprint

        _mint_review_repair_authorization(
            context,
            agent_id,
            source=_REPAIR_SOURCE_MODEL_REVIEW,
            evidence={
                "review_decision": ReviewOutcome.REPAIR.value,
                "review_reason_hash": semantic_fingerprint(value["reason"].strip()),
            },
        )
    return transition


def record_review_tool_action(context, agent_id: str) -> ProtocolTransition | None:
    """Compatibility hook for ordinary review Tool calls.

    Tool use is evidence gathering or concrete work, not an implicit repair
    decision.  Only :func:`record_review_repair_decision` may enter REPAIR.
    """
    return None


def record_review_error(context, agent_id: str) -> ProtocolTransition | None:
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
    store = ExecutionProtocolStore(context, agent_id, policy)
    if not store.load().review_pending:
        return None
    transition = store.apply(
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.ERROR,
        )
    )
    _record_transition_metrics(context, transition)
    return transition


def store_candidate_fallback(context, agent_id: str, actions) -> None:
    """Persist a bounded textual candidate outside the user's workspace."""
    rows = []
    for action in actions or ():
        text = str(getattr(action, "policy_info", "") or "").strip()
        if text:
            rows.append({"policy_info": text[:_MAX_FALLBACK_CHARS]})
    _write_runtime_value(
        context,
        agent_id,
        EXECUTION_PROTOCOL_FALLBACK_KEY,
        (
            {
                "scope": {
                    "task_id": str(getattr(context, "task_id", "") or ""),
                    "task_epoch": int(getattr(context, "task_epoch", 0) or 0),
                    "agent_id": agent_id,
                },
                "actions": rows[:1],
            }
            if rows
            else None
        ),
    )
    try:
        from aworld.runners.post_tool_progress import (
            refresh_public_probe_receipt_projection,
        )

        refresh_public_probe_receipt_projection(context, agent_id=agent_id)
    except Exception:
        pass


def load_candidate_fallback(context, agent_id: str):
    payload = _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_FALLBACK_KEY)
    expected_scope = {
        "task_id": str(getattr(context, "task_id", "") or ""),
        "task_epoch": int(getattr(context, "task_epoch", 0) or 0),
        "agent_id": agent_id,
    }
    if not isinstance(payload, Mapping) or payload.get("scope") != expected_scope:
        if payload is not None:
            clear_candidate_fallback(context, agent_id)
        return None
    actions = payload.get("actions") if isinstance(payload, Mapping) else None
    if not isinstance(actions, list) or not actions:
        return None
    text = actions[0].get("policy_info") if isinstance(actions[0], Mapping) else None
    if not isinstance(text, str) or not text.strip():
        return None
    from aworld.core.common import ActionModel

    return (ActionModel(agent_name=agent_id, policy_info=text),)


def clear_candidate_fallback(context, agent_id: str) -> None:
    _write_runtime_value(context, agent_id, EXECUTION_PROTOCOL_FALLBACK_KEY, None)
    try:
        from aworld.runners.post_tool_progress import (
            refresh_public_probe_receipt_projection,
        )

        refresh_public_probe_receipt_projection(context, agent_id=agent_id)
    except Exception:
        pass


def execution_protocol_requires_tool_free_finalization(context, agent_id: str) -> bool:
    """Return true when the caller deadline or convergence latch finalizes.

    A model-requested repair is deliberately not a finalization state.  After
    the review model attaches an explicit structured repair decision to a
    concrete Tool call, normal execution continues under the original task
    budget until the model emits a new candidate final response.  Separately,
    rejected calls may finalize a contractless task whose only remaining
    action was already model-bound.  They must not manufacture completion when
    the public task still has a declared deliverable and no candidate exists;
    in that case the production gate stays active until a real candidate or
    the outer task budget ends.  After a candidate exists, rejected batches
    following exhaustion of its diagnostic quota may establish the same fact
    for revision/validation.  A durable eligible latch forces the next turn
    Tool-free; admitted candidate progress changes the convergence stage
    before the pre-generation boundary.
    """
    owner = state_context(context)
    transaction = getattr(owner, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            return _execution_protocol_requires_tool_free_finalization_locked(
                context, agent_id
            )
    return _execution_protocol_requires_tool_free_finalization_locked(
        context, agent_id
    )


def _execution_protocol_requires_tool_free_finalization_locked(
    context,
    agent_id: str,
) -> bool:
    """Resolve a persisted latch only at the pre-generation boundary."""

    from aworld.core.execution_protocol import ProtocolPhase

    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is not ProtocolMode.GUIDE:
        return False
    state = ExecutionProtocolStore(context, agent_id, policy).load()
    if state.phase is ProtocolPhase.FINALIZE:
        return True
    gate = _read_runtime_value(context, agent_id, MUTATION_GATE_STATE_KEY)
    if (
        not isinstance(gate, Mapping)
        or gate.get("active") is not True
        or not _gate_matches_current_scope(context, agent_id, gate)
    ):
        return False
    stage = gate.get("convergence_stage")
    _pre_count, pre_candidate_latched, _pre_state_is_canonical = (
        _normalized_pre_candidate_rejection_state(gate)
    )
    pre_candidate_exhausted = bool(
        stage == ConvergenceStage.PRODUCE_CANDIDATE.value
        and pre_candidate_latched
    )
    candidate_exhausted = False
    if stage == ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT.value:
        _mask, diagnostic_count, _canonical = (
            _normalized_candidate_diagnostic_state(gate)
        )
        _rejection_mask, _rejections, latched, _latch_is_canonical = (
            _normalized_exhausted_rejection_state(
                gate,
                gate.get("candidate_fingerprint"),
            )
        )
        _pure_count, pure_latched, _pure_is_canonical = (
            _normalized_pure_rejection_state(
                gate,
                gate.get("candidate_fingerprint"),
            )
        )
        # Pure rejected batches have an independent budget, while the tighter
        # legacy latch still applies once diagnostic allowance is exhausted.
        candidate_exhausted = bool(
            pure_latched
            or (
                diagnostic_count >= _MAX_CANDIDATE_DIAGNOSTIC_READS
                and latched
            )
        )
    if not (pre_candidate_exhausted or candidate_exhausted):
        return False
    transition = _apply_event(
        context,
        agent_id,
        ExecutionProtocolEvent(kind=EventKind.CONVERGENCE_EXHAUSTED),
    )
    if (
        transition.decision.action is ControllerAction.ENTER_FINALIZATION
        and transition.decision.reason is not DecisionReason.PERSISTENCE_ERROR
        and transition.state.phase is ProtocolPhase.FINALIZE
    ):
        _record_pending_checkpoint(context, agent_id, transition)
        return True
    # A concurrent caller may have completed the idempotent transition after
    # our initial load.  Only durable typed FINALIZE state authorizes a
    # Tool-free model turn; a malformed gate or persistence failure cannot.
    return (
        ExecutionProtocolStore(context, agent_id, policy).load().phase
        is ProtocolPhase.FINALIZE
    )


def final_review_guidance(
    transition: ProtocolTransition | None,
    *,
    independent_acceptance_enabled: bool = True,
) -> str | None:
    if (
        transition is None
        or transition.decision.action is not ControllerAction.REQUEST_FINAL_REVIEW
    ):
        return None
    if not independent_acceptance_enabled:
        return (
            "AWorld model-owned completion reflection: autonomously reassess "
            "the public request, the candidate response, and observations from "
            "this run. This is solver self-review, not independent framework "
            "acceptance. No trusted independent validation contract is active, "
            "so do not request or invent framework-owned acceptance evidence. "
            "Ordinary Tool calls may inspect or verify concrete state and remain "
            "part of this review. If a concrete Tool call begins a necessary "
            "repair, mark only that call with the structured "
            "__aworld_review_decision object whose decision is repair and whose "
            "reason identifies the observed material gap; otherwise omit that "
            "control object. When the candidate is adequate, return the best "
            "current final response and state material uncertainty accurately."
        )
    return (
        "AWorld model-owned completion review with independent acceptance: "
        "the next provider request is "
        "rebuilt from bounded public task, candidate, and framework evidence; "
        "solver reasoning is excluded. Identify the highest-risk counterexample "
        "and execute one fresh equivalent probe. A later terminal decision must "
        "be exactly accept, repair, or uncertain, and accept requires the matching "
        "successful framework probe receipt. Solver self-tests alone are not "
        "acceptance evidence."
    )


__all__ = [
    "bind_pending_next_action_call",
    "EXECUTION_PROTOCOL_METRICS_KEY",
    "EXECUTION_PROTOCOL_FALLBACK_KEY",
    "EXECUTION_PROTOCOL_MODEL_PROFILE_KEY",
    "EXECUTION_PROTOCOL_MODEL_DECISIONS_KEY",
    "EXECUTION_PROTOCOL_HYPOTHESES_KEY",
    "EXECUTION_PROTOCOL_CRITIC_KEY",
    "EXECUTION_PROTOCOL_CONVERGENCE_GUIDANCE_KEY",
    "EXECUTION_PROTOCOL_PENDING_KEY",
    "EXECUTION_PROTOCOL_POLICY_KEY",
    "EXECUTION_PROTOCOL_PUBLIC_PROBES_KEY",
    "configure_execution_protocol",
    "constrain_candidate_convergence_tool_catalog",
    "acceptance_critic_active",
    "clear_acceptance_critic_state",
    "clear_candidate_fallback",
    "consume_execution_protocol_guidance",
    "build_execution_protocol_telemetry",
    "execution_protocol_policy",
    "execution_protocol_control_eligible",
    "execution_protocol_accepts_model_profile",
    "execution_protocol_model_decision_boundary",
    "execution_protocol_requires_tool_free_finalization",
    "framework_observable_validation_kind",
    "final_review_guidance",
    "model_owned_review_active",
    "record_candidate_final",
    "record_model_execution_profile",
    "record_model_decision_boundary",
    "record_model_decision_attempt_failure",
    "record_model_decision_unavailable",
    "record_model_plan_update",
    "record_pre_generation_delivery_decision",
    "record_public_probe_observations",
    "record_public_probe_plan",
    "record_tool_hypotheses",
    "record_acceptance_probe_plan",
    "record_acceptance_probe_observation",
    "record_acceptance_critic_decision",
    "record_review_error",
    "record_review_repair_decision",
    "record_review_tool_action",
    "record_tool_protocol_event",
    "load_candidate_fallback",
    "load_execution_protocol_state",
    "load_model_plan_update",
    "MutationGateRejectionReason",
    "mutation_gate_interception",
    "load_public_probe_receipts",
    "load_acceptance_critic_state",
    "project_execution_protocol_telemetry",
    "store_candidate_fallback",
]
