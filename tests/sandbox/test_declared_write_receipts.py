from __future__ import annotations

from copy import deepcopy
import asyncio
import gc
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
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
    DECLARED_WRITE_LOCK_ROOT_ENV,
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


def _process_lease_worker(
    lock_root: str,
    namespace: str,
    paths: tuple[str, ...],
    attempting,
    entered,
    release,
    outcome,
    timeout_seconds: float = 5.0,
) -> None:
    os.environ[DECLARED_WRITE_LOCK_ROOT_ENV] = lock_root

    async def run() -> None:
        attempting.set()
        try:
            async with overlapping_path_leases(
                namespace,
                paths,
                timeout_seconds=timeout_seconds,
            ):
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)
            outcome.put(("ok", ""))
        except BaseException as exc:  # pragma: no cover - asserted by parent.
            outcome.put((type(exc).__name__, str(exc)))

    asyncio.run(run())


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
    receipts = payload["metadata"]["terminal_execution_receipt"][
        DECLARED_PUBLIC_WRITE_RECEIPTS_KEY
    ]
    state = _record_progress(context, action, result)

    assert len(receipts) == 1
    assert receipts[0]["changed"] is (case != "unchanged")
    assert receipts[0]["exit_code"] == return_code
    assert receipts[0]["timed_out"] is timed_out
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


@pytest.mark.skipif(declared_write_module.fcntl is None, reason="POSIX flock required")
@pytest.mark.parametrize(
    "second_path",
    ["/workspace/result.json", "/workspace/result.json/child"],
)
def test_process_leases_serialize_same_and_ancestor_targets(
    tmp_path: Path,
    second_path: str,
) -> None:
    process_context = multiprocessing.get_context("fork")
    first_attempting = process_context.Event()
    first_entered = process_context.Event()
    first_release = process_context.Event()
    second_attempting = process_context.Event()
    second_entered = process_context.Event()
    second_release = process_context.Event()
    first_outcome = process_context.Queue()
    second_outcome = process_context.Queue()
    arguments = (str(tmp_path / "locks"), "terminal-host")
    first = process_context.Process(
        target=_process_lease_worker,
        args=(
            *arguments,
            ("/workspace/result.json",),
            first_attempting,
            first_entered,
            first_release,
            first_outcome,
        ),
    )
    second = process_context.Process(
        target=_process_lease_worker,
        args=(
            *arguments,
            (second_path,),
            second_attempting,
            second_entered,
            second_release,
            second_outcome,
        ),
    )
    first.start()
    assert first_attempting.wait(3)
    assert first_entered.wait(3)
    second.start()
    assert second_attempting.wait(3)
    assert not second_entered.wait(0.2)

    first_release.set()
    assert second_entered.wait(3)
    second_release.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert first_outcome.get(timeout=1) == ("ok", "")
    assert second_outcome.get(timeout=1) == ("ok", "")


@pytest.mark.skipif(declared_write_module.fcntl is None, reason="POSIX flock required")
def test_process_leases_allow_sibling_targets_concurrently(tmp_path: Path) -> None:
    process_context = multiprocessing.get_context("fork")
    first_attempting = process_context.Event()
    first_entered = process_context.Event()
    first_release = process_context.Event()
    second_attempting = process_context.Event()
    second_entered = process_context.Event()
    second_release = process_context.Event()
    first_outcome = process_context.Queue()
    second_outcome = process_context.Queue()
    arguments = (str(tmp_path / "locks"), "terminal-host")
    first = process_context.Process(
        target=_process_lease_worker,
        args=(
            *arguments,
            ("/workspace/first.json",),
            first_attempting,
            first_entered,
            first_release,
            first_outcome,
        ),
    )
    second = process_context.Process(
        target=_process_lease_worker,
        args=(
            *arguments,
            ("/workspace/second.json",),
            second_attempting,
            second_entered,
            second_release,
            second_outcome,
        ),
    )
    first.start()
    assert first_entered.wait(3)
    second.start()
    assert second_attempting.wait(3)
    assert second_entered.wait(3)

    first_release.set()
    second_release.set()
    first.join(5)
    second.join(5)

    assert first_outcome.get(timeout=1) == ("ok", "")
    assert second_outcome.get(timeout=1) == ("ok", "")


