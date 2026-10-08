"""MCP transport waits for the packaged tool execution budget, not a shorter default."""

from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import aworld.sandbox.run.mcp_servers as mcp_servers
from aworld.sandbox.task_budget import FrameworkTaskBudget

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.delenv("TERMINAL_TIMEOUT", raising=False)
    monkeypatch.delenv("AWORLD_TERMINAL_MAX_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES", raising=False)
    remote = AsyncMock(return_value="complete")
    reused = AsyncMock(return_value="complete")
    monkeypatch.setattr(mcp_servers, "call_mcp_tool_with_exit_stack", remote)
    monkeypatch.setattr(mcp_servers, "call_mcp_tool_with_reuse", reused)
    monkeypatch.setattr(
        mcp_servers,
        "lower_mcp_call_result",
        lambda value, **kwargs: SimpleNamespace(success=True, content=value, **kwargs),
    )

    class Servers:
        check_tool_params = mcp_servers.McpServers.check_tool_params
        call = mcp_servers.McpServers._call_tool_impl

        def __init__(self, tools):
            self.tool_list = tools
            self.mcp_config = {"mcpServers": {"terminal": {"type": "stdio"}}}
            self.mcp_servers = ["terminal"]
            self.sandbox = None
            self.server_instances = {}
            self._prepare_remote_skill_execution_params = AsyncMock()
            self.list_tools = AsyncMock()

        def _inject_env_content_parameter(self, *args):
            pass

        def _update_metadata(self, *args):
            pass

        def _should_reuse(self):
            return False

    return SimpleNamespace(
        resolve=mcp_servers._resolve_mcp_transport_timeout,
        Servers=Servers,
        remote=remote,
        reused=reused,
    )


def _schema(server="terminal", tool="run_code", **timeout):
    return [
        {
            "type": "function",
            "function": {
                "name": f"{server}__{tool}",
                "parameters": {
                    "properties": {"timeout": {"type": "number", **timeout}}
                },
            },
        }
    ]


def test_context_parameter_enrichment_does_not_materialize_schema_defaults(runtime):
    servers = runtime.Servers(_schema(default=300))
    params = {"code": "long command"}
    assert asyncio.run(servers.check_tool_params(None, "terminal", "run_code", params))
    assert "timeout" not in params
    assert (
        runtime.resolve(
            server_name="terminal",
            tool_name="run_code",
            parameter=params,
            tool_list=servers.tool_list,
        )
        == 310
    )


@pytest.mark.parametrize("reuse", [False, True])
def test_actual_call_uses_terminal_default_even_when_schema_projection_loses_it(
    runtime, reuse
):
    servers = runtime.Servers(_schema())
    servers._should_reuse = lambda: reuse
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": "build"},
    }
    (result,) = asyncio.run(servers.call(action_list=[action]))
    assert result.success
    transport = runtime.reused if reuse else runtime.remote
    assert transport.await_args.kwargs["timeout"] == 310
    assert transport.await_args.kwargs["parameter"] == {"code": "build"}


@pytest.mark.parametrize("declared,expected", [(45, 120), (450, 460), ("900", 910)])
def test_generic_schema_defaults_control_transport_budget(runtime, declared, expected):
    assert (
        runtime.resolve(
            server_name="build",
            tool_name="execute",
            parameter={},
            tool_list=_schema("build", "execute", default=declared),
        )
        == expected
    )


@pytest.mark.parametrize(
    "value,expected", [(240, 250), ("300", 310), (" 450.5 ", 460.5)]
)
def test_explicit_valid_timeouts_are_normalized_and_override_schema(
    runtime, value, expected
):
    params = {"timeout": value}
    result = runtime.resolve(
        server_name="terminal",
        tool_name="run_code",
        parameter=params,
        tool_list=_schema(default=900),
    )
    assert result == expected
    assert isinstance(params["timeout"], float)


@pytest.mark.parametrize(
    "value", [True, False, None, 0, -1, "NaN", "inf", float("inf"), []]
)
def test_invalid_explicit_timeout_is_a_tool_error_without_remote_execution(
    runtime, value
):
    servers = runtime.Servers(_schema())
    (result,) = asyncio.run(
        servers.call(
            action_list=[
                {
                    "tool_name": "terminal",
                    "action_name": "run_code",
                    "params": {"timeout": value},
                }
            ]
        )
    )
    assert not result.success
    assert "positive finite number" in result.error
    runtime.remote.assert_not_awaited()


