import time
from typing import Any

from aworld.core.common import ActionModel, Observation
from aworld.utils.serialized_util import to_serializable

WATCHDOG_STATE_KEY = "post_tool_progress_watchdog"
WATCHDOG_METRICS_KEY = "post_tool_progress_metrics"
SEMANTIC_PROGRESS_KEY = "context_semantic_progress"
_SEMANTIC_RUNTIME_KEY = "semantic_progress"
_POST_TOOL_TURNS_RUNTIME_KEY = "post_tool_turns"
_RECENT_SEMANTIC_PAIR_WINDOW = 8
_PROGRESS_GUARD_REPEAT_THRESHOLD = 3



def _select_semantic_state(shared: Any, local: Any) -> dict[str, Any] | None:
    """Choose the newest typed state while retaining ContextState compatibility."""
    shared_state = shared if isinstance(shared, dict) else None
    local_state = local if isinstance(local, dict) else None
    if shared_state is None:
        return local_state
    if local_state is None or local_state == shared_state:
        return shared_state
    shared_revision = shared_state.get("runtime_revision")
    local_revision = local_state.get("runtime_revision")
    # Direct ContextState injection is a supported test/configuration boundary.
    # A value without a runtime revision is therefore an explicit override, not
    # an older transport snapshot.
    if local_revision is None:
        return local_state
    if isinstance(local_revision, int) and isinstance(shared_revision, int):
        return local_state if local_revision > shared_revision else shared_state
    return shared_state


def _runtime_context(context):
    if context is None:
        return None
    event_manager = getattr(context, "event_manager", None)
    root_context = (
        getattr(event_manager, "context", None) if event_manager is not None else None
    )
    return root_context or context


def _metrics_dict(context) -> dict[str, Any]:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return {}
    metrics = runtime_context.context_info.get(WATCHDOG_METRICS_KEY)
    if not isinstance(metrics, dict):
        metrics = {}
        runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    return metrics


def increment_watchdog_metric(context, key: str, delta: int = 1) -> int:
    metrics = _metrics_dict(context)
    metrics[key] = int(metrics.get(key, 0) or 0) + delta
    runtime_context = _runtime_context(context)
    runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    return metrics[key]


def record_adaptive_context_metrics(
    context,
    *,
    checkpoint: bool = False,
    no_progress_checkpoint: bool = False,
    escalation_level: int = 0,
    progress_reset: bool = False,
) -> None:
    """Record bounded adaptive-policy evidence on the runtime Context."""
    if (
        isinstance(escalation_level, bool)
        or not isinstance(escalation_level, int)
        or escalation_level < 0
    ):
        raise ValueError("escalation_level must be non-negative")

    metrics = _metrics_dict(context)

    def count_value(key: str) -> int:
        value = metrics.get(key, 0)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else 0
        )

    if checkpoint:
        metrics["adaptive_checkpoint_count"] = (
            count_value("adaptive_checkpoint_count") + 1
        )
    if no_progress_checkpoint:
        metrics["adaptive_no_progress_checkpoint_count"] = (
            count_value("adaptive_no_progress_checkpoint_count") + 1
        )
    if escalation_level > 0:
        metrics["adaptive_escalation_count"] = (
            count_value("adaptive_escalation_count") + 1
        )
        metrics["adaptive_escalation_level_max"] = max(
            count_value("adaptive_escalation_level_max"),
            escalation_level,
        )
    if progress_reset:
        metrics["adaptive_goal_progress_reset_count"] = (
            count_value("adaptive_goal_progress_reset_count") + 1
        )
    runtime_context = _runtime_context(context)
    if runtime_context is not None:
        runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics


