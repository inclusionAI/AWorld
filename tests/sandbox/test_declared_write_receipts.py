from __future__ import annotations

from copy import deepcopy
import asyncio
import gc
import hashlib
import json
from pathlib import Path
import shlex
from types import SimpleNamespace
import weakref

import pytest

import aworld.sandbox.declared_write as declared_write_module
from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.runners.post_tool_progress import (
    capture_public_deliverable_baseline,
    record_semantic_tool_progress,
)
from aworld.sandbox.declared_write import (
    DECLARED_PUBLIC_WRITE_RECEIPTS_KEY,
    build_declared_public_write_contract,
    build_declared_public_write_receipt,
    declared_write_operation_sha256,
    framework_scope_sha256,
    framework_scope_values,
    overlapping_path_leases,
    semantic_target_sha256,
)
from aworld.sandbox.run.mcp_servers import McpServers
from aworld.sandbox.tool_observation import SandboxToolObservationRuntime
from aworld.sandbox.tool_servers.terminal.src import terminal as terminal_module
from aworld.sandbox.tool_servers.terminal.src.terminal import CommandResult


def _public_context(tmp_path: Path, *, task_id: str = "declared-write") -> Context:
    context = Context(
        task_id=task_id,
        session=Session(session_id="session"),
        workspace_path=str(tmp_path),
    )
    context.agent_info.current_agent_id = "agent"
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(tmp_path / "result.json"),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    return context


def _hidden_scope(
    context: Context,
    *,
    call_id: str,
    contract: dict[str, object] | None = None,
) -> dict[str, object]:
    lifecycle = context.context_lifecycle_state
    return {
        "task_id": context.task_id,
        "task_epoch": context.task_epoch,
        "session_id": context.session_id,
        "session_epoch": lifecycle.session_epoch,
        "branch_id": lifecycle.branch_id,
        "checkpoint_revision": lifecycle.checkpoint_revision,
        "agent_id": "agent",
        "prompt_namespace": "agent",
        "sandbox_id": "sandbox",
        "tool_call_id": call_id,
        "declared_public_write_contract": (
            contract or build_declared_public_write_contract(context)
        ),
    }


def _command_result(
    *,
    success: bool,
    return_code: int,
    timed_out: bool = False,
    stdout: str = "",
    background_process_requested: bool = False,
    background_output_detached: bool = False,
    capture_complete: bool = True,
    process_group_quiesced: bool | None = None,
) -> CommandResult:
    return CommandResult(
        command="opaque-writer",
        success=success,
        stdout=stdout,
        stderr="",
        return_code=return_code,
        duration="0:00:00.001000",
        timestamp="2026-10-09T12:00:00",
        timed_out=timed_out,
        background_process_requested=background_process_requested,
        background_output_detached=background_output_detached,
        capture_complete=capture_complete,
        process_group_quiesced=(
            not timed_out
            if process_group_quiesced is None
            else process_group_quiesced
        ),
    )


async def _execute_terminal_declared_write(
    monkeypatch: pytest.MonkeyPatch,
    context: Context,
    *,
    call_id: str,
    requested_path: Path | None = None,
    write_content: str | None = "candidate",
    success: bool = True,
    return_code: int = 0,
    timed_out: bool = False,
    stdout: str = "",
    include_declaration: bool = True,
    timeout: float = 10,
    result_overrides: dict[str, object] | None = None,
) -> tuple[ActionModel, ActionResult, dict[str, object]]:
    output = requested_path or Path(
        context.context_info["public_deliverable_contract"]["artifacts"][0]["path"]
    )

    async def execute(*_args, **_kwargs):
        if write_content is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(write_content, encoding="utf-8")
        return _command_result(
            success=success,
            return_code=return_code,
            timed_out=timed_out,
            stdout=stdout,
            **(result_overrides or {}),
        )

    monkeypatch.setattr(terminal_module, "workspace", Path(context.workspace_path))
    monkeypatch.setattr(terminal_module, "_execute_command_async", execute)
    params: dict[str, object] = {
        "code": "opaque-writer --dynamic-target",
        "cwd": context.workspace_path,
    }
    if include_declaration:
        params["declared_write_paths"] = [str(output)]
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id=call_id,
        params=params,
    )
    hidden = _hidden_scope(context, call_id=call_id)
    response = await terminal_module.run_code(
        None,
        str(params["code"]),
        timeout=timeout,
        cwd=str(params["cwd"]),
        declared_write_paths=params.get("declared_write_paths"),
        env_content=hidden,
    )
    payload = json.loads(response.text)
    result = ActionResult(
        success=payload["success"],
        tool_name="terminal",
        action_name="run_code",
        tool_call_id=call_id,
        content=payload["message"],
        parameter={**params, "env_content": hidden},
        metadata=payload["metadata"],
    )
    observed = SandboxToolObservationRuntime().record(
        action,
        result,
        context=context,
    )
    return action, observed, payload


