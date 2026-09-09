from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Sequence

from aworld.self_evolve.evaluation_plan import (
    QualificationStatus,
    SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1,
    SEMANTIC_RECORDED_OUTCOME_RUNNER_PROTOCOL_FINGERPRINT_V1,
    SemanticModelQualificationReportV1,
    SemanticQualificationMethod,
    SemanticQualificationRegistryV1,
)
from aworld.self_evolve.evidence import (
    ClaimVerificationVerdict,
    EvidenceClaimKind,
    EvidenceConflictKind,
    EvidenceEntityKind,
    EvidenceResolutionStatus,
    SemanticSourceDispositionKind,
)
from aworld.self_evolve.improvement_signals import SignalActionability
from aworld.self_evolve.improvement_signals import (
    SelfImprovementSignalKind,
)


SEMANTIC_QUALIFICATION_CASE_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_qualification_case.v1"
)
SEMANTIC_QUALIFICATION_CORPUS_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_qualification_corpus.v1"
)
SEMANTIC_QUALIFICATION_CASE_OUTCOME_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_qualification_case_outcome.v1"
)
SEMANTIC_QUALIFICATION_OUTCOME_SET_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_qualification_outcome_set.v1"
)
SEMANTIC_QUALIFICATION_THRESHOLD_SET_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_qualification_thresholds.v1"
)
SEMANTIC_QUALIFICATION_EXPECTATIONS_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_qualification_expectations.v1"
)
SEMANTIC_QUALIFICATION_CASE_ATTESTATION_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_qualification_case_attestation.v1"
)
HUMAN_ANNOTATION_PROTOCOL_V1 = "human_labeled_dual_review.v1"

SOURCE_UNIT_DISPOSITION_ACCURACY = (
    "source_unit_disposition_accuracy"
)
ACCEPTED_CLAIM_PRECISION = "accepted_claim_precision"
REQUIRED_CLAIM_RECALL = "required_claim_recall"
CITATION_SPAN_EXACT_MATCH = "citation_span_exact_match"
ENTAILMENT_ACCURACY = "entailment_accuracy"
ENTITY_LINK_ACCURACY = "entity_link_accuracy"
CONFLICT_DETECTION_RECALL = "conflict_detection_recall"
CONFLICT_DETECTION_PRECISION = "conflict_detection_precision"
SIGNAL_ACTIONABILITY_ACCURACY = "signal_actionability_accuracy"

QUALIFICATION_METRIC_KEYS = frozenset(
    {
        SOURCE_UNIT_DISPOSITION_ACCURACY,
        ACCEPTED_CLAIM_PRECISION,
        REQUIRED_CLAIM_RECALL,
        CITATION_SPAN_EXACT_MATCH,
        ENTAILMENT_ACCURACY,
        ENTITY_LINK_ACCURACY,
        CONFLICT_DETECTION_RECALL,
        CONFLICT_DETECTION_PRECISION,
        SIGNAL_ACTIONABILITY_ACCURACY,
    }
)

_FRAMEWORK_THRESHOLDS_V1 = MappingProxyType(
    {
        SOURCE_UNIT_DISPOSITION_ACCURACY: 1.0,
        ACCEPTED_CLAIM_PRECISION: 0.98,
        REQUIRED_CLAIM_RECALL: 1.0,
        CITATION_SPAN_EXACT_MATCH: 1.0,
        ENTAILMENT_ACCURACY: 0.98,
        ENTITY_LINK_ACCURACY: 0.98,
        CONFLICT_DETECTION_RECALL: 1.0,
        CONFLICT_DETECTION_PRECISION: 1.0,
        SIGNAL_ACTIONABILITY_ACCURACY: 0.95,
    }
)
_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_CORPUS_BYTES = 4 * 1024 * 1024
_MAX_TRUST_ARTIFACT_BYTES = 1024 * 1024
_MAX_CASES = 10_000
_MAX_LABELS_PER_DIMENSION = 100_000

# These are versioned framework policy identities, not values supplied by a
# run's model response or source document.
FRAMEWORK_SEMANTIC_QUALIFICATION_CORPUS_FINGERPRINT_V1 = (
    "sha256:e338670ebaa14f210de56ffdacad1f8188eda13a04a356f1641078371188001f"
)
FRAMEWORK_SEMANTIC_QUALIFICATION_THRESHOLD_SET_FINGERPRINT_V1 = (
    "sha256:d5e5c115b89c8860e987324b7e1c729d697fb077f77f25878d325b6f6530ca49"
)


class SemanticQualificationContractError(ValueError):
    """A stable failure in the offline semantic qualification protocol."""

    def __init__(self, reason_code: str, message: str) -> None:
        if not _REASON_PATTERN.fullmatch(reason_code):
            raise ValueError("reason_code must be lower_snake_case")
        self.reason_code = reason_code
        super().__init__(message)


class QualificationAnnotationOrigin(str, Enum):
    HUMAN_LABELED = "human_labeled"


@dataclass(frozen=True)
class SemanticQualificationSourceInputV1:
    """Source-only qualification input; gold labels are never exposed."""

    run_token: str
    source_documents: Mapping[str, str]

    def __post_init__(self) -> None:
        _safe_id(self.run_token, field_name="run_token")
        object.__setattr__(
            self,
            "source_documents",
            MappingProxyType(
                _source_documents(self.source_documents)
            ),
        )

    @property
    def source_fingerprint(self) -> str:
        return _fingerprint_json(
            {
                "schema_version": (
                    "aworld.self_evolve.semantic_qualification_source.v1"
                ),
                "run_token": self.run_token,
                "source_documents": dict(self.source_documents),
            }
        )


@dataclass(frozen=True)
class SemanticQualificationDeploymentRunnerV1:
    """An explicitly identified deployment adapter for qualification."""

    model_profile_fingerprint: str
    provider_fingerprint: str
    semantic_protocol_fingerprint: str
    constitution_fingerprint: str
    outcome_runner: Callable[
        [SemanticQualificationSourceInputV1],
        SemanticQualificationCaseOutcomeV1
        | Awaitable[SemanticQualificationCaseOutcomeV1],
    ] = field(compare=False, repr=False)

    def __post_init__(self) -> None:
        for field_name in (
            "model_profile_fingerprint",
            "provider_fingerprint",
            "semantic_protocol_fingerprint",
            "constitution_fingerprint",
        ):
            _fingerprint(getattr(self, field_name), field_name=field_name)
        if not callable(self.outcome_runner):
            raise SemanticQualificationContractError(
                "qualification_runner_invalid",
                "qualification deployment runner must be callable",
            )


@dataclass(frozen=True)
class SemanticQualificationSnapshotDeploymentRunnerV1:
    """Run the exact semantic-ingestion deployment for qualification."""

    model_profile_fingerprint: str
    provider_fingerprint: str
    semantic_protocol_fingerprint: str
    constitution_fingerprint: str
    snapshot_runner: Callable[
        [SemanticQualificationSourceInputV1],
        Any | Awaitable[Any],
    ] = field(compare=False, repr=False)

    def __post_init__(self) -> None:
        for field_name in (
            "model_profile_fingerprint",
            "provider_fingerprint",
            "semantic_protocol_fingerprint",
            "constitution_fingerprint",
        ):
            _fingerprint(getattr(self, field_name), field_name=field_name)
        if not callable(self.snapshot_runner):
            raise SemanticQualificationContractError(
                "qualification_runner_invalid",
                "qualification snapshot runner must be callable",
            )