def record_semantic_tool_progress(
    context,
    *,
    tool_name: str,
    agent_id: str,
    actions: list[ActionModel],
    observation: Observation,
) -> dict[str, Any] | None:
    """Record bounded hashes for repetition and low-information-gain signals."""
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return None
    from aworld.core.context.compiler import (
        semantic_fingerprint,
        semantic_result_fingerprint,
    )

    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    previous = (
        shared_reader(agent_id, _SEMANTIC_RUNTIME_KEY)
        if callable(shared_reader)
        else None
    )
    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        state_by_agent = {}
    previous = _select_semantic_state(previous, state_by_agent.get(agent_id))
    if not isinstance(previous, dict):
        previous = {}

    serialized_observation = to_serializable(observation)
    action_results = (
        serialized_observation.get("action_result", [])
        if isinstance(serialized_observation, dict)
        else []
    )
    artifact_receipts = [
        metadata.get("context_management")
        for result in action_results
        if isinstance(result, dict)
        for metadata in (result.get("metadata"),)
        if isinstance(metadata, dict)
        and isinstance(metadata.get("context_management"), dict)
    ]
    artifact_changed = any(
        receipt.get("artifact_changed") is True for receipt in artifact_receipts
    )
    rollback_performed = any(
        receipt.get("rollback_performed") is True for receipt in artifact_receipts
    )
    implicit_artifact_loss = any(
        receipt.get("implicit_artifact_loss_detected") is True
        for receipt in artifact_receipts
    )
    artifact_fingerprint = next(
        (
            receipt.get("artifact_fingerprint_after")
            for receipt in reversed(artifact_receipts)
            if isinstance(receipt.get("artifact_fingerprint_after"), str)
        ),
        None,
    )

    operation_hash = semantic_fingerprint(
        {
            "tool_name": tool_name,
            "actions": to_serializable(actions),
        }
    )
    result_hash = semantic_result_fingerprint(serialized_observation)
    completion_assessment = None
    try:
        completion_assessment = runtime_context.assess_completion_contract(
            agent_claimed_finished=False
        )
    except Exception:
        completion_assessment = None
    completion_projection = (
        {
            "status": completion_assessment.status.value,
            "reason_codes": list(completion_assessment.reason_codes),
            "artifact_evidence_count": len(
                getattr(runtime_context, "_completion_artifact_evidence", ())
            ),
            "self_check_count": len(
                getattr(runtime_context, "_completion_self_checks", ())
            ),
            "final_evidence_count": len(
                getattr(runtime_context, "_completion_final_evidence_codes", ())
            ),
            "satisfied_artifact_count": sum(
                evidence.exists is True
                for evidence in getattr(
                    runtime_context, "_completion_artifact_evidence", ()
                )
            ),
            "successful_self_check_count": sum(
                evidence.exit_code == 0
                for evidence in getattr(runtime_context, "_completion_self_checks", ())
            ),
            "valid_immutable_input_count": sum(
                evidence.expected_hash == evidence.observed_hash
                for evidence in getattr(
                    runtime_context, "_completion_immutable_input_evidence", ()
                )
            ),
            "external_verifier_passed": bool(
                getattr(runtime_context, "_completion_external_verifier", None)
                and runtime_context._completion_external_verifier.passed
            ),
        }
        if completion_assessment is not None
        else None
    )
    completion_fingerprint = (
        semantic_fingerprint(completion_projection)
        if completion_projection is not None
        else None
    )
    completion_evidence_fingerprint = (
        semantic_fingerprint(
            {
                "artifacts": {
                    str(item.requirement_id): {
                        "exists": item.exists,
                        "content_hash": item.content_hash,
                        "media_type": item.media_type,
                    }
                    for item in getattr(
                        runtime_context, "_completion_artifact_evidence", ()
                    )
                },
                "immutable_inputs": {
                    str(item.input_id): {
                        "expected_hash": item.expected_hash,
                        "observed_hash": item.observed_hash,
                    }
                    for item in getattr(
                        runtime_context,
                        "_completion_immutable_input_evidence",
                        (),
                    )
                },
                "self_checks": {
                    str(item.command_id): {
                        "exit_code": item.exit_code,
                        "output_hash": item.output_hash,
                    }
                    for item in getattr(
                        runtime_context, "_completion_self_checks", ()
                    )
                },
                "final_evidence_codes": sorted(
                    getattr(
                        runtime_context, "_completion_final_evidence_codes", ()
                    )
                ),
                "external_verifier_passed": bool(
                    getattr(runtime_context, "_completion_external_verifier", None)
                    and runtime_context._completion_external_verifier.passed
                ),
            }
        )
        if completion_projection is not None
        else None
    )
    validation_evidence_advanced = bool(
        completion_evidence_fingerprint
        and completion_evidence_fingerprint
        != previous.get("completion_evidence_fingerprint")
    )
    goal_progress_observable = completion_projection is not None
    completion_positive_evidence = (
        sum(
            int(completion_projection[key])
            for key in (
                "satisfied_artifact_count",
                "successful_self_check_count",
                "valid_immutable_input_count",
                "final_evidence_count",
                "external_verifier_passed",
            )
        )
        if completion_projection is not None
        else 0
    )
    completion_score = (
        [
            completion_positive_evidence,
            -len(completion_projection["reason_codes"]),
        ]
        if completion_projection is not None
        else None
    )
    previous_completion_score = previous.get("completion_score")
    completion_advanced = bool(
        completion_score is not None
        and (
            (
                isinstance(previous_completion_score, list)
                and tuple(completion_score) > tuple(previous_completion_score)
            )
            or (
                not isinstance(previous_completion_score, list)
                and completion_positive_evidence > 0
            )
        )
    )
    recent_artifact_fingerprints = list(
        previous.get("recent_artifact_fingerprints") or []
    )[-7:]
    artifact_advanced = bool(
        artifact_changed
        and not rollback_performed
        and artifact_fingerprint
        and artifact_fingerprint != previous.get("artifact_fingerprint")
        and artifact_fingerprint not in recent_artifact_fingerprints
    )
    # A novel filesystem fingerprint is evidence of work, not evidence that the
    # task objective advanced. Treating every scratch file, partial download, or
    # rewritten script as goal progress lets unproductive exploration reset the
    # stagnation window indefinitely. Only explicit Completion Contract evidence
    # may reset goal-level stagnation; artifact novelty still prevents false
    # repeated-operation and low-information classifications below.
    goal_progress = completion_advanced
    goal_progress_count = int(previous.get("goal_progress_count", 0) or 0) + int(
        goal_progress
    )
    get_agent_step = getattr(runtime_context, "get_agent_step", None)
    current_agent_step = get_agent_step(agent_id) if callable(get_agent_step) else 0
    if not isinstance(current_agent_step, int) or isinstance(current_agent_step, bool):
        current_agent_step = 0
    last_goal_progress_agent_step = (
        current_agent_step
        if goal_progress
        else previous.get("last_goal_progress_agent_step")
    )
    # Absence of a Completion Contract means goal progress is unknown, not
    # negative.  Counting every Tool boundary as no progress in that state makes
    # adaptive policy compact and inject recovery advice at a fixed cadence even
    # while the agent is doing useful, novel work.  Repetition, low-information
    # gain, and budget pressure remain observable without a goal contract.
    no_goal_progress_count = (
        0
        if goal_progress or not goal_progress_observable
        else int(previous.get("no_goal_progress_count", 0) or 0) + 1
    )
    semantic_pair_hash = semantic_fingerprint(
        {"operation_hash": operation_hash, "result_hash": result_hash}
    )
    progress_guard_reset = artifact_advanced or validation_evidence_advanced
    previous_pairs = previous.get("recent_operation_result_hashes")
    recent_pairs = (
        [
            value
            for value in previous_pairs
            if isinstance(value, str)
        ][-(_RECENT_SEMANTIC_PAIR_WINDOW - 1) :]
        if isinstance(previous_pairs, list) and not progress_guard_reset
        else []
    )
    recent_pairs.append(semantic_pair_hash)
    previous_results = previous.get("recent_result_hashes")
    history = (
        [
            value
            for value in previous_results
            if isinstance(value, str)
        ][-(_RECENT_SEMANTIC_PAIR_WINDOW - 1) :]
        if isinstance(previous_results, list) and not progress_guard_reset
        else []
    )
    history.append(result_hash)
    repetition_count = recent_pairs.count(semantic_pair_hash)
    low_information_gain_count = history.count(result_hash)
    progress_guard_required = (
        repetition_count >= _PROGRESS_GUARD_REPEAT_THRESHOLD
        and not progress_guard_reset
    )
    if artifact_fingerprint:
        recent_artifact_fingerprints.append(artifact_fingerprint)
    state = {
        "agent_id": agent_id,
        "operation_hash": operation_hash,
        "result_hash": result_hash,
        "operation_result_hash": semantic_pair_hash,
        "repetition_count": repetition_count,
        "low_information_gain_count": low_information_gain_count,
        "recent_operation_result_hashes": recent_pairs,
        "recent_result_hashes": history,
        "recent_artifact_fingerprints": recent_artifact_fingerprints[-8:],
        "artifact_changed": artifact_changed,
        "artifact_fingerprint": artifact_fingerprint,
        "artifact_advanced": artifact_advanced,
        "rollback_performed": rollback_performed,
        "implicit_artifact_loss": implicit_artifact_loss,
        "completion_fingerprint": completion_fingerprint,
        "completion_evidence_fingerprint": completion_evidence_fingerprint,
        "completion_score": completion_score,
        "completion_advanced": completion_advanced,
        "validation_evidence_advanced": validation_evidence_advanced,
        "progress_guard_reset": progress_guard_reset,
        "progress_guard_required": progress_guard_required,
        "progress_guard_repeat_threshold": _PROGRESS_GUARD_REPEAT_THRESHOLD,
        "progress_guard_recent_window": _RECENT_SEMANTIC_PAIR_WINDOW,
        "goal_progress_observable": goal_progress_observable,
        "goal_progress": goal_progress,
        "goal_progress_count": goal_progress_count,
        "last_goal_progress_agent_step": last_goal_progress_agent_step,
        "no_goal_progress_count": no_goal_progress_count,
        "updated_at": time.time(),
        "runtime_revision": int(previous.get("runtime_revision", 0) or 0) + 1,
    }
    state_by_agent[agent_id] = state
    runtime_context.context_info[SEMANTIC_PROGRESS_KEY] = state_by_agent
    shared_writer = getattr(runtime_context, "write_task_runtime_state", None)
    if callable(shared_writer):
        shared_writer(agent_id, _SEMANTIC_RUNTIME_KEY, state)

    # Project the same append-only Tool boundary into a bounded operational
    # ledger.  Runtime fan-in prevents transport-copy loss; Amni WorkingState
    # makes the ledger part of normal checkpoint/resume state.
    from aworld.core.context.compiler import (
        ADAPTIVE_WORK_STATE_KEY,
        advance_adaptive_work_state,
        build_adaptive_work_state_entry,
    )

    serialized_actions = to_serializable(actions)
    work_entry = build_adaptive_work_state_entry(
        tool_name=tool_name,
        actions=serialized_actions if isinstance(serialized_actions, list) else [],
        observation=(
            serialized_observation if isinstance(serialized_observation, dict) else {}
        ),
        semantic_progress=state,
    )
    update_runtime = getattr(runtime_context, "update_task_runtime_state", None)
    if callable(update_runtime):
        work_state = update_runtime(
            agent_id,
            ADAPTIVE_WORK_STATE_KEY,
            lambda current: advance_adaptive_work_state(current, work_entry),
        )
    else:
        context_key = f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
        work_state = advance_adaptive_work_state(
            runtime_context.context_info.get(context_key), work_entry
        )
    context_key = f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
    runtime_context.context_info[context_key] = work_state
    put_working_state = getattr(runtime_context, "put", None)
    if callable(put_working_state):
        try:
            put_working_state(context_key, work_state)
        except Exception:
            # The runtime registry is authoritative during the current process;
            # non-Amni Context implementations need not expose WorkingState.
            pass

    metrics = _metrics_dict(runtime_context)
    metrics["semantic_tool_observation_count"] = (
        int(metrics.get("semantic_tool_observation_count", 0) or 0) + 1
    )
    if repetition_count > 1 and not progress_guard_reset:
        metrics["repeated_operation_count"] = (
            int(metrics.get("repeated_operation_count", 0) or 0) + 1
        )
    if low_information_gain_count > 1 and not progress_guard_reset:
        metrics["low_information_gain_count"] = (
            int(metrics.get("low_information_gain_count", 0) or 0) + 1
        )
    if progress_guard_reset:
        metrics["semantic_recent_window_reset_count"] = (
            int(metrics.get("semantic_recent_window_reset_count", 0) or 0) + 1
        )
    if artifact_changed:
        metrics["task_artifact_change_count"] = (
            int(metrics.get("task_artifact_change_count", 0) or 0) + 1
        )
    if goal_progress:
        metrics["goal_progress_count"] = (
            int(metrics.get("goal_progress_count", 0) or 0) + 1
        )
    elif goal_progress_observable:
        metrics["no_goal_progress_observation_count"] = (
            int(metrics.get("no_goal_progress_observation_count", 0) or 0) + 1
        )
    if rollback_performed:
        metrics["sandbox_rollback_count"] = (
            int(metrics.get("sandbox_rollback_count", 0) or 0) + 1
        )
    if implicit_artifact_loss:
        metrics["implicit_artifact_loss_count"] = (
            int(metrics.get("implicit_artifact_loss_count", 0) or 0) + 1
        )
    runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    metrics["adaptive_work_state_revision"] = int(work_state.get("revision", 0) or 0)
    return state


