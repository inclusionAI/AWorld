# coding: utf-8

import json

from aworld.memory.models import MemoryAIMessage, MessageMetadata
from aworld.memory.tool_call_compaction import normalize_tool_call_arguments_for_replay
from aworld.models.model_response import ToolCall


def test_memory_ai_message_with_tool_calls_serializes_string_content_as_text_parts():
    message = MemoryAIMessage(
        content="I will run a tool.",
        tool_calls=[
            ToolCall(
                id="toolu_test_123",
                type="function",
                function={
                    "name": "bash",
                    "arguments": "{\"command\":\"echo hi\"}",
                },
            )
        ],
        metadata=MessageMetadata(
            agent_id="agent-1",
            agent_name="Aworld",
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
        ),
    )

    openai_message = message.to_openai_message()

    assert openai_message["content"] == [{"type": "text", "text": "I will run a tool."}]
    assert openai_message["tool_calls"][0]["function"]["name"] == "bash"


def test_memory_ai_message_without_tool_calls_keeps_plain_string_content():
    message = MemoryAIMessage(
        content="Normal assistant reply.",
        tool_calls=[],
        metadata=MessageMetadata(
            agent_id="agent-1",
            agent_name="Aworld",
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
        ),
    )

    openai_message = message.to_openai_message()

    assert openai_message["content"] == "Normal assistant reply."


def test_memory_ai_message_drops_string_none_reasoning_details():
    message = MemoryAIMessage(
        content="I will run a tool.",
        tool_calls=[
            ToolCall(
                id="toolu_test_456",
                type="function",
                function={
                    "name": "bash",
                    "arguments": "{\"command\":\"echo hi\"}",
                },
            )
        ],
        reasoning_details="None",
        metadata=MessageMetadata(
            agent_id="agent-1",
            agent_name="Aworld",
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
        ),
    )

    openai_message = message.to_openai_message()

    assert "reasoning_details" not in openai_message or openai_message["reasoning_details"] is None


def test_memory_ai_message_keeps_list_reasoning_details():
    reasoning_details = [{"type": "thinking", "text": "Step 1"}]
    message = MemoryAIMessage(
        content="I will run a tool.",
        tool_calls=[
            ToolCall(
                id="toolu_test_789",
                type="function",
                function={
                    "name": "bash",
                    "arguments": "{\"command\":\"echo hi\"}",
                },
            )
        ],
        reasoning_details=reasoning_details,
        metadata=MessageMetadata(
            agent_id="agent-1",
            agent_name="Aworld",
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
        ),
    )

    openai_message = message.to_openai_message()

    assert openai_message["reasoning_details"] == reasoning_details


def test_memory_ai_message_canonicalizes_small_tool_call_arguments_for_replay():
    message = MemoryAIMessage(
        content="I will run a tool.",
        tool_calls=[
            ToolCall(
                id="toolu_test_canonical",
                type="function",
                function={
                    "name": "bash",
                    "arguments": '{\n  "b": 2,\n  "a": 1\n}',
                },
            )
        ],
        metadata=MessageMetadata(
            agent_id="agent-1",
            agent_name="Aworld",
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
        ),
    )

    openai_message = message.to_openai_message()

    assert openai_message["tool_calls"][0]["function"]["arguments"] == '{"a":1,"b":2}'


def test_memory_ai_message_compacts_oversized_tool_call_arguments_for_replay():
    message = MemoryAIMessage(
        content="I will run a tool.",
        tool_calls=[
            ToolCall(
                id="toolu_test_compact",
                type="function",
                function={
                    "name": "bash",
                    "arguments": json.dumps(
                        {
                            "cmd": "python script.py",
                            "payload": "X" * 20000,
                            "cwd": "/tmp/workspace",
                        }
                    ),
                },
            )
        ],
        metadata=MessageMetadata(
            agent_id="agent-1",
            agent_name="Aworld",
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
        ),
    )

    openai_message = message.to_openai_message()
    compacted_args = json.loads(openai_message["tool_calls"][0]["function"]["arguments"])

    assert compacted_args["cmd"] == "python script.py"
    assert compacted_args["cwd"] == "/tmp/workspace"
    assert compacted_args["payload"]["_aworld_replay"] == "compacted_string_field"
    assert compacted_args["payload"]["sanitized_reason"] == "oversized_string_field_compaction"
    assert compacted_args["payload"]["content_hash"].startswith("sha256:")


def test_normalize_tool_call_arguments_compacts_large_string_fields_before_full_placeholder():
    arguments = json.dumps(
        {
            "command": "cat > script.py <<'EOF'\n" + ("print('hello world')\n" * 300) + "EOF\npython3 script.py\n",
            "timeout": 30,
        }
    )

    normalized = normalize_tool_call_arguments_for_replay(
        arguments,
        tool_name="bash",
        token_threshold=100000,
    )
    parsed = json.loads(normalized)

    assert parsed["timeout"] == 30
    assert isinstance(parsed["command"], dict)
    assert parsed["command"]["_aworld_replay"] == "compacted_string_field"
    assert parsed["command"]["field_hint"] == "command"
    assert parsed["command"]["sanitized_reason"] == "oversized_string_field_compaction"


def test_normalize_tool_call_arguments_preserves_moderate_subagent_directive_text():
    directive = (
        "使用 agent-browser 技能连接到本地 CDP (端口 9222)，访问 X 平台，查看用户 elliotchen100 的帖子列表。"
        "找到标题或内容包含“AI 编程的下一个瓶颈，不是代码，是理解”相关主题的帖子，提取完整内容，"
        "并返回标题、链接、发布时间、关键摘要与是否确认命中目标。"
    ) * 6

    normalized = normalize_tool_call_arguments_for_replay(
        json.dumps({"directive": directive, "name": "developer"}, ensure_ascii=False),
        tool_name="async_spawn_subagent__spawn",
        token_threshold=100000,
    )
    parsed = json.loads(normalized)

    assert parsed["name"] == "developer"
    assert parsed["directive"] == directive
