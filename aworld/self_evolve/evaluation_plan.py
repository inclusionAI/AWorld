from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from aworld.self_evolve.evidence import (
    ClaimVerificationOrigin,
    ClaimVerificationVerdict,
    EvidenceClaimKind,
    EvidenceConflictStatus,
    EvidenceResolutionStatus,
    SelfImprovementCaseV1,
    SelfImprovementEvidenceGraphV1,
)
from aworld.self_evolve.improvement_signals import (
    DatasetSplit,
    SelfImprovementSignalSetV1,
    SignalActionability,
    SignalVerificationStatus,
)


SEMANTIC_INGESTION_PROFILE_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_ingestion_profile.v1"
)
HUMAN_EVIDENCE_APPROVAL_SCHEMA_VERSION = (
    "aworld.self_evolve.human_evidence_approval.v1"
)
SEMANTIC_MODEL_QUALIFICATION_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_model_qualification.v1"
)
SELF_IMPROVEMENT_EVALUATION_PLAN_SCHEMA_VERSION = (
    "aworld.self_evolve.evaluation_plan.v1"
)
EVIDENCE_AUTHORITY_CONTEXT_SCHEMA_VERSION = (
    "aworld.self_evolve.evidence_authority_context.v1"
)
SEMANTIC_QUALIFICATION_REGISTRY_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_qualification_registry.v1"
)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_ALIAS_VALUES = 10_000
_MAX_ALIAS_LENGTH = 512


class EvaluationPlanContractError(ValueError):
    """A stable failure in semantic evaluation policy compilation."""

    def __init__(self, reason_code: str, message: str) -> None:
        if not _REASON_PATTERN.fullmatch(reason_code):
            raise ValueError("reason_code must be lower_snake_case")
        self.reason_code = reason_code
        super().__init__(message)


class ComparisonUnit(str, Enum):
    TASK = "task"
    EXECUTION = "execution"
    HARNESS = "harness"


class HumanClaimAuthority(str, Enum):
    ADVISORY = "advisory"
    SOFT_LABEL = "soft_label"
    GROUND_TRUTH = "ground_truth"


class HistoricalJudgeAuthority(str, Enum):
    IGNORED = "ignored"
    ADVISORY = "advisory"
    SCORED_SIGNAL = "scored_signal"


class JudgeRubricPolicy(str, Enum):
    EXACT = "exact"
    COMPATIBLE_ONLY = "compatible_only"
    SEPARATE = "separate"


class AggregationPolicy(str, Enum):
    NONE = "none"
    MAJORITY = "majority"
    WEIGHTED = "weighted"
    MEDIAN = "median"


class ConflictPolicy(str, Enum):
    REQUIRE_REVIEW = "require_review"
    PROPOSAL_ONLY = "proposal_only"
    REJECT = "reject"


class ManifestOrigin(str, Enum):
    OPERATOR_EXPLICIT = "operator_explicit"
    CONVENTIONAL_UNTRUSTED = "conventional_untrusted"
    TRUSTED_REGISTERED_INGESTOR = "trusted_registered_ingestor"
    ABSENT = "absent"


class EvaluationDisposition(str, Enum):
    ELIGIBLE_FOR_VERIFIED_PIPELINE = "eligible_for_verified_pipeline"
    PROPOSAL_ONLY = "proposal_only"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    REJECTED = "rejected"


class QualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    FAILED = "failed"
    EXPIRED = "expired"


_HUMAN_AUTHORITY_RANK = {
    HumanClaimAuthority.ADVISORY: 0,
    HumanClaimAuthority.SOFT_LABEL: 1,
    HumanClaimAuthority.GROUND_TRUTH: 2,
}
_HISTORICAL_AUTHORITY_RANK = {
    HistoricalJudgeAuthority.IGNORED: 0,
    HistoricalJudgeAuthority.ADVISORY: 1,
    HistoricalJudgeAuthority.SCORED_SIGNAL: 2,
}