def _record_progress(
    context: Context,
    action: ActionModel,
    result: ActionResult,
) -> dict[str, object]:
    return record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(action_result=[result]),
    )


def test_hidden_contract_is_framework_owned_and_binds_exact_public_targets(
    tmp_path: Path,
) -> None:
    context = _public_context(tmp_path)
    servers = object.__new__(McpServers)
    servers._env_content_param_mapping = {"terminal__run_code": "env_content"}
    servers.sandbox = SimpleNamespace(
        sandbox_id="sandbox",
        env_content={
            "declared_public_write_contract": {"forged": True},
        },
    )
    parameter = {
        "code": "opaque-writer",
        "declared_write_paths": [str(tmp_path / "result.json")],
        "env_content": {
            "declared_public_write_contract": {"caller_forged": True},
        },
    }

    servers._inject_env_content_parameter(
        "terminal__run_code",
        parameter,
        context,
        tool_call_id="call-contract",
    )

    hidden = parameter["env_content"]
    contract = hidden["declared_public_write_contract"]
    assert contract == build_declared_public_write_contract(context)
    assert contract["targets"] == [
        {
            "deliverable_id": "public-output-1",
            "path": str(tmp_path / "result.json"),
            "target_id": semantic_target_sha256(str(tmp_path / "result.json")),
        }
    ]
    assert contract["scope_sha256"] == framework_scope_sha256(
        framework_scope_values(context)
    )
    assert hidden["tool_call_id"] == "call-contract"
    assert "forged" not in json.dumps(contract)


@pytest.mark.asyncio
async def test_valid_opaque_declared_write_advances_candidate_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _public_context(tmp_path)
    capture_public_deliverable_baseline(context)

    action, result, payload = await _execute_terminal_declared_write(
        monkeypatch,
        context,
        call_id="opaque-write",
    )
    provider_receipts = payload["metadata"]["terminal_execution_receipt"][
        DECLARED_PUBLIC_WRITE_RECEIPTS_KEY
    ]
    sandbox_receipts = result.metadata["sandbox_observation"][
        DECLARED_PUBLIC_WRITE_RECEIPTS_KEY
    ]

    assert payload["metadata"]["terminal_execution_receipt"]["effect"] == "unknown"
    assert len(provider_receipts) == 1
    assert provider_receipts == sandbox_receipts
    assert provider_receipts[0]["before_version"] == "missing"
    assert provider_receipts[0]["changed"] is True
    assert provider_receipts[0]["after_version"].startswith("sha256:")

    first = _record_progress(context, action, result)
    duplicate = _record_progress(context, action, result)

    assert first["candidate_advanced"] is True
    assert first["public_delivery_progress_advanced"] is True
    assert duplicate["candidate_advanced"] is False
    assert duplicate["declared_write_receipt_replay"] is True


