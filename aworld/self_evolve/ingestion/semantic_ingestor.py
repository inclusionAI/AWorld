from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Mapping, Sequence

from aworld.self_evolve.constitution import (
    AgenticRole,
    AgenticStageReportV1,
    AgenticStageStatus,
    SemanticRolloutPolicyV1,
    SemanticRolloutStage,
    SelfEvolveConstitutionV1,
    SelfEvolveStage,
    default_self_evolve_constitution,
)
from aworld.self_evolve.evaluation_plan import (
    EvidenceAuthorityContextV1,
    EvaluationDisposition,
    ManifestOrigin,
    SemanticIngestionProfileV1,
    SemanticModelQualificationReportV1,
    SemanticQualificationRegistryV1,
    SelfImprovementEvaluationPlanV1,
    compile_evaluation_plan,
    default_semantic_ingestion_profile,
    effective_profile_for_origin,
    issue_evidence_authority_context,
)
from aworld.self_evolve.evidence import (
    EvidenceClaimKind,
    SelfImprovementCaseV1,
    SelfImprovementEvidenceGraphV1,
    authoritative_verification_registry_fingerprint,
)
from aworld.self_evolve.improvement_signals import (
    DatasetSplit,
    SelfImprovementSignalV1,
    SelfImprovementSignalSetV1,
)

from .chunking import SourceBundleV1, build_source_bundle
from .mapping import SourceManifest
from .semantic_compiler import (
    ResolvedSemanticTraceV1,
    TraceCandidateAttestationV1,
    attest_resolved_trace,
    canonical_semantic_case_id,
    canonical_semantic_plan_id,
    canonical_semantic_signal_id,
    compile_semantic_dataset,
    compile_target_evidence_bundle,
)
from .semantic_resolver import (
    canonicalize_evidence_graph,
    resolve_evidence_graph_deterministically,
)
from .semantic_snapshot import FrozenSemanticIngestionSnapshotV2
from .semantic_verifier import (
    SemanticQualificationEvidenceV1,
    SemanticResolutionEvidenceV1,
    build_semantic_evidence_quality_report,
    evaluate_semantic_quality_gate,
)
from .semantic_workflow import (
    BoundedSemanticStageExecutor,
    SemanticAgentBindingV1,
    SemanticAgentCandidateV1,
    SemanticProvider,
    SemanticStageDecisionV1,
    validate_evidence_graph_against_source_bundle,
)
from .types import (
    DatasetIngestionRequest,
    IngestionContractError,
    IngestionManifestOrigin,
    IngestionMode,
    IngestorTrustLevel,
    SourceInventory,
    fingerprint_json,
)


SEMANTIC_INGESTOR_PROTOCOL_FINGERPRINT = fingerprint_json(
    {
        "schema_version": (
            "aworld.self_evolve.semantic_ingestor_protocol.v2"
        ),
        "candidate_population": 2,
        "deterministic_resolution_executions": 2,
        "trace_consensus_attestation": (
            "source_bound_independent_population_v1"
        ),
        "canonical_semantic_identity": (
            "case_signal_plan_framework_derived_v1"
        ),
    }
)
DEFAULT_QUALIFICATION_CORPUS_FINGERPRINT = fingerprint_json(
    {
        "schema_version": (
            "aworld.self_evolve.semantic_qualification_corpus.v1"
        )
    }
)
DEFAULT_QUALIFICATION_THRESHOLD_SET_FINGERPRINT = fingerprint_json(
    {
        "schema_version": (
            "aworld.self_evolve.semantic_qualification_thresholds.v1"
        )
    }
)


