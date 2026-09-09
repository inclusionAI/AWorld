from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping, Sequence

from aworld.self_evolve.evaluation_plan import (
    SemanticIngestionProfileV1,
)
from aworld.self_evolve.evidence import (
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
    SelfImprovementEvidenceGraphV1,
    SemanticSourceDispositionKind,
    SemanticSourceDispositionV1,
)

from .types import canonical_json_bytes, fingerprint_json


_WHITESPACE_PATTERN = re.compile(r"\s+")


def canonicalize_evidence_graph(
    graph: SelfImprovementEvidenceGraphV1,
    *,
    profile: SemanticIngestionProfileV1,
) -> SelfImprovementEvidenceGraphV1:
    """Canonicalize agent-local IDs without using source layout."""

    entity_id_map, entities, ambiguous_entity_ids = _canonical_entities(
        graph.entities,
        claims=graph.claims,
        profile=profile,
    )
    verification_by_id = {
        item.verification_id: item
        for item in graph.claim_verifications
    }
    claim_id_map: dict[str, str] = {}
    claim_rows: dict[str, list[tuple[EvidenceClaimV1, Mapping[str, Any]]]] = {}
    for claim in graph.claims:
        payload = _rewrite_entity_references(
            claim.payload,
            entity_id_map,
        )
        identity = {
            "kind": claim.kind.value,
            "subject_entity_ids": sorted(
                entity_id_map[item]
                for item in claim.subject_entity_ids
            ),
            "object_entity_ids": sorted(
                entity_id_map[item]
                for item in claim.object_entity_ids
            ),
            "payload": payload,
        }
        claim_id = _semantic_id("claim", identity)
        claim_id_map[claim.claim_id] = claim_id
        claim_rows.setdefault(claim_id, []).append((claim, payload))

    claims: list[EvidenceClaimV1] = []
    verifications: list[ClaimVerificationV1] = []
    for claim_id in sorted(claim_rows):
        rows = claim_rows[claim_id]
        original_verifications = [
            verification_by_id[verification_id]
            for claim, _ in rows
            for verification_id in claim.verification_ids
        ]
        resolution = _conservative_resolution(
            tuple(claim for claim, _ in rows),
            original_verifications,
        )
        rewritten_verifications = tuple(
            _canonical_verification(
                item,
                claim_id=claim_id,
            )
            for item in original_verifications
        )
        unique_verifications = {
            item.verification_id: item
            for item in rewritten_verifications
        }
        verifications.extend(unique_verifications.values())
        first, payload = rows[0]
        producers = {
            claim.producer_kind for claim, _ in rows
        }
        producer = _conservative_producer(producers)
        confidences = [
            claim.agent_confidence
            for claim, _ in rows
            if claim.agent_confidence is not None
        ]
        claims.append(
            EvidenceClaimV1(
                claim_id=claim_id,
                kind=first.kind,
                subject_entity_ids=tuple(
                    sorted(
                        {
                            entity_id_map[item]
                            for claim, _ in rows
                            for item in claim.subject_entity_ids
                        }
                    )
                ),
                object_entity_ids=tuple(
                    sorted(
                        {
                            entity_id_map[item]
                            for claim, _ in rows
                            for item in claim.object_entity_ids
                        }
                    )
                ),
                payload=payload,
                source_span_ids=tuple(
                    sorted(
                        {
                            item
                            for claim, _ in rows
                            for item in claim.source_span_ids
                        }
                    )
                ),
                producer_kind=producer,
                resolution_status=resolution,
                verification_ids=tuple(
                    sorted(unique_verifications)
                ),
                agent_confidence=(
                    min(confidences) if confidences else None
                ),
            )
        )

    dispositions = tuple(
        _rewrite_disposition(item, claim_id_map)
        for item in graph.source_dispositions
    )
    conflicts = _canonical_conflicts(
        graph.conflicts,
        claims=claims,
        claim_id_map=claim_id_map,
        ambiguous_entity_ids=ambiguous_entity_ids,
    )
    return SelfImprovementEvidenceGraphV1(
        spans=graph.spans,
        entities=tuple(entities),
        claims=tuple(claims),
        claim_verifications=tuple(
            {
                item.verification_id: item
                for item in verifications
            }.values()
        ),
        source_dispositions=dispositions,
        conflicts=conflicts,
        unresolved_references=graph.unresolved_references,
        profile_fingerprint=profile.fingerprint,
        extractor_population_fingerprint=(
            graph.extractor_population_fingerprint
        ),
    )


