"""Export AWorld direct-run summaries as ATIF trajectories."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


_THINK_BLOCK_RE = re.compile(r"<think>\s*(.*?)\s*</think>", re.DOTALL)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": value} if value is not None else {}


def _iso_timestamp(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _split_message_and_reasoning(content: Any) -> tuple[str, str | None]:
    text = content if isinstance(content, str) else str(content or "")
    reasoning_parts = _THINK_BLOCK_RE.findall(text)
    message = _THINK_BLOCK_RE.sub("", text).strip()
    reasoning = "\n\n".join(part.strip() for part in reasoning_parts if part.strip())
    if not message:
        message = "(tool use)" if reasoning_parts else "(empty response)"
    return message, reasoning or None


def _tool_result_index(native_items: list[dict[str, Any]]) -> dict[str, str]:
    results: dict[str, str] = {}
    for item in native_items:
        state_input = _as_dict(_as_dict(item.get("state")).get("input"))
        for result in state_input.get("action_result") or []:
            if not isinstance(result, dict):
                continue
            call_id = result.get("tool_call_id")
            if call_id:
                results[str(call_id)] = str(result.get("content") or "")
    return results


def _native_agent_step(
    item: dict[str, Any],
    *,
    step_id: int,
    model_name: str | None,
    tool_results: dict[str, str],
) -> dict[str, Any]:
    meta = _as_dict(item.get("meta"))
    action = _as_dict(item.get("action"))
    message, reasoning = _split_message_and_reasoning(action.get("content"))

    tool_calls: list[dict[str, Any]] = []
    observation_results: list[dict[str, Any]] = []
    for index, raw_call in enumerate(action.get("tool_calls") or [], start=1):
        if not isinstance(raw_call, dict):
            continue
        function = _as_dict(raw_call.get("function"))
        call_id = str(raw_call.get("id") or f"aworld-call-{step_id}-{index}")
        tool_calls.append(
            {
                "tool_call_id": call_id,
                "function_name": str(function.get("name") or "unknown"),
                "arguments": _parse_arguments(function.get("arguments")),
            }
        )
        if call_id in tool_results:
            observation_results.append(
                {
                    "source_call_id": call_id,
                    "content": tool_results[call_id],
                }
            )

    step: dict[str, Any] = {
        "step_id": step_id,
        "source": "agent",
        "message": message,
        "llm_call_count": 1,
        "extra": {
            "aworld_step": meta.get("step"),
            "aworld_task_id": meta.get("task_id"),
            "aworld_agent_id": meta.get("agent_id"),
        },
    }
    timestamp = _iso_timestamp(meta.get("execute_time"))
    if timestamp:
        step["timestamp"] = timestamp
    if model_name:
        step["model_name"] = model_name
    if reasoning:
        step["reasoning_content"] = reasoning
    if tool_calls:
        step["tool_calls"] = tool_calls
    if observation_results:
        step["observation"] = {"results": observation_results}
    return step


def _as_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _count_tool_calls(native_items: list[dict[str, Any]]) -> int:
    count = 0
    for item in native_items:
        calls = _as_dict(item.get("action")).get("tool_calls")
        if isinstance(calls, list):
            count += sum(1 for call in calls if isinstance(call, dict))
    return count


def _run_metric(
    run_outcome: dict[str, Any],
    trajectory_payload: dict[str, Any],
    name: str,
    fallback: int,
) -> int:
    for source in (run_outcome, trajectory_payload):
        value = _as_nonnegative_int(source.get(name))
        if value is not None:
            return value
    return fallback


class AtifExportStatus(str, Enum):
    PERSISTED = "persisted"
    FAILED = "failed"
    NOT_REQUESTED = "not_requested"


@dataclass(frozen=True)
class AtifExportReceipt:
    """Sanitized control-plane result for one ATIF output sink."""

    status: AtifExportStatus
    trajectory_fidelity: str
    error_code: str | None = None
    error_type: str | None = None

    SCHEMA_VERSION = "aworld.atif.export.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", AtifExportStatus(self.status))
        if self.status is AtifExportStatus.FAILED and not self.error_code:
            raise ValueError("failed ATIF export requires error_code")
        if self.status is not AtifExportStatus.FAILED and (
            self.error_code is not None or self.error_type is not None
        ):
            raise ValueError("ATIF export errors are only valid for failed receipts")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "status": self.status.value,
            "trajectory_fidelity": self.trajectory_fidelity,
        }
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.error_type is not None:
            payload["error_type"] = self.error_type
        return payload


def build_atif_trajectory(
    trajectory_payload: dict[str, Any],
    *,
    prompt: str,
    agent_name: str,
    agent_version: str,
    model_name: str | None = None,
    run_outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert AWorld's direct-run trajectory payload to ATIF v1.7."""
    normalized_outcome = _as_dict(run_outcome)
    native_items = [
        item
        for item in trajectory_payload.get("trajectory") or []
        if isinstance(item, dict)
    ]
    session_id = next(
        (
            str(_as_dict(item.get("meta")).get("session_id"))
            for item in native_items
            if _as_dict(item.get("meta")).get("session_id")
        ),
        f"aworld-{uuid.uuid4()}",
    )
    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "source": "user",
            "message": prompt,
        }
    ]
    tool_results = _tool_result_index(native_items)
    for item in native_items:
        steps.append(
            _native_agent_step(
                item,
                step_id=len(steps) + 1,
                model_name=model_name,
                tool_results=tool_results,
            )
        )

    captured_agent_steps = [step for step in steps if step.get("source") == "agent"]
    semantic_status = str(normalized_outcome.get("semantic_status") or "succeeded")
    completed = semantic_status == "succeeded"
    if len(steps) == 1 and completed:
        steps.append(
            {
                "step_id": 2,
                "source": "agent",
                "message": "(AWorld completed without a captured response)",
                "llm_call_count": 0,
            }
        )

    inferred_llm_calls = len(trajectory_payload.get("llm_calls") or [])
    if not inferred_llm_calls:
        inferred_llm_calls = len(captured_agent_steps)
    llm_call_count = _run_metric(
        normalized_outcome,
        trajectory_payload,
        "llm_call_count",
        inferred_llm_calls,
    )
    tool_call_count = _run_metric(
        normalized_outcome,
        trajectory_payload,
        "tool_call_count",
        _count_tool_calls(native_items),
    )
    action_count = _run_metric(
        normalized_outcome,
        trajectory_payload,
        "action_count",
        len(captured_agent_steps),
    )

    # Native captured steps each represent one provider action.  Reconcile the
    # per-step ATIF counters to the authoritative control-plane total without
    # fabricating extra assistant messages on partial failures.
    remaining_llm_calls = llm_call_count
    for step in captured_agent_steps:
        step["llm_call_count"] = 1 if remaining_llm_calls > 0 else 0
        remaining_llm_calls = max(0, remaining_llm_calls - 1)
    if captured_agent_steps and remaining_llm_calls:
        captured_agent_steps[-1]["llm_call_count"] += remaining_llm_calls

    agent: dict[str, Any] = {
        "name": agent_name,
        "version": agent_version,
    }
    if model_name:
        agent["model_name"] = model_name

    trajectory_fidelity = str(
        normalized_outcome.get("trajectory_fidelity")
        or trajectory_payload.get("trajectory_fidelity")
        or ("complete" if completed else "partial")
    )
    final_metrics: dict[str, Any] = {
        "total_steps": len(steps),
        "extra": {
            "llm_call_count": llm_call_count,
            "tool_call_count": tool_call_count,
            "action_count": action_count,
        },
    }
    aworld_projection: dict[str, Any] = {
        "completion_state": "complete" if completed else "incomplete",
        "trajectory_fidelity": trajectory_fidelity,
        "llm_call_count": llm_call_count,
        "tool_call_count": tool_call_count,
        "action_count": action_count,
        "last_successful_checkpoint": normalized_outcome.get(
            "last_successful_checkpoint"
        ),
    }
    if normalized_outcome:
        aworld_projection["run_outcome"] = normalized_outcome

    return {
        "schema_version": "ATIF-v1.7",
        "session_id": session_id,
        "agent": agent,
        "steps": steps,
        "final_metrics": final_metrics,
        "extra": {
            "producer": "aworld-cli",
            "trajectory_capture_mode": trajectory_payload.get(
                "trajectory_capture_mode",
                "unknown",
            ),
            "aworld": aworld_projection,
        },
    }


def write_atif_trajectory(path: str | os.PathLike[str], trajectory: dict[str, Any]) -> None:
    """Write an ATIF trajectory atomically."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(output_path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def try_write_atif_trajectory(
    path: str | os.PathLike[str],
    trajectory: dict[str, Any],
    *,
    trajectory_fidelity: str,
) -> AtifExportReceipt:
    """Persist ATIF without allowing sink failure to alter run semantics."""

    try:
        write_atif_trajectory(path, trajectory)
    except Exception as exc:
        return AtifExportReceipt(
            status=AtifExportStatus.FAILED,
            trajectory_fidelity=trajectory_fidelity,
            error_code="atif_write_failed",
            error_type=type(exc).__name__,
        )
    return AtifExportReceipt(
        status=AtifExportStatus.PERSISTED,
        trajectory_fidelity=trajectory_fidelity,
    )


__all__ = [
    "AtifExportReceipt",
    "AtifExportStatus",
    "build_atif_trajectory",
    "try_write_atif_trajectory",
    "write_atif_trajectory",
]
