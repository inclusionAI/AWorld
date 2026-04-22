from __future__ import annotations

from typing import Any


def next_sequence(state: dict[str, Any]) -> int:
    state["seq"] = int(state.get("seq", 0)) + 1
    return state["seq"]


def next_tool_id(state: dict[str, Any]) -> str:
    state["tool_seq"] = int(state.get("tool_seq", 0)) + 1
    return f"acp_tool_{state['tool_seq']}"


def normalize_text_delta(state: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "event_type": "text_delta",
        "seq": next_sequence(state),
        "text": text,
    }


def normalize_thought_delta(state: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "event_type": "thought_delta",
        "seq": next_sequence(state),
        "text": text,
    }


def normalize_final_text(state: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "event_type": "final_text",
        "seq": next_sequence(state),
        "text": text,
    }


def normalize_tool_start(
    state: dict[str, Any],
    *,
    native_id: str | None,
    tool_name: str,
    payload: Any,
) -> dict[str, Any]:
    tool_call_id = native_id or next_tool_id(state)
    state[f"tool::{tool_name}"] = tool_call_id
    return {
        "event_type": "tool_start",
        "seq": next_sequence(state),
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "raw_input": payload,
    }


def normalize_tool_end(
    state: dict[str, Any],
    *,
    native_id: str | None,
    tool_name: str,
    status: str,
    payload: Any,
) -> dict[str, Any]:
    tool_call_id = native_id or state.setdefault(f"tool::{tool_name}", next_tool_id(state))
    return {
        "event_type": "tool_end",
        "seq": next_sequence(state),
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "status": status,
        "raw_output": payload,
    }
