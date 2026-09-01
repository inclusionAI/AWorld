"""Typed contracts for explicit-run and candidate-evaluation execution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import AbstractSet, Protocol

from aworld.self_evolve.budget import (
    BudgetDecision,
    BudgetStage,
    CandidateAttemptKey,
    CandidateAttemptStage,
)
from aworld.self_evolve.campaign_policy import is_verified_apply_policy
from aworld.self_evolve.challenger import ChallengeReport
from aworld.self_evolve.credit_assignment import (
    TargetSelectionDecision,
    TargetSelectionReport,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.optimizers.base import CandidateSourceDisposition
from aworld.self_evolve.provenance import (
    InferredNewSkillPolicy,
    TargetMutationIntent,
    TargetProvenance,
)
from aworld.self_evolve.regression import RegressionEvidence
from aworld.self_evolve.replay import (
    CandidateReplayResult,
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
