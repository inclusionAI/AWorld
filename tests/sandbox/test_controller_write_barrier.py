from __future__ import annotations

import asyncio
from concurrent.futures import Future as ConcurrentFuture
from pathlib import Path
import shlex
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from aworld.core.common import ActionResult
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.sandbox import declared_write as declared_write_module
from aworld.sandbox.base import BaseSandbox
from aworld.sandbox.declared_write import (
    ControllerWriteBarrierTimeout,
    controller_public_write_barrier,
)
from aworld.sandbox.run import mcp_servers as mcp_servers_module
from aworld.sandbox.runtime import manager as sandbox_manager_module
from aworld.sandbox.runtime.loop_pool import SandboxLoopPool
from aworld.sandbox.task_budget import ToolLeaseDecision
from aworld.sandbox.tool_observation import SandboxToolObservationRuntime
from aworld.sandbox.tool_servers.terminal.src.terminal import _execute_command_async


def _public_context(tmp_path: Path, *, task_id: str = "controller-barrier") -> Context:
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
                "deliverable_id": "result",
                "path": str(tmp_path / "result.json"),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    return context


class _Observations(SandboxToolObservationRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.recorded_while_barrier_held = False

    def lookup(self, *_args, **_kwargs):
        return None

    def current_generation(self, _context) -> int:
        return 0

    def record(self, _action, result, *, context):
        del context
        manager = declared_write_module._CONTROLLER_WRITE_BARRIERS
        with manager._lock:
            self.recorded_while_barrier_held = bool(manager._active)
        return result


class _Provider:
    def __init__(
        self,
        *,
        entered: asyncio.Event,
        release: asyncio.Event,
        timeout: float = 1.0,
    ) -> None:
        self.entered = entered
        self.release = release
        self.timeout = timeout
        self.received_lease = False

    def controller_write_barrier_timeout(self, *_args, **_kwargs) -> float:
        return self.timeout

    async def call_tool(self, *, action_list, controller_write_lease, **_kwargs):
        self.received_lease = controller_write_lease is not None
        self.entered.set()
        await self.release.wait()
        action = action_list[0]
        return [
            ActionResult(
                success=True,
                tool_name=action["tool_name"],
                action_name=action["action_name"],
                tool_call_id=action.get("tool_call_id"),
                content="ok",
                parameter=action.get("params") or {},
            )
        ]


class _ControllerSandbox:
    call_tool = BaseSandbox.call_tool
    _sandbox_tool_observations = BaseSandbox._sandbox_tool_observations
    _tool_execution_boundaries = BaseSandbox._tool_execution_boundaries

    def __init__(self, name: str, provider: _Provider) -> None:
        self.sandbox_id = name
        self.env_type = "local"
        self.mcpservers = provider
        self._tool_observation_runtime = _Observations()


@pytest.mark.asyncio
@pytest.mark.parametrize("separate_sandboxes", [False, True])
async def test_parent_barrier_serializes_undeclared_cross_provider_calls(
    tmp_path: Path,
    separate_sandboxes: bool,
) -> None:
    context = _public_context(tmp_path)
    first_entered = asyncio.Event()
    first_release = asyncio.Event()
    second_entered = asyncio.Event()
    second_release = asyncio.Event()
    first_provider = _Provider(entered=first_entered, release=first_release)
    second_provider = _Provider(entered=second_entered, release=second_release)
    first_sandbox = _ControllerSandbox("first", first_provider)
    second_sandbox = (
        _ControllerSandbox("second", second_provider)
        if separate_sandboxes
        else first_sandbox
    )
    if not separate_sandboxes:
        first_provider.release = first_release

    first_action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "tool_call_id": "opaque-declared-window",
        "params": {"code": "opaque-writer --dynamic-target"},
    }
    second_action = {
        "tool_name": "filesystem",
        "action_name": "write_file",
        "tool_call_id": "undeclared-foreign-writer",
        "params": {"path": str(tmp_path / "result.json"), "content": "foreign"},
    }

    first_task = asyncio.create_task(
        first_sandbox.call_tool(action_list=[first_action], context=context)
    )
    await first_entered.wait()
    if not separate_sandboxes:
        first_sandbox.mcpservers = second_provider
    second_task = asyncio.create_task(
        second_sandbox.call_tool(action_list=[second_action], context=context)
    )
    await asyncio.sleep(0.05)
    assert not second_entered.is_set()

    first_release.set()
    await first_task
    await asyncio.wait_for(second_entered.wait(), timeout=1)
    second_release.set()
    await second_task

    assert first_provider.received_lease is True
    assert second_provider.received_lease is True
    assert first_sandbox._tool_observation_runtime.recorded_while_barrier_held is True


