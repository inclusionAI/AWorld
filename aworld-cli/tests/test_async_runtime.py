from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from aworld_cli.async_runtime import (
    BOUNDED_ASYNC_SHUTDOWN_ENV,
    TASK_COMPLETION_RESERVE_ENV,
    TASK_DEADLINE_EPOCH_ENV,
    DirectRunDeadlineExceeded,
    hard_exit_direct_run_if_configured,
    run_direct_async,
)


def test_direct_async_keeps_standard_asyncio_run_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(BOUNDED_ASYNC_SHUTDOWN_ENV, raising=False)

    assert run_direct_async(asyncio.sleep(0, result="complete")) == "complete"


def test_direct_async_returns_at_caller_deadline_without_waiting_for_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BOUNDED_ASYNC_SHUTDOWN_ENV, "0.02")
    monkeypatch.setenv(TASK_DEADLINE_EPOCH_ENV, str(time.time() + 0.05))
    monkeypatch.setenv(TASK_COMPLETION_RESERVE_ENV, "0")

    async def stubborn_provider() -> None:
        await asyncio.Event().wait()

    started = time.monotonic()
    with pytest.raises(DirectRunDeadlineExceeded):
        run_direct_async(stubborn_provider())
    assert time.monotonic() - started < 1


def test_bounded_shutdown_exits_process_with_stubborn_provider_task() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import asyncio
from aworld_cli.async_runtime import run_direct_async

async def stubborn_provider():
    while True:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            continue

async def direct_run():
    asyncio.create_task(stubborn_provider())
    await asyncio.sleep(0)
    return "deadline returned to main"

print(run_direct_async(direct_run()), flush=True)
print("direct-run process exited", flush=True)
"""
    env = os.environ.copy()
    env[BOUNDED_ASYNC_SHUTDOWN_ENV] = "0.02"
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(source_root), env.get("PYTHONPATH")))
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        timeout=1,
        check=False,
    )

    assert completed.returncode == 0
    assert "deadline returned to main" in completed.stdout
    assert "direct-run process exited" in completed.stdout
    assert "closing the event loop" in completed.stderr


def test_one_shot_hard_exit_does_not_wait_for_stubborn_executor_thread() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import asyncio
import threading
from aworld_cli.async_runtime import hard_exit_direct_run_if_configured, run_direct_async

thread_started = threading.Event()
thread_release = threading.Event()

def stubborn_provider_thread():
    thread_started.set()
    thread_release.wait()

async def direct_run():
    asyncio.create_task(asyncio.to_thread(stubborn_provider_thread))
    while not thread_started.is_set():
        await asyncio.sleep(0.001)
    return "outcome and trajectory finalized"

print(run_direct_async(direct_run()), flush=True)
hard_exit_direct_run_if_configured(7)
"""
    env = os.environ.copy()
    env[BOUNDED_ASYNC_SHUTDOWN_ENV] = "0.02"
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(source_root), env.get("PYTHONPATH")))
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        timeout=1,
        check=False,
    )

    assert completed.returncode == 7
    assert "outcome and trajectory finalized" in completed.stdout


def test_hard_exit_is_disabled_for_ordinary_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(BOUNDED_ASYNC_SHUTDOWN_ENV, raising=False)

    assert hard_exit_direct_run_if_configured(9) is None


def test_invalid_bounded_shutdown_does_not_leak_coroutine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BOUNDED_ASYNC_SHUTDOWN_ENV, "0")

    with pytest.raises(ValueError, match="must be greater than 0"):
        run_direct_async(asyncio.sleep(0))


@pytest.mark.parametrize("main_fails", (False, True))
def test_broken_background_cleanup_does_not_replace_main_result(
    monkeypatch: pytest.MonkeyPatch, main_fails: bool
) -> None:
    monkeypatch.setenv(BOUNDED_ASYNC_SHUTDOWN_ENV, "0.02")

    async def breaks_loop_during_cancellation():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.get_running_loop().stop()
            raise

    async def direct_run():
        asyncio.create_task(breaks_loop_during_cancellation())
        await asyncio.sleep(0)
        if main_fails:
            raise RuntimeError("primary direct-run failure")
        return "primary direct-run result"

    if main_fails:
        with pytest.raises(RuntimeError, match="primary direct-run failure"):
            run_direct_async(direct_run())
    else:
        assert run_direct_async(direct_run()) == "primary direct-run result"


def test_one_shot_shutdown_does_not_run_synchronously_blocking_cancellation() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import asyncio
import time
from aworld_cli.async_runtime import hard_exit_direct_run_if_configured, run_direct_async

async def blocking_cleanup():
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        time.sleep(5)

async def direct_run():
    asyncio.create_task(blocking_cleanup())
    await asyncio.sleep(0)
    return "outcome finalized"

print(run_direct_async(direct_run()), flush=True)
hard_exit_direct_run_if_configured(0)
"""
    env = os.environ.copy()
    env[BOUNDED_ASYNC_SHUTDOWN_ENV] = "0.02"
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(source_root), env.get("PYTHONPATH")))
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        timeout=1,
        check=False,
    )

    assert completed.returncode == 0
    assert "outcome finalized" in completed.stdout


def test_one_shot_generation_skips_synchronously_blocking_stream_close() -> None:
    cli_source_root = Path(__file__).resolve().parents[1] / "src"
    repository_root = Path(__file__).resolve().parents[2]
    script = """
import asyncio
import time
from aworld.agents.llm_agent import LLMAgent
from aworld_cli.async_runtime import hard_exit_direct_run_if_configured, run_direct_async

class BlockingStream:
    async def aclose(self):
        time.sleep(5)

async def direct_run():
    await LLMAgent._close_generation_stream(BlockingStream())
    return "stream outcome finalized"

print(run_direct_async(direct_run()), flush=True)
hard_exit_direct_run_if_configured(0)
"""
    env = os.environ.copy()
    env[BOUNDED_ASYNC_SHUTDOWN_ENV] = "0.02"
    env["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            (
                str(repository_root),
                str(cli_source_root),
                env.get("PYTHONPATH"),
            ),
        )
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )

    assert completed.returncode == 0
    assert "stream outcome finalized" in completed.stdout