def test_terminal_environment_overrides_and_maximum_match_execution_policy(runtime):
    assert (
        runtime.resolve(
            server_name="terminal",
            tool_name="run_code",
            parameter={},
            tool_list=_schema(),
            environ={"TERMINAL_TIMEOUT": "600"},
        )
        == 610
    )
    assert (
        runtime.resolve(
            server_name="terminal",
            tool_name="run_code",
            parameter={"timeout": 900},
            tool_list=_schema(),
            environ={"AWORLD_TERMINAL_MAX_TIMEOUT_SECONDS": "250"},
        )
        == 260
    )
    assert (
        runtime.resolve(
            server_name="terminal",
            tool_name="run_code",
            parameter={"timeout": 1e20},
            tool_list=_schema(),
            environ={},
        )
        == 3610
    )
    assert (
        runtime.resolve(
            server_name="terminal",
            tool_name="run_code",
            parameter={},
            tool_list=_schema(),
            environ={"TERMINAL_TIMEOUT": "nan"},
        )
        == 310
    )


def test_transport_lease_honors_explicit_reserve_when_snapshot_is_unspecified():
    budget = FrameworkTaskBudget(
        bounded=True,
        deadline_epoch_seconds=1120,
        remaining_seconds=120,
        completion_reserve_seconds=None,
        captured_at_epoch_seconds=1000,
    )

    decision = mcp_servers._transport_lease_decision(
        requested_timeout=300,
        budget=budget,
        environ={"AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS": "60"},
        now_epoch=1000,
    )

    assert decision.effective_seconds == 60
    assert decision.limited_by == "task_deadline"


@pytest.mark.asyncio
async def test_cancelled_provider_retention_is_bounded_and_forcibly_cleaned():
    limit = mcp_servers._MAX_RETAINED_CANCELLED_PROVIDER_CALLS
    loop = asyncio.get_running_loop()
    cancellation_counts = [0] * (limit + 5)

    async def suppress_cancellation(index):
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_counts[index] += 1

    tasks = [
        asyncio.create_task(suppress_cancellation(index))
        for index in range(limit + 5)
    ]
    await asyncio.sleep(0)
    for task in tasks:
        task.cancel()
    await asyncio.sleep(0)
    for task in tasks:
        mcp_servers._retain_cancelled_provider_call(task)
        retained, _ = mcp_servers._provider_cleanup_snapshot(loop)
        assert (
            retained
            <= mcp_servers._MAX_RETAINED_CANCELLED_PROVIDER_CALLS
        )

    for _ in range(12):
        await asyncio.sleep(0)

    assert mcp_servers._provider_cleanup_snapshot(loop) == (0, False)
    assert all(task.done() for task in tasks)
    assert mcp_servers._provider_cleanup_state_count() == 0


def test_provider_cleanup_isolated_across_debug_event_loops():
    barrier = threading.Barrier(2)

    def run_loop(index):
        loop = asyncio.new_event_loop()
        loop.set_debug(True)
        errors = []
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        asyncio.set_event_loop(loop)
        barrier.wait(timeout=5)

        async def scenario():
            cancellation_counts = [0] * 4

            async def stubborn_provider(provider_index):
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancellation_counts[provider_index] += 1

            decision = mcp_servers.ToolLeaseDecision(
                requested_seconds=1,
                policy_seconds=1,
                effective_seconds=0.001,
                remaining_task_seconds=None,
                limited_by=None,
            )
            outcomes = await asyncio.gather(
                *(
                    mcp_servers._await_with_task_lease(
                        stubborn_provider(provider_index), decision
                    )
                    for provider_index in range(4)
                ),
                return_exceptions=True,
            )
            assert all(
                isinstance(outcome, mcp_servers._TaskToolLeaseTimeout)
                for outcome in outcomes
            )
            for _ in range(20):
                if mcp_servers._provider_cleanup_snapshot(loop) == (0, False):
                    break
                await asyncio.sleep(0)
            assert mcp_servers._provider_cleanup_snapshot(loop) == (0, False)
            assert all(count >= 2 for count in cancellation_counts)
            assert not [
                task for task in asyncio.all_tasks(loop) if task is not asyncio.current_task()
            ]

        try:
            loop.run_until_complete(scenario())
            loop.run_until_complete(loop.shutdown_asyncgens())
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            return index, errors, pending
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run_loop, range(2)))

    assert all(not errors and not pending for _, errors, pending in results)
    assert mcp_servers._provider_cleanup_state_count() == 0