@pytest.mark.asyncio
async def test_timed_out_live_provider_retains_barrier_until_actual_quiescence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _public_context(tmp_path, task_id="retained-provider")
    release = asyncio.Event()

    async def cancellation_resistant_provider() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    retained: set[asyncio.Future[object]] = set()

    def retain_without_forcing_quiescence(
        task: asyncio.Future[object], **_kwargs
    ) -> None:
        retained.add(task)
        task.add_done_callback(retained.discard)

    monkeypatch.setattr(
        mcp_servers_module,
        "_retain_cancelled_provider_call",
        retain_without_forcing_quiescence,
    )

    decision = ToolLeaseDecision(
        requested_seconds=0.01,
        policy_seconds=0.01,
        effective_seconds=0.01,
        remaining_task_seconds=1.0,
        limited_by="task_lease",
    )
    async with controller_public_write_barrier(
        context, timeout_seconds=0.2
    ) as lease:
        with pytest.raises(mcp_servers_module._TaskToolLeaseTimeout):
            await mcp_servers_module._await_with_task_lease(
                cancellation_resistant_provider(),
                decision,
                controller_write_lease=lease,
            )

    with pytest.raises(ControllerWriteBarrierTimeout):
        async with controller_public_write_barrier(
            context, timeout_seconds=0.05
        ):
            pytest.fail("barrier opened while timed-out provider was still live")

    release.set()
    for _ in range(100):
        manager = declared_write_module._CONTROLLER_WRITE_BARRIERS
        with manager._lock:
            if not manager._active:
                break
        await asyncio.sleep(0.01)
    async with controller_public_write_barrier(context, timeout_seconds=0.2):
        pass


@pytest.mark.asyncio
async def test_parent_barrier_wait_returns_typed_failure_without_dispatch(
    tmp_path: Path,
) -> None:
    context = _public_context(tmp_path, task_id="barrier-budget")
    holder_entered = asyncio.Event()
    holder_release = asyncio.Event()

    async def holder() -> None:
        async with controller_public_write_barrier(
            context, timeout_seconds=0.2
        ):
            holder_entered.set()
            await holder_release.wait()

    holder_task = asyncio.create_task(holder())
    await holder_entered.wait()
    provider_entered = asyncio.Event()
    provider = _Provider(
        entered=provider_entered,
        release=asyncio.Event(),
        timeout=0.03,
    )
    sandbox = _ControllerSandbox("bounded-wait", provider)

    result = await sandbox.call_tool(
        action_list=[
            {
                "tool_name": "filesystem",
                "action_name": "write_file",
                "tool_call_id": "blocked-call",
                "params": {"path": str(tmp_path / "result.json"), "content": "x"},
            }
        ],
        context=context,
    )

    assert result[0].success is False
    assert result[0].error == "controller_write_barrier_timeout"
    assert result[0].metadata["failure_code"] == "controller_write_barrier_timeout"
    assert not provider_entered.is_set()
    holder_release.set()
    await holder_task