class SemanticSelfImprovementIngestor:
    """Constitution-driven semantic ingestion with deterministic transitions."""

    def __init__(
        self,
        *,
        provider: SemanticProvider,
        provider_fingerprint: str,
        model_profile_fingerprint: str,
        protocol_fingerprint: str = (
            SEMANTIC_INGESTOR_PROTOCOL_FINGERPRINT
        ),
        constitution: SelfEvolveConstitutionV1 | None = None,
        qualification_report: (
            SemanticModelQualificationReportV1 | None
        ) = None,
        qualification_registry: (
            SemanticQualificationRegistryV1 | None
        ) = None,
        qualification_corpus_fingerprint: str = (
            DEFAULT_QUALIFICATION_CORPUS_FINGERPRINT
        ),
        qualification_threshold_set_fingerprint: str = (
            DEFAULT_QUALIFICATION_THRESHOLD_SET_FINGERPRINT
        ),
        timeout_seconds: float = 60.0,
    ) -> None:
        self.provider = provider
        self.provider_fingerprint = provider_fingerprint
        self.model_profile_fingerprint = model_profile_fingerprint
        self.protocol_fingerprint = protocol_fingerprint
        self.constitution = (
            constitution or default_self_evolve_constitution()
        )
        self.qualification_report = qualification_report
        self.qualification_registry = (
            qualification_registry
            or SemanticQualificationRegistryV1(
                trusted_report_fingerprints=()
            )
        )
        self.qualification_corpus_fingerprint = (
            qualification_corpus_fingerprint
        )
        self.qualification_threshold_set_fingerprint = (
            qualification_threshold_set_fingerprint
        )
        self.executor = BoundedSemanticStageExecutor(
            self.constitution,
            timeout_seconds=timeout_seconds,
        )

    async def prepare(
        self,
        request: DatasetIngestionRequest,
        *,
        inventory: SourceInventory,
        manifest: SourceManifest | None,
        manifest_origin: IngestionManifestOrigin,
        extractor_fingerprints: Sequence[str],
        ingestor_name: str,
        ingestor_version: str,
        trust_level: IngestorTrustLevel,
    ) -> FrozenSemanticIngestionSnapshotV2:
        bundle = build_source_bundle(
            request.source_path,
            inventory=inventory,
            ingestion_limits=request.limits,
        )
        requested_profile = (
            manifest.semantic_profile
            if manifest is not None
            and manifest.semantic_profile is not None
            else default_semantic_ingestion_profile()
        )
        profile = effective_profile_for_origin(
            requested_profile,
            manifest_origin=ManifestOrigin(
                manifest_origin.value
            ),
        )
        manifest_fingerprint = (
            manifest.fingerprint if manifest is not None else None
        )
        qualification_evidence = SemanticQualificationEvidenceV1(
            registry=self.qualification_registry,
            report=self.qualification_report,
            model_profile_fingerprint=(
                self.model_profile_fingerprint
            ),
            provider_fingerprint=self.provider_fingerprint,
            semantic_protocol_fingerprint=self.protocol_fingerprint,
            constitution_fingerprint=self.constitution.fingerprint,
            corpus_fingerprint=(
                self.qualification_corpus_fingerprint
            ),
            threshold_set_fingerprint=(
                self.qualification_threshold_set_fingerprint
            ),
        )
        rollout_policy = _rollout_policy(request.mode)
        ingestion_id = FrozenSemanticIngestionSnapshotV2.identity_for(
            inventory_fingerprint=inventory.source_root_fingerprint,
            source_bundle_fingerprint=bundle.fingerprint,
            constitution_fingerprint=self.constitution.fingerprint,
            rollout_policy_fingerprint=rollout_policy.fingerprint,
            semantic_profile_fingerprint=profile.fingerprint,
            manifest_fingerprint=manifest_fingerprint,
            manifest_origin=manifest_origin,
            extractor_fingerprints=extractor_fingerprints,
            semantic_model_profile_fingerprint=(
                self.model_profile_fingerprint
            ),
            semantic_provider_fingerprint=(
                self.provider_fingerprint
            ),
            semantic_protocol_fingerprint=(
                self.protocol_fingerprint
            ),
            qualification_report_fingerprint=(
                self.qualification_report.report_fingerprint
                if self.qualification_report is not None
                else None
            ),
            ingestor_name=ingestor_name,
            ingestor_version=ingestor_version,
            trust_level=trust_level,
        )

        reports = [
            _deterministic_stage_report(
                self.constitution,
                stage=SelfEvolveStage.DISCOVER,
                inputs=(inventory.source_root_fingerprint,),
                outputs=(bundle.fingerprint,),
            )
        ]
        understanding = await self.executor.execute(
            SelfEvolveStage.UNDERSTAND,
            input_fingerprints=(bundle.fingerprint,),
            source_data={
                "source_bundle": bundle.private_prompt_projection(),
            },
            bindings=self._bindings(
                AgenticRole.SOURCE_UNDERSTANDING,
                AgenticRole.SOURCE_UNDERSTANDING,
            ),
            validator=_generic_stage_validator(
                SelfEvolveStage.UNDERSTAND,
                self.constitution,
            ),
            profile_public_projection=profile.to_dict(),
        )
        reports.extend(understanding.reports)
        understanding_fingerprint = (
            understanding.decision.output_fingerprints[0]
        )

        extraction = await self.executor.execute(
            SelfEvolveStage.EXTRACT,
            input_fingerprints=(understanding_fingerprint,),
            source_data={
                "source_bundle": bundle.private_prompt_projection(),
                "source_understanding_candidates": [
                    item.to_dict()
                    for item in understanding.candidates
                ],
            },
            bindings=self._bindings(
                AgenticRole.EVIDENCE_EXTRACTION,
                AgenticRole.EVIDENCE_EXTRACTION,
            ),
            validator=_generic_stage_validator(
                SelfEvolveStage.EXTRACT,
                self.constitution,
            ),
            profile_public_projection=profile.to_dict(),
        )
        reports.extend(extraction.reports)
        extraction_fingerprint = (
            extraction.decision.output_fingerprints[0]
        )

        verified_graphs: list[
            tuple[
                SelfImprovementEvidenceGraphV1,
                tuple[ResolvedSemanticTraceV1, ...],
                SemanticAgentCandidateV1,
            ]
        ] = []

        def verify_candidates(
            candidates: Sequence[SemanticAgentCandidateV1],
        ) -> SemanticStageDecisionV1:
            verified_graphs.clear()
            for candidate in candidates:
                payload = _candidate_payload(candidate)
                graph_payload = _mapping(
                    payload.get("evidence_graph"),
                    "evidence_graph",
                )
                graph = canonicalize_evidence_graph(
                    SelfImprovementEvidenceGraphV1.from_agent_dict(
                        graph_payload
                    ),
                    profile=profile,
                )
                validation = (
                    validate_evidence_graph_against_source_bundle(
                        bundle,
                        graph,
                    )
                )
                if not validation.valid:
                    continue
                traces = tuple(
                    ResolvedSemanticTraceV1.from_agent_dict(
                        _mapping(item, "resolved_trace")
                    )
                    for item in _sequence(
                        payload.get("resolved_traces", ()),
                        "resolved_traces",
                    )
                )
                _validate_trace_drafts(graph, traces)
                verified_graphs.append(
                    (graph, traces, candidate)
                )
            if len(verified_graphs) < 2:
                raise IngestionContractError(
                    "semantic_candidate_count_insufficient",
                    "verification requires two source-valid graph candidates",
                )
            selected = min(
                verified_graphs,
                key=lambda item: (
                    item[0].logical_fingerprint,
                    item[0].provenance_fingerprint,
                    item[2].candidate_id,
                ),
            )
            return _complete_decision(
                SelfEvolveStage.VERIFY_COVERAGE_AND_ENTAILMENT,
                candidates=tuple(
                    item[2] for item in verified_graphs
                ),
                outputs=(
                    selected[0].provenance_fingerprint,
                ),
                constitution=self.constitution,
            )

        verification = await self.executor.execute(
            SelfEvolveStage.VERIFY_COVERAGE_AND_ENTAILMENT,
            input_fingerprints=(extraction_fingerprint,),
            source_data={
                "source_bundle": bundle.private_prompt_projection(),
                "evidence_candidates": [
                    item.to_dict() for item in extraction.candidates
                ],
            },
            bindings=self._bindings(
                AgenticRole.COVERAGE_AUDIT,
                AgenticRole.ENTAILMENT_VERIFICATION,
            ),
            validator=verify_candidates,
            profile_public_projection=profile.to_dict(),
        )
        reports.extend(verification.reports)
        selected_graph, selected_trace_drafts, _ = min(
            verified_graphs,
            key=lambda item: (
                item[0].logical_fingerprint,
                item[0].provenance_fingerprint,
                item[2].candidate_id,
            ),
        )
        resolved_traces = _attest_consensus_traces(
            selected_graph=selected_graph,
            selected_trace_drafts=selected_trace_drafts,
            source_bundle=bundle,
            verified_graphs=verified_graphs,
            extractor_fingerprints=extractor_fingerprints,
        )
        graph, deterministic_match = (
            resolve_evidence_graph_deterministically(
                selected_graph,
                profile=profile,
            )
        )
        if not deterministic_match:
            raise IngestionContractError(
                "semantic_resolution_nondeterministic",
                "semantic resolver produced different outputs",
            )
        resolution_evidence = SemanticResolutionEvidenceV1(
            candidate_graphs=tuple(
                item[0] for item in verified_graphs
            ),
            resolver_output_fingerprints=(
                graph.logical_fingerprint,
                graph.logical_fingerprint,
            ),
        )
        reports.append(
            _deterministic_stage_report(
                self.constitution,
                stage=SelfEvolveStage.RESOLVE_AND_DETECT_CONFLICT,
                inputs=(selected_graph.provenance_fingerprint,),
                outputs=(graph.logical_fingerprint,),
            )
        )

        synthesized: list[
            tuple[
                tuple[SelfImprovementCaseV1, ...],
                SelfImprovementSignalSetV1,
                SemanticAgentCandidateV1,
                Mapping[str, str],
                Mapping[str, str],
            ]
        ] = []

        def validate_signals(
            candidates: Sequence[SemanticAgentCandidateV1],
        ) -> SemanticStageDecisionV1:
            synthesized.clear()
            for candidate in candidates:
                payload = _candidate_payload(candidate)
                cases = tuple(
                    SelfImprovementCaseV1.from_dict(
                        _mapping(item, "semantic_case")
                    )
                    for item in _sequence(
                        payload.get("semantic_cases", ()),
                        "semantic_cases",
                    )
                )
                signal_set = SelfImprovementSignalSetV1.from_dict(
                    _mapping(
                        payload.get("improvement_signal_set"),
                        "improvement_signal_set",
                    )
                )
                (
                    cases,
                    signal_set,
                    case_aliases,
                    signal_aliases,
                ) = _canonicalize_cases_and_signals(
                    cases,
                    signal_set,
                    graph=graph,
                )
                signal_set = replace(
                    signal_set,
                    case_splits=_framework_case_splits(
                        tuple(item.case_id for item in cases)
                    ),
                    evidence_graph_logical_fingerprint=(
                        graph.logical_fingerprint
                    ),
                )
                for case in cases:
                    case.validate_against(graph)
                for signal in signal_set.signals:
                    case = next(
                        (
                            item
                            for item in cases
                            if item.case_id == signal.case_id
                        ),
                        None,
                    )
                    if case is None:
                        raise IngestionContractError(
                            "dangling_case_reference",
                            "signal references an unknown semantic case",
                        )
                    signal.validate_against(graph, case)
                synthesized.append(
                    (
                        cases,
                        signal_set,
                        candidate,
                        case_aliases,
                        signal_aliases,
                    )
                )
            if len(synthesized) < 2:
                raise IngestionContractError(
                    "semantic_candidate_count_insufficient",
                    "signal synthesis requires two valid candidates",
                )
            selected_cases, selected_signals, _, _, _ = min(
                synthesized,
                key=lambda item: (
                    fingerprint_json(
                        [
                            case.to_dict()
                            for case in item[0]
                        ]
                    ),
                    item[1].fingerprint,
                    item[2].candidate_id,
                ),
            )
            target = compile_target_evidence_bundle(
                graph,
                selected_cases,
                resolved_traces={
                    item.trace_ref: item
                    for item in resolved_traces
                },
                require_trace_attestation=True,
            )
            return _complete_decision(
                SelfEvolveStage.SYNTHESIZE_IMPROVEMENT_SIGNALS,
                candidates=tuple(
                    item[2] for item in synthesized
                ),
                outputs=(
                    selected_signals.fingerprint,
                    target.fingerprint,
                ),
                constitution=self.constitution,
            )

        synthesis = await self.executor.execute(
            SelfEvolveStage.SYNTHESIZE_IMPROVEMENT_SIGNALS,
            input_fingerprints=(graph.logical_fingerprint,),
            source_data={
                "evidence_graph": graph.to_dict(),
            },
            bindings=self._bindings(
                AgenticRole.SIGNAL_SYNTHESIS,
                AgenticRole.SIGNAL_CRITIC,
            ),
            validator=validate_signals,
            profile_public_projection=profile.to_dict(),
        )
        reports.extend(synthesis.reports)
        (
            semantic_cases,
            signal_set,
            _,
            case_aliases,
            signal_aliases,
        ) = min(
            synthesized,
            key=lambda item: (
                fingerprint_json(
                    [case.to_dict() for case in item[0]]
                ),
                item[1].fingerprint,
                item[2].candidate_id,
            ),
        )
        authority_context = issue_evidence_authority_context(graph)
        planned: list[
            tuple[
                tuple[SelfImprovementEvaluationPlanV1, ...],
                SemanticAgentCandidateV1,
            ]
        ] = []

        def validate_plans(
            candidates: Sequence[SemanticAgentCandidateV1],
        ) -> SemanticStageDecisionV1:
            planned.clear()
            cases_by_id = {
                item.case_id: item for item in semantic_cases
            }
            for candidate in candidates:
                payload = _candidate_payload(candidate)
                proposals = tuple(
                    _remap_plan_proposal(
                        SelfImprovementEvaluationPlanV1.from_dict(
                            _mapping(item, "evaluation_plan")
                        ),
                        case_aliases=case_aliases,
                        signal_aliases=signal_aliases,
                    )
                    for item in _sequence(
                        payload.get("evaluation_plans", ()),
                        "evaluation_plans",
                    )
                )
                if {
                    item.case_id for item in proposals
                } != set(cases_by_id):
                    raise IngestionContractError(
                        "evaluation_plan_coverage_incomplete",
                        "planner must cover every semantic case",
                    )
                compiled_plans = tuple(
                    _compile_plan(
                        proposal,
                        profile=profile,
                        manifest_origin=manifest_origin,
                        manifest_fingerprint=manifest_fingerprint,
                        graph=graph,
                        case=cases_by_id[proposal.case_id],
                        signal_set=signal_set,
                        authority_context=authority_context,
                        qualification_evidence=(
                            qualification_evidence
                        ),
                    )
                    for proposal in proposals
                )
                planned.append((compiled_plans, candidate))
            if len(planned) < 2:
                raise IngestionContractError(
                    "semantic_candidate_count_insufficient",
                    "evaluation planning requires two valid candidates",
                )
            selected_plans, _ = min(
                planned,
                key=lambda item: (
                    _plan_bundle_fingerprint(item[0]),
                    item[1].candidate_id,
                ),
            )
            return _complete_decision(
                SelfEvolveStage.PLAN_EVALUATION,
                candidates=tuple(item[1] for item in planned),
                outputs=(
                    _plan_bundle_fingerprint(selected_plans),
                ),
                constitution=self.constitution,
            )

        planning = await self.executor.execute(
            SelfEvolveStage.PLAN_EVALUATION,
            input_fingerprints=(signal_set.fingerprint,),
            source_data={
                "evidence_graph_logical_fingerprint": (
                    graph.logical_fingerprint
                ),
                "semantic_cases": [
                    item.to_dict() for item in semantic_cases
                ],
                "improvement_signal_set": signal_set.to_dict(),
            },
            bindings=self._bindings(
                AgenticRole.EVALUATION_PLANNING,
                AgenticRole.EVALUATION_PLANNING,
            ),
            validator=validate_plans,
            profile_public_projection=profile.to_dict(),
        )
        reports.extend(planning.reports)
        evaluation_plans, _ = min(
            planned,
            key=lambda item: (
                _plan_bundle_fingerprint(item[0]),
                item[1].candidate_id,
            ),
        )
        compiled_dataset = compile_semantic_dataset(
            graph=graph,
            cases=semantic_cases,
            signal_set=signal_set,
            evaluation_plans=evaluation_plans,
            resolved_traces={
                item.trace_ref: item for item in resolved_traces
            },
            ingestion_id=ingestion_id,
            authority_context=authority_context,
            manifest_origin=ManifestOrigin(
                manifest_origin.value
            ),
            manifest_fingerprint=manifest_fingerprint,
            verified_only_signal_projection=(
                request.mode is IngestionMode.AUTO_VERIFIED
            ),
            require_trace_attestation=True,
        )
        quality = build_semantic_evidence_quality_report(
            bundle=bundle,
            graph=graph,
            constitution=self.constitution,
            stage_reports=reports,
            signal_set=signal_set,
            semantic_cases=semantic_cases,
            evaluation_plans=evaluation_plans,
            compiled_dataset=compiled_dataset,
            resolution_evidence=resolution_evidence,
            qualification_evidence=qualification_evidence,
        )
        gate = evaluate_semantic_quality_gate(
            quality,
            mode=request.mode,
        )
        authoritative_ids = tuple(
            item.verification_id
            for item in graph.claim_verifications
            if item.is_authoritative_origin
        )
        return FrozenSemanticIngestionSnapshotV2(
            ingestion_id=ingestion_id,
            inventory=inventory,
            source_bundle=bundle,
            constitution=self.constitution,
            rollout_policy=rollout_policy,
            semantic_profile=profile,
            stage_reports=tuple(reports),
            evidence_graph=graph,
            evidence_authority_context=authority_context,
            semantic_cases=semantic_cases,
            improvement_signal_set=signal_set,
            evaluation_plans=evaluation_plans,
            resolved_traces=resolved_traces,
            compiled_dataset=compiled_dataset,
            quality_report=quality,
            quality_gate=gate,
            resolution_evidence=resolution_evidence,
            authoritative_verification_ids=authoritative_ids,
            verification_registry_fingerprint=(
                authoritative_verification_registry_fingerprint(
                    graph,
                    authoritative_ids,
                )
            ),
            semantic_model_profile_fingerprint=(
                self.model_profile_fingerprint
            ),
            semantic_provider_fingerprint=(
                self.provider_fingerprint
            ),
            semantic_protocol_fingerprint=(
                self.protocol_fingerprint
            ),
            qualification_registry=self.qualification_registry,
            qualification_corpus_fingerprint=(
                self.qualification_corpus_fingerprint
            ),
            qualification_threshold_set_fingerprint=(
                self.qualification_threshold_set_fingerprint
            ),
            qualification_report=self.qualification_report,
            manifest_fingerprint=manifest_fingerprint,
            source_manifest=(
                manifest.to_dict() if manifest is not None else None
            ),
            manifest_origin=manifest_origin,
            extractor_fingerprints=tuple(
                sorted(extractor_fingerprints)
            ),
            ingestor_name=ingestor_name,
            ingestor_version=ingestor_version,
            ingestor_trust_level=trust_level,
        )

    def _bindings(
        self,
        first_role: AgenticRole,
        second_role: AgenticRole,
    ) -> tuple[SemanticAgentBindingV1, ...]:
        return tuple(
            SemanticAgentBindingV1(
                role=role,
                provider_fingerprint=self.provider_fingerprint,
                model_fingerprint=self.model_profile_fingerprint,
                protocol_fingerprint=self.protocol_fingerprint,
                independence_group=f"semantic-independent-{index}",
                provider=self.provider,
            )
            for index, role in enumerate(
                (first_role, second_role),
                start=1,
            )
        )


