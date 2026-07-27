from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest

from aworld.self_evolve.constitution import (
    SelfEvolveStage,
    default_self_evolve_constitution,
)
from aworld.self_evolve.campaign import (
    SelfImprovementCampaignController,
    run_self_improvement_campaign,
)
from aworld.self_evolve.evaluation_plan import (
    EvaluationDisposition,
    HumanEvidenceApprovalV1,
    ManifestOrigin,
    QualificationStatus,
    SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1,
    SemanticModelQualificationReportV1,
    SemanticQualificationMethod,
    SemanticQualificationRegistryV1,
    SelfImprovementEvaluationPlanV1,
    default_semantic_ingestion_profile,
)
from aworld.self_evolve.evidence import (
    EvidenceClaimKind,
    EvidenceEntityKind,
    SelfImprovementCaseResolutionStatus,
    SelfImprovementCaseV1,
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
from aworld.self_evolve.ingestion import (
    AgenticDatasetIngestor,
    IngestionRegistry,
    builtin_extractors,
)
from aworld.self_evolve.ingestion.chunking import build_source_bundle
from aworld.self_evolve.ingestion.scanner import scan_source
from aworld.self_evolve.ingestion.semantic_compiler import (
    ResolvedSemanticTraceV1,
)
from aworld.self_evolve.ingestion.semantic_resolver import (
    canonicalize_evidence_graph,
)
from aworld.self_evolve.ingestion.semantic_snapshot import (
    FrozenSemanticIngestionSnapshotV2,
)
from aworld.self_evolve.ingestion.semantic_ingestor import (
    promote_frozen_semantic_ingestion,
)
from aworld.self_evolve.ingestion.semantic_workflow import (
    SEMANTIC_AGENT_CANDIDATE_SCHEMA_VERSION,
    SemanticProviderResponseV1,
    evidence_source_span_from_chunk,
)
from aworld.self_evolve.ingestion.types import (
    DatasetIngestionRequest,
    FrozenIngestionSnapshot,
    IngestionContractError,
    IngestionManifestOrigin,
    IngestionMode,
    fingerprint_json,
)
from aworld.self_evolve.runner import (
    _load_human_evidence_approval,
    optimize_from_cli_request,
    promote_ingestion_from_cli_request,
)
from aworld.self_evolve.semantic_qualification import (
    FRAMEWORK_SEMANTIC_QUALIFICATION_CORPUS_FINGERPRINT_V1,
    framework_semantic_qualification_thresholds_v1,
)
from tests.self_evolve.test_semantic_compiler import (
    _with_input_and_traces,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


class _SemanticFixtureProvider:
    def __init__(
        self,
        *,
        graph,
        semantic_case: SelfImprovementCaseV1,
        signal_set: SelfImprovementSignalSetV1,
        evaluation_plan: SelfImprovementEvaluationPlanV1,
        traces: Mapping[str, ResolvedSemanticTraceV1],
        disagree_on_trace: bool = False,
    ) -> None:
        self.graph = graph
        self.semantic_case = semantic_case
        self.signal_set = signal_set
        self.evaluation_plan = evaluation_plan
        self.traces = traces
        self.disagree_on_trace = disagree_on_trace
        self.calls: list[tuple[str, int]] = []
        self.constitution = default_self_evolve_constitution()

    async def generate(
        self,
        prompt: str,
        *,
        stage: str,
        candidate_index: int,
    ) -> SemanticProviderResponseV1:
        del prompt
        normalized_stage = SelfEvolveStage(stage)
        self.calls.append((normalized_stage.value, candidate_index))
        payload: Mapping[str, Any]
        if normalized_stage is SelfEvolveStage.UNDERSTAND:
            payload = {"source_summary": "mixed harness evidence"}
        elif normalized_stage is SelfEvolveStage.EXTRACT:
            payload = {"extraction_summary": "typed evidence proposed"}
        elif (
            normalized_stage
            is SelfEvolveStage.VERIFY_COVERAGE_AND_ENTAILMENT
        ):
            traces = list(self.traces.values())
            if self.disagree_on_trace and candidate_index == 1:
                original = traces[0]
                trajectory = {
                    "steps": [
                        {
                            "id": "hallucinated",
                            "action": {"content": "not in source"},
                        }
                    ]
                }
                traces[0] = ResolvedSemanticTraceV1(
                    trace_ref=original.trace_ref,
                    trace_fingerprint=fingerprint_json(trajectory),
                    trajectory=trajectory,
                )
            payload = {
                "evidence_graph": self.graph.to_dict(),
                "resolved_traces": [
                    item.to_dict()
                    for item in traces
                ],
            }
        elif (
            normalized_stage
            is SelfEvolveStage.SYNTHESIZE_IMPROVEMENT_SIGNALS
        ):
            semantic_case = self.semantic_case
            signal_set = self.signal_set
            if candidate_index == 1:
                semantic_case = replace(
                    semantic_case,
                    case_id="candidate-two-case",
                )
                alternate_signal = replace(
                    signal_set.signals[0],
                    signal_id="candidate-two-signal",
                    case_id=semantic_case.case_id,
                )
                signal_set = replace(
                    signal_set,
                    signals=(alternate_signal,),
                    case_splits={
                        semantic_case.case_id: DatasetSplit.TRAIN
                    },
                )
            payload = {
                "semantic_cases": [semantic_case.to_dict()],
                "improvement_signal_set": signal_set.to_dict(),
            }
        elif normalized_stage is SelfEvolveStage.PLAN_EVALUATION:
            evaluation_plan = self.evaluation_plan
            if candidate_index == 1:
                evaluation_plan = replace(
                    evaluation_plan,
                    plan_id="candidate-two-plan",
                )
            payload = {
                "evaluation_plans": [
                    evaluation_plan.to_dict()
                ],
            }
        else:
            raise AssertionError(
                f"unexpected agentic stage: {normalized_stage.value}"
            )
        contract = self.constitution.contract_for(normalized_stage)
        return SemanticProviderResponseV1(
            content={
                "schema_version": (
                    SEMANTIC_AGENT_CANDIDATE_SCHEMA_VERSION
                ),
                "stage": normalized_stage.value,
                "artifact_schema_versions": list(
                    contract.required_output_schemas
                ),
                "payload": payload,
            },
            input_token_count=3,
            output_token_count=2,
        )


def _provider_for_source(
    source_path: Path,
    *,
    disagree_on_trace: bool = False,
) -> _SemanticFixtureProvider:
    inventory = scan_source(source_path)
    bundle = build_source_bundle(source_path, inventory=inventory)
    profile = default_semantic_ingestion_profile()
    local_graph, _, _, _, traces = _with_input_and_traces()
    span = evidence_source_span_from_chunk(
        bundle.chunks[0],
        span_id="span-1",
    )
    local_graph = replace(
        local_graph,
        spans=(span,),
        profile_fingerprint=profile.fingerprint,
        source_dispositions=tuple(
            SemanticSourceDispositionV1(
                source_unit_id=source_unit_id,
                disposition=SemanticSourceDispositionKind.EVIDENCE,
                claim_ids=tuple(
                    item.claim_id for item in local_graph.claims
                ),
                reason_codes=("semantic_evidence",),
                auditor_verification_id="coverage-report-1",
            )
            for source_unit_id in bundle.source_unit_ids
        ),
    )
    graph = canonicalize_evidence_graph(
        local_graph,
        profile=profile,
    )
    entities = {item.entity_id: item for item in graph.entities}
    task_id = next(
        item.entity_id
        for item in graph.entities
        if item.kind is EvidenceEntityKind.TASK
    )
    execution_ids = tuple(
        sorted(
            (
                item.entity_id
                for item in graph.entities
                if item.kind is EvidenceEntityKind.EXECUTION
            ),
            key=lambda item: entities[item].canonical_name,
        )
    )
    claims_by_kind = {
        kind: tuple(
            item
            for item in graph.claims
            if item.kind is kind
        )
        for kind in EvidenceClaimKind
    }
    trajectory_ids = tuple(
        sorted(
            (
                item.claim_id
                for item in claims_by_kind[
                    EvidenceClaimKind.EXECUTION_TRAJECTORY
                ]
            ),
            key=lambda item: next(
                claim.subject_entity_ids[0]
                for claim in graph.claims
                if claim.claim_id == item
            ),
        )
    )
    result_ids = tuple(
        sorted(
            item.claim_id
            for item in claims_by_kind[
                EvidenceClaimKind.EXECUTION_RESULT
            ]
        )
    )
    comparison_ids = tuple(
        item.claim_id
        for item in claims_by_kind[
            EvidenceClaimKind.HUMAN_COMPARISON
        ]
    )
    input_ids = tuple(
        item.claim_id
        for item in claims_by_kind[EvidenceClaimKind.TASK_INPUT]
    )
    semantic_case = SelfImprovementCaseV1(
        case_id="case-1",
        task_entity_id=task_id,
        input_claim_ids=input_ids,
        execution_entity_ids=execution_ids,
        trajectory_claim_ids=trajectory_ids,
        result_claim_ids=result_ids,
        comparison_claim_ids=comparison_ids,
        conflict_ids=tuple(
            item.conflict_id for item in graph.conflicts
        ),
        resolution_status=(
            SelfImprovementCaseResolutionStatus.RESOLVED
        ),
    )
    preferred_execution = next(
        item.entity_id
        for item in graph.entities
        if item.kind is EvidenceEntityKind.EXECUTION
        and item.canonical_name == "execution b"
    )
    supporting_claim_ids = tuple(
        sorted((*trajectory_ids, *result_ids, *comparison_ids))
    )
    signal = SelfImprovementSignalV1(
        signal_id="signal-1",
        case_id=semantic_case.case_id,
        kind=SelfImprovementSignalKind.PREFERENCE_DELTA,
        compared_execution_ids=execution_ids,
        preferred_execution_ids=(preferred_execution,),
        supporting_claim_ids=supporting_claim_ids,
        opposing_claim_ids=(),
        behavior_delta=BehaviorDeltaV1(
            preferred_observations=(
                "B recovered after the tool error",
            ),
            non_preferred_observations=(
                "A repeated the failed tool call",
            ),
            result_difference=("B completed; A failed",),
            source_claim_ids=tuple(
                sorted((*trajectory_ids, *result_ids))
            ),
        ),
        metric_delta={"task_success": 1.0},
        desired_behavior=("Diagnose a tool failure before retrying",),
        avoid_behavior=("Do not repeat an identical failed call",),
        capability_requirement=("Recover from tool failures",),
        verification_status=SignalVerificationStatus.VERIFIED,
        actionability=SignalActionability.ACTIONABLE,
        reason_codes=("verified_behavior_contrast",),
    )
    signal_set = SelfImprovementSignalSetV1(
        signals=(signal,),
        case_splits={semantic_case.case_id: DatasetSplit.TRAIN},
        synthesis_report_refs=("synthesis-1",),
        critic_report_refs=("critic-1",),
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    evaluation_plan = SelfImprovementEvaluationPlanV1(
        plan_id="plan-1",
        case_id=semantic_case.case_id,
        comparison_unit=profile.comparison_unit,
        training_signal_ids=(signal.signal_id,),
        supporting_evidence_claim_ids=supporting_claim_ids,
        replay_seed_execution_id=execution_ids[0],
        expected_output_claim_id=None,
        human_claim_authority=profile.human_claim_authority,
        historical_judge_authority=(
            profile.historical_judge_authority
        ),
        rubric_groups={},
        aggregation_policy=profile.aggregation_policy,
        conflict_policy=profile.conflict_policy,
        current_evaluator_required=True,
        disposition=EvaluationDisposition.PROPOSAL_ONLY,
        reason_codes=("agent_proposal",),
        profile_fingerprint=profile.fingerprint,
    )
    semantic_case.validate_against(graph)
    signal.validate_against(graph, semantic_case)
    return _SemanticFixtureProvider(
        graph=graph,
        semantic_case=semantic_case,
        signal_set=signal_set,
        evaluation_plan=evaluation_plan,
        traces=traces,
        disagree_on_trace=disagree_on_trace,
    )


def _prepare(
    source_path: Path,
    *,
    manifest_path: Path | None = None,
    manifest_origin: IngestionManifestOrigin = (
        IngestionManifestOrigin.ABSENT
    ),
    mode: IngestionMode = IngestionMode.INGESTION_ONLY,
):
    provider = _provider_for_source(source_path)
    ingestor = AgenticDatasetIngestor(
        semantic_provider=provider,
        semantic_provider_fingerprint=_fingerprint("1"),
        semantic_model_profile_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
    )
    snapshot = asyncio.run(
        ingestor.prepare(
            DatasetIngestionRequest(
                source_path=source_path,
                ingestor_name="auto",
                manifest_path=manifest_path,
                manifest_origin=manifest_origin,
                mode=mode,
            )
        )
    )
    return snapshot, provider


@pytest.mark.parametrize("directory_input", [False, True])
def test_semantic_ingestor_accepts_single_or_multiple_free_form_files(
    tmp_path: Path,
    directory_input: bool,
) -> None:
    if directory_input:
        source_path = tmp_path / "domain-data"
        source_path.mkdir()
        (source_path / "trajectories.md").write_text(
            "# Harness trajectories\nA failed. B recovered.\n",
            encoding="utf-8",
        )
        (source_path / "reviews.md").write_text(
            "Human: B > A. Judge 1: A. Judge 2: B.\n",
            encoding="utf-8",
        )
    else:
        source_path = tmp_path / "domain.md"
        source_path.write_text(
            "# Combined evidence\n"
            "Harness A trajectory failed; Harness B recovered.\n"
            "Human ranking: B > A. Judge 1: A. Judge 2: B.\n",
            encoding="utf-8",
        )

    snapshot, provider = _prepare(source_path)

    assert isinstance(snapshot, FrozenSemanticIngestionSnapshotV2)
    assert len(provider.calls) == 10
    assert {stage for stage, _ in provider.calls} == {
        SelfEvolveStage.UNDERSTAND.value,
        SelfEvolveStage.EXTRACT.value,
        SelfEvolveStage.VERIFY_COVERAGE_AND_ENTAILMENT.value,
        SelfEvolveStage.SYNTHESIZE_IMPROVEMENT_SIGNALS.value,
        SelfEvolveStage.PLAN_EVALUATION.value,
    }
    assert len(
        snapshot.compiled_dataset.target_evidence_bundle.executions
    ) == 2
    assert len(snapshot.resolved_traces) == 2
    assert all(
        item.extraction_attestation is not None
        for item in snapshot.resolved_traces
    )
    assert snapshot.semantic_cases[0].case_id.startswith("case:")
    assert snapshot.improvement_signal_set.signals[
        0
    ].signal_id.startswith("signal:")
    assert snapshot.evaluation_plans[0].plan_id.startswith("plan:")
    assert snapshot.quality_report.semantic_valid_candidate_count == 2
    assert snapshot.quality_report.semantic_resolution_execution_count == 2
    assert snapshot.quality_gate.passed


def test_mixed_structured_document_routes_to_semantic_ingestion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mixed.json"
    source.write_text(
        json.dumps(
            {
                "task_id": "task-1",
                "trajectory": [{"id": "a"}],
                "human_ranking": "B > A",
                "judge_results": ["A", "B"],
            }
        ),
        encoding="utf-8",
    )

    snapshot, provider = _prepare(source)

    assert isinstance(snapshot, FrozenSemanticIngestionSnapshotV2)
    assert len(provider.calls) == 10


def test_disagreeing_or_uncited_trace_cannot_enter_target_inference(
    tmp_path: Path,
) -> None:
    source = tmp_path / "domain.md"
    source.write_text(
        "Harness A failed. Harness B recovered. Human ranking B > A.\n",
        encoding="utf-8",
    )
    provider = _provider_for_source(
        source,
        disagree_on_trace=True,
    )
    ingestor = AgenticDatasetIngestor(
        semantic_provider=provider,
        semantic_provider_fingerprint=_fingerprint("1"),
        semantic_model_profile_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
    )

    with pytest.raises(
        IngestionContractError,
        match="source-valid graph candidates|trace",
    ):
        asyncio.run(
            ingestor.prepare(
                DatasetIngestionRequest(
                    source_path=source,
                    ingestor_name="auto",
                    mode=IngestionMode.INGESTION_ONLY,
                )
            )
        )


def test_auto_verified_rejects_proposal_plan_before_optimizer_projection(
    tmp_path: Path,
) -> None:
    source = tmp_path / "domain.md"
    source.write_text(
        "Harness A failed. Harness B recovered. Human ranking B > A.\n",
        encoding="utf-8",
    )

    snapshot, _ = _prepare(
        source,
        mode=IngestionMode.AUTO_VERIFIED,
    )

    assert snapshot.quality_gate.allowed is False
    assert "semantic_trainable_plan_not_verified" in (
        snapshot.quality_gate.reason_codes
    )
    assert snapshot.quality_report.non_verified_trainable_plan_count == 1
    assert snapshot.normalized_cases[0].self_improvement_signals == ()


def test_operator_approval_and_allowlisted_qualification_reach_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "domain.md"
    source.write_text(
        "Harness A failed. Harness B recovered. Human ranking B > A.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "semantic-source.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": (
                    "aworld.self_evolve.source_manifest.v1"
                ),
                "semantics": (
                    default_semantic_ingestion_profile().to_dict()
                ),
            }
        ),
        encoding="utf-8",
    )
    first, _ = _prepare(
        source,
        manifest_path=manifest,
        manifest_origin=IngestionManifestOrigin.OPERATOR_EXPLICIT,
    )
    assert first.manifest_fingerprint is not None
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_ingestion(first)
    template_path = (
        store.ingestion_path(first.ingestion_id)
        / "evidence_approval_template.json"
    )
    assert template_path.is_file()
    template_approval = _load_human_evidence_approval(template_path)
    template_payload = json.loads(
        template_path.read_text(encoding="utf-8")
    )
    weak_approval_path = tmp_path / "weak-approval.json"
    weak_approval_payload = dict(template_payload)
    weak_approval_payload.pop("constitution_fingerprint")
    weak_approval_path.write_text(
        json.dumps(weak_approval_payload),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema drifted"):
        _load_human_evidence_approval(weak_approval_path)
    drifted_approval_path = tmp_path / "drifted-approval.json"
    drifted_approval_payload = dict(template_payload)
    drifted_approval_payload["authority"] = "self-asserted"
    drifted_approval_path.write_text(
        json.dumps(drifted_approval_payload),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema drifted"):
        _load_human_evidence_approval(drifted_approval_path)
    with pytest.raises(ValueError, match="mode does not match"):
        optimize_from_cli_request(
            workspace_root=tmp_path,
            frozen_ingestion_id=first.ingestion_id,
            source_ingestor="auto",
            apply_policy="auto_verified",
            target="skill:demo",
            infer_target=False,
        )
    approval = HumanEvidenceApprovalV1(
        evidence_graph_logical_fingerprint=(
            first.evidence_graph.logical_fingerprint
        ),
        evidence_graph_provenance_fingerprint=(
            first.evidence_graph.provenance_fingerprint
        ),
        source_bundle_fingerprint=first.source_bundle.fingerprint,
        constitution_fingerprint=first.constitution.fingerprint,
        semantic_profile_fingerprint=first.semantic_profile.fingerprint,
        manifest_fingerprint=first.manifest_fingerprint,
        approval_origin=ManifestOrigin.OPERATOR_EXPLICIT,
    )
    assert template_approval == approval
    thresholds = framework_semantic_qualification_thresholds_v1()
    report = SemanticModelQualificationReportV1(
        model_profile_fingerprint=_fingerprint("2"),
        provider_fingerprint=_fingerprint("1"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        constitution_fingerprint=first.constitution.fingerprint,
        corpus_fingerprint=(
            FRAMEWORK_SEMANTIC_QUALIFICATION_CORPUS_FINGERPRINT_V1
        ),
        threshold_set_fingerprint=(
            thresholds.threshold_set_fingerprint
        ),
        metric_values=dict(thresholds.metric_thresholds),
        required_thresholds=dict(thresholds.metric_thresholds),
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
    qualification_only = asyncio.run(
        AgenticDatasetIngestor(
            semantic_provider=_provider_for_source(source),
            semantic_provider_fingerprint=_fingerprint("1"),
            semantic_model_profile_fingerprint=_fingerprint("2"),
            semantic_protocol_fingerprint=_fingerprint("3"),
            semantic_qualification_report=report,
            semantic_qualification_registry=(
                SemanticQualificationRegistryV1(
                    trusted_report_fingerprints=(
                        report.report_fingerprint,
                    )
                )
            ),
        ).prepare(
            DatasetIngestionRequest(
                source_path=source,
                manifest_path=manifest,
                manifest_origin=(
                    IngestionManifestOrigin.OPERATOR_EXPLICIT
                ),
                mode=IngestionMode.AUTO_VERIFIED,
            )
        )
    )
    assert "supporting_evidence_not_authoritative" in (
        qualification_only.evaluation_plans[0].reason_codes
    )
    approval_only = asyncio.run(
        AgenticDatasetIngestor(
            semantic_provider=_provider_for_source(source),
            semantic_provider_fingerprint=_fingerprint("1"),
            semantic_model_profile_fingerprint=_fingerprint("2"),
            semantic_protocol_fingerprint=_fingerprint("3"),
            semantic_human_evidence_approval=approval,
        ).prepare(
            DatasetIngestionRequest(
                source_path=source,
                manifest_path=manifest,
                manifest_origin=(
                    IngestionManifestOrigin.OPERATOR_EXPLICIT
                ),
                mode=IngestionMode.AUTO_VERIFIED,
            )
        )
    )
    assert "semantic_model_not_qualified" in (
        approval_only.evaluation_plans[0].reason_codes
    )
    provider = _provider_for_source(source)
    ingestor = AgenticDatasetIngestor(
        semantic_provider=provider,
        semantic_provider_fingerprint=_fingerprint("1"),
        semantic_model_profile_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
        semantic_human_evidence_approval=approval,
        semantic_qualification_report=report,
        semantic_qualification_registry=SemanticQualificationRegistryV1(
            trusted_report_fingerprints=(report.report_fingerprint,)
        ),
    )

    verified = asyncio.run(
        ingestor.prepare(
            DatasetIngestionRequest(
                source_path=source,
                manifest_path=manifest,
                manifest_origin=(
                    IngestionManifestOrigin.OPERATOR_EXPLICIT
                ),
                mode=IngestionMode.AUTO_VERIFIED,
            )
        )
    )

    assert verified.evidence_graph.logical_fingerprint == (
        first.evidence_graph.logical_fingerprint
    )
    assert verified.ingestion_id != first.ingestion_id
    assert verified.quality_gate.allowed is True
    assert verified.quality_report.semantic_model_profile_qualified is True
    assert verified.quality_report.verified_eligible_plan_count == 1
    assert verified.evaluation_plans[0].disposition is (
        EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
    )
    promoted = promote_frozen_semantic_ingestion(
        first,
        mode=IngestionMode.AUTO_VERIFIED,
        human_approval=approval,
        qualification_report=report,
        qualification_registry=SemanticQualificationRegistryV1(
            trusted_report_fingerprints=(report.report_fingerprint,)
        ),
    )
    assert promoted.evidence_graph == first.evidence_graph
    assert promoted.ingestion_id != first.ingestion_id
    assert promoted.evidence_authority_context == (
        verified.evidence_authority_context
    )
    assert promoted.qualification_report == (
        verified.qualification_report
    )
    assert promoted.quality_gate.allowed is True
    assert promoted.ingestion_model_call_count == (
        first.ingestion_model_call_count
    )
    qualification_path = tmp_path / "qualification.json"
    qualification_path.write_text(
        json.dumps(report.to_dict()),
        encoding="utf-8",
    )
    registry_path = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "semantic_qualifications"
        / "index.json"
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            SemanticQualificationRegistryV1(
                trusted_report_fingerprints=(
                    report.report_fingerprint,
                )
            ).to_dict()
        ),
        encoding="utf-8",
    )
    promoted_from_frozen = promote_ingestion_from_cli_request(
        workspace_root=tmp_path,
        frozen_ingestion_id=first.ingestion_id,
        semantic_evidence_approval=str(template_path),
        semantic_qualification_report=str(qualification_path),
    )
    assert promoted_from_frozen.ingestion_id != first.ingestion_id
    assert promoted_from_frozen.evidence_authority_context == (
        promoted.evidence_authority_context
    )
    assert promoted_from_frozen.quality_gate.allowed is True
    assert promoted_from_frozen.ingestion_model_call_count == (
        first.ingestion_model_call_count
    )

    def fake_run_bounded(self, campaign, *, runtime_request=None):
        return {
            "campaign_id": campaign.campaign_id,
            "persisted_request": dict(campaign.request),
            "runtime_request": dict(runtime_request or {}),
        }

    monkeypatch.setattr(
        SelfImprovementCampaignController,
        "run_bounded",
        fake_run_bounded,
    )
    campaign_summary = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "apply_policy": "auto_verified",
            "frozen_ingestion_id": first.ingestion_id,
            "semantic_evidence_approval": str(template_path),
            "semantic_qualification_report": str(
                qualification_path
            ),
            "source_ingestor": "auto",
            "target": "skill:demo",
            "infer_target": False,
        },
        max_improvement_cycles=2,
    )
    persisted_request = campaign_summary["persisted_request"]
    runtime_request = campaign_summary["runtime_request"]
    assert persisted_request["frozen_ingestion_id"] != (
        first.ingestion_id
    )
    assert persisted_request["semantic_evidence_approval"] is None
    assert persisted_request["semantic_qualification_report"] is None
    assert runtime_request["frozen_ingestion_id"] == (
        persisted_request["frozen_ingestion_id"]
    )
    assert runtime_request["semantic_evidence_approval"] is None
    assert runtime_request["semantic_qualification_report"] is None
    campaign_snapshot = store.read_ingestion(
        persisted_request["frozen_ingestion_id"]
    )
    assert campaign_snapshot.quality_gate.mode is (
        IngestionMode.AUTO_VERIFIED
    )
    assert campaign_snapshot.ingestion_model_call_count == (
        first.ingestion_model_call_count
    )


