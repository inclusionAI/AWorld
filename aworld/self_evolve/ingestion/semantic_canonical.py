from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from aworld.self_evolve.evaluation_plan import (
    _FRAMEWORK_DETERMINISTIC_EXTRACTOR_ATTESTATION,
    EvidenceAuthorityContextV1,
    EvaluationDisposition,
    ManifestOrigin,
    SemanticIngestionProfileV1,
    SelfImprovementEvaluationPlanV1,
    compile_evaluation_plan,
    issue_evidence_authority_context,
)
from aworld.self_evolve.evidence import (
    ClaimVerificationOrigin,
    ClaimVerificationVerdict,
    ClaimVerificationV1,
    EvidenceClaimKind,
    EvidenceClaimV1,
    EvidenceConflictKind,
    EvidenceConflictStatus,
    EvidenceConflictV1,
    EvidenceEntityKind,
    EvidenceEntityV1,
    EvidenceProducerKind,
    EvidenceResolutionStatus,
    SelfImprovementCaseResolutionStatus,
    SelfImprovementCaseV1,
    SelfImprovementEvidenceGraphV1,
    SemanticSourceDispositionKind,
    SemanticSourceDispositionV1,
)
from aworld.self_evolve.improvement_signals import (
    BehaviorDeltaV1,
    DatasetSplit,
    SelfImprovementSignalKind,
    SelfImprovementSignalSetV1,
    SelfImprovementSignalV1,
    SignalActionability,
    SignalVerificationStatus,
)

from .chunking import SourceBundleV1
from .semantic_compiler import (
    ResolvedSemanticTraceV1,
    TraceExtractionOrigin,
    attest_resolved_trace,
    canonical_semantic_case_id,
    canonical_semantic_plan_id,
    canonical_semantic_signal_id,
)
from .semantic_verifier import SemanticQualificationEvidenceV1
from .semantic_workflow import (
    evidence_source_span_from_chunk,
    validate_evidence_graph_against_source_bundle,
)
from .types import (
    IngestionContractError,
    fingerprint_json,
    validate_safe_id,
)


CANONICAL_SEMANTIC_SOURCE_SCHEMA_VERSION = (
    "aworld.self_evolve.canonical_semantic_source.v1"
)
CANONICAL_SEMANTIC_DECODER_PROTOCOL_FINGERPRINT = fingerprint_json(
    {
        "schema_version": (
            "aworld.self_evolve.canonical_semantic_decoder_protocol.v1"
        ),
        "source_schema_version": (
            CANONICAL_SEMANTIC_SOURCE_SCHEMA_VERSION
        ),
        "authority_origin": "deterministic_decoder",
    }
)

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "bundle_key",
        "part_key",
        "entities",
        "claims",
        "conflicts",
        "cases",
        "signals",
        "plans",
        "traces",
    }
)
_TOP_LEVEL_ARRAY_FIELDS = (
    "entities",
    "claims",
    "conflicts",
    "cases",
    "signals",
    "plans",
    "traces",
)
_CONTROLLED_FIELDS = frozenset(
    {
        "actionability",
        "agent_confidence",
        "approved_evidence_graph_fingerprint",
        "auditor_verification_id",
        "authority",
        "candidate_attestations",
        "case_id",
        "claim_id",
        "conflict_id",
        "current_evaluator_required",
        "deterministic_verification_ids",
        "disposition",
        "entity_id",
        "evidence_graph_logical_fingerprint",
        "evidence_graph_provenance_fingerprint",
        "extraction_attestation",
        "extraction_origin",
        "extractor_population_fingerprint",
        "historical_judge_authority",
        "human_claim_authority",
        "human_approval",
        "independence_group",
        "manifest_origin",
        "model_profile_fingerprint",
        "plan_fingerprint",
        "plan_id",
        "producer_kind",
        "profile_fingerprint",
        "qualification",
        "qualification_report",
        "reason_codes",
        "resolution_status",
        "schema_version",
        "signal_id",
        "source_span_ids",
        "split",
        "trace_fingerprint",
        "trace_ref",
        "trust_level",
        "verification_ids",
        "verification_origin",
        "verification_registry_fingerprint",
        "verification_status",
        "verifier_fingerprint",
        "verdict",
    }
)


@dataclass(frozen=True)
class CanonicalSemanticSourcePartV1:
    bundle_key: str
    part_key: str
    asset_id: str
    relative_path: str
    source_unit_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class CanonicalSemanticSourceSetV1:
    bundle_key: str
    parts: tuple[CanonicalSemanticSourcePartV1, ...]
    manifest_asset_id: str | None = None
    schema_version: str = CANONICAL_SEMANTIC_SOURCE_SCHEMA_VERSION

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(
            {
                "schema_version": self.schema_version,
                "bundle_key": self.bundle_key,
                "parts": [
                    {
                        "part_key": item.part_key,
                        "asset_id": item.asset_id,
                        "relative_path": item.relative_path,
                        "source_unit_ids": sorted(item.source_unit_ids),
                        "chunk_ids": sorted(item.chunk_ids),
                        "payload": item.payload,
                    }
                    for item in self.parts
                ],
                "manifest_asset_id": self.manifest_asset_id,
            }
        )


@dataclass(frozen=True)
class CanonicalSemanticDecodeResultV1:
    source_set_fingerprint: str
    evidence_graph: SelfImprovementEvidenceGraphV1
    evidence_authority_context: EvidenceAuthorityContextV1
    semantic_cases: tuple[SelfImprovementCaseV1, ...]
    improvement_signal_set: SelfImprovementSignalSetV1
    evaluation_plans: tuple[SelfImprovementEvaluationPlanV1, ...]
    resolved_traces: tuple[ResolvedSemanticTraceV1, ...]
    decoder_protocol_fingerprint: str = (
        CANONICAL_SEMANTIC_DECODER_PROTOCOL_FINGERPRINT
    )
    model_call_count: int = 0


