from __future__ import annotations

from dataclasses import replace

import pytest

from aworld.self_evolve.evidence import (
    ClaimVerificationOrigin,
    ClaimVerificationV1,
    ClaimVerificationVerdict,
    EvidenceClaimKind,
    EvidenceClaimV1,
    EvidenceConflictKind,
    EvidenceConflictStatus,
    EvidenceConflictV1,
    EvidenceContractError,
    EvidenceEntityKind,
    EvidenceEntityV1,
    EvidenceProducerKind,
    EvidenceResolutionStatus,
    EvidenceSourceSpanV1,
    SelfImprovementCaseResolutionStatus,
    SelfImprovementCaseV1,
    SelfImprovementEvidenceGraphV1,
    SemanticSourceDispositionKind,
    SemanticSourceDispositionV1,
    authoritative_verification_registry_fingerprint,
)


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def _span(
    *,
    span_id: str = "span-1",
    asset_character: str = "a",
    chunk_id: str = "chunk-1",
    line_start: int = 1,
) -> EvidenceSourceSpanV1:
    return EvidenceSourceSpanV1(
        span_id=span_id,
        asset_id=_fingerprint(asset_character),
        chunk_id=chunk_id,
        byte_start=0,
        byte_end=12,
        line_start=line_start,
        line_end=line_start,
        content_fingerprint=_fingerprint("b"),
    )


def _entities() -> tuple[EvidenceEntityV1, ...]:
    return (
        EvidenceEntityV1(
            entity_id="task-1",
            kind=EvidenceEntityKind.TASK,
            canonical_name="Generic task",
            source_span_ids=("span-1",),
        ),
        EvidenceEntityV1(
            entity_id="execution-a",
            kind=EvidenceEntityKind.EXECUTION,
            canonical_name="Execution A",
            source_span_ids=("span-1",),
        ),
        EvidenceEntityV1(
            entity_id="execution-b",
            kind=EvidenceEntityKind.EXECUTION,
            canonical_name="Execution B",
            source_span_ids=("span-1",),
        ),
        EvidenceEntityV1(
            entity_id="reviewer-human",
            kind=EvidenceEntityKind.REVIEWER,
            canonical_name="Human reviewer",
            source_span_ids=("span-1",),
        ),
    )


def _verification(
    *,
    verification_id: str = "verify-comparison",
    verdict: ClaimVerificationVerdict = ClaimVerificationVerdict.ENTAILED,
) -> ClaimVerificationV1:
    return ClaimVerificationV1(
        verification_id=verification_id,
        claim_id="claim-comparison",
        verdict=verdict,
        verification_origin=ClaimVerificationOrigin.SEMANTIC_AGENT,
        verifier_fingerprint=_fingerprint("c"),
        independence_group="semantic-verifier-a",
        source_span_ids=("span-1",),
        rationale_codes=("source_supports_direction",),
    )


def _claim() -> EvidenceClaimV1:
    return EvidenceClaimV1(
        claim_id="claim-comparison",
        kind=EvidenceClaimKind.HUMAN_COMPARISON,
        subject_entity_ids=("reviewer-human",),
        object_entity_ids=("execution-b", "execution-a"),
        payload={
            "relation": "preferred_over",
            "preferred_entity_id": "execution-b",
            "scope": "task-1",
        },
        source_span_ids=("span-1",),
        producer_kind=EvidenceProducerKind.SEMANTIC_AGENT,
        resolution_status=EvidenceResolutionStatus.RESOLVED,
        verification_ids=("verify-comparison",),
        agent_confidence=0.91,
    )


def _graph(
    *,
    span: EvidenceSourceSpanV1 | None = None,
    claims: tuple[EvidenceClaimV1, ...] | None = None,
    verifications: tuple[ClaimVerificationV1, ...] | None = None,
) -> SelfImprovementEvidenceGraphV1:
    return SelfImprovementEvidenceGraphV1(
        spans=(span or _span(),),
        entities=_entities(),
        claims=claims or (_claim(),),
        claim_verifications=verifications or (_verification(),),
        source_dispositions=(
            SemanticSourceDispositionV1(
                source_unit_id="source-unit-1",
                disposition=SemanticSourceDispositionKind.EVIDENCE,
                claim_ids=("claim-comparison",),
                reason_codes=("comparison_evidence",),
                auditor_verification_id="verify-comparison",
            ),
        ),
        profile_fingerprint=_fingerprint("d"),
        extractor_population_fingerprint=_fingerprint("e"),
    )


def test_graph_round_trip_and_fingerprints() -> None:
    graph = _graph()
    restored = SelfImprovementEvidenceGraphV1.from_dict(graph.to_dict())

    assert restored == graph
    assert restored.logical_fingerprint == graph.logical_fingerprint
    assert restored.provenance_fingerprint == graph.provenance_fingerprint


