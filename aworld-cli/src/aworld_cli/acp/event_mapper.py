from __future__ import annotations

from typing import Any


def map_runtime_event_to_session_update(
    session_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    event_type = event["event_type"]

    if event_type in {"text_delta", "final_text"}:
        return {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"text": event["text"]},
            },
        }

    if event_type == "thought_delta":
        return {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"text": event["text"]},
            },
        }

    if event_type == "tool_start":
        return {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": event["tool_call_id"],
                "kind": event["tool_name"],
                "content": event.get("raw_input") if event.get("raw_input") is not None else {},
            },
        }

    if event_type == "tool_end":
        return {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": event["tool_call_id"],
                "kind": event.get("tool_name", "unknown"),
                "status": event["status"],
                "content": event.get("raw_output"),
            },
        }

    if event_type == "step_start":
        return {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": event["step_id"],
                "kind": "step",
                "title": event["display_name"],
                "content": _step_content(event),
            },
        }

    if event_type == "step_end":
        return {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": event["step_id"],
                "kind": "step",
                "title": event["display_name"],
                "status": _step_status(event.get("status")),
                "content": _step_content(event),
            },
        }

    raise ValueError(f"Unsupported runtime event: {event_type}")


def _step_content(event: dict[str, Any]) -> dict[str, Any]:
    content = {
        "type": "step",
        "stepId": event["step_id"],
        "parentStepId": event.get("parent_step_id"),
        "name": event["name"],
        "displayName": event["display_name"],
        "stepNum": event.get("step_num"),
        "status": event["status"],
    }
    if event.get("payload") is not None:
        content["data"] = event["payload"]
    return content


def _step_status(status: Any) -> str:
    if not isinstance(status, str):
        return "completed"
    normalized = status.upper()
    if normalized == "FAILED":
        return "failed"
    if normalized == "CANCELLED":
        return "cancelled"
    return "completed"
