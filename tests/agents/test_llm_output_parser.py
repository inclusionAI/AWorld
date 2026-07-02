import pytest

from aworld.agents.llm_agent import LlmOutputParser
from aworld.models.model_response import Function, ModelResponse, ToolCall


@pytest.mark.asyncio
async def test_parser_ignores_malformed_tool_call_when_response_has_content():
    response = ModelResponse(
        id="resp_1",
        model="test-model",
        content="final answer",
        tool_calls=[
            ToolCall(
                id="call_1",
                function=Function(name="bash", arguments=None),
            )
        ],
    )

    result = await LlmOutputParser().parse(response, agent_id="Aworld")

    assert result.is_call_tool is False
    assert len(result.actions) == 1
    assert result.actions[0].policy_info == "final answer"
    assert result.actions[0].tool_name is None


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
