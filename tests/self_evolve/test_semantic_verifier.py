from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from aworld.self_evolve.constitution import (
    AgenticStageReportV1,
    AgenticStageStatus,
    SelfEvolveStage,
    default_self_evolve_constitution,
)
from aworld.self_evolve.evidence import (
    ClaimVerificationOrigin,
    SemanticSourceDispositionKind,
    SemanticSourceDispositionV1,
)
from aworld.self_evolve.evaluation_plan import (
    EvaluationDisposition,
    ManifestOrigin,
    QualificationStatus,
    SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1,
    SemanticModelQualificationReportV1,
    SemanticQualificationMethod,
    SemanticQualificationRegistryV1,
)
from aworld.self_evolve.ingestion.semantic_compiler import (
    compile_semantic_dataset,
)
from aworld.self_evolve.ingestion.chunking import build_source_bundle
from aworld.self_evolve.ingestion.scanner import scan_source
from aworld.self_evolve.ingestion.semantic_verifier import (
    DEFAULT_INGESTION_STAGES,
    SemanticQualificationEvidenceV1,
    SemanticResolutionEvidenceV1,
    build_semantic_evidence_quality_report,
    evaluate_semantic_quality_gate,
)
from aworld.self_evolve.ingestion.semantic_workflow import (
    evidence_source_span_from_chunk,
)
from aworld.self_evolve.ingestion.types import IngestionMode
from tests.self_evolve.test_semantic_compiler import (
    _authoritative_graph_and_context,
    _with_input_and_traces,
)


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def _semantic_artifacts(tmp_path: Path):
    source = tmp_path / "comparison.md"
    source.write_text(
        "Task input, trajectories, results, and comparison: "
        "Harness B > Harness A.\n",
        encoding="utf-8",
    )
    bundle = build_source_bundle(
        source,
        inventory=scan_source(source),
    )
    span = evidence_source_span_from_chunk(
        bundle.chunks[0],
        span_id="span-1",
    )
    graph, case, signal_set, plan, traces = _with_input_and_traces()
    graph = replace(
        graph,
        spans=(span,),
        source_dispositions=tuple(
            SemanticSourceDispositionV1(
                source_unit_id=source_unit_id,
                disposition=SemanticSourceDispositionKind.EVIDENCE,
                claim_ids=tuple(
                    item.claim_id for item in graph.claims
                ),
                reason_codes=("semantic_evidence",),
                auditor_verification_id="coverage-report-1",
            )
            for source_unit_id in bundle.source_unit_ids
        ),
    )
    graph, authority_context = _authoritative_graph_and_context(
        graph
    )
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
        authority_context=authority_context,
        manifest_origin=ManifestOrigin.ABSENT,
        manifest_fingerprint=_fingerprint("9"),
    )
    candidate_graph = replace(
        graph,
        claim_verifications=tuple(
            replace(
                item,
                verification_origin=(
                    ClaimVerificationOrigin.SEMANTIC_AGENT
                ),
            )
            for item in graph.claim_verifications
        ),
    )
    resolution = SemanticResolutionEvidenceV1(
        candidate_graphs=(candidate_graph, candidate_graph),
        resolver_output_fingerprints=(
            graph.logical_fingerprint,
            graph.logical_fingerprint,
        ),
    )
    constitution = default_self_evolve_constitution()
    report = SemanticModelQualificationReportV1(
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=constitution.fingerprint,
        corpus_fingerprint=_fingerprint("5"),
        threshold_set_fingerprint=_fingerprint("6"),
        metric_values={"claim_precision": 0.99},
        required_thresholds={"claim_precision": 0.98},
        false_authority_elevation_count=0,
        status=QualificationStatus.QUALIFIED,
        issued_at_utc="2026-01-01T00:00:00Z",
        expires_at_utc="2100-01-01T00:00:00Z",
        qualification_method=(
            SemanticQualificationMethod.EXACT_SNAPSHOT_V1
        ),
        runner_protocol_fingerprint=(
            SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1
        ),
        case_attestation_bundle_fingerprint=_fingerprint("a"),
    )
    qualification = SemanticQualificationEvidenceV1(
        registry=SemanticQualificationRegistryV1(
            trusted_report_fingerprints=(
                report.report_fingerprint,
            )
        ),
        report=report,
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=constitution.fingerprint,
        corpus_fingerprint=_fingerprint("5"),
        threshold_set_fingerprint=_fingerprint("6"),
    )
    return (
        bundle,
        graph,
        case,
        signal_set,
        plan,
        compiled,
        resolution,
        qualification,
    )


