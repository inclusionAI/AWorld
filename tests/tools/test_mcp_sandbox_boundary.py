from types import SimpleNamespace

import pytest

from aworld.config.conf import ConfigDict
from aworld.core.agent.base import AgentFactory
from aworld.core.common import ActionModel, ActionResult
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.tools.mcp_tool.async_mcp_tool import McpTool


class _ForbiddenDirectTransport:
    async def call_tool(self, *args, **kwargs):
        raise AssertionError("McpTool bypassed the sandbox policy boundary")


class _RecordingSandbox:
    def __init__(self):
        self.mcpservers = _ForbiddenDirectTransport()
        self.calls = []

    async def call_tool(self, **kwargs):
        self.calls.append(kwargs)
        return [
            ActionResult(
                is_done=False,
                success=True,
                content="ok",
                metadata={"context_management": {"checkpoint_created": True}},
            )
        ]


@pytest.mark.asyncio
async def test_mcp_tool_enters_through_sandbox_policy_boundary(monkeypatch):
    sandbox = _RecordingSandbox()
    monkeypatch.setattr(
        AgentFactory,
        "agent_instance",
        lambda name: SimpleNamespace(sandbox=sandbox),
    )
    context = Context(task_id="task-1", session_id="session-1")
    message = Message(
        session_id="session-1",
        sender="agent-1",
        headers={"context": context},
    )
    action = ActionModel(
        tool_name="mcp",
        action_name="terminal__run_code",
        tool_call_id="call-1",
        agent_name="agent-1",
        params={"code": "opaque-reader"},
    )

    tool = McpTool(ConfigDict({}))
    observation, reward, *_ = await tool.do_step([action], message)

    assert reward == 1
    assert observation.action_result[0].content == "ok"
    assert len(sandbox.calls) == 1
    assert sandbox.calls[0]["context"] is context
    assert sandbox.calls[0]["event_message"] is message
    assert sandbox.calls[0]["action_list"][0].tool_name == "terminal"
    assert sandbox.calls[0]["action_list"][0].action_name == "run_code"