def resolve_evidence_graph_deterministically(
    graph: SelfImprovementEvidenceGraphV1,
    *,
    profile: SemanticIngestionProfileV1,
) -> tuple[SelfImprovementEvidenceGraphV1, bool]:
    """Run the pure resolver twice and attest identical logical output."""

    first = canonicalize_evidence_graph(graph, profile=profile)
    second = canonicalize_evidence_graph(graph, profile=profile)
    return (
        first,
        first.logical_fingerprint == second.logical_fingerprint
        and canonical_json_bytes(first.to_dict())
        == canonical_json_bytes(second.to_dict()),
    )


def semantic_candidate_consensus(
    candidates: Sequence[SelfImprovementEvidenceGraphV1],
) -> float:
    """Return exact logical-claim Jaccard consensus for a population."""

    if not candidates:
        return 0.0
    claim_sets = [
        {
            canonical_json_bytes(
                {
                    "kind": claim.kind.value,
                    "subjects": sorted(claim.subject_entity_ids),
                    "objects": sorted(claim.object_entity_ids),
                    "payload": claim.payload,
                    "resolution": claim.resolution_status.value,
                }
            )
            for claim in graph.claims
        }
        for graph in candidates
    ]
    union = set().union(*claim_sets)
    if not union:
        return 1.0
    intersection = set(claim_sets[0]).intersection(*claim_sets[1:])
    return len(intersection) / len(union)


def _canonical_entities(
    entities: Sequence[EvidenceEntityV1],
    *,
    claims: Sequence[EvidenceClaimV1],
    profile: SemanticIngestionProfileV1,
) -> tuple[
    dict[str, str],
    tuple[EvidenceEntityV1, ...],
    frozenset[str],
]:
    entity_contexts = _entity_contexts(entities, claims)
    alias_indexes: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {}
    for kind, canonical_values in profile.entity_aliases.items():
        index: dict[str, tuple[str, tuple[str, ...]]] = {}
        for canonical, aliases in canonical_values.items():
            stable_aliases = tuple(
                sorted(
                    {
                        str(canonical),
                        *(str(item) for item in aliases),
                    },
                    key=_normalized_text,
                )
            )
            for alias in stable_aliases:
                index[_normalized_text(alias)] = (
                    str(canonical),
                    stable_aliases,
                )
        alias_indexes[str(kind)] = index

    identity_rows: dict[
        tuple[str, str, tuple[str, ...]],
        list[EvidenceEntityV1],
    ] = {}
    canonical_metadata: dict[
        tuple[str, str, tuple[str, ...]],
        tuple[str, tuple[str, ...]],
    ] = {}
    for entity in entities:
        alias_index = alias_indexes.get(entity.kind.value, {})
        matched = alias_index.get(
            _normalized_text(entity.canonical_name)
        )
        if matched is None:
            for alias in entity.aliases:
                matched = alias_index.get(_normalized_text(alias))
                if matched is not None:
                    break
        if matched is None:
            canonical_name = _normalized_text(entity.canonical_name)
            stable_aliases: tuple[str, ...] = ()
        else:
            canonical_name, stable_aliases = matched
        key = (
            entity.kind.value,
            _normalized_text(canonical_name),
            entity_contexts.get(entity.entity_id, ()),
        )
        identity_rows.setdefault(key, []).append(entity)
        canonical_metadata[key] = (canonical_name, stable_aliases)

    id_map: dict[str, str] = {}
    result: list[EvidenceEntityV1] = []
    ambiguous_entity_ids: set[str] = set()
    for key in sorted(identity_rows):
        rows = identity_rows[key]
        canonical_name, stable_aliases = canonical_metadata[key]
        entity_id = _semantic_id(
            f"entity-{key[0]}",
            {
                "kind": key[0],
                "canonical_key": key[1],
                "semantic_context": list(key[2]),
            },
        )
        for row in rows:
            id_map[row.entity_id] = entity_id
        attributes = _merge_entity_attributes(rows)
        if attributes.get("resolution_status") == "unresolved":
            ambiguous_entity_ids.add(entity_id)
        result.append(
            EvidenceEntityV1(
                entity_id=entity_id,
                kind=rows[0].kind,
                canonical_name=canonical_name,
                aliases=stable_aliases,
                source_span_ids=tuple(
                    sorted(
                        {
                            item
                            for row in rows
                            for item in row.source_span_ids
                        }
                    )
                ),
                attributes=attributes,
            )
        )
    return id_map, tuple(result), frozenset(ambiguous_entity_ids)