def semantic_progress_for_agent(context, *, agent_id: str) -> dict[str, Any]:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return {}
    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    state = (
        shared_reader(agent_id, _SEMANTIC_RUNTIME_KEY)
        if callable(shared_reader)
        else None
    )
    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        state_by_agent = {}
    state = _select_semantic_state(state, state_by_agent.get(agent_id))
    return dict(state) if isinstance(state, dict) else {}


def acknowledge_semantic_checkpoint(context, *, agent_id: str) -> None:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return
    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    shared_state = (
        shared_reader(agent_id, _SEMANTIC_RUNTIME_KEY)
        if callable(shared_reader)
        else None
    )
    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        state_by_agent = {}
    state = _select_semantic_state(shared_state, state_by_agent.get(agent_id))
    if not isinstance(state, dict):
        return
    state["repetition_count"] = 0
    state["low_information_gain_count"] = 0
    state["no_goal_progress_count"] = 0
    state["recent_operation_result_hashes"] = []
    state["recent_result_hashes"] = []
    state["progress_guard_required"] = False
    state["progress_guard_reset"] = True
    state["runtime_revision"] = int(state.get("runtime_revision", 0) or 0) + 1
    state_by_agent[agent_id] = state
    runtime_context.context_info[SEMANTIC_PROGRESS_KEY] = state_by_agent
    shared_writer = getattr(runtime_context, "write_task_runtime_state", None)
    if callable(shared_writer):
        shared_writer(agent_id, _SEMANTIC_RUNTIME_KEY, state)