@dataclass(frozen=True)
class SemanticIngestionProfileV1:
    profile_id: str
    entity_aliases: Mapping[str, Mapping[str, tuple[str, ...]]] = field(
        default_factory=dict
    )
    comparison_unit: ComparisonUnit = ComparisonUnit.TASK
    human_claim_authority: HumanClaimAuthority = (
        HumanClaimAuthority.SOFT_LABEL
    )
    historical_judge_authority: HistoricalJudgeAuthority = (
        HistoricalJudgeAuthority.ADVISORY
    )
    judge_rubric_policy: JudgeRubricPolicy = JudgeRubricPolicy.SEPARATE
    aggregation_policy: AggregationPolicy = AggregationPolicy.NONE
    conflict_policy: ConflictPolicy = ConflictPolicy.REQUIRE_REVIEW
    approved_evidence_graph_fingerprint: str | None = None
    schema_version: str = SEMANTIC_INGESTION_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_INGESTION_PROFILE_SCHEMA_VERSION,
            "semantic ingestion profile",
        )
        _safe_id(self.profile_id, field_name="profile_id")
        object.__setattr__(
            self,
            "comparison_unit",
            ComparisonUnit(self.comparison_unit),
        )
        object.__setattr__(
            self,
            "human_claim_authority",
            HumanClaimAuthority(self.human_claim_authority),
        )
        object.__setattr__(
            self,
            "historical_judge_authority",
            HistoricalJudgeAuthority(self.historical_judge_authority),
        )
        object.__setattr__(
            self,
            "judge_rubric_policy",
            JudgeRubricPolicy(self.judge_rubric_policy),
        )
        object.__setattr__(
            self,
            "aggregation_policy",
            AggregationPolicy(self.aggregation_policy),
        )
        object.__setattr__(
            self,
            "conflict_policy",
            ConflictPolicy(self.conflict_policy),
        )
        normalized = _normalize_aliases(self.entity_aliases)
        object.__setattr__(
            self,
            "entity_aliases",
            MappingProxyType(
                {
                    kind: MappingProxyType(dict(names))
                    for kind, names in normalized.items()
                }
            ),
        )
        if self.approved_evidence_graph_fingerprint is not None:
            _fingerprint(
                self.approved_evidence_graph_fingerprint,
                field_name="approved_evidence_graph_fingerprint",
            )

    @property
    def fingerprint(self) -> str:
        return _fingerprint_json(self.authority_profile_dict())

    def authority_profile_dict(self) -> dict[str, Any]:
        """Return the non-self-referential policy portion of the profile."""

        result = self.to_dict()
        result.pop("approved_evidence_graph_fingerprint", None)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "entity_aliases": {
                kind: {
                    canonical: sorted(aliases)
                    for canonical, aliases in sorted(names.items())
                }
                for kind, names in sorted(self.entity_aliases.items())
            },
            "comparison_unit": self.comparison_unit.value,
            "human_claim_authority": self.human_claim_authority.value,
            "historical_judge_authority": (
                self.historical_judge_authority.value
            ),
            "judge_rubric_policy": self.judge_rubric_policy.value,
            "aggregation_policy": self.aggregation_policy.value,
            "conflict_policy": self.conflict_policy.value,
            "approved_evidence_graph_fingerprint": (
                self.approved_evidence_graph_fingerprint
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticIngestionProfileV1":
        _schema(payload, SEMANTIC_INGESTION_PROFILE_SCHEMA_VERSION)
        aliases: dict[str, dict[str, tuple[str, ...]]] = {}
        for kind, values in _as_mapping(
            payload.get("entity_aliases", {})
        ).items():
            aliases[str(kind)] = {
                str(canonical): tuple(
                    str(item)
                    for item in _sequence(
                        alias_values,
                        "entity_aliases",
                    )
                )
                for canonical, alias_values in _as_mapping(values).items()
            }
        return cls(
            profile_id=str(payload.get("profile_id") or ""),
            entity_aliases=aliases,
            comparison_unit=ComparisonUnit(
                str(payload.get("comparison_unit") or "")
            ),
            human_claim_authority=HumanClaimAuthority(
                str(payload.get("human_claim_authority") or "")
            ),
            historical_judge_authority=HistoricalJudgeAuthority(
                str(payload.get("historical_judge_authority") or "")
            ),
            judge_rubric_policy=JudgeRubricPolicy(
                str(payload.get("judge_rubric_policy") or "")
            ),
            aggregation_policy=AggregationPolicy(
                str(payload.get("aggregation_policy") or "")
            ),
            conflict_policy=ConflictPolicy(
                str(payload.get("conflict_policy") or "")
            ),
            approved_evidence_graph_fingerprint=(
                str(payload["approved_evidence_graph_fingerprint"])
                if payload.get("approved_evidence_graph_fingerprint")
                is not None
                else None
            ),
        )


def default_semantic_ingestion_profile() -> SemanticIngestionProfileV1:
    return SemanticIngestionProfileV1(profile_id="framework-default-v1")


@dataclass(frozen=True)
class HumanEvidenceApprovalV1:
    evidence_graph_logical_fingerprint: str
    manifest_fingerprint: str
    approval_origin: ManifestOrigin
    approved_claim_scope: tuple[str, ...] = ("whole_graph",)
    schema_version: str = HUMAN_EVIDENCE_APPROVAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            HUMAN_EVIDENCE_APPROVAL_SCHEMA_VERSION,
            "human evidence approval",
        )
        _fingerprint(
            self.evidence_graph_logical_fingerprint,
            field_name="evidence_graph_logical_fingerprint",
        )
        _fingerprint(
            self.manifest_fingerprint,
            field_name="manifest_fingerprint",
        )
        object.__setattr__(
            self,
            "approval_origin",
            ManifestOrigin(self.approval_origin),
        )
        if self.approval_origin is not ManifestOrigin.OPERATOR_EXPLICIT:
            raise EvaluationPlanContractError(
                "approval_origin_untrusted",
                "human evidence approval requires an operator-explicit manifest",
            )
        _safe_ids(
            self.approved_claim_scope,
            field_name="approved_claim_scope",
        )
        if not self.approved_claim_scope:
            raise EvaluationPlanContractError(
                "approval_scope_missing",
                "human evidence approval requires a claim scope",
            )
        object.__setattr__(
            self,
            "approved_claim_scope",
            tuple(sorted(self.approved_claim_scope)),
        )

    @property
    def fingerprint(self) -> str:
        return _fingerprint_json(self.to_dict())

    def matches(
        self,
        *,
        graph_fingerprint: str,
        manifest_fingerprint: str,
        manifest_origin: ManifestOrigin,
    ) -> bool:
        return (
            ManifestOrigin(manifest_origin)
            is ManifestOrigin.OPERATOR_EXPLICIT
            and self.evidence_graph_logical_fingerprint
            == graph_fingerprint
            and self.manifest_fingerprint == manifest_fingerprint
        )

    def approves_claim(self, claim_id: str) -> bool:
        _safe_id(claim_id, field_name="claim_id")
        return (
            "whole_graph" in self.approved_claim_scope
            or claim_id in self.approved_claim_scope
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_graph_logical_fingerprint": (
                self.evidence_graph_logical_fingerprint
            ),
            "manifest_fingerprint": self.manifest_fingerprint,
            "approval_origin": self.approval_origin.value,
            "approved_claim_scope": sorted(self.approved_claim_scope),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "HumanEvidenceApprovalV1":
        _schema(payload, HUMAN_EVIDENCE_APPROVAL_SCHEMA_VERSION)
        return cls(
            evidence_graph_logical_fingerprint=str(
                payload.get("evidence_graph_logical_fingerprint") or ""
            ),
            manifest_fingerprint=str(
                payload.get("manifest_fingerprint") or ""
            ),
            approval_origin=ManifestOrigin(
                str(payload.get("approval_origin") or "")
            ),
            approved_claim_scope=_string_tuple(
                payload,
                "approved_claim_scope",
            ),
        )


def issue_human_evidence_approval(
    *,
    profile: SemanticIngestionProfileV1,
    graph_fingerprint: str,
    manifest_fingerprint: str,
    manifest_origin: ManifestOrigin,
    approved_claim_scope: tuple[str, ...] = ("whole_graph",),
) -> HumanEvidenceApprovalV1:
    """Create approval only after the operator pins the exact graph."""

    if ManifestOrigin(manifest_origin) is not ManifestOrigin.OPERATOR_EXPLICIT:
        raise EvaluationPlanContractError(
            "approval_origin_untrusted",
            "only an explicit source manifest can approve evidence",
        )
    if profile.approved_evidence_graph_fingerprint != graph_fingerprint:
        raise EvaluationPlanContractError(
            "approval_graph_fingerprint_mismatch",
            "profile approval does not match the current logical graph",
        )
    return HumanEvidenceApprovalV1(
        evidence_graph_logical_fingerprint=graph_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
        approval_origin=ManifestOrigin.OPERATOR_EXPLICIT,
        approved_claim_scope=approved_claim_scope,
    )


