from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.sandbox.config.manager import ToolConfigManager
from aworld.sandbox.builtin.router import BuiltinToolRouter
from aworld.sandbox.execution_boundary import (
    ToolExecutionTarget,
    TransportKind,
    resolve_tool_execution_boundary,
)
from aworld.sandbox.implementations.sandbox import Sandbox
from aworld.sandbox.models import SandboxEnvType
from aworld.mcp_client import utils as mcp_utils
from aworld.core.common import ActionResult
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.compiler import LifecycleAction
from aworld.core.event.base import Message
from aworld.core.tool_action_journal import read_tool_action_journal
from aworld.sandbox.terminal_receipt import (
    TERMINAL_EXECUTION_RECEIPT_KEY,
    TerminalExecutionPlan,
    build_terminal_execution_receipt,
)


def test_stdio_boundary_is_the_current_process_environment_and_cwd(
    tmp_path: Path,
) -> None:
    receipt = resolve_tool_execution_boundary(
        server_name="terminal",
        server_config={"type": "stdio", "command": "python", "args": ["server.py"]},
        sandbox_mode="local",
        process_cwd=tmp_path,
    )

    assert receipt.transport is TransportKind.STDIO
    assert receipt.target is ToolExecutionTarget.CURRENT_PROCESS_ENVIRONMENT
    assert receipt.process_relationship == "child_process"
    assert receipt.working_directory == str(tmp_path.resolve())
    assert receipt.working_directory_source == "aworld_process_cwd"
    assert receipt.mode_consistent is True
    assert receipt.reason_code is None
    assert str(tmp_path) not in repr(receipt)


def test_stdio_boundary_resolves_configured_relative_cwd_from_aworld_process(
    tmp_path: Path,
) -> None:
    receipt = resolve_tool_execution_boundary(
        server_name="filesystem",
        server_config={
            "type": "stdio",
            "command": "python",
            "cwd": "task",
        },
        sandbox_mode="local",
        process_cwd=tmp_path,
    )

    assert receipt.working_directory == str((tmp_path / "task").resolve())
    assert receipt.working_directory_source == "server_config"


@pytest.mark.parametrize("transport", ["sse", "streamable-http", "api"])
def test_network_transport_is_remote_even_when_legacy_mode_says_local(
    transport: str,
    tmp_path: Path,
) -> None:
    receipt = resolve_tool_execution_boundary(
        server_name="search",
        server_config={"type": transport, "url": "https://example.invalid/mcp"},
        sandbox_mode="local",
        process_cwd=tmp_path,
    )

    assert receipt.target is ToolExecutionTarget.REMOTE_SERVICE
    assert receipt.mode_consistent is False
    assert receipt.reason_code == "mode_transport_mismatch"
    # The receipt is deliberately metadata-only: endpoint URLs and environment
    # values must not leak into trajectory or diagnostic output.
    serialized = receipt.to_dict()
    assert "example.invalid" not in repr(serialized)


def test_docker_stdio_bridge_reports_container_as_the_effective_target(
    tmp_path: Path,
) -> None:
    receipt = resolve_tool_execution_boundary(
        server_name="docker",
        server_config={
            "type": "stdio",
            "command": "python",
            "env": {"AWORLD_DOCKER_CONTAINER": "private-container-name"},
        },
        sandbox_mode="remote",
        sandbox_env_type=SandboxEnvType.DOCKER,
        sandbox_metadata={"docker_workdir": "/app"},
        process_cwd=tmp_path,
    )

    assert receipt.target is ToolExecutionTarget.DOCKER_CONTAINER
    assert receipt.process_relationship == "stdio_bridge"
    assert receipt.working_directory == "/app"
    assert receipt.working_directory_source == "sandbox_metadata"
    assert receipt.mode_consistent is True
    assert "private-container-name" not in repr(receipt.to_dict())


def test_non_bridge_stdio_server_in_docker_process_is_not_mislabeled_container(
    tmp_path: Path,
) -> None:
    receipt = resolve_tool_execution_boundary(
        server_name="custom-local-helper",
        server_config={"type": "stdio", "command": "python"},
        sandbox_mode="remote",
        sandbox_env_type=SandboxEnvType.DOCKER,
        sandbox_metadata={"docker_workdir": "/app"},
        process_cwd=tmp_path,
    )

    assert receipt.target is ToolExecutionTarget.CURRENT_PROCESS_ENVIRONMENT
    assert receipt.working_directory == str(tmp_path.resolve())
    assert receipt.mode_consistent is False


