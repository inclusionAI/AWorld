from __future__ import annotations

from dataclasses import replace

import pytest

from aworld.self_evolve.evaluation_plan import (
    AggregationPolicy,
    ComparisonUnit,
    ConflictPolicy,
    EvidenceAuthorityContextV1,
    EvaluationDisposition,
    EvaluationPlanContractError,
    HistoricalJudgeAuthority,
    HumanClaimAuthority,
    HumanEvidenceApprovalV1,
    JudgeRubricPolicy,
    ManifestOrigin,
    QualificationStatus,
    SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1,
    SelfImprovementEvaluationPlanV1,
    SemanticIngestionProfileV1,
    SemanticModelQualificationReportV1,
    SemanticQualificationMethod,
    SemanticQualificationRegistryV1,
    compile_evaluation_plan,
    default_semantic_ingestion_profile,
    effective_profile_for_origin,
    issue_evidence_authority_context,
    issue_human_evidence_approval,
)
from aworld.self_evolve.improvement_signals import (
    DatasetSplit,
    SelfImprovementSignalSetV1,
)
from tests.self_evolve.test_improvement_signals import (
    _graph_and_case,
    _signal,
)


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def _production_qualification_fields() -> dict[str, object]:
    return {
        "qualification_method": (
            SemanticQualificationMethod.EXACT_SNAPSHOT_V1
        ),
        "runner_protocol_fingerprint": (
            SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1
        ),
        "case_attestation_bundle_fingerprint": _fingerprint("a"),
    }


def _elevated_profile(
    *,
    approved_graph: str | None = None,
) -> SemanticIngestionProfileV1:
    return SemanticIngestionProfileV1(
        profile_id="domain-profile-v1",
        entity_aliases={
            "harness": {
                "harness-a": ("Harness A", "A"),
                "harness-b": ("Harness B", "B"),
            }
        },
        comparison_unit=ComparisonUnit.HARNESS,
        human_claim_authority=HumanClaimAuthority.GROUND_TRUTH,
        historical_judge_authority=(
            HistoricalJudgeAuthority.SCORED_SIGNAL
        ),
        judge_rubric_policy=JudgeRubricPolicy.COMPATIBLE_ONLY,
        aggregation_policy=AggregationPolicy.MAJORITY,
        conflict_policy=ConflictPolicy.PROPOSAL_ONLY,
        approved_evidence_graph_fingerprint=approved_graph,
    )


def _plan(
    profile_fingerprint: str,
) -> SelfImprovementEvaluationPlanV1:
    return SelfImprovementEvaluationPlanV1(
        plan_id="plan-1",
        case_id="case-1",
        comparison_unit=ComparisonUnit.HARNESS,
        training_signal_ids=("signal-1",),
        supporting_evidence_claim_ids=(
            "claim-traj-a",
            "claim-traj-b",
            "claim-result-a",
            "claim-result-b",
            "claim-preference",
        ),
        replay_seed_execution_id="execution-a",
        expected_output_claim_id="claim-result-b",
        human_claim_authority=HumanClaimAuthority.GROUND_TRUTH,
        historical_judge_authority=(
            HistoricalJudgeAuthority.SCORED_SIGNAL
        ),
        rubric_groups={"rubric-group-1": ("rubric-1", "rubric-2")},
        aggregation_policy=AggregationPolicy.MAJORITY,
        conflict_policy=ConflictPolicy.PROPOSAL_ONLY,
        current_evaluator_required=True,
        disposition=(
            EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
        ),
        reason_codes=(),
        profile_fingerprint=profile_fingerprint,
    )


def test_framework_default_profile_is_conservative() -> None:
    profile = default_semantic_ingestion_profile()

    assert profile.human_claim_authority is HumanClaimAuthority.SOFT_LABEL
    assert (
        profile.historical_judge_authority
        is HistoricalJudgeAuthority.ADVISORY
    )
    assert profile.judge_rubric_policy is JudgeRubricPolicy.SEPARATE
    assert profile.aggregation_policy is AggregationPolicy.NONE
    assert profile.conflict_policy is ConflictPolicy.REQUIRE_REVIEW


def test_profile_round_trip_is_canonical() -> None:
    profile = _elevated_profile()
    restored = SemanticIngestionProfileV1.from_dict(profile.to_dict())

    assert restored == profile
    assert restored.fingerprint == profile.fingerprint