@dataclass(frozen=True)
class EvidenceAuthorityContextV1:
    """Framework-owned attestations for non-agent verification origins."""

    evidence_graph_provenance_fingerprint: str
    verification_registry_fingerprint: str
    deterministic_verification_ids: tuple[str, ...] = ()
    trusted_registered_verification_ids: tuple[str, ...] = ()
    human_approval: HumanEvidenceApprovalV1 | None = None
    schema_version: str = EVIDENCE_AUTHORITY_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            EVIDENCE_AUTHORITY_CONTEXT_SCHEMA_VERSION,
            "evidence authority context",
        )
        _fingerprint(
            self.evidence_graph_provenance_fingerprint,
            field_name="evidence_graph_provenance_fingerprint",
        )
        _fingerprint(
            self.verification_registry_fingerprint,
            field_name="verification_registry_fingerprint",
        )
        _safe_ids(
            self.deterministic_verification_ids,
            field_name="deterministic_verification_ids",
        )
        _safe_ids(
            self.trusted_registered_verification_ids,
            field_name="trusted_registered_verification_ids",
        )

    def authorizes_claim(
        self,
        claim_id: str,
        *,
        graph: SelfImprovementEvidenceGraphV1,
        manifest_origin: ManifestOrigin,
        manifest_fingerprint: str,
    ) -> bool:
        claims = {item.claim_id: item for item in graph.claims}
        if (
            graph.provenance_fingerprint
            != self.evidence_graph_provenance_fingerprint
            or self.verification_registry_fingerprint
            != _evidence_authority_registry_fingerprint(
                graph,
                deterministic_verification_ids=(
                    self.deterministic_verification_ids
                ),
                trusted_registered_verification_ids=(
                    self.trusted_registered_verification_ids
                ),
            )
        ):
            return False
        claim = claims.get(claim_id)
        if claim is None:
            return False
        verifications = {
            item.verification_id: item
            for item in graph.claim_verifications
        }
        approval_matches = (
            self.human_approval is not None
            and self.human_approval.matches(
                graph_fingerprint=graph.logical_fingerprint,
                manifest_fingerprint=manifest_fingerprint,
                manifest_origin=manifest_origin,
            )
            and self.human_approval.approves_claim(claim_id)
        )
        if approval_matches and any(
            verifications[verification_id].verdict
            is ClaimVerificationVerdict.ENTAILED
            for verification_id in claim.verification_ids
        ):
            return True
        for verification_id in claim.verification_ids:
            verification = verifications[verification_id]
            if verification.verdict is not ClaimVerificationVerdict.ENTAILED:
                continue
            if (
                verification.verification_origin
                is ClaimVerificationOrigin.DETERMINISTIC_DECODER
                and verification_id
                in self.deterministic_verification_ids
            ):
                return True
            if (
                verification.verification_origin
                is ClaimVerificationOrigin.TRUSTED_REGISTERED_INGESTOR
                and verification_id
                in self.trusted_registered_verification_ids
            ):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_graph_provenance_fingerprint": (
                self.evidence_graph_provenance_fingerprint
            ),
            "verification_registry_fingerprint": (
                self.verification_registry_fingerprint
            ),
            "deterministic_verification_ids": sorted(
                self.deterministic_verification_ids
            ),
            "trusted_registered_verification_ids": sorted(
                self.trusted_registered_verification_ids
            ),
            "human_approval": (
                self.human_approval.to_dict()
                if self.human_approval is not None
                else None
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "EvidenceAuthorityContextV1":
        _schema(payload, EVIDENCE_AUTHORITY_CONTEXT_SCHEMA_VERSION)
        approval = payload.get("human_approval")
        if approval is not None and not isinstance(approval, Mapping):
            raise EvaluationPlanContractError(
                "schema_invalid",
                "human_approval must be an object",
            )
        return cls(
            evidence_graph_provenance_fingerprint=str(
                payload.get("evidence_graph_provenance_fingerprint")
                or ""
            ),
            verification_registry_fingerprint=str(
                payload.get("verification_registry_fingerprint") or ""
            ),
            deterministic_verification_ids=_string_tuple(
                payload,
                "deterministic_verification_ids",
            ),
            trusted_registered_verification_ids=_string_tuple(
                payload,
                "trusted_registered_verification_ids",
            ),
            human_approval=(
                HumanEvidenceApprovalV1.from_dict(approval)
                if approval is not None
                else None
            ),
        )


def issue_evidence_authority_context(
    graph: SelfImprovementEvidenceGraphV1,
    *,
    deterministic_verification_ids: tuple[str, ...] = (),
    trusted_registered_verification_ids: tuple[str, ...] = (),
    human_approval: HumanEvidenceApprovalV1 | None = None,
) -> EvidenceAuthorityContextV1:
    """Issue a graph-bound authority context from framework-owned IDs."""

    verifications = {
        item.verification_id: item
        for item in graph.claim_verifications
    }
    for verification_id in deterministic_verification_ids:
        verification = verifications.get(verification_id)
        if (
            verification is None
            or verification.verification_origin
            is not ClaimVerificationOrigin.DETERMINISTIC_DECODER
        ):
            raise EvaluationPlanContractError(
                "verification_authority_untrusted",
                "deterministic authority ID has no matching verification",
            )
    for verification_id in trusted_registered_verification_ids:
        verification = verifications.get(verification_id)
        if (
            verification is None
            or verification.verification_origin
            is not ClaimVerificationOrigin.TRUSTED_REGISTERED_INGESTOR
        ):
            raise EvaluationPlanContractError(
                "verification_authority_untrusted",
                "registered authority ID has no matching verification",
            )
    return EvidenceAuthorityContextV1(
        evidence_graph_provenance_fingerprint=(
            graph.provenance_fingerprint
        ),
        verification_registry_fingerprint=(
            _evidence_authority_registry_fingerprint(
                graph,
                deterministic_verification_ids=(
                    deterministic_verification_ids
                ),
                trusted_registered_verification_ids=(
                    trusted_registered_verification_ids
                ),
            )
        ),
        deterministic_verification_ids=(
            deterministic_verification_ids
        ),
        trusted_registered_verification_ids=(
            trusted_registered_verification_ids
        ),
        human_approval=human_approval,
    )


def _evidence_authority_registry_fingerprint(
    graph: SelfImprovementEvidenceGraphV1,
    *,
    deterministic_verification_ids: Sequence[str],
    trusted_registered_verification_ids: Sequence[str],
) -> str:
    verifications = {
        item.verification_id: item
        for item in graph.claim_verifications
    }
    selected_ids = sorted(
        {
            *deterministic_verification_ids,
            *trusted_registered_verification_ids,
        }
    )
    selected = []
    for verification_id in selected_ids:
        verification = verifications.get(verification_id)
        if verification is None:
            selected.append({"verification_id": verification_id})
            continue
        selected.append(
            {
                "verification_id": verification.verification_id,
                "claim_id": verification.claim_id,
                "origin": verification.verification_origin.value,
                "verifier_fingerprint": (
                    verification.verifier_fingerprint
                ),
            }
        )
    return _fingerprint_json(
        {
            "schema_version": (
                "aworld.self_evolve.evidence_authority_registry.v1"
            ),
            "evidence_graph_provenance_fingerprint": (
                graph.provenance_fingerprint
            ),
            "verifications": selected,
        }
    )


@dataclass(frozen=True)
class SemanticModelQualificationReportV1:
    model_profile_fingerprint: str
    provider_fingerprint: str
    semantic_protocol_fingerprint: str
    constitution_fingerprint: str
    corpus_fingerprint: str
    threshold_set_fingerprint: str
    metric_values: Mapping[str, float]
    required_thresholds: Mapping[str, float]
    false_authority_elevation_count: int
    status: QualificationStatus
    schema_version: str = SEMANTIC_MODEL_QUALIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_MODEL_QUALIFICATION_SCHEMA_VERSION,
            "semantic model qualification",
        )
        for name in (
            "model_profile_fingerprint",
            "provider_fingerprint",
            "semantic_protocol_fingerprint",
            "constitution_fingerprint",
            "corpus_fingerprint",
            "threshold_set_fingerprint",
        ):
            _fingerprint(getattr(self, name), field_name=name)
        _finite_metrics(self.metric_values, field_name="metric_values")
        _finite_metrics(
            self.required_thresholds,
            field_name="required_thresholds",
        )
        if not self.metric_values or not self.required_thresholds:
            raise EvaluationPlanContractError(
                "qualification_thresholds_missing",
                "qualification requires non-empty metrics and thresholds",
            )
        object.__setattr__(
            self,
            "metric_values",
            MappingProxyType(
                {
                    str(key): float(value)
                    for key, value in self.metric_values.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "required_thresholds",
            MappingProxyType(
                {
                    str(key): float(value)
                    for key, value in self.required_thresholds.items()
                }
            ),
        )
        if (
            isinstance(self.false_authority_elevation_count, bool)
            or not isinstance(self.false_authority_elevation_count, int)
            or self.false_authority_elevation_count < 0
        ):
            raise EvaluationPlanContractError(
                "qualification_count_invalid",
                "false authority elevation count must be non-negative",
            )
        object.__setattr__(
            self,
            "status",
            QualificationStatus(self.status),
        )

    @property
    def report_fingerprint(self) -> str:
        return _fingerprint_json(self.canonical_dict())

    @property
    def thresholds_satisfied(self) -> bool:
        return all(
            key in self.metric_values
            and self.metric_values[key] >= threshold
            for key, threshold in self.required_thresholds.items()
        )

    def qualifies(
        self,
        *,
        model_profile_fingerprint: str,
        provider_fingerprint: str,
        semantic_protocol_fingerprint: str,
        constitution_fingerprint: str,
        corpus_fingerprint: str,
        threshold_set_fingerprint: str,
    ) -> bool:
        return (
            self.status is QualificationStatus.QUALIFIED
            and self.false_authority_elevation_count == 0
            and self.thresholds_satisfied
            and self.model_profile_fingerprint
            == model_profile_fingerprint
            and self.provider_fingerprint == provider_fingerprint
            and self.semantic_protocol_fingerprint
            == semantic_protocol_fingerprint
            and self.constitution_fingerprint == constitution_fingerprint
            and self.corpus_fingerprint == corpus_fingerprint
            and self.threshold_set_fingerprint
            == threshold_set_fingerprint
        )

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_profile_fingerprint": self.model_profile_fingerprint,
            "provider_fingerprint": self.provider_fingerprint,
            "semantic_protocol_fingerprint": (
                self.semantic_protocol_fingerprint
            ),
            "constitution_fingerprint": self.constitution_fingerprint,
            "corpus_fingerprint": self.corpus_fingerprint,
            "threshold_set_fingerprint": self.threshold_set_fingerprint,
            "metric_values": {
                key: float(value)
                for key, value in sorted(self.metric_values.items())
            },
            "required_thresholds": {
                key: float(value)
                for key, value in sorted(self.required_thresholds.items())
            },
            "false_authority_elevation_count": (
                self.false_authority_elevation_count
            ),
            "status": self.status.value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.canonical_dict(),
            "report_fingerprint": self.report_fingerprint,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticModelQualificationReportV1":
        _schema(payload, SEMANTIC_MODEL_QUALIFICATION_SCHEMA_VERSION)
        report = cls(
            model_profile_fingerprint=str(
                payload.get("model_profile_fingerprint") or ""
            ),
            provider_fingerprint=str(
                payload.get("provider_fingerprint") or ""
            ),
            semantic_protocol_fingerprint=str(
                payload.get("semantic_protocol_fingerprint") or ""
            ),
            constitution_fingerprint=str(
                payload.get("constitution_fingerprint") or ""
            ),
            corpus_fingerprint=str(
                payload.get("corpus_fingerprint") or ""
            ),
            threshold_set_fingerprint=str(
                payload.get("threshold_set_fingerprint") or ""
            ),
            metric_values={
                str(key): float(value)
                for key, value in _as_mapping(
                    payload.get("metric_values", {})
                ).items()
            },
            required_thresholds={
                str(key): float(value)
                for key, value in _as_mapping(
                    payload.get("required_thresholds", {})
                ).items()
            },
            false_authority_elevation_count=payload.get(
                "false_authority_elevation_count"
            ),  # type: ignore[arg-type]
            status=QualificationStatus(
                str(payload.get("status") or "")
            ),
        )
        claimed = payload.get("report_fingerprint")
        if claimed is not None and claimed != report.report_fingerprint:
            raise EvaluationPlanContractError(
                "fingerprint_mismatch",
                "semantic qualification report fingerprint mismatch",
            )
        return report


@dataclass(frozen=True)
class SemanticQualificationRegistryV1:
    """Workspace/framework allowlist of independently issued reports."""

    trusted_report_fingerprints: tuple[str, ...]
    schema_version: str = SEMANTIC_QUALIFICATION_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_QUALIFICATION_REGISTRY_SCHEMA_VERSION,
            "semantic qualification registry",
        )
        if len(self.trusted_report_fingerprints) != len(
            set(self.trusted_report_fingerprints)
        ):
            raise EvaluationPlanContractError(
                "duplicate_reference",
                "qualification report fingerprints must be unique",
            )
        for value in self.trusted_report_fingerprints:
            _fingerprint(
                value,
                field_name="trusted_report_fingerprint",
            )
        object.__setattr__(
            self,
            "trusted_report_fingerprints",
            tuple(sorted(self.trusted_report_fingerprints)),
        )

    def accepts(
        self,
        report: SemanticModelQualificationReportV1 | None,
        *,
        model_profile_fingerprint: str,
        provider_fingerprint: str,
        semantic_protocol_fingerprint: str,
        constitution_fingerprint: str,
        corpus_fingerprint: str,
        threshold_set_fingerprint: str,
    ) -> bool:
        return (
            report is not None
            and report.report_fingerprint
            in self.trusted_report_fingerprints
            and report.qualifies(
                model_profile_fingerprint=model_profile_fingerprint,
                provider_fingerprint=provider_fingerprint,
                semantic_protocol_fingerprint=(
                    semantic_protocol_fingerprint
                ),
                constitution_fingerprint=constitution_fingerprint,
                corpus_fingerprint=corpus_fingerprint,
                threshold_set_fingerprint=threshold_set_fingerprint,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trusted_report_fingerprints": list(
                self.trusted_report_fingerprints
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticQualificationRegistryV1":
        _schema(
            payload,
            SEMANTIC_QUALIFICATION_REGISTRY_SCHEMA_VERSION,
        )
        return cls(
            trusted_report_fingerprints=_string_tuple(
                payload,
                "trusted_report_fingerprints",
            ),
        )


@dataclass(frozen=True)
class SelfImprovementEvaluationPlanV1:
    plan_id: str
    case_id: str
    comparison_unit: ComparisonUnit
    training_signal_ids: tuple[str, ...]
    supporting_evidence_claim_ids: tuple[str, ...]
    replay_seed_execution_id: str | None
    expected_output_claim_id: str | None
    human_claim_authority: HumanClaimAuthority
    historical_judge_authority: HistoricalJudgeAuthority
    rubric_groups: Mapping[str, tuple[str, ...]]
    aggregation_policy: AggregationPolicy
    conflict_policy: ConflictPolicy
    current_evaluator_required: bool
    disposition: EvaluationDisposition
    reason_codes: tuple[str, ...]
    profile_fingerprint: str
    schema_version: str = SELF_IMPROVEMENT_EVALUATION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SELF_IMPROVEMENT_EVALUATION_PLAN_SCHEMA_VERSION,
            "self-improvement evaluation plan",
        )
        _safe_id(self.plan_id, field_name="plan_id")
        _safe_id(self.case_id, field_name="case_id")
        object.__setattr__(
            self,
            "comparison_unit",
            ComparisonUnit(self.comparison_unit),
        )
        object.__setattr__(
            self,
            "human_claim_authority",
            HumanClaimAuthority(self.human_claim_authority),
        )
        object.__setattr__(
            self,
            "historical_judge_authority",
            HistoricalJudgeAuthority(self.historical_judge_authority),
        )
        object.__setattr__(
            self,
            "aggregation_policy",
            AggregationPolicy(self.aggregation_policy),
        )
        object.__setattr__(
            self,
            "conflict_policy",
            ConflictPolicy(self.conflict_policy),
        )
        object.__setattr__(
            self,
            "disposition",
            EvaluationDisposition(self.disposition),
        )
        _safe_ids(
            self.training_signal_ids,
            field_name="training_signal_ids",
        )
        _safe_ids(
            self.supporting_evidence_claim_ids,
            field_name="supporting_evidence_claim_ids",
        )
        object.__setattr__(
            self,
            "training_signal_ids",
            tuple(sorted(self.training_signal_ids)),
        )
        object.__setattr__(
            self,
            "supporting_evidence_claim_ids",
            tuple(sorted(self.supporting_evidence_claim_ids)),
        )
        if self.replay_seed_execution_id is not None:
            _safe_id(
                self.replay_seed_execution_id,
                field_name="replay_seed_execution_id",
            )
        if self.expected_output_claim_id is not None:
            _safe_id(
                self.expected_output_claim_id,
                field_name="expected_output_claim_id",
            )
        if self.current_evaluator_required is not True:
            raise EvaluationPlanContractError(
                "current_evaluator_required",
                "semantic plans cannot disable the current evaluator",
            )
        normalized_groups: dict[str, tuple[str, ...]] = {}
        for group_id, rubric_ids in self.rubric_groups.items():
            _safe_id(group_id, field_name="rubric_groups")
            normalized = tuple(str(item) for item in rubric_ids)
            _safe_ids(normalized, field_name="rubric_groups")
            if not normalized:
                raise EvaluationPlanContractError(
                    "rubric_group_empty",
                    "rubric groups must not be empty",
                )
            normalized_groups[group_id] = normalized
        object.__setattr__(
            self,
            "rubric_groups",
            MappingProxyType(normalized_groups),
        )
        _reason_codes(self.reason_codes)
        object.__setattr__(
            self,
            "reason_codes",
            tuple(sorted(self.reason_codes)),
        )
        _fingerprint(
            self.profile_fingerprint,
            field_name="profile_fingerprint",
        )

    @property
    def plan_fingerprint(self) -> str:
        return _fingerprint_json(self.canonical_dict())

    def validate_references(
        self,
        *,
        graph: SelfImprovementEvidenceGraphV1,
        case: SelfImprovementCaseV1,
        signal_set: SelfImprovementSignalSetV1,
    ) -> None:
        if self.case_id != case.case_id:
            raise EvaluationPlanContractError(
                "evaluation_case_mismatch",
                "evaluation plan and case identities do not match",
            )
        signals = {item.signal_id: item for item in signal_set.signals}
        claims = {item.claim_id for item in graph.claims}
        if (
            signal_set.evidence_graph_logical_fingerprint
            != graph.logical_fingerprint
        ):
            raise EvaluationPlanContractError(
                "signal_graph_fingerprint_mismatch",
                "signal set was compiled from a different evidence graph",
            )
        _refs_exist(
            self.training_signal_ids,
            signals,
            "dangling_signal_reference",
        )
        if any(
            signals[item].case_id != self.case_id
            for item in self.training_signal_ids
        ):
            raise EvaluationPlanContractError(
                "evaluation_signal_case_mismatch",
                "evaluation signals must belong to the same case",
            )
        if any(
            signal_set.case_splits[signals[item].case_id]
            is not DatasetSplit.TRAIN
            for item in self.training_signal_ids
        ):
            raise EvaluationPlanContractError(
                "held_out_signal_exposure",
                "training signals must belong to the train split",
            )
        if any(
            signals[item].actionability
            is not SignalActionability.ACTIONABLE
            or signals[item].verification_status
            is not SignalVerificationStatus.VERIFIED
            for item in self.training_signal_ids
        ):
            raise EvaluationPlanContractError(
                "training_signal_not_actionable",
                "training signals must be verified and actionable",
            )
        case_claim_ids = {
            *case.input_claim_ids,
            *case.trajectory_claim_ids,
            *case.result_claim_ids,
            *case.comparison_claim_ids,
        }
        _refs_exist(
            self.supporting_evidence_claim_ids,
            claims,
            "dangling_claim_reference",
        )
        if not set(self.supporting_evidence_claim_ids).issubset(
            case_claim_ids
        ):
            raise EvaluationPlanContractError(
                "evaluation_claim_outside_case",
                "supporting evidence must belong to the evaluation case",
            )
        if self.expected_output_claim_id is not None:
            _refs_exist(
                (self.expected_output_claim_id,),
                claims,
                "dangling_claim_reference",
            )
            if self.expected_output_claim_id not in case_claim_ids:
                raise EvaluationPlanContractError(
                    "evaluation_claim_outside_case",
                    "expected output evidence must belong to the case",
                )
        if (
            self.replay_seed_execution_id is not None
            and self.replay_seed_execution_id
            not in case.execution_entity_ids
        ):
            raise EvaluationPlanContractError(
                "replay_seed_outside_case",
                "replay seed execution must belong to the case",
            )
        if self.profile_fingerprint != graph.profile_fingerprint:
            raise EvaluationPlanContractError(
                "profile_fingerprint_mismatch",
                "evaluation plan profile differs from the evidence graph",
            )

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "case_id": self.case_id,
            "comparison_unit": self.comparison_unit.value,
            "training_signal_ids": sorted(self.training_signal_ids),
            "supporting_evidence_claim_ids": sorted(
                self.supporting_evidence_claim_ids
            ),
            "replay_seed_execution_id": self.replay_seed_execution_id,
            "expected_output_claim_id": self.expected_output_claim_id,
            "human_claim_authority": self.human_claim_authority.value,
            "historical_judge_authority": (
                self.historical_judge_authority.value
            ),
            "rubric_groups": {
                key: sorted(value)
                for key, value in sorted(self.rubric_groups.items())
            },
            "aggregation_policy": self.aggregation_policy.value,
            "conflict_policy": self.conflict_policy.value,
            "current_evaluator_required": self.current_evaluator_required,
            "disposition": self.disposition.value,
            "reason_codes": sorted(self.reason_codes),
            "profile_fingerprint": self.profile_fingerprint,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.canonical_dict(),
            "plan_fingerprint": self.plan_fingerprint,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SelfImprovementEvaluationPlanV1":
        _schema(payload, SELF_IMPROVEMENT_EVALUATION_PLAN_SCHEMA_VERSION)
        plan = cls(
            plan_id=str(payload.get("plan_id") or ""),
            case_id=str(payload.get("case_id") or ""),
            comparison_unit=ComparisonUnit(
                str(payload.get("comparison_unit") or "")
            ),
            training_signal_ids=_string_tuple(
                payload,
                "training_signal_ids",
            ),
            supporting_evidence_claim_ids=_string_tuple(
                payload,
                "supporting_evidence_claim_ids",
            ),
            replay_seed_execution_id=(
                str(payload["replay_seed_execution_id"])
                if payload.get("replay_seed_execution_id") is not None
                else None
            ),
            expected_output_claim_id=(
                str(payload["expected_output_claim_id"])
                if payload.get("expected_output_claim_id") is not None
                else None
            ),
            human_claim_authority=HumanClaimAuthority(
                str(payload.get("human_claim_authority") or "")
            ),
            historical_judge_authority=HistoricalJudgeAuthority(
                str(payload.get("historical_judge_authority") or "")
            ),
            rubric_groups={
                str(key): tuple(
                    str(item)
                    for item in _sequence(value, "rubric_groups")
                )
                for key, value in _as_mapping(
                    payload.get("rubric_groups", {})
                ).items()
            },
            aggregation_policy=AggregationPolicy(
                str(payload.get("aggregation_policy") or "")
            ),
            conflict_policy=ConflictPolicy(
                str(payload.get("conflict_policy") or "")
            ),
            current_evaluator_required=payload.get(
                "current_evaluator_required"
            ),  # type: ignore[arg-type]
            disposition=EvaluationDisposition(
                str(payload.get("disposition") or "")
            ),
            reason_codes=_string_tuple(payload, "reason_codes"),
            profile_fingerprint=str(
                payload.get("profile_fingerprint") or ""
            ),
        )
        claimed = payload.get("plan_fingerprint")
        if claimed is not None and claimed != plan.plan_fingerprint:
            raise EvaluationPlanContractError(
                "fingerprint_mismatch",
                "self-improvement evaluation plan fingerprint mismatch",
            )
        return plan


def effective_profile_for_origin(
    profile: SemanticIngestionProfileV1,
    *,
    manifest_origin: ManifestOrigin,
    approval: HumanEvidenceApprovalV1 | None = None,
    graph_fingerprint: str | None = None,
    manifest_fingerprint: str | None = None,
) -> SemanticIngestionProfileV1:
    """Apply the deterministic authority ceiling for a manifest origin."""

    origin = ManifestOrigin(manifest_origin)
    framework_default = default_semantic_ingestion_profile()
    human_ceiling = profile.human_claim_authority
    historical_ceiling = profile.historical_judge_authority
    rubric_policy = profile.judge_rubric_policy
    aggregation_policy = profile.aggregation_policy
    conflict_policy = profile.conflict_policy

    if origin in {
        ManifestOrigin.ABSENT,
        ManifestOrigin.CONVENTIONAL_UNTRUSTED,
    }:
        human_ceiling = _min_human_authority(
            human_ceiling,
            framework_default.human_claim_authority,
        )
        historical_ceiling = _min_historical_authority(
            historical_ceiling,
            framework_default.historical_judge_authority,
        )
        rubric_policy = framework_default.judge_rubric_policy
        aggregation_policy = framework_default.aggregation_policy
        conflict_policy = framework_default.conflict_policy
    elif (
        origin is ManifestOrigin.OPERATOR_EXPLICIT
        and human_ceiling is HumanClaimAuthority.GROUND_TRUTH
    ):
        approval_valid = (
            approval is not None
            and graph_fingerprint is not None
            and manifest_fingerprint is not None
            and approval.matches(
                graph_fingerprint=graph_fingerprint,
                manifest_fingerprint=manifest_fingerprint,
                manifest_origin=origin,
            )
        )
        if not approval_valid:
            human_ceiling = HumanClaimAuthority.SOFT_LABEL

    return replace(
        profile,
        human_claim_authority=human_ceiling,
        historical_judge_authority=historical_ceiling,
        judge_rubric_policy=rubric_policy,
        aggregation_policy=aggregation_policy,
        conflict_policy=conflict_policy,
        approved_evidence_graph_fingerprint=None,
    )


def compile_evaluation_plan(
    proposal: SelfImprovementEvaluationPlanV1,
    *,
    profile: SemanticIngestionProfileV1,
    manifest_origin: ManifestOrigin,
    manifest_fingerprint: str,
    graph: SelfImprovementEvidenceGraphV1,
    case: SelfImprovementCaseV1,
    signal_set: SelfImprovementSignalSetV1,
    authority_context: EvidenceAuthorityContextV1,
    qualification_report: SemanticModelQualificationReportV1 | None,
    qualification_registry: SemanticQualificationRegistryV1,
    model_profile_fingerprint: str,
    provider_fingerprint: str,
    semantic_protocol_fingerprint: str,
    constitution_fingerprint: str,
    qualification_corpus_fingerprint: str,
    qualification_threshold_set_fingerprint: str,
) -> SelfImprovementEvaluationPlanV1:
    """Compile policy from frozen facts; agent fields are suggestions only."""

    effective = effective_profile_for_origin(
        profile,
        manifest_origin=manifest_origin,
        approval=authority_context.human_approval,
        graph_fingerprint=graph.logical_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
    )
    if graph.profile_fingerprint != effective.fingerprint:
        raise EvaluationPlanContractError(
            "profile_fingerprint_mismatch",
            "evidence graph was not built with the effective profile",
        )
    human_authority = _min_human_authority(
        proposal.human_claim_authority,
        effective.human_claim_authority,
    )
    historical_authority = _min_historical_authority(
        proposal.historical_judge_authority,
        effective.historical_judge_authority,
    )
    reasons: set[str] = set()
    disposition = EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
    if human_authority is not proposal.human_claim_authority:
        reasons.add("human_authority_clamped")
        if proposal.expected_output_claim_id is not None:
            disposition = _more_restrictive_disposition(
                disposition,
                EvaluationDisposition.HUMAN_REVIEW_REQUIRED,
            )
        else:
            disposition = _more_restrictive_disposition(
                disposition,
                EvaluationDisposition.PROPOSAL_ONLY,
            )
    if historical_authority is not proposal.historical_judge_authority:
        reasons.add("historical_judge_authority_clamped")
    model_qualified = qualification_registry.accepts(
        qualification_report,
        model_profile_fingerprint=model_profile_fingerprint,
        provider_fingerprint=provider_fingerprint,
        semantic_protocol_fingerprint=semantic_protocol_fingerprint,
        constitution_fingerprint=constitution_fingerprint,
        corpus_fingerprint=qualification_corpus_fingerprint,
        threshold_set_fingerprint=(
            qualification_threshold_set_fingerprint
        ),
    )
    if not model_qualified:
        reasons.add("semantic_model_not_qualified")
        disposition = _more_restrictive_disposition(
            disposition,
            EvaluationDisposition.PROPOSAL_ONLY,
        )
    conflicts = {
        item.conflict_id: item for item in graph.conflicts
    }
    unresolved_conflicts = [
        conflicts[item]
        for item in case.conflict_ids
        if conflicts[item].status is EvidenceConflictStatus.UNRESOLVED
    ]
    if unresolved_conflicts:
        reasons.add("unresolved_semantic_conflict")
    if (
        effective.conflict_policy is ConflictPolicy.REQUIRE_REVIEW
        and unresolved_conflicts
    ):
        disposition = _more_restrictive_disposition(
            disposition,
            EvaluationDisposition.HUMAN_REVIEW_REQUIRED,
        )
    elif (
        effective.conflict_policy is ConflictPolicy.PROPOSAL_ONLY
        and unresolved_conflicts
    ):
        disposition = _more_restrictive_disposition(
            disposition,
            EvaluationDisposition.PROPOSAL_ONLY,
        )
    elif (
        effective.conflict_policy is ConflictPolicy.REJECT
        and unresolved_conflicts
    ):
        disposition = EvaluationDisposition.REJECTED

    if case.resolution_status.value == "rejected":
        reasons.add("self_improvement_case_rejected")
        disposition = EvaluationDisposition.REJECTED
    elif case.resolution_status.value == "ambiguous":
        reasons.add("self_improvement_case_ambiguous")
        disposition = _more_restrictive_disposition(
            disposition,
            EvaluationDisposition.HUMAN_REVIEW_REQUIRED,
        )
    if graph.unresolved_references:
        reasons.add("unresolved_evidence_reference")
        disposition = _more_restrictive_disposition(
            disposition,
            EvaluationDisposition.HUMAN_REVIEW_REQUIRED,
        )

    claims = {item.claim_id: item for item in graph.claims}
    signals = {
        item.signal_id: item for item in signal_set.signals
    }
    _refs_exist(
        proposal.training_signal_ids,
        signals,
        "dangling_signal_reference",
    )
    adopted_signals = [
        signals[item] for item in proposal.training_signal_ids
    ]
    for signal in adopted_signals:
        signal.validate_against(graph, case)
    derived_claim_ids = {
        claim_id
        for signal in adopted_signals
        for claim_id in (
            *signal.supporting_claim_ids,
            *signal.opposing_claim_ids,
            *signal.behavior_delta.source_claim_ids,
        )
    }
    if set(proposal.supporting_evidence_claim_ids) != derived_claim_ids:
        reasons.add("supporting_evidence_recompiled")
    if (
        signal_set.case_splits.get(case.case_id) is DatasetSplit.TRAIN
        and not adopted_signals
    ):
        reasons.add("trainable_signal_missing")
        disposition = _more_restrictive_disposition(
            disposition,
            EvaluationDisposition.PROPOSAL_ONLY,
        )
    authoritative_claim_ids = {
        claim_id
        for claim_id in derived_claim_ids
        if authority_context.authorizes_claim(
            claim_id,
            graph=graph,
            manifest_origin=manifest_origin,
            manifest_fingerprint=manifest_fingerprint,
        )
    }
    if derived_claim_ids - authoritative_claim_ids:
        reasons.add("supporting_evidence_not_authoritative")
        disposition = _more_restrictive_disposition(
            disposition,
            EvaluationDisposition.PROPOSAL_ONLY,
        )
    if any(
        claims[claim_id].resolution_status
        is not EvidenceResolutionStatus.RESOLVED
        for claim_id in derived_claim_ids
        if claim_id in claims
    ):
        reasons.add("supporting_evidence_unresolved")
        disposition = _more_restrictive_disposition(
            disposition,
            EvaluationDisposition.HUMAN_REVIEW_REQUIRED,
        )
    expected_output_claim_id = proposal.expected_output_claim_id
    if expected_output_claim_id is not None:
        expected_claim = claims.get(expected_output_claim_id)
        verifications = {
            item.verification_id: item
            for item in graph.claim_verifications
        }
        expected_valid = (
            expected_claim is not None
            and expected_output_claim_id in case.result_claim_ids
            and expected_claim.kind
            is EvidenceClaimKind.EXECUTION_RESULT
            and expected_claim.resolution_status
            is EvidenceResolutionStatus.RESOLVED
            and len(expected_claim.subject_entity_ids) == 1
            and expected_claim.subject_entity_ids[0]
            in case.execution_entity_ids
            and all(
                verifications[item].verdict
                not in {
                    ClaimVerificationVerdict.CONTRADICTED,
                    ClaimVerificationVerdict.INSUFFICIENT,
                    ClaimVerificationVerdict.AMBIGUOUS,
                }
                for item in expected_claim.verification_ids
            )
        )
        if not expected_valid:
            reasons.add("expected_output_claim_invalid")
            expected_output_claim_id = None
            disposition = _more_restrictive_disposition(
                disposition,
                EvaluationDisposition.HUMAN_REVIEW_REQUIRED,
            )
    if expected_output_claim_id is not None:
        expected_authorized = authority_context.authorizes_claim(
            expected_output_claim_id,
            graph=graph,
            manifest_origin=manifest_origin,
            manifest_fingerprint=manifest_fingerprint,
        )
        if not expected_authorized:
            reasons.add("expected_output_not_authoritative")
            disposition = _more_restrictive_disposition(
                disposition,
                EvaluationDisposition.HUMAN_REVIEW_REQUIRED,
            )

    rubric_groups = _compile_rubric_groups(
        graph,
        case,
        effective.judge_rubric_policy,
    )
    if dict(proposal.rubric_groups) != rubric_groups:
        reasons.add("rubric_groups_recompiled")

    compiled = replace(
        proposal,
        supporting_evidence_claim_ids=tuple(sorted(derived_claim_ids)),
        expected_output_claim_id=(
            expected_output_claim_id
            if disposition
            is EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE
            else None
        ),
        comparison_unit=effective.comparison_unit,
        human_claim_authority=human_authority,
        historical_judge_authority=historical_authority,
        aggregation_policy=effective.aggregation_policy,
        conflict_policy=effective.conflict_policy,
        rubric_groups=rubric_groups,
        current_evaluator_required=True,
        disposition=disposition,
        reason_codes=tuple(sorted(reasons)),
        profile_fingerprint=effective.fingerprint,
    )
    compiled.validate_references(
        graph=graph,
        case=case,
        signal_set=signal_set,
    )
    return compiled


def _compile_rubric_groups(
    graph: SelfImprovementEvidenceGraphV1,
    case: SelfImprovementCaseV1,
    policy: JudgeRubricPolicy,
) -> dict[str, tuple[str, ...]]:
    """Fail closed: only identical rubric IDs are known compatible in v1."""

    claims = {item.claim_id: item for item in graph.claims}
    rubric_ids = sorted(
        {
            str(claims[claim_id].payload["rubric_id"])
            for claim_id in case.comparison_claim_ids
            if (
                claim_id in claims
                and claims[claim_id].kind
                is EvidenceClaimKind.LLM_JUDGE_ASSESSMENT
            )
        }
    )
    prefix = {
        JudgeRubricPolicy.EXACT: "exact",
        JudgeRubricPolicy.COMPATIBLE_ONLY: "compatible",
        JudgeRubricPolicy.SEPARATE: "separate",
    }[policy]
    return {
        f"rubric-group:{prefix}:{hashlib.sha256(rubric_id.encode('utf-8')).hexdigest()[:16]}": (
            rubric_id,
        )
        for rubric_id in rubric_ids
    }


def _more_restrictive_disposition(
    left: EvaluationDisposition,
    right: EvaluationDisposition,
) -> EvaluationDisposition:
    rank = {
        EvaluationDisposition.ELIGIBLE_FOR_VERIFIED_PIPELINE: 0,
        EvaluationDisposition.PROPOSAL_ONLY: 1,
        EvaluationDisposition.HUMAN_REVIEW_REQUIRED: 2,
        EvaluationDisposition.REJECTED: 3,
    }
    return max((left, right), key=lambda value: rank[value])


def _min_human_authority(
    left: HumanClaimAuthority,
    right: HumanClaimAuthority,
) -> HumanClaimAuthority:
    return min(
        (left, right),
        key=lambda value: _HUMAN_AUTHORITY_RANK[value],
    )


def _min_historical_authority(
    left: HistoricalJudgeAuthority,
    right: HistoricalJudgeAuthority,
) -> HistoricalJudgeAuthority:
    return min(
        (left, right),
        key=lambda value: _HISTORICAL_AUTHORITY_RANK[value],
    )


def _normalize_aliases(
    aliases: Mapping[str, Mapping[str, tuple[str, ...]]],
) -> dict[str, dict[str, tuple[str, ...]]]:
    result: dict[str, dict[str, tuple[str, ...]]] = {}
    count = 0
    for raw_kind, raw_names in aliases.items():
        kind = str(raw_kind)
        _safe_id(kind, field_name="entity_aliases")
        names: dict[str, tuple[str, ...]] = {}
        for raw_canonical, raw_aliases in raw_names.items():
            canonical = str(raw_canonical)
            _safe_id(canonical, field_name="entity_aliases")
            normalized = tuple(
                sorted(
                    str(item)
                    for item in _sequence(
                        raw_aliases,
                        "entity_aliases",
                    )
                )
            )
            if len(normalized) != len(set(normalized)):
                raise EvaluationPlanContractError(
                    "duplicate_alias",
                    "entity aliases must be unique",
                )
            for alias in normalized:
                if (
                    not alias.strip()
                    or len(alias) > _MAX_ALIAS_LENGTH
                ):
                    raise EvaluationPlanContractError(
                        "alias_invalid",
                        "entity alias must be a bounded non-empty string",
                    )
            count += len(normalized)
            names[canonical] = normalized
        result[kind] = names
    if count > _MAX_ALIAS_VALUES:
        raise EvaluationPlanContractError(
            "alias_limit_exceeded",
            "semantic profile contains too many aliases",
        )
    return result


def _finite_metrics(values: Mapping[str, float], *, field_name: str) -> None:
    for key, value in values.items():
        _safe_id(key, field_name=field_name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise EvaluationPlanContractError(
                "qualification_metric_invalid",
                f"{field_name} values must be finite numbers",
            )


def _require_schema(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise EvaluationPlanContractError(
            "unsupported_schema_version",
            f"unsupported {label} schema version: {actual!r}",
        )


def _schema(payload: Mapping[str, Any], expected: str) -> None:
    _require_schema(
        str(payload.get("schema_version") or ""),
        expected,
        expected,
    )


def _safe_id(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID_PATTERN.fullmatch(value):
        raise EvaluationPlanContractError(
            "schema_invalid",
            f"{field_name} must be a bounded stable identifier",
        )


def _safe_ids(values: Sequence[str], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise EvaluationPlanContractError(
            "duplicate_reference",
            f"{field_name} must not contain duplicates",
        )
    for value in values:
        _safe_id(value, field_name=field_name)


def _fingerprint(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise EvaluationPlanContractError(
            "fingerprint_invalid",
            f"{field_name} must be a sha256 fingerprint",
        )


def _reason_codes(values: Sequence[str]) -> None:
    if len(values) != len(set(values)):
        raise EvaluationPlanContractError(
            "duplicate_reason_code",
            "reason_codes must not contain duplicates",
        )
    for value in values:
        if not isinstance(value, str) or not _REASON_PATTERN.fullmatch(value):
            raise EvaluationPlanContractError(
                "reason_code_invalid",
                "reason codes must be lower_snake_case",
            )


def _refs_exist(
    references: Sequence[str],
    index: Mapping[str, Any] | set[str],
    reason_code: str,
) -> None:
    missing = [item for item in references if item not in index]
    if missing:
        raise EvaluationPlanContractError(
            reason_code,
            f"unknown reference: {missing[0]}",
        )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationPlanContractError(
            "schema_invalid",
            "expected an object",
        )
    return value


def _string_tuple(
    payload: Mapping[str, Any],
    name: str,
) -> tuple[str, ...]:
    return tuple(
        str(item)
        for item in _sequence(payload.get(name, ()), name)
    )


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise EvaluationPlanContractError(
            "schema_invalid",
            f"{field_name} must be an array",
        )
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvaluationPlanContractError(
            "schema_invalid",
            "value is not canonical JSON",
        ) from exc


def _fingerprint_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()
