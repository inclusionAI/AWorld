"""Typed authoritative evaluation execution and budget settlement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from aworld.logs.util import logger
from aworld.self_evolve.budget import (
    BudgetUsage,
    BudgetUsageCompleteness,
    BudgetUsageObservation,
    CandidateAttemptStage,
)
from aworld.self_evolve.campaign_policy import (
    gate_has_candidate_owned_repair,
    is_verified_apply_policy,
)
from aworld.self_evolve.challenger import ChallengeReport
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.controllers.run_evaluation_admission import (
    CandidateEvaluationAdmissionResult,
    can_reuse_single_case_replay_validation,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
)
from aworld.self_evolve.controllers.run_replay_execution import (
    CandidateReplayExecutionResult,
)
from aworld.self_evolve.controllers.run_regression_execution import (
    RegressionExecution,
    RegressionExecutionRequest,
)
from aworld.self_evolve.controllers.screening_execution import (
    _budget_usage_for_attempt_event,
    _emit_progress,
)
from aworld.self_evolve.controllers.run_telemetry import (
    _stage_telemetry_usage_delta,
    _stage_telemetry_usage_snapshot,
)
from aworld.self_evolve.evaluation import (
    EvaluationBackend,
    EvaluationRequest,
    determine_candidate_confidence,
)
from aworld.self_evolve.evaluation_reporting import (
    _accumulate_score_evidence,
    _evidence_quality_gate,
    _summary_with_replay_evidence_metrics,
)
from aworld.self_evolve.controllers.run_budget_support import (
    _judge_actual_token_usage,
    _same_evaluation_execution,
)
from aworld.self_evolve.gates import (
    CostLatencyRegressionGate,
    EvaluationComparabilityGate,
    EvaluationRuntimeHealthGate,
    GlobalRegressionBenchmarkGate,
    HeldOutVerificationGate,
    JudgeOnlySignalGate,
    RequiredVerificationGate,
    ScoreImprovementGate,
)
from aworld.self_evolve.regression import RegressionEvidence
from aworld.self_evolve.replay_gates import _replay_stability_gate
from aworld.self_evolve.types import EvaluationSummary, GateResult


EvaluatePair = Callable[
    ...,
    Awaitable[tuple[EvaluationSummary, EvaluationSummary]],
]
EvaluateVariant = Callable[..., Awaitable[EvaluationSummary]]
MergeReplayEvidence = Callable[..., EvaluationSummary]
EvidenceQualityGateBuilder = Callable[..., GateResult | None]
AccumulateScoreEvidence = Callable[
    [EvaluationSummary, EvaluationSummary],
    EvaluationSummary,
]
ReplayStabilityGateBuilder = Callable[..., GateResult | None]
SameEvaluationExecution = Callable[
    [EvaluationSummary, EvaluationSummary],
    bool,
]
JudgeActualTokenUsage = Callable[..., tuple[int | None, str]]
IndependentRegressionEvaluator = Callable[
    ...,
    Awaitable[
        tuple[
            RegressionEvidence | None,
            ChallengeReport | None,
            GateResult,
        ]
    ],
]
ReplayInfrastructureFailurePredicate = Callable[[GateResult], bool]


@dataclass(frozen=True)
class CandidateEvaluationExecutionRequest:
    """Frozen evaluation request with replay and budget admission results."""

    evaluation: CandidateEvaluationRequest
    replay: CandidateReplayExecutionResult
    admission: CandidateEvaluationAdmissionResult

    def __post_init__(self) -> None:
        if not isinstance(self.evaluation, CandidateEvaluationRequest):
            raise TypeError("evaluation execution request must be typed")
        if not isinstance(self.replay, CandidateReplayExecutionResult):
            raise TypeError("evaluation execution replay result must be typed")
        if not isinstance(self.admission, CandidateEvaluationAdmissionResult):
            raise TypeError("evaluation execution admission must be typed")
        if self.replay.terminal_result is not None:
            raise ValueError("terminal replay result cannot be evaluated")
        if self.admission.terminal_result is not None:
            raise ValueError("terminal evaluation admission cannot be executed")


@dataclass(frozen=True)
class CandidateEvaluationExecutionPolicy:
    """Runner-owned policy for authoritative evaluation execution."""

    evaluation_backend: EvaluationBackend | None
    max_iterations: int
    min_score_delta: float
    replay_stability_margin: float
    min_eval_cases: int
    require_resource_evidence: bool

    def __post_init__(self) -> None:
        for field_name in ("max_iterations", "min_eval_cases"):
            value = getattr(self, field_name)
            minimum = 1 if field_name == "max_iterations" else 0
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
            ):
                raise ValueError(f"{field_name} must be at least {minimum}")


@dataclass(frozen=True)
class CandidateEvaluationExecutionRuntime:
    """Injected execution services and historical helper seams."""

    task_batch_executor: object
    max_concurrency: int
    execution_telemetry: SelfEvolveExecutionTelemetry
    progress_callback: Callable[[str, str], Any] | None
    evaluate_pair: EvaluatePair
    evaluate_variant: EvaluateVariant
    evaluate_independent_regression: RegressionExecution | IndependentRegressionEvaluator
    gate_is_replay_infrastructure_failure: (
        ReplayInfrastructureFailurePredicate
    )
    merge_replay_evidence: MergeReplayEvidence = _summary_with_replay_evidence_metrics
    evidence_quality_gate: EvidenceQualityGateBuilder = _evidence_quality_gate
    accumulate_score_evidence: AccumulateScoreEvidence = _accumulate_score_evidence
    replay_stability_gate: ReplayStabilityGateBuilder = _replay_stability_gate
    same_evaluation_execution: SameEvaluationExecution = _same_evaluation_execution
    judge_actual_token_usage: JudgeActualTokenUsage = _judge_actual_token_usage

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_concurrency, bool)
            or not isinstance(self.max_concurrency, int)
            or self.max_concurrency < 1
        ):
            raise ValueError("max_concurrency must be a positive integer")
        for field_name in (
            "evaluate_pair",
            "evaluate_variant",
            "merge_replay_evidence",
            "evidence_quality_gate",
            "accumulate_score_evidence",
            "replay_stability_gate",
            "same_evaluation_execution",
            "judge_actual_token_usage",
            "gate_is_replay_infrastructure_failure",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")
        if not isinstance(self.evaluate_independent_regression, RegressionExecution) and (
            not callable(self.evaluate_independent_regression)
        ):
            raise TypeError(
                "evaluate_independent_regression must be a typed execution or callable"
            )
        if not isinstance(
            self.execution_telemetry,
            SelfEvolveExecutionTelemetry,
        ):
            raise TypeError("execution_telemetry must be typed")


@dataclass(frozen=True)
class CandidateEvaluationExecutionResult:
    """Authoritative evaluation evidence after reservation settlement."""

    gate_results: tuple[GateResult, ...]
    baseline_summary: EvaluationSummary | None
    candidate_summary: EvaluationSummary | None
    held_out_summary: EvaluationSummary | None
    regression_evidence: RegressionEvidence | None
    challenge_report: ChallengeReport | None
    fresh_evaluation_completed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_results", tuple(self.gate_results))
        if not all(isinstance(gate, GateResult) for gate in self.gate_results):
            raise TypeError("evaluation execution gate_results must be typed")
        for field_name in (
            "baseline_summary",
            "candidate_summary",
            "held_out_summary",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, EvaluationSummary):
                raise TypeError(f"{field_name} must be typed when present")


async def execute_candidate_evaluation(
    request: CandidateEvaluationExecutionRequest,
    policy: CandidateEvaluationExecutionPolicy,
    runtime: CandidateEvaluationExecutionRuntime,
) -> CandidateEvaluationExecutionResult:
    """Execute evaluation, tie-break, held-out, regression, and settlement."""

    evaluation = request.evaluation
    replay = request.replay
    admission = request.admission
    gate_results = list(admission.gate_results)
    baseline_summary: EvaluationSummary | None = None
    candidate_summary: EvaluationSummary | None = None
    held_out_summary: EvaluationSummary | None = None
    regression_evidence: RegressionEvidence | None = None
    challenge_report: ChallengeReport | None = None
    score_tiebreak_budget_summaries: tuple[EvaluationSummary, ...] = ()
    fresh_evaluation_completed = False
    expected_judge_summary_count = admission.expected_judge_summary_count
    backend = policy.evaluation_backend
    budget_context = evaluation.budget_context

    if backend is not None:
        if not admission.replay_blocked_verified_apply:
            tracker = evaluation.attempt_tracker
            attempt_key = evaluation.attempt_key
            if tracker is not None and attempt_key is not None:
                tracker.emit(
                    attempt_key,
                    CandidateAttemptStage.EVALUATION,
                    case_count=admission.evaluation_case_count,
                    usage=(
                        _budget_usage_for_attempt_event(
                            admission.evaluation_budget
                        )
                        if admission.evaluation_budget is not None
                        else None
                    ),
                )
            evaluation_telemetry_before = _stage_telemetry_usage_snapshot(
                runtime.execution_telemetry,
                "evaluation",
            )
            try:
                _emit_progress(
                    runtime.progress_callback,
                    "evaluation",
                    (
                        "Evaluating baseline and candidate "
                        f"for iteration {evaluation.iteration_number}/"
                        f"{policy.max_iterations} candidate "
                        f"{evaluation.candidate_number}/"
                        f"{evaluation.candidate_count}"
                    ),
                )
                baseline_summary, candidate_summary = await runtime.evaluate_pair(
                    backend,
                    dataset=admission.evaluation_dataset,
                    candidate=evaluation.candidate,
                    dataset_split="validation",
                    artifact_namespace=evaluation.run_id,
                    task_batch_executor=runtime.task_batch_executor,
                    max_concurrency=runtime.max_concurrency,
                    execution_telemetry=runtime.execution_telemetry,
                    baseline_cache=evaluation.baseline_evaluation_cache,
                )
                if replay.replay_result is not None:
                    baseline_summary = runtime.merge_replay_evidence(
                        baseline_summary,
                        replay.replay_result.baseline,
                    )
                    candidate_summary = runtime.merge_replay_evidence(
                        candidate_summary,
                        replay.replay_result.candidate,
                    )
                validation_health_gate = EvaluationRuntimeHealthGate().evaluate(
                    (baseline_summary, candidate_summary)
                )
                if not validation_health_gate.passed:
                    fresh_evaluation_completed = False
                    gate_results.append(validation_health_gate)
                else:
                    score_gate = ScoreImprovementGate(
                        min_delta=policy.min_score_delta
                    ).evaluate(
                        baseline=baseline_summary,
                        candidate=candidate_summary,
                    )
                    pre_tiebreak_evidence_gate = runtime.evidence_quality_gate(
                        candidate_summary,
                        baseline=baseline_summary,
                    )
                    tiebreak_candidate_repair_required = bool(
                        pre_tiebreak_evidence_gate is not None
                        and not pre_tiebreak_evidence_gate.passed
                        and gate_has_candidate_owned_repair(
                            pre_tiebreak_evidence_gate
                        )
                    )
                    if (
                        is_verified_apply_policy(evaluation.apply_policy)
                        and evaluation.allow_score_tiebreak
                        and not tiebreak_candidate_repair_required
                        and not score_gate.passed
                        and isinstance(score_gate.details, Mapping)
                        and score_gate.details.get("tiebreak_eligible") is True
                    ):
                        _emit_progress(
                            runtime.progress_callback,
                            "evaluation",
                            (
                                "Running bounded score tie-break for candidate "
                                f"{evaluation.candidate_number}/"
                                f"{evaluation.candidate_count}"
                            ),
                        )
                        initial_score_gate = score_gate
                        initial_baseline_summary = baseline_summary
                        initial_candidate_summary = candidate_summary
                        (
                            tiebreak_baseline,
                            tiebreak_candidate,
                        ) = await runtime.evaluate_pair(
                            backend,
                            dataset=admission.evaluation_dataset,
                            candidate=evaluation.candidate,
                            dataset_split="validation",
                            artifact_namespace=(
                                f"{evaluation.run_id}-score-tiebreak-1-"
                                f"{evaluation.candidate.candidate_id}"
                            ),
                            task_batch_executor=runtime.task_batch_executor,
                            max_concurrency=runtime.max_concurrency,
                            execution_telemetry=runtime.execution_telemetry,
                        )
                        expected_judge_summary_count += 2
                        if replay.replay_result is not None:
                            tiebreak_baseline = runtime.merge_replay_evidence(
                                tiebreak_baseline,
                                replay.replay_result.baseline,
                            )
                            tiebreak_candidate = runtime.merge_replay_evidence(
                                tiebreak_candidate,
                                replay.replay_result.candidate,
                            )
                        tiebreak_health = EvaluationRuntimeHealthGate().evaluate(
                            (tiebreak_baseline, tiebreak_candidate)
                        )
                        if tiebreak_health.passed:
                            accumulated_baseline = (
                                runtime.accumulate_score_evidence(
                                    initial_baseline_summary,
                                    tiebreak_baseline,
                                )
                            )
                            accumulated_candidate = (
                                runtime.accumulate_score_evidence(
                                    initial_candidate_summary,
                                    tiebreak_candidate,
                                )
                            )
                            score_gate = ScoreImprovementGate(
                                min_delta=policy.min_score_delta
                            ).evaluate(
                                baseline=accumulated_baseline,
                                candidate=accumulated_candidate,
                            )
                            score_gate = replace(
                                score_gate,
                                details={
                                    **dict(score_gate.details or {}),
                                    "tiebreak_round": 1,
                                    "initial_decision": dict(
                                        initial_score_gate.details or {}
                                    ),
                                    "initial_baseline_execution_id": (
                                        initial_baseline_summary.metrics.get(
                                            "evaluation_execution_id"
                                        )
                                    ),
                                    "initial_candidate_execution_id": (
                                        initial_candidate_summary.metrics.get(
                                            "evaluation_execution_id"
                                        )
                                    ),
                                },
                            )
                            baseline_summary = accumulated_baseline
                            candidate_summary = accumulated_candidate
                            score_tiebreak_budget_summaries = (
                                initial_baseline_summary,
                                initial_candidate_summary,
                            )
                        else:
                            score_tiebreak_budget_summaries = (
                                tiebreak_baseline,
                                tiebreak_candidate,
                            )
                            gate_results.append(
                                replace(
                                    tiebreak_health,
                                    gate_name="score_tiebreak_runtime_health",
                                )
                            )
                    elif (
                        is_verified_apply_policy(evaluation.apply_policy)
                        and not score_gate.passed
                        and isinstance(score_gate.details, Mapping)
                        and score_gate.details.get("tiebreak_eligible") is True
                    ):
                        score_gate = replace(
                            score_gate,
                            details={
                                **dict(score_gate.details),
                                "tiebreak_skipped": True,
                                "tiebreak_skip_reason": (
                                    "candidate_repair_required_before_tiebreak"
                                    if tiebreak_candidate_repair_required
                                    else (
                                        "score_tiebreak_candidate_limit_reached"
                                    )
                                ),
                            },
                        )
                    quality_gates: list[GateResult] = [
                        EvaluationComparabilityGate().evaluate(
                            baseline=baseline_summary,
                            candidate=candidate_summary,
                        ),
                        score_gate,
                        CostLatencyRegressionGate(
                            max_cost_regression_ratio=0.25,
                            max_latency_regression_ratio=0.5,
                            require_resource_evidence=(
                                policy.require_resource_evidence
                            ),
                        ).evaluate(
                            baseline=baseline_summary,
                            candidate=candidate_summary,
                        ),
                    ]
                    replay_stability_gate = runtime.replay_stability_gate(
                        baseline_summary=baseline_summary,
                        candidate_summary=candidate_summary,
                        min_score_delta=policy.min_score_delta,
                        replay_stability_margin=policy.replay_stability_margin,
                        replay_used=replay.replay_dataset is not None,
                    )
                    if replay_stability_gate is not None:
                        quality_gates.append(replay_stability_gate)
                if (
                    validation_health_gate.passed
                    and is_verified_apply_policy(evaluation.apply_policy)
                ):
                    if can_reuse_single_case_replay_validation(
                        admission.evaluation_dataset
                    ):
                        logger.info(
                            "self_evolve.evaluator.held_out.skip "
                            f"run_id={evaluation.run_id} "
                            f"candidate_id={evaluation.candidate.candidate_id} "
                            "reason=single_case_replay_validation_reused"
                        )
                        held_out_summary = replace(
                            candidate_summary,
                            dataset_split="single_case_replay",
                            metrics={
                                **dict(candidate_summary.metrics),
                                "evaluation_evidence_role": "held_out_alias",
                                "evaluation_alias_of_execution_id": (
                                    candidate_summary.metrics.get(
                                        "evaluation_execution_id"
                                    )
                                ),
                                "evaluation_fresh_execution": False,
                                "evaluation_reused": True,
                            },
                        )
                    else:
                        held_out_summary = await runtime.evaluate_variant(
                            backend,
                            request=EvaluationRequest(
                                variant_id=evaluation.candidate.candidate_id,
                                candidate=evaluation.candidate,
                                dataset=admission.evaluation_dataset,
                                dataset_split="held_out",
                                artifact_namespace=evaluation.run_id,
                            ),
                            task_batch_executor=runtime.task_batch_executor,
                            execution_telemetry=runtime.execution_telemetry,
                        )
                        if replay.replay_result is not None:
                            held_out_summary = runtime.merge_replay_evidence(
                                held_out_summary,
                                replay.replay_result.candidate,
                            )
                    final_health_gate = EvaluationRuntimeHealthGate().evaluate(
                        (
                            baseline_summary,
                            candidate_summary,
                            held_out_summary,
                        )
                    )
                    gate_results.append(final_health_gate)
                    fresh_evaluation_completed = final_health_gate.passed
                    if final_health_gate.passed:
                        confidence = determine_candidate_confidence(
                            dataset=admission.evaluation_dataset,
                            validation_summary=candidate_summary,
                            held_out_summary=held_out_summary,
                            min_eval_cases=policy.min_eval_cases,
                        )
                        evidence_quality_gates: list[GateResult] = []
                        candidate_evidence_gate = runtime.evidence_quality_gate(
                            candidate_summary,
                            baseline=baseline_summary,
                        )
                        if candidate_evidence_gate is not None:
                            evidence_quality_gates.append(
                                candidate_evidence_gate
                            )
                        if not runtime.same_evaluation_execution(
                            candidate_summary,
                            held_out_summary,
                        ):
                            held_out_evidence_gate = (
                                runtime.evidence_quality_gate(held_out_summary)
                            )
                            if held_out_evidence_gate is not None:
                                evidence_quality_gates.append(
                                    held_out_evidence_gate
                                )
                        pre_regression_gates = [
                            *quality_gates,
                            *evidence_quality_gates,
                            RequiredVerificationGate().evaluate(
                                held_out_summary
                            ),
                            HeldOutVerificationGate(
                                min_eval_cases=policy.min_eval_cases
                            ).evaluate(confidence),
                            JudgeOnlySignalGate().evaluate(confidence),
                        ]
                        gate_results.extend(pre_regression_gates)
                        if all(gate.passed for gate in pre_regression_gates):
                            regression_executor = runtime.evaluate_independent_regression
                            if isinstance(regression_executor, RegressionExecution):
                                regression_result = await regression_executor.execute(
                                    RegressionExecutionRequest(
                                        run_id=evaluation.run_id,
                                        target=evaluation.target,
                                        selection_dataset=evaluation.dataset,
                                        candidate=evaluation.candidate,
                                        apply_policy=evaluation.apply_policy,
                                        budget_context=budget_context,
                                    )
                                )
                                (
                                    regression_evidence,
                                    challenge_report,
                                    challenger_gate,
                                ) = regression_result.as_tuple()
                            else:
                                (
                                    regression_evidence,
                                    challenge_report,
                                    challenger_gate,
                                ) = await regression_executor(
                                    run_id=evaluation.run_id,
                                    target=evaluation.target,
                                    selection_dataset=evaluation.dataset,
                                    candidate=evaluation.candidate,
                                    apply_policy=evaluation.apply_policy,
                                    budget_context=budget_context,
                                )
                            gate_results.append(challenger_gate)
                            gate_results.append(
                                GlobalRegressionBenchmarkGate().evaluate(
                                    evaluation.candidate,
                                    regression_evidence,
                                )
                            )
                elif validation_health_gate.passed:
                    fresh_evaluation_completed = True
                    gate_results.extend(
                        [validation_health_gate, *quality_gates]
                    )
            except Exception as exc:
                gate_results.append(
                    GateResult(
                        gate_name="evaluation",
                        passed=False,
                        reason="evaluation backend failed",
                        details={
                            "failure_class": "infrastructure",
                            "code": "evaluation_infrastructure_error",
                            "type": type(exc).__name__,
                            "reason": str(exc),
                        },
                    )
                )
            finally:
                if admission.evaluation_budget is not None:
                    if budget_context is None:
                        raise RuntimeError(
                            "evaluation reservation requires budget context"
                        )
                    evaluation_telemetry_after = (
                        _stage_telemetry_usage_snapshot(
                            runtime.execution_telemetry,
                            "evaluation",
                        )
                    )
                    evaluation_usage = _stage_telemetry_usage_delta(
                        evaluation_telemetry_before,
                        evaluation_telemetry_after,
                    )
                    budget_context.debit(
                        admission.evaluation_budget,
                        usage_observation=evaluation_usage.observation,
                        actual_source=evaluation_usage.source,
                    )
                if admission.judge_budget is not None:
                    if budget_context is None:
                        raise RuntimeError(
                            "judge reservation requires budget context"
                        )
                    regression_judge_summaries = tuple(
                        summary
                        for result in (
                            regression_evidence.suite_results
                            if regression_evidence is not None
                            else ()
                        )
                        for summary in (
                            result.baseline_summary,
                            result.candidate_summary,
                        )
                        if result.fresh_execution
                    )
                    judge_tokens, judge_source = (
                        runtime.judge_actual_token_usage(
                            baseline_summary,
                            candidate_summary,
                            held_out_summary,
                            *score_tiebreak_budget_summaries,
                            *regression_judge_summaries,
                            expected_summary_count=(
                                expected_judge_summary_count
                                + len(regression_judge_summaries)
                            ),
                        )
                    )
                    if (
                        judge_tokens is not None
                        and judge_source.startswith("known_lower_bound_")
                    ):
                        budget_context.debit(
                            admission.judge_budget,
                            usage_observation=BudgetUsageObservation(
                                known_lower_bound=BudgetUsage(
                                    tokens=judge_tokens,
                                ),
                                completeness=(
                                    BudgetUsageCompleteness.incomplete()
                                ),
                            ),
                            actual_source=judge_source,
                        )
                    else:
                        budget_context.debit(
                            admission.judge_budget,
                            tokens=judge_tokens,
                            actual_source=judge_source,
                        )
    elif (
        is_verified_apply_policy(evaluation.apply_policy)
        or evaluation.source_disposition.requires_fresh_evaluation
    ):
        gate_results.append(
            GateResult(
                gate_name=(
                    "evaluator_rerun_evaluation"
                    if evaluation.source_disposition.requires_fresh_evaluation
                    else "auto_verified_evaluation"
                ),
                passed=False,
                reason=(
                    "stored-evidence evaluator rerun requires evaluation backend"
                    if evaluation.source_disposition.requires_fresh_evaluation
                    else "auto_verified apply policy requires evaluation backend"
                ),
                details={
                    "failure_class": "infrastructure",
                    "code": "evaluation_backend_missing",
                },
            )
        )

    if (
        evaluation.source_disposition.requires_fresh_evaluation
        and not any(
            not gate.passed
            and runtime.gate_is_replay_infrastructure_failure(gate)
            for gate in gate_results
        )
    ):
        gate_results.append(
            GateResult(
                gate_name="fresh_evaluator_rerun",
                passed=(
                    fresh_evaluation_completed
                    and baseline_summary is not None
                    and candidate_summary is not None
                ),
                reason=(
                    "fresh baseline and candidate evaluation completed"
                    if fresh_evaluation_completed
                    else "fresh baseline and candidate evaluation did not complete"
                ),
                details={
                    "source_disposition": (
                        evaluation.source_disposition.to_dict()
                    ),
                    "failure_class": (
                        None if fresh_evaluation_completed else "infrastructure"
                    ),
                    "code": (
                        None
                        if fresh_evaluation_completed
                        else "fresh_evaluation_not_completed"
                    ),
                },
            )
        )

    return CandidateEvaluationExecutionResult(
        gate_results=tuple(gate_results),
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        held_out_summary=held_out_summary,
        regression_evidence=regression_evidence,
        challenge_report=challenge_report,
        fresh_evaluation_completed=fresh_evaluation_completed,
    )