def test_builtin_stdio_configs_pin_the_first_workspace_as_process_cwd(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "task"
    workspace.mkdir()

    config = ToolConfigManager(
        mode="local",
        workspaces=[str(workspace), str(tmp_path / "secondary")],
    ).get_mcp_config(["filesystem", "terminal"])

    assert config["mcpServers"]["filesystem"]["cwd"] == str(workspace.resolve())
    assert config["mcpServers"]["terminal"]["cwd"] == str(workspace.resolve())


def test_builtin_stdio_cwd_does_not_drift_after_config_is_built(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = tmp_path / "initial"
    later = tmp_path / "later"
    initial.mkdir()
    later.mkdir()
    monkeypatch.chdir(initial)

    config = ToolConfigManager(mode="local").get_mcp_config(["filesystem"])
    monkeypatch.chdir(later)

    assert config["mcpServers"]["filesystem"]["cwd"] == str(initial.resolve())


@pytest.mark.asyncio
async def test_mcp_factory_passes_the_pinned_cwd_to_the_stdio_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class _Server:
        def __init__(self, *, name, params):
            captured.update({"name": name, "params": params})

        async def connect(self):
            return None

    monkeypatch.setattr(mcp_utils, "MCPServerStdio", _Server)
    workspace = tmp_path / "task"
    workspace.mkdir()
    config = ToolConfigManager(
        mode="local", workspaces=[str(workspace)]
    ).get_mcp_config(["filesystem"])

    await mcp_utils.get_server_instance("filesystem", mcp_config=config)

    assert captured["name"] == "filesystem"
    assert captured["params"]["cwd"] == str(workspace.resolve())


def test_remote_mode_cannot_silently_spawn_process_local_builtin_tools() -> None:
    with pytest.raises(ValueError, match="process-local"):
        ToolConfigManager(mode="remote").get_mcp_config(["filesystem"])


def test_unknown_sandbox_mode_fails_closed() -> None:
    sandbox = object.__new__(Sandbox)
    sandbox._mode = "local"

    with pytest.raises(ValueError, match="local.*remote"):
        sandbox.mode = "locla"


@pytest.mark.asyncio
async def test_builtin_router_does_not_fall_back_to_local_for_unknown_mode() -> None:
    class _Builtin:
        async def execute(self, *_args, **_kwargs):
            pytest.fail("invalid mode must not execute a process-local Tool")

    result = await BuiltinToolRouter(SimpleNamespace(mode="locla")).route_call(
        "terminal",
        "execute_command",
        _Builtin(),
        command="pwd",
    )

    assert result["success"] is False
    assert result["data"] is None
    assert "Unsupported sandbox mode" in result["error"]


def test_sandbox_resolves_boundary_from_its_effective_merged_config(
    tmp_path: Path,
) -> None:
    sandbox = object.__new__(Sandbox)
    sandbox._mcp_config = {
        "mcpServers": {
            "filesystem": {
                "type": "stdio",
                "command": "python",
                "cwd": str(tmp_path),
            }
        }
    }
    sandbox._mode = "local"
    sandbox._env_type = SandboxEnvType.LOCAL
    sandbox._metadata = {}

    receipt = sandbox.get_tool_execution_boundary("filesystem")

    assert receipt.target is ToolExecutionTarget.CURRENT_PROCESS_ENVIRONMENT
    assert receipt.working_directory == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_sandbox_action_journal_carries_redacted_boundary_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = []

    def _record(**kwargs):
        recorded.append(kwargs)

    class _McpServers:
        async def call_tool(self, **kwargs):
            return [ActionResult(success=True, content="ok")]

    monkeypatch.setattr("aworld.sandbox.base.append_tool_action_event", _record)
    sandbox = object.__new__(Sandbox)
    sandbox._sandbox_id = "sandbox-1"
    sandbox._mcp_config = {
        "mcpServers": {
            "terminal": {
                "type": "stdio",
                "command": "python",
                "cwd": str(tmp_path),
            }
        }
    }
    sandbox._mode = "local"
    sandbox._env_type = SandboxEnvType.LOCAL
    sandbox._metadata = {}
    sandbox._mcpservers = _McpServers()

    await sandbox.call_tool(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "execute_command",
                "params": {"command": "pwd"},
            }
        ],
        context=SimpleNamespace(),
    )

    boundary = recorded[0]["metadata"]["execution_boundaries"][0]
    assert boundary["target"] == "current_process_environment"
    assert boundary["working_directory_present"] is True
    assert boundary["working_directory_hash"].startswith("sha256:")
    assert str(tmp_path) not in repr(boundary)


def _sandbox_context():
    return SimpleNamespace(
        task_id="task-1",
        task_epoch=1,
        session_id="session-1",
    )


