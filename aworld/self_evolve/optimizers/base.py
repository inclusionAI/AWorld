from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Mapping, Protocol

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.lessons import LessonRecord
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    OptimizerLineage,
    SelfEvolveTargetRef,
)

if TYPE_CHECKING:
    from aworld.self_evolve.evolution_context import EvolutionContext
    from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
    from aworld.self_evolve.repair_conformance import RepairConformanceContract


class CandidateSourceKind(str, Enum):
    """How an optimizer obtained the candidate returned for this attempt."""

    GENERATED = "generated"
    STORED_EVIDENCE_RERUN = "stored_evidence_rerun"


@dataclass(frozen=True)
class CandidateSourceDisposition:
    """Typed candidate-source semantics consumed by the orchestration layer.

    A stored-evidence rerun is a new evaluation attempt over an existing source
    candidate.  It may bypass historical deduplication, but never same-run
    collision checks or the requirement to complete a fresh evaluation.
    """

    kind: CandidateSourceKind = CandidateSourceKind.GENERATED
    source_run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", CandidateSourceKind(self.kind))
        if self.kind is CandidateSourceKind.STORED_EVIDENCE_RERUN:
            if (
                not isinstance(self.source_run_id, str)
                or not self.source_run_id.strip()
            ):
                raise ValueError("stored-evidence rerun requires source_run_id")
        elif self.source_run_id is not None:
            raise ValueError("generated candidate source cannot declare source_run_id")

    @property
    def bypass_historical_deduplication(self) -> bool:
        return self.kind is CandidateSourceKind.STORED_EVIDENCE_RERUN

    @property
    def requires_fresh_evaluation(self) -> bool:
        return self.kind is CandidateSourceKind.STORED_EVIDENCE_RERUN

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source_run_id": self.source_run_id,
            "bypass_historical_deduplication": (
                self.bypass_historical_deduplication
            ),
            "requires_fresh_evaluation": self.requires_fresh_evaluation,
        }


@dataclass(frozen=True)
class OptimizerRequest:
    target: SelfEvolveTargetRef
    current_content: str
    target_fingerprint: str
    trace_packs: tuple[TracePack, ...]
    validation_feedback: tuple[EvaluationSummary, ...] = ()
    prior_feedback: tuple[EvaluationSummary, ...] = ()
    lesson_records: tuple[LessonRecord, ...] = ()
    trainable_cases: tuple[EvalCase, ...] = ()
    max_candidates: int = 1
    replay_requirements: tuple[ReplayCapabilityRequirement, ...] = ()
    target_package_inventory: tuple[str, ...] = ()
    evolution_context: EvolutionContext | None = None

    @classmethod
    def from_dataset(
        cls,
        *,
        target: SelfEvolveTargetRef,
        current_content: str,
        target_fingerprint: str,
        trace_packs: tuple[TracePack, ...],
        validation_feedback: tuple[EvaluationSummary, ...],
        prior_feedback: tuple[EvaluationSummary, ...] = (),
        lesson_records: tuple[LessonRecord, ...] = (),
        dataset: SelfEvolveDataset,
        max_candidates: int = 1,
        replay_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
        target_package_inventory: tuple[str, ...] = (),
    ) -> "OptimizerRequest":
        trainable_ids = set(dataset.recipe.trainable_case_ids)
        return cls(
            target=target,
            current_content=current_content,
            target_fingerprint=target_fingerprint,
            trace_packs=trace_packs,
            validation_feedback=validation_feedback,
            prior_feedback=prior_feedback,
            lesson_records=lesson_records,
            trainable_cases=tuple(
                case for case in dataset.cases if case.case_id in trainable_ids
            ),
            max_candidates=max_candidates,
            replay_requirements=tuple(replay_requirements),
            target_package_inventory=tuple(target_package_inventory),
        )


@dataclass(frozen=True)
class OptimizerResult:
    candidates: tuple[CandidateVariant, ...]
    lineage: tuple[OptimizerLineage, ...] = ()
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    # Non-persistent execution-only context.  Exact repair assertions must not
    # be copied into diagnostics, prompts, feedback, lineage, or reports.
    private_context: Mapping[str, "RepairConformanceContract"] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    source_disposition: CandidateSourceDisposition = field(
        default_factory=CandidateSourceDisposition
    )


class CandidateOptimizer(Protocol):
    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        """Propose candidate variants without reading held-out eval cases."""
