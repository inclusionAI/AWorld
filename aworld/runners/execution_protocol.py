"""Runtime integration helpers for the domain-independent execution protocol.

This module converts existing framework observations into text-free protocol
events.  It never decides whether the user's task is correct and it never
writes control state into the user's workspace.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
import re
import secrets
from typing import Any, Mapping

from aworld.core.context.execution_state import state_context
from aworld.core.execution_protocol import (
    ControllerAction,
    EventKind,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolStore,
    ModelExecutionProfile,
    ProtocolMode,
    ProtocolTransition,
    ReviewOutcome,
)


EXECUTION_PROTOCOL_POLICY_KEY = "execution_protocol_policy"
EXECUTION_PROTOCOL_PENDING_KEY = "execution_protocol_pending_guidance"
EXECUTION_PROTOCOL_METRICS_KEY = "execution_protocol_metrics"
EXECUTION_PROTOCOL_FALLBACK_KEY = "execution_protocol_candidate_fallback"
EXECUTION_PROTOCOL_MODEL_PROFILE_KEY = "execution_protocol_model_profile_attempt"
EXECUTION_PROTOCOL_HYPOTHESES_KEY = "execution_protocol_hypotheses"
EXECUTION_PROTOCOL_CRITIC_KEY = "execution_protocol_acceptance_critic"
INDEPENDENT_ACCEPTANCE_CRITIC_ENV = "AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC"
SEMANTIC_PROGRESS_LEDGER_ENV = "AWORLD_SEMANTIC_PROGRESS_LEDGER"
_MAX_FALLBACK_CHARS = 64_000
_MAX_TELEMETRY_COUNTER = 1_000_000


def _context_key(base: str, agent_id: str) -> str:
    return f"{base}:{agent_id}"


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
    context_info = getattr(owner, "context_info", None)
    if isinstance(context_info, dict):
        scoped_key = _context_key(key, agent_id)
        if value is None:
            context_info.pop(scoped_key, None)
        else:
            context_info[scoped_key] = deepcopy(value)


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
    return (
        context_info.get(_context_key(key, agent_id))
        if isinstance(context_info, Mapping)
        else None
    )


def configure_execution_protocol(
    context, agent_id: str, policy: ExecutionProtocolPolicy
) -> None:
    """Publish a validated policy for Tool-boundary transport copies."""
    if not isinstance(policy, ExecutionProtocolPolicy):
        raise TypeError("policy must be ExecutionProtocolPolicy")
    acceptance_flag = os.environ.get(INDEPENDENT_ACCEPTANCE_CRITIC_ENV)
    semantic_flag = os.environ.get(SEMANTIC_PROGRESS_LEDGER_ENV)
    policy = replace(
        policy,
        independent_acceptance_enabled=(
            policy.independent_acceptance_enabled
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


def acceptance_critic_active(context, agent_id: str) -> bool:
    raw = os.environ.get(INDEPENDENT_ACCEPTANCE_CRITIC_ENV)
    if raw is not None and raw.strip().lower() in {"0", "false", "no", "off"}:
        return False
    policy = execution_protocol_policy(context, agent_id)
    if not policy.independent_acceptance_enabled or policy.mode is ProtocolMode.OFF:
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
        PROBE_KINDS,
        probe_arguments_are_executable,
    )

    if probe_kind not in PROBE_KINDS or not probe_arguments_are_executable(
        arguments_projection
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
            "candidate": candidate[:64_000],
            "evidence": evidence,
            "artifact_before": evidence.get("artifact_fingerprint"),
            "probe_kind": probe_kind,
            "challenge": secrets.token_hex(16),
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
    call_ids = {
        getattr(action, "tool_call_id", None)
        for action in actions or ()
        if isinstance(getattr(action, "tool_call_id", None), str)
    }
    if current.get("tool_call_id") not in call_ids:
        return False
    if not isinstance(result_projection, Mapping) or result_projection.get(
        "tool_call_id"
    ) != current.get("tool_call_id"):
        return False
    from aworld.core.execution_protocol.acceptance import validate_probe_result

    validated, validation_code = validate_probe_result(
        probe_kind=current["probe_kind"],
        tool_identity=current["tool_identity"],
        arguments=current["arguments_projection"],
        result=result_projection,
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
        result_projection=result_projection,
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


def record_acceptance_critic_decision(
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
    transition = ExecutionProtocolStore(context, agent_id, policy).apply(
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=outcome,
        )
    )
    _record_transition_metrics(context, transition)
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


def project_execution_protocol_telemetry(value: Any) -> dict[str, Any] | None:
    """Validate and bound the only public execution-protocol projection."""
    if not isinstance(value, Mapping) or value.get("schema_version") != (
        "aworld.execution-protocol-telemetry/v1"
    ):
        return None
    required = {"mode", "phase", "armed"}
    if not required.issubset(value):
        return None
    enums = {
        "mode": {"off", "observe", "guide"},
        "phase": {"execute", "finalize", "review", "repair", "complete"},
        "model_horizon": {"short", "long"},
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
        "acceptance_continuation_suppressed": {"deadline_reserve"},
    }
    booleans = {
        "armed",
        "finalization_entered",
        "implicit_acceptance_created",
        "acceptance_satisfied",
    }
    counters = {
        "event_count",
        "tool_observation_count",
        "stagnant_observations",
        "replan_count",
        "candidate_final_count",
        "final_review_count",
        "repair_count",
        "acceptance_attempt",
        "acceptance_continuation_count",
        "acceptance_controller_error_count",
    }
    allowed = {"schema_version", *enums, *booleans, *counters}
    if set(value) - allowed:
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
    telemetry = {
        "schema_version": "aworld.execution-protocol-telemetry/v1",
        "mode": policy.mode.value,
        "phase": state.phase.value,
        "armed": state.long_horizon_armed,
        "event_count": state.event_count,
        "tool_observation_count": state.tool_observation_count,
        "stagnant_observations": state.stagnant_observations,
        "replan_count": state.replan_count,
        "candidate_final_count": state.candidate_final_count,
        "final_review_count": state.final_review_count,
        "repair_count": state.repair_count,
        "finalization_entered": state.finalization_entered,
        "model_horizon": (
            state.model_execution_profile.horizon.value
            if state.model_execution_profile is not None
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


def _record_transition_metrics(context, transition: ProtocolTransition) -> None:
    owner = state_context(context)
    if owner is None:
        return
    metrics = owner.context_info.get(EXECUTION_PROTOCOL_METRICS_KEY)
    if not isinstance(metrics, dict):
        metrics = {}
    action = transition.decision.action.value
    reason = transition.decision.reason.value
    metrics["event_count"] = transition.state.event_count
    metrics["tool_observation_count"] = transition.state.tool_observation_count
    metrics["replan_count"] = transition.state.replan_count
    metrics["final_review_count"] = transition.state.final_review_count
    metrics["repair_count"] = transition.state.repair_count
    metrics["long_horizon_armed"] = transition.state.long_horizon_armed
    if transition.state.model_execution_profile is not None:
        metrics["model_horizon"] = (
            transition.state.model_execution_profile.horizon.value
        )
        metrics["model_confidence"] = (
            transition.state.model_execution_profile.confidence
        )
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


def record_tool_protocol_event(
    context, agent_id: str, semantic_state: Mapping[str, Any]
) -> ProtocolTransition | None:
    """Project one existing semantic Tool observation into protocol state."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
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
        current_step=int(semantic_state.get("current_agent_step", 0) or 0),
        remaining_seconds=_remaining_task_seconds(context),
        operation_hash=semantic_state.get("operation_hash"),
        result_hash=semantic_state.get("result_hash"),
    )
    transition = _apply_event(context, agent_id, event)
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
            },
        )
    return transition


