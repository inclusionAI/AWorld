from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.event_mapper import map_runtime_event_to_session_update


def test_text_delta_maps_to_agent_message_chunk() -> None:
    update = map_runtime_event_to_session_update(
        "session-1",
        {"event_type": "text_delta", "seq": 1, "text": "hi"},
    )

    assert update["sessionId"] == "session-1"
    assert update["update"]["sessionUpdate"] == "agent_message_chunk"
    assert update["update"]["content"]["text"] == "hi"


def test_tool_start_maps_kind_and_content() -> None:
    update = map_runtime_event_to_session_update(
        "session-1",
        {
            "event_type": "tool_start",
            "seq": 2,
            "tool_call_id": "tool_1",
            "tool_name": "shell",
            "raw_input": {"command": "pwd"},
        },
    )

    assert update["update"]["sessionUpdate"] == "tool_call"
    assert update["update"]["toolCallId"] == "tool_1"
    assert update["update"]["kind"] == "shell"
    assert update["update"]["content"] == {"command": "pwd"}


def test_tool_end_maps_kind_status_and_content() -> None:
    update = map_runtime_event_to_session_update(
        "session-1",
        {
            "event_type": "tool_end",
            "seq": 3,
            "tool_call_id": "tool_1",
            "tool_name": "shell",
            "status": "completed",
            "raw_output": {"cwd": "/tmp"},
        },
    )

    assert update["update"]["sessionUpdate"] == "tool_call_update"
    assert update["update"]["toolCallId"] == "tool_1"
    assert update["update"]["kind"] == "shell"
    assert update["update"]["status"] == "completed"
    assert update["update"]["content"] == {"cwd": "/tmp"}


def test_tool_start_and_end_default_content_shape_to_object_or_null() -> None:
    start = map_runtime_event_to_session_update(
        "session-1",
        {
            "event_type": "tool_start",
            "seq": 2,
            "tool_call_id": "tool_1",
            "tool_name": "shell",
            "raw_input": None,
        },
    )
    end = map_runtime_event_to_session_update(
        "session-1",
        {
            "event_type": "tool_end",
            "seq": 3,
            "tool_call_id": "tool_1",
            "tool_name": "shell",
            "status": "completed",
            "raw_output": None,
        },
    )

    assert start["update"]["content"] == {}
    assert end["update"]["content"] is None


def test_turn_error_is_not_mapped_to_session_update_notification() -> None:
    try:
        map_runtime_event_to_session_update(
            "session-1",
            {
                "event_type": "turn_error",
                "seq": 9,
                "code": "AWORLD_ACP_REQUIRES_HUMAN",
                "message": "boom",
            },
        )
    except ValueError as exc:
        assert "Unsupported runtime event" in str(exc)
    else:
        raise AssertionError("Expected ValueError because turn_error ends the prompt, not a sessionUpdate")


def test_final_text_maps_to_terminal_agent_message_chunk() -> None:
    update = map_runtime_event_to_session_update(
        "session-1",
        {"event_type": "final_text", "seq": 3, "text": "done"},
    )

    assert update["update"]["sessionUpdate"] == "agent_message_chunk"
    assert update["update"]["content"]["text"] == "done"
