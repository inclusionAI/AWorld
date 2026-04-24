from __future__ import annotations

import sys
from pathlib import Path

from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.output.base import ChunkOutput, MessageOutput, ToolResultOutput

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.runtime_adapter import adapt_output_to_runtime_events, normalize_tool_end


def test_tool_result_without_prior_start_gets_synthetic_turn_scoped_id() -> None:
    state: dict[str, object] = {}

    event = normalize_tool_end(
        state,
        native_id=None,
        tool_name="shell",
        status="completed",
        payload={"ok": True},
    )

    assert event["event_type"] == "tool_end"
    assert event["tool_call_id"].startswith("acp_tool_")


def test_tool_result_preserves_native_id_when_present() -> None:
    state: dict[str, object] = {}

    event = normalize_tool_end(
        state,
        native_id="native-tool-1",
        tool_name="shell",
        status="completed",
        payload={"ok": True},
    )

    assert event["tool_call_id"] == "native-tool-1"
    assert event["tool_name"] == "shell"


def test_chunk_output_maps_to_text_delta_event() -> None:
    state: dict[str, object] = {}
    output = ChunkOutput(
        data=ModelResponse(id="resp-1", model="demo", content="hello"),
        metadata={},
    )

    events = adapt_output_to_runtime_events(state, output)

    assert events == [{"event_type": "text_delta", "seq": 1, "text": "hello"}]


def test_message_output_tool_calls_become_tool_start_events() -> None:
    state: dict[str, object] = {}
    output = MessageOutput(
        source=ModelResponse(
            id="resp-1",
            model="demo",
            content="",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    function=Function(name="shell", arguments='{"command":"pwd"}'),
                )
            ],
        )
    )

    events = adapt_output_to_runtime_events(state, output)

    assert len(events) == 1
    assert events[0]["event_type"] == "tool_start"
    assert events[0]["tool_call_id"] == "call-1"
    assert events[0]["tool_name"] == "shell"
    assert events[0]["raw_input"] == {"command": "pwd"}


def test_tool_result_output_maps_to_tool_end_event() -> None:
    state: dict[str, object] = {}
    output = ToolResultOutput(
        tool_name="shell",
        data={"cwd": "/tmp"},
        origin_tool_call=ToolCall(
            id="call-1",
            function=Function(name="shell", arguments='{"command":"pwd"}'),
        ),
    )

    events = adapt_output_to_runtime_events(state, output)

    assert len(events) == 1
    assert events[0]["event_type"] == "tool_end"
    assert events[0]["tool_call_id"] == "call-1"
    assert events[0]["status"] == "completed"


def test_message_output_final_text_is_suppressed_after_chunk_stream() -> None:
    state: dict[str, object] = {"saw_text_delta": True}
    output = MessageOutput(
        source=ModelResponse(id="resp-1", model="demo", content="hello")
    )

    events = adapt_output_to_runtime_events(state, output)

    assert events == []