def execution_protocol_accepts_model_profile(context, agent_id: str) -> bool:
    """Return whether one optional model profile may still be offered."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return False
    state = ExecutionProtocolStore(context, agent_id, policy).load()
    return (
        not state.long_horizon_armed
        and state.model_execution_profile is None
        and _read_runtime_value(
            context,
            agent_id,
            EXECUTION_PROTOCOL_MODEL_PROFILE_KEY,
        )
        is None
    )


def record_model_execution_profile(
    context,
    agent_id: str,
    value: Mapping[str, Any],
) -> ProtocolTransition | None:
    """Validate and record one content-free model activation assessment.

    Malformed or repeated assessments fail open and never block ordinary Tool
    execution. The attempt marker prevents a bad model response from injecting
    the same control metadata on every later turn.
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
            {"status": "invalid"},
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


def consume_execution_protocol_guidance(context, agent_id: str) -> str | None:
    """Return one bounded control message, consuming it exactly once."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is not ProtocolMode.GUIDE:
        return None
    pending = _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_PENDING_KEY)
    if not isinstance(pending, Mapping):
        return None
    _write_runtime_value(context, agent_id, EXECUTION_PROTOCOL_PENDING_KEY, None)
    action = pending.get("action")
    if action == ControllerAction.REQUEST_REPLAN.value:
        _apply_event(
            context,
            agent_id,
            ExecutionProtocolEvent(kind=EventKind.REPLAN_APPLIED),
        )
        try:
            from aworld.runners.post_tool_progress import (
                acknowledge_semantic_checkpoint,
            )

            acknowledge_semantic_checkpoint(context, agent_id=agent_id)
        except Exception:
            pass
        return (
            "AWorld long-horizon checkpoint: recent observed actions have not "
            "produced enough new evidence. Reconcile the rolling plan with the "
            "actual observations, retire assumptions or approaches contradicted "
            "by evidence, and choose one bounded next action that can materially "
            "change the decision. Do not treat this checkpoint or a revised plan "
            "as evidence of task completion."
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


def record_candidate_final(context, agent_id: str) -> ProtocolTransition | None:
    """Record a candidate final result and decide whether one review is due."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
    store = ExecutionProtocolStore(context, agent_id, policy)
    if store.load().review_pending:
        transition = store.apply(
            ExecutionProtocolEvent(
                kind=EventKind.REVIEW_RESULT,
                review_outcome=ReviewOutcome.UNKNOWN,
            )
        )
        _record_transition_metrics(context, transition)
        return transition
    transition = store.apply(ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL))
    _record_transition_metrics(context, transition)
    return transition


