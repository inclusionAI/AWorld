from __future__ import annotations

from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from aworld.self_evolve.evaluation_plan import (
    EvidenceAuthorityContextV1,
    EvaluationDisposition,
    ManifestOrigin,
    SelfImprovementEvaluationPlanV1,
)
from aworld.self_evolve.evidence import (
    EvidenceClaimKind,
    EvidenceResolutionStatus,
    SelfImprovementCaseV1,
    SelfImprovementEvidenceGraphV1,
)
from aworld.self_evolve.improvement_signals import (
    DatasetSplit,
    SelfImprovementSignalV1,
    SelfImprovementSignalSetV1,
    TargetEvidenceBundleV1,
    TargetExecutionEvidenceV1,
)

from .chunking import SourceBundleV1
from .types import (
    CaseSourceProvenance,
    IngestionContractError,
    NormalizedCaseRecord,
    fingerprint_json,
    validate_fingerprint,
    validate_safe_id,
)


SEMANTIC_COMPILATION_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_compilation.v1"
)
SEMANTIC_TRACE_RESOLUTION_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_trace_resolution.v1"
)
TRACE_SOURCE_BINDING_SCHEMA_VERSION = (
    "aworld.self_evolve.trace_source_binding.v1"
)
TRACE_CANDIDATE_ATTESTATION_SCHEMA_VERSION = (
    "aworld.self_evolve.trace_candidate_attestation.v1"
)
TRACE_EXTRACTION_ATTESTATION_SCHEMA_VERSION = (
    "aworld.self_evolve.trace_extraction_attestation.v1"
)