def _entity_contexts(
    entities: Sequence[EvidenceEntityV1],
    claims: Sequence[EvidenceClaimV1],
) -> Mapping[str, tuple[str, ...]]:
    """Build semantic context keys without using paths, spans, or local IDs."""

    by_id = {item.entity_id: item for item in entities}
    contexts: dict[str, set[str]] = {
        item.entity_id: set() for item in entities
    }
    context_attribute_names = {
        "case_id",
        "context_id",
        "context_key",
        "execution_id",
        "harness_id",
        "harness_ref",
        "run_id",
        "task_id",
        "task_ref",
    }
    for entity in entities:
        for key in sorted(context_attribute_names):
            if key not in entity.attributes:
                continue
            contexts[entity.entity_id].add(
                "attribute:"
                + key
                + ":"
                + fingerprint_json(
                    {"value": entity.attributes[key]}
                )
            )

    for claim in claims:
        referenced = {
            *claim.subject_entity_ids,
            *claim.object_entity_ids,
            *_entity_ids_in_value(claim.payload, by_id),
        }
        semantic_anchors = {
            _entity_anchor(by_id[item])
            for item in referenced
            if by_id[item].kind
            in {
                # A task or harness is the stable comparison context for
                # repeated execution/reviewer/result names.
                EvidenceEntityKind.TASK,
                EvidenceEntityKind.HARNESS,
            }
        }
        scope = claim.payload.get("scope")
        if isinstance(scope, str) and scope.strip():
            scoped_entity = by_id.get(scope)
            semantic_anchors.add(
                (
                    _entity_anchor(scoped_entity)
                    if scoped_entity is not None
                    else f"scope:{_normalized_text(scope)}"
                )
            )
        for entity_id in referenced:
            contexts[entity_id].update(semantic_anchors)
    return {
        entity_id: tuple(sorted(values))
        for entity_id, values in contexts.items()
    }


def _entity_anchor(entity: EvidenceEntityV1) -> str:
    return (
        f"{entity.kind.value}:"
        f"{_normalized_text(entity.canonical_name)}"
    )


def _entity_ids_in_value(
    value: Any,
    entities: Mapping[str, EvidenceEntityV1],
) -> set[str]:
    if isinstance(value, Mapping):
        result: set[str] = set()
        for item in value.values():
            result.update(_entity_ids_in_value(item, entities))
        return result
    if isinstance(value, (list, tuple)):
        result = set()
        for item in value:
            result.update(_entity_ids_in_value(item, entities))
        return result
    if isinstance(value, str) and value in entities:
        return {value}
    return set()


def _merge_entity_attributes(
    rows: Sequence[EvidenceEntityV1],
) -> Mapping[str, Any]:
    payloads = {
        canonical_json_bytes(row.attributes): row.attributes
        for row in rows
    }
    if len(payloads) == 1:
        return next(iter(payloads.values()))
    return {
        "resolution_status": "unresolved",
        "attribute_variant_fingerprints": sorted(
            fingerprint_json(value) for value in payloads.values()
        ),
    }


def _canonical_verification(
    verification: ClaimVerificationV1,
    *,
    claim_id: str,
) -> ClaimVerificationV1:
    identity = {
        "claim_id": claim_id,
        "verdict": verification.verdict.value,
        "origin": verification.verification_origin.value,
        "verifier_fingerprint": verification.verifier_fingerprint,
        "independence_group": verification.independence_group,
        "source_span_ids": sorted(verification.source_span_ids),
        "rationale_codes": sorted(verification.rationale_codes),
    }
    return replace(
        verification,
        verification_id=_semantic_id("verification", identity),
        claim_id=claim_id,
    )


def _conservative_resolution(
    claims: Sequence[EvidenceClaimV1],
    verifications: Sequence[ClaimVerificationV1],
) -> EvidenceResolutionStatus:
    if all(
        item.resolution_status is EvidenceResolutionStatus.REJECTED
        for item in claims
    ):
        return EvidenceResolutionStatus.REJECTED
    if (
        all(
            item.resolution_status is EvidenceResolutionStatus.RESOLVED
            for item in claims
        )
        and any(
            item.verdict is ClaimVerificationVerdict.ENTAILED
            for item in verifications
        )
        and not any(
            item.verdict is ClaimVerificationVerdict.CONTRADICTED
            for item in verifications
        )
    ):
        return EvidenceResolutionStatus.RESOLVED
    return EvidenceResolutionStatus.AMBIGUOUS


