from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.executors.base_executor import (
    _MAX_TOOL_RESULT_DISPLAY_CHARS,
    _bound_tool_result_display_content,
)


def test_tool_result_display_content_is_bounded_before_cleanup() -> None:
    oversized = "HEAD" + ("x" * (_MAX_TOOL_RESULT_DISPLAY_CHARS * 3)) + "TAIL"

    bounded = _bound_tool_result_display_content(oversized)

    assert bounded.startswith("HEAD")
    assert bounded.endswith("TAIL")
    assert "tool result display truncated" in bounded
    assert len(bounded) < len(oversized)


def test_tool_result_display_content_preserves_normal_results() -> None:
    content = "normal tool result"

    assert _bound_tool_result_display_content(content) == content
