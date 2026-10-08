from dataclasses import replace
import pytest
from mcp.types import CallToolResult, TextContent
from types import SimpleNamespace

from aworld.sandbox.run.mcp_servers import (
    McpServers,
    _build_tool_call_failure_result,
    _coalesce_tool_result_content,
)
from aworld.sandbox.errors import SandboxInfrastructureError
from aworld.core.context.base import Context
from aworld.core.execution_protocol import (
    ConvergenceStage,
    ExecutionProtocolPolicy,
    ExecutionProtocolStore,
)
from aworld.core.task import Task
from aworld.runners.execution_protocol import configure_execution_protocol
from aworld.sandbox.task_budget import FrameworkTaskBudget, resolve_tool_lease


def test_coalesce_tool_result_content_returns_plain_string_for_single_item():
    assert _coalesce_tool_result_content(["only line"]) == "only line"


def test_coalesce_tool_result_content_preserves_multiple_items():
    assert _coalesce_tool_result_content(["line one", "line two"]) == [
        "line one",
        "line two",
    ]


def test_coalesce_tool_result_content_returns_empty_string_for_no_items():
    assert _coalesce_tool_result_content([]) == ""


def test_build_tool_call_failure_result_includes_error_context_and_parameter_summary():
    result = _build_tool_call_failure_result(
        server_name="terminal",
        tool_name="mcp_execute_command",
        parameter={"command": "python script.py", "timeout": 30},
        error=RuntimeError("boom"),
    )

    assert result.tool_name == "terminal"
    assert result.action_name == "mcp_execute_command"
    assert "terminal__mcp_execute_command" in result.content
    assert "RuntimeError: boom" in result.content
    assert "command=python script.py" in result.content
    assert "timeout=30" in result.content


def test_build_tool_call_failure_result_preserves_typed_infrastructure_error():
    result = _build_tool_call_failure_result(
        server_name="docker",
        tool_name="run_code",
        parameter={},
        error=SandboxInfrastructureError(
            "docker_checkpoint_create_failed", "backend unavailable"
        ),
    )

    assert result.metadata == {
        "failure_category": "infrastructure",
        "failure_code": "docker_checkpoint_create_failed",
    }


def test_hidden_env_content_keeps_framework_task_scope_authoritative():
    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"terminal__run_code": "env_content"}
    servers.sandbox = SimpleNamespace(
        sandbox_id="sandbox",
        env_content={
            "task_id": "stale-sandbox-task",
            "session_id": "stale-sandbox-session",
            "task_epoch": "stale-sandbox-epoch",
            "checkpoint_revision": 99,
            "sandbox_id": "stale-sandbox-id",
            "sandbox_only": "kept",
        },
    )
    parameter = {
        "env_content": {
            "task_id": "caller-task",
            "session_id": "caller-session",
            "task_epoch": "caller-epoch",
            "checkpoint_revision": 98,
            "session_epoch": 98,
            "branch_id": "caller-branch",
            "agent_id": "caller-agent",
            "prompt_namespace": "caller-prompt",
            "sandbox_id": "caller-sandbox-id",
            "caller_only": "kept",
        }
    }

    servers._inject_env_content_parameter(
        "terminal__run_code",
        parameter,
        SimpleNamespace(
            task_id="trusted-task",
            session_id="trusted-session",
            task_epoch=37,
            agent_info=SimpleNamespace(current_agent_id="trusted-agent"),
            context_lifecycle_state=SimpleNamespace(
                session_epoch=2,
                branch_id="trusted-branch",
                checkpoint_revision=0,
            ),
        ),
    )

    assert parameter["env_content"] == {
        "task_id": "trusted-task",
        "session_id": "trusted-session",
        "task_epoch": 37,
        "session_epoch": 2,
        "branch_id": "trusted-branch",
        "checkpoint_revision": 0,
        "agent_id": "trusted-agent",
        "prompt_namespace": "trusted-agent",
        "sandbox_id": "sandbox",
        "caller_only": "kept",
        "sandbox_only": "kept",
        "task_budget": {
            "authority": "aworld_task",
            "schema_version": "aworld.task-budget/v1",
            "bounded": False,
            "stage": "execute",
        },
    }