def test_semantically_exhaustive_structural_schema_uses_zero_model_calls(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cases.json"
    source.write_text(
        json.dumps(
            [
                {
                    "case_id": "case-1",
                    "input": "question",
                    "expected_output": "answer",
                }
            ]
        ),
        encoding="utf-8",
    )
    provider = _provider_for_source(source)
    ingestor = AgenticDatasetIngestor(
        semantic_provider=provider,
        semantic_provider_fingerprint=_fingerprint("1"),
        semantic_model_profile_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
    )

    snapshot = asyncio.run(
        ingestor.prepare(
            DatasetIngestionRequest(
                source_path=source,
                ingestor_name="auto",
                mode=IngestionMode.INGESTION_ONLY,
            )
        )
    )

    assert isinstance(snapshot, FrozenIngestionSnapshot)
    assert provider.calls == []


def test_free_form_representations_compile_to_equivalent_logical_ir(
    tmp_path: Path,
) -> None:
    combined = tmp_path / "combined.md"
    combined.write_text(
        "Harness A failed. Harness B recovered. Human ranking B > A.\n",
        encoding="utf-8",
    )
    split = tmp_path / "split"
    split.mkdir()
    (split / "runs.md").write_text(
        "Harness A failed. Harness B recovered.\n",
        encoding="utf-8",
    )
    (split / "ranking.md").write_text(
        "Human ranking B > A.\n",
        encoding="utf-8",
    )
    structured = tmp_path / "mixed.json"
    structured.write_text(
        json.dumps(
            {
                "harness_runs": {
                    "A": {"result": "failed"},
                    "B": {"result": "recovered"},
                },
                "human_ranking": ["B", "A"],
            }
        ),
        encoding="utf-8",
    )

    snapshots = tuple(_prepare(path)[0] for path in (
        combined,
        split,
        structured,
    ))

    assert len(
        {
            item.evidence_graph.logical_fingerprint
            for item in snapshots
        }
    ) == 1
    assert len(
        {
            item.improvement_signal_set.fingerprint
            for item in snapshots
        }
    ) == 1
    assert len(
        {
            item.normalized_dataset_fingerprint
            for item in snapshots
        }
    ) == 1
    assert len(
        {
            item.compiled_dataset.evaluation_plan_bundle_fingerprint
            for item in snapshots
        }
    ) == 1
    assert len(
        {
            item.compiled_dataset.target_evidence_bundle.fingerprint
            for item in snapshots
        }
    ) == 1
    assert len(
        {
            item.evidence_graph.provenance_fingerprint
            for item in snapshots
        }
    ) == 3


def test_semantic_only_explicit_manifest_routes_to_semantic_ingestion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "domain.md"
    source.write_text("Harness A failed; Harness B recovered.\n")
    manifest = tmp_path / "semantic-source.yaml"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": (
                    "aworld.self_evolve.source_manifest.v1"
                ),
                "semantics": (
                    default_semantic_ingestion_profile().to_dict()
                ),
            }
        ),
        encoding="utf-8",
    )

    snapshot, provider = _prepare(
        source,
        manifest_path=manifest,
        manifest_origin=(
            IngestionManifestOrigin.OPERATOR_EXPLICIT
        ),
    )

    assert isinstance(snapshot, FrozenSemanticIngestionSnapshotV2)
    assert snapshot.manifest_origin is (
        IngestionManifestOrigin.OPERATOR_EXPLICIT
    )
    assert snapshot.manifest_fingerprint is not None
    assert len(provider.calls) == 10


