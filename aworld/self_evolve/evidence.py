from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence


EVIDENCE_SOURCE_SPAN_SCHEMA_VERSION = (
    "aworld.self_evolve.evidence_source_span.v1"
)
EVIDENCE_ENTITY_SCHEMA_VERSION = "aworld.self_evolve.evidence_entity.v1"
EVIDENCE_CLAIM_SCHEMA_VERSION = "aworld.self_evolve.evidence_claim.v1"
CLAIM_VERIFICATION_SCHEMA_VERSION = (
    "aworld.self_evolve.claim_verification.v1"
)
SEMANTIC_SOURCE_DISPOSITION_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_source_disposition.v1"
)
EVIDENCE_CONFLICT_SCHEMA_VERSION = "aworld.self_evolve.evidence_conflict.v1"
SELF_IMPROVEMENT_EVIDENCE_GRAPH_SCHEMA_VERSION = (
    "aworld.self_evolve.evidence_graph.v1"
)
SELF_IMPROVEMENT_CASE_SCHEMA_VERSION = (
    "aworld.self_evolve.self_improvement_case.v1"
)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class EvidenceContractError(ValueError):
    """A stable failure in semantic self-improvement evidence."""

    def __init__(self, reason_code: str, message: str) -> None:
        if not _REASON_PATTERN.fullmatch(reason_code):
            raise ValueError("reason_code must be lower_snake_case")
        self.reason_code = reason_code
        super().__init__(message)


class EvidenceEntityKind(str, Enum):
    TASK = "task"
    HARNESS = "harness"
    EXECUTION = "execution"
    RESULT = "result"
    ARTIFACT = "artifact"
    RUBRIC = "rubric"
    REVIEWER = "reviewer"


class EvidenceClaimKind(str, Enum):
    TASK_INPUT = "task_input"
    EXECUTION_TRAJECTORY = "execution_trajectory"
    EXECUTION_RESULT = "execution_result"
    METRIC_OBSERVATION = "metric_observation"
    HUMAN_COMPARISON = "human_comparison"
    LLM_JUDGE_ASSESSMENT = "llm_judge_assessment"
    EXPLICIT_RELATION = "explicit_relation"


class EvidenceProducerKind(str, Enum):
    DETERMINISTIC_DECODER = "deterministic_decoder"
    SEMANTIC_AGENT = "semantic_agent"
    REGISTERED_INGESTOR = "registered_ingestor"


class EvidenceResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


class ClaimVerificationVerdict(str, Enum):
    ENTAILED = "entailed"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"
    AMBIGUOUS = "ambiguous"


class ClaimVerificationOrigin(str, Enum):
    DETERMINISTIC_DECODER = "deterministic_decoder"
    TRUSTED_REGISTERED_INGESTOR = "trusted_registered_ingestor"
    HUMAN_APPROVED = "human_approved"
    SEMANTIC_AGENT = "semantic_agent"


class SemanticSourceDispositionKind(str, Enum):
    EVIDENCE = "evidence"
    IRRELEVANT = "irrelevant"
    UNRESOLVED = "unresolved"
    DEFERRED = "deferred"


class EvidenceConflictKind(str, Enum):
    PREFERENCE_DISAGREEMENT = "preference_disagreement"
    SCORE_INCOMPATIBLE = "score_incompatible"
    RUBRIC_INCOMPATIBLE = "rubric_incompatible"
    ENTITY_AMBIGUITY = "entity_ambiguity"
    TRAJECTORY_IDENTITY_COLLISION = "trajectory_identity_collision"


class EvidenceConflictStatus(str, Enum):
    UNRESOLVED = "unresolved"
    POLICY_RESOLVED = "policy_resolved"
    INFORMATIONAL = "informational"


class SelfImprovementCaseResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