@dataclass(frozen=True)
class TraceSourceBindingV1:
    span_id: str
    chunk_id: str
    source_unit_id: str
    span_content_fingerprint: str
    source_unit_fingerprint: str
    schema_version: str = TRACE_SOURCE_BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TRACE_SOURCE_BINDING_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid trace source binding schema",
            )
        for name in ("span_id", "chunk_id", "source_unit_id"):
            validate_safe_id(getattr(self, name), field_name=name)
        for name in (
            "span_content_fingerprint",
            "source_unit_fingerprint",
        ):
            validate_fingerprint(getattr(self, name), field_name=name)

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "span_id": self.span_id,
            "chunk_id": self.chunk_id,
            "source_unit_id": self.source_unit_id,
            "span_content_fingerprint": (
                self.span_content_fingerprint
            ),
            "source_unit_fingerprint": self.source_unit_fingerprint,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "TraceSourceBindingV1":
        return cls(
            span_id=str(payload.get("span_id") or ""),
            chunk_id=str(payload.get("chunk_id") or ""),
            source_unit_id=str(payload.get("source_unit_id") or ""),
            span_content_fingerprint=str(
                payload.get("span_content_fingerprint") or ""
            ),
            source_unit_fingerprint=str(
                payload.get("source_unit_fingerprint") or ""
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )


@dataclass(frozen=True)
class TraceCandidateAttestationV1:
    candidate_fingerprint: str
    provider_fingerprint: str
    model_fingerprint: str
    protocol_fingerprint: str
    independence_group: str
    schema_version: str = TRACE_CANDIDATE_ATTESTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TRACE_CANDIDATE_ATTESTATION_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid trace candidate attestation schema",
            )
        for name in (
            "candidate_fingerprint",
            "provider_fingerprint",
            "model_fingerprint",
            "protocol_fingerprint",
        ):
            validate_fingerprint(getattr(self, name), field_name=name)
        validate_safe_id(
            self.independence_group,
            field_name="independence_group",
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "candidate_fingerprint": self.candidate_fingerprint,
            "provider_fingerprint": self.provider_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "protocol_fingerprint": self.protocol_fingerprint,
            "independence_group": self.independence_group,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "TraceCandidateAttestationV1":
        return cls(
            candidate_fingerprint=str(
                payload.get("candidate_fingerprint") or ""
            ),
            provider_fingerprint=str(
                payload.get("provider_fingerprint") or ""
            ),
            model_fingerprint=str(
                payload.get("model_fingerprint") or ""
            ),
            protocol_fingerprint=str(
                payload.get("protocol_fingerprint") or ""
            ),
            independence_group=str(
                payload.get("independence_group") or ""
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )


@dataclass(frozen=True)
class TraceExtractionAttestationV1:
    trace_fingerprint: str
    evidence_graph_logical_fingerprint: str
    source_bindings: tuple[TraceSourceBindingV1, ...]
    candidate_attestations: tuple[TraceCandidateAttestationV1, ...]
    extractor_fingerprints: tuple[str, ...] = ()
    schema_version: str = TRACE_EXTRACTION_ATTESTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TRACE_EXTRACTION_ATTESTATION_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid trace extraction attestation schema",
            )
        validate_fingerprint(
            self.trace_fingerprint,
            field_name="trace_fingerprint",
        )
        validate_fingerprint(
            self.evidence_graph_logical_fingerprint,
            field_name="evidence_graph_logical_fingerprint",
        )
        if not self.source_bindings:
            raise IngestionContractError(
                "semantic_trace_source_attestation_missing",
                "trace attestation requires source bindings",
            )
        span_ids = [item.span_id for item in self.source_bindings]
        if len(span_ids) != len(set(span_ids)):
            raise IngestionContractError(
                "duplicate_identity",
                "trace source binding span IDs must be unique",
            )
        if len(self.candidate_attestations) < 2:
            raise IngestionContractError(
                "semantic_trace_consensus_insufficient",
                "trace attestation requires two independent candidates",
            )
        candidate_ids = [
            item.candidate_fingerprint
            for item in self.candidate_attestations
        ]
        independence_groups = [
            item.independence_group
            for item in self.candidate_attestations
        ]
        if (
            len(candidate_ids) != len(set(candidate_ids))
            or len(independence_groups) != len(set(independence_groups))
        ):
            raise IngestionContractError(
                "semantic_trace_candidate_independence_missing",
                "trace candidate attestations must be unique and independent",
            )
        for value in self.extractor_fingerprints:
            validate_fingerprint(
                value,
                field_name="extractor_fingerprint",
            )
        object.__setattr__(
            self,
            "source_bindings",
            tuple(
                sorted(
                    self.source_bindings,
                    key=lambda item: item.span_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "candidate_attestations",
            tuple(
                sorted(
                    self.candidate_attestations,
                    key=lambda item: item.candidate_fingerprint,
                )
            ),
        )
        object.__setattr__(
            self,
            "extractor_fingerprints",
            tuple(sorted(set(self.extractor_fingerprints))),
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
            "trace_fingerprint": self.trace_fingerprint,
            "evidence_graph_logical_fingerprint": (
                self.evidence_graph_logical_fingerprint
            ),
            "source_bindings": [
                item.to_dict() for item in self.source_bindings
            ],
            "candidate_attestations": [
                item.to_dict() for item in self.candidate_attestations
            ],
            "extractor_fingerprints": list(
                self.extractor_fingerprints
            ),
        }
        if include_fingerprint:
            result["fingerprint"] = self.fingerprint
        return result

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "TraceExtractionAttestationV1":
        attestation = cls(
            trace_fingerprint=str(
                payload.get("trace_fingerprint") or ""
            ),
            evidence_graph_logical_fingerprint=str(
                payload.get("evidence_graph_logical_fingerprint") or ""
            ),
            source_bindings=tuple(
                TraceSourceBindingV1.from_dict(_mapping(item))
                for item in _sequence_value(
                    payload.get("source_bindings", ()),
                    "source_bindings",
                )
            ),
            candidate_attestations=tuple(
                TraceCandidateAttestationV1.from_dict(_mapping(item))
                for item in _sequence_value(
                    payload.get("candidate_attestations", ()),
                    "candidate_attestations",
                )
            ),
            extractor_fingerprints=tuple(
                str(item)
                for item in _sequence_value(
                    payload.get("extractor_fingerprints", ()),
                    "extractor_fingerprints",
                )
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )
        claimed = payload.get("fingerprint")
        if claimed is not None and claimed != attestation.fingerprint:
            raise IngestionContractError(
                "fingerprint_mismatch",
                "trace extraction attestation fingerprint mismatch",
            )
        return attestation


@dataclass(frozen=True)
class ResolvedSemanticTraceV1:
    trace_ref: str
    trace_fingerprint: str
    trajectory: Mapping[str, Any]
    extraction_attestation: TraceExtractionAttestationV1 | None = None
    schema_version: str = SEMANTIC_TRACE_RESOLUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_TRACE_RESOLUTION_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid semantic trace resolution schema",
            )
        if (
            not isinstance(self.trace_ref, str)
            or not self.trace_ref
            or "/" in self.trace_ref
            or "\\" in self.trace_ref
        ):
            raise IngestionContractError(
                "semantic_trace_ref_invalid",
                "semantic trace ref must be an opaque local identity",
            )
        if fingerprint_json(self.trajectory) != self.trace_fingerprint:
            raise IngestionContractError(
                "semantic_trace_attestation_mismatch",
                "resolved semantic trace does not match its fingerprint",
            )
        frozen = _freeze_json(
            json.loads(
                json.dumps(
                    _thaw_json(self.trajectory),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        )
        steps = frozen.get("steps")
        if (
            not isinstance(steps, tuple)
            or not steps
            or not all(isinstance(item, Mapping) for item in steps)
        ):
            raise IngestionContractError(
                "semantic_trace_invalid",
                "resolved semantic trace requires non-empty typed steps",
            )
        object.__setattr__(self, "trajectory", frozen)
        if (
            self.extraction_attestation is not None
            and self.extraction_attestation.trace_fingerprint
            != self.trace_fingerprint
        ):
            raise IngestionContractError(
                "semantic_trace_attestation_mismatch",
                "trace extraction attestation references another trace",
            )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "trace_ref": self.trace_ref,
            "trace_fingerprint": self.trace_fingerprint,
            "trajectory": _thaw_json(self.trajectory),
        }
        if self.extraction_attestation is not None:
            result["extraction_attestation"] = (
                self.extraction_attestation.to_dict()
            )
        return result

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ResolvedSemanticTraceV1":
        trajectory = payload.get("trajectory")
        if not isinstance(trajectory, Mapping):
            raise IngestionContractError(
                "semantic_trace_invalid",
                "resolved semantic trace trajectory must be an object",
            )
        attestation = payload.get("extraction_attestation")
        return cls(
            trace_ref=str(payload.get("trace_ref") or ""),
            trace_fingerprint=str(
                payload.get("trace_fingerprint") or ""
            ),
            trajectory=trajectory,
            extraction_attestation=(
                TraceExtractionAttestationV1.from_dict(
                    _mapping(attestation)
                )
                if attestation is not None
                else None
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )

    @classmethod
    def from_agent_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ResolvedSemanticTraceV1":
        if payload.get("extraction_attestation") is not None:
            raise IngestionContractError(
                "semantic_trace_attestation_agent_controlled",
                "trace extraction attestations are framework-owned",
            )
        return cls.from_dict(payload)


def attest_resolved_trace(
    resolved: ResolvedSemanticTraceV1,
    *,
    graph: SelfImprovementEvidenceGraphV1,
    trajectory_claim_id: str,
    source_bundle: SourceBundleV1,
    candidate_attestations: Sequence[
        TraceCandidateAttestationV1
    ],
    extractor_fingerprints: Sequence[str] = (),
) -> ResolvedSemanticTraceV1:
    """Bind a consensus trace to graph citations and frozen source units."""

    claims = {item.claim_id: item for item in graph.claims}
    claim = claims.get(trajectory_claim_id)
    if (
        claim is None
        or claim.kind is not EvidenceClaimKind.EXECUTION_TRAJECTORY
        or claim.payload.get("trace_ref") != resolved.trace_ref
        or claim.payload.get("trace_fingerprint")
        != resolved.trace_fingerprint
    ):
        raise IngestionContractError(
            "semantic_trace_claim_mismatch",
            "trace resolution does not match its trajectory claim",
        )
    spans = {item.span_id: item for item in graph.spans}
    chunks = {item.chunk_id: item for item in source_bundle.chunks}
    bindings: list[TraceSourceBindingV1] = []
    for span_id in claim.source_span_ids:
        span = spans.get(span_id)
        chunk = chunks.get(span.chunk_id) if span is not None else None
        if span is None or chunk is None:
            raise IngestionContractError(
                "semantic_trace_source_attestation_missing",
                "trajectory claim citation is absent from the source bundle",
            )
        bindings.append(
            TraceSourceBindingV1(
                span_id=span.span_id,
                chunk_id=span.chunk_id,
                source_unit_id=chunk.source_unit_id,
                span_content_fingerprint=span.content_fingerprint,
                source_unit_fingerprint=fingerprint_json(
                    chunk.public_projection()
                ),
            )
        )
    return ResolvedSemanticTraceV1(
        trace_ref=resolved.trace_ref,
        trace_fingerprint=resolved.trace_fingerprint,
        trajectory=resolved.trajectory,
        extraction_attestation=TraceExtractionAttestationV1(
            trace_fingerprint=resolved.trace_fingerprint,
            evidence_graph_logical_fingerprint=(
                graph.logical_fingerprint
            ),
            source_bindings=tuple(bindings),
            candidate_attestations=tuple(candidate_attestations),
            extractor_fingerprints=tuple(extractor_fingerprints),
        ),
    )


def validate_resolved_trace_attestation(
    resolved: ResolvedSemanticTraceV1,
    *,
    graph: SelfImprovementEvidenceGraphV1,
    trajectory_claim_id: str,
    source_bundle: SourceBundleV1 | None = None,
) -> None:
    attestation = resolved.extraction_attestation
    if attestation is None:
        raise IngestionContractError(
            "semantic_trace_source_attestation_missing",
            "resolved semantic trace lacks framework source attestation",
        )
    if (
        attestation.trace_fingerprint != resolved.trace_fingerprint
        or attestation.evidence_graph_logical_fingerprint
        != graph.logical_fingerprint
    ):
        raise IngestionContractError(
            "semantic_trace_attestation_mismatch",
            "trace attestation is not bound to the frozen graph",
        )
    claims = {item.claim_id: item for item in graph.claims}
    claim = claims.get(trajectory_claim_id)
    if (
        claim is None
        or claim.kind is not EvidenceClaimKind.EXECUTION_TRAJECTORY
        or claim.payload.get("trace_ref") != resolved.trace_ref
        or claim.payload.get("trace_fingerprint")
        != resolved.trace_fingerprint
    ):
        raise IngestionContractError(
            "semantic_trace_claim_mismatch",
            "trace attestation does not match the trajectory claim",
        )
    bindings = {
        item.span_id: item for item in attestation.source_bindings
    }
    if set(bindings) != set(claim.source_span_ids):
        raise IngestionContractError(
            "semantic_trace_source_attestation_mismatch",
            "trace attestation citations differ from the trajectory claim",
        )
    spans = {item.span_id: item for item in graph.spans}
    chunks = (
        {item.chunk_id: item for item in source_bundle.chunks}
        if source_bundle is not None
        else {}
    )
    for span_id, binding in bindings.items():
        span = spans.get(span_id)
        if (
            span is None
            or binding.chunk_id != span.chunk_id
            or binding.span_content_fingerprint
            != span.content_fingerprint
        ):
            raise IngestionContractError(
                "semantic_trace_source_attestation_mismatch",
                "trace attestation does not match the cited source span",
            )
        if source_bundle is not None:
            chunk = chunks.get(binding.chunk_id)
            if (
                chunk is None
                or binding.source_unit_id != chunk.source_unit_id
                or binding.source_unit_fingerprint
                != fingerprint_json(chunk.public_projection())
            ):
                raise IngestionContractError(
                    "semantic_trace_source_attestation_mismatch",
                    "trace attestation does not match the frozen source unit",
                )


@dataclass(frozen=True)
class CompiledSemanticDatasetV1:
    normalized_cases: tuple[NormalizedCaseRecord, ...]
    target_evidence_bundle: TargetEvidenceBundleV1
    evidence_graph_logical_fingerprint: str
    improvement_signal_set_fingerprint: str
    evaluation_plan_bundle_fingerprint: str
    normalization_fingerprint: str
    normalized_dataset_fingerprint: str
    schema_version: str = SEMANTIC_COMPILATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_COMPILATION_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid semantic compilation schema",
            )
        case_ids = [item.case_id for item in self.normalized_cases]
        if not case_ids:
            raise IngestionContractError(
                "normalized_dataset_empty",
                "semantic compilation produced no cases",
            )
        if len(case_ids) != len(set(case_ids)):
            raise IngestionContractError(
                "duplicate_identity",
                "semantic compilation produced duplicate case IDs",
            )
        expected = logical_normalized_dataset_fingerprint(
            self.normalized_cases
        )
        if expected != self.normalized_dataset_fingerprint:
            raise IngestionContractError(
                "fingerprint_mismatch",
                "semantic normalized dataset fingerprint mismatch",
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
            "normalized_cases": [
                item.to_dict()
                for item in sorted(
                    self.normalized_cases,
                    key=lambda value: value.case_id,
                )
            ],
            "target_evidence_bundle": (
                self.target_evidence_bundle.to_dict()
            ),
            "evidence_graph_logical_fingerprint": (
                self.evidence_graph_logical_fingerprint
            ),
            "improvement_signal_set_fingerprint": (
                self.improvement_signal_set_fingerprint
            ),
            "evaluation_plan_bundle_fingerprint": (
                self.evaluation_plan_bundle_fingerprint
            ),
            "normalization_fingerprint": self.normalization_fingerprint,
            "normalized_dataset_fingerprint": (
                self.normalized_dataset_fingerprint
            ),
        }
        if include_fingerprint:
            result["fingerprint"] = self.fingerprint
        return result

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "CompiledSemanticDatasetV1":
        cases = payload.get("normalized_cases", ())
        if not isinstance(cases, Sequence) or isinstance(
            cases,
            (str, bytes, bytearray),
        ):
            raise IngestionContractError(
                "schema_invalid",
                "compiled semantic normalized_cases must be an array",
            )
        target_bundle = payload.get("target_evidence_bundle")
        if not isinstance(target_bundle, Mapping):
            raise IngestionContractError(
                "schema_invalid",
                "compiled semantic target_evidence_bundle must be an object",
            )
        compiled = cls(
            normalized_cases=tuple(
                NormalizedCaseRecord.from_dict(_mapping(item))
                for item in cases
            ),
            target_evidence_bundle=TargetEvidenceBundleV1.from_dict(
                target_bundle
            ),
            evidence_graph_logical_fingerprint=str(
                payload.get("evidence_graph_logical_fingerprint") or ""
            ),
            improvement_signal_set_fingerprint=str(
                payload.get("improvement_signal_set_fingerprint") or ""
            ),
            evaluation_plan_bundle_fingerprint=str(
                payload.get("evaluation_plan_bundle_fingerprint") or ""
            ),
            normalization_fingerprint=str(
                payload.get("normalization_fingerprint") or ""
            ),
            normalized_dataset_fingerprint=str(
                payload.get("normalized_dataset_fingerprint") or ""
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )
        claimed = payload.get("fingerprint")
        if claimed is not None and claimed != compiled.fingerprint:
            raise IngestionContractError(
                "fingerprint_mismatch",
                "compiled semantic dataset fingerprint mismatch",
            )
        return compiled


def compile_target_evidence_bundle(
    graph: SelfImprovementEvidenceGraphV1,
    cases: Sequence[SelfImprovementCaseV1],
    *,
    resolved_traces: Mapping[str, ResolvedSemanticTraceV1],
    require_trace_attestation: bool = False,
) -> TargetEvidenceBundleV1:
    claims = {item.claim_id: item for item in graph.claims}
    executions: list[TargetExecutionEvidenceV1] = []
    for case in cases:
        case.validate_against(graph)
        for trajectory_claim_id in case.trajectory_claim_ids:
            claim = claims[trajectory_claim_id]
            if (
                claim.kind is not EvidenceClaimKind.EXECUTION_TRAJECTORY
                or claim.resolution_status
                is not EvidenceResolutionStatus.RESOLVED
            ):
                raise IngestionContractError(
                    "target_trajectory_invalid",
                    "target trajectory must be resolved trajectory evidence",
                )
            execution_id = claim.subject_entity_ids[0]
            trace_ref = str(claim.payload["trace_ref"])
            resolved = resolved_traces.get(trace_ref)
            if (
                resolved is None
                or resolved.trace_ref != trace_ref
                or resolved.trace_fingerprint
                != claim.payload["trace_fingerprint"]
            ):
                raise IngestionContractError(
                    "semantic_trace_resolution_missing",
                    "every target trajectory requires an exact frozen resolution",
                )
            if require_trace_attestation:
                validate_resolved_trace_attestation(
                    resolved,
                    graph=graph,
                    trajectory_claim_id=trajectory_claim_id,
                )
            result_claim_ids = tuple(
                sorted(
                    claim_id
                    for claim_id in case.result_claim_ids
                    if claims[claim_id].subject_entity_ids
                    == (execution_id,)
                )
            )
            executions.append(
                TargetExecutionEvidenceV1(
                    case_id=case.case_id,
                    task_entity_id=case.task_entity_id,
                    execution_entity_id=execution_id,
                    trajectory_claim_id=trajectory_claim_id,
                    result_claim_ids=result_claim_ids,
                    trace_ref=trace_ref,
                    trace_fingerprint=str(
                        claim.payload["trace_fingerprint"]
                    ),
                )
            )
    bundle = TargetEvidenceBundleV1(
        executions=tuple(executions),
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    bundle.validate_against(graph, cases)
    return bundle


def compile_semantic_dataset(
    *,
    graph: SelfImprovementEvidenceGraphV1,
    cases: Sequence[SelfImprovementCaseV1],
    signal_set: SelfImprovementSignalSetV1,
    evaluation_plans: Sequence[SelfImprovementEvaluationPlanV1],
    resolved_traces: Mapping[str, ResolvedSemanticTraceV1] | None = None,
    case_input_overrides: Mapping[str, Any] | None = None,
    ingestion_id: str | None = None,
    authority_context: EvidenceAuthorityContextV1 | None = None,
    manifest_origin: ManifestOrigin = ManifestOrigin.ABSENT,
    manifest_fingerprint: str | None = None,
    verified_only_signal_projection: bool = False,
    require_trace_attestation: bool = False,
) -> CompiledSemanticDatasetV1:
    """One-way compile semantic IR into legacy-compatible normalized cases."""

    trace_resolutions = resolved_traces or {}
    input_overrides = case_input_overrides or {}
    if signal_set.evidence_graph_logical_fingerprint != (
        graph.logical_fingerprint
    ):
        raise IngestionContractError(
            "evidence_graph_fingerprint_mismatch",
            "signal set and graph fingerprints differ",
        )
    case_by_id = {item.case_id: item for item in cases}
    plan_by_case = {item.case_id: item for item in evaluation_plans}
    if len(case_by_id) != len(cases) or len(plan_by_case) != len(
        evaluation_plans
    ):
        raise IngestionContractError(
            "duplicate_identity",
            "semantic cases and plans must have unique IDs",
        )
    if set(plan_by_case) != set(case_by_id):
        raise IngestionContractError(
            "evaluation_plan_coverage_incomplete",
            "every semantic case requires exactly one evaluation plan",
        )
    if not set(case_by_id).issubset(signal_set.case_splits):
        raise IngestionContractError(
            "semantic_split_coverage_incomplete",
            "every semantic case requires a frozen dataset split",
        )
    for case in cases:
        case.validate_against(graph)
        plan = plan_by_case[case.case_id]
        plan.validate_references(
            graph=graph,
            case=case,
            signal_set=signal_set,
        )
        if (
            plan.disposition
            is EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
            or plan.expected_output_claim_id is not None
        ):
            if authority_context is None:
                raise IngestionContractError(
                    "semantic_plan_authority_attestation_missing",
                    "verified semantic plans require framework authority context",
                )
            effective_manifest_fingerprint = (
                manifest_fingerprint
                or fingerprint_json(
                    {
                        "manifest_origin": ManifestOrigin(
                            manifest_origin
                        ).value
                    }
                )
            )
            authority_claim_ids = {
                *plan.supporting_evidence_claim_ids,
                *(
                    (plan.expected_output_claim_id,)
                    if plan.expected_output_claim_id is not None
                    else ()
                ),
            }
            unauthorized = [
                claim_id
                for claim_id in sorted(authority_claim_ids)
                if not authority_context.authorizes_claim(
                    claim_id,
                    graph=graph,
                    manifest_origin=ManifestOrigin(manifest_origin),
                    manifest_fingerprint=(
                        effective_manifest_fingerprint
                    ),
                )
            ]
            if unauthorized:
                raise IngestionContractError(
                    "semantic_plan_authority_attestation_invalid",
                    "verified semantic plan references non-authoritative evidence",
                )
    target_bundle = compile_target_evidence_bundle(
        graph,
        cases,
        resolved_traces=trace_resolutions,
        require_trace_attestation=require_trace_attestation,
    )
    plan_bundle_fingerprint = fingerprint_json(
        [
            plan_by_case[case_id].canonical_dict()
            for case_id in sorted(plan_by_case)
        ]
    )
    normalization_fingerprint = fingerprint_json(
        {
            "schema_version": SEMANTIC_COMPILATION_SCHEMA_VERSION,
            "evidence_graph_logical_fingerprint": (
                graph.logical_fingerprint
            ),
            "improvement_signal_set_fingerprint": signal_set.fingerprint,
            "evaluation_plan_bundle_fingerprint": (
                plan_bundle_fingerprint
            ),
            "target_evidence_bundle_fingerprint": (
                target_bundle.fingerprint
            ),
        }
    )
    claims = {item.claim_id: item for item in graph.claims}
    optimizer_projection = signal_set.optimizer_projection(
        allowed_splits=(DatasetSplit.TRAIN, DatasetSplit.VALIDATION),
    )
    projection_by_id = {
        str(item["signal_id"]): item for item in optimizer_projection
    }
    signals_by_case: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for case_id in case_by_id:
        plan = plan_by_case[case_id]
        if (
            signal_set.case_splits[case_id] is DatasetSplit.HELD_OUT
            or (
                verified_only_signal_projection
                and plan.disposition
                is not EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
            )
        ):
            signals_by_case[case_id] = ()
            continue
        adopted_ids = plan.training_signal_ids
        signals_by_case[case_id] = tuple(
            projection_by_id[signal_id]
            for signal_id in adopted_ids
            if signal_id in projection_by_id
        )

    normalized_cases: list[NormalizedCaseRecord] = []
    for case_id in sorted(case_by_id):
        case = case_by_id[case_id]
        plan = plan_by_case[case_id]
        task_input = input_overrides.get(case_id)
        if task_input is None:
            inputs = [
                claims[item].payload["input"]
                for item in case.input_claim_ids
                if claims[item].kind is EvidenceClaimKind.TASK_INPUT
            ]
            if len(inputs) != 1:
                raise IngestionContractError(
                    "semantic_case_input_ambiguous",
                    "semantic case requires exactly one task input",
                )
            task_input = inputs[0]
        expected_output = None
        if (
            plan.disposition
            is EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
            and plan.expected_output_claim_id is not None
        ):
            expected_claim = claims[plan.expected_output_claim_id]
            expected_output = expected_claim.payload["result"]

        trajectory = None
        trace_replayability = "absent"
        if plan.replay_seed_execution_id is not None:
            trajectory_claims = [
                claims[item]
                for item in case.trajectory_claim_ids
                if claims[item].subject_entity_ids
                == (plan.replay_seed_execution_id,)
            ]
            if len(trajectory_claims) != 1:
                raise IngestionContractError(
                    "replay_seed_trajectory_ambiguous",
                    "replay seed must resolve to exactly one trajectory",
                )
            trajectory_claim = trajectory_claims[0]
            trace_ref = str(trajectory_claim.payload["trace_ref"])
            resolved = trace_resolutions.get(trace_ref)
            if (
                resolved is None
                or resolved.trace_ref != trace_ref
                or resolved.trace_fingerprint
                != trajectory_claim.payload["trace_fingerprint"]
            ):
                raise IngestionContractError(
                    "semantic_trace_resolution_missing",
                    "replay seed trace lacks an exact frozen resolution",
                )
            trajectory = _thaw_json(resolved.trajectory)
            trace_replayability = "replayable"

        asset_locators = _case_asset_locators(graph, case)
        normalized_cases.append(
            NormalizedCaseRecord(
                case_id=case.case_id,
                input=task_input,
                expected_output=expected_output,
                source=CaseSourceProvenance(
                    asset_ids=tuple(
                        item[0] for item in asset_locators
                    ),
                    record_locators=tuple(
                        item[1] for item in asset_locators
                    ),
                    mapping_fingerprint=None,
                    normalization_fingerprint=(
                        normalization_fingerprint
                    ),
                    ingestion_id=ingestion_id,
                ),
                metadata={
                    "self_improvement_case_ref": case.fingerprint,
                    "evaluation_plan_ref": plan.plan_fingerprint,
                    "evaluation_disposition": plan.disposition.value,
                    "evaluation_reason_codes": list(plan.reason_codes),
                    "improvement_signal_set_fingerprint": (
                        signal_set.fingerprint
                    ),
                },
                trajectory=trajectory,
                trace_replayability=trace_replayability,
                self_improvement_signals=signals_by_case[case_id],
            )
        )
    normalized = tuple(normalized_cases)
    return CompiledSemanticDatasetV1(
        normalized_cases=normalized,
        target_evidence_bundle=target_bundle,
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
        improvement_signal_set_fingerprint=signal_set.fingerprint,
        evaluation_plan_bundle_fingerprint=plan_bundle_fingerprint,
        normalization_fingerprint=normalization_fingerprint,
        normalized_dataset_fingerprint=(
            logical_normalized_dataset_fingerprint(normalized)
        ),
    )


def logical_normalized_dataset_fingerprint(
    records: Sequence[NormalizedCaseRecord],
) -> str:
    """Hash logical cases while excluding physical source provenance."""

    return fingerprint_json(
        [
            {
                "case_id": item.case_id,
                "input": item.input,
                "expected_output": item.expected_output,
                "metadata": item.metadata,
                "trajectory_fingerprint": (
                    fingerprint_json(item.trajectory)
                    if item.trajectory is not None
                    else None
                ),
                "trace_replayability": item.trace_replayability,
                "self_improvement_signals": (
                    item.self_improvement_signals
                ),
            }
            for item in sorted(records, key=lambda value: value.case_id)
        ]
    )


def canonical_semantic_case_id(
    case: SelfImprovementCaseV1,
    *,
    graph: SelfImprovementEvidenceGraphV1,
) -> str:
    identity = case.to_dict()
    identity.pop("schema_version", None)
    identity.pop("case_id", None)
    identity.pop("trainable_signal_projection", None)
    return (
        "case:"
        + fingerprint_json(
            {
                "schema_version": (
                    "aworld.self_evolve.canonical_case_identity.v1"
                ),
                "evidence_graph_logical_fingerprint": (
                    graph.logical_fingerprint
                ),
                "case": identity,
            }
        ).removeprefix("sha256:")[:32]
    )


def canonical_semantic_signal_id(
    signal: SelfImprovementSignalV1,
) -> str:
    identity = signal.canonical_dict()
    identity.pop("signal_id", None)
    return (
        "signal:"
        + fingerprint_json(
            {
                "schema_version": (
                    "aworld.self_evolve.canonical_signal_identity.v1"
                ),
                "signal": identity,
            }
        ).removeprefix("sha256:")[:32]
    )


def canonical_semantic_plan_id(
    plan: SelfImprovementEvaluationPlanV1,
) -> str:
    identity = plan.canonical_dict()
    identity.pop("plan_id", None)
    return (
        "plan:"
        + fingerprint_json(
            {
                "schema_version": (
                    "aworld.self_evolve.canonical_plan_identity.v1"
                ),
                "plan": identity,
            }
        ).removeprefix("sha256:")[:32]
    )


def _case_asset_locators(
    graph: SelfImprovementEvidenceGraphV1,
    case: SelfImprovementCaseV1,
) -> tuple[tuple[str, str], ...]:
    claims = {item.claim_id: item for item in graph.claims}
    spans = {item.span_id: item for item in graph.spans}
    claim_ids = {
        *case.input_claim_ids,
        *case.trajectory_claim_ids,
        *case.result_claim_ids,
        *case.comparison_claim_ids,
    }
    by_asset: dict[str, str] = {}
    for claim_id in claim_ids:
        for span_id in claims[claim_id].source_span_ids:
            by_asset.setdefault(spans[span_id].asset_id, span_id)
    if not by_asset:
        raise IngestionContractError(
            "semantic_case_provenance_missing",
            "semantic case has no cited source asset",
        )
    return tuple(sorted(by_asset.items()))


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze_json(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IngestionContractError(
            "schema_invalid",
            "expected an object",
        )
    return value


def _sequence_value(
    value: Any,
    field_name: str,
) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise IngestionContractError(
            "schema_invalid",
            f"{field_name} must be an array",
        )
    return value