def arm_post_tool_progress_watchdog(
    context,
    *,
    tool_name: str,
    agent_id: str,
    actions: list[ActionModel],
    followup_observation: Observation,
    followup_sender: str | None = None,
) -> dict[str, Any] | None:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return None

    record_semantic_tool_progress(
        runtime_context,
        tool_name=tool_name,
        agent_id=agent_id,
        actions=actions,
        observation=followup_observation,
    )
    from aworld.core.context.compiler import (
        ADAPTIVE_WORK_STATE_KEY,
        semantic_fingerprint,
    )

    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    adaptive_work_state = (
        shared_reader(agent_id, ADAPTIVE_WORK_STATE_KEY)
        if callable(shared_reader)
        else None
    )
    if not isinstance(adaptive_work_state, dict):
        adaptive_work_state = runtime_context.context_info.get(
            f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
        )

    continuation_token = semantic_fingerprint(
        {
            "agent_id": agent_id,
            "tool_name": tool_name,
            "tool_call_ids": [
                action.tool_call_id for action in actions if action.tool_call_id
            ],
            "observation": to_serializable(followup_observation),
        }
    )
    state = {
        "armed_at": time.time(),
        "agent_id": agent_id,
        "tool_name": tool_name,
        "followup_sender": followup_sender or tool_name,
        "tool_call_ids": [
            action.tool_call_id for action in actions if action.tool_call_id
        ],
        "followup_observation": to_serializable(followup_observation),
        "actions": to_serializable(actions),
        "retry_count": 0,
        "continuation_token": continuation_token,
        # Bind the bounded ledger to the same immutable continuation token as
        # the Action/Observation pair.  This gives the immediately-following
        # model request read-your-write access even when an event transport
        # copy cannot yet query Amni WorkingState.
        "adaptive_work_state": adaptive_work_state,
    }
    runtime_context.context_info[WATCHDOG_STATE_KEY] = state
    shared_updater = getattr(runtime_context, "update_task_runtime_state", None)
    if callable(shared_updater):

        def retain_recent_turns(current):
            turns = dict(current) if isinstance(current, dict) else {}
            turns[continuation_token] = {
                "actions": state["actions"],
                "followup_observation": state["followup_observation"],
                "adaptive_work_state": state["adaptive_work_state"],
            }
            while len(turns) > 32:
                turns.pop(next(iter(turns)))
            return turns

        shared_updater(agent_id, _POST_TOOL_TURNS_RUNTIME_KEY, retain_recent_turns)
    return state


