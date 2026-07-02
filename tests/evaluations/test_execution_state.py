from __future__ import annotations

import pytest

from aworld.core.task import TaskResponse
from aworld.evaluations.execution import EvalState, normalize_task_response_to_eval_state
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


def test_normalize_mapping_response_preserves_completion_and_tool_calls() -> None:
    state = normalize_task_response_to_eval_state(
        case_id="case-2",
        response={
            "status": "success",
            "answer": "ok",
            "completion": [{"role": "assistant", "content": "ok"}],
            "trajectory": [{"tool_calls": [{"name": "search"}]}],
        },
    )

    assert state.completion[0]["content"] == "ok"
    assert state.tool_calls[0]["name"] == "search"


def test_normalize_eval_state_response_preserves_state_fields() -> None:
    state = normalize_task_response_to_eval_state(
        case_id="case-3",
        response=EvalState(
            case_id="source-case",
            status="success",
            answer="done",
            trajectory=[{"role": "assistant", "content": "step"}],
        ),
        target={"target_kind": "program"},
    )

    assert state.case_id == "case-3"
    assert state.answer == "done"
    assert state.trajectory[0]["content"] == "step"
    assert state.metadata["_target"]["target_kind"] == "program"


def test_normalize_eval_state_shaped_mapping_preserves_response_metadata() -> None:
    state = normalize_task_response_to_eval_state(
        case_id="case-4",
        response=EvalState(
            case_id="source-case",
            status="success",
            answer="done",
            metadata={"program": "demo"},
        ).to_dict(),
        target={"target_kind": "program"},
        metadata={"suite": "demo-suite"},
    )

    assert state.metadata["program"] == "demo"
    assert state.metadata["suite"] == "demo-suite"
    assert state.metadata["_target"]["target_kind"] == "program"


def test_normalize_mapping_rejects_malformed_list_fields() -> None:
    with pytest.raises(ValueError, match="trajectory"):
        normalize_task_response_to_eval_state(
            case_id="case-5",
            response={
                "status": "success",
                "answer": "ok",
                "trajectory": "bad",
            },
        )

    with pytest.raises(ValueError, match="completion"):
        normalize_task_response_to_eval_state(
            case_id="case-6",
            response={
                "status": "success",
                "answer": "ok",
                "completion": {"role": "assistant"},
            },
        )

    with pytest.raises(ValueError, match="tool_calls"):
        normalize_task_response_to_eval_state(
            case_id="case-7",
            response={
                "status": "success",
                "answer": "ok",
                "tool_calls": "bad",
            },
        )