def test_profile_fingerprint_excludes_graph_approval_directive() -> None:
    without_approval = _elevated_profile()
    with_approval = _elevated_profile(
        approved_graph=_fingerprint("a")
    )

    assert with_approval.fingerprint == without_approval.fingerprint


def test_untrusted_manifest_cannot_raise_authority_or_policy() -> None:
    effective = effective_profile_for_origin(
        _elevated_profile(),
        manifest_origin=ManifestOrigin.CONVENTIONAL_UNTRUSTED,
    )

    assert effective.human_claim_authority is HumanClaimAuthority.SOFT_LABEL
    assert (
        effective.historical_judge_authority
        is HistoricalJudgeAuthority.ADVISORY
    )
    assert effective.judge_rubric_policy is JudgeRubricPolicy.SEPARATE
    assert effective.aggregation_policy is AggregationPolicy.NONE
    assert effective.conflict_policy is ConflictPolicy.REQUIRE_REVIEW


def test_human_approval_is_explicit_and_content_addressed() -> None:
    graph_fingerprint = _fingerprint("a")
    manifest_fingerprint = _fingerprint("b")
    profile = _elevated_profile(approved_graph=graph_fingerprint)

    with pytest.raises(
        EvaluationPlanContractError,
        match="explicit source manifest",
    ):
        issue_human_evidence_approval(
            profile=profile,
            graph_fingerprint=graph_fingerprint,
            manifest_fingerprint=manifest_fingerprint,
            manifest_origin=ManifestOrigin.CONVENTIONAL_UNTRUSTED,
        )

    approval = issue_human_evidence_approval(
        profile=profile,
        graph_fingerprint=graph_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
    )

    assert approval.matches(
        graph_fingerprint=graph_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
    )
    assert not approval.matches(
        graph_fingerprint=_fingerprint("c"),
        manifest_fingerprint=manifest_fingerprint,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
    )


def test_operator_ground_truth_requires_matching_approval() -> None:
    graph_fingerprint = _fingerprint("a")
    manifest_fingerprint = _fingerprint("b")
    graph_provenance_fingerprint = _fingerprint("c")
    source_bundle_fingerprint = _fingerprint("d")
    constitution_fingerprint = _fingerprint("e")
    profile = _elevated_profile(approved_graph=graph_fingerprint)
    without_approval = effective_profile_for_origin(
        profile,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        graph_fingerprint=graph_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
    )
    approval = issue_human_evidence_approval(
        profile=profile,
        graph_fingerprint=graph_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
    )
    weakly_approved = effective_profile_for_origin(
        profile,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        approval=approval,
        graph_fingerprint=graph_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
    )
    strong_approval = issue_human_evidence_approval(
        profile=profile,
        graph_fingerprint=graph_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        graph_provenance_fingerprint=(
            graph_provenance_fingerprint
        ),
        source_bundle_fingerprint=source_bundle_fingerprint,
        constitution_fingerprint=constitution_fingerprint,
        semantic_profile_fingerprint=profile.fingerprint,
    )
    approved = effective_profile_for_origin(
        profile,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        approval=strong_approval,
        graph_fingerprint=graph_fingerprint,
        graph_provenance_fingerprint=(
            graph_provenance_fingerprint
        ),
        source_bundle_fingerprint=source_bundle_fingerprint,
        constitution_fingerprint=constitution_fingerprint,
        semantic_profile_fingerprint=profile.fingerprint,
        manifest_fingerprint=manifest_fingerprint,
    )

    assert (
        without_approval.human_claim_authority
        is HumanClaimAuthority.SOFT_LABEL
    )
    assert (
        weakly_approved.human_claim_authority
        is HumanClaimAuthority.SOFT_LABEL
    )
    assert (
        approved.human_claim_authority
        is HumanClaimAuthority.GROUND_TRUTH
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "graph_fingerprint",
        "graph_provenance_fingerprint",
        "source_bundle_fingerprint",
        "constitution_fingerprint",
        "semantic_profile_fingerprint",
        "manifest_fingerprint",
    ],
)
def test_strong_operator_approval_rejects_every_binding_drift(
    field_name: str,
) -> None:
    values = {
        "graph_fingerprint": _fingerprint("a"),
        "graph_provenance_fingerprint": _fingerprint("b"),
        "source_bundle_fingerprint": _fingerprint("c"),
        "constitution_fingerprint": _fingerprint("d"),
        "semantic_profile_fingerprint": _fingerprint("e"),
        "manifest_fingerprint": _fingerprint("f"),
    }
    approval = HumanEvidenceApprovalV1(
        evidence_graph_logical_fingerprint=values[
            "graph_fingerprint"
        ],
        evidence_graph_provenance_fingerprint=values[
            "graph_provenance_fingerprint"
        ],
        source_bundle_fingerprint=values[
            "source_bundle_fingerprint"
        ],
        constitution_fingerprint=values["constitution_fingerprint"],
        semantic_profile_fingerprint=values[
            "semantic_profile_fingerprint"
        ],
        manifest_fingerprint=values["manifest_fingerprint"],
        approval_origin=ManifestOrigin.OPERATOR_EXPLICIT,
    )
    drifted = dict(values)
    drifted[field_name] = _fingerprint("9")

    assert not approval.matches(
        **drifted,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
    )


