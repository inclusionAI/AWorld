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
    has_trajectory_set_validation_source,
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
    min_eval_cases: int = 30
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
        if (
            isinstance(self.min_eval_cases, bool)
            or not isinstance(self.min_eval_cases, int)
            or self.min_eval_cases < 0
        ):
            raise ValueError("min_eval_cases must be a non-negative integer")
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


@dataclass(frozen=True)
class DeterministicReplayAdmission:
    """Canonical replay evidence projection consumed by verified evaluation."""

    available: bool
    reason_code: str
    required_gate_names: tuple[str, ...]
    passed_gate_names: tuple[str, ...]
    failed_gate_names: tuple[str, ...]
    comparable: bool
    strict_execution_succeeded: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "reason_code": self.reason_code,
            "required_gate_names": list(self.required_gate_names),
            "passed_gate_names": list(self.passed_gate_names),
            "failed_gate_names": list(self.failed_gate_names),
            "comparable": self.comparable,
            "strict_execution_succeeded": self.strict_execution_succeeded,
        }


def _deterministic_replay_admission(
    replay: CandidateReplayExecutionResult,
) -> DeterministicReplayAdmission:
    """Project admitted deterministic evidence without redefining success.

    Task-owned baseline failures may form valid recovery pairs. Consequently,
    ``CandidateReplayResult.succeeded`` is diagnostic only: the authoritative
    admission criterion is deterministic pair comparability plus the typed
    replay and confidence gates that already evaluated those failures.
    """

    required_gate_names = ("candidate_replay", "replay_confidence")
    relevant_gate_names = {
        *required_gate_names,
        "replay_evaluator_admission",
    }
    relevant_gates = tuple(
        gate for gate in replay.gate_results if gate.gate_name in relevant_gate_names
    )
    passed_gate_names = tuple(
        dict.fromkeys(gate.gate_name for gate in relevant_gates if gate.passed)
    )
    failed_gate_names = tuple(
        dict.fromkeys(gate.gate_name for gate in relevant_gates if not gate.passed)
    )
    replay_result = replay.replay_result
    replay_dataset = replay.replay_dataset
    strict_execution_succeeded = bool(
        replay_result is not None and replay_result.succeeded
    )
    required_gates_passed = all(
        gate_name in passed_gate_names for gate_name in required_gate_names
    )
    # ``candidate_replay`` is emitted only after the replay controller calls
    # candidate_replay_is_comparable(require_adapted=True). Do not repeat that
    # check against replay_dataset: it is the derived evaluator dataset and can
    # contain repetition-expanded case ids that intentionally differ from the
    # source replay member ids.
    comparable = "candidate_replay" in passed_gate_names
    available = bool(
        replay_result is not None
        and replay_dataset is not None
        and required_gates_passed
        and not failed_gate_names
        and comparable
    )
    if available:
        reason_code = "deterministic_comparable_replay_admitted"
    elif replay_result is None or replay_dataset is None:
        reason_code = "replay_evidence_missing"
    elif failed_gate_names:
        reason_code = "typed_replay_gate_failed"
    elif not required_gates_passed:
        reason_code = "typed_replay_gate_missing"
    else:
        reason_code = "replay_pairs_not_deterministically_comparable"
    return DeterministicReplayAdmission(
        available=available,
        reason_code=reason_code,
        required_gate_names=required_gate_names,
        passed_gate_names=passed_gate_names,
        failed_gate_names=failed_gate_names,
        comparable=comparable,
        strict_execution_succeeded=strict_execution_succeeded,
    )


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


def _verification_feasibility_gate(
    *,
    dataset: SelfEvolveDataset,
    min_eval_cases: int,
    deterministic_replay: DeterministicReplayAdmission,
) -> GateResult:
    """Reject verified-only work before judge spend when confidence is unreachable."""

    source = dataset.recipe.source
    independent_held_out_count = source.get("held_out_member_count")
    held_out_case_count = (
        int(independent_held_out_count)
        if isinstance(independent_held_out_count, int)
        and not isinstance(independent_held_out_count, bool)
        and independent_held_out_count >= 0
        else len(dataset.recipe.held_out_case_ids)
    )
    command_case_count = sum(
        1 for case in dataset.cases if case.verification_command
    )
    deterministic_available = bool(
        deterministic_replay.available or command_case_count > 0
    )
    trajectory_set_available = bool(
        held_out_case_count > 0
        and has_trajectory_set_validation_source(dataset)
    )
    single_case_replay_available = bool(
        deterministic_replay.available
        and source.get("paired_replay") is True
        and source.get("original_case_count") == 1
    )
    reachable = bool(
        deterministic_available
        and (
            held_out_case_count >= min_eval_cases
            or trajectory_set_available
            or single_case_replay_available
        )
    )
    details = {
        "code": (
            "verified_confidence_reachable"
            if reachable
            else "verified_confidence_unreachable"
        ),
        "held_out_case_count": held_out_case_count,
        "min_eval_cases": min_eval_cases,
        "deterministic_replay_available": deterministic_replay.available,
        "deterministic_replay_admission": deterministic_replay.to_dict(),
        "verification_command_case_count": command_case_count,
        "deterministic_verification_available": deterministic_available,
        "trajectory_set_validation_available": trajectory_set_available,
        "single_case_replay_available": single_case_replay_available,
    }
    if reachable:
        return GateResult(
            gate_name="verification_feasibility",
            passed=True,
            reason="verified confidence is reachable for the admitted evaluation plan",
            details=details,
        )
    return GateResult(
        gate_name="verification_feasibility",
        passed=False,
        reason=(
            "verified confidence is unreachable: available independent held-out "
            "cases and deterministic verification cannot satisfy policy"
        ),
        details={
            **details,
            "failure_class": "framework",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "repairable": False,
            "next_action": (
                "provide verification commands, add independent held-out cases, "
                "or use a non-verified apply policy"
            ),
        },
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
    if (
        verified_apply
        and not replay_blocked_verified_apply
        and getattr(policy.evaluation_backend, "probabilistic_only", False)
        is True
    ):
        feasibility_gate = _verification_feasibility_gate(
            dataset=evaluation_dataset,
            min_eval_cases=policy.min_eval_cases,
            deterministic_replay=_deterministic_replay_admission(replay),
        )
        gate_results.append(feasibility_gate)
        if not feasibility_gate.passed:
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
                ),
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
