"""Export AWorld direct-run summaries as ATIF trajectories."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
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


def build_atif_trajectory(
    trajectory_payload: dict[str, Any],
    *,
    prompt: str,
    agent_name: str,
    agent_version: str,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Convert AWorld's direct-run trajectory payload to ATIF v1.7."""
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

    if len(steps) == 1:
        steps.append(
            {
                "step_id": 2,
                "source": "agent",
                "message": "(AWorld completed without a captured response)",
                "llm_call_count": 0,
            }
        )

    agent: dict[str, Any] = {
        "name": agent_name,
        "version": agent_version,
    }
    if model_name:
        agent["model_name"] = model_name

    return {
        "schema_version": "ATIF-v1.7",
        "session_id": session_id,
        "agent": agent,
        "steps": steps,
        "final_metrics": {"total_steps": len(steps)},
        "extra": {
            "producer": "aworld-cli",
            "trajectory_capture_mode": trajectory_payload.get(
                "trajectory_capture_mode",
                "unknown",
            ),
        },
    }


def write_atif_trajectory(path: str | os.PathLike[str], trajectory: dict[str, Any]) -> None:
    """Write an ATIF trajectory atomically."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    temporary_path.write_text(
        json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