def _validate_trace_drafts(
    graph: SelfImprovementEvidenceGraphV1,
    traces: Sequence[ResolvedSemanticTraceV1],
) -> None:
    by_ref = {item.trace_ref: item for item in traces}
    if len(by_ref) != len(traces):
        raise IngestionContractError(
            "duplicate_identity",
            "semantic trace refs must be unique within a candidate",
        )
    trajectory_claims = [
        item
        for item in graph.claims
        if item.kind is EvidenceClaimKind.EXECUTION_TRAJECTORY
    ]
    expected_refs = {
        str(item.payload["trace_ref"]) for item in trajectory_claims
    }
    if set(by_ref) != expected_refs:
        raise IngestionContractError(
            "semantic_trace_resolution_incomplete",
            "candidate traces must cover every trajectory claim exactly",
        )
    for claim in trajectory_claims:
        trace = by_ref[str(claim.payload["trace_ref"])]
        if trace.trace_fingerprint != claim.payload["trace_fingerprint"]:
            raise IngestionContractError(
                "semantic_trace_claim_mismatch",
                "candidate trace differs from its trajectory claim",
            )
        if trace.extraction_attestation is not None:
            raise IngestionContractError(
                "semantic_trace_attestation_agent_controlled",
                "candidate cannot supply a framework trace attestation",
            )