def _stage_reports():
    constitution = default_self_evolve_constitution()
    reports = []
    previous_output = _fingerprint("0")
    for index, stage in enumerate(DEFAULT_INGESTION_STAGES):
        contract = constitution.contract_for(stage)
        output = _fingerprint(format(index + 1, "x"))
        next_index = tuple(SelfEvolveStage).index(stage) + 1
        reports.append(
            AgenticStageReportV1(
                report_id=f"report-{stage.value}",
                stage=stage,
                input_fingerprints=(previous_output,),
                output_fingerprints=(output,),
                agent_role=contract.allowed_roles[0],
                provider_fingerprint=_fingerprint("a"),
                model_fingerprint=_fingerprint("b"),
                protocol_fingerprint=_fingerprint("c"),
                independence_group=f"group-{index}",
                attempt_count=1,
                status=AgenticStageStatus.COMPLETE,
                next_stage_proposal=tuple(SelfEvolveStage)[next_index],
                input_schema_versions=contract.required_input_schemas,
                output_schema_versions=contract.required_output_schemas,
                model_call_count=1,
                source_bytes_consumed=100,
                token_count=50,
            )
        )
        previous_output = output
    return tuple(reports)


def test_semantic_quality_metrics_and_verified_gate(tmp_path: Path) -> None:
    (
        bundle,
        graph,
        case,
        signal_set,
        plan,
        compiled,
        resolution,
        qualification,
    ) = _semantic_artifacts(tmp_path)
    report = build_semantic_evidence_quality_report(
        bundle=bundle,
        graph=graph,
        constitution=default_self_evolve_constitution(),
        stage_reports=_stage_reports(),
        signal_set=signal_set,
        semantic_cases=(case,),
        evaluation_plans=(plan,),
        compiled_dataset=compiled,
        resolution_evidence=resolution,
        qualification_evidence=qualification,
    )

    assert report.source_span_coverage_rate == 1.0
    assert report.semantic_source_disposition_coverage_rate == 1.0
    assert report.semantic_entailment_coverage_rate == 1.0
    assert report.agentic_stage_completion_rate == 1.0
    assert report.failure_reason_codes == ()
    assert evaluate_semantic_quality_gate(
        report,
        mode=IngestionMode.AUTO_VERIFIED,
    ).allowed is True


def test_unexplained_source_unit_is_a_hard_failure(tmp_path: Path) -> None:
    (
        bundle,
        graph,
        case,
        signal_set,
        plan,
        compiled,
        resolution,
        qualification,
    ) = _semantic_artifacts(tmp_path)
    graph = replace(graph, source_dispositions=())
    report = build_semantic_evidence_quality_report(
        bundle=bundle,
        graph=graph,
        constitution=default_self_evolve_constitution(),
        stage_reports=_stage_reports(),
        signal_set=signal_set,
        semantic_cases=(case,),
        evaluation_plans=(plan,),
        compiled_dataset=compiled,
        resolution_evidence=resolution,
        qualification_evidence=qualification,
    )

    decision = evaluate_semantic_quality_gate(
        report,
        mode=IngestionMode.PROPOSAL,
    )
    assert decision.allowed is False
    assert "semantic_source_units_unexplained" in decision.reason_codes