def post_tool_turn_for_continuation(
    context, *, agent_id: str, continuation_token: str
) -> dict[str, Any] | None:
    """Return the immutable Tool turn that authorized a continuation.

    Event-driven Amni Memory can briefly expose a view in which the assistant
    Tool call or its result has not become query-visible yet.  The continuation
    token is already carried over the event boundary, so bind it to the exact
    Action/Observation pair as a read-your-write fallback instead of asking the
    model to continue from a stale history snapshot.
    """
    if not isinstance(continuation_token, str) or not continuation_token:
        return None
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return None
    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    turns = (
        shared_reader(agent_id, _POST_TOOL_TURNS_RUNTIME_KEY)
        if callable(shared_reader)
        else None
    )
    if isinstance(turns, dict):
        turn = turns.get(continuation_token)
        if isinstance(turn, dict):
            return dict(turn)
    state = runtime_context.context_info.get(WATCHDOG_STATE_KEY)
    if (
        isinstance(state, dict)
        and state.get("agent_id") == agent_id
        and state.get("continuation_token") == continuation_token
    ):
        return {
            "actions": state.get("actions") or [],
            "followup_observation": state.get("followup_observation") or {},
            "adaptive_work_state": state.get("adaptive_work_state"),
        }
    return None


def mark_post_tool_progress_llm_started(context, *, agent_id: str) -> float | None:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return None

    state = runtime_context.context_info.get(WATCHDOG_STATE_KEY)
    if not isinstance(state, dict) or state.get("agent_id") != agent_id:
        return None

    latency_seconds = max(time.time() - float(state.get("armed_at") or 0.0), 0.0)
    metrics = _metrics_dict(runtime_context)
    latencies = list(metrics.get("tool_success_to_next_llm_latencies") or [])
    latencies.append(round(latency_seconds, 3))
    metrics["tool_success_to_next_llm_latencies"] = latencies
    metrics["tool_success_to_next_llm_count"] = (
        int(metrics.get("tool_success_to_next_llm_count", 0) or 0) + 1
    )
    runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    runtime_context.context_info.pop(WATCHDOG_STATE_KEY, None)
    return latency_seconds
