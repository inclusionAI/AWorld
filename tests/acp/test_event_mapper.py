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


def test_final_text_maps_to_terminal_agent_message_chunk() -> None:
    update = map_runtime_event_to_session_update(
        "session-1",
        {"event_type": "final_text", "seq": 3, "text": "done"},
    )

    assert update["update"]["sessionUpdate"] == "agent_message_chunk"
    assert update["update"]["content"]["text"] == "done"
