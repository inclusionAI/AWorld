import time
from typing import Any

from aworld.core.common import ActionModel, Observation
from aworld.utils.serialized_util import to_serializable

WATCHDOG_STATE_KEY = "post_tool_progress_watchdog"
WATCHDOG_METRICS_KEY = "post_tool_progress_metrics"
SEMANTIC_PROGRESS_KEY = "context_semantic_progress"


def _runtime_context(context):
    if context is None:
        return None
    event_manager = getattr(context, "event_manager", None)
    root_context = getattr(event_manager, "context", None) if event_manager is not None else None
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

    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        state_by_agent = {}
    previous = state_by_agent.get(agent_id)
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
    same_operation = operation_hash == previous.get("operation_hash")
    same_result = result_hash == previous.get("result_hash")
    repetition_count = (
        int(previous.get("repetition_count", 0) or 0) + 1
        if same_operation and same_result and not artifact_changed
        else 1
    )
    low_information_gain_count = (
        int(previous.get("low_information_gain_count", 0) or 0) + 1
        if same_result and not artifact_changed
        else 1
    )
    history = list(previous.get("recent_result_hashes") or [])[-7:]
    history.append(result_hash)
    state = {
        "agent_id": agent_id,
        "operation_hash": operation_hash,
        "result_hash": result_hash,
        "repetition_count": repetition_count,
        "low_information_gain_count": low_information_gain_count,
        "recent_result_hashes": history,
        "artifact_changed": artifact_changed,
        "artifact_fingerprint": artifact_fingerprint,
        "updated_at": time.time(),
    }
    state_by_agent[agent_id] = state
    runtime_context.context_info[SEMANTIC_PROGRESS_KEY] = state_by_agent

    metrics = _metrics_dict(runtime_context)
    metrics["semantic_tool_observation_count"] = int(
        metrics.get("semantic_tool_observation_count", 0) or 0
    ) + 1
    if same_operation and same_result and not artifact_changed:
        metrics["repeated_operation_count"] = int(
            metrics.get("repeated_operation_count", 0) or 0
        ) + 1
    if same_result and not artifact_changed:
        metrics["low_information_gain_count"] = int(
            metrics.get("low_information_gain_count", 0) or 0
        ) + 1
    if artifact_changed:
        metrics["task_artifact_change_count"] = int(
            metrics.get("task_artifact_change_count", 0) or 0
        ) + 1
    runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    return state


def semantic_progress_for_agent(context, *, agent_id: str) -> dict[str, Any]:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return {}
    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        return {}
    state = state_by_agent.get(agent_id)
    return dict(state) if isinstance(state, dict) else {}


def acknowledge_semantic_checkpoint(context, *, agent_id: str) -> None:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return
    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        return
    state = state_by_agent.get(agent_id)
    if not isinstance(state, dict):
        return
    state["repetition_count"] = 0
    state["low_information_gain_count"] = 0
    state_by_agent[agent_id] = state
    runtime_context.context_info[SEMANTIC_PROGRESS_KEY] = state_by_agent


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

    state = {
        "armed_at": time.time(),
        "agent_id": agent_id,
        "tool_name": tool_name,
        "followup_sender": followup_sender or tool_name,
        "tool_call_ids": [action.tool_call_id for action in actions if action.tool_call_id],
        "followup_observation": to_serializable(followup_observation),
        "retry_count": 0,
    }
    runtime_context.context_info[WATCHDOG_STATE_KEY] = state
    return state


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
    metrics["tool_success_to_next_llm_count"] = int(metrics.get("tool_success_to_next_llm_count", 0) or 0) + 1
    runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    runtime_context.context_info.pop(WATCHDOG_STATE_KEY, None)
    return latency_seconds