def _attest_consensus_traces(
    *,
    selected_graph: SelfImprovementEvidenceGraphV1,
    selected_trace_drafts: Sequence[ResolvedSemanticTraceV1],
    source_bundle: SourceBundleV1,
    verified_graphs: Sequence[
        tuple[
            SelfImprovementEvidenceGraphV1,
            tuple[ResolvedSemanticTraceV1, ...],
            SemanticAgentCandidateV1,
        ]
    ],
    extractor_fingerprints: Sequence[str],
) -> tuple[ResolvedSemanticTraceV1, ...]:
    selected_by_ref = {
        item.trace_ref: item for item in selected_trace_drafts
    }
    selected_claims = [
        item
        for item in selected_graph.claims
        if item.kind is EvidenceClaimKind.EXECUTION_TRAJECTORY
    ]
    attested: list[ResolvedSemanticTraceV1] = []
    for selected_claim in sorted(
        selected_claims,
        key=lambda item: item.claim_id,
    ):
        selected_ref = str(selected_claim.payload["trace_ref"])
        selected_trace = selected_by_ref[selected_ref]
        selected_source_identity = _trajectory_source_identity(
            selected_graph,
            selected_claim,
        )
        candidate_attestations: list[
            TraceCandidateAttestationV1
        ] = []
        for candidate_graph, candidate_traces, candidate in (
            verified_graphs
        ):
            matching_claims = [
                item
                for item in candidate_graph.claims
                if (
                    item.kind
                    is EvidenceClaimKind.EXECUTION_TRAJECTORY
                    and item.subject_entity_ids
                    == selected_claim.subject_entity_ids
                    and item.payload.get("trace_fingerprint")
                    == selected_trace.trace_fingerprint
                    and _trajectory_source_identity(
                        candidate_graph,
                        item,
                    )
                    == selected_source_identity
                )
            ]
            if len(matching_claims) != 1:
                continue
            candidate_claim = matching_claims[0]
            candidate_ref = str(
                candidate_claim.payload["trace_ref"]
            )
            matching_traces = [
                item
                for item in candidate_traces
                if (
                    item.trace_ref == candidate_ref
                    and item.trace_fingerprint
                    == selected_trace.trace_fingerprint
                )
            ]
            if len(matching_traces) != 1:
                continue
            candidate_attestations.append(
                TraceCandidateAttestationV1(
                    candidate_fingerprint=candidate.fingerprint,
                    provider_fingerprint=(
                        candidate.provider_fingerprint
                    ),
                    model_fingerprint=candidate.model_fingerprint,
                    protocol_fingerprint=(
                        candidate.protocol_fingerprint
                    ),
                    independence_group=candidate.independence_group,
                )
            )
        if len(candidate_attestations) < 2:
            raise IngestionContractError(
                "semantic_trace_consensus_insufficient",
                "trajectory must be independently extracted from the same source evidence",
            )
        attested.append(
            attest_resolved_trace(
                selected_trace,
                graph=selected_graph,
                trajectory_claim_id=selected_claim.claim_id,
                source_bundle=source_bundle,
                candidate_attestations=candidate_attestations,
                extractor_fingerprints=extractor_fingerprints,
            )
        )
    return tuple(attested)


