from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import pytest

from aworld.config.conf import EvaluationConfig
from aworld.evaluations.substrate import JudgeTimeoutError
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
import aworld.self_evolve.evaluation as evaluation_module
from aworld.self_evolve.evaluation import (
    AWorldTrajectoryEvaluatorBackend,
    CandidateConfidenceDecision,
    CommandVerificationBackend,
    EvaluateRunnerBackend,
    EvaluationBackend,
    EvaluationRequest,
    TrajectoryQualityBackend,
    determine_candidate_confidence,
    estimate_replay_cost,
    evaluate_baseline_and_candidate,
)
from aworld.self_evolve.trace_pack import build_trace_pack
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    SelfEvolveTargetRef,
)


def _dataset(cases: tuple[EvalCase, ...]) -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=cases,
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": len(cases)},
            split_seed="seed",
            splits={"train": [case.case_id for case in cases], "validation": [], "held_out": []},
        ),
    )


def _candidate(candidate_id: str = "candidate") -> CandidateVariant:
    return CandidateVariant(
        candidate_id=candidate_id,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="test candidate",
    )


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_uses_existing_source_runtime(tmp_path) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Recover the workflow."}},
            "action": {"content": "Recovered with evidence."},
            "reward": {"status": "ok"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="task-eval",
    )
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                trace_pack=trace_pack,
            ),
        )
    )
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: trajectory-judge\n---\nJudge trajectory quality.\n", encoding="utf-8")
    calls = []

    def fake_run_evaluator_source(**kwargs):
        calls.append(kwargs)
        log_path = kwargs["input"]
        raw_line = __import__("ast").literal_eval(open(log_path, encoding="utf-8").read().strip())
        assert raw_line["task_id"] == "task-eval"
        assert raw_line["is_sub_task"] is False
        loaded_trajectory = __import__("json").loads(raw_line["trajectory"])
        assert loaded_trajectory[0]["action"]["content"] == "Recovered with evidence."
        return {
            "suite_id": "trajectory-source-evaluator",
            "summary": {
                "trajectory-source-evaluator": {
                    "score": {"mean": 88.0},
                    "A1_groundedness": {"mean": 4.0},
                }
            },
            "gate": {"status": "pass", "metric_name": "score", "value": 88.0},
            "report_path": str(tmp_path / "report.json"),
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent=str(judge_agent),
        run_evaluator_source=fake_run_evaluator_source,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert len(calls) == 1
    assert calls[0]["kind"] == "trajectory"
    assert calls[0]["judge_agent"] == str(judge_agent)
    assert calls[0]["task_id"] == "task-eval"
    assert summary.metrics["evaluator_mode"] == "aworld_trajectory_evaluator"
    assert summary.metrics["score"] == 88.0
    assert summary.metrics["A1_groundedness"] == 4.0
    assert summary.metrics["evaluator_gate_passed"] is True
    assert summary.metrics["evaluation_agent_signal"] is True


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_retries_transient_judge_parse_failure(tmp_path) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )
    calls = []

    def flaky_run_evaluator_source(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ValueError("judge response does not contain a valid JSON object")
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 84.0}}},
            "gate": {"status": "pass", "metric_name": "score", "value": 84.0},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=flaky_run_evaluator_source,
        judge_repetitions=1,
        judge_failure_retries=1,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert len(calls) == 2
    assert summary.metrics["score"] == 84.0
    assert summary.metrics["evaluator_gate_passed"] is True
    assert summary.metrics["judge_attempt_count"] == 2
    assert summary.metrics["judge_success_count"] == 1
    assert summary.metrics["judge_failure_count"] == 1
    assert summary.metrics["judge_failures"][0]["type"] == "ValueError"


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_falls_back_from_missing_model_profile(
    tmp_path,
) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )
    profiles_seen: list[str | None] = []

    def profile_sensitive_run_evaluator_source(**kwargs):
        profiles_seen.append(kwargs["judge_model_profile"])
        if kwargs["judge_model_profile"] == "judge":
            raise KeyError("model profile not found or incomplete: judge")
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 84.0}}},
            "gate": {"status": "pass", "metric_name": "score", "value": 84.0},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        judge_model_profile="judge",
        run_evaluator_source=profile_sensitive_run_evaluator_source,
        judge_repetitions=1,
        judge_failure_retries=1,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert profiles_seen == ["judge", None]
    assert summary.metrics["score"] == 84.0
    assert summary.metrics["evaluator_gate_passed"] is True
    assert summary.metrics["judge_attempt_count"] == 2
    assert summary.metrics["judge_success_count"] == 1
    assert summary.metrics["judge_failure_count"] == 1
    assert summary.metrics["judge_failures"][0]["type"] == "KeyError"
    assert summary.metrics["judge_model_profile_fallback"] == "judge"


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_aggregates_judge_repetitions(tmp_path) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )
    scores = [40.0, 60.0, 80.0]

    def variable_run_evaluator_source(**kwargs):
        score = scores.pop(0)
        return {
            "summary": {
                "trajectory-source-evaluator": {
                    "score": {"mean": score},
                    "A1_groundedness": {"mean": score / 20.0},
                }
            },
            "gate": {
                "status": "pass",
                "metric_name": "score",
                "value": score,
            },
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=variable_run_evaluator_source,
        judge_repetitions=3,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert scores == []
    assert summary.metrics["score"] == 60.0
    assert summary.metrics["score_std"] > 0
    assert summary.metrics["A1_groundedness"] == 3.0
    assert summary.metrics["judge_repetitions"] == 3
    assert summary.metrics["judge_success_count"] == 3
    assert summary.metrics["evaluator_gate_passed"] is True


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_extracts_evidence_quality_metrics(tmp_path) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )

    def evidence_quality_report(**kwargs):
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 84.0}}},
            "results": [
                {
                    "case_id": "task-eval",
                    "judge": {
                        "score": 84.0,
                        "verdict": "Pass",
                        "evidence_quality": {
                            "has_evidence": True,
                            "evidence_block_count": 2,
                            "evidence_compacted": False,
                            "evidence_incomplete": False,
                        },
                    },
                }
            ],
            "gate": {"status": "pass", "metric_name": "score", "value": 84.0},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=evidence_quality_report,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert summary.metrics["has_evidence"] == 1.0
    assert summary.metrics["evidence_block_count"] == 2
    assert summary.metrics["evidence_compacted"] is False
    assert summary.metrics["evidence_incomplete"] is False


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_preserves_any_compacted_repetition(tmp_path) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )
    compacted_values = [False, True]

    def repeated_evidence_report(**kwargs):
        compacted = compacted_values.pop(0)
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 84.0}}},
            "results": [
                {
                    "case_id": "task-eval",
                    "judge": {
                        "score": 84.0,
                        "verdict": "Pass",
                        "evidence_quality": {
                            "has_evidence": True,
                            "evidence_block_count": 1,
                            "evidence_compacted": compacted,
                            "evidence_incomplete": False,
                        },
                    },
                }
            ],
            "gate": {"status": "pass", "metric_name": "score", "value": 84.0},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=repeated_evidence_report,
        judge_repetitions=2,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert compacted_values == []
    assert summary.metrics["has_evidence"] is True
    assert summary.metrics["evidence_block_count"] == 1
    assert summary.metrics["evidence_compacted"] is True


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_degrades_when_all_judge_attempts_fail(tmp_path) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )

    def failing_run_evaluator_source(**kwargs):
        raise ValueError("judge response does not contain a valid JSON object")

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=failing_run_evaluator_source,
        judge_repetitions=2,
        judge_failure_retries=1,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert summary.metrics["score"] == 0.0
    assert summary.metrics["evaluator_gate_passed"] is False
    assert summary.metrics["deterministic_signal"] is False
    assert summary.metrics["judge_attempt_count"] == 3
    assert summary.metrics["judge_success_count"] == 0
    assert summary.metrics["judge_failure_count"] == 3


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_times_out_hung_judge_call(tmp_path) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )

    async def hanging_run_evaluator_source(**kwargs):
        await asyncio.sleep(60)

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=hanging_run_evaluator_source,
        judge_repetitions=1,
        judge_failure_retries=0,
        judge_timeout_seconds=0.01,
    )

    summary = await asyncio.wait_for(
        backend.evaluate_variant(
            EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
        ),
        timeout=1.0,
    )

    assert summary.metrics["score"] == 0.0
    assert summary.metrics["evaluator_gate_passed"] is False
    assert summary.metrics["judge_attempt_count"] == 1
    assert summary.metrics["judge_success_count"] == 0
    assert summary.metrics["judge_failure_count"] == 1
    assert summary.metrics["judge_failures"][0]["type"] == "TimeoutError"
    assert "timed out after 0.01s" in summary.metrics["judge_failures"][0]["reason"]


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_preserves_judge_timeout_diagnostics(
    tmp_path,
) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )
    diagnostics = (
        {
            "phase": "initial_judge",
            "round_index": 0,
            "status": "timed_out",
            "prompt_chars": 1200,
            "estimated_input_tokens": 400,
            "artifact_request_count": 0,
            "artifact_read_count": 0,
            "artifact_read_chars": 0,
            "latency_ms": 10.0,
            "timeout_seconds": 0.01,
        },
    )

    def timed_out_run_evaluator_source(**kwargs):
        raise JudgeTimeoutError("judge call timed out during initial_judge", diagnostics=diagnostics)

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=timed_out_run_evaluator_source,
        judge_repetitions=1,
        judge_failure_retries=0,
        judge_timeout_seconds=1,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    failure = summary.metrics["judge_failures"][0]
    assert failure["type"] == "TimeoutError"
    assert failure["diagnostics"] == list(diagnostics)
    assert failure["timeout_phase"] == "initial_judge"


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_preserves_provider_timeout_without_limit(
    tmp_path,
) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )

    def timed_out_run_evaluator_source(**kwargs):
        raise JudgeTimeoutError(
            "judge call timed out during initial_judge",
            diagnostics=(
                {
                    "phase": "initial_judge",
                    "status": "timed_out",
                    "latency_ms": 10.0,
                    "timeout_seconds": None,
                },
            ),
        )

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=timed_out_run_evaluator_source,
        judge_repetitions=1,
        judge_failure_retries=0,
        judge_timeout_seconds=None,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    failure = summary.metrics["judge_failures"][0]
    assert failure["reason"] == "judge call timed out during initial_judge"
    assert failure["timeout_phase"] == "initial_judge"


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_summarizes_judge_call_diagnostics(
    tmp_path,
) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )

    def diagnostic_run_evaluator_source(**kwargs):
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 84.0}}},
            "results": [
                {
                    "case_id": "task-eval",
                    "judge_diagnostics": [
                        {
                            "phase": "initial_judge",
                            "status": "succeeded",
                            "prompt_chars": 1200,
                            "estimated_input_tokens": 400,
                            "artifact_request_count": 1,
                            "artifact_read_count": 0,
                            "artifact_read_chars": 0,
                            "latency_ms": 25.0,
                        },
                        {
                            "phase": "artifact_read_round_1",
                            "status": "succeeded",
                            "prompt_chars": 2200,
                            "estimated_input_tokens": 650,
                            "artifact_request_count": 0,
                            "artifact_read_count": 1,
                            "artifact_read_chars": 700,
                            "latency_ms": 40.0,
                        },
                    ],
                }
            ],
            "gate": {"status": "pass", "metric_name": "score", "value": 84.0},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=diagnostic_run_evaluator_source,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert summary.metrics["judge_call_count"] == 2
    assert summary.metrics["judge_artifact_read_round_count"] == 1
    assert summary.metrics["judge_artifact_request_count"] == 1
    assert summary.metrics["judge_artifact_read_count"] == 1
    assert summary.metrics["judge_artifact_read_chars"] == 700
    assert summary.metrics["judge_prompt_chars_total"] == 3400
    assert summary.metrics["judge_estimated_input_tokens_total"] == 1050
    assert summary.metrics["judge_model_latency_ms_total"] == pytest.approx(65.0)
    assert summary.metrics["judge_model_latency_ms_max"] == pytest.approx(40.0)
    assert summary.metrics["judge_timeout_count"] == 0
    assert len(summary.metrics["judge_call_diagnostics"]) == 2


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_runs_default_source_runtime_outside_active_loop(
    tmp_path, monkeypatch
) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )

    def sync_source_runtime(**kwargs):
        async def build_report():
            return {
                "summary": {"trajectory-source-evaluator": {"score": {"mean": 82.0}}},
                "gate": {"status": "pass", "metric_name": "score", "value": 82.0},
            }

        return asyncio.run(build_report())

    monkeypatch.setattr(
        evaluation_module,
        "_load_run_evaluator_source_cli",
        lambda: sync_source_runtime,
    )
    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert summary.metrics["score"] == 82.0
    assert summary.metrics["evaluator_gate_passed"] is True


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_isolates_runtime_log_path(
    tmp_path, monkeypatch
) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )
    monkeypatch.setenv("AWORLD_LOG_PATH", str(tmp_path / "original-logs"))
    captured: dict[str, str | None] = {}

    def source_runtime(**kwargs):
        import os

        captured["aworld_log_path"] = os.environ.get("AWORLD_LOG_PATH")
        captured["trajectory_log_disabled"] = os.environ.get(
            "AWORLD_TRAJECTORY_LOG_DISABLED"
        )
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 82.0}}},
            "gate": {"status": "pass", "metric_name": "score", "value": 82.0},
        }

    monkeypatch.setattr(
        evaluation_module,
        "_load_run_evaluator_source_cli",
        lambda: source_runtime,
    )
    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(
            variant_id="candidate",
            candidate=None,
            dataset=dataset,
            dataset_split="validation",
            artifact_namespace="run-1",
        )
    )

    expected_log_path = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "evaluator"
        / "run-1"
        / "candidate"
        / "validation"
        / "logs"
    )
    assert summary.metrics["score"] == 82.0
    assert captured["aworld_log_path"] == str(expected_log_path)
    assert captured["trajectory_log_disabled"] == "1"
    assert os.environ["AWORLD_LOG_PATH"] == str(tmp_path / "original-logs")
    assert "AWORLD_TRAJECTORY_LOG_DISABLED" not in os.environ


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_does_not_outer_timeout_default_source_runtime(
    tmp_path, monkeypatch
) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )

    def slow_but_successful_source_runtime(**kwargs):
        import time

        time.sleep(0.05)
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 81.0}}},
            "gate": {"status": "pass", "metric_name": "score", "value": 81.0},
        }

    monkeypatch.setattr(
        evaluation_module,
        "_load_run_evaluator_source_cli",
        lambda: slow_but_successful_source_runtime,
    )
    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        judge_timeout_seconds=0.01,
    )

    summary = await asyncio.wait_for(
        backend.evaluate_variant(
            EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
        ),
        timeout=1.0,
    )

    assert summary.metrics["score"] == 81.0
    assert summary.metrics["evaluator_gate_passed"] is True
    assert summary.metrics["judge_success_count"] == 1


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_compares_variant_trajectories(tmp_path) -> None:
    baseline_trajectory = [
        {
            "state": {"input": {"content": "Complete task."}},
            "action": {"content": "Stopped early."},
            "reward": {"status": "failed"},
        }
    ]
    candidate_trajectory = [
        {
            "state": {"input": {"content": "Complete task."}},
            "action": {"content": "Completed with cited evidence."},
            "reward": {"status": "ok"},
        }
    ]
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-variant",
                input={"content": "Complete task."},
                metadata={
                    "variant_trajectories": {
                        "baseline": baseline_trajectory,
                        "cand-1": candidate_trajectory,
                    }
                },
            ),
        )
    )
    seen_actions = []

    def fake_run_evaluator_source(**kwargs):
        raw_line = __import__("ast").literal_eval(open(kwargs["input"], encoding="utf-8").read().strip())
        loaded_trajectory = __import__("json").loads(raw_line["trajectory"])
        action = loaded_trajectory[0]["action"]["content"]
        seen_actions.append(action)
        score = 91.0 if "Completed" in action else 55.0
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": score}}},
            "gate": {"status": "pass", "metric_name": "score", "value": score},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=fake_run_evaluator_source,
    )

    baseline, candidate = await evaluate_baseline_and_candidate(
        backend,
        dataset=dataset,
        candidate=_candidate("cand-1"),
    )

    assert seen_actions == ["Stopped early.", "Completed with cited evidence."]
    assert baseline.metrics["score"] == 55.0
    assert candidate.metrics["score"] == 91.0


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_deduplicates_replay_variant_trajectories(
    tmp_path,
) -> None:
    shared_baseline = [{"action": {"content": "Baseline replay."}}]
    candidate_a = [{"action": {"content": "Candidate replay A."}}]
    candidate_b = [{"action": {"content": "Candidate replay B."}}]
    cases = []
    for index, candidate_trajectory in enumerate(
        (candidate_a, candidate_a, candidate_b, candidate_b),
        start=1,
    ):
        cases.append(
            EvalCase(
                case_id=f"task-{index}",
                input={"content": "Complete task."},
                metadata={
                    "variant_trajectories": {
                        "baseline": shared_baseline,
                        "cand-1": candidate_trajectory,
                    },
                    "replay": {"baseline": {}, "cand-1": {}},
                },
            )
        )
    dataset = _dataset(tuple(cases))
    line_counts = []

    def fake_run_evaluator_source(**kwargs):
        raw_lines = [
            line
            for line in open(kwargs["input"], encoding="utf-8").read().splitlines()
            if line.strip()
        ]
        line_counts.append(len(raw_lines))
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 80.0}}},
            "gate": {"status": "pass", "metric_name": "score", "value": 80.0},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=fake_run_evaluator_source,
    )

    baseline, candidate = await evaluate_baseline_and_candidate(
        backend,
        dataset=dataset,
        candidate=_candidate("cand-1"),
    )

    assert line_counts == [1, 2]
    assert baseline.metrics["original_case_count"] == 4
    assert baseline.metrics["effective_case_count"] == 1
    assert baseline.metrics["deduplicated_case_count"] == 3
    assert baseline.metrics["command_case_count"] == 1
    assert candidate.metrics["original_case_count"] == 4
    assert candidate.metrics["effective_case_count"] == 2
    assert candidate.metrics["deduplicated_case_count"] == 2
    assert candidate.metrics["command_case_count"] == 2


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_isolates_validation_and_held_out_members(
    tmp_path,
) -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="train-task",
                input="Train task",
                metadata={
                    "variant_trajectories": {
                        "cand-1": [{"action": {"content": "train candidate"}}]
                    }
                },
            ),
            EvalCase(
                case_id="held-task",
                input="Held-out task",
                metadata={
                    "variant_trajectories": {
                        "cand-1": [{"action": {"content": "held candidate"}}]
                    }
                },
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={
                "train": ["train-task"],
                "validation": [],
                "held_out": ["held-task"],
            },
            trainable_case_ids=("train-task",),
            held_out_case_ids=("held-task",),
        ),
    )
    seen_task_ids: list[list[str]] = []

    def fake_run_evaluator_source(**kwargs):
        import ast

        seen_task_ids.append(
            [
                ast.literal_eval(line)["task_id"]
                for line in open(kwargs["input"], encoding="utf-8")
                if line.strip()
            ]
        )
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 80.0}}},
            "gate": {"status": "pass", "metric_name": "score", "value": 80.0},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=fake_run_evaluator_source,
    )

    for split in ("validation", "held_out"):
        await backend.evaluate_variant(
            EvaluationRequest(
                variant_id="cand-1",
                candidate=_candidate("cand-1"),
                dataset=dataset,
                dataset_split=split,
            )
        )

    assert seen_task_ids == [["train-task"], ["held-task"]]


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_skips_empty_held_out_split(
    tmp_path,
) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="train-task",
                input="Train task",
                metadata={
                    "variant_trajectories": {
                        "cand-1": [{"action": {"content": "candidate"}}]
                    }
                },
            ),
        )
    )
    calls = []

    def fake_run_evaluator_source(**kwargs):
        calls.append(kwargs)
        raise AssertionError("empty held-out split must not invoke evaluator")

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=fake_run_evaluator_source,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(
            variant_id="cand-1",
            candidate=_candidate("cand-1"),
            dataset=dataset,
            dataset_split="held_out",
        )
    )

    assert calls == []
    assert summary.metrics["score"] == 0.0
    assert summary.metrics["evaluator_gate_passed"] is False
    assert summary.metrics["evaluation_case_count"] == 0


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_scopes_artifacts_by_namespace(tmp_path) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-eval",
                input={"content": "Recover the workflow."},
                metadata={"baseline_trajectory": [{"action": {"content": "Recovered."}}]},
            ),
        )
    )
    input_paths = []

    def fake_run_evaluator_source(**kwargs):
        input_paths.append(kwargs["input"])
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 82.0}}},
            "gate": {"status": "pass", "metric_name": "score", "value": 82.0},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_agent_name="trajectory-judge",
        run_evaluator_source=fake_run_evaluator_source,
    )

    for namespace in ("run-a", "run-b"):
        await backend.evaluate_variant(
            EvaluationRequest(
                variant_id="baseline",
                candidate=None,
                dataset=dataset,
                dataset_split="validation",
                artifact_namespace=namespace,
            )
        )

    assert input_paths == [
        str(
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "evaluator"
            / "run-a"
            / "baseline"
            / "validation"
            / "trajectory.log"
        ),
        str(
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "evaluator"
            / "run-b"
            / "baseline"
            / "validation"
            / "trajectory.log"
        ),
    ]


