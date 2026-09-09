from __future__ import annotations

from dataclasses import replace

import pytest

from aworld.self_evolve.evidence import (
    ClaimVerificationOrigin,
    ClaimVerificationV1,
    ClaimVerificationVerdict,
    EvidenceClaimKind,
    EvidenceClaimV1,
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
)
from aworld.self_evolve.improvement_signals import (
    BehaviorDeltaV1,
    DatasetSplit,
    ImprovementSignalContractError,
    SelfImprovementSignalKind,
    SelfImprovementSignalSetV1,
    SelfImprovementSignalV1,
    SignalActionability,
    SignalVerificationStatus,
    TargetEvidenceBundleV1,
    TargetExecutionEvidenceV1,
)


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def _claim(
    claim_id: str,
    kind: EvidenceClaimKind,
    subject: str,
    *,
    objects: tuple[str, ...] = (),
) -> EvidenceClaimV1:
    if kind is EvidenceClaimKind.EXECUTION_TRAJECTORY:
        payload = {
            "trace_ref": f"trace:{claim_id}",
            "trace_fingerprint": (
                _fingerprint("1")
                if claim_id.endswith("-a")
                else _fingerprint("2")
            ),
        }
    elif kind is EvidenceClaimKind.EXECUTION_RESULT:
        payload = {"result": {"claim_id": claim_id}}
    elif kind is EvidenceClaimKind.HUMAN_COMPARISON:
        payload = {
            "relation": "preferred_over",
            "preferred_entity_id": "execution-b",
            "scope": "task-1",
        }
    else:
        payload = {"relation": "observed"}
    return EvidenceClaimV1(
        claim_id=claim_id,
        kind=kind,
        subject_entity_ids=(subject,),
        object_entity_ids=objects,
        payload=payload,
        source_span_ids=("span-1",),
        producer_kind=EvidenceProducerKind.SEMANTIC_AGENT,
        resolution_status=EvidenceResolutionStatus.RESOLVED,
        verification_ids=(f"verify-{claim_id}",),
        agent_confidence=0.9,
    )


def _graph_and_case() -> tuple[
    SelfImprovementEvidenceGraphV1,
    SelfImprovementCaseV1,
]:
    claims = (
        _claim(
            "claim-traj-a",
            EvidenceClaimKind.EXECUTION_TRAJECTORY,
            "execution-a",
        ),
        _claim(
            "claim-traj-b",
            EvidenceClaimKind.EXECUTION_TRAJECTORY,
            "execution-b",
        ),
        _claim(
            "claim-result-a",
            EvidenceClaimKind.EXECUTION_RESULT,
            "execution-a",
        ),
        _claim(
            "claim-result-b",
            EvidenceClaimKind.EXECUTION_RESULT,
            "execution-b",
        ),
        _claim(
            "claim-preference",
            EvidenceClaimKind.HUMAN_COMPARISON,
            "reviewer-human",
            objects=("execution-a", "execution-b"),
        ),
    )
    verifications = tuple(
        ClaimVerificationV1(
            verification_id=f"verify-{claim.claim_id}",
            claim_id=claim.claim_id,
            verdict=ClaimVerificationVerdict.ENTAILED,
            verification_origin=ClaimVerificationOrigin.SEMANTIC_AGENT,
            verifier_fingerprint=_fingerprint("c"),
            independence_group="verifier-a",
            source_span_ids=("span-1",),
        )
        for claim in claims
    )
    graph = SelfImprovementEvidenceGraphV1(
        spans=(
            EvidenceSourceSpanV1(
                span_id="span-1",
                asset_id=_fingerprint("a"),
                chunk_id="chunk-1",
                byte_start=0,
                byte_end=100,
                line_start=1,
                line_end=5,
                content_fingerprint=_fingerprint("b"),
            ),
        ),
        entities=(
            EvidenceEntityV1(
                entity_id="task-1",
                kind=EvidenceEntityKind.TASK,
                canonical_name="Task",
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
                canonical_name="Reviewer",
                source_span_ids=("span-1",),
            ),
        ),
        claims=claims,
        claim_verifications=verifications,
        source_dispositions=(
            SemanticSourceDispositionV1(
                source_unit_id="unit-1",
                disposition=SemanticSourceDispositionKind.EVIDENCE,
                claim_ids=tuple(claim.claim_id for claim in claims),
                reason_codes=("self_improvement_evidence",),
                auditor_verification_id="coverage-report-1",
            ),
        ),
        profile_fingerprint=_fingerprint("d"),
        extractor_population_fingerprint=_fingerprint("e"),
    )
    case = SelfImprovementCaseV1(
        case_id="case-1",
        task_entity_id="task-1",
        input_claim_ids=(),
        execution_entity_ids=("execution-a", "execution-b"),
        trajectory_claim_ids=("claim-traj-a", "claim-traj-b"),
        result_claim_ids=("claim-result-a", "claim-result-b"),
        comparison_claim_ids=("claim-preference",),
        conflict_ids=(),
        resolution_status=SelfImprovementCaseResolutionStatus.RESOLVED,
    )
    case.validate_against(graph)
    return graph, case


