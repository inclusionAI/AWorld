from __future__ import annotations

import ast
import inspect
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aworld.self_evolve.budget import (
    BudgetCeilings,
    BudgetEstimateConfidence,
    BudgetEstimateSource,
    BudgetStage,
    CandidateAttemptKey,
    CandidateAttemptStage,
    RunBudgetLedger,
    StageBudgetEstimate,
)
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.controllers import (
    run_evaluation_execution as execution_module,
)
from aworld.self_evolve.controllers.run_evaluation_admission import (
    CandidateEvaluationAdmissionResult,
)
from aworld.self_evolve.controllers.run_evaluation_execution import (
    CandidateEvaluationExecutionPolicy,
    CandidateEvaluationExecutionRequest,
    CandidateEvaluationExecutionRuntime,
    execute_candidate_evaluation,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
)
from aworld.self_evolve.controllers.run_replay_execution import (
    CandidateReplayExecutionResult,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
    SelfEvolveTargetRef,
)


def _dataset() -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input={"content": "task"}),),
        recipe=DatasetRecipe(
            source={"kind": "evaluation-execution-test"},
            split_seed="seed",
            splits={"train": ["case-1"]},
            trainable_case_ids=("case-1",),
        ),
    )


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef("skill", "demo", None),
        content="# Improved\n",
        rationale="exercise evaluation execution",
    )


class _AttemptTracker:
    def __init__(self) -> None:
        self.events: list[CandidateAttemptStage] = []

    def emit(self, _key, stage, **_kwargs):
        self.events.append(stage)


class _BudgetContext:
    def __init__(self) -> None:
        self.debits: list[dict[str, object]] = []

    def debit(self, decision, **kwargs):
        self.debits.append({"decision": decision, **kwargs})


def _decision(stage: BudgetStage, item_id: str):
    ledger = RunBudgetLedger(BudgetCeilings(None, None, None))
    return ledger.reserve(
        StageBudgetEstimate(
            stage=stage,
            item_id=item_id,
            tokens=10,
            cost_usd=Decimal("1"),
            wall_seconds=Decimal("1"),
            source=BudgetEstimateSource.CONFIGURED_COLD_START,
            confidence=BudgetEstimateConfidence.LOW,
        )
    )


def _summary(
    variant_id: str,
    score: float,
    *,
    execution_id: str,
    score_std: float | None = None,
) -> EvaluationSummary:
    metrics: dict[str, object] = {
        "score": score,
        "latency_ms": 10.0,
        "cost_usd": 1.0,
        "evaluation_execution_id": execution_id,
        "judge_success_count": 3,
        "judge_attempt_count": 3,
        "judge_total_tokens": 5,
        "command_case_count": 1,
        "command_pass_count": 1,
        "deterministic_signal": True,
    }
    if score_std is not None:
        metrics["score_std"] = score_std
    return EvaluationSummary(
        variant_id=variant_id,
        metrics=metrics,
        dataset_split="validation",
    )


def _request(
    *,
    apply_policy: str = "proposal",
    budget_context=None,
    attempt_tracker=None,
    evaluation_budget=None,
    judge_budget=None,
    backend=object(),
) -> tuple[
    CandidateEvaluationExecutionRequest,
    CandidateEvaluationExecutionPolicy,
]:
    dataset = _dataset()
    evaluation = CandidateEvaluationRequest(
        run_id="run-1",
        target=SimpleNamespace(),
        dataset=dataset,
        candidate=_candidate(),
        apply_policy=apply_policy,
        target_provenance=None,
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
        attempt_key=(
            CandidateAttemptKey("run-1", 0, 0)
            if attempt_tracker is not None
            else None
        ),
        attempt_tracker=attempt_tracker,
        budget_context=budget_context,
    )
    replay = CandidateReplayExecutionResult(
        gate_results=(
            GateResult("target_behavior_delta", True, "behavior changed"),
        ),
        replay_result=None,
        replay_dataset=None,
        replay_started=False,
    )
    admission = CandidateEvaluationAdmissionResult(
        gate_results=replay.gate_results,
        evaluation_dataset=dataset,
        replay_blocked_verified_apply=False,
        evaluation_budget=evaluation_budget,
        judge_budget=judge_budget,
        expected_judge_summary_count=(2 if backend is not None else 0),
        evaluation_case_count=1,
        baseline_is_cached=False,
        evaluation_units=2 if backend is not None else 0,
    )
    return (
        CandidateEvaluationExecutionRequest(
            evaluation=evaluation,
            replay=replay,
            admission=admission,
        ),
        CandidateEvaluationExecutionPolicy(
            evaluation_backend=backend,
            max_iterations=2,
            min_score_delta=0.0,
            replay_stability_margin=0.0,
            min_eval_cases=0,
            require_resource_evidence=False,
        ),
    )


