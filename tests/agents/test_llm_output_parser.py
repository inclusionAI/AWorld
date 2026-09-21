import pytest
from types import SimpleNamespace

from aworld.agents.llm_agent import (
    LlmOutputParser,
    ToolCallBatchParseError,
    ToolCallParseIssueCode,
)
from aworld.models.model_response import Function, ModelResponse, ToolCall


@pytest.mark.parametrize(
    ("name", "arguments", "expected_code"),
    [
        ("", '{}', ToolCallParseIssueCode.MISSING_TOOL_NAME),
        ("bash", None, ToolCallParseIssueCode.EMPTY_ARGUMENTS),
        ("bash", '{"command":', ToolCallParseIssueCode.INVALID_ARGUMENTS_JSON),
        ("bash", '["pwd"]', ToolCallParseIssueCode.ARGUMENTS_NOT_OBJECT),
    ],
)
@pytest.mark.asyncio
async def test_parser_rejects_all_invalid_tool_call_batches(
    name, arguments, expected_code
):
    response = ModelResponse(
        id="resp_1",
        model="test-model",
        content="final answer",
        tool_calls=[
            ToolCall(
                id="call_1",
                function=Function(name=name, arguments=arguments),
            )
        ],
    )

    with pytest.raises(ToolCallBatchParseError) as exc_info:
        await LlmOutputParser().parse(response, agent_id="Aworld")

    assert len(exc_info.value.issues) == 1
    assert exc_info.value.issues[0].call_id == "call_1"
    assert exc_info.value.issues[0].code is expected_code


@pytest.mark.asyncio
async def test_parser_rejects_entire_batch_when_one_tool_call_is_invalid():
    response = ModelResponse(
        id="resp_1",
        model="test-model",
        content="",
        tool_calls=[
            ToolCall(
                id="call_valid",
                function=Function(name="bash", arguments='{"command": "pwd"}'),
            ),
            ToolCall(
                id="call_invalid",
                function=Function(name="bash", arguments='{"command":'),
            ),
        ],
    )

    with pytest.raises(ToolCallBatchParseError) as exc_info:
        await LlmOutputParser().parse(response, agent_id="Aworld")

    assert [issue.call_id for issue in exc_info.value.issues] == ["call_invalid"]
    assert (
        exc_info.value.issues[0].code
        is ToolCallParseIssueCode.INVALID_ARGUMENTS_JSON
    )


@pytest.mark.asyncio
async def test_parser_rejects_duplicate_tool_call_ids_atomically():
    response = ModelResponse(
        id="resp_1",
        model="test-model",
        tool_calls=[
            ToolCall(
                id="call_duplicate",
                function=Function(name="bash", arguments='{"command": "pwd"}'),
            ),
            ToolCall(
                id="call_duplicate",
                function=Function(name="bash", arguments='{"command": "ls"}'),
            ),
        ],
    )

    with pytest.raises(ToolCallBatchParseError) as exc_info:
        await LlmOutputParser().parse(response, agent_id="Aworld")

    assert len(exc_info.value.issues) == 1
    assert exc_info.value.issues[0].call_id == "call_duplicate"
    assert (
        exc_info.value.issues[0].code
        is ToolCallParseIssueCode.DUPLICATE_CALL_ID
    )


@pytest.mark.parametrize(
    ("invalid_call", "expected_code"),
    [
        (
            {"function": {"name": "bash", "arguments": "{}"}},
            ToolCallParseIssueCode.MISSING_CALL_ID,
        ),
        (
            {"id": "call_invalid", "function": {"arguments": "{}"}},
            ToolCallParseIssueCode.MISSING_TOOL_NAME,
        ),
        ({"type": "function"}, ToolCallParseIssueCode.MISSING_CALL_ID),
        ({}, ToolCallParseIssueCode.MISSING_CALL_ID),
    ],
)
@pytest.mark.asyncio
async def test_openai_conversion_preserves_malformed_calls_for_atomic_rejection(
    invalid_call, expected_code
):
    response = {
        "id": "resp_provider",
        "model": "test-model",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_valid",
                            "type": "function",
                            "function": {
                                "name": "bash",
                                "arguments": '{"command": "pwd"}',
                            },
                        },
                        invalid_call,
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    converted = ModelResponse.from_openai_response(response)

    assert len(converted.tool_calls) == 2
    with pytest.raises(ToolCallBatchParseError) as exc_info:
        await LlmOutputParser().parse(converted, agent_id="Aworld")
    assert len(exc_info.value.issues) == 1
    assert exc_info.value.issues[0].code is expected_code


@pytest.mark.asyncio
async def test_parser_keeps_valid_tool_call():
    response = ModelResponse(
        id="resp_1",
        model="test-model",
        content="",
        tool_calls=[
            ToolCall(
                id="call_1",
                function=Function(name="bash", arguments='{"command": "pwd"}'),
            )
        ],
    )

    result = await LlmOutputParser().parse(response, agent_id="Aworld")

    assert result.is_call_tool is True
    assert len(result.actions) == 1
    assert result.actions[0].tool_name == "bash"
    assert result.actions[0].params == {"command": "pwd"}


@pytest.mark.asyncio
async def test_parser_uses_agent_scoped_mapping_with_shared_sandbox():
    shared_mcp = SimpleNamespace(
        mcp_servers=["alpha", "beta"],
        # Simulate a later agent overwriting the legacy shared mapping.
        map_tool_list={"run": "beta__run"},
    )
    shared_sandbox = SimpleNamespace(mcpservers=shared_mcp)
    alpha_agent = SimpleNamespace(
        sandbox=shared_sandbox,
        tool_mapping={"run": "alpha__run"},
    )
    response = ModelResponse(
        id="resp_1",
        model="test-model",
        tool_calls=[
            ToolCall(
                id="call_1",
                function=Function(name="run", arguments='{"code": "pwd"}'),
            )
        ],
    )

    result = await LlmOutputParser().parse(
        response,
        agent_id="alpha-agent",
        agent=alpha_agent,
    )

    assert result.actions[0].tool_name == "mcp"
    assert result.actions[0].action_name == "alpha__run"


@pytest.mark.asyncio
async def test_parser_does_not_fall_back_to_another_agents_shared_mapping():
    shared_mcp = SimpleNamespace(
        mcp_servers=["alpha", "beta"],
        map_tool_list={"run": "beta__run"},
    )
    shared_sandbox = SimpleNamespace(mcpservers=shared_mcp)
    agent_without_registered_tools = SimpleNamespace(
        sandbox=shared_sandbox,
        tool_mapping={},
    )
    response = ModelResponse(
        id="resp_1",
        model="test-model",
        tool_calls=[
            ToolCall(
                id="call_1",
                function=Function(name="run", arguments="{}"),
            )
        ],
    )

    result = await LlmOutputParser().parse(
        response,
        agent_id="empty-agent",
        agent=agent_without_registered_tools,
    )

    assert result.actions[0].tool_name == "run"
    assert result.actions[0].action_name == ""
