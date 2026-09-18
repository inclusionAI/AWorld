from __future__ import annotations

import asyncio
import statistics
from dataclasses import replace
from pathlib import Path

import pytest

from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.controllers.run_budget_support import _judge_actual_token_usage
from aworld.self_evolve.controllers.run_challenge_execution import (
    ChallengeExecution,
    ChallengeExecutionPolicy,
    ChallengeExecutionRuntime,
)
from aworld.self_evolve.controllers.run_regression_execution import (
    RegressionExecutionPolicy,
    RegressionExecutionRequest,
    RegressionExecutionRuntime,
    RegressionReplayExecution,
    RegressionReplayResult,
    execute_independent_regression,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.evaluation import EvaluationRequest, evaluation_request_identity
from aworld.self_evolve.regression import (
    RegressionSuiteSpec,
    ResolvedRegressionSuite,
    dataset_case_fingerprints,
)
from aworld.self_evolve.replay import replay_dataset_fingerprint
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
)


# The six ordered scores reproduce the observed two-case, three-judge regression.
INITIAL_SCORES = (
    [95.0, 89.4, 95.0, 91.0, 92.0, 93.0],
    [98.0, 88.8, 98.0, 90.6, 98.0, 86.2],
)
POSITIVE_SCORES = ([90.0] * 6, [94.0] * 6)


def _dataset(*case_ids: str) -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=name, input=f"Task {name}") for name in case_ids),
        recipe=DatasetRecipe(
            source={"kind": "tiebreak-test"},
            split_seed="seed",
            splits={"train": list(case_ids)},
            trainable_case_ids=case_ids,
        ),
    )


class _Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        policy="verified_only",
        initial=INITIAL_SCORES,
        additional=POSITIVE_SCORES,
        mutate=None,
        error=None,
    ):
        path = tmp_path / "SKILL.md"
        path.write_text("# Demo\n")
        target = SkillTextTarget(path)
        candidate = CandidateVariant(
            candidate_id="candidate-1",
            target=target.identity,
            content="# Improved demo\n",
            rationale="test",
            target_fingerprint=target.fingerprint_current_content(),
        )
        self.request = RegressionExecutionRequest(
            "run-tiebreak", target, _dataset("selection"), candidate, policy, None
        )
        dataset = _dataset("case-a", "case-b")
        self.suite = ResolvedRegressionSuite(
            RegressionSuiteSpec(
                suite_id="contract",
                source_kind="jsonl",
                source_ref="contract.jsonl",
                source_version="sha256:source",
                dataset_fingerprint=replay_dataset_fingerprint(dataset),
                split_fingerprint="sha256:split",
                case_fingerprints=dataset_case_fingerprints(dataset),
            ),
            dataset,
        )
        self.calls = []
        self.raw = []
        self.replay_count = 0
        self.initial, self.additional, self.mutate, self.error = (
            initial,
            additional,
            mutate,
            error,
        )
        store = FilesystemSelfEvolveStore(tmp_path / "store")
        self.runtime = RegressionExecutionRuntime(
            store=store,
            challenge=ChallengeExecution(
                ChallengeExecutionPolicy(False, 1, ()),
                ChallengeExecutionRuntime(store, None),
            ),
            regression_backend=object(),
            regression_replay_backend=object(),
            selection_backend=None,
            replay=RegressionReplayExecution(self.replay),
            task_batch_executor=object(),
            max_concurrency=1,
            execution_telemetry=SelfEvolveExecutionTelemetry(),
            evaluate_pair=self.evaluate,
        )

    async def replay(self, request):
        self.replay_count += 1
        return RegressionReplayResult(
            request.dataset, GateResult("candidate_replay", True, "comparable")
        )

    async def evaluate(self, backend, **kwargs):
        index = len(self.calls)
        self.calls.append((backend, kwargs))
        if index and self.error is not None:
            raise self.error
        scores = self.initial if not index else self.additional
        summaries = []
        for role, samples in zip(("baseline", "candidate"), scores):
            candidate = kwargs["candidate"] if role == "candidate" else None
            variant = candidate.candidate_id if candidate else "baseline"
            identity = evaluation_request_identity(
                backend,
                EvaluationRequest(
                    variant,
                    candidate,
                    kwargs["dataset"],
                    dataset_split="regression",
                    preserve_case_cardinality=True,
                ),
                baseline_target_fingerprint=kwargs["candidate"].target_fingerprint,
            )
            metrics = {
                "score": statistics.mean(samples),
                "score_samples": list(samples),
                "comparison_plan_fingerprint": "sha256:plan",
                "comparison_case_ids": ["case-a", "case-b"],
                "comparison_effective_case_count": 2,
                "comparison_case_count": 2,
                "comparison_cardinality_preserved": True,
                "evaluation_identity": identity.to_dict(),
                "evaluation_identity_fingerprint": identity.fingerprint,
                "evaluation_execution_id": f"{role}-{index}",
                "evaluation_fresh_execution": True,
                "evaluation_reused": False,
                "evaluation_reused_from_execution_id": None,
                "judge_attempt_count": 3,
                "judge_success_count": 3,
                "judge_failure_count": 0,
                "judge_timeout_count": 0,
                "judge_total_tokens": 100 + index,
                "cost_usd": 1.0,
                "latency_ms": 10.0,
            }
            summary = EvaluationSummary(variant, metrics, "regression")
            if self.mutate is not None:
                summary = self.mutate(index, role, summary)
            summaries.append(summary)
        self.raw.extend(summaries)
        return tuple(summaries)

    async def run(self):
        result = await execute_independent_regression(
            self.request,
            RegressionExecutionPolicy(True, 1, 1, (self.suite,)),
            self.runtime,
        )
        assert result.evidence is not None
        return result.evidence.suite_results[0]


