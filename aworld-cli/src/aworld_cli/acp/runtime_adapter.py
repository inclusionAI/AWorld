from __future__ import annotations

import json
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
    open_tool_calls = state.setdefault("open_tool_calls", [])
    if isinstance(open_tool_calls, list):
        open_tool_calls.append({"tool_name": tool_name, "tool_call_id": tool_call_id})
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
    open_tool_calls = state.get("open_tool_calls")
    if isinstance(open_tool_calls, list):
        for index in range(len(open_tool_calls) - 1, -1, -1):
            item = open_tool_calls[index]
            if not isinstance(item, dict):
                continue
            if item.get("tool_call_id") == tool_call_id:
                open_tool_calls.pop(index)
                break
    return {
        "event_type": "tool_end",
        "seq": next_sequence(state),
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "status": status,
        "raw_output": payload,
    }


def adapt_output_to_runtime_events(
    state: dict[str, Any],
    output: Any,
) -> list[dict[str, Any]]:
    output_type = _output_type(output)
    events: list[dict[str, Any]] = []

    if output_type == "chunk":
        reasoning = _extract_reasoning(output)
        if reasoning:
            events.append(normalize_thought_delta(state, reasoning))
        text = _extract_text(output)
        if text:
            state["saw_text_delta"] = True
            events.append(normalize_text_delta(state, text))
        return events

    if output_type == "message":
        for tool_call in _extract_tool_calls(output):
            events.append(
                normalize_tool_start(
                    state,
                    native_id=getattr(tool_call, "id", None),
                    tool_name=_tool_name(tool_call),
                    payload=_tool_arguments(tool_call),
                )
            )

        reasoning = _extract_reasoning(output)
        if reasoning:
            events.append(normalize_thought_delta(state, reasoning))

        text = _extract_text(output)
        if text and not state.get("saw_text_delta") and _should_emit_message_text(output):
            events.append(normalize_final_text(state, text))
        return events

    if output_type == "tool_call_result":
        origin_tool_call = getattr(output, "origin_tool_call", None)
        events.append(
            normalize_tool_end(
                state,
                native_id=getattr(origin_tool_call, "id", None),
                tool_name=getattr(output, "tool_name", None)
                or _tool_name(origin_tool_call)
                or "unknown",
                status="completed",
                payload=getattr(output, "data", None),
            )
        )
        return events

    return events


def _output_type(output: Any) -> str:
    getter = getattr(output, "output_type", None)
    return getter() if callable(getter) else ""


def _extract_text(output: Any) -> str:
    response = getattr(output, "response", None)
    if isinstance(response, str) and response:
        return response

    content = getattr(output, "content", None)
    if isinstance(content, str) and content:
        return content

    data = getattr(output, "data", None)
    data_content = getattr(data, "content", None)
    if isinstance(data_content, str) and data_content:
        return data_content

    source = getattr(output, "source", None)
    source_content = getattr(source, "content", None)
    if isinstance(source_content, str) and source_content:
        return source_content

    return ""


def _extract_reasoning(output: Any) -> str:
    reasoning = getattr(output, "reasoning", None)
    if isinstance(reasoning, str) and reasoning:
        return reasoning

    data = getattr(output, "data", None)
    data_reasoning = getattr(data, "reasoning_content", None)
    if isinstance(data_reasoning, str) and data_reasoning:
        return data_reasoning

    source = getattr(output, "source", None)
    source_reasoning = getattr(source, "reasoning_content", None)
    if isinstance(source_reasoning, str) and source_reasoning:
        return source_reasoning

    return ""


def _should_emit_message_text(output: Any) -> bool:
    metadata = getattr(output, "metadata", None)
    if not isinstance(metadata, dict):
        return True

    is_finished = metadata.get("is_finished")
    if is_finished is True:
        return True
    if is_finished is False:
        return False

    sender = metadata.get("sender")
    receiver = metadata.get("receiver")
    # Legacy/self-test bridges emit direct MessageOutput objects without routing metadata.
    # Routed runtime messages without an explicit completion marker are often intermediate
    # observations that should not be surfaced as final ACP text.
    if sender is not None or receiver is not None:
        return False
    return True


def _extract_tool_calls(output: Any) -> list[Any]:
    raw_tool_calls = getattr(output, "tool_calls", None) or []
    if raw_tool_calls:
        return [_unwrap_tool_call(tool_call) for tool_call in raw_tool_calls]

    source = getattr(output, "source", None)
    source_tool_calls = getattr(source, "tool_calls", None) or []
    return [_unwrap_tool_call(tool_call) for tool_call in source_tool_calls]


def _unwrap_tool_call(tool_call: Any) -> Any:
    return getattr(tool_call, "data", tool_call)


def _tool_name(tool_call: Any) -> str:
    function = getattr(tool_call, "function", None)
    name = getattr(function, "name", None)
    return name or "unknown"


def _tool_arguments(tool_call: Any) -> Any:
    function = getattr(tool_call, "function", None)
    arguments = getattr(function, "arguments", None)
    if not isinstance(arguments, str):
        return arguments if arguments is not None else {}

    text = arguments.strip()
    if not text:
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return arguments
