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


def test_stdio_boundary_is_the_current_process_environment_and_cwd(tmp_path: Path) -> None:
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
    config = ToolConfigManager(mode="local", workspaces=[str(workspace)]).get_mcp_config(
        ["filesystem"]
    )

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
