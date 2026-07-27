from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aworld.self_evolve.constitution import (
    AgenticStageReportV1,
    SelfEvolveConstitutionV1,
    SemanticRolloutPolicyV1,
    SemanticRolloutStage,
    SelfEvolveStage,
    validate_report_chain,
)
from aworld.self_evolve.evaluation_plan import (
    EvidenceAuthorityContextV1,
    EvaluationDisposition,
    ManifestOrigin,
    SemanticIngestionProfileV1,
    SemanticModelQualificationReportV1,
    SemanticQualificationRegistryV1,
    SelfImprovementEvaluationPlanV1,
)
from aworld.self_evolve.evidence import (
    SelfImprovementCaseV1,
    SelfImprovementEvidenceGraphV1,
    authoritative_verification_registry_fingerprint,
)
from aworld.self_evolve.improvement_signals import (
    SelfImprovementSignalSetV1,
)

from .chunking import SourceBundleV1
from .semantic_compiler import (
    CompiledSemanticDatasetV1,
    ResolvedSemanticTraceV1,
    canonical_semantic_case_id,
    canonical_semantic_plan_id,
    canonical_semantic_signal_id,
    validate_resolved_trace_attestation,
)
from .semantic_verifier import (
    DEFAULT_SEMANTIC_CONSENSUS_THRESHOLD,
    SemanticEvidenceQualityReportV1,
    SemanticQualificationEvidenceV1,
    SemanticQualityGateDecisionV1,
    SemanticResolutionEvidenceV1,
    build_semantic_evidence_quality_report,
    evaluate_semantic_quality_gate,
)
from .types import (
    IngestionContractError,
    IngestionManifestOrigin,
    IngestionMode,
    IngestorTrustLevel,
    NormalizedCaseRecord,
    SourceInventory,
    fingerprint_json,
    validate_fingerprint,
    validate_safe_id,
)


FROZEN_SEMANTIC_INGESTION_SNAPSHOT_SCHEMA_VERSION = (
    "aworld.self_evolve.frozen_semantic_ingestion.v2"
)
SEMANTIC_RESOLUTION_AUDIT_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_resolution_audit.v1"
)


def _mode_for_rollout_stage(
    stage: SemanticRolloutStage,
) -> IngestionMode:
    return {
        SemanticRolloutStage.SHADOW: IngestionMode.INGESTION_ONLY,
        SemanticRolloutStage.PROPOSAL: IngestionMode.PROPOSAL,
        SemanticRolloutStage.TARGET_EVIDENCE: IngestionMode.PROPOSAL,
        SemanticRolloutStage.VERIFIED: IngestionMode.AUTO_VERIFIED,
    }[SemanticRolloutStage(stage)]