@pytest.mark.parametrize(
    "host,explicit,prefixes,expected",
    [
        ({}, {"TERMINAL_TIMEOUT": "600"}, "", 610),
        ({"AWORLD_TERMINAL_MAX_TIMEOUT_SECONDS": "250"}, {}, "", 310),
        ({"TERMINAL_TIMEOUT": "900"}, {"TERMINAL_TIMEOUT": "600"}, "TERMINAL_", 610),
        ({"TERMINAL_TIMEOUT": "900"}, {}, "TERMINAL_", 910),
    ],
)
def test_call_uses_effective_child_environment_not_ambient_host(
    runtime, monkeypatch, host, explicit, prefixes, expected
):
    for key, value in host.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES", prefixes)
    servers = runtime.Servers(_schema())
    servers.mcp_config["mcpServers"]["terminal"]["env"] = explicit
    (result,) = asyncio.run(
        servers.call(
            action_list=[
                {
                    "tool_name": "terminal",
                    "action_name": "run_code",
                    "params": {"code": "build"},
                }
            ]
        )
    )
    assert result.success
    assert runtime.remote.await_args.kwargs["timeout"] == expected


def test_other_tools_keep_existing_fallback_and_transport_always_has_finite_cap(
    runtime,
):
    assert (
        runtime.resolve(
            server_name="terminal",
            tool_name="read_output_artifact",
            parameter={},
            tool_list=[],
        )
        == 120
    )
    assert (
        runtime.resolve(
            server_name="browser",
            tool_name="fetch",
            parameter={},
            tool_list=_schema("browser", "fetch", default="inf"),
        )
        == 120
    )
    assert (
        runtime.resolve(
            server_name="build",
            tool_name="execute",
            parameter={"timeout": 1e100},
            tool_list=[],
        )
        == 86410
    )


def test_terminal_default_fallback_matches_the_pinned_public_tool_contract():
    tree = ast.parse(
        (ROOT / "aworld/sandbox/tool_servers/terminal/src/terminal.py").read_text()
    )
    defaults = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defaults[target.id] = node.value.value
    assert defaults["_DEFAULT_COMMAND_TIMEOUT_SECONDS"] == 300
    assert defaults["_MAX_COMMAND_TIMEOUT_SECONDS"] == 3600
    run_code = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_code"
    )
    timeout_index = next(
        index for index, arg in enumerate(run_code.args.args) if arg.arg == "timeout"
    )
    timeout_default = run_code.args.defaults[
        timeout_index - (len(run_code.args.args) - len(run_code.args.defaults))
    ]
    default_expr = next(
        item.value for item in timeout_default.keywords if item.arg == "default"
    )
    assert isinstance(default_expr, ast.Name)
    assert defaults[default_expr.id] == 300