@pytest.mark.asyncio
async def test_sandbox_delegates_repeated_filesystem_read_epoch_checks_to_provider() -> None:
    calls = []

    class _McpServers:
        async def call_tool(self, **kwargs):
            calls.append(kwargs["action_list"])
            action = kwargs["action_list"][0]
            return [
                ActionResult(
                    success=True,
                    tool_name=action["tool_name"],
                    action_name=action["action_name"],
                    content="alpha\nbeta\n",
                )
            ]

    sandbox = object.__new__(Sandbox)
    sandbox._sandbox_id = "sandbox-1"
    sandbox._env_type = SandboxEnvType.LOCAL
    sandbox._metadata = {}
    sandbox._mcpservers = _McpServers()
    action = {
        "tool_name": "filesystem",
        "action_name": "read_file",
        "params": {"path": "/app/a.txt", "head": 20},
    }
    context = _sandbox_context()

    first = await sandbox.call_tool(action_list=[action], context=context)
    repeated = await sandbox.call_tool(action_list=[action], context=context)

    assert len(calls) == 2
    assert first[0].content == "alpha\nbeta\n"
    assert repeated[0].content == first[0].content
    receipt = repeated[0].metadata["sandbox_observation"]
    assert receipt["canonical_tool"] == "filesystem.read_file"
    assert receipt["effect"] == "read_only"
    assert receipt["cache_hit"] is False


@pytest.mark.asyncio
async def test_sandbox_uses_terminal_receipt_then_replays_from_observation_cache() -> None:
    calls = []
    code = "if depth > 3:\n    print(depth)"
    terminal_receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan("python", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
    )

    class _McpServers:
        async def call_tool(self, **kwargs):
            action = kwargs["action_list"][0]
            calls.append(action)
            return [
                ActionResult(
                    success=True,
                    tool_name=action["tool_name"],
                    action_name=action["action_name"],
                    content="4\n",
                    metadata={TERMINAL_EXECUTION_RECEIPT_KEY: terminal_receipt},
                    parameter=action["params"],
                )
            ]

    sandbox = object.__new__(Sandbox)
    sandbox._sandbox_id = "sandbox-1"
    sandbox._env_type = SandboxEnvType.LOCAL
    sandbox._metadata = {}
    sandbox._mcpservers = _McpServers()
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code, "language": "python"},
    }
    context = ApplicationContext.create(
        session_id="cache-session",
        task_id="task-1",
        task_content="inspect state",
    )

    first = await sandbox.call_tool(action_list=[action], context=context)
    repeated = await sandbox.call_tool(action_list=[action], context=context)
    context.advance_context_lifecycle(LifecycleAction.CHECKPOINT)
    rehydrated = await sandbox.call_tool(action_list=[action], context=context)
    retained_again = await sandbox.call_tool(action_list=[action], context=context)

    assert len(calls) == 1
    assert first[0].metadata["sandbox_observation"]["effect"] == "read_only"
    assert first[0].metadata["sandbox_observation"]["workspace_generation"] == 0
    assert repeated[0].metadata["sandbox_observation"]["cache_hit"] is True
    assert repeated[0].metadata["sandbox_observation"]["cache_state"] == (
        "retained_reference"
    )
    assert rehydrated[0].content == "4\n"
    assert rehydrated[0].metadata["sandbox_observation"]["cache_state"] == "rehydrated"
    assert retained_again[0].metadata["sandbox_observation"]["cache_state"] == (
        "retained_reference"
    )


@pytest.mark.asyncio
async def test_sandbox_mutation_invalidates_repeated_read_without_claiming_unknown_progress() -> (
    None
):
    calls = []

    class _McpServers:
        async def call_tool(self, **kwargs):
            action = kwargs["action_list"][0]
            calls.append(action["action_name"])
            return [
                ActionResult(
                    success=True,
                    tool_name=action["tool_name"],
                    action_name=action["action_name"],
                    content="ok",
                )
            ]

    sandbox = object.__new__(Sandbox)
    sandbox._sandbox_id = "sandbox-1"
    sandbox._env_type = SandboxEnvType.LOCAL
    sandbox._metadata = {}
    sandbox._mcpservers = _McpServers()
    context = _sandbox_context()
    read = {
        "tool_name": "filesystem",
        "action_name": "read_file",
        "params": {"path": "/app/a.txt"},
    }
    write = {
        "tool_name": "filesystem",
        "action_name": "write_file",
        "params": {"path": "/app/a.txt", "content": "changed"},
    }

    await sandbox.call_tool(action_list=[read], context=context)
    mutation = await sandbox.call_tool(action_list=[write], context=context)
    await sandbox.call_tool(action_list=[read], context=context)

    assert calls == ["read_file", "write_file", "read_file"]
    receipt = mutation[0].metadata["sandbox_observation"]
    assert receipt["effect"] == "mutating"
    assert receipt["workspace_mutated"] is True
    assert receipt["workspace_generation"] == 1


