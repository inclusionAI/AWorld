from __future__ import annotations

from dataclasses import replace

from aworld.self_evolve.evaluation_plan import SemanticIngestionProfileV1
from aworld.self_evolve.evidence import (
    ClaimVerificationV1,
    EvidenceClaimKind,
    EvidenceConflictKind,
    EvidenceEntityKind,
    EvidenceEntityV1,
    EvidenceResolutionStatus,
)
from aworld.self_evolve.ingestion.semantic_resolver import (
    canonicalize_evidence_graph,
    resolve_evidence_graph_deterministically,
    semantic_candidate_consensus,
)
from tests.self_evolve.test_semantic_evidence import (
    _claim,
    _graph,
    _verification,
)


def _profile() -> SemanticIngestionProfileV1:
    return SemanticIngestionProfileV1(
        profile_id="resolver-test-v1",
        entity_aliases={
            "execution": {
                "execution-a": ("Execution A", "A"),
                "execution-b": ("Execution B", "B"),
            }
        },
    )


def _remap_local_ids(graph):
    entity_map = {
        item.entity_id: f"local-{item.entity_id}"
        for item in graph.entities
    }
    claim_map = {
        item.claim_id: f"local-{item.claim_id}"
        for item in graph.claims
    }
    verification_map = {
        item.verification_id: f"local-{item.verification_id}"
        for item in graph.claim_verifications
    }
    entities = tuple(
        replace(item, entity_id=entity_map[item.entity_id])
        for item in graph.entities
    )
    claims = tuple(
        replace(
            item,
            claim_id=claim_map[item.claim_id],
            subject_entity_ids=tuple(
                entity_map[value]
                for value in item.subject_entity_ids
            ),
            object_entity_ids=tuple(
                entity_map[value]
                for value in item.object_entity_ids
            ),
            payload={
                key: (
                    entity_map.get(value, value)
                    if isinstance(value, str)
                    else value
                )
                for key, value in item.payload.items()
            },
            verification_ids=tuple(
                verification_map[value]
                for value in item.verification_ids
            ),
        )
        for item in graph.claims
    )
    verifications = tuple(
        replace(
            item,
            verification_id=verification_map[item.verification_id],
            claim_id=claim_map[item.claim_id],
        )
        for item in graph.claim_verifications
    )
    dispositions = tuple(
        replace(
            item,
            claim_ids=tuple(claim_map[value] for value in item.claim_ids),
        )
        for item in graph.source_dispositions
    )
    return replace(
        graph,
        entities=entities,
        claims=claims,
        claim_verifications=verifications,
        source_dispositions=dispositions,
    )


def test_canonical_resolution_excludes_agent_local_ids() -> None:
    graph = _graph()
    local = _remap_local_ids(graph)

    first = canonicalize_evidence_graph(graph, profile=_profile())
    second = canonicalize_evidence_graph(local, profile=_profile())

    assert first.logical_fingerprint == second.logical_fingerprint
    assert {
        item.entity_id for item in first.entities
    } == {
        item.entity_id for item in second.entities
    }
    assert {
        item.claim_id for item in first.claims
    } == {
        item.claim_id for item in second.claims
    }


def test_profile_aliases_make_equivalent_names_canonical() -> None:
    graph = _graph()
    alias_graph = replace(
        graph,
        entities=tuple(
            replace(item, canonical_name="A")
            if item.entity_id == "execution-a"
            else item
            for item in graph.entities
        ),
    )

    assert canonicalize_evidence_graph(
        graph,
        profile=_profile(),
    ).logical_fingerprint == canonicalize_evidence_graph(
        alias_graph,
        profile=_profile(),
    ).logical_fingerprint


def test_resolver_preserves_preference_disagreement_as_conflict() -> None:
    graph = _graph()
    judge_verification = replace(
        _verification(verification_id="verify-judge"),
        claim_id="claim-judge",
    )
    judge_claim = replace(
        _claim(),
        claim_id="claim-judge",
        kind=EvidenceClaimKind.LLM_JUDGE_ASSESSMENT,
        payload={
            "rubric_id": "judge-rubric-v1",
            "scope": "task-1",
            "preferred_entity_id": "execution-a",
        },
        verification_ids=("verify-judge",),
        resolution_status=EvidenceResolutionStatus.RESOLVED,
    )
    graph = replace(
        graph,
        claims=(*graph.claims, judge_claim),
        claim_verifications=(
            *graph.claim_verifications,
            judge_verification,
        ),
        source_dispositions=(
            replace(
                graph.source_dispositions[0],
                claim_ids=("claim-comparison", "claim-judge"),
            ),
        ),
    )

    resolved, deterministic = resolve_evidence_graph_deterministically(
        graph,
        profile=_profile(),
    )

    assert deterministic is True
    assert any(
        item.kind is EvidenceConflictKind.PREFERENCE_DISAGREEMENT
        for item in resolved.conflicts
    )


def test_candidate_consensus_is_logical_claim_jaccard() -> None:
    graph = canonicalize_evidence_graph(_graph(), profile=_profile())
    assert semantic_candidate_consensus((graph, graph)) == 1.0

    without_claim = replace(
        graph,
        claims=(),
        claim_verifications=(),
        source_dispositions=(
            replace(
                graph.source_dispositions[0],
                disposition="unresolved",
                claim_ids=(),
                reason_codes=("claim_missing",),
            ),
        ),
    )
    assert semantic_candidate_consensus((graph, without_claim)) == 0.0


