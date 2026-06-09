from __future__ import annotations

from aworld.core.task import TaskResponse
from aworld.evaluations.execution import normalize_task_response_to_eval_state
from aworld.evaluations.scorers.state_extractors import (
    get_assistant_messages,
    get_completion,
    get_tool_calls,
)


def test_normalize_task_response_to_eval_state_captures_answer_usage_and_trajectory() -> None:
    response = TaskResponse(
        id="task-1",
        answer="done",
        usage={"total_tokens": 42},
        trajectory=[{"type": "tool", "name": "search"}],
        success=True,
    )

    state = normalize_task_response_to_eval_state(case_id="case-1", response=response)

    assert state.case_id == "case-1"
    assert state.answer == "done"
    assert state.completion == ["done"]
    assert state.usage["total_tokens"] == 42
    assert state.trajectory[0]["name"] == "search"
    assert state.status == "success"


def test_state_extractors_support_completion_and_tool_queries() -> None:
    state = {
        "completion": [{"role": "assistant", "content": "final"}],
        "trajectory": [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "thinking"},
            {"action": {"tool_calls": [{"name": "search"}]}},
        ],
    }

    assert get_completion(state)[0]["content"] == "final"
    assert get_assistant_messages(state)[0]["content"] == "final"
    assert get_tool_calls(state)[0]["name"] == "search"
