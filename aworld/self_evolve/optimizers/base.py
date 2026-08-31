from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING, Mapping, Protocol

from aworld.self_evolve.candidate_protocol import (
    MAX_EXPOSED_IMPROVEMENT_SIGNAL_IDS,
    candidate_output_contract_fingerprint,
)
from aworld.self_evolve.candidate_errors import (
    normalize_candidate_representation,
)
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


MAX_PROMPT_TRAINABLE_CASES = 32
MAX_PROMPT_IMPROVEMENT_SIGNALS_PER_CASE = 8


class CandidateSemanticValidationError(ValueError):
    """Bounded request-context failure eligible for same-slot repair."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field_path: str | None = None,
        representation: str | None = None,
        repairable: bool = True,
        allowed_improvement_signal_ids: tuple[str, ...] = (),
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = str(code)
        self.field_path = field_path
        self.representation = (
            normalize_candidate_representation(representation).value
            if representation is not None
            else None
        )
        self.repairable = bool(repairable)
        self.allowed_improvement_signal_ids = tuple(
            allowed_improvement_signal_ids
        )
        self.details = dict(details or {})
        self.contract_fingerprint = candidate_output_contract_fingerprint(
            self.allowed_improvement_signal_ids
        )
        super().__init__(str(message)[:512])

    def to_diagnostic(self) -> dict[str, object]:
        diagnostic: dict[str, object] = {
            "code": self.code,
            "stage": "candidate_semantic_validation",
            "failure_class": "candidate",
            "repairable": self.repairable,
            "contract_fingerprint": self.contract_fingerprint,
            "allowed_improvement_signal_ids": list(
                self.allowed_improvement_signal_ids
            ),
        }
        if self.field_path is not None:
            diagnostic["field_path"] = self.field_path
        if self.representation is not None:
            diagnostic["representation"] = self.representation
        if self.details:
            diagnostic["details"] = dict(self.details)
        return diagnostic


class CandidateSourceKind(str, Enum):
    """How an optimizer obtained the candidate returned for this attempt."""

    GENERATED = "generated"
    STORED_EVIDENCE_RERUN = "stored_evidence_rerun"


class CandidateGenerationOutcomeKind(str, Enum):
    """Terminal generation-layer disposition for one scheduled model slot."""

    ADMITTED = "admitted"
    POLICY_FILTERED = "policy_filtered"
    NOOP_FILTERED = "noop_filtered"
    DUPLICATE_FILTERED = "duplicate_filtered"
    MATERIALIZATION_FAILED = "materialization_failed"
    PROTOCOL_INVALID = "protocol_invalid"
    INFRASTRUCTURE_FAILED = "infrastructure_failed"


@dataclass(frozen=True)
class CandidateGenerationOutcome:
    """Content-free causal result preserved across optimizer orchestration.

    Candidate source is deliberately excluded.  Stable identities and typed
    constraints are sufficient for lifecycle accounting, focused repair, and
    Campaign disposition without leaking a rejected package into diagnostics.
    """

    candidate_index: int
    kind: CandidateGenerationOutcomeKind
    candidate_id: str | None = None
    candidate_fingerprint: str | None = None
    semantic_fingerprint: str | None = None
    policy_id: str | None = None
    enforcement: str | None = None
    repairable: bool = False
    reason_codes: tuple[str, ...] = ()
    constraint_ids: tuple[str, ...] = ()
    active_frontier_key: str | None = None
    affected_case_ids: tuple[str, ...] = ()
    strategy_id: str | None = None
    schema_version: str = "aworld.self_evolve.candidate_generation_outcome.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "aworld.self_evolve.candidate_generation_outcome.v1":
            raise ValueError("unsupported candidate generation outcome schema")
        if isinstance(self.candidate_index, bool) or self.candidate_index < 0:
            raise ValueError("candidate generation index must be non-negative")
        object.__setattr__(
            self,
            "kind",
            CandidateGenerationOutcomeKind(self.kind),
        )
        if self.enforcement not in {None, "hard", "heuristic"}:
            raise ValueError("candidate policy enforcement must be hard or heuristic")
        if self.kind is CandidateGenerationOutcomeKind.POLICY_FILTERED:
            if not self.policy_id or self.enforcement != "hard":
                raise ValueError("policy-filtered outcome requires a hard policy_id")
        if self.kind is CandidateGenerationOutcomeKind.ADMITTED and not self.candidate_id:
            raise ValueError("admitted generation outcome requires candidate_id")
        for field_name in (
            "reason_codes",
            "constraint_ids",
            "affected_case_ids",
        ):
            values = getattr(self, field_name)
            if any(not isinstance(item, str) or not item for item in values):
                raise ValueError(f"{field_name} must contain non-empty strings")
            object.__setattr__(self, field_name, tuple(dict.fromkeys(values)))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_index": self.candidate_index,
            "kind": self.kind.value,
            "candidate_id": self.candidate_id,
            "candidate_fingerprint": self.candidate_fingerprint,
            "semantic_fingerprint": self.semantic_fingerprint,
            "policy_id": self.policy_id,
            "enforcement": self.enforcement,
            "repairable": self.repairable,
            "reason_codes": list(self.reason_codes),
            "constraint_ids": list(self.constraint_ids),
            "active_frontier_key": self.active_frontier_key,
            "affected_case_ids": list(self.affected_case_ids),
            "strategy_id": self.strategy_id,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "CandidateGenerationOutcome":
        def strings(field_name: str) -> tuple[str, ...]:
            raw = value.get(field_name)
            if not isinstance(raw, (list, tuple)):
                return ()
            return tuple(item for item in raw if isinstance(item, str) and item)

        raw_index = value.get("candidate_index")
        if not isinstance(raw_index, int) or isinstance(raw_index, bool):
            raise ValueError("candidate generation outcome requires candidate_index")
        return cls(
            candidate_index=raw_index,
            kind=CandidateGenerationOutcomeKind(str(value.get("kind") or "")),
            candidate_id=(
                value.get("candidate_id")
                if isinstance(value.get("candidate_id"), str)
                else None
            ),
            candidate_fingerprint=(
                value.get("candidate_fingerprint")
                if isinstance(value.get("candidate_fingerprint"), str)
                else None
            ),
            semantic_fingerprint=(
                value.get("semantic_fingerprint")
                if isinstance(value.get("semantic_fingerprint"), str)
                else None
            ),
            policy_id=(
                value.get("policy_id")
                if isinstance(value.get("policy_id"), str)
                else None
            ),
            enforcement=(
                value.get("enforcement")
                if isinstance(value.get("enforcement"), str)
                else None
            ),
            repairable=value.get("repairable") is True,
            reason_codes=strings("reason_codes"),
            constraint_ids=strings("constraint_ids"),
            active_frontier_key=(
                value.get("active_frontier_key")
                if isinstance(value.get("active_frontier_key"), str)
                else None
            ),
            affected_case_ids=strings("affected_case_ids"),
            strategy_id=(
                value.get("strategy_id")
                if isinstance(value.get("strategy_id"), str)
                else None
            ),
            schema_version=str(
                value.get("schema_version")
                or "aworld.self_evolve.candidate_generation_outcome.v1"
            ),
        )


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
    target_package_sources: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )
    handbook_slice: Mapping[str, object] | None = None
    evolution_context: EvolutionContext | None = None
    improvement_signal_set_fingerprint: str | None = None
    consumed_mutation_families: tuple[str, ...] = ()
    active_repair_frontier_keys: tuple[str | None, ...] = ()
    skill_evolution_contract: Mapping[str, object] | None = None

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
        target_package_sources: Mapping[
            str, Mapping[str, object]
        ] | None = None,
        handbook_slice: Mapping[str, object] | None = None,
        consumed_mutation_families: tuple[str, ...] = (),
        active_repair_frontier_keys: tuple[str | None, ...] = (),
        skill_evolution_contract: Mapping[str, object] | None = None,
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
            target_package_sources=dict(target_package_sources or {}),
            handbook_slice=handbook_slice,
            consumed_mutation_families=tuple(consumed_mutation_families),
            active_repair_frontier_keys=tuple(active_repair_frontier_keys),
            skill_evolution_contract=(
                dict(skill_evolution_contract)
                if skill_evolution_contract is not None
                else None
            ),
            improvement_signal_set_fingerprint=(
                str(
                    dataset.recipe.source[
                        "improvement_signal_set_fingerprint"
                    ]
                )
                if dataset.recipe.source.get(
                    "improvement_signal_set_fingerprint"
                )
                is not None
                else None
            ),
        )


def exposed_improvement_signal_ids(
    request: OptimizerRequest,
) -> tuple[str, ...]:
    signal_ids: list[str] = []
    for case in request.trainable_cases[:MAX_PROMPT_TRAINABLE_CASES]:
        for signal in getattr(case, "self_improvement_signals", ())[
            :MAX_PROMPT_IMPROVEMENT_SIGNALS_PER_CASE
        ]:
            if not isinstance(signal, Mapping):
                continue
            signal_id = signal.get("signal_id")
            if isinstance(signal_id, str) and signal_id:
                signal_ids.append(signal_id)
    return tuple(dict.fromkeys(signal_ids))[
        :MAX_EXPOSED_IMPROVEMENT_SIGNAL_IDS
    ]


def declared_addressed_improvement_signal_ids(
    request: OptimizerRequest,
    output: Any,
) -> tuple[str, ...]:
    """Accept only candidate-declared IDs that were exposed in its context."""

    payload = output
    if isinstance(payload, Mapping):
        expected_output = payload.get("expected_output")
        if isinstance(expected_output, Mapping):
            payload = expected_output
    raw_ids = (
        payload.get("addressed_improvement_signal_ids", ())
        if isinstance(payload, Mapping)
        else ()
    )
    if not isinstance(raw_ids, (list, tuple)) or any(
        not isinstance(item, str) or not item
        for item in raw_ids
    ):
        raise ValueError(
            "addressed improvement signal IDs must be a string array"
        )
    addressed = tuple(dict.fromkeys(raw_ids))
    exposed_ids = exposed_improvement_signal_ids(request)
    exposed = set(exposed_ids)
    unknown = set(addressed) - exposed
    if unknown:
        raise CandidateSemanticValidationError(
            "unexposed_improvement_signal_ids",
            "candidate addressed an improvement signal that was not exposed",
            field_path="addressed_improvement_signal_ids",
            allowed_improvement_signal_ids=exposed_ids,
        )
    return addressed


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
    generation_outcomes: tuple[CandidateGenerationOutcome, ...] = ()


class CandidateOptimizer(Protocol):
    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        """Propose candidate variants without reading held-out eval cases."""
