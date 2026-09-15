from aworld.sandbox.tool_servers.terminal.src.terminal import (
    CommandResult,
    _bounded_inline_stream,
    _format_command_output,
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