def recognize_canonical_semantic_source(
    bundle: SourceBundleV1,
    manifest_asset_id: str | None = None,
) -> CanonicalSemanticSourceSetV1 | None:
    """Recognize a complete canonical source set, or fail closed.

    Recognition is canonical-first: once one asset declares the canonical
    marker, every non-manifest asset must be a valid part of the same bundle.
    """

    asset_rows: dict[str, dict[str, Any]] = {}
    for chunk in bundle.chunks:
        row = asset_rows.setdefault(
            chunk.asset_id,
            {
                "relative_path": chunk.relative_path,
                "chunks": [],
                "units": [],
                "roots": [],
            },
        )
        row["chunks"].append(chunk)
        row["units"].append(chunk.source_unit_id)
    for unit in bundle.structured_units:
        row = asset_rows.setdefault(
            unit.asset_id,
            {
                "relative_path": unit.relative_path,
                "chunks": [],
                "units": [],
                "roots": [],
            },
        )
        row["units"].append(unit.source_unit_id)
        if unit.unit_kind == "record" and unit.record_locator == "$":
            row["roots"].append(unit.value)

    considered = {
        asset_id: row
        for asset_id, row in asset_rows.items()
        if asset_id != manifest_asset_id
    }
    marker_present = any(
        isinstance(root, Mapping)
        and isinstance(root.get("schema_version"), str)
        and str(root.get("schema_version")).startswith(
            "aworld.self_evolve.canonical_semantic_source."
        )
        for row in considered.values()
        for root in row["roots"]
    )
    if not marker_present:
        marker_present = any(
            CANONICAL_SEMANTIC_SOURCE_SCHEMA_VERSION in chunk.raw_text
            for row in considered.values()
            for chunk in row["chunks"]
        )
    if not marker_present:
        return None
    if not considered:
        raise _error(
            "canonical_source_empty",
            "canonical source marker has no source parts",
        )

    parts: list[CanonicalSemanticSourcePartV1] = []
    for asset_id, row in sorted(
        considered.items(),
        key=lambda item: str(item[1]["relative_path"]),
    ):
        roots = [
            value
            for value in row["roots"]
            if isinstance(value, Mapping)
            and value.get("schema_version")
            == CANONICAL_SEMANTIC_SOURCE_SCHEMA_VERSION
        ]
        if len(roots) != 1 or len(row["roots"]) != 1:
            raise _error(
                "canonical_source_mixed",
                "canonical input cannot mix canonical and non-canonical assets",
            )
        payload = _validate_part(roots[0])
        parts.append(
            CanonicalSemanticSourcePartV1(
                bundle_key=str(payload["bundle_key"]),
                part_key=str(payload["part_key"]),
                asset_id=asset_id,
                relative_path=str(row["relative_path"]),
                source_unit_ids=tuple(sorted(row["units"])),
                chunk_ids=tuple(
                    sorted(chunk.chunk_id for chunk in row["chunks"])
                ),
                payload=payload,
            )
        )

    bundle_keys = {item.bundle_key for item in parts}
    part_keys = [item.part_key for item in parts]
    if len(bundle_keys) != 1:
        raise _error(
            "canonical_bundle_mismatch",
            "canonical parts must declare the same bundle_key",
        )
    if len(part_keys) != len(set(part_keys)):
        raise _error(
            "duplicate_identity",
            "canonical part_key values must be unique",
        )
    result = CanonicalSemanticSourceSetV1(
        bundle_key=next(iter(bundle_keys)),
        parts=tuple(sorted(parts, key=lambda item: item.part_key)),
        manifest_asset_id=manifest_asset_id,
    )
    _validate_global_draft_contract(result)
    return result