def _gate(result, name="score_improvement"):
    return next(gate for gate in result.gate_results if gate.gate_name == name)


@pytest.mark.asyncio
async def test_regression_tiebreak_pools_all_samples_without_replay(tmp_path):
    harness = _Harness(tmp_path)
    result = await harness.run()
    assert len(harness.calls) == 2
    assert harness.replay_count == 1
    assert harness.calls[0][0] is harness.calls[1][0]
    for key in ("candidate", "dataset"):
        assert harness.calls[0][1][key] is harness.calls[1][1][key]
    assert (
        harness.calls[0][1]["artifact_namespace"]
        != harness.calls[1][1]["artifact_namespace"]
    )
    assert result.passed
    assert (
        result.baseline_summary.metrics["score_samples"]
        == INITIAL_SCORES[0] + POSITIVE_SCORES[0]
    )
    assert (
        result.candidate_summary.metrics["score_samples"]
        == INITIAL_SCORES[1] + POSITIVE_SCORES[1]
    )
    assert _gate(result).details["paired_sample_count"] == 12
    assert _gate(result).details["tiebreak_round"] == 1
    assert _gate(result).details["initial_decision"]["decision"] == "inconclusive"
    assert result.judge_summaries == tuple(harness.raw)
    assert len(result.to_dict()["evaluation_summaries"]) == 4
    assert _judge_actual_token_usage(*result.judge_summaries) == (
        402,
        "judge_total_tokens",
    )


@pytest.mark.asyncio
async def test_regression_tiebreak_still_inconclusive_is_bounded(tmp_path):
    harness = _Harness(tmp_path, additional=([90.0] * 6, [99.7, 81.7] * 3))
    result = await harness.run()
    assert len(harness.calls) == 2
    assert not result.passed
    assert _gate(result).details["decision"] == "inconclusive"
    assert _gate(result).details["paired_sample_count"] == 12
    assert len(result.judge_summaries) == 4


@pytest.mark.parametrize(
    "condition", ["negative", "passed", "health", "cost", "latency", "proposal"]
)
@pytest.mark.asyncio
async def test_regression_tiebreak_does_not_evaluate_ineligible_panels(
    tmp_path, condition
):
    initial = INITIAL_SCORES
    if condition == "negative":
        initial = ([90.0] * 6, [95.0, 83.0] * 3)
    elif condition == "passed":
        initial = POSITIVE_SCORES

    def mutate(index, role, summary):
        metrics = dict(summary.metrics)
        if condition == "health":
            metrics.update(judge_success_count=0, judge_failure_count=3)
        elif condition == "cost" and role == "candidate":
            metrics["cost_usd"] = 2.0
        elif condition == "latency" and role == "candidate":
            metrics["latency_ms"] = 20.0
        return replace(summary, metrics=metrics)

    harness = _Harness(
        tmp_path,
        policy="proposal" if condition == "proposal" else "verified_only",
        initial=initial,
        mutate=mutate,
    )
    result = await harness.run()
    assert len(harness.calls) == 1
    assert result.judge_summaries == (result.baseline_summary, result.candidate_summary)
    assert result.evaluation_summaries == ()
    assert "evaluation_summaries" not in result.to_dict()