@pytest.mark.asyncio
async def test_reuse_worker_cancellation_retains_barrier_until_job_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _public_context(tmp_path, task_id="reuse-cancel")
    queue: asyncio.Queue = asyncio.Queue()
    provider_entered = asyncio.Event()
    provider_release = asyncio.Event()

    async def worker() -> None:
        while True:
            function, args, kwargs, future = await queue.get()
            try:
                result = await function(*args, **kwargs)
            except BaseException as exc:
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)
            finally:
                queue.task_done()

    worker_task = asyncio.create_task(worker())
    runtime_context = sandbox_manager_module._SandboxContext(
        loop=asyncio.get_running_loop(),
        queue=queue,
        worker_task=worker_task,
    )
    manager = sandbox_manager_module.SandboxManager()

    async def existing_context(*_args, **_kwargs):
        return runtime_context

    monkeypatch.setattr(manager, "_ensure_context", existing_context)

    async def provider_job() -> None:
        provider_entered.set()
        await provider_release.wait()

    try:
        async with controller_public_write_barrier(
            context, timeout_seconds=0.2
        ) as lease:
            caller = asyncio.create_task(
                manager.run_on_sandbox(
                    "reuse-sandbox",
                    provider_job,
                    _controller_barrier_lease=lease,
                )
            )
            await provider_entered.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            manager_state = declared_write_module._CONTROLLER_WRITE_BARRIERS
            with manager_state._lock:
                retained = tuple(
                    task
                    for state in manager_state._active.values()
                    for task in state.retained_tasks
                )
            assert retained
            assert all(isinstance(task, ConcurrentFuture) for task in retained)

        with pytest.raises(ControllerWriteBarrierTimeout):
            async with controller_public_write_barrier(
                context, timeout_seconds=0.03
            ):
                pytest.fail("reuse job released target barrier before completion")

        provider_release.set()
        await asyncio.wait_for(queue.join(), timeout=1)
        for _ in range(50):
            with declared_write_module._CONTROLLER_WRITE_BARRIERS._lock:
                if not declared_write_module._CONTROLLER_WRITE_BARRIERS._active:
                    break
            await asyncio.sleep(0.01)
        async with controller_public_write_barrier(context, timeout_seconds=0.2):
            pass
    finally:
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_multi_action_batch_aborts_after_first_provider_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers = mcp_servers_module.McpServers(
        mcp_servers=["terminal"],
        mcp_config={
            "mcpServers": {
                "terminal": {"type": "stdio", "command": "unused"}
            }
        },
        sandbox=SimpleNamespace(sandbox_id=None, reuse=False),
    )
    servers.tool_list = [
        {
            "type": "function",
            "function": {
                "name": "terminal__run_code",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    provider_calls = 0

    async def provider_call(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return None

    async def timeout_first(awaitable, _decision, **_kwargs):
        awaitable.close()
        raise mcp_servers_module._TaskToolLeaseTimeout(timed_out=True)

    async def accept_parameters(**_kwargs):
        return None

    monkeypatch.setattr(
        mcp_servers_module, "call_mcp_tool_with_exit_stack", provider_call
    )
    monkeypatch.setattr(mcp_servers_module, "_await_with_task_lease", timeout_first)
    monkeypatch.setattr(servers, "check_tool_params", accept_parameters)

    results = await servers._call_tool_impl(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "tool_call_id": "first",
                "params": {"code": "first"},
            },
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "tool_call_id": "second",
                "params": {"code": "second"},
            },
        ]
    )

    assert provider_calls == 0  # coroutine bodies never started by the fake waiter
    assert len(results) == 2
    assert results[0].error == "task_tool_lease_timeout"
    assert results[1].metadata == {
        "failure_category": "infrastructure",
        "failure_code": "provider_batch_aborted_after_timeout",
    }


def test_controller_barrier_leaves_no_files_or_event_loop_state(tmp_path: Path) -> None:
    context = _public_context(tmp_path, task_id="loop-cleanup")

    async def enter_once() -> None:
        async with controller_public_write_barrier(context, timeout_seconds=0.2):
            pass

    for _ in range(3):
        asyncio.run(enter_once())

    manager = declared_write_module._CONTROLLER_WRITE_BARRIERS
    with manager._lock:
        assert manager._active == {}
        assert manager._waiting == []
    assert list(tmp_path.iterdir()) == []
    assert not hasattr(declared_write_module, "_process_lock_root")


def test_controller_barrier_serializes_across_event_loop_threads(tmp_path: Path) -> None:
    first_context = _public_context(tmp_path, task_id="thread-one")
    second_context = _public_context(tmp_path, task_id="thread-two")
    first_entered = threading.Event()
    first_release = threading.Event()
    second_entered = threading.Event()
    errors: list[BaseException] = []

    def run_first() -> None:
        async def hold() -> None:
            async with controller_public_write_barrier(
                first_context, timeout_seconds=1
            ):
                first_entered.set()
                while not first_release.is_set():
                    await asyncio.sleep(0.01)

        try:
            asyncio.run(hold())
        except BaseException as exc:  # pragma: no cover - asserted below.
            errors.append(exc)

    def run_second() -> None:
        async def enter() -> None:
            async with controller_public_write_barrier(
                second_context, timeout_seconds=1
            ):
                second_entered.set()

        try:
            asyncio.run(enter())
        except BaseException as exc:  # pragma: no cover - asserted below.
            errors.append(exc)

    first = threading.Thread(target=run_first)
    second = threading.Thread(target=run_second)
    first.start()
    assert first_entered.wait(1)
    second.start()
    time.sleep(0.05)
    assert not second_entered.is_set()
    first_release.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert second_entered.is_set()
    assert errors == []
    manager = declared_write_module._CONTROLLER_WRITE_BARRIERS
    with manager._lock:
        assert manager._active == {}
        assert manager._waiting == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse", [False, True])
async def test_mcpservers_reuse_and_nonreuse_dispatch_receive_parent_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reuse: bool,
) -> None:
    context = _public_context(tmp_path, task_id=f"dispatch-{reuse}")
    sandbox = SimpleNamespace(sandbox_id=f"sandbox-{reuse}", reuse=reuse)
    servers = mcp_servers_module.McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {"type": "stdio"}}},
        sandbox=sandbox,
    )
    servers.tool_list = []
    received: list[object] = []

    async def fake_call_tool_impl(*args, **kwargs):
        received.append(
            kwargs.get("controller_write_lease")
            if "controller_write_lease" in kwargs
            else args[-1]
        )
        return [
            ActionResult(
                success=True,
                tool_name="terminal",
                action_name="run_code",
                content="ok",
            )
        ]

    monkeypatch.setattr(servers, "_call_tool_impl", fake_call_tool_impl)

    class ImmediateManager:
        async def run_on_sandbox(
            self,
            _sandbox_id,
            function,
            *args,
            server_name=None,
            _controller_barrier_lease=None,
            **kwargs,
        ):
            del server_name, _controller_barrier_lease
            return await function(*args, **kwargs)

    if reuse:
        manager = ImmediateManager()
        monkeypatch.setattr(
            mcp_servers_module.SandboxManager,
            "get_instance",
            classmethod(lambda _cls: manager),
        )

    results = await servers.call_tool(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "tool_call_id": "dispatch-call",
                "params": {"code": "true"},
            }
        ],
        context=context,
    )

    assert results[0].success is True
    assert len(received) == 1
    assert received[0] is not None