def _runtime(
    *,
    telemetry: SelfEvolveExecutionTelemetry,
    evaluate_pair,
    evaluate_variant=None,
    accumulate_score_evidence=lambda _first, second: second,
) -> CandidateEvaluationExecutionRuntime:
    async def unused_regression(**_kwargs):
        raise AssertionError("regression should not run in controller unit test")

    async def default_variant(_backend, *, request, **_kwargs):
        return replace_split(
            _summary(
                request.variant_id,
                82.0,
                execution_id="held-out",
            ),
            request.dataset_split,
        )

    return CandidateEvaluationExecutionRuntime(
        task_batch_executor=object(),
        max_concurrency=2,
        execution_telemetry=telemetry,
        progress_callback=None,
        evaluate_pair=evaluate_pair,
        evaluate_variant=evaluate_variant or default_variant,
        merge_replay_evidence=lambda summary, _replay: summary,
        evidence_quality_gate=lambda *_args, **_kwargs: None,
        accumulate_score_evidence=accumulate_score_evidence,
        replay_stability_gate=lambda **_kwargs: None,
        same_evaluation_execution=lambda *_args: False,
        judge_actual_token_usage=(
            lambda *_args, **_kwargs: (11, "test_judge_tokens")
        ),
        evaluate_independent_regression=unused_regression,
        gate_is_replay_infrastructure_failure=lambda _gate: False,
    )


def replace_split(
    summary: EvaluationSummary,
    dataset_split: str,
) -> EvaluationSummary:
    return EvaluationSummary(
        variant_id=summary.variant_id,
        metrics=summary.metrics,
        dataset_split=dataset_split,
    )


@pytest.mark.asyncio
async def test_evaluation_execution_settles_telemetry_and_judge_budget() -> None:
    telemetry = SelfEvolveExecutionTelemetry()
    budget = _BudgetContext()
    tracker = _AttemptTracker()
    evaluation_budget = _decision(
        BudgetStage.EVALUATION,
        "candidate-1-evaluation",
    )
    judge_budget = _decision(BudgetStage.JUDGE, "candidate-1-judge")

    async def evaluate_pair(_backend, **_kwargs):
        telemetry.record(
            "evaluation",
            {
                "item_count": 2,
                "elapsed_seconds": 3,
                "token_usage": {"total_tokens": 17},
            },
        )
        return (
            _summary("baseline", 0.2, execution_id="baseline-1"),
            _summary("candidate-1", 0.8, execution_id="candidate-1"),
        )

    request, policy = _request(
        budget_context=budget,
        attempt_tracker=tracker,
        evaluation_budget=evaluation_budget,
        judge_budget=judge_budget,
    )
    result = await execute_candidate_evaluation(
        request,
        policy,
        _runtime(telemetry=telemetry, evaluate_pair=evaluate_pair),
    )

    assert result.fresh_evaluation_completed is True
    assert result.baseline_summary is not None
    assert tracker.events == [CandidateAttemptStage.EVALUATION]
    assert len(budget.debits) == 2
    evaluation_observation = budget.debits[0]["usage_observation"]
    assert evaluation_observation.known_lower_bound.tokens == 17
    assert budget.debits[1]["tokens"] == 11
    assert budget.debits[1]["actual_source"] == "test_judge_tokens"


@pytest.mark.asyncio
async def test_evaluation_execution_runs_bounded_score_tiebreak() -> None:
    telemetry = SelfEvolveExecutionTelemetry()
    pair_calls: list[str | None] = []

    async def evaluate_pair(_backend, **kwargs):
        pair_calls.append(kwargs.get("artifact_namespace"))
        suffix = len(pair_calls)
        return (
            _summary(
                "baseline",
                80.0,
                execution_id=f"baseline-{suffix}",
                score_std=4.0,
            ),
            _summary(
                "candidate-1",
                82.0,
                execution_id=f"candidate-{suffix}",
                score_std=4.0,
            ),
        )

    request, policy = _request(apply_policy="verified_only")
    result = await execute_candidate_evaluation(
        request,
        policy,
        _runtime(telemetry=telemetry, evaluate_pair=evaluate_pair),
    )

    assert pair_calls == [
        "run-1",
        "run-1-score-tiebreak-1-candidate-1",
    ]
    score_gate = next(
        gate
        for gate in result.gate_results
        if gate.gate_name == "score_improvement"
    )
    assert score_gate.details["tiebreak_round"] == 1


@pytest.mark.asyncio
async def test_evaluation_execution_normalizes_backend_exception() -> None:
    telemetry = SelfEvolveExecutionTelemetry()

    async def failed_pair(_backend, **_kwargs):
        raise RuntimeError("judge unavailable")

    request, policy = _request()
    result = await execute_candidate_evaluation(
        request,
        policy,
        _runtime(telemetry=telemetry, evaluate_pair=failed_pair),
    )

    gate = next(
        gate for gate in result.gate_results if gate.gate_name == "evaluation"
    )
    assert gate.passed is False
    assert gate.details["code"] == "evaluation_infrastructure_error"
    assert gate.details["reason"] == "judge unavailable"


@pytest.mark.asyncio
async def test_evaluation_execution_requires_backend_for_verified_apply() -> None:
    telemetry = SelfEvolveExecutionTelemetry()

    async def must_not_evaluate(_backend, **_kwargs):
        raise AssertionError("missing backend must not evaluate")

    request, policy = _request(
        apply_policy="verified_only",
        backend=None,
    )
    result = await execute_candidate_evaluation(
        request,
        policy,
        _runtime(telemetry=telemetry, evaluate_pair=must_not_evaluate),
    )

    gate = next(gate for gate in result.gate_results if not gate.passed)
    assert gate.gate_name == "auto_verified_evaluation"
    assert gate.details["code"] == "evaluation_backend_missing"


def test_run_evaluation_execution_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(execution_module))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert "aworld.self_evolve.runner" not in imported_modules