@pytest.mark.asyncio
async def test_aworld_trajectory_evaluator_backend_accepts_backend_ref(tmp_path) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="task-backend-ref",
                input={"content": "Recover the workflow."},
                trace_pack=build_trace_pack(
                    [
                        {
                            "state": {"input": {"content": "Recover the workflow."}},
                            "action": {"content": "Recovered."},
                            "reward": {"status": "ok"},
                        }
                    ],
                    source_kind="current_trajectory",
                    task_id="task-backend-ref",
                ),
            ),
        )
    )
    calls = []

    def fake_run_evaluator_source(**kwargs):
        calls.append(kwargs)
        return {
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 77.0}}},
            "gate": {"status": "pass", "metric_name": "score", "value": 77.0},
        }

    backend = AWorldTrajectoryEvaluatorBackend(
        workspace_root=tmp_path,
        judge_backend_ref="pkg.module:build_judge",
        run_evaluator_source=fake_run_evaluator_source,
    )

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert calls[0]["judge_backend_ref"] == "pkg.module:build_judge"
    assert calls[0]["judge_agent"] is None
    assert calls[0]["judge_agent_name"] is None
    assert summary.metrics["score"] == 77.0


@pytest.mark.asyncio
async def test_command_verification_backend_reports_objective_pass_rate(tmp_path) -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="case-pass",
                input="pass",
                verification_command="python -c 'raise SystemExit(0)'",
            ),
            EvalCase(
                case_id="case-fail",
                input="fail",
                verification_command="python -c 'raise SystemExit(3)'",
            ),
        )
    )
    backend = CommandVerificationBackend(workspace_root=tmp_path)

    summary = await backend.evaluate_variant(
        EvaluationRequest(variant_id="cand-1", candidate=_candidate("cand-1"), dataset=dataset)
    )

    assert summary.variant_id == "cand-1"
    assert summary.dataset_split == "all"
    assert summary.metrics["deterministic_signal"] is True
    assert summary.metrics["command_case_count"] == 2
    assert summary.metrics["command_pass_count"] == 1
    assert summary.metrics["command_failure_count"] == 1
    assert summary.metrics["command_pass_rate"] == 0.5
    assert summary.metrics["case_results"][1]["returncode"] == 3
    assert summary.metrics["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_evaluate_runner_backend_invokes_existing_runner_factory() -> None:
    eval_config = EvaluationConfig(eval_criterias=[])
    calls = []

    @dataclass
    class FakeEvalResult:
        summary: dict

    class FakeRunner:
        def __init__(self, config):
            calls.append(config)

        async def run(self):
            return FakeEvalResult(summary={"score": 0.75, "case_count": 2})

    backend = EvaluateRunnerBackend(runner_factory=FakeRunner)
    summary = await backend.evaluate_variant(
        EvaluationRequest(
            variant_id="baseline",
            candidate=None,
            dataset=_dataset((EvalCase(case_id="case-1", input="demo"),)),
            eval_config=eval_config,
        )
    )

    assert calls == [eval_config]
    assert summary.variant_id == "baseline"
    assert summary.metrics == {"score": 0.75, "case_count": 2}


@pytest.mark.asyncio
async def test_baseline_and_candidate_evaluation_share_dataset_and_policy() -> None:
    dataset = _dataset((EvalCase(case_id="case-1", input="demo"),))
    eval_config = EvaluationConfig(eval_criterias=[{"metric_name": "score"}])
    requests = []

    class RecordingBackend(EvaluationBackend):
        async def evaluate_variant(self, request: EvaluationRequest):
            requests.append(request)
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={"request_index": len(requests)},
                dataset_split=request.dataset_split,
            )

    baseline, candidate = await evaluate_baseline_and_candidate(
        RecordingBackend(),
        dataset=dataset,
        candidate=_candidate("cand-1"),
        eval_config=eval_config,
        dataset_split="validation",
    )

    assert baseline.variant_id == "baseline"
    assert candidate.variant_id == "cand-1"
    assert requests[0].dataset is dataset
    assert requests[1].dataset is dataset
    assert requests[0].eval_config is eval_config
    assert requests[1].eval_config is eval_config
    assert requests[0].dataset_split == "validation"
    assert requests[1].dataset_split == "validation"