@pytest.mark.asyncio
async def test_reuse_cross_server_batch_stops_after_first_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _public_context(tmp_path, task_id="cross-server-timeout")
    servers = mcp_servers_module.McpServers(
        mcp_servers=["terminal", "filesystem"],
        mcp_config={"mcpServers": {}},
        sandbox=SimpleNamespace(sandbox_id="reuse-cross-server", reuse=True),
    )
    servers.tool_list = []
    started_servers: list[str] = []

    async def fake_impl(action_list, *_args, **_kwargs):
        server = action_list[0]["tool_name"]
        started_servers.append(server)
        if server == "terminal":
            return [
                ActionResult(
                    success=False,
                    tool_name=server,
                    action_name="run_code",
                    error="task_tool_lease_timeout",
                    metadata={"failure_type": "task_tool_lease_timeout"},
                )
            ]
        return [ActionResult(success=True, tool_name=server, action_name="write_file")]

    monkeypatch.setattr(servers, "_call_tool_impl", fake_impl)

    class ImmediateManager:
        async def run_on_sandbox(
            self,
            _sandbox_id,
            function,
            *args,
            server_name=None,
            _controller_barrier_lease=None,
            **kwargs,
        ):
            del server_name, _controller_barrier_lease
            return await function(*args, **kwargs)

    immediate = ImmediateManager()
    monkeypatch.setattr(
        mcp_servers_module.SandboxManager,
        "get_instance",
        classmethod(lambda _cls: immediate),
    )

    results = await servers.call_tool(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": "first"},
            },
            {
                "tool_name": "filesystem",
                "action_name": "write_file",
                "params": {"path": str(tmp_path / "result.json"), "content": "x"},
            },
        ],
        context=context,
    )

    assert started_servers == ["terminal"]
    assert len(results) == 2
    assert results[1].metadata["failure_code"] == (
        "provider_batch_aborted_after_timeout"
    )