def _trajectory_source_identity(
    graph: SelfImprovementEvidenceGraphV1,
    claim: Any,
) -> tuple[tuple[str, int, int, str], ...]:
    spans = {item.span_id: item for item in graph.spans}
    return tuple(
        sorted(
            (
                spans[span_id].asset_id,
                spans[span_id].byte_start,
                spans[span_id].byte_end,
                spans[span_id].content_fingerprint,
            )
            for span_id in claim.source_span_ids
        )
    )


def _rollout_policy(mode: IngestionMode) -> SemanticRolloutPolicyV1:
    stage = {
        IngestionMode.INGESTION_ONLY: SemanticRolloutStage.SHADOW,
        IngestionMode.PROPOSAL: SemanticRolloutStage.PROPOSAL,
        IngestionMode.AUTO_VERIFIED: SemanticRolloutStage.VERIFIED,
    }[IngestionMode(mode)]
    return SemanticRolloutPolicyV1(
        policy_id=f"semantic-rollout-{stage.value}",
        enabled_stage=stage,
    )


def _deterministic_stage_report(
    constitution: SelfEvolveConstitutionV1,
    *,
    stage: SelfEvolveStage,
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
) -> AgenticStageReportV1:
    contract = constitution.contract_for(stage)
    digest = hashlib.sha256(
        repr((stage.value, inputs, outputs)).encode("utf-8")
    ).hexdigest()
    stage_order = tuple(SelfEvolveStage)
    stage_index = stage_order.index(stage)
    return AgenticStageReportV1(
        report_id=f"stage-report:{digest}",
        stage=stage,
        input_fingerprints=inputs,
        output_fingerprints=outputs,
        agent_role=contract.allowed_roles[0],
        provider_fingerprint=fingerprint_json(
            {"provider": "framework"}
        ),
        model_fingerprint=fingerprint_json(
            {"model": "deterministic"}
        ),
        protocol_fingerprint=fingerprint_json(
            {"protocol": "semantic-control-plane-v1"}
        ),
        independence_group=f"deterministic-{stage.value}",
        attempt_count=1,
        status=AgenticStageStatus.COMPLETE,
        next_stage_proposal=(
            stage_order[stage_index + 1]
            if stage_index + 1 < len(stage_order)
            else None
        ),
        input_schema_versions=contract.required_input_schemas,
        output_schema_versions=contract.required_output_schemas,
        model_call_count=0,
        source_bytes_consumed=0,
        token_count=0,
    )