@dataclass(frozen=True)
class EvidenceSourceSpanV1:
    span_id: str
    asset_id: str
    chunk_id: str
    byte_start: int
    byte_end: int
    line_start: int
    line_end: int
    content_fingerprint: str
    schema_version: str = EVIDENCE_SOURCE_SPAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            EVIDENCE_SOURCE_SPAN_SCHEMA_VERSION,
            "evidence source span",
        )
        _safe_id(self.span_id, field_name="span_id")
        _fingerprint(self.asset_id, field_name="asset_id")
        _safe_id(self.chunk_id, field_name="chunk_id")
        _fingerprint(
            self.content_fingerprint,
            field_name="content_fingerprint",
        )
        for name in ("byte_start", "byte_end", "line_start", "line_end"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise EvidenceContractError(
                    "source_span_invalid",
                    f"{name} must be an integer",
                )
        if self.byte_start < 0 or self.byte_end <= self.byte_start:
            raise EvidenceContractError(
                "source_span_invalid",
                "source byte range must be non-empty and ordered",
            )
        if self.line_start < 1 or self.line_end < self.line_start:
            raise EvidenceContractError(
                "source_span_invalid",
                "source line range must be 1-based and ordered",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "span_id": self.span_id,
            "asset_id": self.asset_id,
            "chunk_id": self.chunk_id,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceSourceSpanV1":
        _schema(payload, EVIDENCE_SOURCE_SPAN_SCHEMA_VERSION)
        return cls(
            span_id=str(payload.get("span_id") or ""),
            asset_id=str(payload.get("asset_id") or ""),
            chunk_id=str(payload.get("chunk_id") or ""),
            byte_start=payload.get("byte_start"),  # type: ignore[arg-type]
            byte_end=payload.get("byte_end"),  # type: ignore[arg-type]
            line_start=payload.get("line_start"),  # type: ignore[arg-type]
            line_end=payload.get("line_end"),  # type: ignore[arg-type]
            content_fingerprint=str(
                payload.get("content_fingerprint") or ""
            ),
        )


@dataclass(frozen=True)
class EvidenceEntityV1:
    entity_id: str
    kind: EvidenceEntityKind
    canonical_name: str
    aliases: tuple[str, ...] = ()
    source_span_ids: tuple[str, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = EVIDENCE_ENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            EVIDENCE_ENTITY_SCHEMA_VERSION,
            "evidence entity",
        )
        _safe_id(self.entity_id, field_name="entity_id")
        object.__setattr__(self, "kind", EvidenceEntityKind(self.kind))
        if (
            not isinstance(self.canonical_name, str)
            or not self.canonical_name.strip()
            or len(self.canonical_name) > 512
        ):
            raise EvidenceContractError(
                "schema_invalid",
                "canonical_name must be a bounded non-empty string",
            )
        _unique_strings(self.aliases, field_name="aliases", max_length=512)
        _safe_ids(self.source_span_ids, field_name="source_span_ids")
        object.__setattr__(self, "aliases", tuple(sorted(self.aliases)))
        object.__setattr__(
            self,
            "source_span_ids",
            tuple(sorted(self.source_span_ids)),
        )
        object.__setattr__(
            self,
            "attributes",
            _freeze_json_mapping(self.attributes),
        )

    def logical_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind.value,
            "canonical_name": self.canonical_name.strip(),
            "aliases": sorted(self.aliases),
            "attributes": _json_value(self.attributes),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            **self.logical_dict(),
            "source_span_ids": list(self.source_span_ids),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceEntityV1":
        _schema(payload, EVIDENCE_ENTITY_SCHEMA_VERSION)
        return cls(
            entity_id=str(payload.get("entity_id") or ""),
            kind=EvidenceEntityKind(str(payload.get("kind") or "")),
            canonical_name=str(payload.get("canonical_name") or ""),
            aliases=_string_tuple(payload, "aliases"),
            source_span_ids=_string_tuple(payload, "source_span_ids"),
            attributes=_as_mapping(payload.get("attributes", {})),
        )


@dataclass(frozen=True)
class EvidenceClaimV1:
    claim_id: str
    kind: EvidenceClaimKind
    subject_entity_ids: tuple[str, ...]
    object_entity_ids: tuple[str, ...]
    payload: Mapping[str, Any]
    source_span_ids: tuple[str, ...]
    producer_kind: EvidenceProducerKind
    resolution_status: EvidenceResolutionStatus
    verification_ids: tuple[str, ...]
    agent_confidence: float | None = None
    schema_version: str = EVIDENCE_CLAIM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            EVIDENCE_CLAIM_SCHEMA_VERSION,
            "evidence claim",
        )
        _safe_id(self.claim_id, field_name="claim_id")
        object.__setattr__(self, "kind", EvidenceClaimKind(self.kind))
        object.__setattr__(
            self,
            "producer_kind",
            EvidenceProducerKind(self.producer_kind),
        )
        object.__setattr__(
            self,
            "resolution_status",
            EvidenceResolutionStatus(self.resolution_status),
        )
        _safe_ids(self.subject_entity_ids, field_name="subject_entity_ids")
        _safe_ids(self.object_entity_ids, field_name="object_entity_ids")
        _safe_ids(self.source_span_ids, field_name="source_span_ids")
        _safe_ids(self.verification_ids, field_name="verification_ids")
        object.__setattr__(
            self,
            "subject_entity_ids",
            tuple(sorted(self.subject_entity_ids)),
        )
        object.__setattr__(
            self,
            "object_entity_ids",
            tuple(sorted(self.object_entity_ids)),
        )
        object.__setattr__(
            self,
            "source_span_ids",
            tuple(sorted(self.source_span_ids)),
        )
        object.__setattr__(
            self,
            "verification_ids",
            tuple(sorted(self.verification_ids)),
        )
        if not self.subject_entity_ids:
            raise EvidenceContractError(
                "claim_subject_missing",
                "claim requires at least one subject entity",
            )
        if self.resolution_status is not EvidenceResolutionStatus.REJECTED:
            if not self.source_span_ids:
                raise EvidenceContractError(
                    "claim_citation_missing",
                    "accepted or ambiguous claim requires a source citation",
                )
            if not self.verification_ids:
                raise EvidenceContractError(
                    "claim_verification_missing",
                    "accepted or ambiguous claim requires verification",
                )
        if self.kind is EvidenceClaimKind.HUMAN_COMPARISON and len(
            self.object_entity_ids
        ) < 2:
            raise EvidenceContractError(
                "comparison_incomplete",
                "human comparison requires at least two compared entities",
            )
        if self.kind is EvidenceClaimKind.LLM_JUDGE_ASSESSMENT and not (
            self.object_entity_ids
        ):
            raise EvidenceContractError(
                "comparison_incomplete",
                "judge assessment requires an assessed entity",
            )
        if (
            self.kind
            in {
                EvidenceClaimKind.EXECUTION_TRAJECTORY,
                EvidenceClaimKind.EXECUTION_RESULT,
            }
            and len(self.subject_entity_ids) != 1
        ):
            raise EvidenceContractError(
                "claim_subject_invalid",
                "execution evidence requires exactly one subject",
            )
        object.__setattr__(
            self,
            "payload",
            _freeze_json_mapping(self.payload),
        )
        _validate_claim_payload(
            self.kind,
            self.payload,
            object_entity_ids=self.object_entity_ids,
        )
        if self.agent_confidence is not None:
            confidence = self.agent_confidence
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not math.isfinite(float(confidence))
                or float(confidence) < 0.0
                or float(confidence) > 1.0
            ):
                raise EvidenceContractError(
                    "invalid_rate",
                    "agent_confidence must be between 0 and 1",
                )
            object.__setattr__(self, "agent_confidence", float(confidence))

    def logical_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "kind": self.kind.value,
            "subject_entity_ids": sorted(self.subject_entity_ids),
            "object_entity_ids": sorted(self.object_entity_ids),
            "payload": _json_value(self.payload),
            "resolution_status": self.resolution_status.value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "claim_id": self.claim_id,
            "kind": self.kind.value,
            "subject_entity_ids": list(self.subject_entity_ids),
            "object_entity_ids": list(self.object_entity_ids),
            "payload": _json_value(self.payload),
            "producer_kind": self.producer_kind.value,
            "resolution_status": self.resolution_status.value,
            "source_span_ids": list(self.source_span_ids),
            "verification_ids": list(self.verification_ids),
            "agent_confidence": self.agent_confidence,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceClaimV1":
        _schema(payload, EVIDENCE_CLAIM_SCHEMA_VERSION)
        return cls(
            claim_id=str(payload.get("claim_id") or ""),
            kind=EvidenceClaimKind(str(payload.get("kind") or "")),
            subject_entity_ids=_string_tuple(
                payload,
                "subject_entity_ids",
            ),
            object_entity_ids=_string_tuple(
                payload,
                "object_entity_ids",
            ),
            payload=_as_mapping(payload.get("payload", {})),
            source_span_ids=_string_tuple(payload, "source_span_ids"),
            producer_kind=EvidenceProducerKind(
                str(payload.get("producer_kind") or "")
            ),
            resolution_status=EvidenceResolutionStatus(
                str(payload.get("resolution_status") or "")
            ),
            verification_ids=_string_tuple(payload, "verification_ids"),
            agent_confidence=payload.get("agent_confidence"),
        )


