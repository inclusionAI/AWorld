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
    FIRST_PROVIDER_START_TIMEOUT_ENV,
    TASK_COMPLETION_RESERVE_ENV,
    TASK_DEADLINE_EPOCH_ENV,
    DirectRunDeadlineExceeded,
    hard_exit_direct_run_if_configured,
    run_direct_async,
    run_with_first_provider_start_watchdog,
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
    live_summary = {
        "results": [
            {
                "llm_calls": [{"request_id": "request-1"}],
                "trajectory": [{"action": {"content": "working"}}],
            }
        ]
    }
    with pytest.raises(DirectRunDeadlineExceeded) as raised:
        run_direct_async(
            stubborn_provider(),
            deadline_summary=lambda: live_summary,
        )
    assert time.monotonic() - started < 1
    assert raised.value.summary == live_summary


@pytest.mark.asyncio
async def test_startup_watchdog_reports_nested_await_chain_without_locals(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(FIRST_PROVIDER_START_TIMEOUT_ENV, "0.02")
    monkeypatch.setenv(TASK_DEADLINE_EPOCH_ENV, str(time.time() + 1))
    monkeypatch.setenv(TASK_COMPLETION_RESERVE_ENV, "0")
    release = asyncio.Event()
    secret = "NESTED_SECRET_MUST_NOT_APPEAR"

    async def actual_child_provider_wait() -> None:
        assert secret
        await release.wait()

    async def executor_wrapper() -> None:
        await actual_child_provider_wait()

    with pytest.raises(DirectRunDeadlineExceeded):
        await run_with_first_provider_start_watchdog(
            executor_wrapper(),
            evidence_observed=lambda: False,
        )

    assert '"function":"executor_wrapper"' in caplog.text
    assert '"function":"actual_child_provider_wait"' in caplog.text
    assert secret not in caplog.text
    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_first_provider_watchdog_bounds_pre_provider_startup(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(FIRST_PROVIDER_START_TIMEOUT_ENV, "0.02")
    monkeypatch.setenv(TASK_DEADLINE_EPOCH_ENV, str(time.time() + 1))
    monkeypatch.setenv(TASK_COMPLETION_RESERVE_ENV, "0")
    release = asyncio.Event()

    secret_prompt = "PROMPT_MUST_NOT_APPEAR_IN_WATCHDOG_LOG"
    secret_api_key = "sk-secret-must-not-appear"

    async def stalled_startup() -> None:
        # These locals intentionally verify that the diagnostic never formats
        # frames, locals, coroutine/task reprs, or environment values.
        assert secret_prompt and secret_api_key
        await release.wait()

    started = time.monotonic()
    with pytest.raises(DirectRunDeadlineExceeded) as raised:
        await run_with_first_provider_start_watchdog(
            stalled_startup(),
            evidence_observed=lambda: False,
        )
    assert time.monotonic() - started < 1
    assert raised.value.stage == "provider_start"
    assert raised.value.phase == "awaiting_first_provider_attempt"
    assert '"function":"stalled_startup"' in caplog.text
    assert '"file":"test_async_runtime.py"' in caplog.text
    assert secret_prompt not in caplog.text
    assert secret_api_key not in caplog.text
    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_provider_attempt_disarms_watchdog_before_long_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FIRST_PROVIDER_START_TIMEOUT_ENV, "0.02")
    monkeypatch.setenv(TASK_DEADLINE_EPOCH_ENV, str(time.time() + 1))
    monkeypatch.setenv(TASK_COMPLETION_RESERVE_ENV, "0")
    evidence = False

    async def slow_generation() -> str:
        nonlocal evidence
        await asyncio.sleep(0.005)
        # This models the LLM request journal being created before the
        # provider begins a legitimately slow generation.
        evidence = True
        await asyncio.sleep(0.05)
        return "complete"

    assert (
        await run_with_first_provider_start_watchdog(
            slow_generation(),
            evidence_observed=lambda: evidence,
        )
        == "complete"
    )


@pytest.mark.asyncio
async def test_provider_evidence_probe_failure_disables_watchdog(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(FIRST_PROVIDER_START_TIMEOUT_ENV, "0.01")
    monkeypatch.setenv(TASK_DEADLINE_EPOCH_ENV, str(time.time() + 1))
    monkeypatch.setenv(TASK_COMPLETION_RESERVE_ENV, "0")

    async def healthy_task() -> str:
        await asyncio.sleep(0.03)
        return "complete"

    def broken_probe() -> bool:
        raise RuntimeError("probe unavailable")

    assert (
        await run_with_first_provider_start_watchdog(
            healthy_task(),
            evidence_observed=broken_probe,
        )
        == "complete"
    )
    assert "provider-evidence probe failed open" in caplog.text
    assert "startup watchdog expired" not in caplog.text


@pytest.mark.asyncio
async def test_first_provider_watchdog_is_capped_by_absolute_caller_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FIRST_PROVIDER_START_TIMEOUT_ENV, "10")
    monkeypatch.setenv(TASK_DEADLINE_EPOCH_ENV, str(time.time() + 0.02))
    monkeypatch.setenv(TASK_COMPLETION_RESERVE_ENV, "0")
    release = asyncio.Event()

    async def stalled_startup() -> None:
        await release.wait()

    started = time.monotonic()
    with pytest.raises(DirectRunDeadlineExceeded):
        await run_with_first_provider_start_watchdog(
            stalled_startup(),
            evidence_observed=lambda: False,
        )
    assert time.monotonic() - started < 1
    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_first_provider_watchdog_requires_caller_owned_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FIRST_PROVIDER_START_TIMEOUT_ENV, "1")
    monkeypatch.delenv(TASK_DEADLINE_EPOCH_ENV, raising=False)

    with pytest.raises(ValueError, match=TASK_DEADLINE_EPOCH_ENV):
        await run_with_first_provider_start_watchdog(
            asyncio.sleep(0),
            evidence_observed=lambda: False,
        )


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


def test_one_shot_run_finalizes_before_stubborn_provider_cleanup_without_env() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import asyncio
from aworld_cli.async_runtime import hard_exit_direct_run_if_configured, run_direct_async

async def stubborn_provider():
    while True:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            continue

async def direct_run():
    asyncio.create_task(stubborn_provider())
    await asyncio.sleep(0)
    return "task response with real trajectory"

result = run_direct_async(direct_run(), one_shot=True)
print("outcome and trajectory finalized: " + result, flush=True)
hard_exit_direct_run_if_configured(0, one_shot=True)
"""
    env = os.environ.copy()
    env.pop(BOUNDED_ASYNC_SHUTDOWN_ENV, None)
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
    assert "outcome and trajectory finalized" in completed.stdout
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
