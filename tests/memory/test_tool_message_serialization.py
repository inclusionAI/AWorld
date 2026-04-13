# coding: utf-8

import json

from aworld.memory.models import MemoryToolMessage, MessageMetadata


def test_memory_tool_message_serializes_dict_content_for_openai():
    message = MemoryToolMessage(
        tool_call_id="call-1",
        content={
            "success": True,
            "message": "Created task '喝水提醒'",
            "next_run": "2026-04-13T20:25:41+08:00",
        },
        metadata=MessageMetadata(
            agent_id="agent-1",
            agent_name="Aworld",
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
        ),
    )

    openai_message = message.to_openai_message()

    assert openai_message["role"] == "tool"
    assert openai_message["tool_call_id"] == "call-1"
    assert openai_message["content"] == json.dumps(
        {
            "success": True,
            "message": "Created task '喝水提醒'",
            "next_run": "2026-04-13T20:25:41+08:00",
        },
        ensure_ascii=False,
    )