@dataclass(frozen=True)
class ClaimVerificationV1:
    verification_id: str
    claim_id: str
    verdict: ClaimVerificationVerdict
    verification_origin: ClaimVerificationOrigin
    verifier_fingerprint: str
    independence_group: str
    source_span_ids: tuple[str, ...]
    rationale_codes: tuple[str, ...] = ()
    schema_version: str = CLAIM_VERIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            CLAIM_VERIFICATION_SCHEMA_VERSION,
            "claim verification",
        )
        _safe_id(self.verification_id, field_name="verification_id")
        _safe_id(self.claim_id, field_name="claim_id")
        object.__setattr__(
            self,
            "verdict",
            ClaimVerificationVerdict(self.verdict),
        )
        object.__setattr__(
            self,
            "verification_origin",
            ClaimVerificationOrigin(self.verification_origin),
        )
        _fingerprint(
            self.verifier_fingerprint,
            field_name="verifier_fingerprint",
        )
        _safe_id(self.independence_group, field_name="independence_group")
        _safe_ids(self.source_span_ids, field_name="source_span_ids")
        if not self.source_span_ids:
            raise EvidenceContractError(
                "claim_citation_missing",
                "claim verification requires source spans",
            )
        _reason_codes(self.rationale_codes)
        object.__setattr__(
            self,
            "source_span_ids",
            tuple(sorted(self.source_span_ids)),
        )
        object.__setattr__(
            self,
            "rationale_codes",
            tuple(sorted(self.rationale_codes)),
        )

    @property
    def is_authoritative_origin(self) -> bool:
        return self.verification_origin in {
            ClaimVerificationOrigin.DETERMINISTIC_DECODER,
            ClaimVerificationOrigin.TRUSTED_REGISTERED_INGESTOR,
            ClaimVerificationOrigin.HUMAN_APPROVED,
        }

    def logical_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "claim_id": self.claim_id,
            "verdict": self.verdict.value,
            "verification_origin": self.verification_origin.value,
            "rationale_codes": sorted(self.rationale_codes),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            **self.logical_dict(),
            "verifier_fingerprint": self.verifier_fingerprint,
            "independence_group": self.independence_group,
            "source_span_ids": sorted(self.source_span_ids),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ClaimVerificationV1":
        return cls._decode_dict(
            payload,
            allow_authoritative_origin=False,
        )

    @classmethod
    def _decode_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        allow_authoritative_origin: bool,
    ) -> "ClaimVerificationV1":
        _schema(payload, CLAIM_VERIFICATION_SCHEMA_VERSION)
        verification = cls(
            verification_id=str(payload.get("verification_id") or ""),
            claim_id=str(payload.get("claim_id") or ""),
            verdict=ClaimVerificationVerdict(
                str(payload.get("verdict") or "")
            ),
            verification_origin=ClaimVerificationOrigin(
                str(payload.get("verification_origin") or "")
            ),
            verifier_fingerprint=str(
                payload.get("verifier_fingerprint") or ""
            ),
            independence_group=str(payload.get("independence_group") or ""),
            source_span_ids=_string_tuple(payload, "source_span_ids"),
            rationale_codes=_string_tuple(payload, "rationale_codes"),
        )
        if (
            verification.is_authoritative_origin
            and not allow_authoritative_origin
        ):
            raise EvidenceContractError(
                "verification_authority_untrusted",
                "untrusted evidence cannot self-declare an authoritative origin",
            )
        return verification

    @classmethod
    def from_agent_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ClaimVerificationV1":
        return cls.from_dict(payload)