@dataclass(frozen=True)
class SemanticQualificationExpectationsV1:
    """Human-authored semantic signatures used by the exact snapshot scorer.

    Labels are local corpus handles only. Production scoring resolves them to
    content-derived source, entity, claim, conflict, case, and signal
    signatures before comparing a frozen snapshot.
    """

    source_units: Mapping[str, Mapping[str, Any]]
    spans: Mapping[str, Mapping[str, Any]]
    entities: Mapping[str, Mapping[str, Any]]
    claims: Mapping[str, Mapping[str, Any]]
    conflicts: Mapping[str, Mapping[str, Any]]
    cases: Mapping[str, Mapping[str, Any]]
    signals: Mapping[str, Mapping[str, Any]]
    schema_version: str = (
        SEMANTIC_QUALIFICATION_EXPECTATIONS_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_QUALIFICATION_EXPECTATIONS_SCHEMA_VERSION,
            "semantic qualification expectations",
        )
        normalized = _normalize_semantic_expectations(self.to_dict())
        for name in (
            "source_units",
            "spans",
            "entities",
            "claims",
            "conflicts",
            "cases",
            "signals",
        ):
            object.__setattr__(
                self,
                name,
                _deep_freeze_mapping(normalized[name]),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_units": _plain_json(self.source_units),
            "spans": _plain_json(self.spans),
            "entities": _plain_json(self.entities),
            "claims": _plain_json(self.claims),
            "conflicts": _plain_json(self.conflicts),
            "cases": _plain_json(self.cases),
            "signals": _plain_json(self.signals),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticQualificationExpectationsV1":
        normalized = _normalize_semantic_expectations(payload)
        return cls(
            source_units=normalized["source_units"],
            spans=normalized["spans"],
            entities=normalized["entities"],
            claims=normalized["claims"],
            conflicts=normalized["conflicts"],
            cases=normalized["cases"],
            signals=normalized["signals"],
        )


@dataclass(frozen=True)
class SemanticQualificationCaseV1:
    """Human-reviewed labels for one generic semantic-ingestion scenario."""

    case_id: str
    scenario_tags: tuple[str, ...]
    source_documents: Mapping[str, str]
    source_unit_dispositions: Mapping[
        str, SemanticSourceDispositionKind
    ]
    citation_spans: Mapping[str, tuple[str, ...]]
    entailment_verdicts: Mapping[str, ClaimVerificationVerdict]
    entity_links: Mapping[str, str]
    conflict_ids: tuple[str, ...]
    signal_actionability: Mapping[str, SignalActionability]
    authority_eligible_claim_ids: tuple[str, ...] = ()
    semantic_expectations: (
        SemanticQualificationExpectationsV1 | None
    ) = None
    annotation_origin: QualificationAnnotationOrigin = (
        QualificationAnnotationOrigin.HUMAN_LABELED
    )
    schema_version: str = SEMANTIC_QUALIFICATION_CASE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_QUALIFICATION_CASE_SCHEMA_VERSION,
            "semantic qualification case",
        )
        _safe_id(self.case_id, field_name="case_id")
        object.__setattr__(
            self,
            "annotation_origin",
            QualificationAnnotationOrigin(self.annotation_origin),
        )
        if (
            self.annotation_origin
            is not QualificationAnnotationOrigin.HUMAN_LABELED
        ):
            raise SemanticQualificationContractError(
                "annotation_origin_invalid",
                "qualification gold labels must be human-labeled",
            )

        tags = _normalized_ids(
            self.scenario_tags,
            field_name="scenario_tags",
            allow_empty=False,
        )
        source_documents = _source_documents(self.source_documents)
        dispositions = _enum_mapping(
            self.source_unit_dispositions,
            SemanticSourceDispositionKind,
            field_name="source_unit_dispositions",
            allow_empty=False,
        )
        citations = _tuple_mapping(
            self.citation_spans,
            field_name="citation_spans",
            allow_empty=False,
            allow_empty_values=False,
        )
        entailments = _enum_mapping(
            self.entailment_verdicts,
            ClaimVerificationVerdict,
            field_name="entailment_verdicts",
            allow_empty=False,
        )
        links = _id_mapping(
            self.entity_links,
            field_name="entity_links",
            allow_empty=False,
        )
        conflicts = _normalized_ids(
            self.conflict_ids,
            field_name="conflict_ids",
            allow_empty=True,
        )
        actionability = _enum_mapping(
            self.signal_actionability,
            SignalActionability,
            field_name="signal_actionability",
            allow_empty=False,
        )
        eligible_claims = _normalized_ids(
            self.authority_eligible_claim_ids,
            field_name="authority_eligible_claim_ids",
            allow_empty=True,
        )
        if set(citations) != set(entailments):
            raise SemanticQualificationContractError(
                "gold_labels_inconsistent",
                "citation and entailment labels must cover the same claims",
            )
        unknown_eligible = set(eligible_claims).difference(entailments)
        if unknown_eligible:
            raise SemanticQualificationContractError(
                "gold_labels_inconsistent",
                "authority-eligible claims must have citation and "
                "entailment labels",
            )
        if self.semantic_expectations is not None:
            if not isinstance(
                self.semantic_expectations,
                SemanticQualificationExpectationsV1,
            ):
                raise SemanticQualificationContractError(
                    "semantic_expectations_invalid",
                    "semantic expectations must use the typed v1 contract",
                )
            _validate_expectations_against_case(
                self.semantic_expectations,
                source_documents=source_documents,
                source_unit_dispositions=dispositions,
                citation_spans=citations,
                entailment_verdicts=entailments,
                entity_links=links,
                conflict_ids=conflicts,
                signal_actionability=actionability,
            )

        object.__setattr__(self, "scenario_tags", tags)
        object.__setattr__(
            self,
            "source_documents",
            MappingProxyType(source_documents),
        )
        object.__setattr__(
            self,
            "source_unit_dispositions",
            MappingProxyType(dispositions),
        )
        object.__setattr__(
            self,
            "citation_spans",
            MappingProxyType(citations),
        )
        object.__setattr__(
            self,
            "entailment_verdicts",
            MappingProxyType(entailments),
        )
        object.__setattr__(
            self,
            "entity_links",
            MappingProxyType(links),
        )
        object.__setattr__(self, "conflict_ids", conflicts)
        object.__setattr__(
            self,
            "signal_actionability",
            MappingProxyType(actionability),
        )
        object.__setattr__(
            self,
            "authority_eligible_claim_ids",
            eligible_claims,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "scenario_tags": list(self.scenario_tags),
            "annotation_origin": self.annotation_origin.value,
            "source_documents": dict(
                sorted(self.source_documents.items())
            ),
            "source_unit_dispositions": {
                key: value.value
                for key, value in sorted(
                    self.source_unit_dispositions.items()
                )
            },
            "citation_spans": {
                key: list(value)
                for key, value in sorted(self.citation_spans.items())
            },
            "entailment_verdicts": {
                key: value.value
                for key, value in sorted(
                    self.entailment_verdicts.items()
                )
            },
            "entity_links": dict(sorted(self.entity_links.items())),
            "conflict_ids": list(self.conflict_ids),
            "signal_actionability": {
                key: value.value
                for key, value in sorted(
                    self.signal_actionability.items()
                )
            },
            "authority_eligible_claim_ids": list(
                self.authority_eligible_claim_ids
            ),
            "semantic_expectations": (
                self.semantic_expectations.to_dict()
                if self.semantic_expectations is not None
                else None
            ),
        }

    def source_input(
        self,
        *,
        run_token: str,
    ) -> SemanticQualificationSourceInputV1:
        return SemanticQualificationSourceInputV1(
            run_token=run_token,
            source_documents=self.source_documents,
        )

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticQualificationCaseV1":
        _require_exact_keys(
            payload,
            {
                "schema_version",
                "case_id",
                "scenario_tags",
                "annotation_origin",
                "source_documents",
                "source_unit_dispositions",
                "citation_spans",
                "entailment_verdicts",
                "entity_links",
                "conflict_ids",
                "signal_actionability",
                "authority_eligible_claim_ids",
                "semantic_expectations",
            },
            label="semantic qualification case",
        )
        _require_schema(
            str(payload.get("schema_version") or ""),
            SEMANTIC_QUALIFICATION_CASE_SCHEMA_VERSION,
            "semantic qualification case",
        )
        return cls(
            case_id=_required_string(payload, "case_id"),
            scenario_tags=_string_tuple(payload, "scenario_tags"),
            annotation_origin=QualificationAnnotationOrigin(
                _required_string(payload, "annotation_origin")
            ),
            source_documents={
                str(key): str(value)
                for key, value in _mapping(
                    payload,
                    "source_documents",
                ).items()
            },
            source_unit_dispositions={
                str(key): SemanticSourceDispositionKind(str(value))
                for key, value in _mapping(
                    payload,
                    "source_unit_dispositions",
                ).items()
            },
            citation_spans={
                str(key): _strings(value, "citation_spans")
                for key, value in _mapping(
                    payload,
                    "citation_spans",
                ).items()
            },
            entailment_verdicts={
                str(key): ClaimVerificationVerdict(str(value))
                for key, value in _mapping(
                    payload,
                    "entailment_verdicts",
                ).items()
            },
            entity_links={
                key: value
                for key, value in _mapping(
                    payload,
                    "entity_links",
                ).items()
            },
            conflict_ids=_string_tuple(payload, "conflict_ids"),
            signal_actionability={
                str(key): SignalActionability(str(value))
                for key, value in _mapping(
                    payload,
                    "signal_actionability",
                ).items()
            },
            authority_eligible_claim_ids=_string_tuple(
                payload,
                "authority_eligible_claim_ids",
            ),
            semantic_expectations=(
                SemanticQualificationExpectationsV1.from_dict(
                    _as_mapping(
                        payload.get("semantic_expectations"),
                        "semantic_expectations",
                    )
                )
                if payload.get("semantic_expectations") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class SemanticQualificationCorpusV1:
    cases: tuple[SemanticQualificationCaseV1, ...]
    annotation_protocol: str = HUMAN_ANNOTATION_PROTOCOL_V1
    schema_version: str = SEMANTIC_QUALIFICATION_CORPUS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_QUALIFICATION_CORPUS_SCHEMA_VERSION,
            "semantic qualification corpus",
        )
        if self.annotation_protocol != HUMAN_ANNOTATION_PROTOCOL_V1:
            raise SemanticQualificationContractError(
                "annotation_protocol_unsupported",
                "qualification corpus must use the fixed v1 human "
                "annotation protocol",
            )
        if not self.cases or len(self.cases) > _MAX_CASES:
            raise SemanticQualificationContractError(
                "corpus_size_invalid",
                "qualification corpus must contain a bounded non-empty "
                "case set",
            )
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise SemanticQualificationContractError(
                "duplicate_case_id",
                "qualification case ids must be unique",
            )
        ordered = tuple(sorted(self.cases, key=lambda item: item.case_id))
        object.__setattr__(self, "cases", ordered)

    @property
    def corpus_fingerprint(self) -> str:
        return _fingerprint_json(self.canonical_dict())

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "annotation_protocol": self.annotation_protocol,
            "cases": [case.to_dict() for case in self.cases],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.canonical_dict(),
            "corpus_fingerprint": self.corpus_fingerprint,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticQualificationCorpusV1":
        _require_exact_keys(
            payload,
            {
                "schema_version",
                "annotation_protocol",
                "cases",
                "corpus_fingerprint",
            },
            label="semantic qualification corpus",
        )
        _require_schema(
            str(payload.get("schema_version") or ""),
            SEMANTIC_QUALIFICATION_CORPUS_SCHEMA_VERSION,
            "semantic qualification corpus",
        )
        corpus = cls(
            cases=tuple(
                SemanticQualificationCaseV1.from_dict(
                    _as_mapping(item, "cases")
                )
                for item in _sequence(payload.get("cases"), "cases")
            ),
            annotation_protocol=_required_string(
                payload,
                "annotation_protocol",
            ),
        )
        claimed = _required_string(payload, "corpus_fingerprint")
        _fingerprint(claimed, field_name="corpus_fingerprint")
        if claimed != corpus.corpus_fingerprint:
            raise SemanticQualificationContractError(
                "corpus_fingerprint_mismatch",
                "semantic qualification corpus fingerprint mismatch",
            )
        return corpus


@dataclass(frozen=True)
class SemanticQualificationCaseOutcomeV1:
    """A deployment's structured result for one corpus case."""

    case_id: str
    source_unit_dispositions: Mapping[
        str, SemanticSourceDispositionKind
    ]
    citation_spans: Mapping[str, tuple[str, ...]]
    entailment_verdicts: Mapping[str, ClaimVerificationVerdict]
    entity_links: Mapping[str, str]
    detected_conflict_ids: tuple[str, ...]
    signal_actionability: Mapping[str, SignalActionability]
    elevated_authority_claim_ids: tuple[str, ...] = ()
    unexpected_accepted_claim_count: int = 0
    schema_version: str = (
        SEMANTIC_QUALIFICATION_CASE_OUTCOME_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_QUALIFICATION_CASE_OUTCOME_SCHEMA_VERSION,
            "semantic qualification case outcome",
        )
        _safe_id(self.case_id, field_name="case_id")
        object.__setattr__(
            self,
            "source_unit_dispositions",
            MappingProxyType(
                _enum_mapping(
                    self.source_unit_dispositions,
                    SemanticSourceDispositionKind,
                    field_name="source_unit_dispositions",
                    allow_empty=False,
                )
            ),
        )
        object.__setattr__(
            self,
            "citation_spans",
            MappingProxyType(
                _tuple_mapping(
                    self.citation_spans,
                    field_name="citation_spans",
                    allow_empty=False,
                    allow_empty_values=True,
                )
            ),
        )
        object.__setattr__(
            self,
            "entailment_verdicts",
            MappingProxyType(
                _enum_mapping(
                    self.entailment_verdicts,
                    ClaimVerificationVerdict,
                    field_name="entailment_verdicts",
                    allow_empty=False,
                )
            ),
        )
        object.__setattr__(
            self,
            "entity_links",
            MappingProxyType(
                _id_mapping(
                    self.entity_links,
                    field_name="entity_links",
                    allow_empty=False,
                )
            ),
        )
        object.__setattr__(
            self,
            "detected_conflict_ids",
            _normalized_ids(
                self.detected_conflict_ids,
                field_name="detected_conflict_ids",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "signal_actionability",
            MappingProxyType(
                _enum_mapping(
                    self.signal_actionability,
                    SignalActionability,
                    field_name="signal_actionability",
                    allow_empty=False,
                )
            ),
        )
        object.__setattr__(
            self,
            "elevated_authority_claim_ids",
            _normalized_ids(
                self.elevated_authority_claim_ids,
                field_name="elevated_authority_claim_ids",
                allow_empty=True,
            ),
        )
        if (
            isinstance(self.unexpected_accepted_claim_count, bool)
            or not isinstance(
                self.unexpected_accepted_claim_count,
                int,
            )
            or self.unexpected_accepted_claim_count < 0
        ):
            raise SemanticQualificationContractError(
                "qualification_count_invalid",
                "unexpected accepted claim count must be non-negative",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "source_unit_dispositions": {
                key: value.value
                for key, value in sorted(
                    self.source_unit_dispositions.items()
                )
            },
            "citation_spans": {
                key: list(value)
                for key, value in sorted(self.citation_spans.items())
            },
            "entailment_verdicts": {
                key: value.value
                for key, value in sorted(
                    self.entailment_verdicts.items()
                )
            },
            "entity_links": dict(sorted(self.entity_links.items())),
            "detected_conflict_ids": list(
                self.detected_conflict_ids
            ),
            "signal_actionability": {
                key: value.value
                for key, value in sorted(
                    self.signal_actionability.items()
                )
            },
            "elevated_authority_claim_ids": list(
                self.elevated_authority_claim_ids
            ),
            "unexpected_accepted_claim_count": (
                self.unexpected_accepted_claim_count
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticQualificationCaseOutcomeV1":
        _require_exact_keys(
            payload,
            {
                "schema_version",
                "case_id",
                "source_unit_dispositions",
                "citation_spans",
                "entailment_verdicts",
                "entity_links",
                "detected_conflict_ids",
                "signal_actionability",
                "elevated_authority_claim_ids",
                "unexpected_accepted_claim_count",
            },
            label="semantic qualification case outcome",
        )
        _require_schema(
            str(payload.get("schema_version") or ""),
            SEMANTIC_QUALIFICATION_CASE_OUTCOME_SCHEMA_VERSION,
            "semantic qualification case outcome",
        )
        return cls(
            case_id=_required_string(payload, "case_id"),
            source_unit_dispositions={
                str(key): SemanticSourceDispositionKind(str(value))
                for key, value in _mapping(
                    payload,
                    "source_unit_dispositions",
                ).items()
            },
            citation_spans={
                str(key): _strings(value, "citation_spans")
                for key, value in _mapping(
                    payload,
                    "citation_spans",
                ).items()
            },
            entailment_verdicts={
                str(key): ClaimVerificationVerdict(str(value))
                for key, value in _mapping(
                    payload,
                    "entailment_verdicts",
                ).items()
            },
            entity_links={
                key: value
                for key, value in _mapping(
                    payload,
                    "entity_links",
                ).items()
            },
            detected_conflict_ids=_string_tuple(
                payload,
                "detected_conflict_ids",
            ),
            signal_actionability={
                str(key): SignalActionability(str(value))
                for key, value in _mapping(
                    payload,
                    "signal_actionability",
                ).items()
            },
            elevated_authority_claim_ids=_string_tuple(
                payload,
                "elevated_authority_claim_ids",
            ),
            unexpected_accepted_claim_count=payload.get(
                "unexpected_accepted_claim_count"
            ),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class SemanticQualificationOutcomeSetV1:
    outcomes: tuple[SemanticQualificationCaseOutcomeV1, ...]
    schema_version: str = (
        SEMANTIC_QUALIFICATION_OUTCOME_SET_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_QUALIFICATION_OUTCOME_SET_SCHEMA_VERSION,
            "semantic qualification outcome set",
        )
        if not self.outcomes or len(self.outcomes) > _MAX_CASES:
            raise SemanticQualificationContractError(
                "outcome_set_size_invalid",
                "qualification outcomes must contain a bounded non-empty "
                "case set",
            )
        case_ids = [outcome.case_id for outcome in self.outcomes]
        if len(case_ids) != len(set(case_ids)):
            raise SemanticQualificationContractError(
                "duplicate_case_id",
                "qualification outcome case ids must be unique",
            )
        object.__setattr__(
            self,
            "outcomes",
            tuple(
                sorted(
                    self.outcomes,
                    key=lambda item: item.case_id,
                )
            ),
        )

    @property
    def outcome_fingerprint(self) -> str:
        return _fingerprint_json(self.canonical_dict())

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "outcomes": [
                outcome.to_dict() for outcome in self.outcomes
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.canonical_dict(),
            "outcome_fingerprint": self.outcome_fingerprint,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticQualificationOutcomeSetV1":
        _require_exact_keys(
            payload,
            {
                "schema_version",
                "outcomes",
                "outcome_fingerprint",
            },
            label="semantic qualification outcome set",
        )
        _require_schema(
            str(payload.get("schema_version") or ""),
            SEMANTIC_QUALIFICATION_OUTCOME_SET_SCHEMA_VERSION,
            "semantic qualification outcome set",
        )
        result = cls(
            outcomes=tuple(
                SemanticQualificationCaseOutcomeV1.from_dict(
                    _as_mapping(item, "outcomes")
                )
                for item in _sequence(
                    payload.get("outcomes"),
                    "outcomes",
                )
            )
        )
        claimed = _required_string(payload, "outcome_fingerprint")
        _fingerprint(claimed, field_name="outcome_fingerprint")
        if claimed != result.outcome_fingerprint:
            raise SemanticQualificationContractError(
                "outcome_fingerprint_mismatch",
                "semantic qualification outcome fingerprint mismatch",
            )
        return result


@dataclass(frozen=True)
class SemanticQualificationThresholdSetV1:
    metric_thresholds: Mapping[str, float] = field(
        default_factory=lambda: dict(_FRAMEWORK_THRESHOLDS_V1)
    )
    schema_version: str = (
        SEMANTIC_QUALIFICATION_THRESHOLD_SET_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SEMANTIC_QUALIFICATION_THRESHOLD_SET_SCHEMA_VERSION,
            "semantic qualification threshold set",
        )
        _require_metric_keys(self.metric_thresholds)
        normalized: dict[str, float] = {}
        for key, raw_value in self.metric_thresholds.items():
            value = _score(raw_value, field_name="metric_thresholds")
            normalized[str(key)] = value
        if normalized != dict(_FRAMEWORK_THRESHOLDS_V1):
            raise SemanticQualificationContractError(
                "qualification_threshold_drift",
                "v1 qualification thresholds are framework-fixed",
            )
        object.__setattr__(
            self,
            "metric_thresholds",
            MappingProxyType(dict(sorted(normalized.items()))),
        )

    @property
    def threshold_set_fingerprint(self) -> str:
        return _fingerprint_json(self.to_dict())

    @property
    def is_framework_v1(self) -> bool:
        return dict(self.metric_thresholds) == dict(
            _FRAMEWORK_THRESHOLDS_V1
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "metric_thresholds": {
                key: float(value)
                for key, value in sorted(
                    self.metric_thresholds.items()
                )
            },
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticQualificationThresholdSetV1":
        _require_exact_keys(
            payload,
            {"schema_version", "metric_thresholds"},
            label="semantic qualification threshold set",
        )
        _require_schema(
            str(payload.get("schema_version") or ""),
            SEMANTIC_QUALIFICATION_THRESHOLD_SET_SCHEMA_VERSION,
            "semantic qualification threshold set",
        )
        return cls(
            metric_thresholds={
                str(key): _score(
                    value,
                    field_name="metric_thresholds",
                )
                for key, value in _mapping(
                    payload,
                    "metric_thresholds",
                ).items()
            }
        )


def framework_semantic_qualification_thresholds_v1(
) -> SemanticQualificationThresholdSetV1:
    """Return the immutable framework-owned threshold policy for v1."""

    return SemanticQualificationThresholdSetV1()


def load_semantic_qualification_corpus(
    path: str | Path,
) -> SemanticQualificationCorpusV1:
    """Load a bounded local corpus without retaining provider configuration."""

    payload_bytes = Path(path).read_bytes()
    if len(payload_bytes) > _MAX_CORPUS_BYTES:
        raise SemanticQualificationContractError(
            "corpus_size_invalid",
            "semantic qualification corpus exceeds the byte limit",
        )
    try:
        payload = json.loads(
            payload_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SemanticQualificationContractError(
            "corpus_json_invalid",
            "semantic qualification corpus must be valid UTF-8 JSON",
        ) from exc
    return SemanticQualificationCorpusV1.from_dict(
        _as_mapping(payload, "corpus")
    )


def load_semantic_model_qualification_report(
    path: str | Path,
) -> SemanticModelQualificationReportV1:
    """Load an operator-selected report as data, never as its own trust root."""

    payload = _load_bounded_trust_json(path, label="qualification report")
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "model_profile_fingerprint",
            "provider_fingerprint",
            "semantic_protocol_fingerprint",
            "constitution_fingerprint",
            "corpus_fingerprint",
            "threshold_set_fingerprint",
            "metric_values",
            "required_thresholds",
            "false_authority_elevation_count",
            "status",
            "issued_at_utc",
            "expires_at_utc",
            "qualification_method",
            "runner_protocol_fingerprint",
            "case_attestation_bundle_fingerprint",
            "report_fingerprint",
        },
        label="semantic qualification report",
    )
    try:
        report = SemanticModelQualificationReportV1.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise SemanticQualificationContractError(
            "qualification_report_invalid",
            "semantic qualification report is invalid",
        ) from exc
    _require_metric_keys(report.metric_values)
    _require_metric_keys(report.required_thresholds)
    if (
        report.corpus_fingerprint
        != FRAMEWORK_SEMANTIC_QUALIFICATION_CORPUS_FINGERPRINT_V1
        or report.threshold_set_fingerprint
        != FRAMEWORK_SEMANTIC_QUALIFICATION_THRESHOLD_SET_FINGERPRINT_V1
    ):
        raise SemanticQualificationContractError(
            "qualification_policy_mismatch",
            "semantic qualification report is not bound to framework v1 policy",
        )
    if (
        dict(report.required_thresholds)
        != dict(framework_semantic_qualification_thresholds_v1().metric_thresholds)
    ):
        raise SemanticQualificationContractError(
            "qualification_threshold_drift",
            "semantic qualification report changed framework thresholds",
        )
    return report


def load_semantic_qualification_registry(
    path: str | Path,
) -> SemanticQualificationRegistryV1:
    """Load the workspace-owned report allowlist."""

    payload = _load_bounded_trust_json(path, label="qualification registry")
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "trusted_report_fingerprints",
        },
        label="semantic qualification registry",
    )
    try:
        return SemanticQualificationRegistryV1.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise SemanticQualificationContractError(
            "qualification_registry_invalid",
            "semantic qualification registry is invalid",
        ) from exc


async def run_semantic_model_qualification(
    corpus: SemanticQualificationCorpusV1,
    deployment: SemanticQualificationDeploymentRunnerV1,
    *,
    issued_at_utc: str | None = None,
    expires_at_utc: str | None = None,
) -> SemanticModelQualificationReportV1:
    """Run a deployment adapter over source-only cases, then score outputs."""

    outcomes: list[SemanticQualificationCaseOutcomeV1] = []
    for case in corpus.cases:
        run_token = "qualification-" + secrets.token_hex(16)
        produced = deployment.outcome_runner(
            case.source_input(run_token=run_token)
        )
        if inspect.isawaitable(produced):
            produced = await produced
        if not isinstance(produced, SemanticQualificationCaseOutcomeV1):
            raise SemanticQualificationContractError(
                "qualification_outcome_invalid",
                "qualification runner must return a typed case outcome",
            )
        if produced.case_id != run_token:
            raise SemanticQualificationContractError(
                "qualification_case_mismatch",
                "qualification runner returned an outcome for another "
                "opaque run token",
            )
        outcomes.append(
            SemanticQualificationCaseOutcomeV1(
                case_id=case.case_id,
                source_unit_dispositions=(
                    produced.source_unit_dispositions
                ),
                citation_spans=produced.citation_spans,
                entailment_verdicts=produced.entailment_verdicts,
                entity_links=produced.entity_links,
                detected_conflict_ids=(
                    produced.detected_conflict_ids
                ),
                signal_actionability=(
                    produced.signal_actionability
                ),
                elevated_authority_claim_ids=(
                    produced.elevated_authority_claim_ids
                ),
                unexpected_accepted_claim_count=(
                    produced.unexpected_accepted_claim_count
                ),
            )
        )
    return evaluate_semantic_model_qualification(
        corpus,
        SemanticQualificationOutcomeSetV1(outcomes=tuple(outcomes)),
        model_profile_fingerprint=(
            deployment.model_profile_fingerprint
        ),
        provider_fingerprint=deployment.provider_fingerprint,
        semantic_protocol_fingerprint=(
            deployment.semantic_protocol_fingerprint
        ),
        constitution_fingerprint=(
            deployment.constitution_fingerprint
        ),
        issued_at_utc=issued_at_utc,
        expires_at_utc=expires_at_utc,
    )


async def run_semantic_snapshot_model_qualification(
    corpus: SemanticQualificationCorpusV1,
    deployment: SemanticQualificationSnapshotDeploymentRunnerV1,
    *,
    issued_at_utc: str | None = None,
    expires_at_utc: str | None = None,
) -> SemanticModelQualificationReportV1:
    """Qualify an exact semantic-ingestion deployment from frozen snapshots.

    The deployment sees only an opaque run token and source documents.
    Framework code validates the returned snapshot's source/deployment
    bindings and derives every scored outcome, including authority elevation.
    """

    outcomes: list[SemanticQualificationCaseOutcomeV1] = []
    case_attestations: list[dict[str, Any]] = []
    for case in corpus.cases:
        run_token = "qualification-" + secrets.token_hex(16)
        source_input = case.source_input(run_token=run_token)
        try:
            snapshot = deployment.snapshot_runner(source_input)
            if inspect.isawaitable(snapshot):
                snapshot = await snapshot
        except Exception:
            outcomes.append(_failed_qualification_outcome(case))
            case_attestations.append(
                _qualification_case_attestation(
                    case,
                    snapshot=None,
                    status="execution_failed",
                )
            )
            continue
        _validate_qualification_snapshot(
            snapshot,
            source_input=source_input,
            deployment=deployment,
        )
        outcomes.append(
            _qualification_outcome_from_snapshot(case, snapshot)
        )
        case_attestations.append(
            _qualification_case_attestation(
                case,
                snapshot=snapshot,
                status="validated",
            )
        )
    attestation_bundle_fingerprint = _fingerprint_json(
        {
            "schema_version": (
                SEMANTIC_QUALIFICATION_CASE_ATTESTATION_SCHEMA_VERSION
            ),
            "runner_protocol_fingerprint": (
                SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1
            ),
            "cases": sorted(
                case_attestations,
                key=lambda item: item["case_id"],
            ),
        }
    )
    return evaluate_semantic_model_qualification(
        corpus,
        SemanticQualificationOutcomeSetV1(outcomes=tuple(outcomes)),
        model_profile_fingerprint=(
            deployment.model_profile_fingerprint
        ),
        provider_fingerprint=deployment.provider_fingerprint,
        semantic_protocol_fingerprint=(
            deployment.semantic_protocol_fingerprint
        ),
        constitution_fingerprint=(
            deployment.constitution_fingerprint
        ),
        issued_at_utc=issued_at_utc,
        expires_at_utc=expires_at_utc,
        qualification_method=(
            SemanticQualificationMethod.EXACT_SNAPSHOT_V1
        ),
        runner_protocol_fingerprint=(
            SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1
        ),
        case_attestation_bundle_fingerprint=(
            attestation_bundle_fingerprint
        ),
    )


def _failed_qualification_outcome(
    gold: SemanticQualificationCaseV1,
) -> SemanticQualificationCaseOutcomeV1:
    return SemanticQualificationCaseOutcomeV1(
        case_id=gold.case_id,
        source_unit_dispositions={
            key: _different_disposition(value)
            for key, value in gold.source_unit_dispositions.items()
        },
        citation_spans={
            key: ("missing-span",)
            for key in gold.citation_spans
        },
        entailment_verdicts={
            key: ClaimVerificationVerdict.INSUFFICIENT
            for key in gold.entailment_verdicts
        },
        entity_links={
            key: "missing-entity" for key in gold.entity_links
        },
        detected_conflict_ids=(),
        signal_actionability={
            key: _different_actionability(value)
            for key, value in gold.signal_actionability.items()
        },
        elevated_authority_claim_ids=(),
        unexpected_accepted_claim_count=0,
    )


def evaluate_semantic_model_qualification(
    corpus: SemanticQualificationCorpusV1,
    outcomes: SemanticQualificationOutcomeSetV1,
    *,
    model_profile_fingerprint: str,
    provider_fingerprint: str,
    semantic_protocol_fingerprint: str,
    constitution_fingerprint: str,
    issued_at_utc: str | None = None,
    expires_at_utc: str | None = None,
    qualification_method: SemanticQualificationMethod = (
        SemanticQualificationMethod.RECORDED_OUTCOMES_V1
    ),
    runner_protocol_fingerprint: str = (
        SEMANTIC_RECORDED_OUTCOME_RUNNER_PROTOCOL_FINGERPRINT_V1
    ),
    case_attestation_bundle_fingerprint: str | None = None,
) -> SemanticModelQualificationReportV1:
    """Issue a report bound only to explicit active deployment fingerprints."""

    for field_name, value in (
        ("model_profile_fingerprint", model_profile_fingerprint),
        ("provider_fingerprint", provider_fingerprint),
        (
            "semantic_protocol_fingerprint",
            semantic_protocol_fingerprint,
        ),
        ("constitution_fingerprint", constitution_fingerprint),
    ):
        _fingerprint(value, field_name=field_name)

    gold_by_id = {case.case_id: case for case in corpus.cases}
    outcome_by_id = {
        outcome.case_id: outcome for outcome in outcomes.outcomes
    }
    if set(gold_by_id) != set(outcome_by_id):
        raise SemanticQualificationContractError(
            "outcomes_incomplete",
            "qualification outcomes must contain exactly one result for "
            "every corpus case",
        )

    disposition_correct = 0
    disposition_total = 0
    required_claim_detected = 0
    required_claim_total = 0
    accepted_claim_total = 0
    citation_correct = 0
    citation_total = 0
    entailment_correct = 0
    entailment_total = 0
    entity_link_correct = 0
    entity_link_total = 0
    conflict_detected = 0
    conflict_total = 0
    conflict_prediction_total = 0
    actionability_correct = 0
    actionability_total = 0
    false_authority_elevation_count = 0

    for case_id in sorted(gold_by_id):
        gold = gold_by_id[case_id]
        outcome = outcome_by_id[case_id]
        _validate_outcome_completeness(gold, outcome)

        disposition_correct += _mapping_matches(
            gold.source_unit_dispositions,
            outcome.source_unit_dispositions,
        )
        disposition_total += len(gold.source_unit_dispositions)
        citation_correct += _mapping_matches(
            gold.citation_spans,
            outcome.citation_spans,
        )
        citation_total += len(gold.citation_spans)
        detected_claims = sum(
            bool(spans) and tuple(spans) != ("missing-span",)
            for spans in outcome.citation_spans.values()
        )
        required_claim_detected += detected_claims
        required_claim_total += len(gold.citation_spans)
        accepted_claim_total += (
            detected_claims
            + outcome.unexpected_accepted_claim_count
        )
        entailment_correct += _mapping_matches(
            gold.entailment_verdicts,
            outcome.entailment_verdicts,
        )
        entailment_total += len(gold.entailment_verdicts)
        entity_link_correct += _mapping_matches(
            gold.entity_links,
            outcome.entity_links,
        )
        entity_link_total += len(gold.entity_links)
        expected_conflicts = set(gold.conflict_ids)
        conflict_detected += len(
            expected_conflicts.intersection(
                outcome.detected_conflict_ids
            )
        )
        conflict_total += len(expected_conflicts)
        conflict_prediction_total += len(
            outcome.detected_conflict_ids
        )
        actionability_correct += _mapping_matches(
            gold.signal_actionability,
            outcome.signal_actionability,
        )
        actionability_total += len(gold.signal_actionability)
        false_authority_elevation_count += len(
            set(outcome.elevated_authority_claim_ids).difference(
                gold.authority_eligible_claim_ids
            )
        )

    metric_values = {
        SOURCE_UNIT_DISPOSITION_ACCURACY: _ratio(
            disposition_correct,
            disposition_total,
        ),
        ACCEPTED_CLAIM_PRECISION: _precision(
            required_claim_detected,
            accepted_claim_total,
        ),
        REQUIRED_CLAIM_RECALL: _ratio(
            required_claim_detected,
            required_claim_total,
        ),
        CITATION_SPAN_EXACT_MATCH: _ratio(
            citation_correct,
            citation_total,
        ),
        ENTAILMENT_ACCURACY: _ratio(
            entailment_correct,
            entailment_total,
        ),
        ENTITY_LINK_ACCURACY: _ratio(
            entity_link_correct,
            entity_link_total,
        ),
        CONFLICT_DETECTION_RECALL: (
            _ratio(conflict_detected, conflict_total)
            if conflict_total
            else 1.0
        ),
        CONFLICT_DETECTION_PRECISION: _precision(
            conflict_detected,
            conflict_prediction_total,
        ),
        SIGNAL_ACTIONABILITY_ACCURACY: _ratio(
            actionability_correct,
            actionability_total,
        ),
    }
    thresholds = framework_semantic_qualification_thresholds_v1()
    metrics_pass = all(
        metric_values[key] >= threshold
        for key, threshold in thresholds.metric_thresholds.items()
    )
    status = (
        QualificationStatus.QUALIFIED
        if metrics_pass and false_authority_elevation_count == 0
        else QualificationStatus.FAILED
    )
    issued_at = issued_at_utc or (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    if expires_at_utc is None:
        issued_datetime = datetime.fromisoformat(
            issued_at.replace("Z", "+00:00")
        )
        expires_at = (
            issued_datetime + timedelta(days=30)
        ).isoformat().replace("+00:00", "Z")
    else:
        expires_at = expires_at_utc
    report = SemanticModelQualificationReportV1(
        model_profile_fingerprint=model_profile_fingerprint,
        provider_fingerprint=provider_fingerprint,
        semantic_protocol_fingerprint=semantic_protocol_fingerprint,
        constitution_fingerprint=constitution_fingerprint,
        corpus_fingerprint=corpus.corpus_fingerprint,
        threshold_set_fingerprint=(
            thresholds.threshold_set_fingerprint
        ),
        metric_values=metric_values,
        required_thresholds=thresholds.metric_thresholds,
        false_authority_elevation_count=(
            false_authority_elevation_count
        ),
        status=status,
        issued_at_utc=issued_at,
        expires_at_utc=expires_at,
        qualification_method=qualification_method,
        runner_protocol_fingerprint=runner_protocol_fingerprint,
        case_attestation_bundle_fingerprint=(
            case_attestation_bundle_fingerprint
        ),
    )
    validate_semantic_model_qualification_report(
        report,
        corpus=corpus,
    )
    return report


def validate_semantic_model_qualification_report(
    report: SemanticModelQualificationReportV1,
    *,
    corpus: SemanticQualificationCorpusV1,
) -> None:
    """Reject reports that drift from the corpus or fixed metric policy."""

    thresholds = framework_semantic_qualification_thresholds_v1()
    _require_metric_keys(report.metric_values)
    _require_metric_keys(report.required_thresholds)
    for value in report.metric_values.values():
        _score(value, field_name="metric_values")
    for value in report.required_thresholds.values():
        _score(value, field_name="required_thresholds")
    if report.corpus_fingerprint != corpus.corpus_fingerprint:
        raise SemanticQualificationContractError(
            "corpus_report_fingerprint_mismatch",
            "qualification report is bound to a different corpus",
        )
    if (
        report.threshold_set_fingerprint
        != thresholds.threshold_set_fingerprint
        or dict(report.required_thresholds)
        != dict(thresholds.metric_thresholds)
    ):
        raise SemanticQualificationContractError(
            "qualification_threshold_drift",
            "qualification report does not use the fixed v1 thresholds",
        )
    should_qualify = (
        all(
            report.metric_values[key] >= threshold
            for key, threshold in thresholds.metric_thresholds.items()
        )
        and report.false_authority_elevation_count == 0
    )
    if (
        report.status is QualificationStatus.QUALIFIED
        and not should_qualify
    ):
        raise SemanticQualificationContractError(
            "qualification_status_invalid",
            "qualified status requires every threshold and zero false "
            "authority elevation",
        )


def _validate_qualification_snapshot(
    snapshot: Any,
    *,
    source_input: SemanticQualificationSourceInputV1,
    deployment: SemanticQualificationSnapshotDeploymentRunnerV1,
) -> None:
    from aworld.self_evolve.ingestion.semantic_snapshot import (
        FrozenSemanticIngestionSnapshotV2,
    )
    from aworld.self_evolve.ingestion.types import (
        IngestorTrustLevel,
        fingerprint_bytes,
    )
    from aworld.self_evolve.ingestion.semantic_verifier import (
        SemanticExtractionOrigin,
    )

    if not isinstance(snapshot, FrozenSemanticIngestionSnapshotV2):
        raise SemanticQualificationContractError(
            "qualification_snapshot_invalid",
            "qualification runner must return a frozen semantic snapshot",
        )
    if (
        snapshot.ingestor_trust_level
        is not IngestorTrustLevel.FRAMEWORK_BUILTIN
        or snapshot.resolution_evidence.extraction_origin
        is not SemanticExtractionOrigin.SEMANTIC_AGENT_POPULATION
        or snapshot.ingestion_model_call_count <= 0
    ):
        raise SemanticQualificationContractError(
            "qualification_snapshot_invalid",
            "qualification requires the framework semantic-agent pipeline",
        )
    expected_assets = {
        path: fingerprint_bytes(content.encode("utf-8"))
        for path, content in source_input.source_documents.items()
    }
    actual_assets = {
        asset.relative_path: asset.content_fingerprint
        for asset in snapshot.inventory.assets
    }
    if actual_assets != expected_assets:
        raise SemanticQualificationContractError(
            "qualification_source_mismatch",
            "qualification snapshot is not bound to the source-only input",
        )
    if (
        snapshot.semantic_model_profile_fingerprint
        != deployment.model_profile_fingerprint
        or snapshot.semantic_provider_fingerprint
        != deployment.provider_fingerprint
        or snapshot.semantic_protocol_fingerprint
        != deployment.semantic_protocol_fingerprint
        or snapshot.constitution.fingerprint
        != deployment.constitution_fingerprint
    ):
        raise SemanticQualificationContractError(
            "qualification_deployment_mismatch",
            "qualification snapshot is bound to another deployment",
        )
    if (
        snapshot.evidence_authority_context.human_approval is not None
        or snapshot.evidence_authority_context
        .deterministic_verification_ids
        or snapshot.evidence_authority_context
        .trusted_registered_verification_ids
        or snapshot.qualification_registry.trusted_report_fingerprints
    ):
        raise SemanticQualificationContractError(
            "qualification_snapshot_pretrusted",
            "qualification snapshots cannot inherit authority or an existing "
            "qualification allowlist",
        )


def _qualification_outcome_from_snapshot(
    gold: SemanticQualificationCaseV1,
    snapshot: Any,
) -> SemanticQualificationCaseOutcomeV1:
    expectations = gold.semantic_expectations
    if expectations is None:
        return _failed_qualification_outcome(gold)

    graph = snapshot.evidence_graph
    dispositions_by_id = {
        item.source_unit_id: item.disposition
        for item in graph.source_dispositions
    }
    actual_source_units: dict[str, list[Any]] = defaultdict(list)
    for item in snapshot.source_bundle.chunks:
        actual_source_units[
            _fingerprint_json(
                {
                    "relative_path": item.relative_path,
                    "unit_kind": "chunk",
                    "byte_start": item.byte_start,
                    "byte_end": item.byte_end,
                    "record_locator": None,
                    "field_path": None,
                }
            )
        ].append(item)
    for item in snapshot.source_bundle.structured_units:
        actual_source_units[
            _fingerprint_json(
                {
                    "relative_path": item.relative_path,
                    "unit_kind": "structured",
                    "byte_start": None,
                    "byte_end": None,
                    "record_locator": item.record_locator,
                    "field_path": item.field_path,
                }
            )
        ].append(item)
    source_unit_dispositions: dict[
        str, SemanticSourceDispositionKind
    ] = {}
    for label, expectation in expectations.source_units.items():
        locator_fingerprint = _fingerprint_json(
            {
                key: _plain_json(expectation[key])
                for key in (
                    "relative_path",
                    "unit_kind",
                    "byte_start",
                    "byte_end",
                    "record_locator",
                    "field_path",
                )
            }
        )
        candidates = actual_source_units.get(locator_fingerprint, [])
        actual = candidates.pop(0) if candidates else None
        actual_disposition = (
            dispositions_by_id.get(actual.source_unit_id)
            if actual is not None
            else None
        )
        source_unit_dispositions[label] = (
            actual_disposition
            if actual_disposition is not None
            else _different_disposition(
                gold.source_unit_dispositions[label]
            )
        )

    asset_paths = {
        item.asset_id: item.relative_path
        for item in snapshot.inventory.assets
    }
    actual_span_signatures = {
        item.span_id: _fingerprint_json(
            {
                "relative_path": asset_paths.get(
                    item.asset_id,
                    "<unknown-asset>",
                ),
                "byte_start": item.byte_start,
                "byte_end": item.byte_end,
                "content_fingerprint": item.content_fingerprint,
            }
        )
        for item in graph.spans
    }
    expected_span_signatures = {
        label: _fingerprint_json(_plain_json(expectation))
        for label, expectation in expectations.spans.items()
    }

    actual_entity_signatures = {
        item.entity_id: _fingerprint_json(
            _entity_semantic_descriptor(
                kind=item.kind.value,
                canonical_name=item.canonical_name,
                source_span_signatures=tuple(
                    actual_span_signatures.get(
                        span_id,
                        f"missing:{span_id}",
                    )
                    for span_id in item.source_span_ids
                ),
            )
        )
        for item in graph.entities
    }
    expected_entity_signatures = {
        label: _fingerprint_json(
            _entity_semantic_descriptor(
                kind=str(expectation["kind"]),
                canonical_name=str(expectation["canonical_name"]),
                source_span_signatures=tuple(
                    expected_span_signatures[span_label]
                    for span_label in expectation["source_spans"]
                ),
            )
        )
        for label, expectation in expectations.entities.items()
    }

    actual_claim_core_signatures = {
        item.claim_id: _fingerprint_json(
            _claim_semantic_descriptor(
                kind=item.kind.value,
                subject_entity_signatures=tuple(
                    actual_entity_signatures.get(
                        entity_id,
                        f"missing:{entity_id}",
                    )
                    for entity_id in item.subject_entity_ids
                ),
                object_entity_signatures=tuple(
                    actual_entity_signatures.get(
                        entity_id,
                        f"missing:{entity_id}",
                    )
                    for entity_id in item.object_entity_ids
                ),
                payload=_normalize_semantic_payload(
                    item.payload,
                    entity_signatures=actual_entity_signatures,
                ),
                resolution_status=item.resolution_status.value,
            )
        )
        for item in graph.claims
    }
    expected_claim_core_signatures = {
        label: _fingerprint_json(
            _claim_semantic_descriptor(
                kind=str(expectation["kind"]),
                subject_entity_signatures=tuple(
                    expected_entity_signatures[entity_label]
                    for entity_label in expectation["subject_entities"]
                ),
                object_entity_signatures=tuple(
                    expected_entity_signatures[entity_label]
                    for entity_label in expectation["object_entities"]
                ),
                payload=_normalize_semantic_payload(
                    expectation["payload"],
                    entity_signatures=expected_entity_signatures,
                    gold_entity_references=True,
                ),
                resolution_status=str(
                    expectation["resolution_status"]
                ),
            )
        )
        for label, expectation in expectations.claims.items()
    }
    claims_by_signature: dict[str, list[Any]] = defaultdict(list)
    for claim in graph.claims:
        if claim.resolution_status is not EvidenceResolutionStatus.REJECTED:
            claims_by_signature[
                actual_claim_core_signatures[claim.claim_id]
            ].append(claim)
    selected_claims: dict[str, Any | None] = {}
    for label in sorted(expectations.claims):
        candidates = claims_by_signature.get(
            expected_claim_core_signatures[label],
            [],
        )
        selected_claims[label] = (
            candidates.pop(0) if candidates else None
        )

    verifications = {
        item.verification_id: item
        for item in graph.claim_verifications
    }
    citation_spans: dict[str, tuple[str, ...]] = {}
    entailment_verdicts: dict[
        str, ClaimVerificationVerdict
    ] = {}
    for label in sorted(gold.citation_spans):
        claim = selected_claims[label]
        verification = (
            _selected_claim_verdict(claim, verifications)
            if claim is not None
            else None
        )
        if (
            claim is not None
            and verification is not None
        ):
            actual_citations = tuple(
                sorted(
                    actual_span_signatures.get(
                        span_id,
                        f"missing:{span_id}",
                    )
                    for span_id in claim.source_span_ids
                )
            )
            expected_citations = tuple(
                sorted(
                    expected_span_signatures[span_label]
                    for span_label in expectations.claims[label][
                        "source_spans"
                    ]
                )
            )
            citation_spans[label] = (
                gold.citation_spans[label]
                if actual_citations == expected_citations
                else ("citation-mismatch",)
            )
            entailment_verdicts[label] = verification
        else:
            citation_spans[label] = ("missing-span",)
            entailment_verdicts[label] = (
                ClaimVerificationVerdict.INSUFFICIENT
            )

    actual_entity_signature_set = set(actual_entity_signatures.values())
    entity_links: dict[str, str] = {}
    for label in sorted(gold.entity_links):
        expected_target = gold.entity_links[label]
        entity_links[label] = (
            expected_target
            if expected_entity_signatures[expected_target]
            in actual_entity_signature_set
            else "missing-entity"
        )

    actual_conflict_signatures: list[str] = []
    for conflict in graph.conflicts:
        actual_conflict_signatures.append(
            _fingerprint_json(
                _conflict_semantic_descriptor(
                    kind=conflict.kind.value,
                    claim_signatures=tuple(
                        actual_claim_core_signatures.get(
                            claim_id,
                            f"missing:{claim_id}",
                        )
                        for claim_id in conflict.claim_ids
                    ),
                    comparison_unit=actual_entity_signatures.get(
                        conflict.comparison_unit,
                        _normalize_text(conflict.comparison_unit),
                    ),
                    status=conflict.status.value,
                    resolution_policy_ref=(
                        conflict.resolution_policy_ref
                    ),
                )
            )
        )
    expected_conflict_signatures = {
        label: _fingerprint_json(
            _conflict_semantic_descriptor(
                kind=str(expectation["kind"]),
                claim_signatures=tuple(
                    expected_claim_core_signatures[claim_label]
                    for claim_label in expectation["claims"]
                ),
                comparison_unit=(
                    expected_entity_signatures[
                        str(expectation["comparison_unit_entity"])
                    ]
                    if expectation["comparison_unit_entity"] is not None
                    else _normalize_text(
                        str(expectation["comparison_unit_literal"])
                    )
                ),
                status=str(expectation["status"]),
                resolution_policy_ref=(
                    str(expectation["resolution_policy_ref"])
                    if expectation["resolution_policy_ref"] is not None
                    else None
                ),
            )
        )
        for label, expectation in expectations.conflicts.items()
    }
    expected_conflicts_by_signature: dict[str, list[str]] = defaultdict(
        list
    )
    for label, signature in expected_conflict_signatures.items():
        expected_conflicts_by_signature[signature].append(label)
    detected_conflicts: list[str] = []
    for index, signature in enumerate(
        sorted(actual_conflict_signatures)
    ):
        candidates = expected_conflicts_by_signature.get(signature, [])
        if candidates:
            detected_conflicts.append(candidates.pop(0))
        else:
            detected_conflicts.append(f"unexpected-conflict-{index}")

    actual_case_signatures = {
        item.case_id: _fingerprint_json(
            _case_semantic_descriptor(
                task_entity_signature=actual_entity_signatures.get(
                    item.task_entity_id,
                    f"missing:{item.task_entity_id}",
                ),
                input_claim_signatures=tuple(
                    actual_claim_core_signatures.get(
                        claim_id,
                        f"missing:{claim_id}",
                    )
                    for claim_id in item.input_claim_ids
                ),
                execution_entity_signatures=tuple(
                    actual_entity_signatures.get(
                        entity_id,
                        f"missing:{entity_id}",
                    )
                    for entity_id in item.execution_entity_ids
                ),
                trajectory_claim_signatures=tuple(
                    actual_claim_core_signatures.get(
                        claim_id,
                        f"missing:{claim_id}",
                    )
                    for claim_id in item.trajectory_claim_ids
                ),
                result_claim_signatures=tuple(
                    actual_claim_core_signatures.get(
                        claim_id,
                        f"missing:{claim_id}",
                    )
                    for claim_id in item.result_claim_ids
                ),
                comparison_claim_signatures=tuple(
                    actual_claim_core_signatures.get(
                        claim_id,
                        f"missing:{claim_id}",
                    )
                    for claim_id in item.comparison_claim_ids
                ),
                conflict_signatures=tuple(
                    _actual_conflict_signature_by_id(
                        conflict_id,
                        graph=graph,
                        claim_signatures=actual_claim_core_signatures,
                        entity_signatures=actual_entity_signatures,
                    )
                    for conflict_id in item.conflict_ids
                ),
                resolution_status=item.resolution_status.value,
            )
        )
        for item in snapshot.semantic_cases
    }
    expected_case_signatures = {
        label: _fingerprint_json(
            _case_semantic_descriptor(
                task_entity_signature=expected_entity_signatures[
                    str(expectation["task_entity"])
                ],
                input_claim_signatures=tuple(
                    expected_claim_core_signatures[item]
                    for item in expectation["input_claims"]
                ),
                execution_entity_signatures=tuple(
                    expected_entity_signatures[item]
                    for item in expectation["executions"]
                ),
                trajectory_claim_signatures=tuple(
                    expected_claim_core_signatures[item]
                    for item in expectation["trajectory_claims"]
                ),
                result_claim_signatures=tuple(
                    expected_claim_core_signatures[item]
                    for item in expectation["result_claims"]
                ),
                comparison_claim_signatures=tuple(
                    expected_claim_core_signatures[item]
                    for item in expectation["comparison_claims"]
                ),
                conflict_signatures=tuple(
                    expected_conflict_signatures[item]
                    for item in expectation["conflicts"]
                ),
                resolution_status=str(
                    expectation["resolution_status"]
                ),
            )
        )
        for label, expectation in expectations.cases.items()
    }
    actual_signals_by_signature: dict[str, list[Any]] = defaultdict(list)
    for signal in snapshot.improvement_signal_set.signals:
        signature = _fingerprint_json(
            _signal_semantic_descriptor(
                case_signature=actual_case_signatures.get(
                    signal.case_id,
                    f"missing:{signal.case_id}",
                ),
                kind=signal.kind.value,
                compared_execution_signatures=tuple(
                    actual_entity_signatures.get(
                        entity_id,
                        f"missing:{entity_id}",
                    )
                    for entity_id in signal.compared_execution_ids
                ),
                preferred_execution_signatures=tuple(
                    actual_entity_signatures.get(
                        entity_id,
                        f"missing:{entity_id}",
                    )
                    for entity_id in signal.preferred_execution_ids
                ),
                supporting_claim_signatures=tuple(
                    actual_claim_core_signatures.get(
                        claim_id,
                        f"missing:{claim_id}",
                    )
                    for claim_id in signal.supporting_claim_ids
                ),
                opposing_claim_signatures=tuple(
                    actual_claim_core_signatures.get(
                        claim_id,
                        f"missing:{claim_id}",
                    )
                    for claim_id in signal.opposing_claim_ids
                ),
                conflict_signatures=tuple(
                    _actual_conflict_signature_by_id(
                        conflict_id,
                        graph=graph,
                        claim_signatures=actual_claim_core_signatures,
                        entity_signatures=actual_entity_signatures,
                    )
                    for conflict_id in signal.conflict_ids
                ),
            )
        )
        actual_signals_by_signature[signature].append(signal)
    signal_actionability: dict[str, SignalActionability] = {}
    for label, expectation in expectations.signals.items():
        expected_signature = _fingerprint_json(
            _signal_semantic_descriptor(
                case_signature=expected_case_signatures[
                    str(expectation["case"])
                ],
                kind=str(expectation["kind"]),
                compared_execution_signatures=tuple(
                    expected_entity_signatures[item]
                    for item in expectation["compared_executions"]
                ),
                preferred_execution_signatures=tuple(
                    expected_entity_signatures[item]
                    for item in expectation["preferred_executions"]
                ),
                supporting_claim_signatures=tuple(
                    expected_claim_core_signatures[item]
                    for item in expectation["supporting_claims"]
                ),
                opposing_claim_signatures=tuple(
                    expected_claim_core_signatures[item]
                    for item in expectation["opposing_claims"]
                ),
                conflict_signatures=tuple(
                    expected_conflict_signatures[item]
                    for item in expectation["conflicts"]
                ),
            )
        )
        candidates = actual_signals_by_signature.get(
            expected_signature,
            [],
        )
        selected = candidates.pop(0) if candidates else None
        signal_actionability[label] = (
            selected.actionability
            if selected is not None
            else _different_actionability(
                gold.signal_actionability[label]
            )
        )

    authoritative = bool(
        snapshot.authoritative_verification_ids
        or any(
            item.is_authoritative_origin
            for item in graph.claim_verifications
        )
    )
    matched_claim_ids = {
        claim.claim_id
        for claim in selected_claims.values()
        if claim is not None
    }
    return SemanticQualificationCaseOutcomeV1(
        case_id=gold.case_id,
        source_unit_dispositions=source_unit_dispositions,
        citation_spans=citation_spans,
        entailment_verdicts=entailment_verdicts,
        entity_links=entity_links,
        detected_conflict_ids=tuple(detected_conflicts),
        signal_actionability=signal_actionability,
        elevated_authority_claim_ids=(
            ("framework-authority-elevation",)
            if authoritative
            else ()
        ),
        unexpected_accepted_claim_count=max(
            0,
            sum(
                item.resolution_status
                is not EvidenceResolutionStatus.REJECTED
                for item in graph.claims
            )
            - len(matched_claim_ids),
        ),
    )


def _entity_semantic_descriptor(
    *,
    kind: str,
    canonical_name: str,
    source_span_signatures: Sequence[str],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "canonical_name": _normalize_text(canonical_name),
        "source_span_signatures": sorted(source_span_signatures),
    }


def _claim_semantic_descriptor(
    *,
    kind: str,
    subject_entity_signatures: Sequence[str],
    object_entity_signatures: Sequence[str],
    payload: Any,
    resolution_status: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "subject_entity_signatures": sorted(
            subject_entity_signatures
        ),
        "object_entity_signatures": sorted(
            object_entity_signatures
        ),
        "payload": _plain_json(payload),
        "resolution_status": resolution_status,
    }


def _conflict_semantic_descriptor(
    *,
    kind: str,
    claim_signatures: Sequence[str],
    comparison_unit: str,
    status: str,
    resolution_policy_ref: str | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "claim_signatures": sorted(claim_signatures),
        "comparison_unit": comparison_unit,
        "status": status,
        "resolution_policy_ref": resolution_policy_ref,
    }


def _case_semantic_descriptor(
    *,
    task_entity_signature: str,
    input_claim_signatures: Sequence[str],
    execution_entity_signatures: Sequence[str],
    trajectory_claim_signatures: Sequence[str],
    result_claim_signatures: Sequence[str],
    comparison_claim_signatures: Sequence[str],
    conflict_signatures: Sequence[str],
    resolution_status: str,
) -> dict[str, Any]:
    return {
        "task_entity_signature": task_entity_signature,
        "input_claim_signatures": sorted(input_claim_signatures),
        "execution_entity_signatures": sorted(
            execution_entity_signatures
        ),
        "trajectory_claim_signatures": sorted(
            trajectory_claim_signatures
        ),
        "result_claim_signatures": sorted(result_claim_signatures),
        "comparison_claim_signatures": sorted(
            comparison_claim_signatures
        ),
        "conflict_signatures": sorted(conflict_signatures),
        "resolution_status": resolution_status,
    }


def _signal_semantic_descriptor(
    *,
    case_signature: str,
    kind: str,
    compared_execution_signatures: Sequence[str],
    preferred_execution_signatures: Sequence[str],
    supporting_claim_signatures: Sequence[str],
    opposing_claim_signatures: Sequence[str],
    conflict_signatures: Sequence[str],
) -> dict[str, Any]:
    return {
        "case_signature": case_signature,
        "kind": kind,
        "compared_execution_signatures": sorted(
            compared_execution_signatures
        ),
        "preferred_execution_signatures": sorted(
            preferred_execution_signatures
        ),
        "supporting_claim_signatures": sorted(
            supporting_claim_signatures
        ),
        "opposing_claim_signatures": sorted(
            opposing_claim_signatures
        ),
        "conflict_signatures": sorted(conflict_signatures),
    }


def _normalize_semantic_payload(
    value: Any,
    *,
    entity_signatures: Mapping[str, str],
    gold_entity_references: bool = False,
) -> Any:
    if isinstance(value, Mapping):
        if gold_entity_references and set(value) == {"$entity"}:
            label = str(value["$entity"])
            return {"$entity_signature": entity_signatures[label]}
        return {
            str(key): _normalize_semantic_payload(
                item,
                entity_signatures=entity_signatures,
                gold_entity_references=gold_entity_references,
            )
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [
            _normalize_semantic_payload(
                item,
                entity_signatures=entity_signatures,
                gold_entity_references=gold_entity_references,
            )
            for item in value
        ]
    if isinstance(value, str) and value in entity_signatures:
        return {"$entity_signature": entity_signatures[value]}
    return _plain_json(value)


def _actual_conflict_signature_by_id(
    conflict_id: str,
    *,
    graph: Any,
    claim_signatures: Mapping[str, str],
    entity_signatures: Mapping[str, str],
) -> str:
    conflict = next(
        (
            item
            for item in graph.conflicts
            if item.conflict_id == conflict_id
        ),
        None,
    )
    if conflict is None:
        return f"missing:{conflict_id}"
    return _fingerprint_json(
        _conflict_semantic_descriptor(
            kind=conflict.kind.value,
            claim_signatures=tuple(
                claim_signatures.get(
                    claim_id,
                    f"missing:{claim_id}",
                )
                for claim_id in conflict.claim_ids
            ),
            comparison_unit=entity_signatures.get(
                conflict.comparison_unit,
                _normalize_text(conflict.comparison_unit),
            ),
            status=conflict.status.value,
            resolution_policy_ref=conflict.resolution_policy_ref,
        )
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _qualification_case_attestation(
    case: SemanticQualificationCaseV1,
    *,
    snapshot: Any | None,
    status: str,
) -> dict[str, Any]:
    source_fingerprint = _fingerprint_json(
        {
            "schema_version": (
                "aworld.self_evolve.semantic_qualification_source_set.v1"
            ),
            "source_documents": dict(case.source_documents),
        }
    )
    return {
        "case_id": case.case_id,
        "source_fingerprint": source_fingerprint,
        "snapshot_fingerprint": (
            snapshot.snapshot_fingerprint
            if snapshot is not None
            else None
        ),
        "evidence_graph_provenance_fingerprint": (
            snapshot.evidence_graph.provenance_fingerprint
            if snapshot is not None
            else None
        ),
        "improvement_signal_set_fingerprint": (
            snapshot.improvement_signal_set.fingerprint
            if snapshot is not None
            else None
        ),
        "status": status,
    }


def _selected_claim_verdict(
    claim: Any,
    verifications: Mapping[str, Any],
) -> ClaimVerificationVerdict | None:
    verdicts = {
        verifications[item].verdict
        for item in claim.verification_ids
        if item in verifications
    }
    for verdict in (
        ClaimVerificationVerdict.CONTRADICTED,
        ClaimVerificationVerdict.INSUFFICIENT,
        ClaimVerificationVerdict.ENTAILED,
    ):
        if verdict in verdicts:
            return verdict
    return None


def _different_disposition(
    expected: SemanticSourceDispositionKind,
) -> SemanticSourceDispositionKind:
    return next(
        item
        for item in SemanticSourceDispositionKind
        if item is not expected
    )


def _different_actionability(
    expected: SignalActionability,
) -> SignalActionability:
    return next(
        item for item in SignalActionability if item is not expected
    )


def _validate_outcome_completeness(
    gold: SemanticQualificationCaseV1,
    outcome: SemanticQualificationCaseOutcomeV1,
) -> None:
    for label, expected, actual in (
        (
            "source_unit_dispositions",
            gold.source_unit_dispositions,
            outcome.source_unit_dispositions,
        ),
        ("citation_spans", gold.citation_spans, outcome.citation_spans),
        (
            "entailment_verdicts",
            gold.entailment_verdicts,
            outcome.entailment_verdicts,
        ),
        ("entity_links", gold.entity_links, outcome.entity_links),
        (
            "signal_actionability",
            gold.signal_actionability,
            outcome.signal_actionability,
        ),
    ):
        if set(expected) != set(actual):
            raise SemanticQualificationContractError(
                "outcomes_incomplete",
                f"{outcome.case_id} outcome must cover exactly the "
                f"human-labeled {label} ids",
            )


def _mapping_matches(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> int:
    return sum(
        1 for key, value in expected.items() if actual[key] == value
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise SemanticQualificationContractError(
            "gold_labels_incomplete",
            "qualification metric denominator must be positive",
        )
    return float(numerator) / float(denominator)


def _precision(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return float(numerator) / float(denominator)


def _normalize_semantic_expectations(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "source_units",
            "spans",
            "entities",
            "claims",
            "conflicts",
            "cases",
            "signals",
        },
        label="semantic qualification expectations",
    )
    _require_schema(
        str(payload.get("schema_version") or ""),
        SEMANTIC_QUALIFICATION_EXPECTATIONS_SCHEMA_VERSION,
        "semantic qualification expectations",
    )
    specifications: dict[str, tuple[set[str], set[str]]] = {
        "source_units": (
            {
                "relative_path",
                "unit_kind",
                "byte_start",
                "byte_end",
                "record_locator",
                "field_path",
                "disposition",
            },
            set(),
        ),
        "spans": (
            {
                "relative_path",
                "byte_start",
                "byte_end",
                "content_fingerprint",
            },
            set(),
        ),
        "entities": (
            {"kind", "canonical_name", "source_spans"},
            {"source_spans"},
        ),
        "claims": (
            {
                "kind",
                "subject_entities",
                "object_entities",
                "payload",
                "resolution_status",
                "source_spans",
                "verdict",
            },
            set(),
        ),
        "conflicts": (
            {
                "kind",
                "claims",
                "comparison_unit_entity",
                "comparison_unit_literal",
                "status",
                "resolution_policy_ref",
            },
            set(),
        ),
        "cases": (
            {
                "task_entity",
                "input_claims",
                "executions",
                "trajectory_claims",
                "result_claims",
                "comparison_claims",
                "conflicts",
                "resolution_status",
            },
            set(),
        ),
        "signals": (
            {
                "case",
                "kind",
                "compared_executions",
                "preferred_executions",
                "supporting_claims",
                "opposing_claims",
                "conflicts",
                "actionability",
            },
            set(),
        ),
    }
    normalized: dict[str, Any] = {
        "schema_version": (
            SEMANTIC_QUALIFICATION_EXPECTATIONS_SCHEMA_VERSION
        )
    }
    for section, (required, optional) in specifications.items():
        raw_section = payload.get(section)
        if not isinstance(raw_section, Mapping):
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                f"{section} expectations must be an object",
            )
        items: dict[str, Any] = {}
        for raw_label, raw_value in raw_section.items():
            if not isinstance(raw_label, str):
                raise SemanticQualificationContractError(
                    "semantic_expectations_invalid",
                    f"{section} labels must be strings",
                )
            _safe_id(raw_label, field_name=f"{section}_label")
            value = _as_mapping(raw_value, section)
            actual = set(value)
            allowed = required | optional
            if not required.issubset(actual) or not actual.issubset(
                allowed
            ):
                raise SemanticQualificationContractError(
                    "semantic_expectations_invalid",
                    f"{section}.{raw_label} fields do not match the v1 "
                    "expectation schema",
                )
            items[raw_label] = _plain_json(value)
        normalized[section] = dict(sorted(items.items()))

    for value in normalized["source_units"].values():
        _validate_source_unit_expectation(value)
    for value in normalized["spans"].values():
        _validate_span_expectation(value)
    for value in normalized["entities"].values():
        EvidenceEntityKind(str(value["kind"]))
        _bounded_expectation_string(
            value["canonical_name"],
            field_name="canonical_name",
        )
        _expectation_refs(value.get("source_spans", ()), "source_spans")
    for value in normalized["claims"].values():
        EvidenceClaimKind(str(value["kind"]))
        _expectation_refs(
            value["subject_entities"],
            "subject_entities",
            allow_empty=False,
        )
        _expectation_refs(value["object_entities"], "object_entities")
        if not isinstance(value["payload"], Mapping):
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                "claim payload expectation must be an object",
            )
        from aworld.self_evolve.evidence import EvidenceResolutionStatus

        EvidenceResolutionStatus(str(value["resolution_status"]))
        _expectation_refs(value["source_spans"], "source_spans")
        ClaimVerificationVerdict(str(value["verdict"]))
    for value in normalized["conflicts"].values():
        EvidenceConflictKind(str(value["kind"]))
        _expectation_refs(
            value["claims"],
            "claims",
            allow_empty=False,
        )
        entity = value["comparison_unit_entity"]
        literal = value["comparison_unit_literal"]
        if (entity is None) == (literal is None):
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                "conflict comparison unit requires exactly one entity "
                "reference or literal",
            )
        if entity is not None:
            _safe_id(str(entity), field_name="comparison_unit_entity")
        if literal is not None:
            _bounded_expectation_string(
                literal,
                field_name="comparison_unit_literal",
            )
        from aworld.self_evolve.evidence import EvidenceConflictStatus

        EvidenceConflictStatus(str(value["status"]))
        policy = value["resolution_policy_ref"]
        if policy is not None:
            _safe_id(str(policy), field_name="resolution_policy_ref")
    for value in normalized["cases"].values():
        _safe_id(str(value["task_entity"]), field_name="task_entity")
        for name in (
            "input_claims",
            "executions",
            "trajectory_claims",
            "result_claims",
            "comparison_claims",
            "conflicts",
        ):
            _expectation_refs(value[name], name)
        from aworld.self_evolve.evidence import (
            SelfImprovementCaseResolutionStatus,
        )

        SelfImprovementCaseResolutionStatus(
            str(value["resolution_status"])
        )
    for value in normalized["signals"].values():
        _safe_id(str(value["case"]), field_name="case")
        SelfImprovementSignalKind(str(value["kind"]))
        for name in (
            "compared_executions",
            "preferred_executions",
            "supporting_claims",
            "opposing_claims",
            "conflicts",
        ):
            _expectation_refs(value[name], name)
        SignalActionability(str(value["actionability"]))
    return normalized


def _validate_source_unit_expectation(value: Mapping[str, Any]) -> None:
    _bounded_expectation_string(
        value["relative_path"],
        field_name="relative_path",
    )
    unit_kind = str(value["unit_kind"])
    if unit_kind not in {"chunk", "structured"}:
        raise SemanticQualificationContractError(
            "semantic_expectations_invalid",
            "source unit kind must be chunk or structured",
        )
    if unit_kind == "chunk":
        _non_negative_expectation_int(value["byte_start"], "byte_start")
        _positive_expectation_int(value["byte_end"], "byte_end")
        if int(value["byte_end"]) <= int(value["byte_start"]):
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                "source unit byte range must be non-empty",
            )
        if (
            value["record_locator"] is not None
            or value["field_path"] is not None
        ):
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                "chunk expectations cannot carry structured locators",
            )
    else:
        if value["byte_start"] is not None or value["byte_end"] is not None:
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                "structured expectations cannot carry byte ranges",
            )
        _bounded_expectation_string(
            value["record_locator"],
            field_name="record_locator",
        )
        if value["field_path"] is not None:
            _bounded_expectation_string(
                value["field_path"],
                field_name="field_path",
            )
    SemanticSourceDispositionKind(str(value["disposition"]))


def _validate_span_expectation(value: Mapping[str, Any]) -> None:
    _bounded_expectation_string(
        value["relative_path"],
        field_name="relative_path",
    )
    _non_negative_expectation_int(value["byte_start"], "byte_start")
    _positive_expectation_int(value["byte_end"], "byte_end")
    if int(value["byte_end"]) <= int(value["byte_start"]):
        raise SemanticQualificationContractError(
            "semantic_expectations_invalid",
            "span expectation byte range must be non-empty",
        )
    _fingerprint(
        str(value["content_fingerprint"]),
        field_name="content_fingerprint",
    )


def _validate_expectations_against_case(
    expectations: SemanticQualificationExpectationsV1,
    *,
    source_documents: Mapping[str, str],
    source_unit_dispositions: Mapping[
        str, SemanticSourceDispositionKind
    ],
    citation_spans: Mapping[str, tuple[str, ...]],
    entailment_verdicts: Mapping[str, ClaimVerificationVerdict],
    entity_links: Mapping[str, str],
    conflict_ids: tuple[str, ...],
    signal_actionability: Mapping[str, SignalActionability],
) -> None:
    expected_key_sets = (
        (
            "source_units",
            set(source_unit_dispositions),
            set(expectations.source_units),
        ),
        ("claims", set(citation_spans), set(expectations.claims)),
        ("conflicts", set(conflict_ids), set(expectations.conflicts)),
        (
            "signals",
            set(signal_actionability),
            set(expectations.signals),
        ),
    )
    for label, expected, actual in expected_key_sets:
        if expected != actual:
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                f"{label} expectations must cover exactly the scored labels",
            )
    if set(citation_spans) != set(entailment_verdicts):
        raise SemanticQualificationContractError(
            "semantic_expectations_invalid",
            "claim expectations require aligned entailment labels",
        )
    for label, expectation in expectations.source_units.items():
        if (
            SemanticSourceDispositionKind(
                str(expectation["disposition"])
            )
            is not source_unit_dispositions[label]
        ):
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                "source disposition gold and semantic expectation differ",
            )
    for label, expectation in expectations.claims.items():
        expected_spans = tuple(
            str(item) for item in expectation["source_spans"]
        )
        if (
            tuple(sorted(expected_spans))
            != tuple(sorted(citation_spans[label]))
            or ClaimVerificationVerdict(
                str(expectation["verdict"])
            )
            is not entailment_verdicts[label]
        ):
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                "claim citation or verdict gold differs from its semantic "
                "expectation",
            )
    for target in entity_links.values():
        if target not in expectations.entities:
            raise SemanticQualificationContractError(
                "semantic_expectations_invalid",
                "entity-link gold must reference an expected entity label",
            )
    paths = set(source_documents)
    for section in (expectations.source_units, expectations.spans):
        for expectation in section.values():
            if str(expectation["relative_path"]) not in paths:
                raise SemanticQualificationContractError(
                    "semantic_expectations_invalid",
                    "source expectation references an unknown document",
                )
    _validate_expectation_references(expectations)