def _conservative_producer(
    producers: set[EvidenceProducerKind],
) -> EvidenceProducerKind:
    for candidate in (
        EvidenceProducerKind.SEMANTIC_AGENT,
        EvidenceProducerKind.REGISTERED_INGESTOR,
        EvidenceProducerKind.DETERMINISTIC_DECODER,
    ):
        if candidate in producers:
            return candidate
    return EvidenceProducerKind.SEMANTIC_AGENT


def _rewrite_disposition(
    disposition: SemanticSourceDispositionV1,
    claim_id_map: Mapping[str, str],
) -> SemanticSourceDispositionV1:
    claim_ids = tuple(
        sorted(
            {
                claim_id_map[item]
                for item in disposition.claim_ids
            }
        )
    )
    if (
        disposition.disposition
        is SemanticSourceDispositionKind.EVIDENCE
        and not claim_ids
    ):
        return replace(
            disposition,
            disposition=SemanticSourceDispositionKind.UNRESOLVED,
            claim_ids=(),
            reason_codes=("claim_resolution_missing",),
        )
    return replace(disposition, claim_ids=claim_ids)


def _canonical_conflicts(
    existing: Sequence[EvidenceConflictV1],
    *,
    claims: Sequence[EvidenceClaimV1],
    claim_id_map: Mapping[str, str],
    ambiguous_entity_ids: frozenset[str],
) -> tuple[EvidenceConflictV1, ...]:
    result: dict[
        tuple[str, tuple[str, ...], str],
        EvidenceConflictV1,
    ] = {}
    for conflict in existing:
        claim_ids = tuple(
            sorted({claim_id_map[item] for item in conflict.claim_ids})
        )
        if len(claim_ids) < 2:
            continue
        key = (
            conflict.kind.value,
            claim_ids,
            conflict.comparison_unit,
        )
        result[key] = replace(
            conflict,
            conflict_id=_semantic_id(
                "conflict",
                {
                    "kind": conflict.kind.value,
                    "claim_ids": claim_ids,
                    "comparison_unit": conflict.comparison_unit,
                },
            ),
            claim_ids=claim_ids,
        )

    comparison_groups: dict[
        tuple[str, tuple[str, ...]],
        list[EvidenceClaimV1],
    ] = {}
    for claim in claims:
        if claim.kind not in {
            EvidenceClaimKind.HUMAN_COMPARISON,
            EvidenceClaimKind.LLM_JUDGE_ASSESSMENT,
        }:
            continue
        key = (
            str(claim.payload.get("scope") or ""),
            tuple(sorted(claim.object_entity_ids)),
        )
        comparison_groups.setdefault(key, []).append(claim)
    for (scope, _), group in comparison_groups.items():
        preferred = {
            str(item.payload["preferred_entity_id"])
            for item in group
            if item.payload.get("preferred_entity_id") is not None
        }
        if len(preferred) > 1:
            _add_detected_conflict(
                result,
                kind=EvidenceConflictKind.PREFERENCE_DISAGREEMENT,
                claims=group,
                comparison_unit=scope or "unknown_scope",
            )
        judge_claims = [
            item
            for item in group
            if item.kind is EvidenceClaimKind.LLM_JUDGE_ASSESSMENT
        ]
        rubrics = {
            str(item.payload.get("rubric_id") or "")
            for item in judge_claims
        }
        if len(rubrics) > 1:
            _add_detected_conflict(
                result,
                kind=EvidenceConflictKind.RUBRIC_INCOMPATIBLE,
                claims=judge_claims,
                comparison_unit=scope or "unknown_scope",
            )

    score_groups: dict[
        tuple[str, str, tuple[str, ...], tuple[str, ...]],
        list[EvidenceClaimV1],
    ] = {}
    for claim in claims:
        if claim.kind is EvidenceClaimKind.METRIC_OBSERVATION:
            metric_name = str(claim.payload.get("metric_name") or "")
        elif (
            claim.kind is EvidenceClaimKind.LLM_JUDGE_ASSESSMENT
            and "score" in claim.payload
        ):
            metric_name = str(claim.payload.get("rubric_id") or "")
        else:
            continue
        key = (
            metric_name,
            str(claim.payload.get("scope") or ""),
            tuple(sorted(claim.subject_entity_ids)),
            tuple(sorted(claim.object_entity_ids)),
        )
        score_groups.setdefault(key, []).append(claim)
    for (metric_name, scope, _, _), group in score_groups.items():
        score_variants = {
            canonical_json_bytes(
                {
                    "score": (
                        item.payload.get("value")
                        if item.kind
                        is EvidenceClaimKind.METRIC_OBSERVATION
                        else item.payload.get("score")
                    ),
                    "unit": item.payload.get("unit"),
                    "scale": item.payload.get("scale"),
                }
            )
            for item in group
        }
        if len(score_variants) > 1:
            _add_detected_conflict(
                result,
                kind=EvidenceConflictKind.SCORE_INCOMPATIBLE,
                claims=group,
                comparison_unit=(
                    f"{scope}:{metric_name}".strip(":")
                    or "unknown_score_scope"
                ),
            )

    trajectory_groups: dict[
        tuple[tuple[str, ...], str],
        list[EvidenceClaimV1],
    ] = {}
    for claim in claims:
        if claim.kind is not EvidenceClaimKind.EXECUTION_TRAJECTORY:
            continue
        key = (
            tuple(sorted(claim.subject_entity_ids)),
            str(claim.payload.get("scope") or ""),
        )
        trajectory_groups.setdefault(key, []).append(claim)
    for (execution_ids, scope), group in trajectory_groups.items():
        trajectory_variants = {
            (
                str(item.payload.get("trace_ref") or ""),
                str(item.payload.get("trace_fingerprint") or ""),
            )
            for item in group
        }
        if len(trajectory_variants) > 1:
            _add_detected_conflict(
                result,
                kind=EvidenceConflictKind.TRAJECTORY_IDENTITY_COLLISION,
                claims=group,
                comparison_unit=(
                    scope
                    or ",".join(execution_ids)
                    or "unknown_execution"
                ),
            )

    for entity_id in sorted(ambiguous_entity_ids):
        group = [
            claim
            for claim in claims
            if entity_id in claim.subject_entity_ids
            or entity_id in claim.object_entity_ids
            or _value_contains_string(claim.payload, entity_id)
        ]
        if len(group) > 1:
            _add_detected_conflict(
                result,
                kind=EvidenceConflictKind.ENTITY_AMBIGUITY,
                claims=group,
                comparison_unit=entity_id,
            )
    return tuple(result[key] for key in sorted(result))