def test_replay_cost_preflight_counts_replays_judges_and_verification_budget() -> None:
    dataset = _dataset(
        (
            EvalCase(
                case_id="case-1",
                input="one",
                verification_command="python -c 'raise SystemExit(0)'",
            ),
            EvalCase(case_id="case-2", input="two"),
            EvalCase(
                case_id="case-3",
                input="three",
                verification_command="python -c 'raise SystemExit(0)'",
            ),
        )
    )

    estimate = estimate_replay_cost(
        dataset=dataset,
        candidate_count=2,
        judge_repetitions=3,
        baseline_repetitions=2,
        candidate_repetitions=2,
        replay_candidate_limit=1,
        estimated_tokens_per_replay=100,
        estimated_cost_usd_per_replay=0.01,
        max_run_tokens=2_000,
        max_run_cost_usd=1.0,
    )

    assert estimate.passed is True
    assert estimate.baseline_replay_count == 6
    assert estimate.candidate_replay_count == 6
    assert estimate.total_replay_count == 12
    assert estimate.verification_command_count == 8
    assert estimate.judge_call_count == 9
    assert estimate.estimated_tokens == 1200
    assert estimate.estimated_cost_usd == 0.12


@pytest.mark.asyncio
async def test_trajectory_quality_backend_scores_trace_pack_completion_and_failures() -> None:
    trace_pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {"input": {"content": "Fix login."}},
                "action": {"content": "I will inspect login state.", "tool_calls": []},
                "reward": {"status": "ok"},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "agent"},
                "state": {"messages": []},
                "action": {"content": "Login guidance still failed.", "tool_calls": []},
                "reward": {"status": "failed"},
            },
        ],
        source_kind="current_trajectory",
        task_id="quality-task",
    )
    dataset = _dataset((EvalCase(case_id="quality-task", input="Fix login.", trace_pack=trace_pack),))

    summary = await TrajectoryQualityBackend().evaluate_variant(
        EvaluationRequest(variant_id="baseline", candidate=None, dataset=dataset)
    )

    assert summary.metrics["trajectory_quality_signal"] is True
    assert summary.metrics["trajectory_case_count"] == 1
    assert summary.metrics["failed_step_count"] == 1
    assert summary.metrics["trajectory_quality_score"] == 0.5

    over_budget = estimate_replay_cost(
        dataset=dataset,
        candidate_count=2,
        judge_repetitions=3,
        estimated_tokens_per_replay=100,
        max_run_tokens=200,
    )

    assert over_budget.passed is False
    assert over_budget.reason == "estimated replay tokens exceed max_run_tokens"