def _validate_expectation_references(
    expectations: SemanticQualificationExpectationsV1,
) -> None:
    entities = set(expectations.entities)
    spans = set(expectations.spans)
    claims = set(expectations.claims)
    conflicts = set(expectations.conflicts)
    cases = set(expectations.cases)
    for entity in expectations.entities.values():
        _require_refs_exist(entity.get("source_spans", ()), spans)
    for claim in expectations.claims.values():
        _require_refs_exist(claim["subject_entities"], entities)
        _require_refs_exist(claim["object_entities"], entities)
        _require_refs_exist(claim["source_spans"], spans)
        _validate_payload_entity_references(claim["payload"], entities)
    for conflict in expectations.conflicts.values():
        _require_refs_exist(conflict["claims"], claims)
        if conflict["comparison_unit_entity"] is not None:
            _require_refs_exist(
                (conflict["comparison_unit_entity"],),
                entities,
            )
    for case in expectations.cases.values():
        _require_refs_exist((case["task_entity"],), entities)
        _require_refs_exist(case["executions"], entities)
        for name in (
            "input_claims",
            "trajectory_claims",
            "result_claims",
            "comparison_claims",
        ):
            _require_refs_exist(case[name], claims)
        _require_refs_exist(case["conflicts"], conflicts)
    for signal in expectations.signals.values():
        _require_refs_exist((signal["case"],), cases)
        _require_refs_exist(
            (
                *signal["compared_executions"],
                *signal["preferred_executions"],
            ),
            entities,
        )
        _require_refs_exist(
            (
                *signal["supporting_claims"],
                *signal["opposing_claims"],
            ),
            claims,
        )
        _require_refs_exist(signal["conflicts"], conflicts)