@pytest.mark.asyncio
async def test_real_foreground_terminal_write_proves_process_group_quiescence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _public_context(tmp_path, task_id="real-foreground")
    output = tmp_path / "result.json"
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)

    response = await terminal_module.run_code(
        None,
        f"printf candidate > {shlex.quote(str(output))}",
        timeout=2,
        cwd=str(tmp_path),
        declared_write_paths=[str(output)],
        env_content=_hidden_scope(context, call_id="real-foreground-call"),
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert payload["metadata"]["process_group_quiesced"] is True
    assert receipt[DECLARED_PUBLIC_WRITE_RECEIPTS_KEY][0]["changed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "write_content", "success", "return_code", "timed_out"),
    [
        ("unchanged", None, True, 0, False),
        ("nonzero", "candidate", False, 7, False),
        ("timed_out", "candidate", False, -1, True),
    ],
)
async def test_declared_write_failure_states_never_advance_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    write_content: str | None,
    success: bool,
    return_code: int,
    timed_out: bool,
) -> None:
    context = _public_context(tmp_path, task_id=f"declared-{case}")
    output = tmp_path / "result.json"
    if case == "unchanged":
        output.write_text("baseline", encoding="utf-8")
    capture_public_deliverable_baseline(context)

    action, result, payload = await _execute_terminal_declared_write(
        monkeypatch,
        context,
        call_id=f"call-{case}",
        write_content=write_content,
        success=success,
        return_code=return_code,
        timed_out=timed_out,
    )
    terminal_receipt = payload["metadata"]["terminal_execution_receipt"]
    state = _record_progress(context, action, result)

    if timed_out:
        assert DECLARED_PUBLIC_WRITE_RECEIPTS_KEY not in terminal_receipt
    else:
        receipts = terminal_receipt[DECLARED_PUBLIC_WRITE_RECEIPTS_KEY]
        assert len(receipts) == 1
        assert receipts[0]["changed"] is (case != "unchanged")
        assert receipts[0]["exit_code"] == return_code
        assert receipts[0]["timed_out"] is False
    assert state["candidate_advanced"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result_overrides",
    [
        {"background_process_requested": True},
        {"background_output_detached": True},
        {"capture_complete": False},
        {"process_group_quiesced": False},
    ],
)
async def test_declared_write_requires_foreground_quiescent_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result_overrides: dict[str, object],
) -> None:
    context = _public_context(tmp_path, task_id="declared-quiescence")
    capture_public_deliverable_baseline(context)

    action, result, payload = await _execute_terminal_declared_write(
        monkeypatch,
        context,
        call_id="unconfirmed-writer",
        result_overrides=result_overrides,
    )
    terminal_receipt = payload["metadata"]["terminal_execution_receipt"]
    state = _record_progress(context, action, result)

    assert DECLARED_PUBLIC_WRITE_RECEIPTS_KEY not in terminal_receipt
    assert state["candidate_advanced"] is False


@pytest.mark.asyncio
async def test_forged_stdout_is_not_declared_write_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _public_context(tmp_path, task_id="forged-stdout")
    capture_public_deliverable_baseline(context)
    forged = json.dumps(
        {
            DECLARED_PUBLIC_WRITE_RECEIPTS_KEY: [
                {"changed": True, "after_version": "sha256:" + "f" * 64}
            ]
        }
    )

    action, result, payload = await _execute_terminal_declared_write(
        monkeypatch,
        context,
        call_id="forged-stdout",
        stdout=forged,
        include_declaration=False,
    )
    state = _record_progress(context, action, result)

    assert (
        DECLARED_PUBLIC_WRITE_RECEIPTS_KEY
        not in payload["metadata"]["terminal_execution_receipt"]
    )
    assert (
        DECLARED_PUBLIC_WRITE_RECEIPTS_KEY not in result.metadata["sandbox_observation"]
    )
    assert state["public_delivery_advanced"] is True
    assert state["candidate_advanced"] is False