def test_verified_adds_qualification_consensus_and_population_gates(
    tmp_path: Path,
) -> None:
    (
        bundle,
        graph,
        case,
        signal_set,
        plan,
        compiled,
        resolution,
        _,
    ) = _semantic_artifacts(tmp_path)
    resolution = replace(
        resolution,
        candidate_graphs=(resolution.candidate_graphs[0],),
    )
    report = build_semantic_evidence_quality_report(
        bundle=bundle,
        graph=graph,
        constitution=default_self_evolve_constitution(),
        stage_reports=_stage_reports(),
        signal_set=signal_set,
        semantic_cases=(case,),
        evaluation_plans=(plan,),
        compiled_dataset=compiled,
        resolution_evidence=resolution,
    )

    assert evaluate_semantic_quality_gate(
        report,
        mode=IngestionMode.PROPOSAL,
    ).allowed is True
    verified = evaluate_semantic_quality_gate(
        report,
        mode=IngestionMode.AUTO_VERIFIED,
    )
    assert verified.allowed is False
    assert {
        "semantic_model_not_qualified",
        "semantic_candidate_count_insufficient",
        "semantic_parse_consensus_below_threshold",
    }.issubset(verified.reason_codes)


def test_verified_rejects_proposal_only_trainable_plan(
    tmp_path: Path,
) -> None:
    (
        bundle,
        graph,
        case,
        signal_set,
        plan,
        _,
        resolution,
        qualification,
    ) = _semantic_artifacts(tmp_path)
    proposal = replace(
        plan,
        expected_output_claim_id=None,
        disposition=EvaluationDisposition.PROPOSAL_ONLY,
        reason_codes=("supporting_evidence_not_authoritative",),
    )
    compiled = compile_semantic_dataset(
        graph=graph,
        cases=(case,),
        signal_set=signal_set,
        evaluation_plans=(proposal,),
        resolved_traces=_with_input_and_traces()[4],
        verified_only_signal_projection=True,
    )
    report = build_semantic_evidence_quality_report(
        bundle=bundle,
        graph=graph,
        constitution=default_self_evolve_constitution(),
        stage_reports=_stage_reports(),
        signal_set=signal_set,
        semantic_cases=(case,),
        evaluation_plans=(proposal,),
        compiled_dataset=compiled,
        resolution_evidence=resolution,
        qualification_evidence=qualification,
    )

    assert report.verified_eligible_plan_count == 0
    assert report.non_verified_trainable_plan_count == 1
    assert evaluate_semantic_quality_gate(
        report,
        mode=IngestionMode.PROPOSAL,
    ).allowed is True
    verified = evaluate_semantic_quality_gate(
        report,
        mode=IngestionMode.AUTO_VERIFIED,
    )
    assert verified.allowed is False
    assert "semantic_trainable_plan_not_verified" in (
        verified.reason_codes
    )
    assert compiled.normalized_cases[0].self_improvement_signals == ()


def test_missing_or_broken_stage_chain_fails_closed(tmp_path: Path) -> None:
    (
        bundle,
        graph,
        case,
        signal_set,
        plan,
        compiled,
        resolution,
        qualification,
    ) = _semantic_artifacts(tmp_path)
    reports = _stage_reports()
    broken = (
        reports[0],
        replace(
            reports[1],
            input_fingerprints=(_fingerprint("f"),),
        ),
    )
    report = build_semantic_evidence_quality_report(
        bundle=bundle,
        graph=graph,
        constitution=default_self_evolve_constitution(),
        stage_reports=broken,
        signal_set=signal_set,
        semantic_cases=(case,),
        evaluation_plans=(plan,),
        compiled_dataset=compiled,
        resolution_evidence=resolution,
        qualification_evidence=qualification,
    )

    assert "semantic_stage_report_chain_invalid" in (
        report.failure_reason_codes
    )
    assert "semantic_stage_reports_missing" in report.failure_reason_codes
