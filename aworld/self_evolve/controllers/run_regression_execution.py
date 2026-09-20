"""Typed independent-regression orchestration and evidence persistence."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace

from aworld.self_evolve.budget import BudgetDecision, BudgetStage
from aworld.self_evolve.campaign_policy import is_verified_apply_policy
from aworld.self_evolve.challenger import ChallengeReport
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.controllers.run_challenge_execution import (
    ChallengeExecution,
    ChallengeExecutionRequest,
)
from aworld.self_evolve.controllers.run_resources import RunBudgetContext
from aworld.self_evolve.controllers.run_observers import safe_emit_progress
from aworld.self_evolve.controllers.run_telemetry import (
    stage_telemetry_usage_delta,
    stage_telemetry_usage_snapshot,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.evaluation import (
    EvaluationBackend,
    EvaluationRequest,
    evaluate_baseline_and_candidate,
    evaluation_request_identity,
)
from aworld.self_evolve.evaluation_reporting import _accumulate_score_evidence
from aworld.self_evolve.failure_events import FailureOwner
from aworld.self_evolve.gates import (
    CostLatencyRegressionGate,
    EvaluationRuntimeHealthGate,
    ScoreImprovementGate,
)
from aworld.self_evolve.regression import (
    RegressionEvidence,
    RegressionSuiteResult,
    ResolvedRegressionSuite,
    dataset_case_fingerprints,
    evaluation_backend_identity,
    regression_execution_id,
)
from aworld.self_evolve.replay import (
    CandidateReplayEvidenceReuseBackend,
    replay_dataset_fingerprint,
)
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, GateResult


# Regression panels verify preservation after the separate selection panel has
# already established improvement.  A three-percent practical margin prevents
# high-scoring (90+) two-case contract panels from demanding a statistically
# significant additional gain merely to prove non-regression.
_REGRESSION_SCORE_NONINFERIORITY_MARGIN = 0.03


@dataclass(frozen=True)
class RegressionExecutionRequest:
    run_id: str
    target: SelfEvolveTarget
    selection_dataset: SelfEvolveDataset
    candidate: CandidateVariant
    apply_policy: str
    budget_context: RunBudgetContext | None


@dataclass(frozen=True)
class RegressionReplayRequest:
    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    candidate: CandidateVariant
    apply_policy: str
    suite_id: str
    lifecycle_callback: Callable[[str, dict[str, object]], None]


@dataclass(frozen=True)
class RegressionReplayResult:
    dataset: SelfEvolveDataset | None
    gate: GateResult | None


RegressionReplayCallable = Callable[
    [RegressionReplayRequest], Awaitable[RegressionReplayResult]
]


@dataclass(frozen=True)
class RegressionReplayExecution:
    """Typed seam isolating regression from paired-replay implementation details."""

    execute_request: RegressionReplayCallable

    async def execute(self, request: RegressionReplayRequest) -> RegressionReplayResult:
        return await self.execute_request(request)


@dataclass(frozen=True)
class RegressionExecutionPolicy:
    replay_enabled: bool
    baseline_replay_repetitions: int
    candidate_replay_repetitions: int
    regression_suites: tuple[ResolvedRegressionSuite, ...]


@dataclass(frozen=True)
class RegressionExecutionRuntime:
    store: FilesystemSelfEvolveStore
    challenge: ChallengeExecution
    regression_backend: EvaluationBackend | None
    regression_replay_backend: object | None
    selection_backend: EvaluationBackend | None
    replay: RegressionReplayExecution | None
    task_batch_executor: object
    max_concurrency: int
    execution_telemetry: SelfEvolveExecutionTelemetry
    progress_callback: Callable[[str, str], object] | None = None
    evaluate_pair: Callable[..., Awaitable[tuple[EvaluationSummary, EvaluationSummary]]] = (
        evaluate_baseline_and_candidate
    )


@dataclass(frozen=True)
class RegressionExecutionResult:
    evidence: RegressionEvidence | None
    challenge_report: ChallengeReport | None
    challenge_gate: GateResult

    def as_tuple(
        self,
    ) -> tuple[RegressionEvidence | None, ChallengeReport | None, GateResult]:
        return self.evidence, self.challenge_report, self.challenge_gate


@dataclass(frozen=True)
class RegressionExecution:
    policy: RegressionExecutionPolicy
    runtime: RegressionExecutionRuntime

    async def execute(
        self,
        request: RegressionExecutionRequest,
    ) -> RegressionExecutionResult:
        return await execute_independent_regression(request, self.policy, self.runtime)


async def execute_independent_regression(
    request: RegressionExecutionRequest,
    policy: RegressionExecutionPolicy,
    runtime: RegressionExecutionRuntime,
) -> RegressionExecutionResult:
    challenge = await runtime.challenge.execute(
        ChallengeExecutionRequest(
            run_id=request.run_id,
            target=request.target,
            candidate=request.candidate,
            budget_context=request.budget_context,
        )
    )
    if not challenge.gate.passed:
        return RegressionExecutionResult(None, challenge.report, challenge.gate)
    if not policy.regression_suites or runtime.regression_backend is None:
        return RegressionExecutionResult(None, challenge.report, challenge.gate)

    challenge_suites = challenge.report.suites if challenge.report is not None else ()
    regression_suites = (*policy.regression_suites, *challenge_suites)
    if challenge_suites:
        runtime.store.write_regression_suite_manifest(
            request.run_id,
            tuple(suite.spec for suite in regression_suites),
        )

    suite_results: list[RegressionSuiteResult] = []
    for suite in regression_suites:
        suite_results.append(
            await _execute_regression_suite(request, policy, runtime, suite)
        )

    evidence = RegressionEvidence(
        candidate_id=request.candidate.candidate_id,
        selection_dataset_fingerprint=replay_dataset_fingerprint(
            request.selection_dataset
        ),
        selection_case_fingerprints=dataset_case_fingerprints(
            request.selection_dataset
        ),
        selection_backend_id=evaluation_backend_identity(runtime.selection_backend),
        regression_backend_id=evaluation_backend_identity(runtime.regression_backend),
        suite_results=tuple(suite_results),
    )
    runtime.store.write_regression_evidence(request.run_id, evidence)
    return RegressionExecutionResult(evidence, challenge.report, challenge.gate)


async def _execute_regression_suite(
    request: RegressionExecutionRequest,
    policy: RegressionExecutionPolicy,
    runtime: RegressionExecutionRuntime,
    suite: ResolvedRegressionSuite,
) -> RegressionSuiteResult:
    started_at = time.monotonic()
    execution_id = regression_execution_id(suite.spec.suite_id)
    safe_emit_progress(
        runtime.progress_callback,
        "regression",
        f"Running independent regression suite {suite.spec.suite_id}",
    )
    regression_dataset = suite.dataset
    suite_gates: list[GateResult] = []
    fresh_execution = False
    baseline_summary: EvaluationSummary | None = None
    candidate_summary: EvaluationSummary | None = None
    evaluation_summaries: tuple[EvaluationSummary, ...] = ()
    evaluation_started = False
    evaluation_namespace = f"{request.run_id}-regression-{suite.spec.suite_id}"
    replay_budget: BudgetDecision | None = None
    replay_started = False
    replay_telemetry_before = None
    try:
        if policy.replay_enabled and request.candidate.target.target_type == "skill":
            if isinstance(
                runtime.regression_replay_backend,
                CandidateReplayEvidenceReuseBackend,
            ):
                raise RuntimeError(
                    "stored selection replay evidence cannot approve an independent "
                    "regression suite"
                )
            if runtime.replay is None:
                raise RuntimeError("independent regression replay executor is unavailable")
            if request.budget_context is not None:
                replay_units = max(1, len(suite.dataset.cases)) * (
                    policy.baseline_replay_repetitions
                    + policy.candidate_replay_repetitions
                )
                replay_budget = request.budget_context.reserve(
                    BudgetStage.REGRESSION_REPLAY,
                    f"{request.candidate.candidate_id}-regression-{suite.spec.suite_id}",
                    units=replay_units,
                )
                if not replay_budget.allowed:
                    suite_gates.append(
                        GateResult(
                            gate_name="run_budget_regression_replay",
                            passed=False,
                            reason=(
                                "independent regression replay was not run because "
                                "budget was denied"
                            ),
                            details={
                                "failure_class": "budget",
                                "failure_owner": FailureOwner.FRAMEWORK.value,
                                "repairable": False,
                                "code": "regression_replay_budget_denied",
                                "suite_id": suite.spec.suite_id,
                                "budget_decision": replay_budget.to_dict(),
                            },
                        )
                    )
                    raise RuntimeError("regression replay budget denied")
            def replay_lifecycle(stage: str, _payload: dict[str, object]) -> None:
                nonlocal replay_started
                if stage == "replay_started":
                    replay_started = True

            replay_primary: BaseException | None = None
            replay_completed = False
            try:
                replay_telemetry_before = stage_telemetry_usage_snapshot(
                    runtime.execution_telemetry,
                    "replay",
                )
                replay_result = await runtime.replay.execute(
                    RegressionReplayRequest(
                        run_id=request.run_id,
                        target=request.target,
                        dataset=suite.dataset,
                        candidate=request.candidate,
                        apply_policy=request.apply_policy,
                        suite_id=suite.spec.suite_id,
                        lifecycle_callback=replay_lifecycle,
                    )
                )
                replay_completed = True
            except BaseException as exc:
                replay_primary = exc
                raise
            finally:
                if replay_budget is not None and replay_budget.allowed:
                    try:
                        _settle_replay_budget(
                            request,
                            runtime,
                            replay_budget,
                            replay_started=replay_started,
                            telemetry_before=replay_telemetry_before,
                            release_reason=(
                                "regression_replay_not_started"
                                if replay_completed
                                else "regression_replay_failed_before_start"
                            ),
                        )
                    except BaseException as cleanup_exc:
                        if replay_primary is None:
                            raise
                        try:
                            replay_primary.add_note(
                                "regression replay budget settlement failed: "
                                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                            )
                        except Exception:
                            pass
                    finally:
                        replay_budget = None
            if replay_result.gate is not None:
                suite_gates.append(replay_result.gate)
            if replay_result.dataset is None or (
                replay_result.gate is not None and not replay_result.gate.passed
            ):
                raise RuntimeError(
                    "regression paired replay did not produce comparable evidence"
                )
            regression_dataset = replay_result.dataset

        # Regression suites are resolved from ordinary dataset sources, whose
        # cases normally live in the ``train`` split.  The evaluator is asked
        # for the semantically distinct ``regression`` split, so project the
        # suite's already-independent case panel onto that split explicitly.
        # Without this projection the production evaluator selects zero cases
        # and reports ``evaluation_agent_signal_missing`` even after every
        # regression replay succeeded.
        regression_case_ids = [case.case_id for case in regression_dataset.cases]
        regression_dataset = replace(
            regression_dataset,
            recipe=replace(
                regression_dataset.recipe,
                splits={
                    **dict(regression_dataset.recipe.splits),
                    "regression": regression_case_ids,
                },
            ),
        )
        evaluation_started = True
        baseline_summary, candidate_summary = await runtime.evaluate_pair(
            runtime.regression_backend,
            dataset=regression_dataset,
            candidate=request.candidate,
            dataset_split="regression",
            artifact_namespace=evaluation_namespace,
            task_batch_executor=runtime.task_batch_executor,
            max_concurrency=runtime.max_concurrency,
            execution_telemetry=runtime.execution_telemetry,
        )
        fresh_execution = True
        suite_gates.extend(
            (
                EvaluationRuntimeHealthGate().evaluate(
                    (baseline_summary, candidate_summary)
                ),
                ScoreImprovementGate(
                    min_delta=0.0,
                    minimum_relative_margin=(
                        _REGRESSION_SCORE_NONINFERIORITY_MARGIN
                    ),
                    accept_noninferior=True,
                ).evaluate(
                    baseline=baseline_summary,
                    candidate=candidate_summary,
                ),
                CostLatencyRegressionGate(
                    max_cost_regression_ratio=0.25,
                    max_latency_regression_ratio=0.5,
                ).evaluate(
                    baseline=baseline_summary,
                    candidate=candidate_summary,
                ),
            )
        )
        score_gate = next(
            gate for gate in suite_gates if gate.gate_name == "score_improvement"
        )
        if (
            is_verified_apply_policy(request.apply_policy)
            and not score_gate.passed
            and score_gate.details.get("code") == "score_improvement_inconclusive"
            and score_gate.details.get("tiebreak_eligible") is True
            and all(gate.passed for gate in suite_gates if gate is not score_gate)
        ):
            (
                baseline_summary,
                candidate_summary,
                tiebreak_gates,
                evaluation_summaries,
            ) = await _evaluate_regression_score_tiebreak(
                request=request,
                runtime=runtime,
                dataset=regression_dataset,
                baseline=baseline_summary,
                candidate=candidate_summary,
                initial_score_gate=score_gate,
                artifact_namespace=(
                    f"{request.run_id}-regression-score-tiebreak-1-"
                    f"{execution_id.rsplit('-', 1)[-1]}"
                ),
            )
            suite_gates[suite_gates.index(score_gate)] = tiebreak_gates[0]
            suite_gates.extend(tiebreak_gates[1:])
    except Exception as exc:
        if evaluation_started and baseline_summary is None and candidate_summary is None:
            evaluation_summaries = _missing_regression_evaluation_summaries(
                request.candidate.candidate_id,
                artifact_namespace=evaluation_namespace,
                error=exc,
            )
        if not any(
            gate.gate_name == "run_budget_regression_replay" for gate in suite_gates
        ):
            suite_gates.append(
                GateResult(
                    gate_name="independent_regression_execution",
                    passed=False,
                    reason="independent regression suite execution failed",
                    details={
                        "failure_class": "infrastructure",
                        "failure_owner": FailureOwner.FRAMEWORK.value,
                        "repairable": False,
                        "code": "independent_regression_execution_failed",
                        "suite_id": suite.spec.suite_id,
                        "type": type(exc).__name__,
                        "reason": sanitize_text(str(exc), max_chars=240),
                    },
                )
            )
    baseline_summary = baseline_summary or EvaluationSummary(
        variant_id="baseline",
        dataset_split="regression",
        metrics={"regression_execution_available": False},
    )
    candidate_summary = candidate_summary or EvaluationSummary(
        variant_id=request.candidate.candidate_id,
        dataset_split="regression",
        metrics={"regression_execution_available": False},
    )
    return RegressionSuiteResult(
        spec=suite.spec,
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        gate_results=tuple(suite_gates),
        execution_id=execution_id,
        duration_ms=max(0, int((time.monotonic() - started_at) * 1000)),
        fresh_execution=fresh_execution,
        evaluation_summaries=evaluation_summaries,
    )


async def _evaluate_regression_score_tiebreak(
    *,
    request: RegressionExecutionRequest,
    runtime: RegressionExecutionRuntime,
    dataset: SelfEvolveDataset,
    baseline: EvaluationSummary,
    candidate: EvaluationSummary,
    initial_score_gate: GateResult,
    artifact_namespace: str,
) -> tuple[
    EvaluationSummary,
    EvaluationSummary,
    tuple[GateResult, ...],
    tuple[EvaluationSummary, ...],
]:
    """Spend at most one reserved judge round on the same replayed panel."""

    raw_summaries = (baseline, candidate)
    initial_incompatibilities = _regression_tiebreak_incompatibilities(
        request, runtime, dataset, raw_summaries
    )
    if initial_incompatibilities:
        return (
            baseline,
            candidate,
            (
                initial_score_gate,
                _regression_tiebreak_measurement_failure(initial_incompatibilities),
            ),
            (),
        )
    safe_emit_progress(
        runtime.progress_callback,
        "regression",
        "Running one bounded independent regression score tie-break",
    )
    try:
        additional = await runtime.evaluate_pair(
            runtime.regression_backend,
            dataset=dataset,
            candidate=request.candidate,
            dataset_split="regression",
            artifact_namespace=artifact_namespace,
            task_batch_executor=runtime.task_batch_executor,
            max_concurrency=runtime.max_concurrency,
            execution_telemetry=runtime.execution_telemetry,
        )
    except Exception as exc:
        # The pair may have partly executed before raising. Missing summaries
        # must remain visible to the budget ledger as unknown usage, not zero.
        missing = _missing_regression_evaluation_summaries(
            request.candidate.candidate_id,
            artifact_namespace=artifact_namespace,
            error=exc,
        )
        failure = GateResult(
            gate_name="regression_score_tiebreak_execution",
            passed=False,
            reason="independent regression score tie-break execution failed",
            details={
                "code": "regression_score_tiebreak_execution_failed",
                "failure_class": "infrastructure",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": "shared_run",
                "failure_source": "native",
                "repairable": False,
                "type": type(exc).__name__,
                "reason": sanitize_text(str(exc), max_chars=240),
            },
        )
        return (
            baseline,
            candidate,
            (initial_score_gate, failure),
            (*raw_summaries, *missing),
        )

    raw_summaries = (*raw_summaries, *additional)
    health = replace(
        EvaluationRuntimeHealthGate().evaluate(additional),
        gate_name="regression_score_tiebreak_runtime_health",
    )
    cost = replace(
        CostLatencyRegressionGate(
            max_cost_regression_ratio=0.25,
            max_latency_regression_ratio=0.5,
        ).evaluate(baseline=additional[0], candidate=additional[1]),
        gate_name="regression_score_tiebreak_cost_latency",
    )
    reasons = _regression_tiebreak_incompatibilities(
        request, runtime, dataset, raw_summaries
    )
    pooled = (
        _accumulate_score_evidence(baseline, additional[0]),
        _accumulate_score_evidence(candidate, additional[1]),
    )
    for summary in pooled:
        accumulation = summary.metrics.get("score_evidence_accumulation")
        if (
            not isinstance(accumulation, Mapping)
            or accumulation.get("status") != "pooled"
        ):
            reasons.append("score_samples_not_pooled")
    if reasons:
        return (
            baseline,
            candidate,
            (
                initial_score_gate,
                health,
                cost,
                _regression_tiebreak_measurement_failure(reasons),
            ),
            raw_summaries,
        )
    if not health.passed or not cost.passed:
        return baseline, candidate, (initial_score_gate, health, cost), raw_summaries

    score = ScoreImprovementGate(
        min_delta=0.0,
        minimum_relative_margin=_REGRESSION_SCORE_NONINFERIORITY_MARGIN,
        accept_noninferior=True,
    ).evaluate(baseline=pooled[0], candidate=pooled[1])
    score = replace(
        score,
        details={
            **dict(score.details),
            "tiebreak_round": 1,
            "initial_decision": dict(initial_score_gate.details),
            "initial_baseline_execution_id": baseline.metrics.get(
                "evaluation_execution_id"
            ),
            "initial_candidate_execution_id": candidate.metrics.get(
                "evaluation_execution_id"
            ),
        },
    )
    return pooled[0], pooled[1], (score, health, cost), raw_summaries


def _missing_regression_evaluation_summaries(
    candidate_id: str,
    *,
    artifact_namespace: str,
    error: Exception,
) -> tuple[EvaluationSummary, ...]:
    """Record an attempted pair with unknown usage, without inventing a result."""

    return tuple(
        EvaluationSummary(
            variant_id=variant_id,
            dataset_split="regression",
            metrics={
                "evaluation_execution_id": f"{artifact_namespace}-{role}-missing",
                "evaluation_fresh_execution": True,
                "evaluation_summary_missing": True,
                "evaluation_exception_type": type(error).__name__,
            },
        )
        for role, variant_id in (("baseline", "baseline"), ("candidate", candidate_id))
    )


def _regression_tiebreak_measurement_failure(reasons: list[str]) -> GateResult:
    return GateResult(
        gate_name="regression_score_tiebreak_measurement",
        passed=False,
        reason="independent regression score rounds cannot be pooled safely",
        details={
            "code": "regression_score_tiebreak_incompatible",
            "failure_class": "framework",
            "failure_owner": FailureOwner.FRAMEWORK.value,
            "failure_scope": "shared_run",
            "failure_source": "native",
            "repairable": False,
            "reason_codes": list(dict.fromkeys(reasons)),
        },
    )


def _regression_tiebreak_incompatibilities(
    request: RegressionExecutionRequest,
    runtime: RegressionExecutionRuntime,
    dataset: SelfEvolveDataset,
    summaries: tuple[EvaluationSummary, ...],
) -> list[str]:
    """Bind every raw arm to the requested panel and a distinct fresh execution."""

    reasons: list[str] = []
    expected_case_ids = [case.case_id for case in dataset.cases]
    expected_identities = tuple(
        evaluation_request_identity(
            runtime.regression_backend,
            EvaluationRequest(
                variant_id=variant_id,
                candidate=variant,
                dataset=dataset,
                dataset_split="regression",
                preserve_case_cardinality=True,
            ),
            baseline_target_fingerprint=request.candidate.target_fingerprint,
        )
        for variant_id, variant in (
            ("baseline", None),
            (request.candidate.candidate_id, request.candidate),
        )
    )
    plan = summaries[0].metrics.get("comparison_plan_fingerprint")
    execution_ids: set[str] = set()
    for index, summary in enumerate(summaries):
        metrics = summary.metrics
        expected = expected_identities[index % 2]
        if (
            summary.variant_id
            != ("baseline" if index % 2 == 0 else request.candidate.candidate_id)
            or summary.dataset_split != "regression"
            or metrics.get("evaluation_identity") != expected.to_dict()
            or metrics.get("evaluation_identity_fingerprint") != expected.fingerprint
        ):
            reasons.append("evaluation_identity_mismatch")
        if (
            not isinstance(plan, str)
            or not plan
            or metrics.get("comparison_plan_fingerprint") != plan
        ):
            reasons.append("comparison_plan_mismatch")
        case_ids = metrics.get("comparison_case_ids")
        if (
            not isinstance(case_ids, (list, tuple))
            or list(case_ids) != expected_case_ids
            or metrics.get("comparison_cardinality_preserved") is not True
            or metrics.get("comparison_case_count") != len(expected_case_ids)
            or metrics.get("comparison_effective_case_count") != len(expected_case_ids)
        ):
            reasons.append("comparison_case_order_or_cardinality_mismatch")
        execution_id = metrics.get("evaluation_execution_id")
        if (
            not isinstance(execution_id, str)
            or not execution_id
            or execution_id in execution_ids
            or metrics.get("evaluation_fresh_execution") is not True
            or metrics.get("evaluation_reused") is not False
            or metrics.get("evaluation_reused_from_execution_id") is not None
            or metrics.get("evaluation_alias_of_execution_id") is not None
        ):
            reasons.append("evaluation_execution_not_fresh_or_distinct")
        if isinstance(execution_id, str):
            execution_ids.add(execution_id)
        samples = metrics.get("score_samples")
        paired_samples = summaries[index ^ 1].metrics.get("score_samples")
        if (
            not isinstance(samples, (list, tuple))
            or not isinstance(paired_samples, (list, tuple))
            or not expected_case_ids
            or not samples
            or len(samples) != len(paired_samples)
            or len(samples) % len(expected_case_ids)
        ):
            reasons.append("paired_score_sample_coverage_mismatch")
    return reasons


def _settle_replay_budget(
    request: RegressionExecutionRequest,
    runtime: RegressionExecutionRuntime,
    decision: BudgetDecision | None,
    *,
    replay_started: bool,
    telemetry_before: object | None,
    release_reason: str,
) -> None:
    if decision is None or not decision.allowed:
        return None
    budget_context = request.budget_context
    assert budget_context is not None
    if replay_started:
        assert telemetry_before is not None
        telemetry_after = stage_telemetry_usage_snapshot(
            runtime.execution_telemetry,
            "replay",
        )
        usage = stage_telemetry_usage_delta(telemetry_before, telemetry_after)  # type: ignore[arg-type]
        budget_context.debit(
            decision,
            usage_observation=usage.observation,
            actual_source=usage.source,
        )
    else:
        budget_context.release(decision, reason_code=release_reason)
    return None


__all__ = [
    "RegressionExecution",
    "RegressionExecutionPolicy",
    "RegressionExecutionRequest",
    "RegressionExecutionResult",
    "RegressionExecutionRuntime",
    "RegressionReplayExecution",
    "RegressionReplayRequest",
    "RegressionReplayResult",
    "execute_independent_regression",
]
