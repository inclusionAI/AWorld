"""Typed contracts for explicit-run and candidate-evaluation execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import AbstractSet, Protocol

from aworld.self_evolve.budget import (
    BudgetDecision,
    BudgetStage,
    CandidateAttemptKey,
    CandidateAttemptStage,
    ZeroBudgetUsageProofProvider,
)
from aworld.self_evolve.campaign_policy import (
    effective_replay_repetitions,
    is_verified_apply_policy,
)
from aworld.self_evolve.candidate_package import (
    CandidateMutationKind,
    classify_candidate_mutation,
)
from aworld.self_evolve.challenger import ChallengeReport
from aworld.self_evolve.credit_assignment import (
    TargetSelectionDecision,
    TargetSelectionReport,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.evaluation import estimate_replay_cost
from aworld.self_evolve.gates import BudgetGate, TargetBehaviorDeltaGate
from aworld.self_evolve.optimizers.base import CandidateSourceDisposition
from aworld.self_evolve.provenance import (
    InferredNewSkillPolicy,
    TargetMutationIntent,
    TargetProvenance,
)
from aworld.self_evolve.regression import RegressionEvidence
from aworld.self_evolve.replay import (
    CandidateReplayEvidenceReuseBackend,
    CandidateReplayResult,
    _is_replayable_user_task_case,
)
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
)


class CandidateEvaluationAttemptTracker(Protocol):
    """Attempt-lifecycle operations consumed by candidate evaluation."""

    def emit(
        self,
        key: CandidateAttemptKey,
        stage: CandidateAttemptStage,
        *,
        reason_code: str | None = None,
        case_count: int | None = None,
        usage: object | None = None,
    ) -> object: ...

    def has_stage(
        self,
        key: CandidateAttemptKey,
        *stages: CandidateAttemptStage,
    ) -> bool: ...

    def last_stage(self, key: CandidateAttemptKey) -> CandidateAttemptStage: ...

    def terminal(self, key: CandidateAttemptKey) -> bool: ...


class CandidateEvaluationBudgetContext(Protocol):
    """Budget operations consumed by candidate evaluation."""

    def reserve(
        self,
        stage: BudgetStage,
        item_id: str,
        *,
        units: int = 1,
        **kwargs: object,
    ) -> BudgetDecision: ...

    def debit(
        self,
        decision: BudgetDecision,
        **kwargs: object,
    ) -> object: ...

    def release(
        self,
        decision: BudgetDecision,
        *,
        reason_code: str,
    ) -> object: ...


@dataclass(frozen=True)
class ExplicitTargetRunRequest:
    """Frozen caller-owned inputs for one explicit-target run."""

    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    trace_packs: tuple[TracePack, ...]
    apply_policy: str = "proposal"
    target_selection_report: TargetSelectionReport | None = None
    target_provenance: TargetProvenance | None = None
    target_selection_decision: TargetSelectionDecision | None = None
    campaign_prior_run_ids: tuple[str, ...] | None = None
    campaign_scheduler_checkpoint_run_ids: tuple[str, ...] | None = None
    campaign_id: str | None = None
    campaign_cycle: int | None = None

    def __post_init__(self) -> None:
        run_id = self.run_id.strip() if isinstance(self.run_id, str) else ""
        if not run_id:
            raise ValueError("explicit target run_id must be non-empty")
        if self.apply_policy not in {
            "proposal",
            "auto_verified",
            "verified_only",
        }:
            raise ValueError(f"unsupported apply policy: {self.apply_policy}")
        object.__setattr__(self, "trace_packs", tuple(self.trace_packs))
        if self.campaign_prior_run_ids is not None:
            object.__setattr__(
                self,
                "campaign_prior_run_ids",
                tuple(self.campaign_prior_run_ids),
            )
        if self.campaign_scheduler_checkpoint_run_ids is not None:
            object.__setattr__(
                self,
                "campaign_scheduler_checkpoint_run_ids",
                tuple(self.campaign_scheduler_checkpoint_run_ids),
            )


@dataclass(frozen=True)
class CandidateEvaluationRequest:
    """Frozen inputs for one candidate's authoritative evaluation funnel."""

    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    candidate: CandidateVariant
    apply_policy: str
    target_provenance: TargetProvenance | None
    iteration_number: int
    candidate_number: int
    candidate_count: int
    rejected_candidate_ids: AbstractSet[str] = field(default_factory=frozenset)
    accepted_candidate_ids: AbstractSet[str] = field(default_factory=frozenset)
    target_provenance_unresolved_reason: str | None = None
    target_selection_report: TargetSelectionReport | None = None
    baseline_replay_dir: str | None = None
    capability_requirements: tuple[ReplayCapabilityRequirement, ...] = ()
    attempt_key: CandidateAttemptKey | None = None
    attempt_tracker: CandidateEvaluationAttemptTracker | None = None
    budget_context: CandidateEvaluationBudgetContext | None = None
    precomputed_gate_results: tuple[GateResult, ...] = ()
    source_disposition: CandidateSourceDisposition = field(
        default_factory=CandidateSourceDisposition
    )
    baseline_evaluation_cache: (
        MutableMapping[str, EvaluationSummary] | None
    ) = None
    allow_score_tiebreak: bool = True

    def __post_init__(self) -> None:
        run_id = self.run_id.strip() if isinstance(self.run_id, str) else ""
        if not run_id:
            raise ValueError("candidate evaluation run_id must be non-empty")
        if self.apply_policy not in {
            "proposal",
            "auto_verified",
            "verified_only",
        }:
            raise ValueError(
                f"unsupported candidate evaluation apply policy: {self.apply_policy}"
            )
        for field_name, value in (
            ("iteration_number", self.iteration_number),
            ("candidate_number", self.candidate_number),
            ("candidate_count", self.candidate_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.candidate_number > self.candidate_count:
            raise ValueError("candidate_number cannot exceed candidate_count")
        object.__setattr__(
            self,
            "rejected_candidate_ids",
            frozenset(self.rejected_candidate_ids),
        )
        object.__setattr__(
            self,
            "accepted_candidate_ids",
            frozenset(self.accepted_candidate_ids),
        )
        object.__setattr__(
            self,
            "capability_requirements",
            tuple(self.capability_requirements),
        )
        object.__setattr__(
            self,
            "precomputed_gate_results",
            tuple(self.precomputed_gate_results),
        )


@dataclass(frozen=True)
class CandidateEvaluationState:
    """Typed view over the legacy mutable iteration-state payload."""

    payload: dict[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            raise TypeError("candidate evaluation state payload must be a dict")
        status = self.payload.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError("candidate evaluation state requires a status")

    @property
    def status(self) -> str:
        return str(self.payload["status"])

    @property
    def gate_results(self) -> tuple[GateResult, ...]:
        raw_results = self.payload.get("gate_results", ())
        if not isinstance(raw_results, (list, tuple)):
            raise TypeError("candidate evaluation gate_results must be a sequence")
        if not all(isinstance(gate, GateResult) for gate in raw_results):
            raise TypeError("candidate evaluation gate_results must be typed")
        return tuple(raw_results)


@dataclass(frozen=True)
class CandidateEvaluationResult:
    """Typed outcome consumed by the explicit-run state machine."""

    state: CandidateEvaluationState
    report_item: dict[str, object]
    feedback: tuple[EvaluationSummary, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.state, CandidateEvaluationState):
            raise TypeError("candidate evaluation state must be typed")
        if not isinstance(self.report_item, dict):
            raise TypeError("candidate evaluation report_item must be a dict")
        object.__setattr__(self, "feedback", tuple(self.feedback))

    def as_tuple(
        self,
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        tuple[EvaluationSummary, ...],
    ]:
        return self.state.payload, self.report_item, self.feedback

    @classmethod
    def from_tuple(
        cls,
        value: tuple[
            dict[str, object],
            dict[str, object],
            tuple[EvaluationSummary, ...],
        ],
    ) -> "CandidateEvaluationResult":
        state, report_item, feedback = value
        return cls(
            state=CandidateEvaluationState(state),
            report_item=report_item,
            feedback=feedback,
        )


CandidateGateEvaluator = Callable[..., list[GateResult]]
CandidateFeedbackBuilder = Callable[..., tuple[EvaluationSummary, ...]]


@dataclass(frozen=True)
class CandidateLocalAdmissionPolicy:
    """Runner-owned policy injected into controller-owned local admission."""

    workspace_root: Path
    max_candidate_chars: int
    allow_generated_target_mutation: bool
    allow_external_target_mutation: bool
    target_intent: TargetMutationIntent | str | None
    inferred_new_skill_policy: InferredNewSkillPolicy | str
    skip_duplicate_rejected_candidate_gate: bool
    gate_evaluator: CandidateGateEvaluator

    def __post_init__(self) -> None:
        if self.max_candidate_chars < 1:
            raise ValueError("max_candidate_chars must be positive")
        if not callable(self.gate_evaluator):
            raise TypeError("candidate gate evaluator must be callable")


@dataclass(frozen=True)
class CandidateLocalAdmissionResult:
    """Local gate outcome and optional terminal duplicate decision."""

    gate_results: tuple[GateResult, ...]
    terminal_result: CandidateEvaluationResult | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_results", tuple(self.gate_results))
        if not all(isinstance(gate, GateResult) for gate in self.gate_results):
            raise TypeError("local admission gate_results must be typed")


CandidateCapabilityValidator = Callable[..., Awaitable[list[GateResult]]]
ReusableBaselineCaseCounter = Callable[..., int]
TypedGateFailureMapper = Callable[[GateResult], GateResult]


@dataclass(frozen=True)
class CandidateReplayAdmissionPolicy:
    """Runner-owned replay planning and reservation policy."""

    replay_enabled: bool
    replay_backend: object | None
    repetitions_explicit: bool
    measurement_min_independent_cases: int
    baseline_repetitions: int
    candidate_repetitions: int
    judge_repetitions: int
    replay_candidate_limit: int | None
    per_attempt_replay_token_limit: int | None
    replay_tokens_per_unit: int | None

    def __post_init__(self) -> None:
        for field_name, value in (
            (
                "measurement_min_independent_cases",
                self.measurement_min_independent_cases,
            ),
            ("baseline_repetitions", self.baseline_repetitions),
            ("candidate_repetitions", self.candidate_repetitions),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if (
            isinstance(self.judge_repetitions, bool)
            or not isinstance(self.judge_repetitions, int)
            or self.judge_repetitions < 0
        ):
            raise ValueError("judge_repetitions must be a non-negative integer")
        if self.replay_candidate_limit is not None and (
            isinstance(self.replay_candidate_limit, bool)
            or not isinstance(self.replay_candidate_limit, int)
            or self.replay_candidate_limit < 1
        ):
            raise ValueError("replay_candidate_limit must be positive when set")
        if self.per_attempt_replay_token_limit is not None and (
            isinstance(self.per_attempt_replay_token_limit, bool)
            or not isinstance(self.per_attempt_replay_token_limit, int)
            or self.per_attempt_replay_token_limit < 1
        ):
            raise ValueError(
                "per_attempt_replay_token_limit must be positive when set"
            )
        if self.replay_tokens_per_unit is not None and (
            isinstance(self.replay_tokens_per_unit, bool)
            or not isinstance(self.replay_tokens_per_unit, int)
            or self.replay_tokens_per_unit < 0
        ):
            raise ValueError("replay_tokens_per_unit must be non-negative when set")


@dataclass(frozen=True)
class CandidateReplayAdmissionRuntime:
    """Injected seams used by controller-owned replay admission."""

    reusable_baseline_case_count: ReusableBaselineCaseCounter
    validate_capabilities: CandidateCapabilityValidator
    typed_gate_failure: TypedGateFailureMapper
    feedback_builder: CandidateFeedbackBuilder

    def __post_init__(self) -> None:
        for field_name in (
            "reusable_baseline_case_count",
            "validate_capabilities",
            "typed_gate_failure",
            "feedback_builder",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")


@dataclass(frozen=True)
class CandidateReplayAdmissionResult:
    """Replay plan, admission gates, and the held run-budget reservation."""

    gate_results: tuple[GateResult, ...]
    replay_case_count: int
    replay_planned: bool
    reuses_replay_evidence: bool
    effective_baseline_repetitions: int
    effective_candidate_repetitions: int
    replay_budget: BudgetDecision | None
    capability_gates: tuple[GateResult, ...]
    capability_blocked: bool
    terminal_result: CandidateEvaluationResult | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_results", tuple(self.gate_results))
        object.__setattr__(self, "capability_gates", tuple(self.capability_gates))
        if not all(isinstance(gate, GateResult) for gate in self.gate_results):
            raise TypeError("replay admission gate_results must be typed")
        if not all(isinstance(gate, GateResult) for gate in self.capability_gates):
            raise TypeError("replay admission capability_gates must be typed")
        if self.replay_case_count < 0:
            raise ValueError("replay_case_count must be non-negative")
        if self.effective_baseline_repetitions < 1:
            raise ValueError("effective_baseline_repetitions must be positive")
        if self.effective_candidate_repetitions < 1:
            raise ValueError("effective_candidate_repetitions must be positive")


def duplicate_rejected_candidate_gate(
    candidate: CandidateVariant,
    *,
    rejected_candidate_ids: AbstractSet[str],
    apply_policy: str,
) -> GateResult | None:
    if not is_verified_apply_policy(apply_policy):
        return None
    if candidate.candidate_id not in rejected_candidate_ids:
        return GateResult(
            gate_name="duplicate_rejected_candidate",
            passed=True,
            reason="candidate has not been previously rejected for this target",
        )
    return GateResult(
        gate_name="duplicate_rejected_candidate",
        passed=False,
        reason="candidate repeats a previously rejected candidate for this target",
        details={"candidate_id": candidate.candidate_id},
    )


def duplicate_accepted_candidate_gate(
    candidate: CandidateVariant,
    *,
    accepted_candidate_ids: AbstractSet[str],
    apply_policy: str,
) -> GateResult | None:
    if not is_verified_apply_policy(apply_policy):
        return None
    if candidate.candidate_id not in accepted_candidate_ids:
        return GateResult(
            gate_name="duplicate_accepted_candidate",
            passed=True,
            reason="candidate has not been previously accepted for this target",
        )
    return GateResult(
        gate_name="duplicate_accepted_candidate",
        passed=False,
        reason="candidate repeats a previously accepted candidate for this target",
        details={"candidate_id": candidate.candidate_id},
    )


def iteration_report_item(
    *,
    iteration_number: int,
    candidate_number: int,
    candidate_count: int,
    candidate: CandidateVariant,
    status: str,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
    held_out_summary: EvaluationSummary | None,
    failed_gates: Iterable[GateResult],
    regression_evidence: RegressionEvidence | None = None,
    challenge_report: ChallengeReport | None = None,
) -> dict[str, object]:
    return {
        "iteration": iteration_number,
        "candidate_number": candidate_number,
        "candidate_count": candidate_count,
        "candidate_id": candidate.candidate_id,
        "status": status,
        "baseline_metrics": (
            dict(baseline_summary.metrics)
            if baseline_summary is not None
            else None
        ),
        "candidate_metrics": (
            dict(candidate_summary.metrics)
            if candidate_summary is not None
            else None
        ),
        "held_out_metrics": (
            dict(held_out_summary.metrics)
            if held_out_summary is not None
            else None
        ),
        "failed_gates": [gate.gate_name for gate in failed_gates],
        "regression_evidence": (
            regression_evidence.to_dict()
            if regression_evidence is not None
            else None
        ),
        "challenge_report": (
            challenge_report.to_dict()
            if challenge_report is not None
            else None
        ),
    }


def iteration_state(
    *,
    candidate: CandidateVariant,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
    held_out_summary: EvaluationSummary | None,
    replay_result: CandidateReplayResult | None,
    replay_dataset: SelfEvolveDataset | None,
    gate_results: list[GateResult] | tuple[GateResult, ...],
    feedback: tuple[EvaluationSummary, ...],
    status: str,
    regression_evidence: RegressionEvidence | None = None,
    challenge_report: ChallengeReport | None = None,
) -> dict[str, object]:
    return {
        "candidate": candidate,
        "baseline_summary": baseline_summary,
        "candidate_summary": candidate_summary,
        "held_out_summary": held_out_summary,
        "replay_result": replay_result,
        "replay_dataset": replay_dataset,
        "gate_results": gate_results,
        "feedback": feedback,
        "status": status,
        "regression_evidence": regression_evidence,
        "challenge_report": challenge_report,
    }


def terminal_candidate_evaluation_result(
    *,
    candidate: CandidateVariant,
    iteration_number: int,
    candidate_number: int,
    candidate_count: int,
    gate_results: Iterable[GateResult],
    feedback_builder: CandidateFeedbackBuilder,
    status: str = "rejected",
    replay_result: CandidateReplayResult | None = None,
    replay_dataset: SelfEvolveDataset | None = None,
) -> CandidateEvaluationResult:
    gates = tuple(gate_results)
    failed_gates = [gate for gate in gates if not gate.passed]
    feedback = feedback_builder(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=failed_gates,
    )
    report_item = iteration_report_item(
        iteration_number=iteration_number,
        candidate_number=candidate_number,
        candidate_count=candidate_count,
        candidate=candidate,
        status=status,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=failed_gates,
    )
    state = iteration_state(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        replay_result=replay_result,
        replay_dataset=replay_dataset,
        gate_results=gates,
        feedback=feedback,
        status=status,
    )
    return CandidateEvaluationResult.from_tuple(
        (state, report_item, feedback)
    )


def _replayable_user_task_dataset(
    dataset: SelfEvolveDataset,
) -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=tuple(
            case
            for case in dataset.cases
            if _is_replayable_user_task_case(case)
        ),
        recipe=dataset.recipe,
    )


def _backend_proves_zero_budget_usage(
    backend: object | None,
    stage: BudgetStage,
) -> bool:
    if not isinstance(backend, ZeroBudgetUsageProofProvider):
        return False
    try:
        return backend.proves_zero_budget_usage(stage) is True
    except Exception:
        return False


async def execute_candidate_replay_admission(
    request: CandidateEvaluationRequest,
    policy: CandidateReplayAdmissionPolicy,
    runtime: CandidateReplayAdmissionRuntime,
    *,
    initial_gate_results: Iterable[GateResult],
) -> CandidateReplayAdmissionResult:
    """Plan paired replay and admit it through budget/capability gates."""

    gate_results = list(initial_gate_results)
    replay_dataset = _replayable_user_task_dataset(request.dataset)
    replay_case_count = len(replay_dataset.cases)
    replay_planned = bool(
        policy.replay_enabled
        and request.candidate.target.target_type == "skill"
        and policy.replay_backend is not None
        and replay_case_count > 0
    )
    reuses_replay_evidence = bool(
        replay_planned
        and isinstance(
            policy.replay_backend,
            CandidateReplayEvidenceReuseBackend,
        )
    )
    (
        effective_baseline_repetitions,
        effective_candidate_repetitions,
        _,
    ) = effective_replay_repetitions(
        apply_policy=request.apply_policy,
        repetitions_explicit=policy.repetitions_explicit,
        replay_case_count=replay_case_count,
        measurement_min_independent_cases=(
            policy.measurement_min_independent_cases
        ),
        baseline_repetitions=policy.baseline_repetitions,
        candidate_repetitions=policy.candidate_repetitions,
    )

    per_attempt_budget_gate: GateResult | None = None
    if not reuses_replay_evidence:
        replay_backend_proven_zero = _backend_proves_zero_budget_usage(
            policy.replay_backend,
            BudgetStage.PAIRED_REPLAY,
        )
        per_attempt_budget_gate = BudgetGate().evaluate(
            estimate_replay_cost(
                dataset=replay_dataset,
                candidate_count=1,
                judge_repetitions=policy.judge_repetitions,
                baseline_repetitions=effective_baseline_repetitions,
                candidate_repetitions=effective_candidate_repetitions,
                replay_candidate_limit=policy.replay_candidate_limit,
                max_run_tokens=policy.per_attempt_replay_token_limit,
                estimated_tokens_per_replay=(
                    None
                    if replay_backend_proven_zero
                    else policy.replay_tokens_per_unit
                ),
                backend_proven_zero=replay_backend_proven_zero,
            )
        )
        per_attempt_budget_gate = replace(
            per_attempt_budget_gate,
            details={
                **dict(per_attempt_budget_gate.details or {}),
                "budget_semantics": "per_attempt_replay_ceiling",
                "baseline_reuse_accounting": (
                    "conservative_independent_attempt_includes_baseline"
                ),
                "run_ledger_is_authoritative_for_baseline_reuse": True,
            },
        )
        gate_results.append(per_attempt_budget_gate)
    if (
        replay_planned
        and per_attempt_budget_gate is not None
        and not per_attempt_budget_gate.passed
    ):
        if request.attempt_tracker is not None and request.attempt_key is not None:
            request.attempt_tracker.emit(
                request.attempt_key,
                CandidateAttemptStage.NOT_RUN,
                reason_code="per_attempt_replay_budget_denied",
            )
        return CandidateReplayAdmissionResult(
            gate_results=tuple(gate_results),
            replay_case_count=replay_case_count,
            replay_planned=replay_planned,
            reuses_replay_evidence=reuses_replay_evidence,
            effective_baseline_repetitions=effective_baseline_repetitions,
            effective_candidate_repetitions=effective_candidate_repetitions,
            replay_budget=None,
            capability_gates=(),
            capability_blocked=False,
            terminal_result=terminal_candidate_evaluation_result(
                candidate=request.candidate,
                iteration_number=request.iteration_number,
                candidate_number=request.candidate_number,
                candidate_count=request.candidate_count,
                gate_results=gate_results,
                feedback_builder=runtime.feedback_builder,
            ),
        )

    replay_budget: BudgetDecision | None = None
    if (
        replay_planned
        and not reuses_replay_evidence
        and request.budget_context is not None
    ):
        reusable_baseline_case_count = runtime.reusable_baseline_case_count(
            dataset=request.dataset,
            baseline_replay_dir=request.baseline_replay_dir,
            baseline_repetitions=effective_baseline_repetitions,
        )
        replay_units = (
            replay_case_count * effective_candidate_repetitions
            + max(0, replay_case_count - reusable_baseline_case_count)
            * effective_baseline_repetitions
        )
        replay_budget = request.budget_context.reserve(
            BudgetStage.PAIRED_REPLAY,
            f"{request.candidate.candidate_id}-paired-replay",
            units=replay_units,
        )
        if not replay_budget.allowed:
            gate_results.append(
                GateResult(
                    gate_name="run_budget_paired_replay",
                    passed=False,
                    reason="paired replay was not run because budget was denied",
                    details={
                        "failure_class": "budget",
                        "code": "replay_budget_denied",
                        "budget_decision": replay_budget.to_dict(),
                    },
                )
            )
            if (
                request.attempt_tracker is not None
                and request.attempt_key is not None
            ):
                request.attempt_tracker.emit(
                    request.attempt_key,
                    CandidateAttemptStage.NOT_RUN,
                    reason_code="replay_budget_denied",
                )
            return CandidateReplayAdmissionResult(
                gate_results=tuple(gate_results),
                replay_case_count=replay_case_count,
                replay_planned=replay_planned,
                reuses_replay_evidence=reuses_replay_evidence,
                effective_baseline_repetitions=effective_baseline_repetitions,
                effective_candidate_repetitions=effective_candidate_repetitions,
                replay_budget=replay_budget,
                capability_gates=(),
                capability_blocked=False,
                terminal_result=terminal_candidate_evaluation_result(
                    candidate=request.candidate,
                    iteration_number=request.iteration_number,
                    candidate_number=request.candidate_number,
                    candidate_count=request.candidate_count,
                    gate_results=gate_results,
                    feedback_builder=runtime.feedback_builder,
                ),
            )

    capability_gates = (
        ()
        if reuses_replay_evidence
        else tuple(
            await runtime.validate_capabilities(
                run_id=request.run_id,
                target=request.target,
                dataset=request.dataset,
                candidate=request.candidate,
                requirements=request.capability_requirements,
            )
        )
    )
    if not all(isinstance(gate, GateResult) for gate in capability_gates):
        raise TypeError("candidate capability validator must return GateResult values")
    gate_results.extend(capability_gates)
    capability_blocked = any(not gate.passed for gate in capability_gates)
    mutation_classification = classify_candidate_mutation(
        request.candidate,
        current_content=request.target.load_current_content(),
    )
    if (
        is_verified_apply_policy(request.apply_policy)
        and mutation_classification.kind
        is CandidateMutationKind.EVALUATION_SUPPORT
    ):
        support_preflight_gates = tuple(
            gate
            for gate in capability_gates
            if gate.gate_name == "candidate_capability_replay"
            and gate.passed
            and isinstance(gate.details, Mapping)
            and gate.details.get("operational_preflight") is True
        )
        support_bootstrap_ready = bool(
            support_preflight_gates
            and all(gate.passed for gate in gate_results)
        )
        gate_results.append(
            GateResult(
                gate_name="evaluation_support_prerequisite",
                passed=support_bootstrap_ready,
                reason=(
                    "evaluation support passed deterministic capability preflight"
                    if support_bootstrap_ready
                    else (
                        "evaluation support requires a successful deterministic "
                        "capability preflight before composition"
                    )
                ),
                details={
                    "candidate_status": (
                        "prerequisite" if support_bootstrap_ready else "rejected"
                    ),
                    "code": (
                        "evaluation_support_prerequisite_ready"
                        if support_bootstrap_ready
                        else "evaluation_support_preflight_missing"
                    ),
                    "failure_class": (
                        None if support_bootstrap_ready else "candidate"
                    ),
                    "repairable": not support_bootstrap_ready,
                    "proof_gate_names": [
                        gate.gate_name for gate in support_preflight_gates
                    ],
                    "mutation": mutation_classification.to_dict(),
                },
            )
        )
        gate_results.append(
            runtime.typed_gate_failure(
                TargetBehaviorDeltaGate().evaluate(
                    current_content=request.target.load_current_content(),
                    candidate=request.candidate,
                )
            )
        )
        if replay_budget is not None:
            if request.budget_context is None:
                raise RuntimeError("replay reservation requires a budget context")
            request.budget_context.release(
                replay_budget,
                reason_code="evaluation_support_prerequisite_lane",
            )
            replay_budget = None
        if (
            request.attempt_tracker is not None
            and request.attempt_key is not None
            and not request.attempt_tracker.terminal(request.attempt_key)
        ):
            request.attempt_tracker.emit(
                request.attempt_key,
                (
                    CandidateAttemptStage.PREREQUISITE_READY
                    if support_bootstrap_ready
                    else CandidateAttemptStage.REJECTED
                ),
                reason_code=(
                    "evaluation_support_bootstrap_ready"
                    if support_bootstrap_ready
                    else "evaluation_support_preflight_missing"
                ),
            )
        status = "prerequisite" if support_bootstrap_ready else "rejected"
        return CandidateReplayAdmissionResult(
            gate_results=tuple(gate_results),
            replay_case_count=replay_case_count,
            replay_planned=replay_planned,
            reuses_replay_evidence=reuses_replay_evidence,
            effective_baseline_repetitions=effective_baseline_repetitions,
            effective_candidate_repetitions=effective_candidate_repetitions,
            replay_budget=replay_budget,
            capability_gates=capability_gates,
            capability_blocked=capability_blocked,
            terminal_result=terminal_candidate_evaluation_result(
                candidate=request.candidate,
                iteration_number=request.iteration_number,
                candidate_number=request.candidate_number,
                candidate_count=request.candidate_count,
                gate_results=gate_results,
                feedback_builder=runtime.feedback_builder,
                status=status,
            ),
        )

    return CandidateReplayAdmissionResult(
        gate_results=tuple(gate_results),
        replay_case_count=replay_case_count,
        replay_planned=replay_planned,
        reuses_replay_evidence=reuses_replay_evidence,
        effective_baseline_repetitions=effective_baseline_repetitions,
        effective_candidate_repetitions=effective_candidate_repetitions,
        replay_budget=replay_budget,
        capability_gates=capability_gates,
        capability_blocked=capability_blocked,
    )


def duplicate_candidate_evaluation_result(
    *,
    request: CandidateEvaluationRequest,
    gate_results: Iterable[GateResult],
) -> CandidateEvaluationResult:
    """Preserve the minimal historical duplicate-rejection feedback shape."""

    gates = tuple(gate_results)
    failed_gates = [gate for gate in gates if not gate.passed]
    feedback = (
        EvaluationSummary(
            variant_id=request.candidate.candidate_id,
            metrics={
                "failed_gates": [gate.gate_name for gate in failed_gates],
                "candidate_status": "rejected",
            },
            dataset_split="validation",
        ),
    )
    report_item = iteration_report_item(
        iteration_number=request.iteration_number,
        candidate_number=request.candidate_number,
        candidate_count=request.candidate_count,
        candidate=request.candidate,
        status="rejected",
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=failed_gates,
    )
    state = iteration_state(
        candidate=request.candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        replay_result=None,
        replay_dataset=None,
        gate_results=list(gates),
        feedback=feedback,
        status="rejected",
    )
    return CandidateEvaluationResult.from_tuple(
        (state, report_item, feedback)
    )


def execute_candidate_local_admission(
    request: CandidateEvaluationRequest,
    policy: CandidateLocalAdmissionPolicy,
) -> CandidateLocalAdmissionResult:
    """Evaluate local contracts and historical duplicate admission."""

    gate_results = list(request.precomputed_gate_results)
    if not gate_results:
        gate_results.extend(
            policy.gate_evaluator(
                request.candidate,
                current_content=request.target.load_current_content(),
                workspace_root=policy.workspace_root,
                max_chars=policy.max_candidate_chars,
                target_provenance=request.target_provenance,
                target_provenance_unresolved_reason=(
                    request.target_provenance_unresolved_reason
                ),
                allow_generated_target_mutation=(
                    policy.allow_generated_target_mutation
                ),
                allow_external_target_mutation=(
                    policy.allow_external_target_mutation
                ),
                target_intent=policy.target_intent,
                inferred_new_skill_policy=policy.inferred_new_skill_policy,
                apply_policy=request.apply_policy,
            )
        )
        if request.attempt_tracker is not None and request.attempt_key is not None:
            request.attempt_tracker.emit(
                request.attempt_key,
                CandidateAttemptStage.LOCAL_GATES,
            )
    if (
        not policy.skip_duplicate_rejected_candidate_gate
        and not request.source_disposition.bypass_historical_deduplication
    ):
        accepted_gate = duplicate_accepted_candidate_gate(
            request.candidate,
            accepted_candidate_ids=request.accepted_candidate_ids,
            apply_policy=request.apply_policy,
        )
        if accepted_gate is not None:
            gate_results.append(accepted_gate)
        rejected_gate = duplicate_rejected_candidate_gate(
            request.candidate,
            rejected_candidate_ids=request.rejected_candidate_ids,
            apply_policy=request.apply_policy,
        )
        if rejected_gate is not None:
            gate_results.append(rejected_gate)
    duplicate_blocked = any(
        gate.gate_name
        in {"duplicate_accepted_candidate", "duplicate_rejected_candidate"}
        and not gate.passed
        for gate in gate_results
    )
    if not duplicate_blocked:
        return CandidateLocalAdmissionResult(tuple(gate_results))
    if (
        request.attempt_tracker is not None
        and request.attempt_key is not None
        and not request.attempt_tracker.terminal(request.attempt_key)
    ):
        request.attempt_tracker.emit(
            request.attempt_key,
            CandidateAttemptStage.REJECTED,
            reason_code="duplicate_prior_candidate",
        )
    return CandidateLocalAdmissionResult(
        gate_results=tuple(gate_results),
        terminal_result=duplicate_candidate_evaluation_result(
            request=request,
            gate_results=gate_results,
        ),
    )