def test_candidate_confidence_requires_sufficient_held_out_and_deterministic_signal() -> None:
    single_case_dataset = _dataset((EvalCase(case_id="case-1", input="demo"),))

    insufficient = determine_candidate_confidence(
        dataset=single_case_dataset,
        validation_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="validation",
        ),
        held_out_summary=None,
        min_eval_cases=2,
    )

    assert insufficient == CandidateConfidenceDecision(
        confidence="limited",
        reason="insufficient held-out eval cases for verified confidence",
        selection_split="validation",
        verification_split=None,
        deterministic_signal_present=True,
        held_out_case_count=0,
    )

    held_out_dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="train-1", input="train"),
            EvalCase(case_id="held-1", input="held"),
            EvalCase(case_id="held-2", input="held"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 3},
            split_seed="seed",
            splits={"train": ["train-1"], "validation": [], "held_out": ["held-1", "held-2"]},
            held_out_case_ids=("held-1", "held-2"),
        ),
    )

    judge_only = determine_candidate_confidence(
        dataset=held_out_dataset,
        validation_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": False},
            dataset_split="validation",
        ),
        held_out_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": False},
            dataset_split="held_out",
        ),
        min_eval_cases=2,
    )

    assert judge_only.confidence == "limited"
    assert judge_only.reason == "verified confidence requires a deterministic signal"
    assert judge_only.deterministic_signal_present is False

    verified = determine_candidate_confidence(
        dataset=held_out_dataset,
        validation_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="validation",
        ),
        held_out_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.3, "deterministic_signal": True},
            dataset_split="held_out",
        ),
        min_eval_cases=2,
    )

    assert verified.confidence == "verified"
    assert verified.reason == "held-out deterministic evaluation is sufficient"
    assert verified.verification_split == "held_out"