def test_physical_source_layout_changes_only_provenance_fingerprint() -> None:
    original = _graph()
    moved = _graph(
        span=replace(
            _span(),
            asset_id=_fingerprint("f"),
            chunk_id="chunk-other",
            line_start=7,
            line_end=7,
        )
    )

    assert moved.logical_fingerprint == original.logical_fingerprint
    assert moved.provenance_fingerprint != original.provenance_fingerprint


def test_extraction_route_changes_only_provenance_fingerprint() -> None:
    original = _graph()
    deterministic = _graph(
        claims=(
            replace(
                _claim(),
                producer_kind=EvidenceProducerKind.DETERMINISTIC_DECODER,
                agent_confidence=None,
            ),
        )
    )

    assert deterministic.logical_fingerprint == original.logical_fingerprint
    assert (
        deterministic.provenance_fingerprint
        != original.provenance_fingerprint
    )


def test_graph_order_does_not_change_fingerprints() -> None:
    graph = _graph()
    reordered = replace(graph, entities=tuple(reversed(graph.entities)))

    assert reordered.logical_fingerprint == graph.logical_fingerprint
    assert reordered.provenance_fingerprint == graph.provenance_fingerprint


def test_resolved_claim_requires_entailment_and_rejects_contradiction() -> None:
    with pytest.raises(EvidenceContractError, match="entailed"):
        _graph(verifications=(_verification(verdict=ClaimVerificationVerdict.INSUFFICIENT),))

    entailed = _verification()
    contradicted = _verification(
        verification_id="verify-contradicted",
        verdict=ClaimVerificationVerdict.CONTRADICTED,
    )
    claim = replace(
        _claim(),
        verification_ids=("verify-comparison", "verify-contradicted"),
    )
    with pytest.raises(EvidenceContractError, match="contradicted"):
        _graph(claims=(claim,), verifications=(entailed, contradicted))


def test_dangling_and_duplicate_references_fail_closed() -> None:
    dangling = replace(
        _claim(),
        object_entity_ids=("execution-missing", "execution-a"),
        payload={
            "relation": "preferred_over",
            "preferred_entity_id": "execution-missing",
            "scope": "task-1",
        },
    )
    with pytest.raises(EvidenceContractError, match="unknown"):
        _graph(claims=(dangling,))

    graph = _graph()
    with pytest.raises(EvidenceContractError, match="duplicate"):
        replace(graph, spans=(graph.spans[0], graph.spans[0]))


def test_claim_and_disposition_require_citations_and_auditable_reasons() -> None:
    with pytest.raises(EvidenceContractError, match="citation"):
        replace(_claim(), source_span_ids=())
    with pytest.raises(EvidenceContractError, match="auditable reason"):
        SemanticSourceDispositionV1(
            source_unit_id="source-unit-2",
            disposition=SemanticSourceDispositionKind.IRRELEVANT,
            claim_ids=(),
            reason_codes=(),
            auditor_verification_id="verify-comparison",
        )


def test_claim_entity_kinds_are_validated() -> None:
    invalid = replace(
        _claim(),
        object_entity_ids=("task-1", "execution-a"),
        payload={
            "relation": "preferred_over",
            "preferred_entity_id": "execution-a",
            "scope": "task-1",
        },
    )
    with pytest.raises(EvidenceContractError, match="executions or harnesses"):
        _graph(claims=(invalid,))


def test_logical_fingerprint_is_sensitive_to_semantic_direction() -> None:
    graph = _graph()
    reversed_claim = replace(
        _claim(),
        payload={
            "relation": "preferred_over",
            "preferred_entity_id": "execution-a",
            "scope": "task-1",
        },
    )
    reversed_graph = _graph(claims=(reversed_claim,))

    assert reversed_graph.logical_fingerprint != graph.logical_fingerprint


def test_case_validates_typed_graph_references() -> None:
    graph = _graph()
    case = SelfImprovementCaseV1(
        case_id="case-1",
        task_entity_id="task-1",
        input_claim_ids=(),
        execution_entity_ids=("execution-a", "execution-b"),
        trajectory_claim_ids=(),
        result_claim_ids=(),
        comparison_claim_ids=("claim-comparison",),
        conflict_ids=(),
        resolution_status=SelfImprovementCaseResolutionStatus.RESOLVED,
        trainable_signal_projection={"signal_ids": ["signal-1"]},
    )

    case.validate_against(graph)
    assert SelfImprovementCaseV1.from_dict(case.to_dict()) == case
    with pytest.raises(EvidenceContractError, match="unknown"):
        replace(case, comparison_claim_ids=("claim-missing",)).validate_against(
            graph
        )