def test_hidden_scope_preserves_zero_epoch_and_current_checkpoint_revision():
    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"docker__read_file": "env_content"}
    servers.sandbox = SimpleNamespace(env_content={}, sandbox_id="sandbox")
    parameter = {}
    context = SimpleNamespace(
        task_id="task",
        session_id="session",
        task_epoch=0,
        context_lifecycle_state=SimpleNamespace(
            session_epoch=3,
            branch_id="rewind-branch",
            checkpoint_revision=4,
        ),
        agent_info=SimpleNamespace(current_agent_id="agent"),
    )

    servers._inject_env_content_parameter(
        "docker__read_file",
        parameter,
        context,
    )

    assert parameter["env_content"]["task_epoch"] == 0
    assert parameter["env_content"]["checkpoint_revision"] == 4
    assert parameter["env_content"]["session_epoch"] == 3
    assert parameter["env_content"]["branch_id"] == "rewind-branch"
    assert parameter["env_content"]["agent_id"] == "agent"
    assert parameter["env_content"]["prompt_namespace"] == "agent"
    assert parameter["env_content"]["sandbox_id"] == "sandbox"


def test_hidden_env_content_carries_framework_owned_task_budget():
    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"terminal__run_code": "env_content"}
    servers.sandbox = SimpleNamespace(env_content={})
    task = SimpleNamespace(
        deadline_epoch_seconds=2000.0,
        completion_reserve_seconds=30.0,
        bind_deadline=lambda: 2000.0,
    )
    parameter = {
        "env_content": {
            "task_budget": {
                "authority": "aworld_task",
                "schema_version": "aworld.task-budget/v1",
                "bounded": True,
                "stage": "execute",
                "deadline_epoch_seconds": 9999.0,
                "remaining_seconds": 9999.0,
                "completion_reserve_seconds": 0.0,
                "captured_at_epoch_seconds": 0.0,
            }
        }
    }

    servers._inject_env_content_parameter(
        "terminal__run_code",
        parameter,
        SimpleNamespace(
            task_id="task",
            session_id="session",
            task_epoch=2,
            get_task=lambda: task,
        ),
    )

    budget = parameter["env_content"]["task_budget"]
    assert budget["authority"] == "aworld_task"
    assert budget["schema_version"] == "aworld.task-budget/v1"
    assert budget["bounded"] is True
    assert budget["deadline_epoch_seconds"] <= 2000.0
    assert budget["completion_reserve_seconds"] == 30.0


@pytest.mark.parametrize(
    "failure_mode", ["missing_context", "missing_getter", "getter_error"]
)
def test_hidden_task_budget_is_authoritative_even_without_a_task(failure_mode):
    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"terminal__run_code": "env_content"}
    servers.sandbox = SimpleNamespace(
        env_content={
            "task_budget": {
                "authority": "aworld_task",
                "bounded": True,
                "deadline_epoch_seconds": 999999.0,
            },
            "sandbox_only": "kept",
        }
    )
    parameter = {
        "env_content": {
            "task_budget": {
                "authority": "aworld_task",
                "bounded": True,
                "deadline_epoch_seconds": 888888.0,
            },
            "caller_only": "kept",
        }
    }
    if failure_mode == "missing_context":
        context = None
    elif failure_mode == "missing_getter":
        context = SimpleNamespace(task_id="task")
    else:

        def fail_get_task():
            raise RuntimeError("stale context")

        context = SimpleNamespace(task_id="task", get_task=fail_get_task)

    servers._inject_env_content_parameter("terminal__run_code", parameter, context)

    assert parameter["env_content"]["task_budget"] == {
        "authority": "aworld_task",
        "schema_version": "aworld.task-budget/v1",
        "bounded": False,
        "stage": "execute",
    }
    assert parameter["env_content"]["sandbox_only"] == "kept"
    assert parameter["env_content"]["caller_only"] == "kept"