def decode_canonical_semantic_source(
    source_set: CanonicalSemanticSourceSetV1,
    bundle: SourceBundleV1,
    profile: SemanticIngestionProfileV1,
    manifest_origin: ManifestOrigin,
    manifest_fingerprint: str | None,
    qualification_evidence: SemanticQualificationEvidenceV1,
    extractor_fingerprints: Sequence[str] = (),
) -> CanonicalSemanticDecodeResultV1:
    """Decode a source-draft envelope without invoking a model."""

    recognized = recognize_canonical_semantic_source(
        bundle,
        manifest_asset_id=source_set.manifest_asset_id,
    )
    if (
        recognized is None
        or recognized.fingerprint != source_set.fingerprint
    ):
        raise _error(
            "canonical_source_bundle_mismatch",
            "canonical source set is not bound to the supplied source bundle",
        )
    _validate_global_draft_contract(source_set)
    part_by_key = {item.part_key: item for item in source_set.parts}
    chunks_by_id = {item.chunk_id: item for item in bundle.chunks}
    if any(
        chunk_id not in chunks_by_id
        for part in source_set.parts
        for chunk_id in part.chunk_ids
    ):
        raise _error(
            "canonical_source_bundle_mismatch",
            "canonical source set references chunks outside the source bundle",
        )

    spans = tuple(
        evidence_source_span_from_chunk(
            chunk,
            span_id=_semantic_id(
                "span",
                {
                    "chunk_id": chunk.chunk_id,
                    "content_fingerprint": chunk.content_fingerprint,
                },
            ),
        )
        for part in source_set.parts
        for chunk in (
            chunks_by_id[chunk_id] for chunk_id in part.chunk_ids
        )
    )
    span_ids_by_part = {
        part.part_key: tuple(
            _semantic_id(
                "span",
                {
                    "chunk_id": chunks_by_id[chunk_id].chunk_id,
                    "content_fingerprint": (
                        chunks_by_id[chunk_id].content_fingerprint
                    ),
                },
            )
            for chunk_id in part.chunk_ids
        )
        for part in source_set.parts
    }

    trace_rows = _draft_rows(source_set, "traces", "trace_key")
    traces_by_key: dict[str, ResolvedSemanticTraceV1] = {}
    for _, draft in trace_rows:
        trace_key = str(draft["trace_key"])
        trajectory = _mapping(draft["trajectory"], "trajectory")
        trace_fingerprint = fingerprint_json(trajectory)
        traces_by_key[trace_key] = ResolvedSemanticTraceV1(
            trace_ref=_semantic_id(
                "trace",
                {"trajectory_fingerprint": trace_fingerprint},
            ),
            trace_fingerprint=trace_fingerprint,
            trajectory=trajectory,
        )

    entity_rows = _draft_rows(source_set, "entities", "entity_key")
    entity_aliases: dict[str, str] = {}
    entities_by_id: dict[str, EvidenceEntityV1] = {}
    for part, draft in entity_rows:
        identity = {
            "kind": str(draft["kind"]),
            "canonical_name": str(draft["canonical_name"]).strip(),
            "aliases": sorted(_strings(draft.get("aliases", ()), "aliases")),
            "attributes": _mapping(
                draft.get("attributes", {}),
                "attributes",
            ),
        }
        entity_id = _semantic_id("entity", identity)
        entity_key = str(draft["entity_key"])
        entity_aliases[entity_key] = entity_id
        current = entities_by_id.get(entity_id)
        source_span_ids = span_ids_by_part[part.part_key]
        candidate = EvidenceEntityV1(
            entity_id=entity_id,
            kind=EvidenceEntityKind(str(draft["kind"])),
            canonical_name=str(draft["canonical_name"]),
            aliases=_strings(draft.get("aliases", ()), "aliases"),
            source_span_ids=source_span_ids,
            attributes=_mapping(
                draft.get("attributes", {}),
                "attributes",
            ),
        )
        if current is None:
            entities_by_id[entity_id] = candidate
        else:
            entities_by_id[entity_id] = replace(
                current,
                source_span_ids=tuple(
                    sorted(
                        {
                            *current.source_span_ids,
                            *candidate.source_span_ids,
                        }
                    )
                ),
            )

    claim_rows = _draft_rows(source_set, "claims", "claim_key")
    pending_claims: dict[str, dict[str, Any]] = {}
    claim_aliases: dict[str, str] = {}
    claims_cited_by_part: dict[str, set[str]] = {
        key: set() for key in part_by_key
    }
    for _, draft in claim_rows:
        payload = _rewrite_claim_payload(
            _mapping(draft["payload"], "payload"),
            entity_aliases=entity_aliases,
            traces_by_key=traces_by_key,
        )
        subject_ids = tuple(
            entity_aliases[item]
            for item in _strings(
                draft["subject_entity_keys"],
                "subject_entity_keys",
            )
        )
        object_ids = tuple(
            entity_aliases[item]
            for item in _strings(
                draft.get("object_entity_keys", ()),
                "object_entity_keys",
            )
        )
        identity = {
            "kind": str(draft["kind"]),
            "subject_entity_ids": sorted(subject_ids),
            "object_entity_ids": sorted(object_ids),
            "payload": payload,
        }
        claim_id = _semantic_id("claim", identity)
        claim_aliases[str(draft["claim_key"])] = claim_id
        citation_parts = _strings(
            draft["citation_part_keys"],
            "citation_part_keys",
        )
        citation_span_ids = tuple(
            sorted(
                {
                    span_id
                    for part_key in citation_parts
                    for span_id in span_ids_by_part[part_key]
                }
            )
        )
        row = pending_claims.setdefault(
            claim_id,
            {
                "kind": EvidenceClaimKind(str(draft["kind"])),
                "subject_entity_ids": subject_ids,
                "object_entity_ids": object_ids,
                "payload": payload,
                "source_span_ids": set(),
            },
        )
        row["source_span_ids"].update(citation_span_ids)
        for part_key in citation_parts:
            claims_cited_by_part[part_key].add(claim_id)

    uncited_parts = sorted(
        key for key, claim_ids in claims_cited_by_part.items() if not claim_ids
    )
    if uncited_parts:
        raise _error(
            "canonical_part_uncited",
            f"canonical part is not cited by any claim: {uncited_parts[0]}",
        )

    claims: list[EvidenceClaimV1] = []
    verifications: list[ClaimVerificationV1] = []
    for claim_id, row in sorted(pending_claims.items()):
        source_span_ids = tuple(sorted(row["source_span_ids"]))
        verification_id = _semantic_id(
            "verification",
            {
                "claim_id": claim_id,
                "origin": ClaimVerificationOrigin.DETERMINISTIC_DECODER.value,
                "verdict": ClaimVerificationVerdict.ENTAILED.value,
                "source_span_ids": source_span_ids,
                "verifier_fingerprint": (
                    CANONICAL_SEMANTIC_DECODER_PROTOCOL_FINGERPRINT
                ),
            },
        )
        verifications.append(
            ClaimVerificationV1(
                verification_id=verification_id,
                claim_id=claim_id,
                verdict=ClaimVerificationVerdict.ENTAILED,
                verification_origin=(
                    ClaimVerificationOrigin.DETERMINISTIC_DECODER
                ),
                verifier_fingerprint=(
                    CANONICAL_SEMANTIC_DECODER_PROTOCOL_FINGERPRINT
                ),
                independence_group="canonical-decoder-v1",
                source_span_ids=source_span_ids,
                rationale_codes=("canonical_typed_source",),
            )
        )
        claims.append(
            EvidenceClaimV1(
                claim_id=claim_id,
                kind=row["kind"],
                subject_entity_ids=tuple(row["subject_entity_ids"]),
                object_entity_ids=tuple(row["object_entity_ids"]),
                payload=row["payload"],
                source_span_ids=source_span_ids,
                producer_kind=EvidenceProducerKind.DETERMINISTIC_DECODER,
                resolution_status=EvidenceResolutionStatus.RESOLVED,
                verification_ids=(verification_id,),
                agent_confidence=None,
            )
        )

    conflict_rows = _draft_rows(source_set, "conflicts", "conflict_key")
    conflict_aliases: dict[str, str] = {}
    conflicts_by_id: dict[str, EvidenceConflictV1] = {}
    for _, draft in conflict_rows:
        claim_ids = tuple(
            sorted(
                claim_aliases[item]
                for item in _strings(
                    draft["claim_keys"],
                    "claim_keys",
                )
            )
        )
        identity = {
            "kind": str(draft["kind"]),
            "claim_ids": claim_ids,
            "comparison_unit": str(draft["comparison_unit"]),
        }
        conflict_id = _semantic_id("conflict", identity)
        conflict_aliases[str(draft["conflict_key"])] = conflict_id
        conflicts_by_id.setdefault(
            conflict_id,
            EvidenceConflictV1(
                conflict_id=conflict_id,
                kind=EvidenceConflictKind(str(draft["kind"])),
                claim_ids=claim_ids,
                comparison_unit=str(draft["comparison_unit"]),
                status=EvidenceConflictStatus.UNRESOLVED,
            ),
        )

    verification_by_claim = {
        item.claim_id: item.verification_id for item in verifications
    }
    source_asset_by_unit = {
        item.source_unit_id: item.asset_id
        for item in (*bundle.chunks, *bundle.structured_units)
    }
    part_by_asset = {item.asset_id: item for item in source_set.parts}
    dispositions: list[SemanticSourceDispositionV1] = []
    fallback_verification_id = verifications[0].verification_id
    for source_unit_id in bundle.source_unit_ids:
        asset_id = source_asset_by_unit[source_unit_id]
        part = part_by_asset.get(asset_id)
        if part is None:
            if asset_id != source_set.manifest_asset_id:
                raise _error(
                    "canonical_source_bundle_mismatch",
                    "source bundle contains an unrecognized canonical asset",
                )
            dispositions.append(
                SemanticSourceDispositionV1(
                    source_unit_id=source_unit_id,
                    disposition=SemanticSourceDispositionKind.IRRELEVANT,
                    claim_ids=(),
                    reason_codes=("operator_manifest_control_plane",),
                    auditor_verification_id=fallback_verification_id,
                )
            )
            continue
        claim_ids = tuple(sorted(claims_cited_by_part[part.part_key]))
        dispositions.append(
            SemanticSourceDispositionV1(
                source_unit_id=source_unit_id,
                disposition=SemanticSourceDispositionKind.EVIDENCE,
                claim_ids=claim_ids,
                reason_codes=("canonical_typed_source",),
                auditor_verification_id=verification_by_claim[
                    claim_ids[0]
                ],
            )
        )

    graph = SelfImprovementEvidenceGraphV1(
        spans=spans,
        entities=tuple(entities_by_id.values()),
        claims=tuple(claims),
        claim_verifications=tuple(verifications),
        source_dispositions=tuple(dispositions),
        conflicts=tuple(conflicts_by_id.values()),
        unresolved_references=(),
        profile_fingerprint=profile.fingerprint,
        extractor_population_fingerprint=(
            CANONICAL_SEMANTIC_DECODER_PROTOCOL_FINGERPRINT
        ),
    )
    validation = validate_evidence_graph_against_source_bundle(
        bundle,
        graph,
    )
    if not validation.valid:
        raise _error(
            "canonical_source_attestation_invalid",
            "canonical graph is not exactly bound to its source bundle",
        )
    authority_context = issue_evidence_authority_context(
        graph,
        deterministic_verification_ids=tuple(
            item.verification_id for item in verifications
        ),
        source_bundle_fingerprint=bundle.fingerprint,
        constitution_fingerprint=(
            qualification_evidence.constitution_fingerprint
        ),
    )

    semantic_cases, case_aliases = _decode_cases(
        source_set,
        graph=graph,
        entity_aliases=entity_aliases,
        claim_aliases=claim_aliases,
        conflict_aliases=conflict_aliases,
    )
    signal_set, signal_aliases = _decode_signals(
        source_set,
        graph=graph,
        semantic_cases=semantic_cases,
        case_aliases=case_aliases,
        entity_aliases=entity_aliases,
        claim_aliases=claim_aliases,
        conflict_aliases=conflict_aliases,
    )
    plans = _decode_plans(
        source_set,
        graph=graph,
        semantic_cases=semantic_cases,
        signal_set=signal_set,
        profile=profile,
        manifest_origin=ManifestOrigin(manifest_origin),
        manifest_fingerprint=manifest_fingerprint,
        qualification_evidence=qualification_evidence,
        authority_context=authority_context,
        case_aliases=case_aliases,
        entity_aliases=entity_aliases,
        claim_aliases=claim_aliases,
        signal_aliases=signal_aliases,
    )
    resolved_traces = _attest_canonical_traces(
        traces_by_key,
        graph=graph,
        bundle=bundle,
        extractor_fingerprints=extractor_fingerprints,
    )
    return CanonicalSemanticDecodeResultV1(
        source_set_fingerprint=source_set.fingerprint,
        evidence_graph=graph,
        evidence_authority_context=authority_context,
        semantic_cases=semantic_cases,
        improvement_signal_set=signal_set,
        evaluation_plans=plans,
        resolved_traces=resolved_traces,
    )


