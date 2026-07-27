from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aworld.self_evolve.constitution import (
    AgenticStageReportV1,
    AgenticStageStatus,
    ConstitutionContractError,
    SelfEvolveConstitutionV1,
    SelfEvolveStage,
    validate_report_chain,
)
from aworld.self_evolve.evidence import (
    ClaimVerificationVerdict,
    EvidenceClaimKind,
    EvidenceConflictStatus,
    EvidenceResolutionStatus,
    SelfImprovementEvidenceGraphV1,
    SemanticSourceDispositionKind,
    SelfImprovementCaseV1,
)
from aworld.self_evolve.evaluation_plan import (
    EvaluationDisposition,
    SemanticModelQualificationReportV1,
    SemanticQualificationRegistryV1,
    SelfImprovementEvaluationPlanV1,
)
from aworld.self_evolve.improvement_signals import (
    DatasetSplit,
    SelfImprovementSignalSetV1,
    SignalActionability,
)

from .chunking import SourceBundleV1
from .semantic_compiler import CompiledSemanticDatasetV1
from .semantic_resolver import semantic_candidate_consensus
from .semantic_workflow import (
    validate_evidence_graph_against_source_bundle,
)
from .types import (
    IngestionContractError,
    IngestionMode,
    fingerprint_json,
)


SEMANTIC_EVIDENCE_QUALITY_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_evidence_quality.v1"
)
SEMANTIC_QUALITY_GATE_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_quality_gate.v1"
)
DEFAULT_SEMANTIC_CONSENSUS_THRESHOLD = 0.80
DEFAULT_INGESTION_STAGES = (
    SelfEvolveStage.DISCOVER,
    SelfEvolveStage.UNDERSTAND,
    SelfEvolveStage.EXTRACT,
    SelfEvolveStage.VERIFY_COVERAGE_AND_ENTAILMENT,
    SelfEvolveStage.RESOLVE_AND_DETECT_CONFLICT,
    SelfEvolveStage.SYNTHESIZE_IMPROVEMENT_SIGNALS,
    SelfEvolveStage.PLAN_EVALUATION,
)