@dataclass(frozen=True)
class SemanticResolutionAuditV1:
    semantic_parse_consensus: float
    semantic_valid_candidate_count: int
    semantic_resolution_execution_count: int
    semantic_resolution_deterministic_match: bool
    schema_version: str = SEMANTIC_RESOLUTION_AUDIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_RESOLUTION_AUDIT_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid semantic resolution audit schema",
            )
        if (
            isinstance(self.semantic_parse_consensus, bool)
            or not isinstance(self.semantic_parse_consensus, (int, float))
            or not math.isfinite(float(self.semantic_parse_consensus))
            or not 0.0 <= float(self.semantic_parse_consensus) <= 1.0
        ):
            raise IngestionContractError(
                "semantic_consensus_invalid",
                "semantic parse consensus must be a finite rate",
            )
        object.__setattr__(
            self,
            "semantic_parse_consensus",
            float(self.semantic_parse_consensus),
        )
        for name in (
            "semantic_valid_candidate_count",
            "semantic_resolution_execution_count",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise IngestionContractError(
                    "semantic_resolution_audit_invalid",
                    f"{name} must be a non-negative integer",
                )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict(include_fingerprint=False))

    def to_dict(
        self,
        *,
        include_fingerprint: bool = True,
    ) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "semantic_parse_consensus": self.semantic_parse_consensus,
            "semantic_valid_candidate_count": (
                self.semantic_valid_candidate_count
            ),
            "semantic_resolution_execution_count": (
                self.semantic_resolution_execution_count
            ),
            "semantic_resolution_deterministic_match": (
                self.semantic_resolution_deterministic_match
            ),
        }
        if include_fingerprint:
            result["fingerprint"] = self.fingerprint
        return result

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticResolutionAuditV1":
        audit = cls(
            semantic_parse_consensus=payload.get(
                "semantic_parse_consensus"
            ),  # type: ignore[arg-type]
            semantic_valid_candidate_count=payload.get(
                "semantic_valid_candidate_count"
            ),  # type: ignore[arg-type]
            semantic_resolution_execution_count=payload.get(
                "semantic_resolution_execution_count"
            ),  # type: ignore[arg-type]
            semantic_resolution_deterministic_match=(
                payload.get("semantic_resolution_deterministic_match")
                is True
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )
        claimed = payload.get("fingerprint")
        if claimed is not None and claimed != audit.fingerprint:
            raise IngestionContractError(
                "fingerprint_mismatch",
                "semantic resolution audit fingerprint mismatch",
            )
        return audit


@dataclass(frozen=True)
class FrozenSemanticIngestionSnapshotV2:
    ingestion_id: str
    inventory: SourceInventory
    source_bundle: SourceBundleV1
    constitution: SelfEvolveConstitutionV1
    rollout_policy: SemanticRolloutPolicyV1
    semantic_profile: SemanticIngestionProfileV1
    stage_reports: tuple[AgenticStageReportV1, ...]
    evidence_graph: SelfImprovementEvidenceGraphV1
    evidence_authority_context: EvidenceAuthorityContextV1
    semantic_cases: tuple[SelfImprovementCaseV1, ...]
    improvement_signal_set: SelfImprovementSignalSetV1
    evaluation_plans: tuple[SelfImprovementEvaluationPlanV1, ...]
    resolved_traces: tuple[ResolvedSemanticTraceV1, ...]
    compiled_dataset: CompiledSemanticDatasetV1
    quality_report: SemanticEvidenceQualityReportV1
    quality_gate: SemanticQualityGateDecisionV1
    resolution_evidence: SemanticResolutionEvidenceV1
    authoritative_verification_ids: tuple[str, ...]
    verification_registry_fingerprint: str
    semantic_model_profile_fingerprint: str
    semantic_provider_fingerprint: str
    semantic_protocol_fingerprint: str
    qualification_registry: SemanticQualificationRegistryV1
    qualification_corpus_fingerprint: str
    qualification_threshold_set_fingerprint: str
    qualification_report: SemanticModelQualificationReportV1 | None = None
    qualification_evaluated_at_utc: str | None = None
    manifest_fingerprint: str | None = None
    source_manifest: Mapping[str, Any] | None = None
    canonical_manifest_asset_id: str | None = None
    manifest_origin: IngestionManifestOrigin = (
        IngestionManifestOrigin.ABSENT
    )
    extractor_fingerprints: tuple[str, ...] = ()
    split_fingerprint: str | None = None
    ingestor_name: str = "auto"
    ingestor_version: str = "2"
    ingestor_trust_level: IngestorTrustLevel = (
        IngestorTrustLevel.FRAMEWORK_BUILTIN
    )
    semantic_consensus_threshold: float = (
        DEFAULT_SEMANTIC_CONSENSUS_THRESHOLD
    )
    identity_schema_version: str = "v2"
    normalization_kind: str = "semantic_evidence"
    schema_version: str = (
        FROZEN_SEMANTIC_INGESTION_SNAPSHOT_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        if (
            self.schema_version
            != FROZEN_SEMANTIC_INGESTION_SNAPSHOT_SCHEMA_VERSION
        ):
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid frozen semantic ingestion snapshot schema",
            )
        if self.identity_schema_version != "v2":
            raise IngestionContractError(
                "schema_version_mismatch",
                "semantic snapshots require v2 ingestion identity",
            )
        if self.normalization_kind != "semantic_evidence":
            raise IngestionContractError(
                "normalization_kind_invalid",
                "semantic snapshots require semantic_evidence normalization",
            )
        validate_safe_id(self.ingestion_id, field_name="ingestion_id")
        object.__setattr__(
            self,
            "manifest_origin",
            IngestionManifestOrigin(self.manifest_origin),
        )
        object.__setattr__(
            self,
            "ingestor_trust_level",
            IngestorTrustLevel(self.ingestor_trust_level),
        )
        if not self.ingestor_name or not self.ingestor_version:
            raise IngestionContractError(
                "schema_invalid",
                "semantic ingestor identity must be non-empty",
            )
        for name in (
            "verification_registry_fingerprint",
            "semantic_model_profile_fingerprint",
            "semantic_provider_fingerprint",
            "semantic_protocol_fingerprint",
            "qualification_corpus_fingerprint",
            "qualification_threshold_set_fingerprint",
        ):
            validate_fingerprint(getattr(self, name), field_name=name)
        for value in self.extractor_fingerprints:
            validate_fingerprint(value, field_name="extractor_fingerprint")
        if self.split_fingerprint is not None:
            validate_fingerprint(
                self.split_fingerprint,
                field_name="split_fingerprint",
            )
        if self.canonical_manifest_asset_id is not None:
            validate_safe_id(
                self.canonical_manifest_asset_id,
                field_name="canonical_manifest_asset_id",
            )
            if (
                self.resolution_evidence.extraction_origin.value
                != "deterministic_canonical"
                or self.source_manifest is None
                or self.canonical_manifest_asset_id
                not in {item.asset_id for item in self.inventory.assets}
            ):
                raise IngestionContractError(
                    "canonical_manifest_asset_invalid",
                    "canonical manifest asset must identify the frozen "
                    "operator or conventional manifest",
                )
        if (
            isinstance(self.semantic_consensus_threshold, bool)
            or not isinstance(
                self.semantic_consensus_threshold,
                (int, float),
            )
            or not math.isfinite(
                float(self.semantic_consensus_threshold)
            )
            or not 0.0
            <= float(self.semantic_consensus_threshold)
            <= 1.0
        ):
            raise IngestionContractError(
                "semantic_consensus_invalid",
                "semantic consensus threshold must be a finite rate",
            )
        object.__setattr__(
            self,
            "semantic_consensus_threshold",
            float(self.semantic_consensus_threshold),
        )
        expected_mode = _mode_for_rollout_stage(
            self.rollout_policy.enabled_stage
        )
        if self.quality_gate.mode is not expected_mode:
            raise IngestionContractError(
                "semantic_rollout_gate_mode_mismatch",
                "frozen rollout stage and semantic quality mode differ",
            )
        self._validate_manifest()
        self._validate_artifact_graph()
        expected_identity = self.identity_for(
            inventory_fingerprint=(
                self.inventory.source_root_fingerprint
            ),
            source_bundle_fingerprint=self.source_bundle.fingerprint,
            constitution_fingerprint=self.constitution.fingerprint,
            rollout_policy_fingerprint=self.rollout_policy.fingerprint,
            semantic_profile_fingerprint=(
                self.semantic_profile.fingerprint
            ),
            manifest_fingerprint=self.manifest_fingerprint,
            manifest_origin=self.manifest_origin,
            extractor_fingerprints=self.extractor_fingerprints,
            semantic_model_profile_fingerprint=(
                self.semantic_model_profile_fingerprint
            ),
            semantic_provider_fingerprint=(
                self.semantic_provider_fingerprint
            ),
            semantic_protocol_fingerprint=(
                self.semantic_protocol_fingerprint
            ),
            qualification_report_fingerprint=(
                self.qualification_report.report_fingerprint
                if self.qualification_report is not None
                else None
            ),
            qualification_evaluated_at_utc=(
                self.qualification_evaluated_at_utc
            ),
            ingestor_name=self.ingestor_name,
            ingestor_version=self.ingestor_version,
            trust_level=self.ingestor_trust_level,
            authority_context_fingerprint=(
                self.evidence_authority_context.fingerprint
            ),
            qualification_registry_fingerprint=(
                self.qualification_registry.fingerprint
            ),
        )
        legacy_identity = self.identity_for(
            inventory_fingerprint=(
                self.inventory.source_root_fingerprint
            ),
            source_bundle_fingerprint=self.source_bundle.fingerprint,
            constitution_fingerprint=self.constitution.fingerprint,
            rollout_policy_fingerprint=self.rollout_policy.fingerprint,
            semantic_profile_fingerprint=(
                self.semantic_profile.fingerprint
            ),
            manifest_fingerprint=self.manifest_fingerprint,
            manifest_origin=self.manifest_origin,
            extractor_fingerprints=self.extractor_fingerprints,
            semantic_model_profile_fingerprint=(
                self.semantic_model_profile_fingerprint
            ),
            semantic_provider_fingerprint=(
                self.semantic_provider_fingerprint
            ),
            semantic_protocol_fingerprint=(
                self.semantic_protocol_fingerprint
            ),
            qualification_report_fingerprint=(
                self.qualification_report.report_fingerprint
                if self.qualification_report is not None
                else None
            ),
            ingestor_name=self.ingestor_name,
            ingestor_version=self.ingestor_version,
            trust_level=self.ingestor_trust_level,
        )
        if (
            self.ingestion_id.startswith("ingestion-")
            and self.ingestion_id
            not in {expected_identity, legacy_identity}
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "semantic ingestion identity does not match frozen inputs",
            )
        if (
            self.ingestion_id == legacy_identity
            and (
                self.rollout_policy.enabled_stage
                is SemanticRolloutStage.VERIFIED
                or self.authoritative_verification_ids
                or self.evidence_authority_context.human_approval
                is not None
                or self.qualification_registry.trusted_report_fingerprints
            )
        ):
            raise IngestionContractError(
                "legacy_semantic_identity_untrusted",
                "legacy semantic identity cannot carry verified authority or "
                "qualification state",
            )

    @property
    def normalized_cases(self) -> tuple[NormalizedCaseRecord, ...]:
        return self.compiled_dataset.normalized_cases

    @property
    def normalized_dataset_fingerprint(self) -> str:
        return self.compiled_dataset.normalized_dataset_fingerprint

    @property
    def ingestion_model_call_count(self) -> int:
        return self.quality_report.semantic_agent_model_call_count

    @property
    def selected_mapping(self) -> None:
        return None

    @property
    def snapshot_fingerprint(self) -> str:
        return fingerprint_json(self.to_dict(include_fingerprint=False))

    def _validate_manifest(self) -> None:
        if self.source_manifest is None:
            if self.manifest_fingerprint is not None:
                raise IngestionContractError(
                    "provenance_missing",
                    "manifest fingerprint requires a frozen manifest",
                )
            if (
                self.manifest_origin
                is not IngestionManifestOrigin.ABSENT
            ):
                raise IngestionContractError(
                    "manifest_origin_invalid",
                    "manifest origin requires a frozen manifest",
                )
            return
        if (
            self.manifest_fingerprint
            != fingerprint_json(self.source_manifest)
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "semantic source manifest fingerprint mismatch",
            )
        if self.manifest_origin is IngestionManifestOrigin.ABSENT:
            raise IngestionContractError(
                "manifest_origin_invalid",
                "frozen semantic manifest requires a typed origin",
            )

    def _validate_artifact_graph(self) -> None:
        if (
            self.source_bundle.inventory_fingerprint
            != self.inventory.source_root_fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "source bundle and source inventory differ",
            )
        if (
            self.resolution_evidence.extraction_origin.value
            == "deterministic_canonical"
        ):
            from .semantic_canonical import (
                recognize_canonical_semantic_source,
                decode_canonical_semantic_source,
            )

            canonical_source = recognize_canonical_semantic_source(
                self.source_bundle,
                manifest_asset_id=self.canonical_manifest_asset_id,
            )
            if canonical_source is None:
                raise IngestionContractError(
                    "canonical_decoder_attestation_invalid",
                    "frozen canonical source is no longer recognizable",
                )
            canonical_qualification = SemanticQualificationEvidenceV1(
                registry=self.qualification_registry,
                report=self.qualification_report,
                model_profile_fingerprint=(
                    self.semantic_model_profile_fingerprint
                ),
                provider_fingerprint=(
                    self.semantic_provider_fingerprint
                ),
                semantic_protocol_fingerprint=(
                    self.semantic_protocol_fingerprint
                ),
                constitution_fingerprint=self.constitution.fingerprint,
                corpus_fingerprint=(
                    self.qualification_corpus_fingerprint
                ),
                threshold_set_fingerprint=(
                    self.qualification_threshold_set_fingerprint
                ),
                evaluated_at_utc=self.qualification_evaluated_at_utc,
                extraction_origin=(
                    self.resolution_evidence.extraction_origin
                ),
                deterministic_attestation_fingerprint=(
                    self.resolution_evidence
                    .deterministic_attestation_fingerprint
                ),
            )
            decoded = decode_canonical_semantic_source(
                canonical_source,
                self.source_bundle,
                self.semantic_profile,
                ManifestOrigin(self.manifest_origin.value),
                self.manifest_fingerprint,
                canonical_qualification,
                extractor_fingerprints=self.extractor_fingerprints,
            )
            mismatched_artifacts = [
                name
                for name, matches in (
                    (
                        "evidence_graph",
                        decoded.evidence_graph == self.evidence_graph,
                    ),
                    (
                        "evidence_authority_context",
                        decoded.evidence_authority_context
                        == self.evidence_authority_context,
                    ),
                    (
                        "semantic_cases",
                        decoded.semantic_cases == self.semantic_cases,
                    ),
                    (
                        "improvement_signal_set",
                        decoded.improvement_signal_set
                        == self.improvement_signal_set,
                    ),
                    (
                        "evaluation_plans",
                        decoded.evaluation_plans
                        == self.evaluation_plans,
                    ),
                    (
                        "resolved_traces",
                        decoded.resolved_traces == self.resolved_traces,
                    ),
                )
                if not matches
            ]
            if mismatched_artifacts:
                raise IngestionContractError(
                    "canonical_decoder_attestation_invalid",
                    "frozen canonical artifacts differ from deterministic "
                    f"decode: {mismatched_artifacts}",
                )
        active_reports = validate_report_chain(
            self.constitution,
            self.stage_reports,
        )
        if len(active_reports) < 7:
            raise IngestionContractError(
                "semantic_stage_reports_missing",
                "semantic snapshot requires all ingestion stage reports",
            )
        if (
            self.inventory.source_root_fingerprint
            not in active_reports[0].input_fingerprints
        ):
            raise IngestionContractError(
                "stage_fingerprint_chain_broken",
                "discover stage is not bound to the source inventory",
            )
        report_by_stage = {
            item.stage: item for item in active_reports
        }
        required_outputs = {
            SelfEvolveStage.DISCOVER: (
                self.source_bundle.fingerprint,
            ),
            SelfEvolveStage.VERIFY_COVERAGE_AND_ENTAILMENT: (
                self.evidence_graph.provenance_fingerprint,
            ),
            SelfEvolveStage.RESOLVE_AND_DETECT_CONFLICT: (
                self.evidence_graph.logical_fingerprint,
            ),
            SelfEvolveStage.SYNTHESIZE_IMPROVEMENT_SIGNALS: (
                self.improvement_signal_set.fingerprint,
                self.compiled_dataset.target_evidence_bundle.fingerprint,
            ),
            SelfEvolveStage.PLAN_EVALUATION: (
                self.compiled_dataset
                .evaluation_plan_bundle_fingerprint,
            ),
        }
        for stage, fingerprints in required_outputs.items():
            report = report_by_stage.get(stage)
            if report is None or not set(fingerprints).issubset(
                report.output_fingerprints
            ):
                raise IngestionContractError(
                    "stage_artifact_attestation_missing",
                    f"{stage.value} report does not attest frozen outputs",
                )
        if (
            self.evidence_graph.profile_fingerprint
            != self.semantic_profile.fingerprint
        ):
            raise IngestionContractError(
                "profile_fingerprint_mismatch",
                "semantic profile and evidence graph differ",
            )
        if (
            self.evidence_authority_context.source_bundle_fingerprint
            is not None
            and self.evidence_authority_context.source_bundle_fingerprint
            != self.source_bundle.fingerprint
        ):
            raise IngestionContractError(
                "verification_authority_untrusted",
                "evidence authority context is bound to another source bundle",
            )
        if (
            self.evidence_authority_context.constitution_fingerprint
            is not None
            and self.evidence_authority_context.constitution_fingerprint
            != self.constitution.fingerprint
        ):
            raise IngestionContractError(
                "verification_authority_untrusted",
                "evidence authority context is bound to another constitution",
            )
        actual_authoritative_ids = tuple(
            sorted(
                item.verification_id
                for item in self.evidence_graph.claim_verifications
                if item.is_authoritative_origin
            )
        )
        if tuple(sorted(self.authoritative_verification_ids)) != (
            actual_authoritative_ids
        ):
            raise IngestionContractError(
                "verification_authority_untrusted",
                "authoritative verification IDs do not match the graph",
            )
        deterministic_ids = tuple(
            sorted(
                item.verification_id
                for item in self.evidence_graph.claim_verifications
                if item.verification_origin.value
                == "deterministic_decoder"
            )
        )
        trusted_registered_ids = tuple(
            sorted(
                item.verification_id
                for item in self.evidence_graph.claim_verifications
                if item.verification_origin.value
                == "trusted_registered_ingestor"
            )
        )
        if (
            tuple(
                sorted(
                    self.evidence_authority_context
                    .deterministic_verification_ids
                )
            )
            != deterministic_ids
            or tuple(
                sorted(
                    self.evidence_authority_context
                    .trusted_registered_verification_ids
                )
            )
            != trusted_registered_ids
        ):
            raise IngestionContractError(
                "verification_authority_untrusted",
                "authority context does not contain the exact authoritative IDs",
            )
        if (
            self.ingestor_trust_level
            is IngestorTrustLevel.EXTERNAL_UNTRUSTED
            and (
                actual_authoritative_ids
                or self.evidence_authority_context.human_approval is not None
                or self.qualification_registry.trusted_report_fingerprints
            )
        ):
            raise IngestionContractError(
                "verification_authority_untrusted",
                "external semantic ingestors cannot supply trust artifacts",
            )
        approval = self.evidence_authority_context.human_approval
        if approval is not None:
            manifest_fingerprint = self.manifest_fingerprint
            if (
                not approval.is_production_bound
                or manifest_fingerprint is None
                or not approval.matches(
                    graph_fingerprint=(
                        self.evidence_graph.logical_fingerprint
                    ),
                    graph_provenance_fingerprint=(
                        self.evidence_graph.provenance_fingerprint
                    ),
                    source_bundle_fingerprint=(
                        self.source_bundle.fingerprint
                    ),
                    constitution_fingerprint=(
                        self.constitution.fingerprint
                    ),
                    semantic_profile_fingerprint=(
                        self.semantic_profile.fingerprint
                    ),
                    manifest_fingerprint=manifest_fingerprint,
                    manifest_origin=ManifestOrigin(
                        self.manifest_origin.value
                    ),
                )
            ):
                raise IngestionContractError(
                    "human_evidence_approval_binding_mismatch",
                    "frozen human approval does not match semantic artifacts",
                )
        expected_registry_fingerprint = (
            authoritative_verification_registry_fingerprint(
                self.evidence_graph,
                self.authoritative_verification_ids,
            )
        )
        if (
            self.verification_registry_fingerprint
            != expected_registry_fingerprint
        ):
            raise IngestionContractError(
                "verification_authority_untrusted",
                "verification authority registry is not graph-bound",
            )
        if (
            self.improvement_signal_set.evidence_graph_logical_fingerprint
            != self.evidence_graph.logical_fingerprint
        ):
            raise IngestionContractError(
                "evidence_graph_fingerprint_mismatch",
                "signal set and evidence graph differ",
            )
        case_by_id = {
            item.case_id: item for item in self.semantic_cases
        }
        plan_by_case = {
            item.case_id: item for item in self.evaluation_plans
        }
        if (
            len(case_by_id) != len(self.semantic_cases)
            or len(plan_by_case) != len(self.evaluation_plans)
            or set(case_by_id) != set(plan_by_case)
        ):
            raise IngestionContractError(
                "evaluation_plan_coverage_incomplete",
                "semantic cases require exactly one evaluation plan",
            )
        for case_id, case in case_by_id.items():
            if case.case_id != canonical_semantic_case_id(
                case,
                graph=self.evidence_graph,
            ):
                raise IngestionContractError(
                    "semantic_case_identity_noncanonical",
                    "frozen semantic case identity is agent-controlled",
                )
            case.validate_against(self.evidence_graph)
            plan = plan_by_case[case_id]
            if plan.plan_id != canonical_semantic_plan_id(plan):
                raise IngestionContractError(
                    "semantic_plan_identity_noncanonical",
                    "frozen semantic plan identity is agent-controlled",
                )
            plan.validate_references(
                graph=self.evidence_graph,
                case=case,
                signal_set=self.improvement_signal_set,
            )
            if (
                plan.disposition
                is EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
                or plan.expected_output_claim_id is not None
            ):
                manifest_fingerprint = (
                    self.manifest_fingerprint
                    or fingerprint_json(
                        {
                            "manifest_origin": (
                                self.manifest_origin.value
                            )
                        }
                    )
                )
                claim_ids = {
                    *plan.supporting_evidence_claim_ids,
                    *(
                        (plan.expected_output_claim_id,)
                        if plan.expected_output_claim_id is not None
                        else ()
                    ),
                }
                if any(
                    not self.evidence_authority_context.authorizes_claim(
                        claim_id,
                        graph=self.evidence_graph,
                        manifest_origin=ManifestOrigin(
                            self.manifest_origin.value
                        ),
                        manifest_fingerprint=manifest_fingerprint,
                    )
                    for claim_id in claim_ids
                ):
                    raise IngestionContractError(
                        "semantic_plan_authority_attestation_invalid",
                        "verified plan is not supported by frozen authority",
                    )
        for signal in self.improvement_signal_set.signals:
            if signal.signal_id != canonical_semantic_signal_id(
                signal
            ):
                raise IngestionContractError(
                    "semantic_signal_identity_noncanonical",
                    "frozen semantic signal identity is agent-controlled",
                )
        self.compiled_dataset.target_evidence_bundle.validate_against(
            self.evidence_graph,
            self.semantic_cases,
        )
        traces_by_ref = {
            item.trace_ref: item for item in self.resolved_traces
        }
        if len(traces_by_ref) != len(self.resolved_traces):
            raise IngestionContractError(
                "duplicate_identity",
                "resolved semantic trace refs must be unique",
            )
        for execution in (
            self.compiled_dataset.target_evidence_bundle.executions
        ):
            resolved = traces_by_ref.get(execution.trace_ref)
            if (
                resolved is None
                or resolved.trace_fingerprint
                != execution.trace_fingerprint
            ):
                raise IngestionContractError(
                    "semantic_trace_resolution_missing",
                    "target evidence lacks a matching frozen trace",
                )
            validate_resolved_trace_attestation(
                resolved,
                graph=self.evidence_graph,
                trajectory_claim_id=execution.trajectory_claim_id,
                source_bundle=self.source_bundle,
            )
            attestation = resolved.extraction_attestation
            if attestation is None:
                raise IngestionContractError(
                    "semantic_trace_source_attestation_missing",
                    "target evidence requires trace attestation",
                )
            if attestation.extractor_fingerprints != tuple(
                sorted(self.extractor_fingerprints)
            ):
                raise IngestionContractError(
                    "semantic_trace_extractor_attestation_mismatch",
                    "trace attestation and frozen extractor identities differ",
                )
            if any(
                candidate.provider_fingerprint
                != self.semantic_provider_fingerprint
                or candidate.model_fingerprint
                != self.semantic_model_profile_fingerprint
                or candidate.protocol_fingerprint
                != self.semantic_protocol_fingerprint
                for candidate in attestation.candidate_attestations
            ):
                raise IngestionContractError(
                    "semantic_trace_deployment_attestation_mismatch",
                    "trace attestation and frozen semantic deployment differ",
                )
        if (
            self.compiled_dataset.evidence_graph_logical_fingerprint
            != self.evidence_graph.logical_fingerprint
            or self.compiled_dataset.improvement_signal_set_fingerprint
            != self.improvement_signal_set.fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "compiled semantic dataset references different evidence",
            )
        expected_plan_fingerprint = fingerprint_json(
            [
                plan_by_case[case_id].canonical_dict()
                for case_id in sorted(plan_by_case)
            ]
        )
        if (
            self.compiled_dataset.evaluation_plan_bundle_fingerprint
            != expected_plan_fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "compiled semantic dataset references different plans",
            )
        for normalized in self.normalized_cases:
            if normalized.source.ingestion_id != self.ingestion_id:
                raise IngestionContractError(
                    "provenance_missing",
                    "normalized semantic case has a different ingestion id",
                )
        expected_quality = build_semantic_evidence_quality_report(
            bundle=self.source_bundle,
            graph=self.evidence_graph,
            constitution=self.constitution,
            stage_reports=self.stage_reports,
            signal_set=self.improvement_signal_set,
            semantic_cases=self.semantic_cases,
            evaluation_plans=self.evaluation_plans,
            compiled_dataset=self.compiled_dataset,
            resolution_evidence=self.resolution_evidence,
            qualification_evidence=SemanticQualificationEvidenceV1(
                registry=self.qualification_registry,
                report=self.qualification_report,
                model_profile_fingerprint=(
                    self.semantic_model_profile_fingerprint
                ),
                provider_fingerprint=(
                    self.semantic_provider_fingerprint
                ),
                semantic_protocol_fingerprint=(
                    self.semantic_protocol_fingerprint
                ),
                constitution_fingerprint=(
                    self.constitution.fingerprint
                ),
                corpus_fingerprint=(
                    self.qualification_corpus_fingerprint
                ),
                threshold_set_fingerprint=(
                    self.qualification_threshold_set_fingerprint
                ),
                evaluated_at_utc=self.qualification_evaluated_at_utc,
                extraction_origin=(
                    self.resolution_evidence.extraction_origin
                ),
                deterministic_attestation_fingerprint=(
                    self.resolution_evidence
                    .deterministic_attestation_fingerprint
                ),
            ),
        )
        if expected_quality != self.quality_report:
            raise IngestionContractError(
                "semantic_quality_attestation_mismatch",
                "semantic quality does not match frozen artifacts",
            )
        expected_gate = evaluate_semantic_quality_gate(
            expected_quality,
            mode=_mode_for_rollout_stage(
                self.rollout_policy.enabled_stage
            ),
            consensus_threshold=self.semantic_consensus_threshold,
        )
        if expected_gate != self.quality_gate:
            raise IngestionContractError(
                "semantic_gate_attestation_mismatch",
                "semantic quality gate does not match frozen artifacts",
            )

    @staticmethod
    def identity_for(
        *,
        inventory_fingerprint: str,
        source_bundle_fingerprint: str,
        constitution_fingerprint: str,
        rollout_policy_fingerprint: str,
        semantic_profile_fingerprint: str,
        manifest_fingerprint: str | None,
        manifest_origin: IngestionManifestOrigin,
        extractor_fingerprints: Sequence[str],
        semantic_model_profile_fingerprint: str,
        semantic_provider_fingerprint: str,
        semantic_protocol_fingerprint: str,
        qualification_report_fingerprint: str | None,
        ingestor_name: str,
        ingestor_version: str,
        trust_level: IngestorTrustLevel,
        qualification_evaluated_at_utc: str | None = None,
        authority_context_fingerprint: str | None = None,
        qualification_registry_fingerprint: str | None = None,
    ) -> str:
        payload = {
            "schema_version": (
                FROZEN_SEMANTIC_INGESTION_SNAPSHOT_SCHEMA_VERSION
            ),
            "identity_schema_version": "v2",
            "normalization_kind": "semantic_evidence",
            "inventory_fingerprint": validate_fingerprint(
                inventory_fingerprint
            ),
            "source_bundle_fingerprint": validate_fingerprint(
                source_bundle_fingerprint
            ),
            "constitution_fingerprint": validate_fingerprint(
                constitution_fingerprint
            ),
            "rollout_policy_fingerprint": validate_fingerprint(
                rollout_policy_fingerprint
            ),
            "semantic_profile_fingerprint": validate_fingerprint(
                semantic_profile_fingerprint
            ),
            "manifest_fingerprint": manifest_fingerprint,
            "manifest_origin": IngestionManifestOrigin(
                manifest_origin
            ).value,
            "extractor_fingerprints": sorted(extractor_fingerprints),
            "semantic_model_profile_fingerprint": validate_fingerprint(
                semantic_model_profile_fingerprint
            ),
            "semantic_provider_fingerprint": validate_fingerprint(
                semantic_provider_fingerprint
            ),
            "semantic_protocol_fingerprint": validate_fingerprint(
                semantic_protocol_fingerprint
            ),
            "qualification_report_fingerprint": (
                qualification_report_fingerprint
            ),
            "qualification_evaluated_at_utc": (
                qualification_evaluated_at_utc
            ),
            "authority_context_fingerprint": (
                validate_fingerprint(authority_context_fingerprint)
                if authority_context_fingerprint is not None
                else None
            ),
            "qualification_registry_fingerprint": (
                validate_fingerprint(qualification_registry_fingerprint)
                if qualification_registry_fingerprint is not None
                else None
            ),
            "ingestor_name": ingestor_name,
            "ingestor_version": ingestor_version,
            "trust_level": IngestorTrustLevel(trust_level).value,
        }
        return (
            "ingestion-"
            + fingerprint_json(payload).removeprefix("sha256:")[:32]
        )

    def to_dict(
        self,
        *,
        public: bool = False,
        include_fingerprint: bool = True,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "identity_schema_version": self.identity_schema_version,
            "normalization_kind": self.normalization_kind,
            "ingestion_id": self.ingestion_id,
            "inventory": self.inventory.to_dict(public=public),
            "constitution_fingerprint": self.constitution.fingerprint,
            "rollout_policy_fingerprint": self.rollout_policy.fingerprint,
            "semantic_profile_fingerprint": (
                self.semantic_profile.fingerprint
            ),
            "evidence_graph_logical_fingerprint": (
                self.evidence_graph.logical_fingerprint
            ),
            "evidence_graph_provenance_fingerprint": (
                self.evidence_graph.provenance_fingerprint
            ),
            "improvement_signal_set_fingerprint": (
                self.improvement_signal_set.fingerprint
            ),
            "evaluation_plan_bundle_fingerprint": (
                self.compiled_dataset
                .evaluation_plan_bundle_fingerprint
            ),
            "target_evidence_bundle_fingerprint": (
                self.compiled_dataset
                .target_evidence_bundle.fingerprint
            ),
            "normalization_fingerprint": (
                self.compiled_dataset.normalization_fingerprint
            ),
            "normalized_dataset_fingerprint": (
                self.normalized_dataset_fingerprint
            ),
            "manifest_fingerprint": self.manifest_fingerprint,
            "manifest_origin": self.manifest_origin.value,
            "canonical_manifest_asset_id": (
                self.canonical_manifest_asset_id
            ),
            "extractor_fingerprints": list(
                self.extractor_fingerprints
            ),
            "split_fingerprint": self.split_fingerprint,
            "ingestor_name": self.ingestor_name,
            "ingestor_version": self.ingestor_version,
            "ingestor_trust_level": self.ingestor_trust_level.value,
            "ingestion_model_call_count": (
                self.ingestion_model_call_count
            ),
            "quality_report": self.quality_report.to_dict(),
            "quality_gate": self.quality_gate.to_dict(),
            "resolution_evidence": self.resolution_evidence.to_dict(),
            "semantic_model_profile_fingerprint": (
                self.semantic_model_profile_fingerprint
            ),
            "semantic_provider_fingerprint": (
                self.semantic_provider_fingerprint
            ),
            "semantic_protocol_fingerprint": (
                self.semantic_protocol_fingerprint
            ),
            "qualification_report_fingerprint": (
                self.qualification_report.report_fingerprint
                if self.qualification_report is not None
                else None
            ),
            "qualification_evaluated_at_utc": (
                self.qualification_evaluated_at_utc
            ),
            "semantic_consensus_threshold": (
                self.semantic_consensus_threshold
            ),
        }
        if public:
            result.update(
                {
                    "source_bundle": (
                        self.source_bundle.public_projection()
                    ),
                    "semantic_case_count": len(self.semantic_cases),
                    "normalized_case_count": len(
                        self.normalized_cases
                    ),
                    "stage_reports": [
                        item.to_dict() for item in self.stage_reports
                    ],
                }
            )
        else:
            result.update(
                {
                    "source_bundle": self.source_bundle.to_dict(),
                    "constitution": self.constitution.to_dict(),
                    "rollout_policy": self.rollout_policy.to_dict(),
                    "semantic_profile": self.semantic_profile.to_dict(),
                    "stage_reports": [
                        item.to_dict() for item in self.stage_reports
                    ],
                    "evidence_graph": self.evidence_graph.to_dict(),
                    "evidence_authority_context": (
                        self.evidence_authority_context.to_dict()
                    ),
                    "semantic_cases": [
                        item.to_dict() for item in self.semantic_cases
                    ],
                    "improvement_signal_set": (
                        self.improvement_signal_set.to_dict()
                    ),
                    "evaluation_plans": [
                        item.to_dict()
                        for item in self.evaluation_plans
                    ],
                    "resolved_traces": [
                        item.to_dict()
                        for item in self.resolved_traces
                    ],
                    "compiled_dataset": (
                        self.compiled_dataset.to_dict()
                    ),
                    "authoritative_verification_ids": list(
                        self.authoritative_verification_ids
                    ),
                    "verification_registry_fingerprint": (
                        self.verification_registry_fingerprint
                    ),
                    "qualification_registry": (
                        self.qualification_registry.to_dict()
                    ),
                    "qualification_corpus_fingerprint": (
                        self.qualification_corpus_fingerprint
                    ),
                    "qualification_threshold_set_fingerprint": (
                        self.qualification_threshold_set_fingerprint
                    ),
                    "qualification_report": (
                        self.qualification_report.to_dict()
                        if self.qualification_report is not None
                        else None
                    ),
                    "source_manifest": (
                        dict(self.source_manifest)
                        if self.source_manifest is not None
                        else None
                    ),
                }
            )
        if include_fingerprint:
            result["snapshot_fingerprint"] = (
                self.snapshot_fingerprint
            )
        return result

    def public_projection(self) -> dict[str, Any]:
        return self.to_dict(public=True)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "FrozenSemanticIngestionSnapshotV2":
        graph_payload = _mapping(
            payload.get("evidence_graph"),
            "evidence_graph",
        )
        graph = SelfImprovementEvidenceGraphV1.from_frozen_dict(
            graph_payload,
            attested_provenance_fingerprint=str(
                payload.get(
                    "evidence_graph_provenance_fingerprint"
                )
                or ""
            ),
            authoritative_verification_ids=_strings(
                payload.get("authoritative_verification_ids", ()),
                "authoritative_verification_ids",
            ),
            verification_registry_fingerprint=str(
                payload.get("verification_registry_fingerprint") or ""
            ),
        )
        qualification_payload = payload.get("qualification_report")
        snapshot = cls(
            ingestion_id=str(payload.get("ingestion_id") or ""),
            inventory=SourceInventory.from_dict(
                _mapping(payload.get("inventory"), "inventory")
            ),
            source_bundle=SourceBundleV1.from_dict(
                _mapping(payload.get("source_bundle"), "source_bundle")
            ),
            constitution=SelfEvolveConstitutionV1.from_dict(
                _mapping(payload.get("constitution"), "constitution")
            ),
            rollout_policy=SemanticRolloutPolicyV1.from_dict(
                _mapping(
                    payload.get("rollout_policy"),
                    "rollout_policy",
                )
            ),
            semantic_profile=SemanticIngestionProfileV1.from_dict(
                _mapping(
                    payload.get("semantic_profile"),
                    "semantic_profile",
                )
            ),
            stage_reports=tuple(
                AgenticStageReportV1.from_dict(_mapping(item, "stage_report"))
                for item in _sequence(
                    payload.get("stage_reports", ()),
                    "stage_reports",
                )
            ),
            evidence_graph=graph,
            evidence_authority_context=(
                EvidenceAuthorityContextV1.from_dict(
                    _mapping(
                        payload.get("evidence_authority_context"),
                        "evidence_authority_context",
                    )
                )
            ),
            semantic_cases=tuple(
                SelfImprovementCaseV1.from_dict(
                    _mapping(item, "semantic_case")
                )
                for item in _sequence(
                    payload.get("semantic_cases", ()),
                    "semantic_cases",
                )
            ),
            improvement_signal_set=SelfImprovementSignalSetV1.from_dict(
                _mapping(
                    payload.get("improvement_signal_set"),
                    "improvement_signal_set",
                )
            ),
            evaluation_plans=tuple(
                SelfImprovementEvaluationPlanV1.from_dict(
                    _mapping(item, "evaluation_plan")
                )
                for item in _sequence(
                    payload.get("evaluation_plans", ()),
                    "evaluation_plans",
                )
            ),
            resolved_traces=tuple(
                ResolvedSemanticTraceV1.from_dict(
                    _mapping(item, "resolved_trace")
                )
                for item in _sequence(
                    payload.get("resolved_traces", ()),
                    "resolved_traces",
                )
            ),
            compiled_dataset=CompiledSemanticDatasetV1.from_dict(
                _mapping(
                    payload.get("compiled_dataset"),
                    "compiled_dataset",
                )
            ),
            quality_report=SemanticEvidenceQualityReportV1.from_dict(
                _mapping(
                    payload.get("quality_report"),
                    "quality_report",
                )
            ),
            quality_gate=SemanticQualityGateDecisionV1.from_dict(
                _mapping(
                    payload.get("quality_gate"),
                    "quality_gate",
                )
            ),
            resolution_evidence=SemanticResolutionEvidenceV1.from_dict(
                _mapping(
                    payload.get("resolution_evidence"),
                    "resolution_evidence",
                )
            ),
            authoritative_verification_ids=_strings(
                payload.get("authoritative_verification_ids", ()),
                "authoritative_verification_ids",
            ),
            verification_registry_fingerprint=str(
                payload.get("verification_registry_fingerprint") or ""
            ),
            semantic_model_profile_fingerprint=str(
                payload.get("semantic_model_profile_fingerprint") or ""
            ),
            semantic_provider_fingerprint=str(
                payload.get("semantic_provider_fingerprint") or ""
            ),
            semantic_protocol_fingerprint=str(
                payload.get("semantic_protocol_fingerprint") or ""
            ),
            qualification_registry=SemanticQualificationRegistryV1.from_dict(
                _mapping(
                    payload.get("qualification_registry"),
                    "qualification_registry",
                )
            ),
            qualification_corpus_fingerprint=str(
                payload.get("qualification_corpus_fingerprint") or ""
            ),
            qualification_threshold_set_fingerprint=str(
                payload.get(
                    "qualification_threshold_set_fingerprint"
                )
                or ""
            ),
            qualification_report=(
                SemanticModelQualificationReportV1.from_dict(
                    _mapping(
                        qualification_payload,
                        "qualification_report",
                    )
                )
                if qualification_payload is not None
                else None
            ),
            qualification_evaluated_at_utc=payload.get(
                "qualification_evaluated_at_utc"
            ),
            manifest_fingerprint=payload.get("manifest_fingerprint"),
            source_manifest=(
                _mapping(
                    payload.get("source_manifest"),
                    "source_manifest",
                )
                if payload.get("source_manifest") is not None
                else None
            ),
            canonical_manifest_asset_id=payload.get(
                "canonical_manifest_asset_id"
            ),
            manifest_origin=IngestionManifestOrigin(
                str(payload.get("manifest_origin") or "absent")
            ),
            extractor_fingerprints=_strings(
                payload.get("extractor_fingerprints", ()),
                "extractor_fingerprints",
            ),
            split_fingerprint=payload.get("split_fingerprint"),
            ingestor_name=str(payload.get("ingestor_name") or ""),
            ingestor_version=str(payload.get("ingestor_version") or ""),
            ingestor_trust_level=IngestorTrustLevel(
                str(payload.get("ingestor_trust_level") or "")
            ),
            semantic_consensus_threshold=payload.get(
                "semantic_consensus_threshold"
            ),  # type: ignore[arg-type]
            identity_schema_version=str(
                payload.get("identity_schema_version") or ""
            ),
            normalization_kind=str(
                payload.get("normalization_kind") or ""
            ),
            schema_version=str(payload.get("schema_version") or ""),
        )
        claimed = payload.get("snapshot_fingerprint")
        if (
            claimed is not None
            and claimed != snapshot.snapshot_fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "frozen semantic snapshot fingerprint mismatch",
            )
        for name, actual in (
            (
                "constitution_fingerprint",
                snapshot.constitution.fingerprint,
            ),
            (
                "rollout_policy_fingerprint",
                snapshot.rollout_policy.fingerprint,
            ),
            (
                "semantic_profile_fingerprint",
                snapshot.semantic_profile.fingerprint,
            ),
            (
                "evidence_graph_logical_fingerprint",
                snapshot.evidence_graph.logical_fingerprint,
            ),
            (
                "improvement_signal_set_fingerprint",
                snapshot.improvement_signal_set.fingerprint,
            ),
            (
                "normalized_dataset_fingerprint",
                snapshot.normalized_dataset_fingerprint,
            ),
        ):
            if payload.get(name) != actual:
                raise IngestionContractError(
                    "fingerprint_mismatch",
                    f"frozen semantic snapshot {name} mismatch",
                )
        return snapshot


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


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    return tuple(str(item) for item in _sequence(value, field_name))