def _signal(
    *,
    signal_id: str = "signal-1",
    case_id: str = "case-1",
    actionability: SignalActionability = SignalActionability.ACTIONABLE,
) -> SelfImprovementSignalV1:
    return SelfImprovementSignalV1(
        signal_id=signal_id,
        case_id=case_id,
        kind=SelfImprovementSignalKind.PREFERENCE_DELTA,
        compared_execution_ids=("execution-a", "execution-b"),
        preferred_execution_ids=("execution-b",),
        supporting_claim_ids=(
            "claim-traj-a",
            "claim-traj-b",
            "claim-result-a",
            "claim-result-b",
            "claim-preference",
        ),
        opposing_claim_ids=(),
        behavior_delta=BehaviorDeltaV1(
            preferred_observations=("B recovered after the tool error",),
            non_preferred_observations=("A repeated the failed tool call",),
            result_difference=("B completed the task; A did not",),
            source_claim_ids=(
                "claim-traj-a",
                "claim-traj-b",
                "claim-result-a",
                "claim-result-b",
            ),
        ),
        metric_delta={"task_success": 1.0},
        desired_behavior=("Diagnose a failed tool call before retrying",),
        avoid_behavior=("Do not repeat an identical failed call",),
        capability_requirement=("Recover from tool execution failures",),
        verification_status=SignalVerificationStatus.VERIFIED,
        actionability=actionability,
        reason_codes=("verified_behavior_contrast",),
    )


def test_actionable_signal_round_trip_and_evidence_validation() -> None:
    graph, case = _graph_and_case()
    signal = _signal()

    signal.validate_against(graph, case)
    restored = SelfImprovementSignalV1.from_dict(signal.to_dict())

    assert restored == signal
    assert restored.fingerprint == signal.fingerprint


def test_actionable_signal_requires_verified_contrast_and_guidance() -> None:
    with pytest.raises(
        ImprovementSignalContractError,
        match="must be verified",
    ):
        replace(
            _signal(),
            verification_status=SignalVerificationStatus.ADVISORY,
        )
    with pytest.raises(
        ImprovementSignalContractError,
        match="behavior contrast",
    ):
        replace(
            _signal(),
            behavior_delta=BehaviorDeltaV1(
                preferred_observations=(),
                non_preferred_observations=(),
                result_difference=(),
                source_claim_ids=(),
            ),
        )


def test_signal_set_is_order_invariant_and_rate_is_trainable_only() -> None:
    first = _signal(signal_id="signal-a")
    held_out = _signal(
        signal_id="signal-b",
        case_id="case-held-out",
        actionability=SignalActionability.ADVISORY,
    )
    original = SelfImprovementSignalSetV1(
        signals=(first, held_out),
        case_splits={
            "case-1": DatasetSplit.TRAIN,
            "case-held-out": DatasetSplit.HELD_OUT,
        },
        synthesis_report_refs=("synthesis-1",),
        critic_report_refs=("critic-1",),
        evidence_graph_logical_fingerprint=_fingerprint("f"),
    )
    reordered = replace(original, signals=tuple(reversed(original.signals)))

    assert original.fingerprint == reordered.fingerprint
    assert original.signal_actionability_rate == 1.0
    assert (
        SelfImprovementSignalSetV1.from_dict(original.to_dict()).fingerprint
        == original.fingerprint
    )