def test_same_execution_names_remain_distinct_across_task_contexts() -> None:
    graph = _graph()
    second_claim = replace(
        _claim(),
        claim_id="claim-context-2",
        subject_entity_ids=("reviewer-2",),
        object_entity_ids=("execution-a-2", "execution-b-2"),
        payload={
            "relation": "preferred_over",
            "preferred_entity_id": "execution-b-2",
            "scope": "task-2",
        },
        verification_ids=("verify-context-2",),
    )
    second_verification = replace(
        _verification(verification_id="verify-context-2"),
        claim_id="claim-context-2",
    )
    graph = replace(
        graph,
        entities=(
            *graph.entities,
            EvidenceEntityV1(
                entity_id="task-2",
                kind=EvidenceEntityKind.TASK,
                canonical_name="Other task",
                source_span_ids=("span-1",),
            ),
            EvidenceEntityV1(
                entity_id="execution-a-2",
                kind=EvidenceEntityKind.EXECUTION,
                canonical_name="Execution A",
                source_span_ids=("span-1",),
            ),
            EvidenceEntityV1(
                entity_id="execution-b-2",
                kind=EvidenceEntityKind.EXECUTION,
                canonical_name="Execution B",
                source_span_ids=("span-1",),
            ),
            EvidenceEntityV1(
                entity_id="reviewer-2",
                kind=EvidenceEntityKind.REVIEWER,
                canonical_name="Human reviewer",
                source_span_ids=("span-1",),
            ),
        ),
        claims=(*graph.claims, second_claim),
        claim_verifications=(
            *graph.claim_verifications,
            second_verification,
        ),
    )

    resolved = canonicalize_evidence_graph(graph, profile=_profile())

    assert sum(
        item.kind is EvidenceEntityKind.EXECUTION
        for item in resolved.entities
    ) == 4
    assert sum(
        item.kind is EvidenceEntityKind.REVIEWER
        for item in resolved.entities
    ) == 2


def test_resolver_detects_score_and_trajectory_collisions() -> None:
    metric_a = replace(
        _claim(),
        kind=EvidenceClaimKind.METRIC_OBSERVATION,
        subject_entity_ids=("execution-a",),
        object_entity_ids=(),
        payload={
            "metric_name": "task_success",
            "scope": "task-1",
            "value": 0.0,
        },
    )
    metric_b = replace(
        metric_a,
        claim_id="claim-metric-b",
        payload={
            "metric_name": "task_success",
            "scope": "task-1",
            "value": 1.0,
        },
        verification_ids=("verify-metric-b",),
    )
    metric_verification = replace(
        _verification(verification_id="verify-metric-b"),
        claim_id="claim-metric-b",
    )
    metric_graph = _graph(
        claims=(metric_a, metric_b),
        verifications=(_verification(), metric_verification),
    )
    resolved_metric = canonicalize_evidence_graph(
        metric_graph,
        profile=_profile(),
    )

    trace_a = replace(
        _claim(),
        kind=EvidenceClaimKind.EXECUTION_TRAJECTORY,
        subject_entity_ids=("execution-a",),
        object_entity_ids=(),
        payload={
            "trace_ref": "trace-a",
            "trace_fingerprint": "sha256:" + "1" * 64,
        },
    )
    trace_b = replace(
        trace_a,
        claim_id="claim-trace-b",
        payload={
            "trace_ref": "trace-b",
            "trace_fingerprint": "sha256:" + "2" * 64,
        },
        verification_ids=("verify-trace-b",),
    )
    trace_verification = replace(
        _verification(verification_id="verify-trace-b"),
        claim_id="claim-trace-b",
    )
    trace_graph = _graph(
        claims=(trace_a, trace_b),
        verifications=(_verification(), trace_verification),
    )
    resolved_trace = canonicalize_evidence_graph(
        trace_graph,
        profile=_profile(),
    )

    assert any(
        item.kind is EvidenceConflictKind.SCORE_INCOMPATIBLE
        for item in resolved_metric.conflicts
    )
    assert any(
        item.kind
        is EvidenceConflictKind.TRAJECTORY_IDENTITY_COLLISION
        for item in resolved_trace.conflicts
    )


def test_resolver_marks_merged_entity_attribute_ambiguity() -> None:
    graph = _graph()
    duplicate = EvidenceEntityV1(
        entity_id="execution-a-duplicate",
        kind=EvidenceEntityKind.EXECUTION,
        canonical_name="Execution A",
        source_span_ids=("span-1",),
        attributes={"runtime": "other"},
    )
    original_entities = tuple(
        replace(item, attributes={"runtime": "first"})
        if item.entity_id == "execution-a"
        else item
        for item in graph.entities
    )
    second_claim = replace(
        _claim(),
        claim_id="claim-duplicate-entity",
        object_entity_ids=("execution-a-duplicate", "execution-b"),
        payload={
            "relation": "preferred_over",
            "preferred_entity_id": "execution-a-duplicate",
            "scope": "task-1",
        },
        verification_ids=("verify-duplicate-entity",),
    )
    second_verification = replace(
        _verification(
            verification_id="verify-duplicate-entity",
        ),
        claim_id="claim-duplicate-entity",
    )
    graph = replace(
        graph,
        entities=(*original_entities, duplicate),
        claims=(*graph.claims, second_claim),
        claim_verifications=(
            *graph.claim_verifications,
            second_verification,
        ),
    )

    resolved = canonicalize_evidence_graph(graph, profile=_profile())

    assert any(
        item.kind is EvidenceConflictKind.ENTITY_AMBIGUITY
        for item in resolved.conflicts
    )