def _generic_stage_validator(
    stage: SelfEvolveStage,
    constitution: SelfEvolveConstitutionV1,
):
    def validate(
        candidates: Sequence[SemanticAgentCandidateV1],
    ) -> SemanticStageDecisionV1:
        return _complete_decision(
            stage,
            candidates=candidates,
            outputs=(
                fingerprint_json(
                    sorted(item.fingerprint for item in candidates)
                ),
            ),
            constitution=constitution,
        )

    return validate


def _complete_decision(
    stage: SelfEvolveStage,
    *,
    candidates: Sequence[SemanticAgentCandidateV1],
    outputs: tuple[str, ...],
    constitution: SelfEvolveConstitutionV1,
) -> SemanticStageDecisionV1:
    return SemanticStageDecisionV1(
        stage=stage,
        accepted_candidate_ids=tuple(
            item.candidate_id for item in candidates
        ),
        output_fingerprints=outputs,
        output_schema_versions=(
            constitution.contract_for(stage).required_output_schemas
        ),
        status=AgenticStageStatus.COMPLETE,
    )


def _candidate_payload(
    candidate: SemanticAgentCandidateV1,
) -> Mapping[str, Any]:
    payload = candidate.to_dict()["payload"]
    if not isinstance(payload, Mapping):
        raise IngestionContractError(
            "semantic_candidate_invalid",
            "candidate payload must be an object",
        )
    return payload


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
        held_out: set[str] = set()
    elif count == 2:
        train = {ordered[0]}
        validation = set()
        held_out = {ordered[1]}
    else:
        held_out_count = max(1, count // 5)
        validation_count = max(1, count // 5)
        train_count = count - held_out_count - validation_count
        train = set(ordered[:train_count])
        validation = set(
            ordered[train_count : train_count + validation_count]
        )
        held_out = set(
            ordered[train_count + validation_count :]
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


def _canonicalize_cases_and_signals(
    cases: Sequence[SelfImprovementCaseV1],
    signal_set: SelfImprovementSignalSetV1,
    *,
    graph: SelfImprovementEvidenceGraphV1,
) -> tuple[
    tuple[SelfImprovementCaseV1, ...],
    SelfImprovementSignalSetV1,
    Mapping[str, str],
    Mapping[str, str],
]:
    case_aliases: dict[str, str] = {}
    canonical_cases: list[SelfImprovementCaseV1] = []
    for case in cases:
        case.validate_against(graph)
        canonical_id = canonical_semantic_case_id(
            case,
            graph=graph,
        )
        case_aliases[case.case_id] = canonical_id
        case_aliases[canonical_id] = canonical_id
        canonical_cases.append(
            replace(
                case,
                case_id=canonical_id,
                trainable_signal_projection={},
            )
        )
    if len({item.case_id for item in canonical_cases}) != len(
        canonical_cases
    ):
        raise IngestionContractError(
            "duplicate_identity",
            "semantic cases collapse to the same canonical identity",
        )

    signal_aliases: dict[str, str] = {}
    canonical_signals: dict[str, SelfImprovementSignalV1] = {}
    for signal in signal_set.signals:
        canonical_case_id = case_aliases.get(signal.case_id)
        if canonical_case_id is None:
            raise IngestionContractError(
                "dangling_case_reference",
                "signal references an unknown semantic case",
            )
        remapped = replace(signal, case_id=canonical_case_id)
        canonical_signal_id = canonical_semantic_signal_id(remapped)
        canonical = replace(
            remapped,
            signal_id=canonical_signal_id,
        )
        existing = canonical_signals.get(canonical_signal_id)
        if existing is not None and existing != canonical:
            raise IngestionContractError(
                "duplicate_identity",
                "canonical signal identity has conflicting payloads",
            )
        canonical_signals[canonical_signal_id] = canonical
        signal_aliases[signal.signal_id] = canonical_signal_id
        signal_aliases[canonical_signal_id] = canonical_signal_id

    remapped_splits: dict[str, DatasetSplit] = {}
    for case_id, split in signal_set.case_splits.items():
        canonical_case_id = case_aliases.get(case_id)
        if canonical_case_id is None:
            continue
        previous = remapped_splits.setdefault(
            canonical_case_id,
            split,
        )
        if previous is not split:
            raise IngestionContractError(
                "semantic_split_conflict",
                "aliases of one canonical case request different splits",
            )
    canonical_set = replace(
        signal_set,
        signals=tuple(canonical_signals.values()),
        case_splits=remapped_splits,
        evidence_graph_logical_fingerprint=graph.logical_fingerprint,
    )
    return (
        tuple(
            sorted(
                canonical_cases,
                key=lambda item: item.case_id,
            )
        ),
        canonical_set,
        case_aliases,
        signal_aliases,
    )


def _remap_plan_proposal(
    proposal: SelfImprovementEvaluationPlanV1,
    *,
    case_aliases: Mapping[str, str],
    signal_aliases: Mapping[str, str],
) -> SelfImprovementEvaluationPlanV1:
    return replace(
        proposal,
        case_id=case_aliases.get(
            proposal.case_id,
            proposal.case_id,
        ),
        training_signal_ids=tuple(
            signal_aliases.get(signal_id, signal_id)
            for signal_id in proposal.training_signal_ids
        ),
    )


def _compile_plan(
    proposal: SelfImprovementEvaluationPlanV1,
    *,
    profile: SemanticIngestionProfileV1,
    manifest_origin: IngestionManifestOrigin,
    manifest_fingerprint: str | None,
    graph: SelfImprovementEvidenceGraphV1,
    case: SelfImprovementCaseV1,
    signal_set: SelfImprovementSignalSetV1,
    authority_context: EvidenceAuthorityContextV1,
    qualification_evidence: SemanticQualificationEvidenceV1,
) -> SelfImprovementEvaluationPlanV1:
    if signal_set.case_splits[case.case_id] is DatasetSplit.HELD_OUT:
        proposal = replace(
            proposal,
            training_signal_ids=(),
            supporting_evidence_claim_ids=(),
            replay_seed_execution_id=None,
            expected_output_claim_id=None,
            disposition=EvaluationDisposition.PROPOSAL_ONLY,
            reason_codes=tuple(
                sorted(
                    {
                        *proposal.reason_codes,
                        "held_out_case",
                    }
                )
            ),
        )
    compiled = compile_evaluation_plan(
        proposal,
        profile=profile,
        manifest_origin=ManifestOrigin(manifest_origin.value),
        manifest_fingerprint=(
            manifest_fingerprint
            or fingerprint_json(
                {"manifest_origin": manifest_origin.value}
            )
        ),
        graph=graph,
        case=case,
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
    )
    canonical_plan_id = canonical_semantic_plan_id(compiled)
    return replace(compiled, plan_id=canonical_plan_id)


def _plan_bundle_fingerprint(
    plans: Sequence[SelfImprovementEvaluationPlanV1],
) -> str:
    return fingerprint_json(
        [
            item.canonical_dict()
            for item in sorted(
                plans,
                key=lambda value: value.case_id,
            )
        ]
    )


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IngestionContractError(
            "schema_invalid",
            f"{field_name} must be an object",
        )
    return value


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise IngestionContractError(
            "schema_invalid",
            f"{field_name} must be an array",
        )
    return value