@pytest.mark.parametrize(
    "change",
    [
        "identity",
        "backend",
        "variant_identity",
        "dataset_identity",
        "plan",
        "case_order",
        "fresh",
        "reused",
        "execution",
        "execution_alias",
        "case_count",
        "variant",
        "samples",
    ],
)
@pytest.mark.asyncio
async def test_regression_tiebreak_rejects_incompatible_additional_evidence(
    tmp_path, change
):
    def mutate(index, role, summary):
        if index != 1 or role != "candidate":
            return summary
        metrics = dict(summary.metrics)
        if change in {"backend", "variant_identity", "dataset_identity"}:
            key = {
                "backend": "backend_fingerprint",
                "variant_identity": "variant_fingerprint",
                "dataset_identity": "dataset_fingerprint",
            }[change]
            metrics["evaluation_identity"] = {
                **metrics["evaluation_identity"],
                key: "sha256:other",
            }
        else:
            changes = {
                "identity": ("evaluation_identity_fingerprint", "sha256:other"),
                "plan": ("comparison_plan_fingerprint", "sha256:other"),
                "case_order": ("comparison_case_ids", ["case-b", "case-a"]),
                "fresh": ("evaluation_fresh_execution", False),
                "reused": ("evaluation_reused", True),
                "execution": ("evaluation_execution_id", "candidate-0"),
                "execution_alias": ("evaluation_alias_of_execution_id", "candidate-0"),
                "case_count": ("comparison_case_count", 1),
                "samples": ("score_samples", [94.0, float("nan")]),
            }
            if change in changes:
                key, value = changes[change]
                metrics[key] = value
        return replace(
            summary,
            metrics=metrics,
            variant_id="other" if change == "variant" else summary.variant_id,
        )

    harness = _Harness(tmp_path, mutate=mutate)
    result = await harness.run()
    assert len(harness.calls) == 2
    assert not result.passed
    assert result.baseline_summary is harness.raw[0]
    assert result.candidate_summary is harness.raw[1]
    assert _gate(result).details["decision"] == "inconclusive"
    failure = _gate(result, "regression_score_tiebreak_measurement")
    assert not failure.passed
    assert failure.details["failure_owner"] == "framework"
    assert failure.details["repairable"] is False
    assert result.judge_summaries == tuple(harness.raw)


@pytest.mark.asyncio
async def test_regression_tiebreak_failed_runtime_preserves_initial_score(tmp_path):
    def mutate(index, role, summary):
        if index == 1:
            return replace(
                summary,
                metrics={
                    **summary.metrics,
                    "judge_success_count": 0,
                    "judge_failure_count": 3,
                },
            )
        return summary

    harness = _Harness(tmp_path, mutate=mutate)
    result = await harness.run()
    assert len(harness.calls) == 2
    assert result.candidate_summary is harness.raw[1]
    assert not result.passed
    assert _gate(result).details["decision"] == "inconclusive"
    assert not _gate(result, "regression_score_tiebreak_runtime_health").passed
    assert result.judge_summaries == tuple(harness.raw)


@pytest.mark.asyncio
async def test_regression_tiebreak_exception_preserves_missing_round_accounting(
    tmp_path,
):
    harness = _Harness(tmp_path, error=RuntimeError("judge failed"))
    result = await harness.run()
    assert len(harness.calls) == 2
    assert not result.passed
    assert result.candidate_summary is harness.raw[1]
    assert _gate(result).details["decision"] == "inconclusive"
    assert not _gate(result, "regression_score_tiebreak_execution").passed
    assert result.judge_summaries[:2] == tuple(harness.raw)
    assert len(result.judge_summaries) == 4
    assert (
        len({s.metrics["evaluation_execution_id"] for s in result.judge_summaries}) == 4
    )
    assert all(
        s.metrics["evaluation_summary_missing"] for s in result.judge_summaries[2:]
    )
    assert all(
        "judge_total_tokens" not in s.metrics for s in result.judge_summaries[2:]
    )
    tokens, source = _judge_actual_token_usage(*result.judge_summaries)
    assert tokens == 200
    assert source.startswith("known_lower_bound_incomplete_judge_telemetry:")