def test_hidden_task_budget_uses_monotonic_tight_remaining_snapshot(monkeypatch):
    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"terminal__run_code": "env_content"}
    servers.sandbox = SimpleNamespace(env_content={})
    task = SimpleNamespace(
        deadline_epoch_seconds=5000.0,
        completion_reserve_seconds=20.0,
        bind_deadline=lambda: 5000.0,
        remaining_seconds=lambda: 40.0,
        parent_task=None,
    )
    monkeypatch.setattr("aworld.sandbox.task_budget.time.time", lambda: 1000.0)
    parameter = {}

    servers._inject_env_content_parameter(
        "terminal__run_code",
        parameter,
        SimpleNamespace(get_task=lambda: task),
    )

    budget = parameter["env_content"]["task_budget"]
    assert budget["deadline_epoch_seconds"] == 1040.0
    assert budget["remaining_seconds"] == 40.0
    assert budget["captured_at_epoch_seconds"] == 1000.0


def test_hidden_task_budget_uses_llm_agent_protocol_reserve(monkeypatch):
    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"terminal__run_code": "env_content"}
    servers.sandbox = SimpleNamespace(env_content={})
    task = Task(
        id="reserve-authority",
        timeout=3753.0,
        completion_reserve_seconds=60.0,
    )
    context = Context(task_id=task.id)
    context.set_task(task)
    context.agent_info.current_agent_id = "agent"
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            finalization_reserve_seconds=105.0,
            candidate_decision_reserve_seconds=405.0,
        ),
    )
    monkeypatch.setattr("aworld.sandbox.task_budget.time.time", lambda: 1000.0)
    task.deadline_epoch_seconds = 4753.0
    task._bound_deadline_epoch_seconds = 4753.0
    task.remaining_seconds = lambda: 3753.0
    parameter = {}

    servers._inject_env_content_parameter(
        "terminal__run_code", parameter, context
    )

    budget = parameter["env_content"]["task_budget"]
    assert budget["completion_reserve_seconds"] == 105.0
    assert budget["remaining_seconds"] == 3753.0
    decision = resolve_tool_lease(
        3700.0,
        budget=FrameworkTaskBudget.from_hidden_dict(budget),
        maximum_seconds=86410.0,
        now_epoch=1000.0,
    )
    assert decision.effective_seconds == 3648.0
    assert decision.limited_by == "task_deadline"


@pytest.mark.parametrize(
    ("remaining", "expected_stage"),
    [
        (59.0, "candidate_due"),
        (34.0, "validation_due"),
        (19.0, "delivery_only"),
    ],
)
def test_hidden_task_budget_uses_live_deadline_fraction(
    remaining: float,
    expected_stage: str,
) -> None:
    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"terminal__run_code": "env_content"}
    servers.sandbox = SimpleNamespace(env_content={})
    task = Task(id=f"stage-{expected_stage}", timeout=100.0)
    context = Context(task_id=task.id)
    context.set_task(task)
    task.remaining_seconds = lambda: remaining

    parameter = {}
    servers._inject_env_content_parameter(
        "terminal__run_code", parameter, context
    )

    budget = parameter["env_content"]["task_budget"]
    assert budget["stage"] == "deadline"
    assert budget["deadline_stage"] == expected_stage
    assert FrameworkTaskBudget.from_hidden_dict(budget).stage.value == expected_stage


def test_hidden_task_budget_projects_typed_convergence_stage():
    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"terminal__run_code": "env_content"}
    servers.sandbox = SimpleNamespace(env_content={})
    task = Task(timeout=300)
    context = Context(task_id=task.id)
    context.set_task(task)
    context.agent_info.current_agent_id = "agent"
    store = ExecutionProtocolStore(context, "agent", ExecutionProtocolPolicy())
    store.save(
        replace(
            store.load(),
            convergence_constraint_active=True,
            convergence_stage=ConvergenceStage.PRODUCE_CANDIDATE,
        )
    )
    parameter = {}

    servers._inject_env_content_parameter("terminal__run_code", parameter, context)

    assert parameter["env_content"]["task_budget"]["stage"] == "convergence"