def _value_contains_string(value: Any, expected: str) -> bool:
    if isinstance(value, Mapping):
        return any(
            _value_contains_string(item, expected)
            for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(
            _value_contains_string(item, expected) for item in value
        )
    return value == expected


def _add_detected_conflict(
    result: dict[
        tuple[str, tuple[str, ...], str],
        EvidenceConflictV1,
    ],
    *,
    kind: EvidenceConflictKind,
    claims: Sequence[EvidenceClaimV1],
    comparison_unit: str,
) -> None:
    claim_ids = tuple(sorted(item.claim_id for item in claims))
    if len(claim_ids) < 2:
        return
    key = (kind.value, claim_ids, comparison_unit)
    result.setdefault(
        key,
        EvidenceConflictV1(
            conflict_id=_semantic_id(
                "conflict",
                {
                    "kind": kind.value,
                    "claim_ids": claim_ids,
                    "comparison_unit": comparison_unit,
                },
            ),
            kind=kind,
            claim_ids=claim_ids,
            comparison_unit=comparison_unit,
            status=EvidenceConflictStatus.UNRESOLVED,
        ),
    )


def _rewrite_entity_references(
    value: Any,
    entity_id_map: Mapping[str, str],
) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _rewrite_entity_references(item, entity_id_map)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _rewrite_entity_references(item, entity_id_map)
            for item in value
        ]
    if isinstance(value, str):
        return entity_id_map.get(value, value)
    return value


def _normalized_text(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value.strip()).casefold()


def _semantic_id(prefix: str, payload: Mapping[str, Any]) -> str:
    return f"{prefix}:{fingerprint_json(payload).removeprefix('sha256:')}"
