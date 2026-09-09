from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from aworld.self_evolve.constitution import (
    AgenticStageReportV1,
    AgenticStageStatus,
    SemanticRolloutPolicyV1,
    SemanticRolloutStage,
    SelfEvolveStage,
    default_self_evolve_constitution,
)
from aworld.self_evolve.datasets import (
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.evaluation_plan import (
    EvaluationDisposition,
    ManifestOrigin,
    QualificationStatus,
    SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1,
    SemanticModelQualificationReportV1,
    SemanticQualificationMethod,
    SemanticQualificationRegistryV1,
    compile_evaluation_plan,
    default_semantic_ingestion_profile,
)
from aworld.self_evolve.evidence import (
    ClaimVerificationOrigin,
    EvidenceClaimKind,
    SemanticSourceDispositionKind,
    SemanticSourceDispositionV1,
    authoritative_verification_registry_fingerprint,
)
from aworld.self_evolve.improvement_signals import DatasetSplit
from aworld.self_evolve.ingestion.chunking import build_source_bundle
from aworld.self_evolve.ingestion.scanner import scan_source
from aworld.self_evolve.ingestion.semantic_compiler import (
    TraceCandidateAttestationV1,
    attest_resolved_trace,
    canonical_semantic_case_id,
    canonical_semantic_plan_id,
    canonical_semantic_signal_id,
    compile_semantic_dataset,
)
from aworld.self_evolve.ingestion.semantic_snapshot import (
    FrozenSemanticIngestionSnapshotV2,
)
from aworld.self_evolve.ingestion.semantic_verifier import (
    SemanticQualificationEvidenceV1,
    SemanticResolutionEvidenceV1,
    build_semantic_evidence_quality_report,
    evaluate_semantic_quality_gate,
)
from aworld.self_evolve.ingestion.semantic_workflow import (
    evidence_source_span_from_chunk,
)
from aworld.self_evolve.ingestion.types import (
    IngestionContractError,
    IngestionManifestOrigin,
    IngestionMode,
    IngestorTrustLevel,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.runner import (
    _validate_frozen_semantic_runtime_admission,
    _validate_rerun_source_runtime_admission,
)
from tests.self_evolve.test_semantic_compiler import (
    _authoritative_graph_and_context,
    _with_input_and_traces,
)


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def _stage_reports(
    *,
    inventory_fingerprint: str,
    source_bundle_fingerprint: str,
    graph_provenance_fingerprint: str,
    graph_logical_fingerprint: str,
    signal_set_fingerprint: str,
    target_bundle_fingerprint: str,
    plan_bundle_fingerprint: str,
) -> tuple[AgenticStageReportV1, ...]:
    constitution = default_self_evolve_constitution()
    definitions = (
        (
            SelfEvolveStage.DISCOVER,
            (inventory_fingerprint,),
            (source_bundle_fingerprint,),
        ),
        (
            SelfEvolveStage.UNDERSTAND,
            (source_bundle_fingerprint,),
            (_fingerprint("a"),),
        ),
        (
            SelfEvolveStage.EXTRACT,
            (_fingerprint("a"),),
            (_fingerprint("b"),),
        ),
        (
            SelfEvolveStage.VERIFY_COVERAGE_AND_ENTAILMENT,
            (_fingerprint("b"),),
            (graph_provenance_fingerprint,),
        ),
        (
            SelfEvolveStage.RESOLVE_AND_DETECT_CONFLICT,
            (graph_provenance_fingerprint,),
            (graph_logical_fingerprint,),
        ),
        (
            SelfEvolveStage.SYNTHESIZE_IMPROVEMENT_SIGNALS,
            (graph_logical_fingerprint,),
            (
                signal_set_fingerprint,
                target_bundle_fingerprint,
            ),
        ),
        (
            SelfEvolveStage.PLAN_EVALUATION,
            (signal_set_fingerprint,),
            (plan_bundle_fingerprint,),
        ),
    )
    reports = []
    for index, (stage, inputs, outputs) in enumerate(definitions):
        contract = constitution.contract_for(stage)
        reports.append(
            AgenticStageReportV1(
                report_id=f"report-{stage.value}",
                stage=stage,
                input_fingerprints=inputs,
                output_fingerprints=outputs,
                agent_role=contract.allowed_roles[0],
                provider_fingerprint=_fingerprint("c"),
                model_fingerprint=_fingerprint("d"),
                protocol_fingerprint=_fingerprint("e"),
                independence_group=f"group-{index}",
                attempt_count=1,
                status=AgenticStageStatus.COMPLETE,
                next_stage_proposal=tuple(SelfEvolveStage)[
                    tuple(SelfEvolveStage).index(stage) + 1
                ],
                input_schema_versions=contract.required_input_schemas,
                output_schema_versions=contract.required_output_schemas,
                model_call_count=1,
                source_bytes_consumed=100,
                token_count=50,
            )
        )
    return tuple(reports)


def _snapshot(
    tmp_path: Path,
    *,
    split: DatasetSplit = DatasetSplit.TRAIN,
    qualification_evaluated_at_utc: str = (
        "2026-07-01T00:00:00Z"
    ),
    qualification_expires_at_utc: str = (
        "2100-01-01T00:00:00Z"
    ),
) -> FrozenSemanticIngestionSnapshotV2:
    source = tmp_path / "domain.md"
    source.write_text(
        "Task input, Harness A and Harness B trajectories, results, "
        "and human comparison B > A.\n",
        encoding="utf-8",
    )
    inventory = scan_source(source)
    bundle = build_source_bundle(source, inventory=inventory)
    span = evidence_source_span_from_chunk(
        bundle.chunks[0],
        span_id="span-1",
    )
    graph, case, signal_set, proposal, traces = (
        _with_input_and_traces()
    )
    profile = default_semantic_ingestion_profile()
    graph = replace(
        graph,
        spans=(span,),
        profile_fingerprint=profile.fingerprint,
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
    canonical_case_id = canonical_semantic_case_id(
        case,
        graph=graph,
    )
    case = replace(
        case,
        case_id=canonical_case_id,
        trainable_signal_projection={},
    )
    original_signal_id = signal_set.signals[0].signal_id
    canonical_signal = replace(
        signal_set.signals[0],
        case_id=canonical_case_id,
    )
    canonical_signal = replace(
        canonical_signal,
        signal_id=canonical_semantic_signal_id(
            canonical_signal
        ),
    )
    proposal = replace(
        proposal,
        case_id=canonical_case_id,
        training_signal_ids=tuple(
            canonical_signal.signal_id
            if item == original_signal_id
            else item
            for item in proposal.training_signal_ids
        ),
    )
    candidate_attestations = (
        TraceCandidateAttestationV1(
            candidate_fingerprint=_fingerprint("a"),
            provider_fingerprint=_fingerprint("2"),
            model_fingerprint=_fingerprint("1"),
            protocol_fingerprint=_fingerprint("3"),
            independence_group="snapshot-candidate-1",
        ),
        TraceCandidateAttestationV1(
            candidate_fingerprint=_fingerprint("b"),
            provider_fingerprint=_fingerprint("2"),
            model_fingerprint=_fingerprint("1"),
            protocol_fingerprint=_fingerprint("3"),
            independence_group="snapshot-candidate-2",
        ),
    )
    trajectory_claims = {
        str(item.payload["trace_ref"]): item
        for item in graph.claims
        if item.kind is EvidenceClaimKind.EXECUTION_TRAJECTORY
    }
    traces = {
        trace_ref: attest_resolved_trace(
            trace,
            graph=graph,
            trajectory_claim_id=(
                trajectory_claims[trace_ref].claim_id
            ),
            source_bundle=bundle,
            candidate_attestations=candidate_attestations,
        )
        for trace_ref, trace in traces.items()
    }
    signal_set = replace(
        signal_set,
        signals=(canonical_signal,),
        case_splits={canonical_case_id: split},
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    constitution = default_self_evolve_constitution()
    qualification_report = SemanticModelQualificationReportV1(
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=constitution.fingerprint,
        corpus_fingerprint=_fingerprint("4"),
        threshold_set_fingerprint=_fingerprint("5"),
        metric_values={"claim_precision": 0.99},
        required_thresholds={"claim_precision": 0.98},
        false_authority_elevation_count=0,
        status=QualificationStatus.QUALIFIED,
        issued_at_utc="2026-01-01T00:00:00Z",
        expires_at_utc=qualification_expires_at_utc,
        qualification_method=(
            SemanticQualificationMethod.EXACT_SNAPSHOT_V1
        ),
        runner_protocol_fingerprint=(
            SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1
        ),
        case_attestation_bundle_fingerprint=_fingerprint("a"),
    )
    qualification_registry = SemanticQualificationRegistryV1(
        trusted_report_fingerprints=(
            qualification_report.report_fingerprint,
        )
    )
    manifest_fingerprint = _fingerprint("9")
    if split is DatasetSplit.HELD_OUT:
        plan = replace(
            proposal,
            training_signal_ids=(),
            supporting_evidence_claim_ids=(),
            replay_seed_execution_id=None,
            expected_output_claim_id=None,
            disposition=EvaluationDisposition.PROPOSAL_ONLY,
            reason_codes=("held_out_case",),
            profile_fingerprint=profile.fingerprint,
        )
        rollout_policy = SemanticRolloutPolicyV1(
            policy_id="semantic-rollout-proposal",
            enabled_stage=SemanticRolloutStage.PROPOSAL,
        )
        mode = IngestionMode.PROPOSAL
    else:
        proposal = replace(
            proposal,
            comparison_unit=profile.comparison_unit,
            human_claim_authority=profile.human_claim_authority,
            historical_judge_authority=(
                profile.historical_judge_authority
            ),
            aggregation_policy=profile.aggregation_policy,
            conflict_policy=profile.conflict_policy,
            profile_fingerprint=profile.fingerprint,
        )
        if split is DatasetSplit.VALIDATION:
            proposal = replace(
                proposal,
                training_signal_ids=(),
                supporting_evidence_claim_ids=(),
            )
        plan = compile_evaluation_plan(
            proposal,
            profile=profile,
            manifest_origin=ManifestOrigin.ABSENT,
            manifest_fingerprint=manifest_fingerprint,
            graph=graph,
            case=case,
            signal_set=signal_set,
            authority_context=authority_context,
            qualification_report=qualification_report,
            qualification_registry=qualification_registry,
            model_profile_fingerprint=_fingerprint("1"),
            provider_fingerprint=_fingerprint("2"),
            semantic_protocol_fingerprint=_fingerprint("3"),
            constitution_fingerprint=constitution.fingerprint,
            qualification_corpus_fingerprint=_fingerprint("4"),
            qualification_threshold_set_fingerprint=(
                _fingerprint("5")
            ),
            qualification_evaluated_at_utc=(
                qualification_evaluated_at_utc
            ),
        )
        rollout_policy = SemanticRolloutPolicyV1(
            policy_id="semantic-rollout-verified",
            enabled_stage=SemanticRolloutStage.VERIFIED,
        )
        mode = IngestionMode.AUTO_VERIFIED
    plan = replace(
        plan,
        plan_id=canonical_semantic_plan_id(plan),
    )
    ingestion_id = FrozenSemanticIngestionSnapshotV2.identity_for(
        inventory_fingerprint=inventory.source_root_fingerprint,
        source_bundle_fingerprint=bundle.fingerprint,
        constitution_fingerprint=constitution.fingerprint,
        rollout_policy_fingerprint=rollout_policy.fingerprint,
        semantic_profile_fingerprint=profile.fingerprint,
        manifest_fingerprint=None,
        manifest_origin=IngestionManifestOrigin.ABSENT,
        extractor_fingerprints=(),
        semantic_model_profile_fingerprint=_fingerprint("1"),
        semantic_provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        qualification_report_fingerprint=(
            qualification_report.report_fingerprint
        ),
        ingestor_name="auto",
        ingestor_version="2",
        trust_level=IngestorTrustLevel.FRAMEWORK_BUILTIN,
        qualification_evaluated_at_utc=(
            qualification_evaluated_at_utc
        ),
        authority_context_fingerprint=authority_context.fingerprint,
        qualification_registry_fingerprint=(
            qualification_registry.fingerprint
        ),
    )
    compiled = compile_semantic_dataset(
        graph=graph,
        cases=(case,),
        signal_set=signal_set,
        evaluation_plans=(plan,),
        resolved_traces=traces,
        ingestion_id=ingestion_id,
        authority_context=authority_context,
        manifest_origin=ManifestOrigin.ABSENT,
        manifest_fingerprint=manifest_fingerprint,
    )
    reports = _stage_reports(
        inventory_fingerprint=inventory.source_root_fingerprint,
        source_bundle_fingerprint=bundle.fingerprint,
        graph_provenance_fingerprint=graph.provenance_fingerprint,
        graph_logical_fingerprint=graph.logical_fingerprint,
        signal_set_fingerprint=signal_set.fingerprint,
        target_bundle_fingerprint=(
            compiled.target_evidence_bundle.fingerprint
        ),
        plan_bundle_fingerprint=(
            compiled.evaluation_plan_bundle_fingerprint
        ),
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
    resolution_evidence = SemanticResolutionEvidenceV1(
        candidate_graphs=(candidate_graph, candidate_graph),
        resolver_output_fingerprints=(
            graph.logical_fingerprint,
            graph.logical_fingerprint,
        ),
    )
    qualification_evidence = SemanticQualificationEvidenceV1(
        registry=qualification_registry,
        report=qualification_report,
        model_profile_fingerprint=_fingerprint("1"),
        provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=constitution.fingerprint,
        corpus_fingerprint=_fingerprint("4"),
        threshold_set_fingerprint=_fingerprint("5"),
        evaluated_at_utc=qualification_evaluated_at_utc,
    )
    quality = build_semantic_evidence_quality_report(
        bundle=bundle,
        graph=graph,
        constitution=constitution,
        stage_reports=reports,
        signal_set=signal_set,
        semantic_cases=(case,),
        evaluation_plans=(plan,),
        compiled_dataset=compiled,
        resolution_evidence=resolution_evidence,
        qualification_evidence=qualification_evidence,
    )
    gate = evaluate_semantic_quality_gate(quality, mode=mode)
    return FrozenSemanticIngestionSnapshotV2(
        ingestion_id=ingestion_id,
        inventory=inventory,
        source_bundle=bundle,
        constitution=constitution,
        rollout_policy=rollout_policy,
        semantic_profile=profile,
        stage_reports=reports,
        evidence_graph=graph,
        evidence_authority_context=authority_context,
        semantic_cases=(case,),
        improvement_signal_set=signal_set,
        evaluation_plans=(plan,),
        resolved_traces=tuple(traces.values()),
        compiled_dataset=compiled,
        quality_report=quality,
        quality_gate=gate,
        resolution_evidence=resolution_evidence,
        authoritative_verification_ids=tuple(
            item.verification_id
            for item in graph.claim_verifications
            if item.is_authoritative_origin
        ),
        verification_registry_fingerprint=(
            authoritative_verification_registry_fingerprint(
                graph,
                tuple(
                    item.verification_id
                    for item in graph.claim_verifications
                    if item.is_authoritative_origin
                ),
            )
        ),
        semantic_model_profile_fingerprint=_fingerprint("1"),
        semantic_provider_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        qualification_registry=qualification_registry,
        qualification_corpus_fingerprint=_fingerprint("4"),
        qualification_threshold_set_fingerprint=_fingerprint("5"),
        qualification_report=qualification_report,
        qualification_evaluated_at_utc=(
            qualification_evidence.evaluated_at_utc
        ),
    )


def test_semantic_snapshot_store_round_trip_and_public_projection(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    store = FilesystemSelfEvolveStore(tmp_path)
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="agentic_source",
            ingestion_snapshot=snapshot,
        )
    )

    path = store.write_ingestion(
        snapshot,
        dataset_recipe=dataset.recipe,
    )
    restored = store.read_ingestion(snapshot.ingestion_id)

    assert path.is_dir()
    assert restored == snapshot
    assert dataset.recipe.source["mapping_fingerprint"] is None
    assert dataset.recipe.source["normalization_kind"] == (
        "semantic_evidence"
    )
    public = json.dumps(
        snapshot.public_projection(),
        ensure_ascii=False,
    )
    assert "Task input" not in public
    assert str(tmp_path) not in public


def test_legacy_semantic_identity_cannot_carry_verified_trust(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    legacy_identity = FrozenSemanticIngestionSnapshotV2.identity_for(
        inventory_fingerprint=(
            snapshot.inventory.source_root_fingerprint
        ),
        source_bundle_fingerprint=snapshot.source_bundle.fingerprint,
        constitution_fingerprint=snapshot.constitution.fingerprint,
        rollout_policy_fingerprint=snapshot.rollout_policy.fingerprint,
        semantic_profile_fingerprint=(
            snapshot.semantic_profile.fingerprint
        ),
        manifest_fingerprint=snapshot.manifest_fingerprint,
        manifest_origin=snapshot.manifest_origin,
        extractor_fingerprints=snapshot.extractor_fingerprints,
        semantic_model_profile_fingerprint=(
            snapshot.semantic_model_profile_fingerprint
        ),
        semantic_provider_fingerprint=(
            snapshot.semantic_provider_fingerprint
        ),
        semantic_protocol_fingerprint=(
            snapshot.semantic_protocol_fingerprint
        ),
        qualification_report_fingerprint=(
            snapshot.qualification_report.report_fingerprint
            if snapshot.qualification_report is not None
            else None
        ),
        ingestor_name=snapshot.ingestor_name,
        ingestor_version=snapshot.ingestor_version,
        trust_level=snapshot.ingestor_trust_level,
    )
    legacy_compiled = replace(
        snapshot.compiled_dataset,
        normalized_cases=tuple(
            replace(
                item,
                source=replace(
                    item.source,
                    ingestion_id=legacy_identity,
                ),
            )
            for item in snapshot.normalized_cases
        ),
    )

    with pytest.raises(
        IngestionContractError,
        match="legacy semantic identity cannot carry verified authority",
    ):
        replace(
            snapshot,
            ingestion_id=legacy_identity,
            compiled_dataset=legacy_compiled,
        )


def test_expired_qualification_preserves_snapshot_audit_but_blocks_new_run(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(
        tmp_path,
        qualification_evaluated_at_utc=(
            "2026-01-15T00:00:00Z"
        ),
        qualification_expires_at_utc=(
            "2026-02-01T00:00:00Z"
        ),
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_ingestion(snapshot)

    restored = store.read_ingestion(snapshot.ingestion_id)

    assert restored == snapshot
    assert restored.quality_gate.allowed is True
    assert restored.qualification_evaluated_at_utc == (
        "2026-01-15T00:00:00Z"
    )
    with pytest.raises(
        ValueError,
        match="qualification is expired",
    ):
        _validate_frozen_semantic_runtime_admission(
            restored,
            mode=IngestionMode.AUTO_VERIFIED,
        )


def test_evaluator_rerun_cannot_upgrade_or_reuse_expired_semantic_input(
    tmp_path: Path,
) -> None:
    proposal = _snapshot(tmp_path, split=DatasetSplit.HELD_OUT)
    with pytest.raises(ValueError, match="mode does not match"):
        _validate_rerun_source_runtime_admission(
            SelfEvolveEvalSourceConfig(
                kind="agentic_source",
                ingestion_snapshot=proposal,
            ),
            apply_policy="auto_verified",
        )

    expired = _snapshot(
        tmp_path,
        qualification_expires_at_utc="2026-02-01T00:00:00Z",
    )
    with pytest.raises(ValueError, match="expired"):
        _validate_rerun_source_runtime_admission(
            SelfEvolveEvalSourceConfig(
                kind="agentic_source",
                ingestion_snapshot=expired,
            ),
            apply_policy="auto_verified",
        )


@pytest.mark.parametrize(
    "relative_path",
    (
        "constitution.json",
        "evidence_graph.json",
        "stage_reports.json",
        "improvement_signals.json",
        "evaluation_plans.json",
        "quality_report.json",
        "resolution_evidence.json",
        "trainable_cases.jsonl",
    ),
)
def test_semantic_snapshot_rejects_tampered_sidecar(
    tmp_path: Path,
    relative_path: str,
) -> None:
    snapshot = _snapshot(tmp_path)
    store = FilesystemSelfEvolveStore(tmp_path)
    root = store.write_ingestion(snapshot)
    path = root / relative_path
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises((ValueError, json.JSONDecodeError)):
        store.read_ingestion(snapshot.ingestion_id)


def test_semantic_held_out_split_is_not_randomly_reintroduced(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path, split=DatasetSplit.HELD_OUT)
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="agentic_source",
            ingestion_snapshot=snapshot,
        )
    )

    assert dataset.recipe.trainable_case_ids == ()
    assert dataset.recipe.held_out_case_ids == (
        snapshot.semantic_cases[0].case_id,
    )
    assert dataset.cases[0].self_improvement_signals == ()


def test_snapshot_binds_rollout_stage_to_quality_gate_mode(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)

    with pytest.raises(
        IngestionContractError,
        match="rollout stage and semantic quality mode",
    ):
        replace(
            snapshot,
            quality_gate=replace(
                snapshot.quality_gate,
                mode=IngestionMode.PROPOSAL,
            ),
        )


def test_snapshot_rejects_tampered_trace_source_attestation(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    trace = snapshot.resolved_traces[0]
    assert trace.extraction_attestation is not None
    attestation = trace.extraction_attestation
    binding = replace(
        attestation.source_bindings[0],
        source_unit_fingerprint=_fingerprint("f"),
    )
    tampered_attestation = replace(
        attestation,
        source_bindings=(
            binding,
            *attestation.source_bindings[1:],
        ),
    )
    tampered_trace = replace(
        trace,
        extraction_attestation=tampered_attestation,
    )

    with pytest.raises(
        IngestionContractError,
        match="frozen source unit",
    ):
        replace(
            snapshot,
            resolved_traces=(
                tampered_trace,
                *snapshot.resolved_traces[1:],
            ),
        )


def test_store_round_trips_validation_case_without_recipe(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(
        tmp_path,
        split=DatasetSplit.VALIDATION,
    )
    store = FilesystemSelfEvolveStore(tmp_path)

    root = store.write_ingestion(snapshot)
    restored = store.read_ingestion(snapshot.ingestion_id)

    assert restored == snapshot
    trainable = [
        json.loads(line)
        for line in (
            root / "trainable_cases.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [item["case_id"] for item in trainable] == [
        snapshot.normalized_cases[0].case_id
    ]
