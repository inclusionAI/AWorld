"""Runtime integration helpers for the domain-independent execution protocol.

This module converts existing framework observations into text-free protocol
events.  It never decides whether the user's task is correct and it never
writes control state into the user's workspace.
"""

from __future__ import annotations

from copy import deepcopy
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
_MAX_FALLBACK_CHARS = 64_000


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
    _write_runtime_value(context, agent_id, EXECUTION_PROTOCOL_POLICY_KEY, policy.to_dict())


def execution_protocol_policy(context, agent_id: str) -> ExecutionProtocolPolicy:
    value = _read_runtime_value(context, agent_id, EXECUTION_PROTOCOL_POLICY_KEY)
    try:
        return ExecutionProtocolPolicy.from_dict(value)
    except (TypeError, ValueError, KeyError):
        return ExecutionProtocolPolicy()


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
        evidence_advanced=bool(
            semantic_state.get("validation_evidence_advanced")
            or semantic_state.get("completion_advanced")
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


def execution_protocol_requires_tool_free_finalization(
    context, agent_id: str
) -> bool:
    """Return true after the one permitted review repair Tool batch."""
    from aworld.core.execution_protocol import ProtocolPhase

    policy = execution_protocol_policy(context, agent_id)
    if policy.mode is ProtocolMode.OFF:
        return False
    state = ExecutionProtocolStore(context, agent_id, policy).load()
    return (
        state.phase is ProtocolPhase.REPAIR
        and state.repair_count > 0
        and not state.review_pending
    )


def final_review_guidance(transition: ProtocolTransition | None) -> str | None:
    if (
        transition is None
        or transition.decision.action is not ControllerAction.REQUEST_FINAL_REVIEW
    ):
        return None
    return (
        "AWorld long-horizon final review: before ending, reconcile the public "
        "request and current completion claims with observations from this run. "
        "Check whether later state-changing actions made earlier evidence stale. "
        "If a specific observed contradiction or material evidence gap can be "
        "addressed by one bounded Tool action, take that action now. Otherwise, "
        "return the best current final response and state material uncertainty "
        "accurately. Do not invent evidence, start broad exploration, or assume "
        "that lack of proof is itself a failure."
    )


__all__ = [
    "EXECUTION_PROTOCOL_METRICS_KEY",
    "EXECUTION_PROTOCOL_FALLBACK_KEY",
    "EXECUTION_PROTOCOL_MODEL_PROFILE_KEY",
    "EXECUTION_PROTOCOL_PENDING_KEY",
    "EXECUTION_PROTOCOL_POLICY_KEY",
    "configure_execution_protocol",
    "clear_candidate_fallback",
    "consume_execution_protocol_guidance",
    "execution_protocol_policy",
    "execution_protocol_accepts_model_profile",
    "execution_protocol_requires_tool_free_finalization",
    "final_review_guidance",
    "record_candidate_final",
    "record_model_execution_profile",
    "record_review_error",
    "record_review_tool_action",
    "record_tool_protocol_event",
    "load_candidate_fallback",
    "store_candidate_fallback",
]