@dataclass(frozen=True)
class SemanticResolutionEvidenceV1:
    candidate_graphs: tuple[SelfImprovementEvidenceGraphV1, ...]
    resolver_output_fingerprints: tuple[str, ...]
    schema_version: str = (
        "aworld.self_evolve.semantic_resolution_evidence.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != (
            "aworld.self_evolve.semantic_resolution_evidence.v1"
        ):
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid semantic resolution evidence schema",
            )
        for value in self.resolver_output_fingerprints:
            if (
                not isinstance(value, str)
                or not value.startswith("sha256:")
                or len(value) != 71
            ):
                raise IngestionContractError(
                    "invalid_fingerprint",
                    "resolver output fingerprint must be SHA-256",
                )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict(include_fingerprint=False))

    def to_dict(
        self,
        *,
        include_fingerprint: bool = True,
    ) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "candidate_graphs": [
                item.to_dict()
                for item in self.candidate_graphs
            ],
            "resolver_output_fingerprints": list(
                self.resolver_output_fingerprints
            ),
        }
        if include_fingerprint:
            result["fingerprint"] = self.fingerprint
        return result

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticResolutionEvidenceV1":
        graphs = payload.get("candidate_graphs", ())
        outputs = payload.get("resolver_output_fingerprints", ())
        for value, name in (
            (graphs, "candidate_graphs"),
            (outputs, "resolver_output_fingerprints"),
        ):
            if not isinstance(value, Sequence) or isinstance(
                value,
                (str, bytes, bytearray),
            ):
                raise IngestionContractError(
                    "schema_invalid",
                    f"{name} must be an array",
                )
        evidence = cls(
            candidate_graphs=tuple(
                SelfImprovementEvidenceGraphV1.from_agent_dict(item)
                for item in graphs
                if isinstance(item, Mapping)
            ),
            resolver_output_fingerprints=tuple(
                str(item) for item in outputs
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )
        if len(evidence.candidate_graphs) != len(graphs):
            raise IngestionContractError(
                "schema_invalid",
                "candidate_graphs must contain only objects",
            )
        claimed = payload.get("fingerprint")
        if claimed is not None and claimed != evidence.fingerprint:
            raise IngestionContractError(
                "fingerprint_mismatch",
                "semantic resolution evidence fingerprint mismatch",
            )
        return evidence


@dataclass(frozen=True)
class SemanticQualificationEvidenceV1:
    registry: SemanticQualificationRegistryV1
    report: SemanticModelQualificationReportV1 | None
    model_profile_fingerprint: str
    provider_fingerprint: str
    semantic_protocol_fingerprint: str
    constitution_fingerprint: str
    corpus_fingerprint: str
    threshold_set_fingerprint: str
    schema_version: str = (
        "aworld.self_evolve.semantic_qualification_evidence.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != (
            "aworld.self_evolve.semantic_qualification_evidence.v1"
        ):
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid semantic qualification evidence schema",
            )
        for name in (
            "model_profile_fingerprint",
            "provider_fingerprint",
            "semantic_protocol_fingerprint",
            "constitution_fingerprint",
            "corpus_fingerprint",
            "threshold_set_fingerprint",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value.startswith("sha256:")
                or len(value) != 71
            ):
                raise IngestionContractError(
                    "invalid_fingerprint",
                    f"{name} must be SHA-256",
                )
    @property
    def qualified(self) -> bool:
        return self.registry.accepts(
            self.report,
            model_profile_fingerprint=self.model_profile_fingerprint,
            provider_fingerprint=self.provider_fingerprint,
            semantic_protocol_fingerprint=(
                self.semantic_protocol_fingerprint
            ),
            constitution_fingerprint=self.constitution_fingerprint,
            corpus_fingerprint=self.corpus_fingerprint,
            threshold_set_fingerprint=self.threshold_set_fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry": self.registry.to_dict(),
            "report": (
                self.report.to_dict()
                if self.report is not None
                else None
            ),
            "model_profile_fingerprint": (
                self.model_profile_fingerprint
            ),
            "provider_fingerprint": self.provider_fingerprint,
            "semantic_protocol_fingerprint": (
                self.semantic_protocol_fingerprint
            ),
            "constitution_fingerprint": (
                self.constitution_fingerprint
            ),
            "corpus_fingerprint": self.corpus_fingerprint,
            "threshold_set_fingerprint": (
                self.threshold_set_fingerprint
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticQualificationEvidenceV1":
        registry = payload.get("registry")
        report = payload.get("report")
        if not isinstance(registry, Mapping):
            raise IngestionContractError(
                "schema_invalid",
                "qualification registry must be an object",
            )
        if report is not None and not isinstance(report, Mapping):
            raise IngestionContractError(
                "schema_invalid",
                "qualification report must be an object",
            )
        return cls(
            registry=SemanticQualificationRegistryV1.from_dict(
                registry
            ),
            report=(
                SemanticModelQualificationReportV1.from_dict(report)
                if report is not None
                else None
            ),
            model_profile_fingerprint=str(
                payload.get("model_profile_fingerprint") or ""
            ),
            provider_fingerprint=str(
                payload.get("provider_fingerprint") or ""
            ),
            semantic_protocol_fingerprint=str(
                payload.get("semantic_protocol_fingerprint") or ""
            ),
            constitution_fingerprint=str(
                payload.get("constitution_fingerprint") or ""
            ),
            corpus_fingerprint=str(
                payload.get("corpus_fingerprint") or ""
            ),
            threshold_set_fingerprint=str(
                payload.get("threshold_set_fingerprint") or ""
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )


@dataclass(frozen=True)
class SemanticEvidenceQualityReportV1:
    source_span_coverage_rate: float
    semantic_source_disposition_coverage_rate: float
    unexplained_semantic_source_unit_count: int
    unresolved_semantic_source_unit_count: int
    semantic_entailment_coverage_rate: float
    contradicted_claim_count: int
    insufficient_claim_count: int
    entity_link_coverage_rate: float
    unresolved_entity_count: int
    comparison_completeness_rate: float
    semantic_conflict_count: int
    unresolved_semantic_conflict_count: int
    uncited_claim_count: int
    invalid_source_span_count: int
    dangling_evidence_reference_count: int
    judge_rubric_compatibility: float
    human_judge_disagreement_rate: float
    semantic_parse_consensus: float
    semantic_valid_candidate_count: int
    agentic_stage_completion_rate: float
    missing_stage_count: int
    signal_actionability_rate: float
    target_evidence_trace_count: int
    evaluation_plan_valid: bool
    semantic_model_profile_qualified: bool
    held_out_semantic_exposure_count: int
    semantic_resolution_execution_count: int
    semantic_resolution_deterministic_match: bool
    semantic_agent_model_call_count: int
    evaluation_planner_model_call_count: int
    constitution_fingerprint: str
    evidence_graph_logical_fingerprint: str
    evidence_graph_provenance_fingerprint: str
    source_bundle_fingerprint: str
    verified_eligible_plan_count: int = 0
    non_verified_trainable_plan_count: int = 0
    warning_reason_codes: tuple[str, ...] = ()
    failure_reason_codes: tuple[str, ...] = ()
    schema_version: str = SEMANTIC_EVIDENCE_QUALITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_EVIDENCE_QUALITY_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid semantic evidence quality schema",
            )
        for name in (
            "source_span_coverage_rate",
            "semantic_source_disposition_coverage_rate",
            "semantic_entailment_coverage_rate",
            "entity_link_coverage_rate",
            "comparison_completeness_rate",
            "judge_rubric_compatibility",
            "human_judge_disagreement_rate",
            "semantic_parse_consensus",
            "agentic_stage_completion_rate",
            "signal_actionability_rate",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise IngestionContractError(
                    "semantic_quality_rate_invalid",
                    f"{name} must be a finite rate",
                )
            object.__setattr__(self, name, float(value))
        for name in (
            "unexplained_semantic_source_unit_count",
            "unresolved_semantic_source_unit_count",
            "contradicted_claim_count",
            "insufficient_claim_count",
            "unresolved_entity_count",
            "semantic_conflict_count",
            "unresolved_semantic_conflict_count",
            "uncited_claim_count",
            "invalid_source_span_count",
            "dangling_evidence_reference_count",
            "semantic_valid_candidate_count",
            "missing_stage_count",
            "target_evidence_trace_count",
            "held_out_semantic_exposure_count",
            "semantic_resolution_execution_count",
            "semantic_agent_model_call_count",
            "evaluation_planner_model_call_count",
            "verified_eligible_plan_count",
            "non_verified_trainable_plan_count",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise IngestionContractError(
                    "semantic_quality_count_invalid",
                    f"{name} must be a non-negative integer",
                )
        for name in (
            "evaluation_plan_valid",
            "semantic_model_profile_qualified",
            "semantic_resolution_deterministic_match",
        ):
            if not isinstance(getattr(self, name), bool):
                raise IngestionContractError(
                    "semantic_quality_boolean_invalid",
                    f"{name} must be boolean",
                )
        for name in (
            "constitution_fingerprint",
            "evidence_graph_logical_fingerprint",
            "evidence_graph_provenance_fingerprint",
            "source_bundle_fingerprint",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value.startswith("sha256:")
                or len(value) != 71
            ):
                raise IngestionContractError(
                    "invalid_fingerprint",
                    f"{name} must be a SHA-256 fingerprint",
                )
        for values in (
            self.warning_reason_codes,
            self.failure_reason_codes,
        ):
            if len(values) != len(set(values)):
                raise IngestionContractError(
                    "duplicate_identity",
                    "semantic quality reason codes must be unique",
                )
        object.__setattr__(
            self,
            "warning_reason_codes",
            tuple(sorted(self.warning_reason_codes)),
        )
        object.__setattr__(
            self,
            "failure_reason_codes",
            tuple(sorted(self.failure_reason_codes)),
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict(include_fingerprint=False))

    def to_dict(
        self,
        *,
        include_fingerprint: bool = True,
    ) -> dict[str, Any]:
        result = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }
        result["warning_reason_codes"] = list(
            self.warning_reason_codes
        )
        result["failure_reason_codes"] = list(
            self.failure_reason_codes
        )
        if include_fingerprint:
            result["fingerprint"] = self.fingerprint
        return result

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticEvidenceQualityReportV1":
        kwargs = {
            name: payload[name]
            for name in cls.__dataclass_fields__
            if name in payload and name != "schema_version"
        }
        for name in (
            "warning_reason_codes",
            "failure_reason_codes",
        ):
            if name in kwargs:
                value = kwargs[name]
                if not isinstance(value, Sequence) or isinstance(
                    value,
                    (str, bytes, bytearray),
                ):
                    raise IngestionContractError(
                        "schema_invalid",
                        f"{name} must be an array",
                    )
                kwargs[name] = tuple(str(item) for item in value)
        report = cls(
            **kwargs,
            schema_version=str(payload.get("schema_version") or ""),
        )
        claimed = payload.get("fingerprint")
        if claimed is not None and claimed != report.fingerprint:
            raise IngestionContractError(
                "fingerprint_mismatch",
                "semantic quality report fingerprint mismatch",
            )
        return report


@dataclass(frozen=True)
class SemanticQualityGateDecisionV1:
    mode: IngestionMode
    allowed: bool
    reason_codes: tuple[str, ...]
    quality_report_fingerprint: str
    schema_version: str = SEMANTIC_QUALITY_GATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_QUALITY_GATE_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid semantic quality gate schema",
            )
        object.__setattr__(self, "mode", IngestionMode(self.mode))
        object.__setattr__(
            self,
            "reason_codes",
            tuple(sorted(self.reason_codes)),
        )

    @property
    def passed(self) -> bool:
        return self.allowed

    @property
    def reason_code(self) -> str:
        return (
            "semantic_ingestion_quality_passed"
            if self.allowed
            else (
                self.reason_codes[0]
                if self.reason_codes
                else "semantic_ingestion_quality_failed"
            )
        )

    @property
    def warning_reason_codes(self) -> tuple[str, ...]:
        return ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gate_name": "dataset_ingestion",
            "passed": self.allowed,
            "reason_code": self.reason_code,
            "warning_reason_codes": [],
            "details": {
                "normalization_kind": "semantic_evidence",
                "semantic_reason_codes": list(self.reason_codes),
                "quality_report_fingerprint": (
                    self.quality_report_fingerprint
                ),
            },
            "mode": self.mode.value,
            "allowed": self.allowed,
            "reason_codes": list(self.reason_codes),
            "quality_report_fingerprint": (
                self.quality_report_fingerprint
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticQualityGateDecisionV1":
        reasons = payload.get("reason_codes", ())
        if not isinstance(reasons, Sequence) or isinstance(
            reasons,
            (str, bytes, bytearray),
        ):
            raise IngestionContractError(
                "schema_invalid",
                "semantic gate reason_codes must be an array",
            )
        return cls(
            mode=IngestionMode(str(payload.get("mode") or "")),
            allowed=payload.get("allowed") is True,
            reason_codes=tuple(str(item) for item in reasons),
            quality_report_fingerprint=str(
                payload.get("quality_report_fingerprint") or ""
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )


def build_semantic_evidence_quality_report(
    *,
    bundle: SourceBundleV1,
    graph: SelfImprovementEvidenceGraphV1,
    constitution: SelfEvolveConstitutionV1,
    stage_reports: Sequence[AgenticStageReportV1],
    signal_set: SelfImprovementSignalSetV1 | None = None,
    semantic_cases: Sequence[SelfImprovementCaseV1] = (),
    evaluation_plans: Sequence[
        SelfImprovementEvaluationPlanV1
    ] = (),
    compiled_dataset: CompiledSemanticDatasetV1 | None = None,
    resolution_evidence: SemanticResolutionEvidenceV1 | None = None,
    qualification_evidence: (
        SemanticQualificationEvidenceV1 | None
    ) = None,
    required_stages: Sequence[SelfEvolveStage] = (
        DEFAULT_INGESTION_STAGES
    ),
) -> SemanticEvidenceQualityReportV1:
    """Recompute semantic quality only from frozen framework artifacts."""

    source_validation = validate_evidence_graph_against_source_bundle(
        bundle,
        graph,
    )
    case_by_id = {item.case_id: item for item in semantic_cases}
    plan_by_case = {
        item.case_id: item for item in evaluation_plans
    }
    evaluation_plan_valid = (
        bool(case_by_id)
        and len(case_by_id) == len(semantic_cases)
        and len(plan_by_case) == len(evaluation_plans)
        and set(case_by_id) == set(plan_by_case)
    )
    if evaluation_plan_valid and signal_set is not None:
        try:
            for case_id, case in case_by_id.items():
                case.validate_against(graph)
                plan_by_case[case_id].validate_references(
                    graph=graph,
                    case=case,
                    signal_set=signal_set,
                )
        except (ValueError, KeyError):
            evaluation_plan_valid = False
    elif signal_set is None:
        evaluation_plan_valid = False
    trainable_case_ids = (
        {
            case_id
            for case_id, split in signal_set.case_splits.items()
            if split in {DatasetSplit.TRAIN, DatasetSplit.VALIDATION}
        }
        if signal_set is not None
        else set()
    )
    trainable_plans = [
        plan_by_case[case_id]
        for case_id in sorted(trainable_case_ids)
        if case_id in plan_by_case
    ]
    verified_eligible_plan_count = sum(
        plan.disposition
        is EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
        for plan in trainable_plans
    )
    non_verified_trainable_plan_count = (
        len(trainable_plans) - verified_eligible_plan_count
    )
    target_evidence_trace_count = (
        len(compiled_dataset.target_evidence_bundle.executions)
        if compiled_dataset is not None
        else 0
    )
    held_out_semantic_exposure_count = 0
    if compiled_dataset is not None and signal_set is not None:
        held_out_semantic_exposure_count = sum(
            len(item.self_improvement_signals)
            for item in compiled_dataset.normalized_cases
            if signal_set.case_splits.get(item.case_id)
            is DatasetSplit.HELD_OUT
        )
    semantic_model_profile_qualified = (
        qualification_evidence.qualified
        if qualification_evidence is not None
        else False
    )
    candidate_graphs = (
        resolution_evidence.candidate_graphs
        if resolution_evidence is not None
        else ()
    )
    semantic_valid_candidate_count = len(candidate_graphs)
    semantic_parse_consensus = (
        semantic_candidate_consensus(candidate_graphs)
        if len(candidate_graphs) >= 2
        else 0.0
    )
    resolver_outputs = (
        resolution_evidence.resolver_output_fingerprints
        if resolution_evidence is not None
        else ()
    )
    semantic_resolution_execution_count = len(resolver_outputs)
    semantic_resolution_deterministic_match = (
        len(resolver_outputs) >= 2
        and len(set(resolver_outputs)) == 1
        and resolver_outputs[0] == graph.logical_fingerprint
    )
    verifications = {
        item.verification_id: item
        for item in graph.claim_verifications
    }
    accepted_claims = [
        item
        for item in graph.claims
        if item.resolution_status is not EvidenceResolutionStatus.REJECTED
    ]
    cited_claims = [
        item for item in accepted_claims if item.source_span_ids
    ]
    entailed_claims = [
        claim
        for claim in accepted_claims
        if any(
            verifications[verification_id].verdict
            is ClaimVerificationVerdict.ENTAILED
            for verification_id in claim.verification_ids
        )
    ]
    contradicted_claims = [
        claim
        for claim in accepted_claims
        if any(
            verifications[verification_id].verdict
            is ClaimVerificationVerdict.CONTRADICTED
            for verification_id in claim.verification_ids
        )
    ]
    insufficient_claims = [
        claim
        for claim in accepted_claims
        if any(
            verifications[verification_id].verdict
            in {
                ClaimVerificationVerdict.INSUFFICIENT,
                ClaimVerificationVerdict.AMBIGUOUS,
            }
            for verification_id in claim.verification_ids
        )
    ]
    dispositions = {
        item.source_unit_id: item for item in graph.source_dispositions
    }
    known_dispositions = [
        dispositions[item]
        for item in bundle.source_unit_ids
        if item in dispositions
    ]
    unresolved_units = sum(
        item.disposition
        in {
            SemanticSourceDispositionKind.UNRESOLVED,
            SemanticSourceDispositionKind.DEFERRED,
        }
        for item in known_dispositions
    )
    comparison_claims = [
        item
        for item in accepted_claims
        if item.kind
        in {
            EvidenceClaimKind.HUMAN_COMPARISON,
            EvidenceClaimKind.LLM_JUDGE_ASSESSMENT,
        }
    ]
    complete_comparisons = [
        item
        for item in comparison_claims
        if len(item.object_entity_ids) >= 2
        and (
            item.payload.get("preferred_entity_id") is not None
            or item.payload.get("score") is not None
            or item.payload.get("ranking") is not None
        )
        and item.payload.get("scope") is not None
    ]
    unresolved_entities = sum(
        item.attributes.get("resolution_status") == "unresolved"
        for item in graph.entities
    )
    entity_reference_count = sum(
        len(item.subject_entity_ids) + len(item.object_entity_ids)
        for item in accepted_claims
    )
    resolved_entity_reference_count = sum(
        len(item.subject_entity_ids) + len(item.object_entity_ids)
        for item in accepted_claims
        if all(
            entity_id
            not in {
                entity.entity_id
                for entity in graph.entities
                if entity.attributes.get("resolution_status")
                == "unresolved"
            }
            for entity_id in (
                *item.subject_entity_ids,
                *item.object_entity_ids,
            )
        )
    )
    rubric_compatibility = _judge_rubric_compatibility(
        comparison_claims
    )
    human_judge_disagreement = _human_judge_disagreement_rate(
        comparison_claims
    )
    try:
        active_reports = validate_report_chain(
            constitution,
            stage_reports,
        )
        stage_chain_valid = True
    except ConstitutionContractError:
        active_reports = ()
        stage_chain_valid = False
    complete_stages = {item.stage for item in active_reports}
    required = {SelfEvolveStage(item) for item in required_stages}
    missing_stages = required - complete_stages
    model_call_count = sum(
        item.model_call_count for item in stage_reports
    )
    planner_call_count = sum(
        item.model_call_count
        for item in stage_reports
        if item.stage is SelfEvolveStage.PLAN_EVALUATION
    )
    signals = signal_set.signals if signal_set is not None else ()
    actionable = sum(
        item.actionability is SignalActionability.ACTIONABLE
        for item in signals
    )
    unresolved_conflicts = sum(
        item.status is EvidenceConflictStatus.UNRESOLVED
        for item in graph.conflicts
    )
    failures: set[str] = set()
    warnings: set[str] = set()
    if source_validation.unexplained_source_unit_count:
        failures.add("semantic_source_units_unexplained")
    if (
        source_validation.invalid_source_span_count
        or source_validation.dangling_chunk_reference_count
        or source_validation.unknown_disposition_count
    ):
        failures.add("semantic_source_spans_invalid")
    if not accepted_claims:
        failures.add("semantic_claims_empty")
    if len(cited_claims) != len(accepted_claims):
        failures.add("semantic_claims_uncited")
    if contradicted_claims:
        failures.add("semantic_claims_contradicted")
    if missing_stages:
        failures.add("semantic_stage_reports_missing")
    if not stage_chain_valid:
        failures.add("semantic_stage_report_chain_invalid")
    if not semantic_resolution_deterministic_match:
        failures.add("semantic_resolution_nondeterministic")
    if semantic_resolution_execution_count < 2:
        failures.add("semantic_resolution_evidence_insufficient")
    if not evaluation_plan_valid:
        failures.add("semantic_evaluation_plan_invalid")
    if held_out_semantic_exposure_count:
        failures.add("held_out_semantic_exposure")
    if target_evidence_trace_count == 0:
        failures.add("semantic_target_evidence_empty")
    if not signals:
        failures.add("semantic_signals_empty")
    if unresolved_units:
        warnings.add("semantic_source_units_unresolved")
    if insufficient_claims:
        warnings.add("semantic_claims_insufficient")
    if unresolved_entities:
        warnings.add("semantic_entities_unresolved")
    if unresolved_conflicts:
        warnings.add("semantic_conflicts_unresolved")
    if signals and actionable != len(signals):
        warnings.add("semantic_signals_non_actionable")
    if non_verified_trainable_plan_count:
        warnings.add("semantic_trainable_plans_not_verified")

    return SemanticEvidenceQualityReportV1(
        source_span_coverage_rate=_rate(
            len(cited_claims),
            len(accepted_claims),
        ),
        semantic_source_disposition_coverage_rate=_rate(
            len(known_dispositions),
            len(bundle.source_unit_ids),
        ),
        unexplained_semantic_source_unit_count=(
            source_validation.unexplained_source_unit_count
        ),
        unresolved_semantic_source_unit_count=unresolved_units,
        semantic_entailment_coverage_rate=_rate(
            len(entailed_claims),
            len(accepted_claims),
        ),
        contradicted_claim_count=len(contradicted_claims),
        insufficient_claim_count=len(insufficient_claims),
        entity_link_coverage_rate=_rate(
            resolved_entity_reference_count,
            entity_reference_count,
        ),
        unresolved_entity_count=unresolved_entities,
        comparison_completeness_rate=_rate(
            len(complete_comparisons),
            len(comparison_claims),
        ),
        semantic_conflict_count=len(graph.conflicts),
        unresolved_semantic_conflict_count=unresolved_conflicts,
        uncited_claim_count=len(accepted_claims) - len(cited_claims),
        invalid_source_span_count=(
            source_validation.invalid_source_span_count
        ),
        dangling_evidence_reference_count=(
            source_validation.dangling_chunk_reference_count
            + source_validation.unknown_disposition_count
        ),
        judge_rubric_compatibility=rubric_compatibility,
        human_judge_disagreement_rate=human_judge_disagreement,
        semantic_parse_consensus=semantic_parse_consensus,
        semantic_valid_candidate_count=semantic_valid_candidate_count,
        agentic_stage_completion_rate=_rate(
            len(required & complete_stages),
            len(required),
        ),
        missing_stage_count=len(missing_stages),
        signal_actionability_rate=(
            _rate(actionable, len(signals)) if signals else 0.0
        ),
        target_evidence_trace_count=target_evidence_trace_count,
        evaluation_plan_valid=evaluation_plan_valid,
        semantic_model_profile_qualified=(
            semantic_model_profile_qualified
        ),
        held_out_semantic_exposure_count=(
            held_out_semantic_exposure_count
        ),
        semantic_resolution_execution_count=(
            semantic_resolution_execution_count
        ),
        semantic_resolution_deterministic_match=(
            semantic_resolution_deterministic_match
        ),
        semantic_agent_model_call_count=model_call_count,
        evaluation_planner_model_call_count=planner_call_count,
        constitution_fingerprint=constitution.fingerprint,
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
        evidence_graph_provenance_fingerprint=(
            graph.provenance_fingerprint
        ),
        source_bundle_fingerprint=bundle.fingerprint,
        verified_eligible_plan_count=verified_eligible_plan_count,
        non_verified_trainable_plan_count=(
            non_verified_trainable_plan_count
        ),
        warning_reason_codes=tuple(warnings),
        failure_reason_codes=tuple(failures),
    )


def evaluate_semantic_quality_gate(
    report: SemanticEvidenceQualityReportV1,
    *,
    mode: IngestionMode,
    consensus_threshold: float = DEFAULT_SEMANTIC_CONSENSUS_THRESHOLD,
) -> SemanticQualityGateDecisionV1:
    normalized_mode = IngestionMode(mode)
    reasons = set(report.failure_reason_codes)
    if normalized_mode is IngestionMode.AUTO_VERIFIED:
        required_rates = {
            "semantic_source_span_coverage_incomplete": (
                report.source_span_coverage_rate
            ),
            "semantic_source_disposition_coverage_incomplete": (
                report.semantic_source_disposition_coverage_rate
            ),
            "semantic_entailment_coverage_incomplete": (
                report.semantic_entailment_coverage_rate
            ),
            "semantic_entity_link_coverage_incomplete": (
                report.entity_link_coverage_rate
            ),
            "semantic_comparison_completeness_incomplete": (
                report.comparison_completeness_rate
            ),
            "semantic_stage_completion_incomplete": (
                report.agentic_stage_completion_rate
            ),
            "semantic_signal_actionability_incomplete": (
                report.signal_actionability_rate
            ),
        }
        reasons.update(
            reason
            for reason, value in required_rates.items()
            if value != 1.0
        )
        if report.unresolved_semantic_source_unit_count:
            reasons.add("semantic_source_units_unresolved")
        if report.insufficient_claim_count:
            reasons.add("semantic_claims_insufficient")
        if report.unresolved_entity_count:
            reasons.add("semantic_entities_unresolved")
        if report.unresolved_semantic_conflict_count:
            reasons.add("semantic_conflicts_unresolved")
        if not report.semantic_model_profile_qualified:
            reasons.add("semantic_model_not_qualified")
        if report.semantic_valid_candidate_count < 2:
            reasons.add("semantic_candidate_count_insufficient")
        if report.semantic_parse_consensus < consensus_threshold:
            reasons.add("semantic_parse_consensus_below_threshold")
        if report.non_verified_trainable_plan_count:
            reasons.add("semantic_trainable_plan_not_verified")
    return SemanticQualityGateDecisionV1(
        mode=normalized_mode,
        allowed=not reasons,
        reason_codes=tuple(reasons),
        quality_report_fingerprint=report.fingerprint,
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return float(numerator) / float(denominator)


def _judge_rubric_compatibility(claims: Sequence[Any]) -> float:
    grouped: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    for claim in claims:
        if claim.kind is not EvidenceClaimKind.LLM_JUDGE_ASSESSMENT:
            continue
        key = (
            str(claim.payload.get("scope") or ""),
            tuple(sorted(claim.object_entity_ids)),
        )
        grouped.setdefault(key, set()).add(
            str(claim.payload.get("rubric_id") or "")
        )
    if not grouped:
        return 1.0
    return _rate(
        sum(len(rubrics) == 1 and "" not in rubrics for rubrics in grouped.values()),
        len(grouped),
    )


def _human_judge_disagreement_rate(claims: Sequence[Any]) -> float:
    human: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    judge: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    for claim in claims:
        preferred = claim.payload.get("preferred_entity_id")
        if not isinstance(preferred, str):
            continue
        key = (
            str(claim.payload.get("scope") or ""),
            tuple(sorted(claim.object_entity_ids)),
        )
        target = (
            human
            if claim.kind is EvidenceClaimKind.HUMAN_COMPARISON
            else judge
        )
        target.setdefault(key, set()).add(preferred)
    shared = set(human) & set(judge)
    return _rate(
        sum(human[key] != judge[key] for key in shared),
        len(shared),
    )
