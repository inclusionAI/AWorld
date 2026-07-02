# coding: utf-8
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from aworld.core.task import TaskResponse


class EvalExecutionMode(str, Enum):
    STATIC = "static"
    AGENT = "agent"
    TASK = "task"
    PROGRAM = "program"


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


def _merge_eval_metadata(
    response_metadata: Any,
    invocation_metadata: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
) -> dict[str, Any]:
    base = dict(response_metadata) if isinstance(response_metadata, Mapping) else {}
    base.update(dict(invocation_metadata or {}))
    base["_target"] = dict(target or {})
    return base


def _list_field_from_response(response: Mapping[str, Any], field_name: str, default: list[Any]) -> list[Any]:
    value = response.get(field_name)
    if value is None:
        return default
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def normalize_task_response_to_eval_state(
    *,
    case_id: str,
    response: Any,
    target: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> EvalState:
    if isinstance(response, EvalState):
        state = response.to_dict()
        state["case_id"] = case_id
        state["metadata"] = {
            **dict(response.metadata or {}),
            **dict(metadata or {}),
            "_target": dict(target or {}),
        }
        return EvalState(**state)

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
            metadata=_merge_eval_metadata(getattr(response, "metadata", {}), metadata, target),
        )

    if isinstance(response, Mapping):
        trajectory = _list_field_from_response(response, "trajectory", [])
        answer = response.get("answer")
        return EvalState(
            case_id=case_id,
            status=str(response.get("status", "success")),
            answer=answer,
            completion=_list_field_from_response(response, "completion", [] if answer is None else [answer]),
            artifacts=dict(response.get("artifacts") or {}),
            trajectory=trajectory,
            tool_calls=_list_field_from_response(
                response,
                "tool_calls",
                _extract_tool_calls_from_trajectory(trajectory),
            ),
            usage=dict(response.get("usage") or {}),
            timing=dict(response.get("timing") or {}),
            error=dict(response.get("error")) if isinstance(response.get("error"), Mapping) else response.get("error"),
            raw_response=dict(response),
            metadata=_merge_eval_metadata(response.get("metadata"), metadata, target),
        )

    return EvalState(
        case_id=case_id,
        status="success",
        answer=response,
        completion=[] if response is None else [response],
        metadata=_merge_eval_metadata({}, metadata, target),
    )


def _validate_importable_callable_ref(ref: str) -> tuple[str, str]:
    if not ref or any(char.isspace() for char in ref) or "/" in ref or "\\" in ref:
        raise ValueError("program execution requires an importable callable reference")
    if ":" in ref:
        module_name, attr_name = ref.split(":", 1)
    elif "." in ref:
        module_name, attr_name = ref.rsplit(".", 1)
    else:
        raise ValueError("program execution requires an importable callable reference")
    module_parts = module_name.split(".")
    if not module_name or not attr_name or attr_name == "py" or "py" in module_parts:
        raise ValueError("program execution requires an importable callable reference")
    return module_name, attr_name


def load_program_callable(ref: str):
    module_name, attr_name = _validate_importable_callable_ref(ref)
    candidate = getattr(importlib.import_module(module_name), attr_name)
    if not callable(candidate):
        raise ValueError(f"program reference is not callable: {ref}")
    return candidate
