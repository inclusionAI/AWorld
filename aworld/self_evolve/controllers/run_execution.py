"""Typed contracts for explicit-run and candidate-evaluation execution."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import AbstractSet, Protocol

from aworld.self_evolve.budget import (
    BudgetDecision,
    BudgetStage,
    CandidateAttemptKey,
    CandidateAttemptStage,
)
from aworld.self_evolve.credit_assignment import (
    TargetSelectionDecision,
    TargetSelectionReport,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.optimizers.base import CandidateSourceDisposition
from aworld.self_evolve.provenance import TargetProvenance
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
