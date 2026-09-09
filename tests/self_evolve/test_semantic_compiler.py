from __future__ import annotations

from dataclasses import replace

import pytest

from aworld.self_evolve.evaluation_plan import (
    ManifestOrigin,
    EvaluationDisposition,
    issue_evidence_authority_context,
)
from aworld.self_evolve.evidence import (
    ClaimVerificationOrigin,
    ClaimVerificationV1,
    EvidenceClaimKind,
    EvidenceClaimV1,
    EvidenceProducerKind,
    EvidenceResolutionStatus,
)
from aworld.self_evolve.improvement_signals import (
    DatasetSplit,
    SelfImprovementSignalSetV1,
)
from aworld.self_evolve.ingestion.semantic_compiler import (
    ResolvedSemanticTraceV1,
    compile_semantic_dataset,
)
from aworld.self_evolve.ingestion.types import (
    IngestionContractError,
    NormalizedCaseRecord,
    fingerprint_json,
)
from tests.self_evolve.test_evaluation_plan import _plan
from tests.self_evolve.test_improvement_signals import (
    _graph_and_case,
    _signal,
)


def _with_input_and_traces():
    graph, case = _graph_and_case()
    input_verification = replace(
        graph.claim_verifications[0],
        verification_id="verify-input",
        claim_id="claim-input",
    )
    input_claim = EvidenceClaimV1(
        claim_id="claim-input",
        kind=EvidenceClaimKind.TASK_INPUT,
        subject_entity_ids=("task-1",),
        object_entity_ids=(),
        payload={"input": {"question": "improve recovery"}},
        source_span_ids=("span-1",),
        producer_kind=EvidenceProducerKind.SEMANTIC_AGENT,
        resolution_status=EvidenceResolutionStatus.RESOLVED,
        verification_ids=("verify-input",),
    )
    trace_a = {"steps": [{"id": "a", "action": {"content": "fail"}}]}
    trace_b = {
        "steps": [{"id": "b", "action": {"content": "recover"}}]
    }
    claims = tuple(
        replace(
            claim,
            payload={
                **dict(claim.payload),
                "trace_fingerprint": fingerprint_json(
                    trace_a
                    if claim.claim_id == "claim-traj-a"
                    else trace_b
                ),
            },
        )
        if claim.kind is EvidenceClaimKind.EXECUTION_TRAJECTORY
        else claim
        for claim in graph.claims
    )
    graph = replace(
        graph,
        claims=(*claims, input_claim),
        claim_verifications=(
            *graph.claim_verifications,
            input_verification,
        ),
        source_dispositions=(
            replace(
                graph.source_dispositions[0],
                claim_ids=(
                    *graph.source_dispositions[0].claim_ids,
                    "claim-input",
                ),
            ),
        ),
    )
    case = replace(case, input_claim_ids=("claim-input",))
    case.validate_against(graph)
    signal_set = SelfImprovementSignalSetV1(
        signals=(_signal(),),
        case_splits={"case-1": DatasetSplit.TRAIN},
        synthesis_report_refs=("synthesis-1",),
        critic_report_refs=("critic-1",),
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    plan = _plan(graph.profile_fingerprint)
    traces = {
        "trace:claim-traj-a": ResolvedSemanticTraceV1(
            trace_ref="trace:claim-traj-a",
            trace_fingerprint=fingerprint_json(trace_a),
            trajectory=trace_a,
        ),
        "trace:claim-traj-b": ResolvedSemanticTraceV1(
            trace_ref="trace:claim-traj-b",
            trace_fingerprint=fingerprint_json(trace_b),
            trajectory=trace_b,
        ),
    }
    return graph, case, signal_set, plan, traces


def _authoritative_graph_and_context(graph):
    verifications = tuple(
        replace(
            item,
            verification_origin=(
                ClaimVerificationOrigin.DETERMINISTIC_DECODER
            ),
        )
        for item in graph.claim_verifications
    )
    authoritative = replace(
        graph,
        claim_verifications=verifications,
    )
    context = issue_evidence_authority_context(
        authoritative,
        deterministic_verification_ids=tuple(
            item.verification_id for item in verifications
        ),
    )
    return authoritative, context


def test_compiler_emits_legacy_cases_signals_and_all_target_traces() -> None:
    graph, case, signal_set, plan, traces = _with_input_and_traces()
    graph, authority_context = _authoritative_graph_and_context(graph)
    signal_set = replace(
        signal_set,
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )

    compiled = compile_semantic_dataset(
        graph=graph,
        cases=(case,),
        signal_set=signal_set,
        evaluation_plans=(plan,),
        resolved_traces=traces,
        ingestion_id="semantic-ingestion-1",
        authority_context=authority_context,
        manifest_origin=ManifestOrigin.ABSENT,
        manifest_fingerprint="sha256:" + "0" * 64,
    )

    assert len(compiled.normalized_cases) == 1
    normalized = compiled.normalized_cases[0]
    assert normalized.input == {"question": "improve recovery"}
    assert normalized.expected_output == {
        "claim_id": "claim-result-b"
    }
    assert normalized.trace_replayability == "replayable"
    assert normalized.self_improvement_signals[0]["signal_id"] == (
        "signal-1"
    )
    assert len(compiled.target_evidence_bundle.executions) == 2
    assert {
        item.execution_entity_id
        for item in compiled.target_evidence_bundle.executions
    } == {"execution-a", "execution-b"}
    restored = NormalizedCaseRecord.from_dict(normalized.to_dict())
    assert restored == normalized
    assert restored.source.mapping_fingerprint is None
    assert restored.source.normalization_fingerprint == (
        compiled.normalization_fingerprint
    )


def test_proposal_plan_does_not_compile_historical_expected_output() -> None:
    graph, case, signal_set, plan, traces = _with_input_and_traces()
    plan = replace(
        plan,
        expected_output_claim_id=None,
        disposition=EvaluationDisposition.PROPOSAL_ONLY,
        reason_codes=("semantic_evidence_not_authoritative",),
    )

    compiled = compile_semantic_dataset(
        graph=graph,
        cases=(case,),
        signal_set=signal_set,
        evaluation_plans=(plan,),
        resolved_traces=traces,
    )

    assert compiled.normalized_cases[0].expected_output is None


def test_verified_compilation_excludes_proposal_only_optimizer_signals() -> None:
    graph, case, signal_set, plan, traces = _with_input_and_traces()
    proposal = replace(
        plan,
        expected_output_claim_id=None,
        disposition=EvaluationDisposition.PROPOSAL_ONLY,
        reason_codes=("semantic_evidence_not_authoritative",),
    )

    compiled = compile_semantic_dataset(
        graph=graph,
        cases=(case,),
        signal_set=signal_set,
        evaluation_plans=(proposal,),
        resolved_traces=traces,
        verified_only_signal_projection=True,
    )

    assert compiled.normalized_cases[0].self_improvement_signals == ()


def test_held_out_case_exposes_no_optimizer_signals() -> None:
    graph, case, signal_set, plan, traces = _with_input_and_traces()
    held_out = replace(
        signal_set,
        case_splits={"case-1": DatasetSplit.HELD_OUT},
    )
    plan = replace(
        plan,
        training_signal_ids=(),
        supporting_evidence_claim_ids=(),
        expected_output_claim_id=None,
        disposition=EvaluationDisposition.PROPOSAL_ONLY,
        reason_codes=("held_out_case",),
    )

    compiled = compile_semantic_dataset(
        graph=graph,
        cases=(case,),
        signal_set=held_out,
        evaluation_plans=(plan,),
        resolved_traces=traces,
    )

    assert compiled.normalized_cases[0].self_improvement_signals == ()


def test_logical_dataset_fingerprint_excludes_physical_layout() -> None:
    graph, case, signal_set, plan, traces = _with_input_and_traces()
    plan = replace(
        plan,
        expected_output_claim_id=None,
        disposition=EvaluationDisposition.PROPOSAL_ONLY,
        reason_codes=("semantic_evidence_not_authoritative",),
    )
    first = compile_semantic_dataset(
        graph=graph,
        cases=(case,),
        signal_set=signal_set,
        evaluation_plans=(plan,),
        resolved_traces=traces,
    )
    moved_graph = replace(
        graph,
        spans=(
            replace(
                graph.spans[0],
                asset_id="sha256:" + "f" * 64,
                chunk_id="chunk-moved",
                line_start=20,
                line_end=24,
            ),
        ),
    )
    moved_signal_set = replace(
        signal_set,
        evidence_graph_logical_fingerprint=(
            moved_graph.logical_fingerprint
        ),
    )
    moved = compile_semantic_dataset(
        graph=moved_graph,
        cases=(case,),
        signal_set=moved_signal_set,
        evaluation_plans=(plan,),
        resolved_traces=traces,
    )

    assert graph.logical_fingerprint == moved_graph.logical_fingerprint
    assert first.normalized_dataset_fingerprint == (
        moved.normalized_dataset_fingerprint
    )
    assert first.normalized_cases[0].source.asset_ids != (
        moved.normalized_cases[0].source.asset_ids
    )


def test_replay_seed_requires_frozen_trace_resolution() -> None:
    graph, case, signal_set, plan, _ = _with_input_and_traces()
    plan = replace(
        plan,
        expected_output_claim_id=None,
        disposition=EvaluationDisposition.PROPOSAL_ONLY,
        reason_codes=("semantic_evidence_not_authoritative",),
    )

    with pytest.raises(
        IngestionContractError,
        match="exact frozen resolution",
    ):
        compile_semantic_dataset(
            graph=graph,
            cases=(case,),
            signal_set=signal_set,
            evaluation_plans=(plan,),
        )


def test_semantic_agent_plan_cannot_authorize_expected_output() -> None:
    graph, case, signal_set, plan, traces = _with_input_and_traces()

    with pytest.raises(
        IngestionContractError,
        match="framework authority context",
    ):
        compile_semantic_dataset(
            graph=graph,
            cases=(case,),
            signal_set=signal_set,
            evaluation_plans=(plan,),
            resolved_traces=traces,
        )