def test_hidden_task_budget_derives_typed_deadline_stage_from_live_task():
    from aworld.runners.execution_protocol import (
        EXECUTION_PROTOCOL_DEADLINE_GUIDANCE_KEY,
    )

    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"terminal__run_code": "env_content"}
    servers.sandbox = SimpleNamespace(env_content={})
    task = Task(timeout=300)
    context = Context(task_id=task.id)
    context.set_task(task)
    task.remaining_seconds = lambda: 90.0
    context.agent_info.current_agent_id = "agent"
    context.write_task_runtime_state(
        "agent",
        EXECUTION_PROTOCOL_DEADLINE_GUIDANCE_KEY,
        {
            "schema_version": "aworld.deadline-guidance/v1",
            "last_stage": "validation_due",
        },
    )
    parameter = {}

    servers._inject_env_content_parameter("terminal__run_code", parameter, context)

    budget = parameter["env_content"]["task_budget"]
    assert budget["stage"] == "deadline"
    assert budget["deadline_stage"] == "validation_due"


def _terminal_tool(tool_name: str, param_name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": f"terminal__{tool_name}",
            "parameters": {
                "type": "object",
                "properties": {
                    param_name: {
                        "type": "string",
                    }
                },
            },
        },
    }


class _FailingSyncSandbox:
    def __init__(self) -> None:
        self.mode = "remote"
        self.sandbox_id = None
        self.reuse = False
        self.env_content_name = None

    async def ensure_skill_execution_assets_ready(
        self,
        skill_name: str,
        skill_config: dict[str, object],
    ) -> str:
        raise RuntimeError(f"sync failed for {skill_name}")


@pytest.mark.asyncio
async def test_call_tool_surfaces_remote_sync_failure_before_terminal_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = {"called": False}

    async def _unexpected_call(**kwargs):
        executed["called"] = True
        return None

    monkeypatch.setattr(
        "aworld.sandbox.run.mcp_servers.call_mcp_tool_with_exit_stack",
        _unexpected_call,
    )

    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=_FailingSyncSandbox(),
        skill_configs={
            "browser-use": {
                "asset_root": "/host/skills/browser-use",
                "execution_assets": {
                    "enabled": True,
                    "relative_paths": ["scripts/run.py"],
                    "digest": "feed1234feed1234",
                },
            }
        },
    )
    servers.tool_list = [_terminal_tool("run_code", "code")]

    results = await servers.call_tool(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": "python /skills/browser-use/scripts/run.py"},
            }
        ],
        context=None,
    )

    assert executed["called"] is False
    assert results is not None
    assert len(results) == 1
    assert "sync failed for browser-use" in results[0].content


@pytest.mark.asyncio
async def test_call_tool_returns_one_typed_result_per_action(monkeypatch):
    async def _fake_call(**kwargs):
        return CallToolResult(
            content=[TextContent(type="text", text="tool rejected input")],
            structuredContent={"reason": "invalid"},
            isError=True,
        )

    monkeypatch.setattr(
        "aworld.sandbox.run.mcp_servers.call_mcp_tool_with_exit_stack",
        _fake_call,
    )
    servers = McpServers(
        mcp_servers=["demo"],
        mcp_config={"mcpServers": {"demo": {}}},
    )
    servers.tool_list = [
        {
            "type": "function",
            "function": {
                "name": "demo__run",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    results = await servers.call_tool(
        action_list=[
            {"tool_name": "demo", "action_name": "run", "params": {}},
            {"tool_name": "demo", "params": {}},
        ]
    )

    assert len(results) == 2
    assert results[0].success is False
    assert results[0].error == "tool rejected input"
    assert results[0].metadata == {"structured_content": {"reason": "invalid"}}
    assert results[1].success is False
    assert "Missing action_name" in results[1].content


@pytest.mark.asyncio
async def test_call_tool_handles_empty_mcp_content_without_dropping_result(monkeypatch):
    async def _fake_call(**kwargs):
        return CallToolResult(content=[])

    monkeypatch.setattr(
        "aworld.sandbox.run.mcp_servers.call_mcp_tool_with_exit_stack",
        _fake_call,
    )
    servers = McpServers(
        mcp_servers=["demo"],
        mcp_config={"mcpServers": {"demo": {}}},
    )
    servers.tool_list = [{}]

    results = await servers.call_tool(
        action_list=[{"tool_name": "demo", "action_name": "noop", "params": {}}]
    )

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].content == ""