def test_authority_context_requires_graph_bound_registry_attestation() -> None:
    graph, _ = _graph_and_case()
    forged = EvidenceAuthorityContextV1(
        evidence_graph_provenance_fingerprint=(
            graph.provenance_fingerprint
        ),
        verification_registry_fingerprint=_fingerprint("7"),
    )

    assert not forged.authorizes_claim(
        "claim-preference",
        graph=graph,
        manifest_origin=ManifestOrigin.CONVENTIONAL_UNTRUSTED,
        manifest_fingerprint=_fingerprint("8"),
    )
    issued = issue_evidence_authority_context(graph)
    assert issued.verification_registry_fingerprint != _fingerprint("7")


def test_model_qualification_is_bound_to_protocol_and_thresholds() -> None:
    report = SemanticModelQualificationReportV1(
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=_fingerprint("4"),
        corpus_fingerprint=_fingerprint("5"),
        threshold_set_fingerprint=_fingerprint("6"),
        metric_values={"claim_precision": 0.99},
        required_thresholds={"claim_precision": 0.98},
        false_authority_elevation_count=0,
        status=QualificationStatus.QUALIFIED,
        issued_at_utc="2026-01-01T00:00:00Z",
        expires_at_utc="2100-01-01T00:00:00Z",
        **_production_qualification_fields(),
    )

    assert report.qualifies(
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=_fingerprint("4"),
        corpus_fingerprint=_fingerprint("5"),
        threshold_set_fingerprint=_fingerprint("6"),
    )
    assert not report.qualifies(
        model_profile_fingerprint=_fingerprint("9"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=_fingerprint("4"),
        corpus_fingerprint=_fingerprint("5"),
        threshold_set_fingerprint=_fingerprint("6"),
    )
    assert (
        SemanticModelQualificationReportV1.from_dict(report.to_dict())
        == report
    )
    assert not replace(
        report,
        false_authority_elevation_count=1,
    ).qualifies(
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=_fingerprint("4"),
        corpus_fingerprint=_fingerprint("5"),
        threshold_set_fingerprint=_fingerprint("6"),
    )
    with pytest.raises(
        EvaluationPlanContractError,
        match="non-empty",
    ):
        replace(report, required_thresholds={})


def test_plan_cannot_disable_current_evaluator_and_round_trips() -> None:
    plan = _plan(_fingerprint("a"))

    assert SelfImprovementEvaluationPlanV1.from_dict(plan.to_dict()) == plan
    with pytest.raises(
        EvaluationPlanContractError,
        match="cannot disable",
    ):
        replace(plan, current_evaluator_required=False)


def test_plan_rejects_held_out_and_cross_case_evidence() -> None:
    profile = default_semantic_ingestion_profile()
    graph, case = _graph_and_case()
    graph = replace(graph, profile_fingerprint=profile.fingerprint)
    held_out = SelfImprovementSignalSetV1(
        signals=(_signal(),),
        case_splits={"case-1": DatasetSplit.HELD_OUT},
        synthesis_report_refs=("synthesis-1",),
        critic_report_refs=("critic-1",),
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    plan = _plan(profile.fingerprint)

    with pytest.raises(
        EvaluationPlanContractError,
        match="train split",
    ):
        plan.validate_references(
            graph=graph,
            case=case,
            signal_set=held_out,
        )

    train = replace(
        held_out,
        case_splits={"case-1": DatasetSplit.TRAIN},
    )
    case_without_result_b = replace(
        case,
        result_claim_ids=("claim-result-a",),
    )
    with pytest.raises(
        EvaluationPlanContractError,
        match="belong to the evaluation case",
    ):
        plan.validate_references(
            graph=graph,
            case=case_without_result_b,
            signal_set=train,
        )


def test_compiler_clamps_untrusted_agent_proposal() -> None:
    profile = _elevated_profile()
    effective = effective_profile_for_origin(
        profile,
        manifest_origin=ManifestOrigin.CONVENTIONAL_UNTRUSTED,
    )
    graph, case = _graph_and_case()
    graph = replace(graph, profile_fingerprint=effective.fingerprint)
    signal_set = SelfImprovementSignalSetV1(
        signals=(_signal(),),
        case_splits={"case-1": DatasetSplit.TRAIN},
        synthesis_report_refs=("synthesis-1",),
        critic_report_refs=("critic-1",),
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    proposal = replace(
        _plan(profile.fingerprint),
        reason_codes=("unresolved_semantic_conflict",),
    )

    compiled = compile_evaluation_plan(
        proposal,
        profile=profile,
        manifest_origin=ManifestOrigin.CONVENTIONAL_UNTRUSTED,
        manifest_fingerprint=_fingerprint("b"),
        graph=graph,
        case=case,
        signal_set=signal_set,
        authority_context=issue_evidence_authority_context(graph),
        qualification_report=None,
        qualification_registry=SemanticQualificationRegistryV1(
            trusted_report_fingerprints=()
        ),
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=_fingerprint("4"),
        qualification_corpus_fingerprint=_fingerprint("5"),
        qualification_threshold_set_fingerprint=_fingerprint("6"),
    )

    assert (
        compiled.human_claim_authority
        is HumanClaimAuthority.SOFT_LABEL
    )
    assert (
        compiled.historical_judge_authority
        is HistoricalJudgeAuthority.ADVISORY
    )
    assert compiled.aggregation_policy is AggregationPolicy.NONE
    assert compiled.conflict_policy is ConflictPolicy.REQUIRE_REVIEW
    assert (
        compiled.disposition
        is EvaluationDisposition.HUMAN_REVIEW_REQUIRED
    )
    assert set(compiled.reason_codes) == {
        "historical_judge_authority_clamped",
        "human_authority_clamped",
        "semantic_model_not_qualified",
        "supporting_evidence_not_authoritative",
        "expected_output_not_authoritative",
        "rubric_groups_recompiled",
    }
    assert "unresolved_semantic_conflict" not in compiled.reason_codes


def test_compiler_preserves_explicit_approved_authority() -> None:
    manifest_fingerprint = _fingerprint("b")
    initial_profile = _elevated_profile()
    graph, case = _graph_and_case()
    graph = replace(graph, profile_fingerprint=initial_profile.fingerprint)
    graph_fingerprint = graph.logical_fingerprint
    profile = _elevated_profile(approved_graph=graph_fingerprint)
    approval = issue_human_evidence_approval(
        profile=profile,
        graph_fingerprint=graph_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        graph_provenance_fingerprint=graph.provenance_fingerprint,
        source_bundle_fingerprint=_fingerprint("7"),
        constitution_fingerprint=_fingerprint("4"),
        semantic_profile_fingerprint=profile.fingerprint,
    )
    signal_set = SelfImprovementSignalSetV1(
        signals=(_signal(),),
        case_splits={"case-1": DatasetSplit.TRAIN},
        synthesis_report_refs=("synthesis-1",),
        critic_report_refs=("critic-1",),
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    report = SemanticModelQualificationReportV1(
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=_fingerprint("4"),
        corpus_fingerprint=_fingerprint("5"),
        threshold_set_fingerprint=_fingerprint("6"),
        metric_values={"claim_precision": 0.99},
        required_thresholds={"claim_precision": 0.98},
        false_authority_elevation_count=0,
        status=QualificationStatus.QUALIFIED,
        issued_at_utc="2026-01-01T00:00:00Z",
        expires_at_utc="2100-01-01T00:00:00Z",
        **_production_qualification_fields(),
    )

    compiled = compile_evaluation_plan(
        _plan(profile.fingerprint),
        profile=profile,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        manifest_fingerprint=manifest_fingerprint,
        graph=graph,
        case=case,
        signal_set=signal_set,
        authority_context=issue_evidence_authority_context(
            graph,
            human_approval=approval,
            source_bundle_fingerprint=_fingerprint("7"),
            constitution_fingerprint=_fingerprint("4"),
        ),
        qualification_report=report,
        qualification_registry=SemanticQualificationRegistryV1(
            trusted_report_fingerprints=(report.report_fingerprint,)
        ),
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=_fingerprint("4"),
        qualification_corpus_fingerprint=_fingerprint("5"),
        qualification_threshold_set_fingerprint=_fingerprint("6"),
    )

    assert (
        compiled.human_claim_authority
        is HumanClaimAuthority.GROUND_TRUTH
    )
    assert (
        compiled.historical_judge_authority
        is HistoricalJudgeAuthority.SCORED_SIGNAL
    )
    assert (
        compiled.disposition
        is EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
    )
    assert compiled.current_evaluator_required is True


def test_partial_human_approval_cannot_authorize_unscoped_claims() -> None:
    manifest_fingerprint = _fingerprint("b")
    initial_profile = _elevated_profile()
    graph, case = _graph_and_case()
    graph = replace(graph, profile_fingerprint=initial_profile.fingerprint)
    profile = _elevated_profile(
        approved_graph=graph.logical_fingerprint
    )
    approval = issue_human_evidence_approval(
        profile=profile,
        graph_fingerprint=graph.logical_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        approved_claim_scope=("claim-preference",),
        graph_provenance_fingerprint=graph.provenance_fingerprint,
        source_bundle_fingerprint=_fingerprint("7"),
        constitution_fingerprint=_fingerprint("4"),
        semantic_profile_fingerprint=profile.fingerprint,
    )
    signal_set = SelfImprovementSignalSetV1(
        signals=(_signal(),),
        case_splits={"case-1": DatasetSplit.TRAIN},
        synthesis_report_refs=("synthesis-1",),
        critic_report_refs=("critic-1",),
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    report = SemanticModelQualificationReportV1(
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=_fingerprint("4"),
        corpus_fingerprint=_fingerprint("5"),
        threshold_set_fingerprint=_fingerprint("6"),
        metric_values={"claim_precision": 0.99},
        required_thresholds={"claim_precision": 0.98},
        false_authority_elevation_count=0,
        status=QualificationStatus.QUALIFIED,
        issued_at_utc="2026-01-01T00:00:00Z",
        expires_at_utc="2100-01-01T00:00:00Z",
        **_production_qualification_fields(),
    )

    compiled = compile_evaluation_plan(
        _plan(profile.fingerprint),
        profile=profile,
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        manifest_fingerprint=manifest_fingerprint,
        graph=graph,
        case=case,
        signal_set=signal_set,
        authority_context=issue_evidence_authority_context(
            graph,
            human_approval=approval,
            source_bundle_fingerprint=_fingerprint("7"),
            constitution_fingerprint=_fingerprint("4"),
        ),
        qualification_report=report,
        qualification_registry=SemanticQualificationRegistryV1(
            trusted_report_fingerprints=(report.report_fingerprint,)
        ),
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=_fingerprint("4"),
        qualification_corpus_fingerprint=_fingerprint("5"),
        qualification_threshold_set_fingerprint=_fingerprint("6"),
    )

    assert (
        compiled.disposition
        is EvaluationDisposition.HUMAN_REVIEW_REQUIRED
    )
    assert "expected_output_not_authoritative" in compiled.reason_codes


def test_authority_context_rejects_missing_or_wrong_strong_bindings() -> None:
    graph, _ = _graph_and_case()
    profile = _elevated_profile(
        approved_graph=graph.logical_fingerprint
    )
    approval = issue_human_evidence_approval(
        profile=profile,
        graph_fingerprint=graph.logical_fingerprint,
        manifest_fingerprint=_fingerprint("8"),
        manifest_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        graph_provenance_fingerprint=graph.provenance_fingerprint,
        source_bundle_fingerprint=_fingerprint("7"),
        constitution_fingerprint=_fingerprint("4"),
        semantic_profile_fingerprint=profile.fingerprint,
    )

    with pytest.raises(
        EvaluationPlanContractError,
        match="exact source and constitution",
    ):
        issue_evidence_authority_context(
            graph,
            human_approval=approval,
        )
    with pytest.raises(
        EvaluationPlanContractError,
        match="exact source and constitution",
    ):
        issue_evidence_authority_context(
            graph,
            human_approval=approval,
            source_bundle_fingerprint=_fingerprint("6"),
            constitution_fingerprint=_fingerprint("4"),
        )