def test_optimizer_projection_never_exposes_held_out_signals() -> None:
    signal_set = SelfImprovementSignalSetV1(
        signals=(
            _signal(signal_id="signal-train"),
            _signal(signal_id="signal-held", case_id="case-held"),
        ),
        case_splits={
            "case-1": DatasetSplit.TRAIN,
            "case-held": DatasetSplit.HELD_OUT,
        },
        synthesis_report_refs=("synthesis-1",),
        critic_report_refs=("critic-1",),
        evidence_graph_logical_fingerprint=_fingerprint("f"),
    )

    projection = signal_set.optimizer_projection()
    assert [item["signal_id"] for item in projection] == ["signal-train"]
    with pytest.raises(
        ImprovementSignalContractError,
        match="held-out",
    ):
        signal_set.optimizer_projection(
            allowed_splits=(DatasetSplit.HELD_OUT,)
        )


def test_signal_validation_rejects_cross_case_execution() -> None:
    graph, case = _graph_and_case()
    invalid = replace(
        _signal(),
        compared_execution_ids=("execution-a", "execution-missing"),
        preferred_execution_ids=("execution-a",),
    )

    with pytest.raises(ImprovementSignalContractError, match="unknown"):
        invalid.validate_against(graph, case)


def _target_bundle(
    graph: SelfImprovementEvidenceGraphV1,
) -> TargetEvidenceBundleV1:
    return TargetEvidenceBundleV1(
        executions=(
            TargetExecutionEvidenceV1(
                case_id="case-1",
                task_entity_id="task-1",
                execution_entity_id="execution-a",
                trajectory_claim_id="claim-traj-a",
                result_claim_ids=("claim-result-a",),
                trace_ref="trace:claim-traj-a",
                trace_fingerprint=_fingerprint("1"),
            ),
            TargetExecutionEvidenceV1(
                case_id="case-1",
                task_entity_id="task-1",
                execution_entity_id="execution-b",
                trajectory_claim_id="claim-traj-b",
                result_claim_ids=("claim-result-b",),
                trace_ref="trace:claim-traj-b",
                trace_fingerprint=_fingerprint("2"),
            ),
        ),
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )


def test_target_bundle_keeps_all_traces_without_preference_weighting() -> None:
    graph, case = _graph_and_case()
    bundle = _target_bundle(graph)

    bundle.validate_against(graph, (case,))
    projection = bundle.trace_projection()

    assert {item["execution_entity_id"] for item in projection} == {
        "execution-a",
        "execution-b",
    }
    assert all("weight" not in item and "preferred" not in item for item in projection)


def test_target_bundle_round_trip_and_graph_binding() -> None:
    graph, case = _graph_and_case()
    bundle = _target_bundle(graph)

    restored = TargetEvidenceBundleV1.from_dict(bundle.to_dict())
    restored.validate_against(graph, (case,))
    assert restored.fingerprint == bundle.fingerprint

    with pytest.raises(
        ImprovementSignalContractError,
        match="different evidence graph",
    ):
        replace(
            bundle,
            evidence_graph_logical_fingerprint=_fingerprint("9"),
        ).validate_against(graph, (case,))


def test_target_bundle_cannot_implicitly_weight_by_omitting_a_trace() -> None:
    graph, case = _graph_and_case()
    bundle = _target_bundle(graph)

    with pytest.raises(
        ImprovementSignalContractError,
        match="every eligible trajectory",
    ):
        replace(
            bundle,
            executions=(bundle.executions[1],),
        ).validate_against(graph, (case,))