def record_review_tool_action(context, agent_id: str) -> ProtocolTransition | None:
    """A concrete Tool action is the only repair signal accepted from review."""
    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return None
    store = ExecutionProtocolStore(context, agent_id, policy)
    if not store.load().review_pending:
        return None
    transition = store.apply(
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.REPAIR,
        )
    )
    _record_transition_metrics(context, transition)
    return transition


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


def execution_protocol_requires_tool_free_finalization(context, agent_id: str) -> bool:
    """Return true when the deadline/stagnation protocol reserved finalization.

    A model-requested repair is deliberately not a finalization state.  After
    the review model uses a Tool to signal that work is incomplete, normal
    execution continues under the original task budget until the model emits a
    new candidate final response.
    """
    from aworld.core.execution_protocol import ProtocolPhase

    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is not ProtocolMode.GUIDE:
        return False
    state = ExecutionProtocolStore(context, agent_id, policy).load()
    return state.phase is ProtocolPhase.FINALIZE


def final_review_guidance(transition: ProtocolTransition | None) -> str | None:
    if (
        transition is None
        or transition.decision.action is not ControllerAction.REQUEST_FINAL_REVIEW
    ):
        return None
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
    "EXECUTION_PROTOCOL_METRICS_KEY",
    "EXECUTION_PROTOCOL_FALLBACK_KEY",
    "EXECUTION_PROTOCOL_MODEL_PROFILE_KEY",
    "EXECUTION_PROTOCOL_HYPOTHESES_KEY",
    "EXECUTION_PROTOCOL_CRITIC_KEY",
    "EXECUTION_PROTOCOL_PENDING_KEY",
    "EXECUTION_PROTOCOL_POLICY_KEY",
    "configure_execution_protocol",
    "acceptance_critic_active",
    "clear_acceptance_critic_state",
    "clear_candidate_fallback",
    "consume_execution_protocol_guidance",
    "build_execution_protocol_telemetry",
    "execution_protocol_policy",
    "execution_protocol_accepts_model_profile",
    "execution_protocol_requires_tool_free_finalization",
    "final_review_guidance",
    "record_candidate_final",
    "record_model_execution_profile",
    "record_tool_hypotheses",
    "record_acceptance_probe_plan",
    "record_acceptance_probe_observation",
    "record_acceptance_critic_decision",
    "record_review_error",
    "record_review_tool_action",
    "record_tool_protocol_event",
    "load_candidate_fallback",
    "load_execution_protocol_state",
    "load_acceptance_critic_state",
    "project_execution_protocol_telemetry",
    "store_candidate_fallback",
]