def _decode_cases(
    source_set: CanonicalSemanticSourceSetV1,
    *,
    graph: SelfImprovementEvidenceGraphV1,
    entity_aliases: Mapping[str, str],
    claim_aliases: Mapping[str, str],
    conflict_aliases: Mapping[str, str],
) -> tuple[tuple[SelfImprovementCaseV1, ...], Mapping[str, str]]:
    aliases: dict[str, str] = {}
    cases: dict[str, SelfImprovementCaseV1] = {}
    conflicts = {item.conflict_id: item for item in graph.conflicts}
    claims = {item.claim_id: item for item in graph.claims}
    for _, draft in _draft_rows(source_set, "cases", "case_key"):
        conflict_ids = _remap(
            draft.get("conflict_keys", ()),
            conflict_aliases,
            "conflict_keys",
        )
        claim_groups = tuple(
            _remap(draft.get(name, ()), claim_aliases, name)
            for name in (
                "input_claim_keys",
                "trajectory_claim_keys",
                "result_claim_keys",
                "comparison_claim_keys",
            )
        )
        ambiguous = any(
            conflicts[item].status is EvidenceConflictStatus.UNRESOLVED
            for item in conflict_ids
        ) or any(
            claims[item].resolution_status
            is not EvidenceResolutionStatus.RESOLVED
            for group in claim_groups
            for item in group
        )
        case = SelfImprovementCaseV1(
            case_id=_semantic_id(
                "case-draft",
                {"case_key": str(draft["case_key"])},
            ),
            task_entity_id=entity_aliases[str(draft["task_entity_key"])],
            input_claim_ids=claim_groups[0],
            execution_entity_ids=_remap(
                draft["execution_entity_keys"],
                entity_aliases,
                "execution_entity_keys",
            ),
            trajectory_claim_ids=claim_groups[1],
            result_claim_ids=claim_groups[2],
            comparison_claim_ids=claim_groups[3],
            conflict_ids=conflict_ids,
            resolution_status=(
                SelfImprovementCaseResolutionStatus.AMBIGUOUS
                if ambiguous
                else SelfImprovementCaseResolutionStatus.RESOLVED
            ),
            trainable_signal_projection={},
        )
        case.validate_against(graph)
        canonical_id = canonical_semantic_case_id(case, graph=graph)
        canonical = replace(case, case_id=canonical_id)
        existing = cases.get(canonical_id)
        if existing is not None and existing != canonical:
            raise _error(
                "duplicate_identity",
                "canonical cases collapse to conflicting identities",
            )
        cases[canonical_id] = canonical
        aliases[str(draft["case_key"])] = canonical_id
    return tuple(sorted(cases.values(), key=lambda item: item.case_id)), aliases