def test_candidate_confidence_counts_independent_held_out_members_not_repetitions() -> None:
    replay_dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=f"held-task__replay_{index}", input="held")
            for index in range(1, 4)
        ),
        recipe=DatasetRecipe(
            source={
                "kind": "trajectory_log",
                "paired_replay": True,
                "held_out_member_count": 1,
            },
            split_seed="seed",
            splits={
                "train": [],
                "validation": [],
                "held_out": [
                    "held-task__replay_1",
                    "held-task__replay_2",
                    "held-task__replay_3",
                ],
            },
            held_out_case_ids=(
                "held-task__replay_1",
                "held-task__replay_2",
                "held-task__replay_3",
            ),
        ),
    )

    decision = determine_candidate_confidence(
        dataset=replay_dataset,
        validation_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"deterministic_signal": True},
            dataset_split="validation",
        ),
        held_out_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"deterministic_signal": True},
            dataset_split="held_out",
        ),
        min_eval_cases=2,
    )

    assert decision.confidence == "limited"
    assert decision.held_out_case_count == 1


def test_candidate_confidence_accepts_stable_single_case_replay() -> None:
    single_case_replay_dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="case-1",
                input="demo",
                metadata={
                    "replay": {
                        "baseline": {
                            "status": "succeeded",
                            "metrics": {
                                "repetition_count": 2,
                                "successful_repetition_count": 2,
                            },
                        },
                        "candidate": {
                            "status": "succeeded",
                            "metrics": {
                                "repetition_count": 3,
                                "successful_repetition_count": 3,
                            },
                        },
                    }
                },
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "trajectory_log", "case_count": 1, "paired_replay": True},
            split_seed="seed",
            splits={"train": ["case-1"], "validation": [], "held_out": []},
            held_out_case_ids=(),
        ),
    )

    decision = determine_candidate_confidence(
        dataset=single_case_replay_dataset,
        validation_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="validation",
        ),
        held_out_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="held_out",
        ),
        min_eval_cases=30,
    )

    assert decision.confidence == "verified"
    assert decision.reason == "single-case replay verification is sufficient"
    assert decision.verification_split == "single_case_replay"
    assert decision.verification_mode == "single_case_replay"
    assert decision.held_out_case_count == 0
    assert decision.baseline_replay_count == 2
    assert decision.candidate_replay_count == 3