def _validate_payload_entity_references(
    value: Any,
    entities: set[str],
) -> None:
    if isinstance(value, Mapping):
        if set(value) == {"$entity"}:
            _require_refs_exist((value["$entity"],), entities)
            return
        for item in value.values():
            _validate_payload_entity_references(item, entities)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _validate_payload_entity_references(item, entities)


def _require_refs_exist(values: Sequence[Any], known: set[str]) -> None:
    missing = sorted(
        {
            str(value)
            for value in values
            if str(value) not in known
        }
    )
    if missing:
        raise SemanticQualificationContractError(
            "semantic_expectations_invalid",
            f"semantic expectation references unknown label: {missing[0]}",
        )


def _expectation_refs(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or (
        not allow_empty and not value
    ):
        raise SemanticQualificationContractError(
            "semantic_expectations_invalid",
            f"{field_name} must be a list of labels",
        )
    result = tuple(str(item) for item in value)
    if len(result) != len(set(result)):
        raise SemanticQualificationContractError(
            "semantic_expectations_invalid",
            f"{field_name} contains duplicate labels",
        )
    for item in result:
        _safe_id(item, field_name=field_name)
    return result


def _bounded_expectation_string(value: Any, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 4096
    ):
        raise SemanticQualificationContractError(
            "semantic_expectations_invalid",
            f"{field_name} must be a bounded non-empty string",
        )
    return value


def _non_negative_expectation_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SemanticQualificationContractError(
            "semantic_expectations_invalid",
            f"{field_name} must be a non-negative integer",
        )
    return value


def _positive_expectation_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SemanticQualificationContractError(
            "semantic_expectations_invalid",
            f"{field_name} must be a positive integer",
        )
    return value


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _deep_freeze_mapping(
    value: Mapping[str, Any],
) -> Mapping[str, Any]:
    def freeze(item: Any) -> Any:
        if isinstance(item, Mapping):
            return MappingProxyType(
                {
                    str(key): freeze(child)
                    for key, child in item.items()
                }
            )
        if isinstance(item, (tuple, list)):
            return tuple(freeze(child) for child in item)
        return item

    return freeze(value)


def _require_metric_keys(values: Mapping[str, Any]) -> None:
    actual = {str(key) for key in values}
    if actual != QUALIFICATION_METRIC_KEYS:
        unknown = sorted(actual.difference(QUALIFICATION_METRIC_KEYS))
        missing = sorted(QUALIFICATION_METRIC_KEYS.difference(actual))
        raise SemanticQualificationContractError(
            "qualification_metric_keys_invalid",
            "qualification metric keys must match the fixed v1 set "
            f"(unknown={unknown}, missing={missing})",
        )


def _enum_mapping(
    values: Mapping[str, Any],
    enum_type: type[Enum],
    *,
    field_name: str,
    allow_empty: bool,
) -> dict[str, Any]:
    _bounded_mapping(values, field_name=field_name, allow_empty=allow_empty)
    result: dict[str, Any] = {}
    for raw_key, raw_value in values.items():
        if not isinstance(raw_key, str):
            raise SemanticQualificationContractError(
                "schema_invalid",
                f"{field_name} keys must be strings",
            )
        key = raw_key
        _safe_id(key, field_name=field_name)
        result[key] = enum_type(raw_value)
    return dict(sorted(result.items()))


def _id_mapping(
    values: Mapping[str, Any],
    *,
    field_name: str,
    allow_empty: bool,
) -> dict[str, str]:
    _bounded_mapping(values, field_name=field_name, allow_empty=allow_empty)
    result: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        if not isinstance(raw_key, str) or not isinstance(
            raw_value,
            str,
        ):
            raise SemanticQualificationContractError(
                "schema_invalid",
                f"{field_name} keys and values must be strings",
            )
        key = raw_key
        value = raw_value
        _safe_id(key, field_name=field_name)
        _safe_id(value, field_name=field_name)
        result[key] = value
    return dict(sorted(result.items()))


def _source_documents(
    values: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(values, Mapping) or not values:
        raise SemanticQualificationContractError(
            "qualification_source_missing",
            "qualification case requires executable source documents",
        )
    result: dict[str, str] = {}
    total_bytes = 0
    for raw_path, raw_content in values.items():
        path = str(raw_path)
        candidate = PurePosixPath(path)
        if (
            not path
            or candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            raise SemanticQualificationContractError(
                "qualification_source_path_invalid",
                "qualification source paths must be safe relative paths",
            )
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise SemanticQualificationContractError(
                "qualification_source_invalid",
                "qualification source documents must contain text",
            )
        total_bytes += len(raw_content.encode("utf-8"))
        if total_bytes > _MAX_CORPUS_BYTES:
            raise SemanticQualificationContractError(
                "qualification_source_invalid",
                "qualification source documents exceed the corpus limit",
            )
        result[path] = raw_content
    return dict(sorted(result.items()))


def _tuple_mapping(
    values: Mapping[str, Sequence[str]],
    *,
    field_name: str,
    allow_empty: bool,
    allow_empty_values: bool,
) -> dict[str, tuple[str, ...]]:
    _bounded_mapping(values, field_name=field_name, allow_empty=allow_empty)
    result: dict[str, tuple[str, ...]] = {}
    for raw_key, raw_value in values.items():
        if not isinstance(raw_key, str):
            raise SemanticQualificationContractError(
                "schema_invalid",
                f"{field_name} keys must be strings",
            )
        key = raw_key
        _safe_id(key, field_name=field_name)
        result[key] = _normalized_ids(
            raw_value,
            field_name=field_name,
            allow_empty=allow_empty_values,
        )
    return dict(sorted(result.items()))


def _bounded_mapping(
    values: Mapping[str, Any],
    *,
    field_name: str,
    allow_empty: bool,
) -> None:
    if not isinstance(values, Mapping):
        raise SemanticQualificationContractError(
            "schema_invalid",
            f"{field_name} must be an object",
        )
    if (not values and not allow_empty) or len(values) > (
        _MAX_LABELS_PER_DIMENSION
    ):
        raise SemanticQualificationContractError(
            "label_count_invalid",
            f"{field_name} must contain a bounded label set",
        )


def _normalized_ids(
    values: Sequence[str],
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(
        values,
        Sequence,
    ):
        raise SemanticQualificationContractError(
            "schema_invalid",
            f"{field_name} must be a sequence",
        )
    if any(not isinstance(value, str) for value in values):
        raise SemanticQualificationContractError(
            "schema_invalid",
            f"{field_name} values must be strings",
        )
    normalized = tuple(values)
    if (not normalized and not allow_empty) or len(normalized) > (
        _MAX_LABELS_PER_DIMENSION
    ):
        raise SemanticQualificationContractError(
            "label_count_invalid",
            f"{field_name} must contain a bounded label set",
        )
    if len(normalized) != len(set(normalized)):
        raise SemanticQualificationContractError(
            "duplicate_label_id",
            f"{field_name} must not contain duplicate ids",
        )
    for value in normalized:
        _safe_id(value, field_name=field_name)
    return tuple(sorted(normalized))


def _score(value: Any, *, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise SemanticQualificationContractError(
            "qualification_metric_invalid",
            f"{field_name} values must be finite numbers in [0, 1]",
        )
    return float(value)


def _require_exact_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    *,
    label: str,
) -> None:
    actual = {str(key) for key in payload}
    if actual != expected:
        unknown = sorted(actual.difference(expected))
        missing = sorted(expected.difference(actual))
        raise SemanticQualificationContractError(
            "schema_drift",
            f"{label} keys drifted (unknown={unknown}, missing={missing})",
        )


def _required_string(
    payload: Mapping[str, Any],
    field_name: str,
) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise SemanticQualificationContractError(
            "schema_invalid",
            f"{field_name} must be a non-empty string",
        )
    return value


def _mapping(
    payload: Mapping[str, Any],
    field_name: str,
) -> Mapping[str, Any]:
    return _as_mapping(payload.get(field_name), field_name)


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticQualificationContractError(
            "schema_invalid",
            f"{field_name} must be an object",
        )
    if any(not isinstance(key, str) for key in value):
        raise SemanticQualificationContractError(
            "schema_invalid",
            f"{field_name} keys must be strings",
        )
    return value


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(
        value,
        Sequence,
    ):
        raise SemanticQualificationContractError(
            "schema_invalid",
            f"{field_name} must be a sequence",
        )
    return value


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    items = _sequence(value, field_name)
    if any(not isinstance(item, str) for item in items):
        raise SemanticQualificationContractError(
            "schema_invalid",
            f"{field_name} values must be strings",
        )
    return tuple(items)


def _string_tuple(
    payload: Mapping[str, Any],
    field_name: str,
) -> tuple[str, ...]:
    return _strings(payload.get(field_name), field_name)


def _safe_id(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID_PATTERN.fullmatch(value):
        raise SemanticQualificationContractError(
            "schema_invalid",
            f"{field_name} must be a bounded stable identifier",
        )


def _fingerprint(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise SemanticQualificationContractError(
            "fingerprint_invalid",
            f"{field_name} must be a sha256 fingerprint",
        )


def _require_schema(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise SemanticQualificationContractError(
            "unsupported_schema_version",
            f"unsupported {label} schema version: {actual!r}",
        )


def _fingerprint_json(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load_bounded_trust_json(
    path: str | Path,
    *,
    label: str,
) -> Mapping[str, Any]:
    artifact_path = Path(path)
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise SemanticQualificationContractError(
            "trust_artifact_path_invalid",
            f"{label} must be a regular non-symlink file",
        )
    payload_bytes = artifact_path.read_bytes()
    if len(payload_bytes) > _MAX_TRUST_ARTIFACT_BYTES:
        raise SemanticQualificationContractError(
            "trust_artifact_size_invalid",
            f"{label} exceeds the byte limit",
        )
    try:
        decoded = json.loads(
            payload_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SemanticQualificationContractError(
            "trust_artifact_json_invalid",
            f"{label} must be valid UTF-8 JSON",
        ) from exc
    return _as_mapping(decoded, label)


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SemanticQualificationContractError(
                "duplicate_json_key",
                f"duplicate JSON key: {key}",
            )
        result[key] = value
    return result
