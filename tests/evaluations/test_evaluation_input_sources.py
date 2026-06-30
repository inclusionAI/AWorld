from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Mapping

import pytest
from pydantic import BaseModel

from aworld.evaluations.sources import (
    AWorldTrajectoryLogSource,
    JsonlTaskSource,
    JsonlTaskAnswerSource,
    create_source_eval_suite,
)
from aworld.evaluations.state_adapters import (
    AnswerStateAdapter,
    TrajectoryLogStateAdapter,
)
from aworld.evaluations.substrate import (
    CallableJudgeBackend,
    EvalSuiteDef,
    EvaluationFlowDef,
    GatePolicyDef,
    JudgeSchemaDef,
    run_evaluation_flow,
)
from aworld.evaluations.trajectory_judge import TrajectoryJudgeSchema


class _ScoreJudgeOutput(BaseModel):
    score: float
    verdict: Literal["pass", "fail"]


def test_jsonl_task_answer_source_defaults_fields_and_default_adapter(tmp_path: Path) -> None:
    path = tmp_path / "answers.jsonl"
    path.write_text(
        json.dumps({"id": "case-1", "input": "What is 2+2?", "answer": "4"}) + "\n",
        encoding="utf-8",
    )

    source = JsonlTaskAnswerSource(path=path)
    records = list(source.iter_records())
    cases = source.to_cases()

    assert records[0].case_id == "case-1"
    assert records[0].input == {"input": "What is 2+2?"}
    assert records[0].answer == "4"
    assert isinstance(source.default_adapter(), AnswerStateAdapter)
    assert cases[0].case_id == "case-1"
    assert cases[0].input == {"input": "What is 2+2?"}
    assert cases[0].metadata["source_record"]["answer"] == "4"


def test_jsonl_task_source_defaults_fields_without_answer(tmp_path: Path) -> None:
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        json.dumps({"id": "case-1", "input": "What is 2+2?"}) + "\n",
        encoding="utf-8",
    )

    source = JsonlTaskSource(path=path)
    records = list(source.iter_records())
    cases = source.to_cases()

    assert records[0].case_id == "case-1"
    assert records[0].input == {"input": "What is 2+2?"}
    assert records[0].answer is None
    assert records[0].metadata["source_kind"] == "task"
    assert cases[0].case_id == "case-1"
    assert cases[0].input == {"input": "What is 2+2?"}
    assert "answer" not in cases[0].metadata["source_record"]


@pytest.mark.asyncio
async def test_source_eval_suite_replays_task_answer_without_execution(tmp_path: Path) -> None:
    path = tmp_path / "answers.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"id": "case-1", "input": "question", "answer": "existing answer"}),
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    async def judge(case_input: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        captured["answer"] = target["answer"]
        captured["status"] = target["status"]
        return {"score": 1.0, "verdict": "pass"}

    suite = create_source_eval_suite(
        suite_id="answer-source",
        source=JsonlTaskAnswerSource(path=path),
        judge_backend=CallableJudgeBackend(backend_id="judge", judge=judge),
        judge_schema=JudgeSchemaDef(output_model=_ScoreJudgeOutput),
        gate_policy=GatePolicyDef(metric_name="score", pass_threshold=1.0),
    )

    assert isinstance(suite, EvalSuiteDef)
    assert suite.runtime_harness is not None

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "source", "target_path": str(path)}, suite=suite)
    )

    assert captured == {"answer": "existing answer", "status": "success"}
    assert report["gate"]["status"] == "pass"
    assert report["results"][0]["state_summary"]["answer"] == "existing answer"


@pytest.mark.asyncio
async def test_trajectory_log_source_replays_rollout_state_with_standard_metrics(tmp_path: Path) -> None:
    task_id = "task-1"
    trajectory = [
        {
            "state": {
                "input": {"content": "question"},
                "messages": [{"role": "system", "content": "system prompt"}],
            },
            "meta": {"step": 1, "pre_agent": "user", "agent_id": "agent"},
            "action": {
                "tool_calls": [
                    {"function": {"name": "search", "arguments": "{}"}},
                ],
                "is_agent_finished": "False",
            },
        },
        {
            "state": {
                "messages": [
                    {"role": "tool", "content": "search result"},
                    {"role": "assistant", "content": "final"},
                ],
            },
            "meta": {"step": 2, "pre_agent": "agent", "agent_id": "agent"},
            "action": {"content": "final answer", "is_agent_finished": "True"},
        },
    ]
    log_path = tmp_path / "trajectory.log"
    log_path.write_text(
        repr({"task_id": task_id, "is_sub_task": False, "trajectory": json.dumps(trajectory)}) + "\n",
        encoding="utf-8",
    )

    source = AWorldTrajectoryLogSource(path=log_path, task_ids=[task_id], extraction_dir=tmp_path)
    suite = create_source_eval_suite(
        suite_id="trajectory-source",
        source=source,
        judge_backend=CallableJudgeBackend(
            backend_id="judge",
            judge=lambda case_input, target: {"score": 1.0, "verdict": "pass"},
        ),
        judge_schema=JudgeSchemaDef(output_model=_ScoreJudgeOutput),
        gate_policy=GatePolicyDef(metric_name="score", pass_threshold=1.0),
    )

    assert isinstance(source.default_adapter(), TrajectoryLogStateAdapter)
    assert "raw_payload" not in source.to_cases()[0].metadata["source_record"]

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "source", "target_path": str(log_path)}, suite=suite)
    )
    result = report["results"][0]

    assert result["state_summary"]["answer"] == "final answer"
    assert result["state_summary"]["tool_call_count"] == 1
    assert result["metadata"]["extracted_path"].endswith(f"extracted_{task_id}.json")
    assert result["artifacts"]["outcome"]["evidence_blocks"] == 1
    assert result["metadata"]["standard_metrics"]["n_turns"] == 2
    assert result["metadata"]["standard_metrics"]["n_tool_calls"] == 1