@pytest.mark.asyncio
async def test_repeated_provider_cancel_cannot_interrupt_process_group_cleanup(
    tmp_path: Path,
) -> None:
    context = _public_context(tmp_path, task_id="repeated-cancel")
    marker = tmp_path / "late.txt"
    child = (
        "import pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.7); "
        f"pathlib.Path({str(marker)!r}).write_text('late')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(60)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(parent)}"
    decision = ToolLeaseDecision(
        requested_seconds=0.1,
        policy_seconds=0.1,
        effective_seconds=0.1,
        remaining_task_seconds=1.0,
        limited_by="task_lease",
    )

    async with controller_public_write_barrier(
        context, timeout_seconds=0.2
    ) as lease:
        with pytest.raises(mcp_servers_module._TaskToolLeaseTimeout):
            await mcp_servers_module._await_with_task_lease(
                _execute_command_async(
                    command,
                    timeout=5,
                    cwd=tmp_path,
                    quiesce_process_group=True,
                ),
                decision,
                controller_write_lease=lease,
            )

    with pytest.raises(ControllerWriteBarrierTimeout):
        async with controller_public_write_barrier(
            context, timeout_seconds=0.03
        ):
            pytest.fail("barrier released before process-group cleanup")
    await asyncio.sleep(0.8)
    assert not marker.exists()
    async with controller_public_write_barrier(context, timeout_seconds=0.5):
        pass


@pytest.mark.asyncio
async def test_cleanup_all_completes_retained_reuse_future(tmp_path: Path) -> None:
    context = _public_context(tmp_path, task_id="cleanup-retained")
    pool = SandboxLoopPool(num_loops=1)
    manager = sandbox_manager_module.SandboxManager()
    manager._loop_pool = pool
    started = threading.Event()
    finished = threading.Event()

    async def provider_job() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    try:
        async with controller_public_write_barrier(
            context, timeout_seconds=0.5
        ) as lease:
            caller = asyncio.create_task(
                manager.run_on_sandbox(
                    "cleanup-retained-sandbox",
                    provider_job,
                    _controller_barrier_lease=lease,
                )
            )
            assert await asyncio.to_thread(started.wait, 1)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller

        await manager.cleanup_all()
        assert finished.wait(1)
        async with controller_public_write_barrier(
            context, timeout_seconds=0.5
        ):
            pass
    finally:
        if pool._loops:
            await asyncio.to_thread(pool.shutdown)