def test_runner_ingestion_only_reports_frozen_semantic_artifacts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "domain.md"
    source.write_text(
        "Harness A failed. Harness B recovered. Human ranking B > A.\n",
        encoding="utf-8",
    )
    provider = _provider_for_source(source)
    ingestor = AgenticDatasetIngestor(
        semantic_provider=provider,
        semantic_provider_fingerprint=_fingerprint("1"),
        semantic_model_profile_fingerprint=_fingerprint("2"),
        semantic_protocol_fingerprint=_fingerprint("3"),
    )
    registry = IngestionRegistry(
        ingestors=(ingestor,),
        extractors=builtin_extractors(),
    )

    summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        from_source=str(source),
        ingestion_only=True,
        infer_target=True,
        ingestion_registry=registry,
    )

    assert summary["status"] == "ingested"
    assert summary["normalization_kind"] == "semantic_evidence"
    assert summary["semantic_entity_count"] >= 4
    assert summary["semantic_claim_count"] >= 6
    assert summary["semantic_signal_count"] == 1
    assert summary["gate_results"][0]["gate_name"] == (
        "dataset_ingestion"
    )
    assert summary["gate_results"][0]["passed"] is True
    assert summary["manifest_origin"] == "absent"
    assert len(provider.calls) == 10

    rerun = optimize_from_cli_request(
        workspace_root=tmp_path,
        frozen_ingestion_id=summary["ingestion_id"],
        source_ingestor="auto",
        ingestion_only=True,
        infer_target=True,
        ingestion_registry=registry,
    )

    assert rerun["ingestion_id"] == summary["ingestion_id"]
    assert rerun["normalization_kind"] == "semantic_evidence"
    assert len(provider.calls) == 10

    campaign = SelfImprovementCampaignController(
        workspace_root=tmp_path
    ).create(
        {
            "apply_policy": "auto_verified",
            "frozen_ingestion_id": summary["ingestion_id"],
            "source_ingestor": "auto",
            "target": "skill:demo",
            "infer_target": False,
        },
        max_cycles=1,
    )

    assert campaign.source_snapshot["normalization_kind"] == (
        "semantic_evidence"
    )
    assert campaign.source_snapshot[
        "evidence_graph_logical_fingerprint"
    ] == summary["evidence_graph_logical_fingerprint"]
    assert campaign.source_snapshot["mapping_fingerprint"] is None
    assert len(provider.calls) == 10
