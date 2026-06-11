# coding: utf-8
from __future__ import annotations

from typing import Any, Mapping


def get_eval_state(output: Any) -> dict[str, Any]:
    if isinstance(output, Mapping) and isinstance(output.get("state"), Mapping):
        return dict(output["state"])
    if isinstance(output, Mapping):
        return dict(output)
    return {}


def get_answer(output: Any) -> Any:
    state = get_eval_state(output)
    if "answer" in state:
        return state["answer"]
    return None


def get_completion(output: Any) -> list[Any]:
    state = get_eval_state(output)
    return list(state.get("completion") or [])


def get_trajectory(output: Any) -> list[dict[str, Any]]:
    state = get_eval_state(output)
    if "trajectory" in state:
        return list(state.get("trajectory") or [])
    return []


def get_messages_by_role(output: Any, role: str) -> list[dict[str, Any]]:
    return [
        dict(message)
        for message in get_trajectory(output)
        if isinstance(message, Mapping) and message.get("role") == role
    ]


def get_assistant_messages(output: Any) -> list[dict[str, Any]]:
    completion = get_completion(output)
    if completion and all(isinstance(item, Mapping) for item in completion):
        return [dict(item) for item in completion]
    return get_messages_by_role(output, "assistant")


def get_tool_calls(output: Any) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for message in get_trajectory(output):
        if not isinstance(message, Mapping):
            continue
        for call in message.get("tool_calls") or []:
            if isinstance(call, Mapping):
                tool_calls.append(dict(call))
        action = message.get("action")
        if isinstance(action, Mapping):
            for call in action.get("tool_calls") or []:
                if isinstance(call, Mapping):
                    tool_calls.append(dict(call))
    return tool_calls
