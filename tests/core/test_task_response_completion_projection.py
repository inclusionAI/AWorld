import json

import pytest

from aworld.self_evolve.replay import _trajectory_task_completion_established
from aworld_cli.top_level_commands.run_cmd import (
    _bounded_task_response_capability_payload,
)


@pytest.mark.parametrize("fallback", [False, True], ids=["projection", "fallback"])
@pytest.mark.parametrize(
    ("finished", "pending_calls", "expected_completion"),
    [
        pytest.param(True, [], True, id="finished-bool"),
        pytest.param("True", [], True, id="finished-string"),
        pytest.param(" TRUE ", [], True, id="finished-normalized-string"),
        pytest.param(False, [], False, id="unfinished-bool"),
        pytest.param("False", [], False, id="unfinished-string"),
        pytest.param("", [], False, id="empty-finish"),
        pytest.param(0, [], False, id="zero-finish"),
        pytest.param(1, [], False, id="nonboolean-finish"),
        pytest.param(None, [], False, id="unknown-finish"),
        pytest.param(..., [], False, id="missing-finish"),
        pytest.param(True, [{}], False, id="pending-bool"),
        pytest.param("True", [{}], False, id="pending-string"),
        pytest.param(True, ({},), False, id="pending-tuple"),
        pytest.param(
            True,
            [
                {"id": "pending-1", "function": {"name": "tool", "arguments": "x" * 20_000}},
                {"id": "pending-2"},
            ],
            False,
            id="pending-large-arguments",
        ),
    ],
)
def test_capability_compaction_preserves_completion(
    fallback, finished, pending_calls, expected_completion
):
    action = {"content": "The task response is ready.", "tool_calls": pending_calls}
    if finished is not ...:
        action["is_agent_finished"] = finished
    sidecar = {
        "schema_version": "aworld.self_evolve.task_response.v1",
        "trajectory_capture_mode": "task_response",
        "trajectory": [
            {
                "meta": {"step": 1, "detail": "x" * 20_000 if fallback else "ok"},
                "state": {"input": {"content": "original task"}},
                "action": action,
            }
        ],
        "llm_calls": [{"payload": "x" * 20_000}],
    }
    assert _trajectory_task_completion_established(
        sidecar["trajectory"], capture_mode="task_response"
    ) is expected_completion

    compact = _bounded_task_response_capability_payload(sidecar, max_bytes=4_096)
    encoded = json.dumps(
        compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    projected = compact["trajectory"][0]

    assert len(encoded) <= 4_096
    assert compact["trajectory_compacted"] is True
    assert ("meta" not in projected) is fallback
    assert _trajectory_task_completion_established(
        compact["trajectory"], capture_mode="task_response"
    ) is expected_completion
    assert ("is_agent_finished" in projected["action"]) is (finished is not ...)
    if finished is not ...:
        assert projected["action"]["is_agent_finished"] == finished
        assert type(projected["action"]["is_agent_finished"]) is type(finished)
    assert projected["action"]["tool_call_count"] == len(pending_calls)
