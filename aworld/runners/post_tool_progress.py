import time
from typing import Any

from aworld.core.common import ActionModel, Observation
from aworld.utils.serialized_util import to_serializable

WATCHDOG_STATE_KEY = "post_tool_progress_watchdog"
WATCHDOG_METRICS_KEY = "post_tool_progress_metrics"


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
