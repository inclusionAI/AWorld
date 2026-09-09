from __future__ import annotations

import pytest

from aworld.core.common import ActionModel
from aworld.core.tool.base import ToolExecutionDenied, _enforce_runtime_tool_call_budget


class _Context:
    pass


class _Message:
    def __init__(self, context: object) -> None:
        self.context = context


def _actions(count: int) -> list[ActionModel]:
    return [
        ActionModel(tool_name="tool", action_name="run", tool_call_id=f"call-{index}")
        for index in range(count)
    ]


def test_runtime_tool_call_budget_is_disabled_without_environment_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_TOOL_CALL_LIMIT", raising=False)
    message = _Message(_Context())

    _enforce_runtime_tool_call_budget("tool", _actions(100), message)


def test_runtime_tool_call_budget_counts_actions_across_tool_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TOOL_CALL_LIMIT", "3")
    context = _Context()
    message = _Message(context)

    _enforce_runtime_tool_call_budget("first-tool", _actions(2), message)
    _enforce_runtime_tool_call_budget("second-tool", _actions(1), message)

    with pytest.raises(ToolExecutionDenied, match="runtime tool-call budget exhausted"):
        _enforce_runtime_tool_call_budget("third-tool", _actions(1), message)

    assert context._aworld_runtime_tool_call_count == 3


def test_runtime_tool_call_budget_ignores_invalid_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TOOL_CALL_LIMIT", "not-an-integer")

    _enforce_runtime_tool_call_budget("tool", _actions(2), _Message(_Context()))