def test_trajectory_log_source_extracts_action_result_tool_evidence(tmp_path: Path) -> None:
    task_id = "task-action-result"
    trajectory = [
        {
            "state": {
                "input": {"content": "question"},
                "messages": [{"role": "system", "content": "system prompt"}],
            },
            "meta": {"step": 1, "pre_agent": "runner", "agent_id": "agent"},
            "action": {
                "tool_calls": [{"function": {"name": "terminal", "arguments": "{\"cmd\":\"curl\"}"}}],
                "is_agent_finished": "False",
            },
        },
        {
            "state": {
                "input": {
                    "content": "tool result transport payload",
                    "action_result": [
                        {
                            "action_name": "mcp_execute_command",
                            "tool_name": "terminal",
                            "content": "parsed source evidence from webpage",
                        }
                    ],
                },
                "messages": [{"role": "assistant", "content": "using evidence"}],
            },
            "meta": {"step": 2, "pre_agent": "async_mcp", "agent_id": "agent"},
            "action": {"content": "final answer", "is_agent_finished": "True"},
        },
    ]
    log_path = tmp_path / "trajectory.log"
    log_path.write_text(
        repr({"task_id": task_id, "is_sub_task": False, "trajectory": json.dumps(trajectory)}) + "\n",
        encoding="utf-8",
    )

    record = next(iter(AWorldTrajectoryLogSource(path=log_path, task_ids=[task_id]).iter_records()))

    assert record.raw_payload["evidence"] == [
        {
            "source": "state.input.action_result",
            "step": 2,
            "action_name": "mcp_execute_command",
            "tool_name": "terminal",
            "content": "parsed source evidence from webpage",
        }
    ]


def test_judge_schema_normalizer_runs_before_typed_validation() -> None:
    schema = JudgeSchemaDef(
        output_model=_ScoreJudgeOutput,
        normalizer=lambda payload: {
            "score": payload["weighted_score"],
            "verdict": payload["final_verdict"],
        },
    )

    payload = schema.validate_payload({"weighted_score": 0.9, "final_verdict": "pass"})

    assert payload == {"score": 0.9, "verdict": "pass"}


def test_trajectory_log_source_reports_missing_task_id(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.log"
    path.write_text("", encoding="utf-8")

    source = AWorldTrajectoryLogSource(path=path, task_ids=["missing-task"])

    with pytest.raises(ValueError, match="missing-task"):
        list(source.iter_records())


def test_trajectory_log_source_can_iterate_all_tasks(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.log"
    first = [
        {
            "state": {"input": {"content": "first"}, "messages": []},
            "meta": {"step": 1},
            "action": {"content": "first answer", "is_agent_finished": "True"},
        }
    ]
    second = [
        {
            "state": {"input": {"content": "second"}, "messages": []},
            "meta": {"step": 1},
            "action": {"content": "second answer", "is_agent_finished": "True"},
        }
    ]
    path.write_text(
        "\n".join(
            [
                repr({"task_id": "task-1", "is_sub_task": False, "trajectory": json.dumps(first)}),
                repr({"task_id": "task-2", "is_sub_task": False, "trajectory": json.dumps(second)}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = list(AWorldTrajectoryLogSource(path=path, task_ids=None).iter_records())

    assert [record.case_id for record in records] == ["task-1", "task-2"]
    assert records[0].answer == "first answer"
    assert records[1].answer == "second answer"
    assert records[0].metadata["source_kind"] == "trajectory"


def test_trajectory_judge_schema_normalizes_dimensions_report() -> None:
    schema = TrajectoryJudgeSchema.default()

    payload = schema.validate_payload(
        {
            "weighted_score": 76,
            "verdict": "Pass",
            "dimensions": {
                "A1_groundedness": {"score": 4},
                "A2_completeness": {"score": 3},
                "A3_relevance": {"score": 4},
                "A4_readability": {"score": 5},
                "B1_tool_use": {"score": 4},
                "B2_efficiency": {"score": 2},
                "B3_compliance": {"score": 4},
                "B4_robustness": {"score": 3},
            },
            "veto_triggered": False,
            "has_evidence": True,
            "evidence_block_count": 2,
            "evidence_compacted": False,
            "evidence_incomplete": False,
            "evidence_quality": {
                "has_evidence": True,
                "evidence_block_count": 2,
                "evidence_compacted": False,
                "evidence_incomplete": False,
            },
        }
    )

    assert payload["score"] == 76
    assert payload["A1_groundedness"] == 4
    assert payload["B2_efficiency"] == 2
    assert payload["has_evidence"] is True
    assert payload["evidence_block_count"] == 2
    assert payload["evidence_compacted"] is False
    assert payload["evidence_quality"]["evidence_block_count"] == 2