@pytest.mark.asyncio
async def test_stdio_call_can_outlive_short_transport_floor(
    runtime, monkeypatch, tmp_path
):
    from aworld.mcp_client.utils import call_mcp_tool_with_exit_stack

    server = tmp_path / "server.py"
    server.write_text(
        "import asyncio\nfrom mcp.server.fastmcp import FastMCP\n"
        "mcp = FastMCP('budget-test')\n"
        "@mcp.tool()\n"
        "async def run_code(code: str, timeout: float = 1.5) -> str:\n"
        "    await asyncio.sleep(0.5)\n"
        "    return 'completed after old transport floor'\n"
        "mcp.run(transport='stdio')\n"
    )
    monkeypatch.setattr(mcp_servers, "_MCP_TRANSPORT_MIN_TIMEOUT_SECONDS", 0.12)
    monkeypatch.setattr(mcp_servers, "_TERMINAL_DEFAULT_TIMEOUT_SECONDS", 1.5)
    monkeypatch.setattr(mcp_servers, "_MCP_TRANSPORT_GRACE_SECONDS", 0.5)
    calls = []

    async def observed_transport(**kwargs):
        assert kwargs["timeout"] == 2.0
        calls.append(kwargs["timeout"])
        return await call_mcp_tool_with_exit_stack(**kwargs)

    monkeypatch.setattr(
        mcp_servers, "call_mcp_tool_with_exit_stack", observed_transport
    )
    servers = runtime.Servers(_schema())
    servers.mcp_config["mcpServers"]["terminal"] = {
        "type": "stdio",
        "command": sys.executable,
        "args": [str(server)],
    }
    (result,) = await servers.call(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": "work"},
            }
        ]
    )
    assert result.success
    assert calls == [2.0]
    assert "completed after old transport floor" in str(result.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize(
    ("server_name", "tool_name"),
    [("terminal", "run_code"), ("legacy", "execute")],
)
async def test_transport_boundary_cancels_provider_at_authoritative_task_lease(
    runtime, monkeypatch, reuse, server_name, tool_name
):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_transport(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(mcp_servers, "call_mcp_tool_with_exit_stack", blocked_transport)
    monkeypatch.setattr(mcp_servers, "call_mcp_tool_with_reuse", blocked_transport)
    monkeypatch.setattr(
        "aworld.sandbox.task_budget.time.time", lambda: 1000.0
    )
    servers = runtime.Servers(_schema(server_name, tool_name))
    servers._should_reuse = lambda: reuse
    task = SimpleNamespace(
        deadline_epoch_seconds=1000.05,
        completion_reserve_seconds=0.0,
        remaining_seconds=lambda: 0.05,
        bind_deadline=lambda: 1000.05,
        parent_task=None,
    )
    context = SimpleNamespace(
        task_id="task",
        get_task=lambda: task,
        agent_info=SimpleNamespace(current_agent_id="agent"),
    )
    servers.sandbox = SimpleNamespace(
        env_content={}, env_content_name=None, sandbox_id=None
    )
    servers._env_content_param_mapping = {}

    (result,) = await servers.call(
        action_list=[
            {
                "tool_name": server_name,
                "action_name": tool_name,
                "params": {"code": "work", "timeout": 300},
            }
        ],
        context=context,
    )

    assert started.is_set()
    assert cancelled.is_set()
    assert result.success is False
    assert result.metadata["failure_type"] == "task_tool_lease_timeout"
    assert result.metadata["requested_timeout_seconds"] == 300
    assert result.metadata["effective_timeout_seconds"] <= 0.05
    assert result.metadata["timeout_limited_by"] == "task_deadline"


@pytest.mark.asyncio
async def test_normal_transport_lease_is_not_fractionally_capped(runtime, monkeypatch):
    monkeypatch.setattr(
        "aworld.sandbox.task_budget.time.time", lambda: 1000.0
    )
    servers = runtime.Servers(_schema())
    task = SimpleNamespace(
        deadline_epoch_seconds=1400.0,
        completion_reserve_seconds=40.0,
        remaining_seconds=lambda: 400.0,
        bind_deadline=lambda: 1400.0,
        parent_task=None,
    )
    context = SimpleNamespace(
        task_id="task",
        get_task=lambda: task,
        agent_info=SimpleNamespace(current_agent_id="agent"),
    )
    servers.sandbox = SimpleNamespace(
        env_content={}, env_content_name=None, sandbox_id=None
    )
    servers._env_content_param_mapping = {}

    (result,) = await servers.call(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": "work", "timeout": 300},
            }
        ],
        context=context,
    )

    assert result.success
    assert runtime.remote.await_args.kwargs["timeout"] == 310


@pytest.mark.asyncio
async def test_provider_timeout_is_not_mislabeled_as_task_lease(runtime, monkeypatch):
    async def provider_timeout(**kwargs):
        raise asyncio.TimeoutError("provider-owned timeout")

    monkeypatch.setattr(
        mcp_servers, "call_mcp_tool_with_exit_stack", provider_timeout
    )
    servers = runtime.Servers(_schema())

    (result,) = await servers.call(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": "work", "timeout": 300},
            }
        ]
    )

    assert result.success is False
    assert result.metadata.get("failure_type") != "task_tool_lease_timeout"
    assert "provider-owned timeout" in result.error


@pytest.mark.asyncio
async def test_exhausted_task_lease_does_not_start_provider(runtime, monkeypatch):
    called = False

    async def provider(**kwargs):
        nonlocal called
        called = True
        return "unexpected"

    monkeypatch.setattr(mcp_servers, "call_mcp_tool_with_exit_stack", provider)
    monkeypatch.setattr(
        "aworld.sandbox.task_budget.time.time", lambda: 1000.0
    )
    servers = runtime.Servers(_schema())
    task = SimpleNamespace(
        deadline_epoch_seconds=1000.0,
        completion_reserve_seconds=0.0,
        remaining_seconds=lambda: 0.0,
        bind_deadline=lambda: 1000.0,
        parent_task=None,
    )
    context = SimpleNamespace(
        task_id="task",
        get_task=lambda: task,
        agent_info=SimpleNamespace(current_agent_id="agent"),
    )

    (result,) = await servers.call(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": "work", "timeout": 300},
            }
        ],
        context=context,
    )

    assert called is False
    assert result.success is False
    assert result.metadata["failure_type"] == "task_tool_lease_exhausted"
    assert result.metadata["effective_timeout_seconds"] == 0
    assert result.metadata["timeout_limited_by"] == "task_deadline_exhausted"
