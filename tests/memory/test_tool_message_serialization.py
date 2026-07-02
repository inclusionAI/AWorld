# coding: utf-8

import json

from aworld.memory.models import MemoryToolMessage, MessageMetadata


def _build_tool_message(content):
    return MemoryToolMessage(
        tool_call_id="call-1",
        content=content,
        metadata=MessageMetadata(
            agent_id="agent-1",
            agent_name="Aworld",
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
        ),
    )


def test_memory_tool_message_serializes_dict_content_for_openai():
    payload = {
        "success": True,
        "message": "Created task '喝水提醒'",
        "next_run": "2026-04-13T20:25:41+08:00",
    }
    message = _build_tool_message(payload)

    openai_message = message.to_openai_message()

    assert openai_message["role"] == "tool"
    assert openai_message["tool_call_id"] == "call-1"
    assert openai_message["content"] == [
        {
            "type": "text",
            "text": json.dumps(payload, ensure_ascii=False),
        }
    ]


def test_memory_tool_message_serializes_plain_string_content_as_text_parts():
    message = _build_tool_message("terminal output")

    openai_message = message.to_openai_message()

    assert openai_message["content"] == [{"type": "text", "text": "terminal output"}]


def test_memory_tool_message_unwraps_legacy_json_array_strings():
    message = _build_tool_message('["line one", "line two"]')

    openai_message = message.to_openai_message()

    assert openai_message["content"] == [
        {"type": "text", "text": "line one"},
        {"type": "text", "text": "line two"},
    ]