@pytest.mark.skipif(declared_write_module.fcntl is None, reason="POSIX flock required")
def test_process_death_releases_declared_write_lease(tmp_path: Path) -> None:
    process_context = multiprocessing.get_context("fork")
    first_attempting = process_context.Event()
    first_entered = process_context.Event()
    first_release = process_context.Event()
    first_outcome = process_context.Queue()
    root = str(tmp_path / "locks")
    first = process_context.Process(
        target=_process_lease_worker,
        args=(
            root,
            "terminal-host",
            ("/workspace/result.json",),
            first_attempting,
            first_entered,
            first_release,
            first_outcome,
        ),
    )
    first.start()
    assert first_entered.wait(3)
    first.terminate()
    first.join(5)
    assert not first.is_alive()

    second_attempting = process_context.Event()
    second_entered = process_context.Event()
    second_release = process_context.Event()
    second_release.set()
    second_outcome = process_context.Queue()
    second = process_context.Process(
        target=_process_lease_worker,
        args=(
            root,
            "terminal-host",
            ("/workspace/result.json",),
            second_attempting,
            second_entered,
            second_release,
            second_outcome,
        ),
    )
    second.start()
    assert second_entered.wait(3)
    second.join(5)
    assert second_outcome.get(timeout=1) == ("ok", "")


@pytest.mark.asyncio
async def test_cancelled_holder_releases_in_process_and_os_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DECLARED_WRITE_LOCK_ROOT_ENV, str(tmp_path / "locks"))
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DECLARED_WRITE_LOCK_ROOT_ENV, str(tmp_path / "locks"))
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
@pytest.mark.skipif(declared_write_module.fcntl is None, reason="POSIX flock required")
async def test_process_lease_default_root_is_private_and_lock_files_persist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DECLARED_WRITE_LOCK_ROOT_ENV, raising=False)
    monkeypatch.setattr(
        declared_write_module.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    async with overlapping_path_leases(
        "default-root",
        ["/workspace/result.json"],
    ):
        pass

    root = Path(declared_write_module._default_process_lock_root())
    assert root.is_dir()
    assert root.stat().st_mode & 0o777 == 0o700
    lock_files = list(root.glob("*.lock"))
    assert lock_files
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in lock_files)


@pytest.mark.asyncio
@pytest.mark.skipif(declared_write_module.fcntl is None, reason="POSIX flock required")
async def test_process_lease_rejects_symlinked_lock_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("unchanged", encoding="utf-8")
    root_lock = lock_root / declared_write_module._process_lock_name(
        "symlink-file",
        "/",
    )
    root_lock.symlink_to(victim)
    monkeypatch.setenv(DECLARED_WRITE_LOCK_ROOT_ENV, str(lock_root))

    with pytest.raises(
        declared_write_module.DeclaredWriteLeaseUnavailable,
        match="declared_write_lease_unavailable",
    ):
        async with overlapping_path_leases(
            "symlink-file",
            ["/workspace/result.json"],
            timeout_seconds=0.2,
        ):
            pytest.fail("symlinked lock file unexpectedly admitted")

    assert victim.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.asyncio
