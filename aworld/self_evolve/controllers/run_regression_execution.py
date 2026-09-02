"""Typed independent-regression orchestration and evidence persistence."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aworld.self_evolve.budget import BudgetDecision, BudgetStage
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
    evaluate_baseline_and_candidate,
)
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

        baseline_summary, candidate_summary = await runtime.evaluate_pair(
            runtime.regression_backend,
            dataset=regression_dataset,
            candidate=request.candidate,
            dataset_split="regression",
            artifact_namespace=(
                f"{request.run_id}-regression-{suite.spec.suite_id}"
            ),
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
                ScoreImprovementGate(min_delta=0.0).evaluate(
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
    except Exception as exc:
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
    )


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
