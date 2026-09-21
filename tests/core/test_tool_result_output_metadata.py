from aworld.core.common import ActionResult
from aworld.core.tool.base import _tool_result_output_metadata


def test_tool_result_output_metadata_exposes_canonical_failure_and_args() -> None:
    result = ActionResult(
        success=False,
        error="tool failed",
        metadata={"execution_time": 2.5},
        parameter={"path": "/tmp/input"},
    )

    assert _tool_result_output_metadata(result) == {
        "execution_time": 2.5,
        "success": False,
        "error": "tool failed",
        "args": {"path": "/tmp/input"},
    }


def test_tool_result_output_metadata_canonical_status_wins_over_tool_metadata() -> None:
    result = ActionResult(
        success=True,
        metadata={"success": False, "error": "stale error"},
    )

    assert _tool_result_output_metadata(result) == {
        "success": True,
    }


def test_tool_result_output_metadata_ignores_legacy_default_status() -> None:
    result = ActionResult(content="legacy tool did not set success")

    assert _tool_result_output_metadata(result) == {}