@pytest.mark.skipif(declared_write_module.fcntl is None, reason="POSIX flock required")
async def test_terminal_lock_timeout_fails_before_execution_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _public_context(tmp_path, task_id="lease-timeout")
    output = tmp_path / "result.json"
    lock_root = str(tmp_path / "locks")
    monkeypatch.setenv(DECLARED_WRITE_LOCK_ROOT_ENV, lock_root)
    process_context = multiprocessing.get_context("fork")
    attempting = process_context.Event()
    entered = process_context.Event()
    release = process_context.Event()
    outcome = process_context.Queue()
    holder = process_context.Process(
        target=_process_lease_worker,
        args=(
            lock_root,
            "terminal-host",
            (str(output),),
            attempting,
            entered,
            release,
            outcome,
        ),
    )
    holder.start()
    assert entered.wait(3)
    try:
        _action, _result, payload = await _execute_terminal_declared_write(
            monkeypatch,
            context,
            call_id="lease-timeout-call",
            timeout=0.05,
        )
    finally:
        release.set()
        holder.join(5)

    receipt = payload["metadata"]["terminal_execution_receipt"]
    assert payload["success"] is False
    assert payload["metadata"]["error_type"] == "declared_write_lease_timeout"
    assert receipt["executed"] is False
    assert DECLARED_PUBLIC_WRITE_RECEIPTS_KEY not in receipt
    assert not output.exists()
    assert outcome.get(timeout=1) == ("ok", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_root_kind", ["file", "symlink", "broad_mode"])
async def test_terminal_invalid_lock_root_fails_closed_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_root_kind: str,
) -> None:
    context = _public_context(tmp_path, task_id=f"lease-root-{invalid_root_kind}")
    output = tmp_path / "result.json"
    lock_root = tmp_path / "invalid-lock-root"
    if invalid_root_kind == "file":
        lock_root.write_text("not a directory", encoding="utf-8")
    elif invalid_root_kind == "symlink":
        real_root = tmp_path / "real-lock-root"
        real_root.mkdir(mode=0o700)
        lock_root.symlink_to(real_root, target_is_directory=True)
    else:
        lock_root.mkdir(mode=0o755)
        lock_root.chmod(0o755)
    monkeypatch.setenv(DECLARED_WRITE_LOCK_ROOT_ENV, str(lock_root))

    _action, _result, payload = await _execute_terminal_declared_write(
        monkeypatch,
        context,
        call_id=f"invalid-root-{invalid_root_kind}",
        timeout=0.2,
    )

    receipt = payload["metadata"]["terminal_execution_receipt"]
    assert payload["success"] is False
    assert payload["metadata"]["error_type"] == "declared_write_lease_unavailable"
    assert receipt["executed"] is False
    assert DECLARED_PUBLIC_WRITE_RECEIPTS_KEY not in receipt
    assert not output.exists()


@pytest.mark.asyncio
async def test_docker_provider_emits_same_declared_write_receipt_contract(
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
    assert receipt[DECLARED_PUBLIC_WRITE_RECEIPTS_KEY][0]["changed"] is True
    assert receipt[DECLARED_PUBLIC_WRITE_RECEIPTS_KEY][0]["target_id"] == (
        semantic_target_sha256("/workspace/result.json")
    )


@pytest.mark.asyncio
async def test_docker_invalid_lock_root_fails_before_command_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "declared-write-lock-failure")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    invalid_root = tmp_path / "lock-root-file"
    invalid_root.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv(DECLARED_WRITE_LOCK_ROOT_ENV, str(invalid_root))
    from aworld.sandbox.tool_servers.docker.src import server

    class LockFailureBridge:
        container = "declared-write-lock-failure"
        workdir = "/workspace"
        shell = "/bin/sh"
        allowed_directories = ["/workspace"]
        max_output_bytes = 4096
        executed = False

        @staticmethod
        def validate_path(value: str) -> str:
            return value

        async def shell_command(self, *_args, **_kwargs):
            self.executed = True
            return 0, b"", b"", False

    bridge = LockFailureBridge()
    monkeypatch.setattr(server, "bridge", bridge)

    response = await server.run_code(
        None,
        "printf x > /workspace/result.json",
        timeout=1,
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is False
    assert payload["metadata"]["error_type"] == "declared_write_lease_unavailable"
    assert receipt["executed"] is False
    assert DECLARED_PUBLIC_WRITE_RECEIPTS_KEY not in receipt
    assert bridge.executed is False


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