def _decode_signals(
    source_set: CanonicalSemanticSourceSetV1,
    *,
    graph: SelfImprovementEvidenceGraphV1,
    semantic_cases: Sequence[SelfImprovementCaseV1],
    case_aliases: Mapping[str, str],
    entity_aliases: Mapping[str, str],
    claim_aliases: Mapping[str, str],
    conflict_aliases: Mapping[str, str],
) -> tuple[SelfImprovementSignalSetV1, Mapping[str, str]]:
    cases = {item.case_id: item for item in semantic_cases}
    aliases: dict[str, str] = {}
    signals: dict[str, SelfImprovementSignalV1] = {}
    for _, draft in _draft_rows(source_set, "signals", "signal_key"):
        case_id = case_aliases[str(draft["case_key"])]
        behavior = _mapping(draft["behavior_delta"], "behavior_delta")
        conflict_ids = _remap(
            draft.get("conflict_keys", ()),
            conflict_aliases,
            "conflict_keys",
        )
        delta = BehaviorDeltaV1(
            preferred_observations=_strings(
                behavior["preferred_observations"],
                "preferred_observations",
            ),
            non_preferred_observations=_strings(
                behavior["non_preferred_observations"],
                "non_preferred_observations",
            ),
            result_difference=_strings(
                behavior["result_difference"],
                "result_difference",
            ),
            source_claim_ids=_remap(
                behavior["source_claim_keys"],
                claim_aliases,
                "source_claim_keys",
            ),
        )
        supporting = _remap(
            draft["supporting_claim_keys"],
            claim_aliases,
            "supporting_claim_keys",
        )
        guidance_complete = all(
            _strings(draft.get(name, ()), name)
            for name in (
                "desired_behavior",
                "avoid_behavior",
                "capability_requirement",
            )
        )
        unresolved_conflict = bool(conflict_ids)
        actionable = (
            bool(supporting)
            and delta.is_contrastive
            and guidance_complete
            and not unresolved_conflict
        )
        signal = SelfImprovementSignalV1(
            signal_id=_semantic_id(
                "signal-draft",
                {"signal_key": str(draft["signal_key"])},
            ),
            case_id=case_id,
            kind=SelfImprovementSignalKind(str(draft["kind"])),
            compared_execution_ids=_remap(
                draft["compared_execution_keys"],
                entity_aliases,
                "compared_execution_keys",
            ),
            preferred_execution_ids=_remap(
                draft.get("preferred_execution_keys", ()),
                entity_aliases,
                "preferred_execution_keys",
            ),
            supporting_claim_ids=supporting,
            opposing_claim_ids=_remap(
                draft.get("opposing_claim_keys", ()),
                claim_aliases,
                "opposing_claim_keys",
            ),
            behavior_delta=delta,
            metric_delta={
                str(key): float(value)
                for key, value in _mapping(
                    draft.get("metric_delta", {}),
                    "metric_delta",
                ).items()
            },
            desired_behavior=_strings(
                draft.get("desired_behavior", ()),
                "desired_behavior",
            ),
            avoid_behavior=_strings(
                draft.get("avoid_behavior", ()),
                "avoid_behavior",
            ),
            capability_requirement=_strings(
                draft.get("capability_requirement", ()),
                "capability_requirement",
            ),
            conflict_ids=conflict_ids,
            verification_status=SignalVerificationStatus.VERIFIED,
            actionability=(
                SignalActionability.ACTIONABLE
                if actionable
                else SignalActionability.BLOCKED
            ),
            reason_codes=(
                ("canonical_evidence_verified",)
                if actionable
                else (
                    "canonical_signal_not_actionable",
                    *(
                        ("unresolved_semantic_conflict",)
                        if unresolved_conflict
                        else ()
                    ),
                )
            ),
        )
        signal.validate_against(graph, cases[case_id])
        canonical_id = canonical_semantic_signal_id(signal)
        canonical = replace(signal, signal_id=canonical_id)
        existing = signals.get(canonical_id)
        if existing is not None and existing != canonical:
            raise _error(
                "duplicate_identity",
                "canonical signals collapse to conflicting identities",
            )
        signals[canonical_id] = canonical
        aliases[str(draft["signal_key"])] = canonical_id
    case_splits = _framework_case_splits(
        tuple(item.case_id for item in semantic_cases)
    )
    report_suffix = graph.logical_fingerprint.removeprefix("sha256:")[:32]
    signal_set = SelfImprovementSignalSetV1(
        signals=tuple(signals.values()),
        case_splits=case_splits,
        synthesis_report_refs=(f"canonical-synthesis:{report_suffix}",),
        critic_report_refs=(f"canonical-critic:{report_suffix}",),
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    return signal_set, aliases


def _decode_plans(
    source_set: CanonicalSemanticSourceSetV1,
    *,
    graph: SelfImprovementEvidenceGraphV1,
    semantic_cases: Sequence[SelfImprovementCaseV1],
    signal_set: SelfImprovementSignalSetV1,
    profile: SemanticIngestionProfileV1,
    manifest_origin: ManifestOrigin,
    manifest_fingerprint: str | None,
    qualification_evidence: SemanticQualificationEvidenceV1,
    authority_context: EvidenceAuthorityContextV1,
    case_aliases: Mapping[str, str],
    entity_aliases: Mapping[str, str],
    claim_aliases: Mapping[str, str],
    signal_aliases: Mapping[str, str],
) -> tuple[SelfImprovementEvaluationPlanV1, ...]:
    cases = {item.case_id: item for item in semantic_cases}
    signals = {item.signal_id: item for item in signal_set.signals}
    plans: dict[str, SelfImprovementEvaluationPlanV1] = {}
    for _, draft in _draft_rows(source_set, "plans", "plan_key"):
        case_id = case_aliases[str(draft["case_key"])]
        training_signal_ids = _remap(
            draft.get("training_signal_keys", ()),
            signal_aliases,
            "training_signal_keys",
        )
        if signal_set.case_splits[case_id] is DatasetSplit.HELD_OUT:
            training_signal_ids = ()
        supporting_claim_ids = tuple(
            sorted(
                {
                    claim_id
                    for signal_id in training_signal_ids
                    for claim_id in (
                        *signals[signal_id].supporting_claim_ids,
                        *signals[signal_id].opposing_claim_ids,
                        *signals[signal_id].behavior_delta.source_claim_ids,
                    )
                }
            )
        )
        replay_key = draft.get("replay_seed_execution_key")
        expected_key = draft.get("expected_output_claim_key")
        proposal = SelfImprovementEvaluationPlanV1(
            plan_id=_semantic_id(
                "plan-draft",
                {"plan_key": str(draft["plan_key"])},
            ),
            case_id=case_id,
            comparison_unit=profile.comparison_unit,
            training_signal_ids=training_signal_ids,
            supporting_evidence_claim_ids=supporting_claim_ids,
            replay_seed_execution_id=(
                entity_aliases[str(replay_key)]
                if replay_key is not None
                and signal_set.case_splits[case_id]
                is not DatasetSplit.HELD_OUT
                else None
            ),
            expected_output_claim_id=(
                claim_aliases[str(expected_key)]
                if expected_key is not None
                and signal_set.case_splits[case_id]
                is not DatasetSplit.HELD_OUT
                else None
            ),
            human_claim_authority=profile.human_claim_authority,
            historical_judge_authority=(
                profile.historical_judge_authority
            ),
            rubric_groups={
                str(group): tuple(
                    entity_aliases[str(item)]
                    for item in _sequence(values, "rubric_groups")
                )
                for group, values in _mapping(
                    draft.get("rubric_groups", {}),
                    "rubric_groups",
                ).items()
            },
            aggregation_policy=profile.aggregation_policy,
            conflict_policy=profile.conflict_policy,
            current_evaluator_required=True,
            disposition=EvaluationDisposition.PROPOSAL_ONLY,
            reason_codes=("canonical_source_proposal",),
            profile_fingerprint=profile.fingerprint,
        )
        compiled = compile_evaluation_plan(
            proposal,
            profile=profile,
            manifest_origin=manifest_origin,
            manifest_fingerprint=(
                manifest_fingerprint
                or fingerprint_json(
                    {"manifest_origin": manifest_origin.value}
                )
            ),
            graph=graph,
            case=cases[case_id],
            signal_set=signal_set,
            authority_context=authority_context,
            qualification_report=qualification_evidence.report,
            qualification_registry=qualification_evidence.registry,
            model_profile_fingerprint=(
                qualification_evidence.model_profile_fingerprint
            ),
            provider_fingerprint=(
                qualification_evidence.provider_fingerprint
            ),
            semantic_protocol_fingerprint=(
                qualification_evidence.semantic_protocol_fingerprint
            ),
            constitution_fingerprint=(
                qualification_evidence.constitution_fingerprint
            ),
            qualification_corpus_fingerprint=(
                qualification_evidence.corpus_fingerprint
            ),
            qualification_threshold_set_fingerprint=(
                qualification_evidence.threshold_set_fingerprint
            ),
            _framework_extractor_attestation=(
                _FRAMEWORK_DETERMINISTIC_EXTRACTOR_ATTESTATION
                if (
                    qualification_evidence.extraction_origin.value
                    == "deterministic_canonical"
                    and qualification_evidence
                    .deterministic_attestation_fingerprint
                    == CANONICAL_SEMANTIC_DECODER_PROTOCOL_FINGERPRINT
                )
                else None
            ),
        )
        canonical = replace(
            compiled,
            plan_id=canonical_semantic_plan_id(compiled),
        )
        if canonical.case_id in plans:
            raise _error(
                "duplicate_identity",
                "canonical source must contain one plan per case",
            )
        plans[canonical.case_id] = canonical
    if plans and set(plans) != set(cases):
        raise _error(
            "evaluation_plan_coverage_incomplete",
            "canonical plans must cover every semantic case",
        )
    return tuple(plans[key] for key in sorted(plans))


def _attest_canonical_traces(
    traces_by_key: Mapping[str, ResolvedSemanticTraceV1],
    *,
    graph: SelfImprovementEvidenceGraphV1,
    bundle: SourceBundleV1,
    extractor_fingerprints: Sequence[str],
) -> tuple[ResolvedSemanticTraceV1, ...]:
    claims_by_ref: dict[str, list[EvidenceClaimV1]] = {}
    for claim in graph.claims:
        if claim.kind is EvidenceClaimKind.EXECUTION_TRAJECTORY:
            claims_by_ref.setdefault(str(claim.payload["trace_ref"]), []).append(
                claim
            )
    result: list[ResolvedSemanticTraceV1] = []
    for trace in traces_by_key.values():
        claims = claims_by_ref.get(trace.trace_ref, [])
        if len(claims) != 1:
            raise _error(
                "canonical_trace_reference_invalid",
                "each canonical trace must be referenced by exactly one claim",
            )
        result.append(
            attest_resolved_trace(
                trace,
                graph=graph,
                trajectory_claim_id=claims[0].claim_id,
                source_bundle=bundle,
                candidate_attestations=(),
                extractor_fingerprints=extractor_fingerprints,
                extraction_origin=(
                    TraceExtractionOrigin.DETERMINISTIC_DECODER
                ),
            )
        )
    return tuple(sorted(result, key=lambda item: item.trace_ref))


def _validate_part(value: Mapping[str, Any]) -> Mapping[str, Any]:
    _exact_fields(
        value,
        required=_TOP_LEVEL_FIELDS,
        optional=frozenset(),
        name="canonical source part",
    )
    if value.get("schema_version") != (
        CANONICAL_SEMANTIC_SOURCE_SCHEMA_VERSION
    ):
        raise _error(
            "schema_version_mismatch",
            "invalid canonical semantic source schema",
        )
    for name in ("bundle_key", "part_key"):
        validate_safe_id(str(value.get(name) or ""), field_name=name)
    for name in _TOP_LEVEL_ARRAY_FIELDS:
        _sequence(value[name], name)
    _reject_controlled_fields(
        {
            key: item
            for key, item in value.items()
            if key != "schema_version"
        }
    )

    specs = {
        "entities": (
            {"entity_key", "kind", "canonical_name"},
            {"aliases", "attributes"},
        ),
        "claims": (
            {
                "claim_key",
                "kind",
                "subject_entity_keys",
                "payload",
                "citation_part_keys",
            },
            {"object_entity_keys"},
        ),
        "conflicts": (
            {"conflict_key", "kind", "claim_keys", "comparison_unit"},
            set(),
        ),
        "cases": (
            {"case_key", "task_entity_key", "execution_entity_keys"},
            {
                "input_claim_keys",
                "trajectory_claim_keys",
                "result_claim_keys",
                "comparison_claim_keys",
                "conflict_keys",
            },
        ),
        "signals": (
            {
                "signal_key",
                "case_key",
                "kind",
                "compared_execution_keys",
                "supporting_claim_keys",
                "behavior_delta",
            },
            {
                "preferred_execution_keys",
                "opposing_claim_keys",
                "metric_delta",
                "desired_behavior",
                "avoid_behavior",
                "capability_requirement",
                "conflict_keys",
            },
        ),
        "plans": (
            {"plan_key", "case_key"},
            {
                "training_signal_keys",
                "replay_seed_execution_key",
                "expected_output_claim_key",
                "rubric_groups",
            },
        ),
        "traces": (
            {"trace_key", "trajectory"},
            set(),
        ),
    }
    for collection, (required, optional) in specs.items():
        for item in _sequence(value[collection], collection):
            mapping = _mapping(item, collection)
            _exact_fields(
                mapping,
                required=frozenset(required),
                optional=frozenset(optional),
                name=collection,
            )
    for signal in _sequence(value["signals"], "signals"):
        behavior = _mapping(signal["behavior_delta"], "behavior_delta")
        _exact_fields(
            behavior,
            required=frozenset(
                {
                    "preferred_observations",
                    "non_preferred_observations",
                    "result_difference",
                    "source_claim_keys",
                }
            ),
            optional=frozenset(),
            name="behavior_delta",
        )
    return MappingProxyType(dict(value))


def _validate_global_draft_contract(
    source_set: CanonicalSemanticSourceSetV1,
) -> None:
    if source_set.schema_version != CANONICAL_SEMANTIC_SOURCE_SCHEMA_VERSION:
        raise _error(
            "schema_version_mismatch",
            "invalid canonical semantic source set schema",
        )
    part_keys = {item.part_key for item in source_set.parts}
    if not part_keys:
        raise _error(
            "canonical_source_empty",
            "canonical source set requires at least one part",
        )
    if any(item.bundle_key != source_set.bundle_key for item in source_set.parts):
        raise _error(
            "canonical_bundle_mismatch",
            "canonical source set contains another bundle",
        )
    for part in source_set.parts:
        validate_safe_id(part.bundle_key, field_name="bundle_key")
        validate_safe_id(part.part_key, field_name="part_key")
        validated = _validate_part(part.payload)
        if (
            validated["bundle_key"] != part.bundle_key
            or validated["part_key"] != part.part_key
            or not part.source_unit_ids
            or not part.chunk_ids
        ):
            raise _error(
                "canonical_source_bundle_mismatch",
                "canonical part metadata differs from its source payload",
            )

    key_fields = {
        "entities": "entity_key",
        "claims": "claim_key",
        "conflicts": "conflict_key",
        "cases": "case_key",
        "signals": "signal_key",
        "plans": "plan_key",
        "traces": "trace_key",
    }
    indexes: dict[str, set[str]] = {}
    for collection, key_field in key_fields.items():
        values = [
            str(item[key_field])
            for _, item in _draft_rows(
                source_set,
                collection,
                key_field,
                validate_unique=False,
            )
        ]
        for value in values:
            validate_safe_id(value, field_name=key_field)
        if len(values) != len(set(values)):
            raise _error(
                "duplicate_identity",
                f"canonical {key_field} values must be unique",
            )
        indexes[collection] = set(values)
    if not indexes["claims"]:
        raise _error(
            "canonical_claim_missing",
            "canonical source requires at least one claim",
        )

    def refs(
        draft: Mapping[str, Any],
        field: str,
        collection: str,
        *,
        required: bool = False,
    ) -> None:
        values = _strings(draft.get(field, ()), field)
        if required and not values:
            raise _error(
                "canonical_reference_missing",
                f"{field} must not be empty",
            )
        missing = sorted(set(values) - indexes[collection])
        if missing:
            raise _error(
                "canonical_reference_dangling",
                f"{field} references an unknown key: {missing[0]}",
            )

    for _, draft in _draft_rows(source_set, "claims", "claim_key"):
        refs(draft, "subject_entity_keys", "entities", required=True)
        refs(draft, "object_entity_keys", "entities")
        citation_parts = _strings(
            draft["citation_part_keys"],
            "citation_part_keys",
        )
        if not citation_parts or set(citation_parts) - part_keys:
            raise _error(
                "canonical_reference_dangling",
                "claim citation_part_keys must reference canonical parts",
            )
        if str(draft["kind"]) == EvidenceClaimKind.EXECUTION_TRAJECTORY.value:
            trace_key = _mapping(draft["payload"], "payload").get(
                "trace_key"
            )
            if not isinstance(trace_key, str) or trace_key not in indexes["traces"]:
                raise _error(
                    "canonical_reference_dangling",
                    "trajectory claim requires a known payload.trace_key",
                )
    conflict_rows = _draft_rows(
        source_set,
        "conflicts",
        "conflict_key",
    )
    for _, draft in conflict_rows:
        refs(draft, "claim_keys", "claims", required=True)
    declared_conflict_claim_sets = tuple(
        frozenset(_strings(draft["claim_keys"], "claim_keys"))
        for _, draft in conflict_rows
    )
    claims_by_slot: dict[
        tuple[str, tuple[str, ...], tuple[str, ...]],
        list[tuple[str, str]],
    ] = {}
    for _, draft in _draft_rows(source_set, "claims", "claim_key"):
        slot = (
            str(draft["kind"]),
            tuple(
                sorted(
                    _strings(
                        draft["subject_entity_keys"],
                        "subject_entity_keys",
                    )
                )
            ),
            tuple(
                sorted(
                    _strings(
                        draft.get("object_entity_keys", ()),
                        "object_entity_keys",
                    )
                )
            ),
        )
        claims_by_slot.setdefault(slot, []).append(
            (
                str(draft["claim_key"]),
                fingerprint_json(
                    _mapping(draft["payload"], "payload")
                ),
            )
        )
    for rows in claims_by_slot.values():
        for index, (left_key, left_payload) in enumerate(rows):
            for right_key, right_payload in rows[index + 1 :]:
                if left_payload == right_payload:
                    continue
                pair = {left_key, right_key}
                if not any(
                    pair.issubset(claim_keys)
                    for claim_keys in declared_conflict_claim_sets
                ):
                    raise _error(
                        "canonical_conflict_undeclared",
                        "canonical claims occupying the same semantic slot "
                        "have different payloads without an explicit conflict",
                    )
    for _, draft in _draft_rows(source_set, "cases", "case_key"):
        if str(draft["task_entity_key"]) not in indexes["entities"]:
            raise _error(
                "canonical_reference_dangling",
                "case task_entity_key is unknown",
            )
        refs(draft, "execution_entity_keys", "entities", required=True)
        for field in (
            "input_claim_keys",
            "trajectory_claim_keys",
            "result_claim_keys",
            "comparison_claim_keys",
        ):
            refs(draft, field, "claims")
        refs(draft, "conflict_keys", "conflicts")
    for _, draft in _draft_rows(source_set, "signals", "signal_key"):
        if str(draft["case_key"]) not in indexes["cases"]:
            raise _error(
                "canonical_reference_dangling",
                "signal case_key is unknown",
            )
        refs(draft, "compared_execution_keys", "entities", required=True)
        refs(draft, "preferred_execution_keys", "entities")
        refs(draft, "supporting_claim_keys", "claims", required=True)
        refs(draft, "opposing_claim_keys", "claims")
        refs(draft, "conflict_keys", "conflicts")
        behavior = _mapping(draft["behavior_delta"], "behavior_delta")
        refs(behavior, "source_claim_keys", "claims", required=True)
    for _, draft in _draft_rows(source_set, "plans", "plan_key"):
        if str(draft["case_key"]) not in indexes["cases"]:
            raise _error(
                "canonical_reference_dangling",
                "plan case_key is unknown",
            )
        refs(draft, "training_signal_keys", "signals")
        for field, collection in (
            ("replay_seed_execution_key", "entities"),
            ("expected_output_claim_key", "claims"),
        ):
            value = draft.get(field)
            if value is not None and str(value) not in indexes[collection]:
                raise _error(
                    "canonical_reference_dangling",
                    f"{field} is unknown",
                )
        for values in _mapping(
            draft.get("rubric_groups", {}),
            "rubric_groups",
        ).values():
            missing = set(_strings(values, "rubric_groups")) - indexes["entities"]
            if missing:
                raise _error(
                    "canonical_reference_dangling",
                    "rubric_groups references an unknown entity",
                )


def _draft_rows(
    source_set: CanonicalSemanticSourceSetV1,
    collection: str,
    key_field: str,
    *,
    validate_unique: bool = True,
) -> list[tuple[CanonicalSemanticSourcePartV1, Mapping[str, Any]]]:
    result: list[
        tuple[CanonicalSemanticSourcePartV1, Mapping[str, Any]]
    ] = []
    seen: set[str] = set()
    for part in source_set.parts:
        for value in _sequence(part.payload[collection], collection):
            draft = _mapping(value, collection)
            key = str(draft.get(key_field) or "")
            if validate_unique and key in seen:
                raise _error(
                    "duplicate_identity",
                    f"duplicate canonical {key_field}",
                )
            seen.add(key)
            result.append((part, draft))
    return result


def _rewrite_claim_payload(
    value: Mapping[str, Any],
    *,
    entity_aliases: Mapping[str, str],
    traces_by_key: Mapping[str, ResolvedSemanticTraceV1],
) -> Mapping[str, Any]:
    trace_key = value.get("trace_key")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "trace_key":
            continue
        result[str(key)] = _rewrite_aliases(item, entity_aliases)
    if trace_key is not None:
        trace = traces_by_key[str(trace_key)]
        result["trace_ref"] = trace.trace_ref
        result["trace_fingerprint"] = trace.trace_fingerprint
    return result


def _rewrite_aliases(value: Any, aliases: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _rewrite_aliases(item, aliases)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return tuple(_rewrite_aliases(item, aliases) for item in value)
    if isinstance(value, str):
        return aliases.get(value, value)
    return value


def _framework_case_splits(
    case_ids: tuple[str, ...],
    *,
    split_seed: str = "self-evolve-default-split",
) -> Mapping[str, DatasetSplit]:
    ordered = sorted(
        set(case_ids),
        key=lambda case_id: hashlib.sha256(
            f"{split_seed}:{case_id}".encode("utf-8")
        ).hexdigest(),
    )
    count = len(ordered)
    if count <= 1:
        train = set(ordered)
        validation: set[str] = set()
    elif count == 2:
        train = {ordered[0]}
        validation = set()
    else:
        held_out_count = max(1, count // 5)
        validation_count = max(1, count // 5)
        train_count = count - held_out_count - validation_count
        train = set(ordered[:train_count])
        validation = set(
            ordered[train_count : train_count + validation_count]
        )
    return {
        case_id: (
            DatasetSplit.TRAIN
            if case_id in train
            else (
                DatasetSplit.VALIDATION
                if case_id in validation
                else DatasetSplit.HELD_OUT
            )
        )
        for case_id in ordered
    }


def _semantic_id(prefix: str, identity: Mapping[str, Any]) -> str:
    return (
        f"{prefix}:"
        + fingerprint_json(
            {
                "schema_version": (
                    "aworld.self_evolve.canonical_source_identity.v1"
                ),
                "kind": prefix,
                "identity": identity,
            }
        ).removeprefix("sha256:")[:32]
    )


def _remap(
    value: Any,
    aliases: Mapping[str, str],
    field_name: str,
) -> tuple[str, ...]:
    return tuple(aliases[item] for item in _strings(value, field_name))


def _exact_fields(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str],
    name: str,
) -> None:
    actual = {str(key) for key in value}
    missing = required - actual
    unknown = actual - required - optional
    if missing or unknown:
        detail = (
            f" missing={sorted(missing)}" if missing else ""
        ) + (f" unknown={sorted(unknown)}" if unknown else "")
        raise _error(
            "canonical_schema_invalid",
            f"{name} has invalid fields:{detail}",
        )


def _reject_controlled_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key)
            if normalized in _CONTROLLED_FIELDS:
                raise _error(
                    "canonical_control_field_forbidden",
                    f"canonical source cannot declare {path}.{normalized}",
                )
            _reject_controlled_fields(item, f"{path}.{normalized}")
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _reject_controlled_fields(item, f"{path}[{index}]")


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(
            "canonical_schema_invalid",
            f"{field_name} must be an object",
        )
    return value


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise _error(
            "canonical_schema_invalid",
            f"{field_name} must be an array",
        )
    return value


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    sequence = _sequence(value, field_name)
    result = tuple(str(item) for item in sequence)
    if any(not isinstance(item, str) or not item for item in sequence):
        raise _error(
            "canonical_schema_invalid",
            f"{field_name} must contain non-empty strings",
        )
    if len(result) != len(set(result)):
        raise _error(
            "duplicate_identity",
            f"{field_name} must contain unique values",
        )
    return result


def _error(reason_code: str, message: str) -> IngestionContractError:
    return IngestionContractError(reason_code, message)
