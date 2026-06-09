# coding: utf-8
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from aworld.core.task import TaskResponse


class EvalExecutionMode(str, Enum):
    STATIC = "static"
    AGENT = "agent"
    TASK = "task"


@dataclass(frozen=True)
class EvalExecutionSpec:
    mode: EvalExecutionMode = EvalExecutionMode.STATIC
    target_ref: str | None = None
    target_config: dict[str, Any] = field(default_factory=dict)
    query_column: str | None = None
    task_builder_ref: str | None = None
    runner_method: str | None = None
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalState:
    case_id: str
    status: str
    answer: Any | None = None
    completion: list[Any] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "answer": self.answer,
            "completion": self.completion,
            "artifacts": self.artifacts,
            "trajectory": self.trajectory,
            "tool_calls": self.tool_calls,
            "usage": self.usage,
            "timing": self.timing,
            "error": self.error,
            "raw_response": self.raw_response,
            "metadata": self.metadata,
        }


def _extract_tool_calls_from_trajectory(trajectory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for step in trajectory:
        if not isinstance(step, Mapping):
            continue
        if isinstance(step.get("tool_calls"), list):
            calls.extend([dict(call) for call in step["tool_calls"] if isinstance(call, Mapping)])
        action = step.get("action")
        if isinstance(action, Mapping) and isinstance(action.get("tool_calls"), list):
            calls.extend([dict(call) for call in action["tool_calls"] if isinstance(call, Mapping)])
    return calls


def normalize_task_response_to_eval_state(
    *,
    case_id: str,
    response: Any,
    target: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> EvalState:
    if isinstance(response, TaskResponse):
        trajectory = list(response.trajectory or [])
        return EvalState(
            case_id=case_id,
            status="success" if response.success else "failed",
            answer=response.answer,
            completion=[] if response.answer is None else [response.answer],
            trajectory=trajectory,
            tool_calls=_extract_tool_calls_from_trajectory(trajectory),
            usage=dict(response.usage or {}),
            timing={"time_cost": response.time_cost},
            raw_response=response.to_dict(),
            metadata={**dict(metadata or {}), "_target": dict(target or {})},
        )

    if isinstance(response, Mapping):
        trajectory = list(response.get("trajectory") or [])
        return EvalState(
            case_id=case_id,
            status=str(response.get("status", "success")),
            answer=response.get("answer"),
            completion=list(response.get("completion") or ([] if response.get("answer") is None else [response.get("answer")])),
            artifacts=dict(response.get("artifacts") or {}),
            trajectory=trajectory,
            tool_calls=list(response.get("tool_calls") or _extract_tool_calls_from_trajectory(trajectory)),
            usage=dict(response.get("usage") or {}),
            timing=dict(response.get("timing") or {}),
            error=dict(response.get("error")) if isinstance(response.get("error"), Mapping) else response.get("error"),
            raw_response=dict(response),
            metadata={**dict(metadata or {}), "_target": dict(target or {})},
        )

    return EvalState(
        case_id=case_id,
        status="success",
        answer=response,
        completion=[] if response is None else [response],
        metadata={**dict(metadata or {}), "_target": dict(target or {})},
    )
