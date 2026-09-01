"""Typed target-behavior and evaluation-budget admission planning."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aworld.self_evolve.budget import (
    BudgetDecision,
    BudgetStage,
    CandidateAttemptStage,
)
from aworld.self_evolve.campaign_policy import is_verified_apply_policy
from aworld.self_evolve.candidate_package import (
    CandidateMutationKind,
    classify_candidate_mutation,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    CandidateEvaluationResult,
    CandidateFeedbackBuilder,
    terminal_candidate_evaluation_result,
)
from aworld.self_evolve.controllers.run_replay_execution import (
    CandidateReplayExecutionResult,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.evaluation import (
    EvaluationBackend,
    EvaluationRequest,
    evaluation_request_identity,
)
from aworld.self_evolve.gates import TargetBehaviorDeltaGate
from aworld.self_evolve.types import GateResult


TypedGateFailureMapper = Callable[[GateResult], GateResult]


@dataclass(frozen=True)
class CandidateEvaluationAdmissionRequest:
    """Frozen candidate request plus its admitted replay evidence."""

    evaluation: CandidateEvaluationRequest
    replay: CandidateReplayExecutionResult

    def __post_init__(self) -> None:
        if not isinstance(self.evaluation, CandidateEvaluationRequest):
            raise TypeError("evaluation admission request must be typed")
        if not isinstance(self.replay, CandidateReplayExecutionResult):
            raise TypeError("evaluation admission replay result must be typed")
        if self.replay.terminal_result is not None:
            raise ValueError("terminal replay result cannot enter evaluation planning")


@dataclass(frozen=True)
class CandidateEvaluationAdmissionPolicy:
    """Runner-owned policy needed to reserve authoritative evaluation work."""

    replay_enabled: bool
    evaluation_backend: EvaluationBackend | None
    judge_repetitions: int
    regression_suite_case_counts: tuple[int, ...] = ()
    challenger_enabled: bool = False
    challenger_max_cases: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.judge_repetitions, bool)
            or not isinstance(self.judge_repetitions, int)
            or self.judge_repetitions < 0
        ):
            raise ValueError("judge_repetitions must be a non-negative integer")
        counts = tuple(self.regression_suite_case_counts)
        if any(
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for count in counts
        ):
            raise ValueError(
                "regression_suite_case_counts must be non-negative integers"
            )
        object.__setattr__(self, "regression_suite_case_counts", counts)
        if (
            isinstance(self.challenger_max_cases, bool)
            or not isinstance(self.challenger_max_cases, int)
            or self.challenger_max_cases < 1
        ):
            raise ValueError("challenger_max_cases must be a positive integer")


@dataclass(frozen=True)
class CandidateEvaluationAdmissionRuntime:
    """Compatibility seams used by evaluation admission."""

    typed_gate_failure: TypedGateFailureMapper
    feedback_builder: CandidateFeedbackBuilder

    def __post_init__(self) -> None:
        if not callable(self.typed_gate_failure):
            raise TypeError("typed_gate_failure must be callable")
        if not callable(self.feedback_builder):
            raise TypeError("feedback_builder must be callable")


@dataclass(frozen=True)
class CandidateEvaluationAdmissionResult:
    """Evaluation dataset, held reservations, and optional terminal result."""

    gate_results: tuple[GateResult, ...]
    evaluation_dataset: SelfEvolveDataset
    replay_blocked_verified_apply: bool
    evaluation_budget: BudgetDecision | None
    judge_budget: BudgetDecision | None
    expected_judge_summary_count: int
    evaluation_case_count: int
    baseline_is_cached: bool
    evaluation_units: int
    terminal_result: CandidateEvaluationResult | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_results", tuple(self.gate_results))
        if not all(isinstance(gate, GateResult) for gate in self.gate_results):
            raise TypeError("evaluation admission gate_results must be typed")
        if not isinstance(self.evaluation_dataset, SelfEvolveDataset):
            raise TypeError("evaluation_dataset must be typed")
        for field_name in (
            "expected_judge_summary_count",
            "evaluation_case_count",
            "evaluation_units",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be non-negative")


def can_reuse_single_case_replay_validation(
    dataset: SelfEvolveDataset,
) -> bool:
    return (
        bool(dataset.recipe.source.get("paired_replay"))
        and dataset.recipe.source.get("original_case_count") == 1
        and not dataset.recipe.held_out_case_ids
    )


def _terminal_result(
    request: CandidateEvaluationAdmissionRequest,
    runtime: CandidateEvaluationAdmissionRuntime,
    *,
    gate_results: list[GateResult],
    status: str = "rejected",
) -> CandidateEvaluationResult:
    evaluation = request.evaluation
    return terminal_candidate_evaluation_result(
        candidate=evaluation.candidate,
        iteration_number=evaluation.iteration_number,
        candidate_number=evaluation.candidate_number,
        candidate_count=evaluation.candidate_count,
        gate_results=gate_results,
        feedback_builder=runtime.feedback_builder,
        status=status,
        replay_result=request.replay.replay_result,
        replay_dataset=request.replay.replay_dataset,
    )


def plan_candidate_evaluation_admission(
    request: CandidateEvaluationAdmissionRequest,
    policy: CandidateEvaluationAdmissionPolicy,
    runtime: CandidateEvaluationAdmissionRuntime,
) -> CandidateEvaluationAdmissionResult:
    """Admit target behavior and reserve evaluation plus judge work."""

    evaluation = request.evaluation
    replay = request.replay
    gate_results = list(replay.gate_results)
    current_content = evaluation.target.load_current_content()
    target_behavior_gate = runtime.typed_gate_failure(
        TargetBehaviorDeltaGate().evaluate(
            current_content=current_content,
            candidate=evaluation.candidate,
        )
    )
    gate_results.append(target_behavior_gate)
    evaluation_dataset = replay.replay_dataset or evaluation.dataset
    if not target_behavior_gate.passed:
        classification = classify_candidate_mutation(
            evaluation.candidate,
            current_content=evaluation.target.load_current_content(),
        )
        support_bootstrap_ready = bool(
            classification.kind is CandidateMutationKind.EVALUATION_SUPPORT
            and all(gate.passed for gate in gate_results[:-1])
        )
        tracker = evaluation.attempt_tracker
        attempt_key = evaluation.attempt_key
        if (
            tracker is not None
            and attempt_key is not None
            and not tracker.terminal(attempt_key)
        ):
            tracker.emit(
                attempt_key,
                (
                    CandidateAttemptStage.PREREQUISITE_READY
                    if support_bootstrap_ready
                    else CandidateAttemptStage.REJECTED
                ),
                reason_code=(
                    "evaluation_support_bootstrap_ready"
                    if support_bootstrap_ready
                    else "target_behavior_delta_missing"
                ),
            )
        status = "prerequisite" if support_bootstrap_ready else "rejected"
        return CandidateEvaluationAdmissionResult(
            gate_results=tuple(gate_results),
            evaluation_dataset=evaluation_dataset,
            replay_blocked_verified_apply=False,
            evaluation_budget=None,
            judge_budget=None,
            expected_judge_summary_count=0,
            evaluation_case_count=len(evaluation_dataset.cases),
            baseline_is_cached=False,
            evaluation_units=0,
            terminal_result=_terminal_result(
                request,
                runtime,
                gate_results=gate_results,
                status=status,
            ),
        )

    verified_apply = is_verified_apply_policy(evaluation.apply_policy)
    replay_blocked_verified_apply = bool(
        verified_apply
        and policy.replay_enabled
        and evaluation.candidate.target.target_type == "skill"
        and replay.replay_dataset is None
    )
    evaluation_budget: BudgetDecision | None = None
    judge_budget: BudgetDecision | None = None
    expected_judge_summary_count = 0
    evaluation_case_count = len(evaluation_dataset.cases)
    baseline_is_cached = False
    evaluation_units = 0
    budget_context = evaluation.budget_context
    if (
        policy.evaluation_backend is not None
        and not replay_blocked_verified_apply
        and budget_context is not None
    ):
        baseline_identity = evaluation_request_identity(
            policy.evaluation_backend,
            EvaluationRequest(
                variant_id="baseline",
                candidate=None,
                dataset=evaluation_dataset,
                dataset_split="validation",
            ),
            baseline_target_fingerprint=(
                evaluation.candidate.target_fingerprint
            ),
        )
        baseline_is_cached = bool(
            evaluation.baseline_evaluation_cache is not None
            and baseline_identity.fingerprint
            in evaluation.baseline_evaluation_cache
        )
        evaluation_variants = 1 if baseline_is_cached else 2
        if verified_apply:
            evaluation_variants += 2
        if (
            verified_apply
            and not can_reuse_single_case_replay_validation(evaluation_dataset)
        ):
            evaluation_variants += 1
        expected_judge_summary_count = 1 if baseline_is_cached else 2
        regression_evaluation_units = (
            sum(
                max(1, case_count) * 2
                for case_count in policy.regression_suite_case_counts
            )
            if verified_apply
            else 0
        )
        if (
            verified_apply
            and policy.challenger_enabled
            and policy.regression_suite_case_counts
        ):
            regression_evaluation_units += policy.challenger_max_cases * 2
        evaluation_units = max(
            1,
            evaluation_case_count * evaluation_variants
            + regression_evaluation_units,
        )
        evaluation_budget = budget_context.reserve(
            BudgetStage.EVALUATION,
            f"{evaluation.candidate.candidate_id}-evaluation",
            units=evaluation_units,
        )
        judge_budget = budget_context.reserve(
            BudgetStage.JUDGE,
            f"{evaluation.candidate.candidate_id}-judge",
            units=max(1, evaluation_units * policy.judge_repetitions),
        )
        denied_decision = next(
            (
                decision
                for decision in (evaluation_budget, judge_budget)
                if not decision.allowed
            ),
            None,
        )
        if denied_decision is not None:
            for decision in (evaluation_budget, judge_budget):
                if decision.allowed:
                    budget_context.release(
                        decision,
                        reason_code="dependent_evaluation_budget_denied",
                    )
            denied_stage = denied_decision.stage.value
            gate_results.append(
                GateResult(
                    gate_name=f"run_budget_{denied_stage}",
                    passed=False,
                    reason="evaluation was not run because budget was denied",
                    details={
                        "failure_class": "budget",
                        "code": f"{denied_stage}_budget_denied",
                        "budget_decision": denied_decision.to_dict(),
                    },
                )
            )
            tracker = evaluation.attempt_tracker
            attempt_key = evaluation.attempt_key
            if tracker is not None and attempt_key is not None:
                tracker.emit(
                    attempt_key,
                    (
                        CandidateAttemptStage.REJECTED
                        if tracker.has_stage(
                            attempt_key,
                            CandidateAttemptStage.PAIRED_REPLAY_STARTED,
                        )
                        else CandidateAttemptStage.NOT_RUN
                    ),
                    reason_code=f"{denied_stage}_budget_denied",
                )
            return CandidateEvaluationAdmissionResult(
                gate_results=tuple(gate_results),
                evaluation_dataset=evaluation_dataset,
                replay_blocked_verified_apply=(
                    replay_blocked_verified_apply
                ),
                evaluation_budget=evaluation_budget,
                judge_budget=judge_budget,
                expected_judge_summary_count=expected_judge_summary_count,
                evaluation_case_count=evaluation_case_count,
                baseline_is_cached=baseline_is_cached,
                evaluation_units=evaluation_units,
                terminal_result=_terminal_result(
                    request,
                    runtime,
                    gate_results=gate_results,
                ),
            )

    return CandidateEvaluationAdmissionResult(
        gate_results=tuple(gate_results),
        evaluation_dataset=evaluation_dataset,
        replay_blocked_verified_apply=replay_blocked_verified_apply,
        evaluation_budget=evaluation_budget,
        judge_budget=judge_budget,
        expected_judge_summary_count=expected_judge_summary_count,
        evaluation_case_count=evaluation_case_count,
        baseline_is_cached=baseline_is_cached,
        evaluation_units=evaluation_units,
    )
