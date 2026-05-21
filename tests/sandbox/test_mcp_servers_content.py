from aworld.sandbox.run.mcp_servers import (
    _build_tool_call_failure_result,
    _coalesce_tool_result_content,
)


def test_coalesce_tool_result_content_returns_plain_string_for_single_item():
    assert _coalesce_tool_result_content(["only line"]) == "only line"


def test_coalesce_tool_result_content_preserves_multiple_items():
    assert _coalesce_tool_result_content(["line one", "line two"]) == ["line one", "line two"]


def test_coalesce_tool_result_content_returns_empty_string_for_no_items():
    assert _coalesce_tool_result_content([]) == ""


def test_build_tool_call_failure_result_includes_error_context_and_parameter_summary():
    result = _build_tool_call_failure_result(
        server_name="terminal",
        tool_name="mcp_execute_command",
        parameter={"command": "python script.py", "timeout": 30},
        error=RuntimeError("boom"),
    )

    assert result.tool_name == "terminal"
    assert result.action_name == "mcp_execute_command"
    assert "terminal__mcp_execute_command" in result.content
    assert "RuntimeError: boom" in result.content
    assert "command=python script.py" in result.content
    assert "timeout=30" in result.content