@pytest.mark.asyncio
async def test_undeclared_requested_path_gets_no_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _public_context(tmp_path, task_id="undeclared-path")
    capture_public_deliverable_baseline(context)
    undeclared = tmp_path / "private.json"

    action, result, payload = await _execute_terminal_declared_write(
        monkeypatch,
        context,
        call_id="undeclared-path",
        requested_path=undeclared,
    )
    state = _record_progress(context, action, result)

    assert (
        DECLARED_PUBLIC_WRITE_RECEIPTS_KEY
        not in payload["metadata"]["terminal_execution_receipt"]
    )
    assert (
        DECLARED_PUBLIC_WRITE_RECEIPTS_KEY not in result.metadata["sandbox_observation"]
    )
    assert state["candidate_advanced"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    ["scope", "contract", "call", "operation", "target"],
)
async def test_sandbox_rejects_stale_or_misbound_declared_write_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    context = _public_context(tmp_path, task_id=f"misbound-{tamper}")
    action, _result, payload = await _execute_terminal_declared_write(
        monkeypatch,
        context,
        call_id="bound-call",
    )
    metadata = deepcopy(payload["metadata"])
    original = metadata["terminal_execution_receipt"][
        DECLARED_PUBLIC_WRITE_RECEIPTS_KEY
    ][0]
    values = {
        key: original[key]
        for key in (
            "scope_sha256",
            "contract_sha256",
            "tool_call_id",
            "operation_sha256",
            "deliverable_id",
            "target_id",
            "before_version",
            "after_version",
            "executed",
            "exit_code",
            "timed_out",
        )
    }
    replacement = "sha256:" + "a" * 64
    field = {
        "scope": "scope_sha256",
        "contract": "contract_sha256",
        "call": "tool_call_id",
        "operation": "operation_sha256",
        "target": "target_id",
    }[tamper]
    values[field] = "stale-call" if field == "tool_call_id" else replacement
    metadata["terminal_execution_receipt"][DECLARED_PUBLIC_WRITE_RECEIPTS_KEY] = [
        build_declared_public_write_receipt(**values)
    ]
    result = ActionResult(
        success=True,
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="bound-call",
        content=payload["message"],
        parameter={
            **action.params,
            "env_content": _hidden_scope(context, call_id="bound-call"),
        },
        metadata=metadata,
    )

    observed = SandboxToolObservationRuntime().record(
        action,
        result,
        context=context,
    )

    assert (
        DECLARED_PUBLIC_WRITE_RECEIPTS_KEY
        not in observed.metadata["sandbox_observation"]
    )


@pytest.mark.asyncio
async def test_changed_receipt_hash_must_match_current_public_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _public_context(tmp_path, task_id="post-version-mismatch")
    capture_public_deliverable_baseline(context)
    action, result, _payload = await _execute_terminal_declared_write(
        monkeypatch,
        context,
        call_id="hash-mismatch",
        write_content="version-a",
    )
    (tmp_path / "result.json").write_text("version-b", encoding="utf-8")

    state = _record_progress(context, action, result)

    assert state["public_delivery_advanced"] is True
    assert state["declared_write_version_match"] is False
    assert state["candidate_advanced"] is False


def test_declared_write_operation_binds_exact_requested_paths(tmp_path: Path) -> None:
    first = declared_write_operation_sha256(
        code="opaque-writer",
        language="shell",
        cwd=str(tmp_path),
        declared_write_paths=[str(tmp_path / "result.json")],
    )
    changed = declared_write_operation_sha256(
        code="opaque-writer",
        language="shell",
        cwd=str(tmp_path),
        declared_write_paths=[str(tmp_path / "other.json")],
    )

    assert first.startswith("sha256:")
    assert first != changed


