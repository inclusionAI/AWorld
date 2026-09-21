from __future__ import annotations

import json

import pytest

from aworld.models.model_response import ToolCall
from aworld.output.base import ToolCallOutput, ToolResultOutput
from aworld_cli.executors import base_executor as base_executor_module
from aworld_cli.executors.base_executor import BaseAgentExecutor


class _Executor(BaseAgentExecutor):
    async def chat(self, message):
        return ""


class _CapturingToolLogger:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def log_tool_call(self, **kwargs):
        self.calls.append(kwargs)


def _executor() -> _Executor:
    executor = _Executor.__new__(_Executor)
    executor.session_id = "session-test"
    executor.tool_logger = _CapturingToolLogger()
    executor._tool_call_started_at = {}
    return executor


def _tool_call(*, call_id: str = "call-1", code: str = "false") -> ToolCall:
    return ToolCall.from_dict(
        {
            "id": call_id,
            "function": {
                "name": "run_code",
                "arguments": {"code": code, "timeout": 30},
            },
        }
    )


def test_tool_result_log_uses_origin_args_full_payload_and_explicit_failure() -> None:
    executor = _executor()
    payload = {
        "success": False,
        "message": "command failed",
        "metadata": {"execution_time": 1.25, "return_code": 7},
    }
    output = ToolResultOutput(
        tool_name="terminal",
        action_name="run_code",
        data=json.dumps(payload),
        origin_tool_call=_tool_call(),
        metadata={"execution_time": 1.25, "return_code": 7},
    )

    executor._log_tool_result_output(
        output,
        tool_info="terminal → run_code",
    )

    [record] = executor.tool_logger.calls
    assert record["args"] == {"code": "false", "timeout": 30}
    assert record["output"] == json.dumps(payload)
    assert record["duration"] == 1.25
    assert record["status"] == "error"
    assert record["error"] == "command failed"
    assert record["metadata"]["tool_call_id"] == "call-1"
    assert record["metadata"]["return_code"] == 7


def test_tool_result_log_measures_end_to_end_duration_from_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _executor()
    clock = iter((10.0, 12.75))
    monkeypatch.setattr(base_executor_module.time, "monotonic", lambda: next(clock))
    tool_call = _tool_call(code="sleep 2")

    executor._track_tool_calls([ToolCallOutput.from_tool_call(tool_call, "task-1")])
    fields = executor._tool_result_log_fields(
        ToolResultOutput(
            tool_name="terminal",
            action_name="run_code",
            data=json.dumps({"success": True, "message": "done"}),
            origin_tool_call=tool_call,
        )
    )

    assert fields["duration"] == 2.75
    assert fields["status"] == "success"
    assert fields["args"]["code"] == "sleep 2"
    assert executor._tool_call_started_at == {}