@pytest.mark.asyncio
async def test_regression_tiebreak_additional_cost_failure_keeps_initial(tmp_path):
    def mutate(index, role, summary):
        if index == 1 and role == "candidate":
            return replace(summary, metrics={**summary.metrics, "cost_usd": 2.0})
        return summary

    harness = _Harness(tmp_path, mutate=mutate)
    result = await harness.run()
    assert len(harness.calls) == 2
    assert result.candidate_summary is harness.raw[1]
    assert not result.passed
    assert _gate(result).details["decision"] == "inconclusive"
    assert not _gate(result, "regression_score_tiebreak_cost_latency").passed
    assert result.judge_summaries == tuple(harness.raw)


@pytest.mark.parametrize(
    "change", ["missing_identity", "wrong_candidate", "wrong_backend"]
)
@pytest.mark.asyncio
async def test_regression_tiebreak_initial_identity_failure_skips_new_judging(
    tmp_path, change
):
    def mutate(index, role, summary):
        if role != "candidate":
            return summary
        metrics = dict(summary.metrics)
        if change == "missing_identity":
            metrics.pop("evaluation_identity")
        elif change == "wrong_backend":
            metrics["evaluation_identity"] = {
                **metrics["evaluation_identity"],
                "backend_fingerprint": "sha256:other",
            }
        return replace(
            summary,
            metrics=metrics,
            variant_id="other" if change == "wrong_candidate" else summary.variant_id,
        )

    harness = _Harness(tmp_path, mutate=mutate)
    result = await harness.run()
    assert len(harness.calls) == 1
    assert not result.passed
    assert result.candidate_summary is harness.raw[1]
    assert not _gate(result, "regression_score_tiebreak_measurement").passed
    assert result.evaluation_summaries == ()


@pytest.mark.asyncio
async def test_regression_initial_judge_exception_retains_unknown_usage(tmp_path):
    harness = _Harness(tmp_path)

    async def failing_pair(backend, **kwargs):
        harness.calls.append((backend, kwargs))
        raise RuntimeError("first pair failed after starting")

    harness.runtime = replace(harness.runtime, evaluate_pair=failing_pair)
    result = await harness.run()
    assert len(harness.calls) == 1
    assert harness.replay_count == 1
    assert result.fresh_execution is False
    assert not result.passed
    assert not _gate(result, "independent_regression_execution").passed
    assert len(result.evaluation_summaries) == 2
    assert (
        len({s.metrics["evaluation_execution_id"] for s in result.judge_summaries}) == 2
    )
    assert all(s.metrics["evaluation_summary_missing"] for s in result.judge_summaries)
    assert all(s.metrics["evaluation_fresh_execution"] for s in result.judge_summaries)
    tokens, source = _judge_actual_token_usage(*result.judge_summaries)
    assert tokens == 0
    assert source.startswith("known_lower_bound_incomplete_judge_telemetry:")


@pytest.mark.asyncio
async def test_regression_replay_failure_does_not_invent_judge_usage(tmp_path):
    harness = _Harness(tmp_path)

    async def failing_replay(request):
        harness.replay_count += 1
        raise RuntimeError("replay failed before judging")

    harness.runtime = replace(
        harness.runtime, replay=RegressionReplayExecution(failing_replay)
    )
    result = await harness.run()
    assert len(harness.calls) == 0
    assert harness.replay_count == 1
    assert result.fresh_execution is False
    assert not result.passed
    assert result.evaluation_summaries == ()
    assert all(
        "evaluation_summary_missing" not in s.metrics for s in result.judge_summaries
    )


@pytest.mark.asyncio
async def test_regression_tiebreak_propagates_cancellation(tmp_path):
    harness = _Harness(tmp_path, error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await harness.run()
    assert len(harness.calls) == 2
