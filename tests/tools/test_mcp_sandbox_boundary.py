from types import SimpleNamespace

import pytest

from aworld.config.conf import ConfigDict
from aworld.core.agent.base import AgentFactory
from aworld.core.common import ActionModel, ActionResult
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.task import Task
from aworld.sandbox.errors import SandboxInfrastructureError
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


class _FailingInfrastructureSandbox:
    async def call_tool(self, **kwargs):
        raise SandboxInfrastructureError(
            "docker_checkpoint_create_failed",
            "Docker checkpoint backend unavailable",
        )


class _PartialInterceptionSandbox:
    def __init__(self):
        self.executed_call_ids = []

    async def call_tool(self, *, action_list, event_message, **_kwargs):
        # The common Tool boundary removes blocked actions and its ephemeral
        # receipt before provider dispatch. The Sandbox receives only admitted
        # calls; direct BaseSandbox callers retain their own enforcement path.
        assert "tool_interception" not in event_message.headers
        results = []
        for action in action_list:
            self.executed_call_ids.append(action.tool_call_id)
            results.append(
                ActionResult(
                    is_done=False,
                    success=True,
                    tool_call_id=action.tool_call_id,
                    tool_name=action.tool_name,
                    action_name=action.action_name,
                    content="executed",
                )
            )
        return results


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


@pytest.mark.asyncio
async def test_mcp_tool_preserves_typed_sandbox_infrastructure_failure(monkeypatch):
    monkeypatch.setattr(
        AgentFactory,
        "agent_instance",
        lambda name: SimpleNamespace(sandbox=_FailingInfrastructureSandbox()),
    )
    context = Context(task_id="task-2", session_id="session-2")
    message = Message(
        session_id="session-2",
        sender="agent-2",
        headers={"context": context},
    )
    action = ActionModel(
        tool_name="mcp",
        action_name="docker__run_code",
        tool_call_id="call-2",
        agent_name="agent-2",
        params={"code": "true"},
    )

    tool = McpTool(ConfigDict({}))
    observation, reward, *_ = await tool.do_step([action], message)

    result = observation.action_result[0]
    assert reward == 0
    assert result.success is False
    assert result.metadata == {
        "failure_category": "infrastructure",
        "failure_code": "docker_checkpoint_create_failed",
    }


@pytest.mark.asyncio
async def test_mcp_tool_defers_partial_interception_to_sandbox(monkeypatch):
    sandbox = _PartialInterceptionSandbox()
    monkeypatch.setattr(
        AgentFactory,
        "agent_instance",
        lambda name: SimpleNamespace(sandbox=sandbox),
    )
    context = Context(task_id="task-partial", session_id="session-partial")
    context.set_task(Task(id="task-partial", input="test", context=context))
    actions = [
        ActionModel(
            tool_name="mcp",
            action_name="terminal__run_code",
            tool_call_id="admitted-call",
            agent_name="agent-partial",
            params={"code": "true"},
        ),
        ActionModel(
            tool_name="mcp",
            action_name="terminal__run_code",
            tool_call_id="blocked-call",
            agent_name="agent-partial",
            params={"code": "cat stale.log"},
        ),
    ]
    message = Message(
        category="tool_call",
        payload=actions,
        sender="agent-partial",
        session_id="session-partial",
        headers={"context": context},
    )
    hook_event = Message(
        category="agent_hook",
        payload=None,
        sender="mutation_gate",
        headers={
            "tool_interception": {
                "schema_version": "aworld.tool-interception/v1",
                "kind": "block",
                "tool_call_ids": ["blocked-call"],
                "block_all": False,
                "error_code": "candidate_convergence_required",
                "content_type": "candidate_convergence_required",
                "message": "submit or revise the candidate",
            }
        },
    )
    tool = McpTool(ConfigDict({}))

    async def hooks(*, hook_point, **_kwargs):
        return [hook_event] if hook_point == "before_tool_call" else []

    monkeypatch.setattr(tool, "run_hooks", hooks)

    result = await tool.step(message)

    assert sandbox.executed_call_ids == ["admitted-call"]
    action_results = result.payload[0].action_result
    assert [item.success for item in action_results] == [True, False]
    assert action_results[1].error == "candidate_convergence_required"


