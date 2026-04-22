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
                "content": event.get("raw_input", {}),
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

    raise ValueError(f"Unsupported runtime event: {event_type}")