def test_policy_resolved_conflict_keeps_raw_claims() -> None:
    conflict = EvidenceConflictV1(
        conflict_id="conflict-1",
        kind=EvidenceConflictKind.PREFERENCE_DISAGREEMENT,
        claim_ids=("claim-a", "claim-b"),
        comparison_unit="task-1",
        status=EvidenceConflictStatus.POLICY_RESOLVED,
        resolution_policy_ref="policy-1",
    )

    assert conflict.status is EvidenceConflictStatus.POLICY_RESOLVED
    assert conflict.claim_ids == ("claim-a", "claim-b")


def test_case_cannot_omit_or_hide_an_unresolved_evidence_conflict() -> None:
    graph = _graph()
    second_claim = replace(
        _claim(),
        claim_id="claim-comparison-2",
        payload={
            "relation": "preferred_over",
            "preferred_entity_id": "execution-a",
            "scope": "task-1",
        },
        verification_ids=("verify-comparison-2",),
    )
    second_verification = replace(
        _verification(verification_id="verify-comparison-2"),
        claim_id="claim-comparison-2",
    )
    conflict = EvidenceConflictV1(
        conflict_id="conflict-preference",
        kind=EvidenceConflictKind.PREFERENCE_DISAGREEMENT,
        claim_ids=("claim-comparison", "claim-comparison-2"),
        comparison_unit="task-1",
        status=EvidenceConflictStatus.UNRESOLVED,
    )
    graph = replace(
        graph,
        claims=(*graph.claims, second_claim),
        claim_verifications=(
            *graph.claim_verifications,
            second_verification,
        ),
        source_dispositions=(
            replace(
                graph.source_dispositions[0],
                claim_ids=(
                    "claim-comparison",
                    "claim-comparison-2",
                ),
            ),
        ),
        conflicts=(conflict,),
    )
    case = SelfImprovementCaseV1(
        case_id="case-1",
        task_entity_id="task-1",
        input_claim_ids=(),
        execution_entity_ids=("execution-a", "execution-b"),
        trajectory_claim_ids=(),
        result_claim_ids=(),
        comparison_claim_ids=(
            "claim-comparison",
            "claim-comparison-2",
        ),
        conflict_ids=(),
        resolution_status=SelfImprovementCaseResolutionStatus.RESOLVED,
    )

    with pytest.raises(EvidenceContractError, match="omits a conflict"):
        case.validate_against(graph)
    with pytest.raises(
        EvidenceContractError,
        match="cannot retain unresolved conflicts",
    ):
        replace(
            case,
            conflict_ids=("conflict-preference",),
        ).validate_against(graph)
    replace(
        case,
        conflict_ids=("conflict-preference",),
        resolution_status=SelfImprovementCaseResolutionStatus.AMBIGUOUS,
    ).validate_against(graph)


def test_agent_decode_cannot_self_declare_authoritative_verification() -> None:
    original = _graph()
    authoritative = replace(
        original,
        claim_verifications=(
            replace(
                original.claim_verifications[0],
                verification_origin=(
                    ClaimVerificationOrigin.HUMAN_APPROVED
                ),
            ),
        ),
    )
    payload = authoritative.to_dict()

    with pytest.raises(
        EvidenceContractError,
        match="cannot self-declare",
    ):
        SelfImprovementEvidenceGraphV1.from_agent_dict(payload)

    frozen = SelfImprovementEvidenceGraphV1.from_frozen_dict(
        payload,
        attested_provenance_fingerprint=(
            authoritative.provenance_fingerprint
        ),
        authoritative_verification_ids=("verify-comparison",),
        verification_registry_fingerprint=(
            authoritative_verification_registry_fingerprint(
                authoritative,
                ("verify-comparison",),
            )
        ),
    )
    assert (
        frozen.claim_verifications[0].verification_origin
        is ClaimVerificationOrigin.HUMAN_APPROVED
    )


def test_claim_payload_is_deep_frozen_and_kind_typed() -> None:
    source_payload = {
        "relation": "preferred_over",
        "preferred_entity_id": "execution-b",
        "scope": "task-1",
        "nested": {"value": ["original"]},
    }
    claim = replace(_claim(), payload=source_payload)
    source_payload["nested"]["value"][0] = "mutated"

    assert claim.payload["nested"]["value"] == ("original",)
    with pytest.raises(TypeError):
        claim.payload["scope"] = "other"  # type: ignore[index]
    with pytest.raises(EvidenceContractError, match="bounded scope"):
        replace(
            _claim(),
            payload={
                "relation": "preferred_over",
                "preferred_entity_id": "execution-b",
            },
        )


def test_sequence_fields_reject_strings_instead_of_splitting_characters() -> None:
    payload = _graph().to_dict()
    payload["unresolved_references"] = "not-an-array"

    with pytest.raises(EvidenceContractError, match="array"):
        SelfImprovementEvidenceGraphV1.from_dict(payload)