@pytest.mark.asyncio
async def test_mcp_direct_fallback_receives_only_admitted_actions(monkeypatch):
    monkeypatch.setattr(AgentFactory, "agent_instance", lambda _name: None)
    context = Context(task_id="task-race", session_id="session-race")
    context.set_task(Task(id="task-race", input="test", context=context))
    actions = [
        ActionModel(
            tool_name="mcp",
            action_name="terminal__run_code",
            tool_call_id="admitted-race",
            agent_name="agent-race",
            params={"code": "true"},
        ),
        ActionModel(
            tool_name="mcp",
            action_name="terminal__run_code",
            tool_call_id="blocked-race",
            agent_name="agent-race",
            params={"code": "cat stale.log"},
        ),
    ]
    message = Message(
        category="tool_call",
        payload=actions,
        sender="agent-race",
        session_id="session-race",
        headers={"context": context},
    )
    hook_event = Message(
        category="agent_hook",
        payload=None,
        sender="mutation_gate",
        headers={
            "tool_interception": {
                "schema_version": "aworld.tool-interception/v1",
                "kind": "block",
                "tool_call_ids": ["blocked-race"],
                "block_all": False,
                "error_code": "candidate_convergence_required",
                "content_type": "candidate_convergence_required",
                "message": "submit or revise the candidate",
            }
        },
    )
    tool = McpTool(ConfigDict({}))
    executed_call_ids = []

    async def direct_execute(provider_actions):
        executed_call_ids.extend(
            action.tool_call_id for action in provider_actions
        )
        return (
            [
                ActionResult(
                    success=True,
                    tool_call_id=action.tool_call_id,
                    tool_name=action.tool_name,
                    action_name=action.action_name,
                    content="direct-executed",
                )
                for action in provider_actions
            ],
            None,
        )

    monkeypatch.setattr(
        tool.action_executor,
        "async_execute_action",
        direct_execute,
    )

    async def hooks(*, hook_point, **_kwargs):
        return [hook_event] if hook_point == "before_tool_call" else []

    monkeypatch.setattr(tool, "run_hooks", hooks)

    result = await tool.step(message)

    assert executed_call_ids == ["admitted-race"]
    action_results = result.payload[0].action_result
    assert [item.success for item in action_results] == [True, False]
    assert action_results[1].error == "candidate_convergence_required"
    assert action_results[1].metadata["provider_executed"] is False


@pytest.mark.asyncio
async def test_mcp_direct_fallback_fails_closed_for_public_deliverable(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(AgentFactory, "agent_instance", lambda _name: None)
    context = Context(
        task_id="task-public",
        session_id="session-public",
        workspace_path=str(tmp_path),
    )
    context.agent_info.current_agent_id = "agent-public"
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "result",
                "path": str(tmp_path / "result.json"),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    message = Message(
        category="tool_call",
        sender="agent-public",
        session_id="session-public",
        headers={"context": context},
    )
    action = ActionModel(
        tool_name="mcp",
        action_name="terminal__run_code",
        tool_call_id="public-direct",
        agent_name="agent-public",
        params={"code": "printf x > result.json"},
    )
    tool = McpTool(ConfigDict({}))

    async def forbidden(_actions):
        raise AssertionError("public-deliverable call bypassed trusted Sandbox")

    monkeypatch.setattr(tool.action_executor, "async_execute_action", forbidden)

    observation, reward, *_ = await tool.do_step([action], message)

    assert reward == 0
    assert observation.action_result[0].success is False
    assert observation.action_result[0].metadata == {
        "failure_category": "infrastructure",
        "failure_code": "sandbox_authority_unavailable",
    }


@pytest.mark.asyncio
async def test_direct_mcp_do_step_fails_partial_v1_interception_closed(
    monkeypatch,
):
    monkeypatch.setattr(AgentFactory, "agent_instance", lambda _name: None)
    context = Context(task_id="task-direct", session_id="session-direct")
    context.set_task(Task(id="task-direct", input="test", context=context))
    actions = [
        ActionModel(
            tool_name="mcp",
            action_name="terminal__run_code",
            tool_call_id=call_id,
            agent_name="agent-direct",
            params={"code": code},
        )
        for call_id, code in (
            ("direct-admitted", "true"),
            ("direct-blocked", "cat stale.log"),
        )
    ]
    message = Message(
        category="tool_call",
        payload=actions,
        sender="agent-direct",
        session_id="session-direct",
        headers={
            "context": context,
            "tool_interception": {
                "schema_version": "aworld.tool-interception/v1",
                "kind": "block",
                "tool_call_ids": ["direct-blocked"],
                "block_all": False,
                "error_code": "candidate_convergence_required",
                "content_type": "candidate_convergence_required",
                "message": "submit or revise the candidate",
            },
        },
    )
    tool = McpTool(ConfigDict({}))

    async def forbidden(_actions):
        raise AssertionError("intercepted direct MCP batch reached transport")

    monkeypatch.setattr(tool.action_executor, "async_execute_action", forbidden)

    observation, reward, *_ = await tool.do_step(actions, message)

    assert reward == 0
    assert [item.success for item in observation.action_result] == [False, False]
    assert all(
        item.error == "candidate_convergence_required"
        for item in observation.action_result
    )