@pytest.mark.asyncio
async def test_sandbox_enforces_agent_capability_allowlist_at_execution() -> None:
    class _McpServers:
        async def call_tool(self, **_kwargs):
            pytest.fail("denied capability must not reach the transport")

    sandbox = object.__new__(Sandbox)
    sandbox._sandbox_id = "sandbox-1"
    sandbox._env_type = SandboxEnvType.LOCAL
    sandbox._metadata = {}
    sandbox._mcpservers = _McpServers()

    results = await sandbox.call_tool(
        action_list=[
            {
                "tool_name": "filesystem",
                "action_name": "read_file",
                "params": {"path": "/app/a.txt"},
            }
        ],
        context=_sandbox_context(),
        allowed_servers=["terminal"],
    )

    assert results[0].success is False
    assert results[0].error == "sandbox_capability_denied"


@pytest.mark.asyncio
async def test_sandbox_materializes_typed_pre_tool_hook_interception() -> None:
    class _McpServers:
        async def call_tool(self, **_kwargs):
            pytest.fail("intercepted read-only action must not reach the transport")

    sandbox = object.__new__(Sandbox)
    sandbox._sandbox_id = "sandbox-1"
    sandbox._env_type = SandboxEnvType.LOCAL
    sandbox._metadata = {}
    sandbox._mcpservers = _McpServers()
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": "cat README.md"},
        "tool_call_id": "call-read",
        "agent_name": "agent",
    }
    event_message = Message(
        category="tool_call",
        payload=[action],
        headers={
            "tool_interception": {
                "schema_version": "aworld.tool-interception/v1",
                "kind": "block",
                "tool_call_ids": ["call-read"],
                "error_code": "candidate_mutation_required",
                "content_type": "candidate_mutation_required",
                "message": "Create a candidate now.",
            }
        },
    )

    results = await sandbox.call_tool(
        action_list=[action],
        context=_sandbox_context(),
        event_message=event_message,
    )

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == "candidate_mutation_required"
    receipt = results[0].metadata["sandbox_observation"]
    assert receipt["effect"] == "blocked_read_only"
    assert receipt["workspace_mutated"] is False


@pytest.mark.asyncio
async def test_sandbox_routes_context_artifact_reads_before_remote_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _McpServers:
        async def call_tool(self, **_kwargs):
            pytest.fail("context-owned artifact must not reach remote transport")

    expected = ActionResult(
        success=True,
        tool_name="terminal",
        action_name="read_output_artifact",
        content={"content": "bounded evidence"},
    )
    monkeypatch.setattr(
        "aworld.core.context.tool_output_runtime.is_context_output_artifact_read",
        lambda _action: True,
    )
    monkeypatch.setattr(
        "aworld.core.context.tool_output_runtime.read_context_output_artifact",
        lambda _context, _action: expected,
    )
    sandbox = object.__new__(Sandbox)
    sandbox._sandbox_id = "sandbox-1"
    sandbox._env_type = SandboxEnvType.LOCAL
    sandbox._metadata = {}
    sandbox._mcpservers = _McpServers()

    (result,) = await sandbox.call_tool(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "read_output_artifact",
                "tool_call_id": "context-read",
                "params": {"artifact_ref": "aworld-tool-output://" + "a" * 64},
            }
        ],
        context=_sandbox_context(),
    )

    assert result is expected
@pytest.mark.asyncio
async def test_sandbox_failure_journals_completed_results_from_earlier_batch_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "tool-actions.journal.jsonl"
    monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(journal))

    class _McpServers:
        def __init__(self) -> None:
            self.calls = 0

        async def call_tool(self, **kwargs):
            self.calls += 1
            action = kwargs["action_list"][0]
            if self.calls == 2:
                raise RuntimeError("transport failed")
            return [
                ActionResult(
                    success=True,
                    tool_call_id=action["tool_call_id"],
                    tool_name=action["tool_name"],
                    action_name=action["action_name"],
                    content="FIRST",
                )
            ]

    sandbox = object.__new__(Sandbox)
    sandbox._sandbox_id = "sandbox-batch"
    sandbox._env_type = SandboxEnvType.LOCAL
    sandbox._metadata = {}
    sandbox._mcpservers = _McpServers()
    context = ApplicationContext.create(
        session_id="batch-session",
        task_id="batch-task",
        task_content="run batch",
    )
    actions = [
        {
            "tool_call_id": "call-1",
            "tool_name": "custom",
            "action_name": "run",
            "params": {"value": 1},
        },
        {
            "tool_call_id": "call-2",
            "tool_name": "custom",
            "action_name": "run",
            "params": {"value": 2},
        },
    ]

    with pytest.raises(RuntimeError, match="transport failed"):
        await sandbox.call_tool(action_list=actions, context=context)

    failed = read_tool_action_journal(journal).events[-1]
    assert failed["event_type"] == "sandbox_call_failed"
    assert failed["status"] == "failed"
    assert len(failed["results"]) == 1
    assert failed["results"][0]["tool_call_id"] == "call-1"
    assert failed["results"][0]["content"] == "FIRST"