def test_path_lease_registry_releases_closed_contended_event_loops() -> None:
    loop_refs: list[weakref.ReferenceType[asyncio.AbstractEventLoop]] = []
    manager_refs: list[weakref.ReferenceType[object]] = []

    async def contend_once() -> tuple[
        weakref.ReferenceType[asyncio.AbstractEventLoop],
        weakref.ReferenceType[object],
    ]:
        loop = asyncio.get_running_loop()
        manager = declared_write_module._lease_manager()
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()

        async def holder() -> None:
            async with overlapping_path_leases("gc-regression", ["/workspace/a"]):
                holder_entered.set()
                await release_holder.wait()

        async def waiter() -> None:
            async with overlapping_path_leases("gc-regression", ["/workspace/a"]):
                return

        holder_task = asyncio.create_task(holder())
        await holder_entered.wait()
        successful_waiter = asyncio.create_task(waiter())
        cancelled_waiter = asyncio.create_task(waiter())
        for _ in range(100):
            if len(manager._waiting) == 2:
                break
            await asyncio.sleep(0)
        assert len(manager._waiting) == 2
        assert manager._condition._loop is loop

        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter
        assert len(manager._waiting) == 1

        release_holder.set()
        await asyncio.gather(holder_task, successful_waiter)
        assert manager._waiting == []
        assert manager._active == {}
        return weakref.ref(loop), weakref.ref(manager)

    for _ in range(3):
        loop_ref, manager_ref = asyncio.run(contend_once())
        loop_refs.append(loop_ref)
        manager_refs.append(manager_ref)

    for _ in range(3):
        gc.collect()

    assert all(reference() is None for reference in manager_refs)
    assert all(reference() is None for reference in loop_refs)
    assert len(declared_write_module._LEASE_MANAGERS) == 0


@pytest.mark.asyncio
async def test_cancelled_holder_releases_in_process_lease() -> None:
    entered = asyncio.Event()

    async def holder() -> None:
        async with overlapping_path_leases(
            "cancel-release",
            ["/workspace/result.json"],
        ):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with overlapping_path_leases(
        "cancel-release",
        ["/workspace/result.json"],
        timeout_seconds=0.2,
    ):
        pass


@pytest.mark.asyncio
async def test_in_process_lease_wait_is_bounded(
) -> None:
    manager = declared_write_module._lease_manager()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with overlapping_path_leases(
            "bounded-local-wait",
            ["/workspace/result.json"],
        ):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    try:
        with pytest.raises(
            declared_write_module.DeclaredWriteLeaseUnavailable,
            match="declared_write_lease_timeout",
        ):
            async with overlapping_path_leases(
                "bounded-local-wait",
                ["/workspace/result.json"],
                timeout_seconds=0.05,
            ):
                pytest.fail("contended lease unexpectedly entered")
        assert manager._waiting == []
        assert len(manager._active) == 1
    finally:
        release.set()
        await task
    assert manager._active == {}


