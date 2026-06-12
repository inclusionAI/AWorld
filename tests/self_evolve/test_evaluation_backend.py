from __future__ import annotations

from dataclasses import dataclass

import pytest

from aworld.config.conf import EvaluationConfig
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.evaluation import (
    CandidateConfidenceDecision,
    CommandVerificationBackend,
    EvaluateRunnerBackend,
    EvaluationBackend,
    EvaluationRequest,
    determine_candidate_confidence,
    estimate_replay_cost,
    evaluate_baseline_and_candidate,
)
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
        estimated_tokens_per_replay=100,
        estimated_cost_usd_per_replay=0.01,
        max_run_tokens=2_000,
        max_run_cost_usd=1.0,
    )

    assert estimate.passed is True
    assert estimate.baseline_replay_count == 3
    assert estimate.candidate_replay_count == 6
    assert estimate.total_replay_count == 9
    assert estimate.verification_command_count == 6
    assert estimate.judge_call_count == 18
    assert estimate.estimated_tokens == 900
    assert estimate.estimated_cost_usd == 0.09

    over_budget = estimate_replay_cost(
        dataset=dataset,
        candidate_count=2,
        judge_repetitions=3,
        estimated_tokens_per_replay=100,
        max_run_tokens=800,
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
