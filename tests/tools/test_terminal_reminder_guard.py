import anyio
import pytest

from examples.gaia.mcp_collections.base import ActionArguments
from examples.gaia.mcp_collections.tools.terminal import (
    CommandResult,
    TerminalActionCollection,
    _MAX_INLINE_STREAM_CHARS,
    _bounded_inline_stream,
    _check_delayed_reminder_simulation,
    _should_log_taskgroup_error,
)

try:
    _EXCEPTION_GROUP_TYPE = ExceptionGroup
except NameError:  # pragma: no cover - exercised on Python < 3.11
    from exceptiongroup import ExceptionGroup as _EXCEPTION_GROUP_TYPE


def test_check_delayed_reminder_simulation_blocks_sleep_reminder():
    blocked, reason = _check_delayed_reminder_simulation('sleep 60 && echo "提醒我喝水"')
    assert blocked is True
    assert "cron" in reason.lower()


def test_terminal_entrypoint_does_not_log_stdio_disconnect_taskgroup():
    error = _EXCEPTION_GROUP_TYPE("unhandled errors in a TaskGroup", [anyio.ClosedResourceError()])

    assert _should_log_taskgroup_error(error) is False


def test_terminal_entrypoint_logs_unexpected_taskgroup_errors():
    error = _EXCEPTION_GROUP_TYPE("unhandled errors in a TaskGroup", [RuntimeError("boom")])

    assert _should_log_taskgroup_error(error) is True


def test_terminal_output_is_bounded_before_context_processing():
    oversized = "HEAD" + ("x" * (_MAX_INLINE_STREAM_CHARS * 2)) + "TAIL"

    bounded = _bounded_inline_stream(oversized)

    assert bounded.startswith("HEAD")
    assert bounded.endswith("TAIL")
    assert "terminal output truncated" in bounded
    assert "redirect the complete output to a file" in bounded
    assert len(bounded) < len(oversized)


@pytest.mark.parametrize("output_format", ["text", "markdown", "json"])
def test_terminal_formatter_never_embeds_an_unbounded_stream(output_format):
    terminal = TerminalActionCollection(
        ActionArguments(name="terminal", workspace=".", unittest=True)
    )
    result = CommandResult(
        command="produce-output",
        success=True,
        stdout="a" * (_MAX_INLINE_STREAM_CHARS * 2),
        stderr="b" * (_MAX_INLINE_STREAM_CHARS * 2),
        return_code=0,
        duration="0:00:01",
        timestamp="2026-07-20T00:00:00",
    )

    formatted = terminal._format_command_output(result, output_format)

    assert formatted.count("terminal output truncated") == 2
    assert len(formatted) < len(result.stdout) + len(result.stderr)


@pytest.mark.parametrize(
    "command",
    [
        "sleep 1",
        "sleep 1 && echo done",
        "python -c \"import time; time.sleep(1); print('done')\"",
    ],
)
def test_check_delayed_reminder_simulation_allows_normal_sleep_usage(command):
    blocked, reason = _check_delayed_reminder_simulation(command)
    assert blocked is False
    assert reason is None


@pytest.mark.asyncio
async def test_execute_command_rejects_sleep_based_reminder():
    terminal = TerminalActionCollection(ActionArguments(name="terminal", workspace=".", unittest=True))

    result = await terminal.mcp_execute_command(
        command='sleep 60 && echo "⏰ 提醒：该喝水了！💧"',
        timeout=10,
        output_format="text",
    )

    assert result.success is False
    assert result.metadata["error_type"] == "reminder_delay_blocked"
    assert "cron" in result.message.lower()


@pytest.mark.parametrize(
    "command",
    [
        'sleep 300; echo "reminder"',
        'sleep 60 && echo "提醒我提交代码" > reminder.txt',
        'sleep 60 && osascript -e \'display notification "该喝水了！" with title "喝水提醒"\'',
        'nohup bash -c \'sleep 60 && osascript -e "display notification \\"该喝水了！\\" with title \\"喝水提醒\\""\'',
        'echo "osascript -e \'display notification \\"该喝水了！\\" with title \\"提醒\\"\'" | at now + 1 minute',
    ],
)
def test_check_delayed_reminder_simulation_blocks_common_variants(command):
    blocked, reason = _check_delayed_reminder_simulation(command)
    assert blocked is True
    assert "cron" in reason.lower()
