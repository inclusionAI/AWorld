import asyncio
import json
from pathlib import Path
import shlex
import sys
import time

import pytest

from aworld.sandbox.tool_servers.terminal.src.terminal import (
    CommandResult,
    _BoundedStreamCapture,
    _HARD_MAX_TOTAL_CAPTURE_BYTES,
    _background_drain_tasks,
    _bounded_inline_stream,
    _execute_command_async,
    _format_command_output,
    _get_total_capture_limit_bytes,
    _has_background_operator,
    run_code,
)


def _result(*, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(
        command="demo",
        success=True,
        stdout=stdout,
        stderr=stderr,
        return_code=0,
        duration="0:00:00.001000",
        timestamp="2026-09-15T12:00:00",
    )


def test_bounded_inline_stream_preserves_head_and_tail() -> None:
    value = "a" * 100 + "z" * 100

    bounded = _bounded_inline_stream(value, max_chars=40)

    assert bounded.startswith("a" * 20)
    assert bounded.endswith("z" * 20)
    assert "160 chars omitted" in bounded


def test_format_command_output_bounds_each_stream() -> None:
    formatted = _format_command_output(
        _result(stdout="x" * 20_000, stderr="y" * 20_000),
        output_format="json",
    )

    assert formatted.count("terminal output truncated") == 2
    assert len(formatted) < 34_000


def test_capture_limit_is_configurable_but_clamped_to_hard_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "999999999999")

    assert _get_total_capture_limit_bytes() == _HARD_MAX_TOTAL_CAPTURE_BYTES


def test_stream_capture_retention_stays_bounded_for_large_output() -> None:
    capture = _BoundedStreamCapture("stdout", 4_096)
    capture.feed(b"HEAD")
    chunk = b"x" * (64 * 1024)
    for _ in range(512):
        capture.feed(chunk)
        assert capture.retained_bytes <= 4_096
    capture.feed(b"TAIL")

    rendered = capture.render()

    assert capture.total_bytes > 32 * 1024 * 1024
    assert capture.retained_bytes == 4_096
    assert len(rendered.encode()) < 5_000
    assert rendered.startswith("HEAD")
    assert rendered.endswith("TAIL")
    assert "terminal stdout truncated" in rendered
    assert "complete stream was drained without retention" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "background_suffix",
    ["", " # nohup marker"],
    ids=["foreground", "background-classified"],
)
async def test_execute_large_stdout_and_stderr_uses_bounded_head_tail_capture(
    monkeypatch: pytest.MonkeyPatch,
    background_suffix: str,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "4096")
    child_code = (
        "import sys; "
        "sys.stdout.write('STDOUT_HEAD' + ('x' * 2000000) + 'STDOUT_TAIL'); "
        "sys.stderr.write('STDERR_HEAD' + ('y' * 2000000) + 'STDERR_TAIL')"
    )
    command = (
        f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}{background_suffix}"
    )

    result = await _execute_command_async(command, timeout=10)

    assert result.success is True
    assert result.output_truncated is True
    assert result.capture_limit_bytes == 4_096
    assert result.stdout_total_bytes == 2_000_022
    assert result.stderr_total_bytes == 2_000_022
    assert len(result.stdout.encode()) < 3_000
    assert len(result.stderr.encode()) < 3_000
    assert result.stdout.startswith("STDOUT_HEAD")
    assert result.stdout.endswith("STDOUT_TAIL")
    assert result.stderr.startswith("STDERR_HEAD")
    assert result.stderr.endswith("STDERR_TAIL")
    assert "terminal stdout truncated" in result.stdout
    assert "terminal stderr truncated" in result.stderr


@pytest.mark.asyncio
async def test_run_code_surfaces_truncation_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "4096")
    child_code = "import sys; sys.stdout.write('HEAD' + ('x' * 100000) + 'TAIL')"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"

    response = await run_code(None, command, timeout=10, output_format="text")
    payload = json.loads(response.text)

    assert payload["success"] is True
    assert payload["metadata"]["output_truncated"] is True
    assert payload["metadata"]["stdout_total_bytes"] == 100_008
    assert payload["metadata"]["stdout_omitted_bytes"] > 0
    assert payload["metadata"]["capture_limit_bytes"] == 4_096
    assert payload["metadata"]["capture_strategy"] == "bounded_head_tail_drain"
    assert payload["metadata"]["output_data"] is None
    assert "Output Capture: TRUNCATED" in payload["message"]
    assert response.model_extra["metadata"] == {}


@pytest.mark.asyncio
async def test_run_code_serializes_command_output_once_without_artifact_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unique_output = "terminal-output-copy-canary-592821"
    monkeypatch.setenv("AWORLD_TERMINAL_TEST_CANARY", unique_output)
    child_code = (
        "import os; print(os.environ['AWORLD_TERMINAL_TEST_CANARY'], flush=True)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"

    response = await run_code(None, command, timeout=10, output_format="text")
    payload = json.loads(response.text)

    assert response.text.count(unique_output) == 1
    assert payload["metadata"]["output_data"] is None
    assert response.model_extra["metadata"] == {}


@pytest.mark.asyncio
async def test_timeout_kills_process_group_reaps_and_preserves_partial_output(
    tmp_path: Path,
) -> None:
    escaped_child_marker = tmp_path / "escaped-child.txt"
    grandchild_code = (
        "import pathlib, time; time.sleep(0.8); "
        f"pathlib.Path({str(escaped_child_marker)!r}).write_text('survived')"
    )
    child_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}]); "
        "print('started', flush=True); time.sleep(60)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"
    started_at = time.monotonic()

    result = await _execute_command_async(command, timeout=0.2)

    assert time.monotonic() - started_at < 4
    assert result.success is False
    assert result.timed_out is True
    assert result.return_code == -1
    assert result.capture_complete is True
    assert result.stdout == "started\n"
    assert "timed out after 0.2 seconds" in result.stderr

    await asyncio.sleep(1)
    assert not escaped_child_marker.exists()


@pytest.mark.asyncio
async def test_background_inherited_pipes_switch_to_detached_drain() -> None:
    grandchild_code = (
        "import sys, time; print('early', flush=True); "
        "time.sleep(0.4); print('late', flush=True)"
    )
    child_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}])"
    )
    # The marker selects the existing long-running/background compatibility
    # path while the child process creates the inherited-pipe condition.
    command = (
        f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)} # nohup marker"
    )
    started_at = time.monotonic()

    result = await _execute_command_async(command, timeout=5)

    assert time.monotonic() - started_at < 1
    assert result.success is True
    assert result.capture_complete is False
    assert result.background_output_detached is True
    assert _background_drain_tasks

    await asyncio.sleep(0.6)
    assert not _background_drain_tasks


@pytest.mark.asyncio
async def test_background_operator_without_spaces_returns_promptly() -> None:
    started_at = time.monotonic()

    result = await _execute_command_async("sleep 0.5&", timeout=0.2)

    assert time.monotonic() - started_at < 0.4
    assert result.success is True
    assert result.timed_out is False
    assert result.background_output_detached is True


@pytest.mark.parametrize(
    ("command", "expected"),
    (
        ("sleep 1&", True),
        ("sleep 1 & echo ready", True),
        ("echo '&'", False),
        (r"echo \&", False),
        ("echo ready && echo done", False),
        ("echo ready 2>&1", False),
        ("echo ready &>output.log", False),
    ),
)
def test_background_operator_is_shell_aware(command: str, expected: bool) -> None:
    assert _has_background_operator(command) is expected