def test_candidate_confidence_counts_multi_member_single_case_replay_metadata() -> None:
    single_case_replay_dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="case-1",
                input="demo",
                metadata={
                    "replay": {
                        "baseline": {
                            "metrics": {
                                "member_count": 4,
                                "repetition_count": 8,
                                "successful_repetition_count": 8,
                            },
                        },
                        "candidate": {
                            "metrics": {
                                "member_count": 4,
                                "repetition_count": 12,
                                "successful_repetition_count": 12,
                            },
                        },
                    }
                },
            ),
        ),
        recipe=DatasetRecipe(
            source={
                "kind": "trajectory_log",
                "case_count": 1,
                "original_case_count": 1,
                "paired_replay": True,
            },
            split_seed="seed",
            splits={"train": ["case-1"], "validation": [], "held_out": []},
            held_out_case_ids=("held-out-1",),
        ),
    )

    decision = determine_candidate_confidence(
        dataset=single_case_replay_dataset,
        validation_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="validation",
        ),
        held_out_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="held_out",
        ),
        min_eval_cases=30,
    )

    assert decision.confidence == "verified"
    assert decision.verification_mode == "single_case_replay"
    assert decision.baseline_replay_count == 8
    assert decision.candidate_replay_count == 12


def test_candidate_confidence_accepts_trajectory_set_validation_with_small_held_out_pool() -> None:
    trajectory_set_dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="case-train", input="train"),
            EvalCase(case_id="case-validation", input="validation"),
            EvalCase(case_id="case-held-out", input="held-out"),
        ),
        recipe=DatasetRecipe(
            source={
                "kind": "trajectory_log",
                "case_count": 3,
                "auto_grouping": {
                    "auto_grouped": True,
                    "selected_case_count": 3,
                },
            },
            split_seed="seed",
            splits={
                "train": ["case-train"],
                "validation": ["case-validation"],
                "held_out": ["case-held-out"],
            },
            held_out_case_ids=("case-held-out",),
        ),
    )

    decision = determine_candidate_confidence(
        dataset=trajectory_set_dataset,
        validation_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="validation",
        ),
        held_out_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="held_out",
        ),
        min_eval_cases=30,
    )

    assert decision.confidence == "verified"
    assert decision.reason == "trajectory-set validation is sufficient"
    assert decision.verification_split == "trajectory_set_validation"
    assert decision.verification_mode == "trajectory_set_validation"
    assert decision.held_out_case_count == 1


def test_candidate_confidence_keeps_single_case_replay_limited_when_repetitions_are_low() -> None:
    single_case_replay_dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="case-1",
                input="demo",
                metadata={
                    "replay": {
                        "baseline": {
                            "metrics": {
                                "repetition_count": 1,
                                "successful_repetition_count": 1,
                            },
                        },
                        "candidate": {
                            "metrics": {
                                "repetition_count": 1,
                                "successful_repetition_count": 1,
                            },
                        },
                    }
                },
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "trajectory_log", "case_count": 1, "paired_replay": True},
            split_seed="seed",
            splits={"train": ["case-1"], "validation": [], "held_out": []},
            held_out_case_ids=(),
        ),
    )

    decision = determine_candidate_confidence(
        dataset=single_case_replay_dataset,
        validation_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="validation",
        ),
        held_out_summary=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score_delta": 0.4, "deterministic_signal": True},
            dataset_split="held_out",
        ),
        min_eval_cases=30,
    )

    assert decision.confidence == "limited"
    assert decision.reason == "insufficient held-out eval cases for verified confidence"
    assert decision.verification_mode == "held_out"