@dataclass(frozen=True)
class SemanticSourceDispositionV1:
    source_unit_id: str
    disposition: SemanticSourceDispositionKind
    claim_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    auditor_verification_id: str
    schema_version: str = SEMANTIC_SOURCE_DISPOSITION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_SOURCE_DISPOSITION_SCHEMA_VERSION,
            "semantic source disposition",
        )
        _safe_id(self.source_unit_id, field_name="source_unit_id")
        object.__setattr__(
            self,
            "disposition",
            SemanticSourceDispositionKind(self.disposition),
        )
        _safe_ids(self.claim_ids, field_name="claim_ids")
        _reason_codes(self.reason_codes)
        object.__setattr__(
            self,
            "claim_ids",
            tuple(sorted(self.claim_ids)),
        )
        object.__setattr__(
            self,
            "reason_codes",
            tuple(sorted(self.reason_codes)),
        )
        _safe_id(
            self.auditor_verification_id,
            field_name="auditor_verification_id",
        )
        if (
            self.disposition is SemanticSourceDispositionKind.EVIDENCE
            and not self.claim_ids
        ):
            raise EvidenceContractError(
                "source_disposition_invalid",
                "evidence disposition requires claim references",
            )
        if (
            self.disposition is SemanticSourceDispositionKind.IRRELEVANT
            and not self.reason_codes
        ):
            raise EvidenceContractError(
                "source_disposition_invalid",
                "irrelevant disposition requires an auditable reason",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_unit_id": self.source_unit_id,
            "disposition": self.disposition.value,
            "claim_ids": sorted(self.claim_ids),
            "reason_codes": sorted(self.reason_codes),
            "auditor_verification_id": self.auditor_verification_id,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticSourceDispositionV1":
        _schema(payload, SEMANTIC_SOURCE_DISPOSITION_SCHEMA_VERSION)
        return cls(
            source_unit_id=str(payload.get("source_unit_id") or ""),
            disposition=SemanticSourceDispositionKind(
                str(payload.get("disposition") or "")
            ),
            claim_ids=_string_tuple(payload, "claim_ids"),
            reason_codes=_string_tuple(payload, "reason_codes"),
            auditor_verification_id=str(
                payload.get("auditor_verification_id") or ""
            ),
        )


@dataclass(frozen=True)
class EvidenceConflictV1:
    conflict_id: str
    kind: EvidenceConflictKind
    claim_ids: tuple[str, ...]
    comparison_unit: str
    status: EvidenceConflictStatus
    resolution_policy_ref: str | None = None
    schema_version: str = EVIDENCE_CONFLICT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            EVIDENCE_CONFLICT_SCHEMA_VERSION,
            "evidence conflict",
        )
        _safe_id(self.conflict_id, field_name="conflict_id")
        object.__setattr__(self, "kind", EvidenceConflictKind(self.kind))
        object.__setattr__(
            self,
            "status",
            EvidenceConflictStatus(self.status),
        )
        _safe_ids(self.claim_ids, field_name="claim_ids")
        object.__setattr__(
            self,
            "claim_ids",
            tuple(sorted(self.claim_ids)),
        )
        if len(self.claim_ids) < 2:
            raise EvidenceContractError(
                "conflict_incomplete",
                "evidence conflict requires at least two claims",
            )
        if (
            not isinstance(self.comparison_unit, str)
            or not self.comparison_unit.strip()
            or len(self.comparison_unit) > 256
        ):
            raise EvidenceContractError(
                "conflict_incomplete",
                "comparison_unit must be a bounded non-empty string",
            )
        if self.status is EvidenceConflictStatus.POLICY_RESOLVED:
            if self.resolution_policy_ref is None:
                raise EvidenceContractError(
                    "conflict_resolution_missing",
                    "policy-resolved conflict requires a policy reference",
                )
            _safe_id(
                self.resolution_policy_ref,
                field_name="resolution_policy_ref",
            )
        elif self.resolution_policy_ref is not None:
            _safe_id(
                self.resolution_policy_ref,
                field_name="resolution_policy_ref",
            )

    def logical_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "kind": self.kind.value,
            "claim_ids": sorted(self.claim_ids),
            "comparison_unit": self.comparison_unit,
            "status": self.status.value,
            "resolution_policy_ref": self.resolution_policy_ref,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, **self.logical_dict()}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceConflictV1":
        _schema(payload, EVIDENCE_CONFLICT_SCHEMA_VERSION)
        return cls(
            conflict_id=str(payload.get("conflict_id") or ""),
            kind=EvidenceConflictKind(str(payload.get("kind") or "")),
            claim_ids=_string_tuple(payload, "claim_ids"),
            comparison_unit=str(payload.get("comparison_unit") or ""),
            status=EvidenceConflictStatus(
                str(payload.get("status") or "")
            ),
            resolution_policy_ref=(
                str(payload["resolution_policy_ref"])
                if payload.get("resolution_policy_ref") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class SelfImprovementEvidenceGraphV1:
    spans: tuple[EvidenceSourceSpanV1, ...]
    entities: tuple[EvidenceEntityV1, ...]
    claims: tuple[EvidenceClaimV1, ...]
    claim_verifications: tuple[ClaimVerificationV1, ...]
    source_dispositions: tuple[SemanticSourceDispositionV1, ...]
    conflicts: tuple[EvidenceConflictV1, ...] = ()
    unresolved_references: tuple[str, ...] = ()
    profile_fingerprint: str = ""
    extractor_population_fingerprint: str = ""
    schema_version: str = SELF_IMPROVEMENT_EVIDENCE_GRAPH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SELF_IMPROVEMENT_EVIDENCE_GRAPH_SCHEMA_VERSION,
            "self-improvement evidence graph",
        )
        _fingerprint(
            self.profile_fingerprint,
            field_name="profile_fingerprint",
        )
        _fingerprint(
            self.extractor_population_fingerprint,
            field_name="extractor_population_fingerprint",
        )
        spans = _index_unique(self.spans, "span_id")
        entities = _index_unique(self.entities, "entity_id")
        claims = _index_unique(self.claims, "claim_id")
        verifications = _index_unique(
            self.claim_verifications,
            "verification_id",
        )
        _index_unique(self.source_dispositions, "source_unit_id")
        _index_unique(self.conflicts, "conflict_id")
        for entity in self.entities:
            _refs_exist(
                entity.source_span_ids,
                spans,
                "dangling_source_span_reference",
            )
        for verification in self.claim_verifications:
            if verification.claim_id not in claims:
                raise EvidenceContractError(
                    "dangling_evidence_reference",
                    "claim verification references an unknown claim",
                )
            _refs_exist(
                verification.source_span_ids,
                spans,
                "dangling_source_span_reference",
            )
        for claim in self.claims:
            _refs_exist(
                (*claim.subject_entity_ids, *claim.object_entity_ids),
                entities,
                "dangling_entity_reference",
            )
            _refs_exist(
                claim.source_span_ids,
                spans,
                "dangling_source_span_reference",
            )
            _refs_exist(
                claim.verification_ids,
                verifications,
                "dangling_verification_reference",
            )
            selected = tuple(
                verifications[item] for item in claim.verification_ids
            )
            if any(item.claim_id != claim.claim_id for item in selected):
                raise EvidenceContractError(
                    "verification_claim_mismatch",
                    "verification identity does not match its claim",
                )
            if claim.resolution_status is EvidenceResolutionStatus.RESOLVED:
                if not any(
                    item.verdict is ClaimVerificationVerdict.ENTAILED
                    for item in selected
                ):
                    raise EvidenceContractError(
                        "claim_not_entailed",
                        "resolved claim requires an entailed verification",
                    )
                if any(
                    item.verdict is ClaimVerificationVerdict.CONTRADICTED
                    for item in selected
                ):
                    raise EvidenceContractError(
                        "claim_contradicted",
                        "contradicted claim cannot be resolved",
                    )
            self._validate_claim_entity_kinds(claim, entities)
        for disposition in self.source_dispositions:
            _refs_exist(
                disposition.claim_ids,
                claims,
                "dangling_claim_reference",
            )
            # This identifier belongs to the source-coverage auditor's stage
            # report, not necessarily to a verification of an extracted claim.
            # Frozen snapshots validate the external report reference.
        for conflict in self.conflicts:
            _refs_exist(
                conflict.claim_ids,
                claims,
                "dangling_claim_reference",
            )
        _safe_ids(
            self.unresolved_references,
            field_name="unresolved_references",
        )
        object.__setattr__(
            self,
            "spans",
            tuple(sorted(self.spans, key=lambda item: item.span_id)),
        )
        object.__setattr__(
            self,
            "entities",
            tuple(
                sorted(self.entities, key=lambda item: item.entity_id)
            ),
        )
        object.__setattr__(
            self,
            "claims",
            tuple(sorted(self.claims, key=lambda item: item.claim_id)),
        )
        object.__setattr__(
            self,
            "claim_verifications",
            tuple(
                sorted(
                    self.claim_verifications,
                    key=lambda item: item.verification_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "source_dispositions",
            tuple(
                sorted(
                    self.source_dispositions,
                    key=lambda item: item.source_unit_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "conflicts",
            tuple(
                sorted(
                    self.conflicts,
                    key=lambda item: item.conflict_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "unresolved_references",
            tuple(sorted(self.unresolved_references)),
        )

    @staticmethod
    def _validate_claim_entity_kinds(
        claim: EvidenceClaimV1,
        entities: Mapping[str, EvidenceEntityV1],
    ) -> None:
        subjects = tuple(entities[item].kind for item in claim.subject_entity_ids)
        objects = tuple(entities[item].kind for item in claim.object_entity_ids)
        if claim.kind in {
            EvidenceClaimKind.EXECUTION_TRAJECTORY,
            EvidenceClaimKind.EXECUTION_RESULT,
            EvidenceClaimKind.METRIC_OBSERVATION,
        } and subjects[0] not in {
            EvidenceEntityKind.EXECUTION,
            EvidenceEntityKind.HARNESS,
        }:
            raise EvidenceContractError(
                "claim_entity_kind_mismatch",
                "execution evidence subject must be an execution or harness",
            )
        if claim.kind is EvidenceClaimKind.HUMAN_COMPARISON and not all(
            item in {EvidenceEntityKind.EXECUTION, EvidenceEntityKind.HARNESS}
            for item in objects
        ):
            raise EvidenceContractError(
                "claim_entity_kind_mismatch",
                "comparison objects must be executions or harnesses",
            )
        if claim.kind is EvidenceClaimKind.LLM_JUDGE_ASSESSMENT:
            if EvidenceEntityKind.REVIEWER not in subjects:
                raise EvidenceContractError(
                    "claim_entity_kind_mismatch",
                    "judge assessment requires a reviewer subject",
                )
            if not all(
                item
                in {EvidenceEntityKind.EXECUTION, EvidenceEntityKind.HARNESS}
                for item in objects
            ):
                raise EvidenceContractError(
                    "claim_entity_kind_mismatch",
                    "judge assessment objects must be executions or harnesses",
                )

    @property
    def logical_fingerprint(self) -> str:
        return _fingerprint_json(self.logical_projection())

    @property
    def provenance_fingerprint(self) -> str:
        return _fingerprint_json(self.provenance_projection())

    def logical_projection(self) -> dict[str, Any]:
        verification_by_id = {
            item.verification_id: item for item in self.claim_verifications
        }
        return {
            "schema_version": self.schema_version,
            "entities": [
                item.logical_dict()
                for item in sorted(self.entities, key=lambda value: value.entity_id)
            ],
            "claims": [
                {
                    **item.logical_dict(),
                    "verification_verdicts": sorted(
                        {
                            verification_by_id[verification_id].verdict.value
                            for verification_id in item.verification_ids
                        }
                    ),
                }
                for item in sorted(self.claims, key=lambda value: value.claim_id)
            ],
            "conflicts": [
                item.logical_dict()
                for item in sorted(
                    self.conflicts,
                    key=lambda value: value.conflict_id,
                )
            ],
            "unresolved_references": sorted(self.unresolved_references),
            "profile_fingerprint": self.profile_fingerprint,
        }

    def provenance_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "logical_fingerprint": self.logical_fingerprint,
            "spans": [
                item.to_dict()
                for item in sorted(self.spans, key=lambda value: value.span_id)
            ],
            "entity_source_spans": {
                item.entity_id: sorted(item.source_span_ids)
                for item in sorted(self.entities, key=lambda value: value.entity_id)
            },
            "claim_source_spans": {
                item.claim_id: sorted(item.source_span_ids)
                for item in sorted(self.claims, key=lambda value: value.claim_id)
            },
            "claim_producers": {
                item.claim_id: item.producer_kind.value
                for item in sorted(self.claims, key=lambda value: value.claim_id)
            },
            "claim_agent_confidence": {
                item.claim_id: item.agent_confidence
                for item in sorted(self.claims, key=lambda value: value.claim_id)
            },
            "claim_verifications": [
                item.to_dict()
                for item in sorted(
                    self.claim_verifications,
                    key=lambda value: value.verification_id,
                )
            ],
            "source_dispositions": [
                item.to_dict()
                for item in sorted(
                    self.source_dispositions,
                    key=lambda value: value.source_unit_id,
                )
            ],
            "extractor_population_fingerprint": (
                self.extractor_population_fingerprint
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "spans": [item.to_dict() for item in self.spans],
            "entities": [item.to_dict() for item in self.entities],
            "claims": [item.to_dict() for item in self.claims],
            "claim_verifications": [
                item.to_dict() for item in self.claim_verifications
            ],
            "source_dispositions": [
                item.to_dict() for item in self.source_dispositions
            ],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "unresolved_references": list(self.unresolved_references),
            "profile_fingerprint": self.profile_fingerprint,
            "extractor_population_fingerprint": (
                self.extractor_population_fingerprint
            ),
            "logical_fingerprint": self.logical_fingerprint,
            "provenance_fingerprint": self.provenance_fingerprint,
        }

    @classmethod
    def _decode_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        allow_authoritative_origins: bool,
    ) -> "SelfImprovementEvidenceGraphV1":
        _schema(payload, SELF_IMPROVEMENT_EVIDENCE_GRAPH_SCHEMA_VERSION)
        graph = cls(
            spans=tuple(
                EvidenceSourceSpanV1.from_dict(_as_mapping(item))
                for item in _sequence(payload.get("spans", ()), "spans")
            ),
            entities=tuple(
                EvidenceEntityV1.from_dict(_as_mapping(item))
                for item in _sequence(payload.get("entities", ()), "entities")
            ),
            claims=tuple(
                EvidenceClaimV1.from_dict(_as_mapping(item))
                for item in _sequence(payload.get("claims", ()), "claims")
            ),
            claim_verifications=tuple(
                ClaimVerificationV1._decode_dict(
                    _as_mapping(item),
                    allow_authoritative_origin=(
                        allow_authoritative_origins
                    ),
                )
                for item in _sequence(
                    payload.get("claim_verifications", ()),
                    "claim_verifications",
                )
            ),
            source_dispositions=tuple(
                SemanticSourceDispositionV1.from_dict(_as_mapping(item))
                for item in _sequence(
                    payload.get("source_dispositions", ()),
                    "source_dispositions",
                )
            ),
            conflicts=tuple(
                EvidenceConflictV1.from_dict(_as_mapping(item))
                for item in _sequence(payload.get("conflicts", ()), "conflicts")
            ),
            unresolved_references=_string_tuple(
                payload,
                "unresolved_references",
            ),
            profile_fingerprint=str(
                payload.get("profile_fingerprint") or ""
            ),
            extractor_population_fingerprint=str(
                payload.get("extractor_population_fingerprint") or ""
            ),
        )
        claimed_logical = payload.get("logical_fingerprint")
        if (
            claimed_logical is not None
            and claimed_logical != graph.logical_fingerprint
        ):
            raise EvidenceContractError(
                "fingerprint_mismatch",
                "evidence graph logical fingerprint mismatch",
            )
        claimed_provenance = payload.get("provenance_fingerprint")
        if (
            claimed_provenance is not None
            and claimed_provenance != graph.provenance_fingerprint
        ):
            raise EvidenceContractError(
                "fingerprint_mismatch",
                "evidence graph provenance fingerprint mismatch",
            )
        return graph

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SelfImprovementEvidenceGraphV1":
        return cls._decode_dict(
            payload,
            allow_authoritative_origins=False,
        )

    @classmethod
    def from_agent_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SelfImprovementEvidenceGraphV1":
        return cls.from_dict(payload)

    @classmethod
    def from_frozen_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        attested_provenance_fingerprint: str,
        authoritative_verification_ids: Sequence[str],
        verification_registry_fingerprint: str,
    ) -> "SelfImprovementEvidenceGraphV1":
        _fingerprint(
            attested_provenance_fingerprint,
            field_name="attested_provenance_fingerprint",
        )
        _fingerprint(
            verification_registry_fingerprint,
            field_name="verification_registry_fingerprint",
        )
        _safe_ids(
            authoritative_verification_ids,
            field_name="authoritative_verification_ids",
        )
        graph = cls._decode_dict(
            payload,
            allow_authoritative_origins=True,
        )
        if (
            graph.provenance_fingerprint
            != attested_provenance_fingerprint
        ):
            raise EvidenceContractError(
                "frozen_authority_attestation_mismatch",
                "frozen graph provenance does not match its attestation",
            )
        attested_ids = set(authoritative_verification_ids)
        actual_ids = {
            item.verification_id
            for item in graph.claim_verifications
            if item.is_authoritative_origin
        }
        if actual_ids != attested_ids:
            raise EvidenceContractError(
                "verification_authority_untrusted",
                "frozen authoritative verification IDs do not match attestation",
            )
        expected_registry_fingerprint = (
            authoritative_verification_registry_fingerprint(
                graph,
                authoritative_verification_ids,
            )
        )
        if (
            verification_registry_fingerprint
            != expected_registry_fingerprint
        ):
            raise EvidenceContractError(
                "verification_authority_untrusted",
                "frozen verification registry fingerprint is not graph-bound",
            )
        return graph


def authoritative_verification_registry_fingerprint(
    graph: SelfImprovementEvidenceGraphV1,
    verification_ids: Sequence[str],
) -> str:
    """Bind privileged verification IDs to exact graph provenance."""

    _safe_ids(
        verification_ids,
        field_name="authoritative_verification_ids",
    )
    verifications = {
        item.verification_id: item
        for item in graph.claim_verifications
    }
    rows: list[dict[str, Any]] = []
    for verification_id in sorted(verification_ids):
        verification = verifications.get(verification_id)
        if verification is None or not verification.is_authoritative_origin:
            raise EvidenceContractError(
                "verification_authority_untrusted",
                "authority registry references a non-authoritative verification",
            )
        rows.append(
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
                "aworld.self_evolve.verification_authority_registry.v1"
            ),
            "evidence_graph_provenance_fingerprint": (
                graph.provenance_fingerprint
            ),
            "verifications": rows,
        }
    )


@dataclass(frozen=True)
class SelfImprovementCaseV1:
    case_id: str
    task_entity_id: str
    input_claim_ids: tuple[str, ...]
    execution_entity_ids: tuple[str, ...]
    trajectory_claim_ids: tuple[str, ...]
    result_claim_ids: tuple[str, ...]
    comparison_claim_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    resolution_status: SelfImprovementCaseResolutionStatus
    trainable_signal_projection: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SELF_IMPROVEMENT_CASE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SELF_IMPROVEMENT_CASE_SCHEMA_VERSION,
            "self-improvement case",
        )
        _safe_id(self.case_id, field_name="case_id")
        _safe_id(self.task_entity_id, field_name="task_entity_id")
        for name in (
            "input_claim_ids",
            "execution_entity_ids",
            "trajectory_claim_ids",
            "result_claim_ids",
            "comparison_claim_ids",
            "conflict_ids",
        ):
            _safe_ids(getattr(self, name), field_name=name)
            object.__setattr__(
                self,
                name,
                tuple(sorted(getattr(self, name))),
            )
        object.__setattr__(
            self,
            "resolution_status",
            SelfImprovementCaseResolutionStatus(self.resolution_status),
        )
        if not self.execution_entity_ids:
            raise EvidenceContractError(
                "case_execution_missing",
                "self-improvement case requires an execution",
            )
        object.__setattr__(
            self,
            "trainable_signal_projection",
            _freeze_json_mapping(self.trainable_signal_projection),
        )

    @property
    def fingerprint(self) -> str:
        return _fingerprint_json(self.to_dict())

    def validate_against(self, graph: SelfImprovementEvidenceGraphV1) -> None:
        entity_by_id = {item.entity_id: item for item in graph.entities}
        claim_by_id = {item.claim_id: item for item in graph.claims}
        conflict_by_id = {item.conflict_id: item for item in graph.conflicts}
        if (
            self.task_entity_id not in entity_by_id
            or entity_by_id[self.task_entity_id].kind is not EvidenceEntityKind.TASK
        ):
            raise EvidenceContractError(
                "case_task_invalid",
                "case task must reference a task entity",
            )
        _refs_exist(
            self.execution_entity_ids,
            entity_by_id,
            "dangling_entity_reference",
        )
        if any(
            entity_by_id[item].kind is not EvidenceEntityKind.EXECUTION
            for item in self.execution_entity_ids
        ):
            raise EvidenceContractError(
                "case_execution_invalid",
                "case execution references must be execution entities",
            )
        claim_groups = (
            self.input_claim_ids,
            self.trajectory_claim_ids,
            self.result_claim_ids,
            self.comparison_claim_ids,
        )
        for group in claim_groups:
            _refs_exist(group, claim_by_id, "dangling_claim_reference")
        _refs_exist(
            self.conflict_ids,
            conflict_by_id,
            "dangling_conflict_reference",
        )
        case_claim_ids = {
            claim_id
            for group in claim_groups
            for claim_id in group
        }
        relevant_conflict_ids = {
            conflict.conflict_id
            for conflict in graph.conflicts
            if set(conflict.claim_ids) & case_claim_ids
        }
        if relevant_conflict_ids - set(self.conflict_ids):
            raise EvidenceContractError(
                "case_conflict_coverage_incomplete",
                "case omits a conflict involving its evidence claims",
            )
        if (
            self.resolution_status
            is SelfImprovementCaseResolutionStatus.RESOLVED
            and any(
                conflict_by_id[conflict_id].status
                is EvidenceConflictStatus.UNRESOLVED
                for conflict_id in self.conflict_ids
            )
        ):
            raise EvidenceContractError(
                "case_resolution_conflict",
                "a resolved case cannot retain unresolved conflicts",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "task_entity_id": self.task_entity_id,
            "input_claim_ids": list(self.input_claim_ids),
            "execution_entity_ids": list(self.execution_entity_ids),
            "trajectory_claim_ids": list(self.trajectory_claim_ids),
            "result_claim_ids": list(self.result_claim_ids),
            "comparison_claim_ids": list(self.comparison_claim_ids),
            "conflict_ids": list(self.conflict_ids),
            "resolution_status": self.resolution_status.value,
            "trainable_signal_projection": _json_value(
                self.trainable_signal_projection
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SelfImprovementCaseV1":
        _schema(payload, SELF_IMPROVEMENT_CASE_SCHEMA_VERSION)
        return cls(
            case_id=str(payload.get("case_id") or ""),
            task_entity_id=str(payload.get("task_entity_id") or ""),
            input_claim_ids=_string_tuple(payload, "input_claim_ids"),
            execution_entity_ids=_string_tuple(
                payload,
                "execution_entity_ids",
            ),
            trajectory_claim_ids=_string_tuple(
                payload,
                "trajectory_claim_ids",
            ),
            result_claim_ids=_string_tuple(payload, "result_claim_ids"),
            comparison_claim_ids=_string_tuple(
                payload,
                "comparison_claim_ids",
            ),
            conflict_ids=_string_tuple(payload, "conflict_ids"),
            resolution_status=SelfImprovementCaseResolutionStatus(
                str(payload.get("resolution_status") or "")
            ),
            trainable_signal_projection=_as_mapping(
                payload.get("trainable_signal_projection", {})
            ),
        )


def _validate_claim_payload(
    kind: EvidenceClaimKind,
    payload: Mapping[str, Any],
    *,
    object_entity_ids: Sequence[str],
) -> None:
    def required_string(name: str) -> str:
        value = payload.get(name)
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 4096
        ):
            raise EvidenceContractError(
                "claim_payload_invalid",
                f"{kind.value} claim requires a bounded {name}",
            )
        return value

    if kind is EvidenceClaimKind.TASK_INPUT:
        if "input" not in payload:
            raise EvidenceContractError(
                "claim_payload_invalid",
                "task_input claim requires input",
            )
    elif kind is EvidenceClaimKind.EXECUTION_TRAJECTORY:
        required_string("trace_ref")
        trace_fingerprint = payload.get("trace_fingerprint")
        if not isinstance(trace_fingerprint, str):
            raise EvidenceContractError(
                "claim_payload_invalid",
                "execution_trajectory claim requires trace_fingerprint",
            )
        _fingerprint(
            trace_fingerprint,
            field_name="trace_fingerprint",
        )
    elif kind is EvidenceClaimKind.EXECUTION_RESULT:
        if "result" not in payload:
            raise EvidenceContractError(
                "claim_payload_invalid",
                "execution_result claim requires result",
            )
    elif kind is EvidenceClaimKind.METRIC_OBSERVATION:
        required_string("metric_name")
        required_string("scope")
        value = payload.get("value")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise EvidenceContractError(
                "claim_payload_invalid",
                "metric_observation claim requires a finite value",
            )
    elif kind is EvidenceClaimKind.HUMAN_COMPARISON:
        relation = required_string("relation")
        required_string("scope")
        if relation not in {"preferred_over", "equivalent", "ranked"}:
            raise EvidenceContractError(
                "claim_payload_invalid",
                "human comparison relation is unsupported",
            )
        if relation == "preferred_over":
            preferred = required_string("preferred_entity_id")
            if preferred not in object_entity_ids:
                raise EvidenceContractError(
                    "claim_payload_invalid",
                    "preferred entity must be one of the compared entities",
                )
        elif relation == "ranked":
            ranking = payload.get("ranking")
            if (
                not isinstance(ranking, (tuple, list))
                or any(not isinstance(item, str) for item in ranking)
                or len(ranking) != len(set(ranking))
                or set(ranking) != set(object_entity_ids)
            ):
                raise EvidenceContractError(
                    "claim_payload_invalid",
                    "ranked comparison requires an explicit complete ranking",
                )
    elif kind is EvidenceClaimKind.LLM_JUDGE_ASSESSMENT:
        required_string("rubric_id")
        required_string("scope")
        if "score" not in payload and "preferred_entity_id" not in payload:
            raise EvidenceContractError(
                "claim_payload_invalid",
                "judge assessment requires a score or preference direction",
            )
        if "score" in payload:
            score = payload["score"]
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                raise EvidenceContractError(
                    "claim_payload_invalid",
                    "judge score must be finite",
                )
        if (
            "preferred_entity_id" in payload
            and payload["preferred_entity_id"] not in object_entity_ids
        ):
            raise EvidenceContractError(
                "claim_payload_invalid",
                "judge preferred entity must be assessed by the claim",
            )
    elif kind is EvidenceClaimKind.EXPLICIT_RELATION:
        required_string("relation")


def _freeze_json_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise EvidenceContractError(
            "schema_invalid",
            "expected a JSON object",
        )
    _canonical_bytes(frozen)
    return frozen


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze_json(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _require_schema(actual: str, expected: str, name: str) -> None:
    if actual != expected:
        raise EvidenceContractError(
            "schema_version_mismatch",
            f"invalid {name} schema",
        )


def _schema(payload: Mapping[str, Any], expected: str) -> None:
    _require_schema(
        str(payload.get("schema_version") or ""),
        expected,
        "payload",
    )


def _safe_id(value: str, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_ID_PATTERN.fullmatch(value)
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise EvidenceContractError(
            "unsafe_identity",
            f"{field_name} is not a safe stable identity",
        )
    return value


def _safe_ids(values: Sequence[str], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise EvidenceContractError(
            "duplicate_identity",
            f"{field_name} contains duplicates",
        )
    for value in values:
        _safe_id(value, field_name=field_name)


def _fingerprint(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise EvidenceContractError(
            "invalid_fingerprint",
            f"{field_name} must be sha256:<64 lowercase hex>",
        )
    return value


def _reason_codes(values: Sequence[str]) -> None:
    if len(values) != len(set(values)):
        raise EvidenceContractError(
            "duplicate_identity",
            "reason_codes contain duplicates",
        )
    for value in values:
        if not _REASON_PATTERN.fullmatch(value):
            raise EvidenceContractError(
                "invalid_reason_code",
                "reason_codes must be lower_snake_case",
            )


def _unique_strings(
    values: Sequence[str],
    *,
    field_name: str,
    max_length: int,
) -> None:
    if len(values) != len(set(values)):
        raise EvidenceContractError(
            "duplicate_identity",
            f"{field_name} contains duplicates",
        )
    if any(
        not isinstance(item, str)
        or not item.strip()
        or len(item) > max_length
        for item in values
    ):
        raise EvidenceContractError(
            "schema_invalid",
            f"{field_name} contains an invalid string",
        )


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            _json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceContractError(
            "schema_invalid",
            "value is not canonical JSON",
        ) from exc


def _fingerprint_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceContractError("schema_invalid", "expected an object")
    return value


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise EvidenceContractError(
            "schema_invalid",
            f"{field_name} must be an array",
        )
    return value


def _string_tuple(payload: Mapping[str, Any], name: str) -> tuple[str, ...]:
    return tuple(
        str(item)
        for item in _sequence(payload.get(name, ()), name)
    )


def _index_unique(
    values: Sequence[Any],
    identity_field: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        identity = getattr(value, identity_field)
        if identity in result:
            raise EvidenceContractError(
                "duplicate_identity",
                f"duplicate {identity_field}: {identity}",
            )
        result[identity] = value
    return result


def _refs_exist(
    references: Sequence[str],
    index: Mapping[str, Any],
    reason_code: str,
) -> None:
    missing = [item for item in references if item not in index]
    if missing:
        raise EvidenceContractError(
            reason_code,
            f"unknown evidence reference: {missing[0]}",
        )