@pytest.mark.asyncio
async def test_docker_provider_omits_declared_receipt_without_quiescence_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "declared-write")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    from aworld.sandbox.tool_servers.docker.src import server

    class DeclaredWriteBridge:
        container = "declared-write"
        workdir = "/workspace"
        shell = "/bin/sh"
        allowed_directories = ["/workspace"]
        max_output_bytes = 4096

        def __init__(self) -> None:
            self.content = b"before"

        def validate_path(self, value: str) -> str:
            assert value == "/workspace/result.json"
            return value

        async def shell_command(self, _code: str, **_kwargs):
            self.content = b"after"
            return 0, b"opaque output", b"", False

        @staticmethod
        def bound_output(value, *, label):
            del label
            return value, {
                "output_truncated": False,
                "raw_bytes": len(value),
                "inline_bytes": len(value),
                "offloaded_bytes": 0,
                "content_sha256": hashlib.sha256(value).hexdigest(),
                "truncation_strategy": "none",
                "artifact_ref": None,
                "head_bytes": len(value),
                "tail_bytes": 0,
            }

        @staticmethod
        def decode_inline_text(value, _policy):
            return value.decode()

    bridge = DeclaredWriteBridge()
    monkeypatch.setattr(server, "bridge", bridge)

    async def versions(targets, *, timeout):
        del timeout
        return (
            {
                target["target_id"]: "sha256:"
                + hashlib.sha256(bridge.content).hexdigest()
                for target in targets
            },
            "sha256:" + "d" * 64,
        )

    monkeypatch.setattr(server, "_container_declared_write_versions", versions)
    scope_values = (
        "docker-task",
        "0",
        "session",
        "0",
        "main",
        "0",
        "agent",
        "agent",
    )
    contract_context = SimpleNamespace(
        task_id="docker-task",
        task_epoch=0,
        session_id="session",
        workspace_path="/workspace",
        agent_info=SimpleNamespace(current_agent_id="agent"),
        context_lifecycle_state=SimpleNamespace(
            session_id="session",
            session_epoch=0,
            task_epoch=0,
            branch_id="main",
            checkpoint_revision=0,
        ),
        context_info={
            "public_deliverable_contract": {
                "schema_version": "aworld.public-deliverables/v1",
                "authority": "public_task_advisory",
                "source": "public_task_text",
                "artifacts": [
                    {
                        "deliverable_id": "docker-output",
                        "path": "/workspace/result.json",
                        "display_path": "result.json",
                        "kind": "file",
                        "authority": "public_task_advisory",
                    }
                ],
            }
        },
    )
    contract = build_declared_public_write_contract(contract_context)
    assert contract["scope_sha256"] == framework_scope_sha256(scope_values)
    env_content = {
        "task_id": "docker-task",
        "task_epoch": 0,
        "session_id": "session",
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent",
        "prompt_namespace": "agent",
        "sandbox_id": "sandbox",
        "tool_call_id": "docker-call",
        "declared_public_write_contract": contract,
    }

    response = await server.run_code(
        None,
        "opaque-writer --dynamic-target",
        declared_write_paths=["/workspace/result.json"],
        env_content=env_content,
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert receipt["effect"] == "unknown"
    assert DECLARED_PUBLIC_WRITE_RECEIPTS_KEY not in receipt


@pytest.mark.asyncio
async def test_docker_overlapping_known_write_snapshots_are_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "declared-write-race")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    from aworld.sandbox.tool_servers.docker.src import server

    class RaceBridge:
        container = "declared-write-race"
        workdir = "/workspace"
        shell = "/bin/sh"
        allowed_directories = ["/workspace"]
        max_output_bytes = 4096

        def __init__(self) -> None:
            self.state = "missing"

        def validate_path(self, value: str) -> str:
            normalized = value.replace("//", "/")
            assert normalized == "/workspace/result.txt"
            return normalized

        async def shell_command(self, code: str, **_kwargs):
            if "first" in code:
                await asyncio.sleep(0.05)
                self.state = "first"
            else:
                await asyncio.sleep(0.1)
            return 0, b"", b"", False

        @staticmethod
        def bound_output(value, *, label):
            del label
            return value, {
                "output_truncated": False,
                "raw_bytes": len(value),
                "inline_bytes": len(value),
                "offloaded_bytes": 0,
                "content_sha256": hashlib.sha256(value).hexdigest(),
                "truncation_strategy": "none",
                "artifact_ref": None,
                "head_bytes": len(value),
                "tail_bytes": 0,
            }

        @staticmethod
        def decode_inline_text(value, _policy):
            return value.decode()

    bridge = RaceBridge()
    monkeypatch.setattr(server, "bridge", bridge)

    async def states(paths, *, timeout):
        del timeout
        if not paths:
            return None
        return tuple(bridge.state for _path in paths)

    monkeypatch.setattr(server, "_container_path_states", states)
    first, second = await asyncio.gather(
        server.run_code(None, "printf first > result.txt"),
        server.run_code(None, "printf second > result.txt"),
    )
    first_receipt = json.loads(first.text)["metadata"]["terminal_execution_receipt"]
    second_receipt = json.loads(second.text)["metadata"]["terminal_execution_receipt"]

    assert first_receipt["mutation_observed"] is True
    assert second_receipt["mutation_observed"] is False
