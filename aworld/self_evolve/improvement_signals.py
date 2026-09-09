from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from aworld.self_evolve.evidence import (
    ClaimVerificationVerdict,
    EvidenceClaimKind,
    EvidenceEntityKind,
    EvidenceResolutionStatus,
    EvidenceConflictStatus,
    SelfImprovementCaseV1,
    SelfImprovementEvidenceGraphV1,
)


BEHAVIOR_DELTA_SCHEMA_VERSION = (
    "aworld.self_evolve.behavior_delta.v1"
)
SELF_IMPROVEMENT_SIGNAL_SCHEMA_VERSION = (
    "aworld.self_evolve.improvement_signal.v1"
)
SELF_IMPROVEMENT_SIGNAL_SET_SCHEMA_VERSION = (
    "aworld.self_evolve.improvement_signal_set.v1"
)
TARGET_EXECUTION_EVIDENCE_SCHEMA_VERSION = (
    "aworld.self_evolve.target_execution_evidence.v1"
)
TARGET_EVIDENCE_BUNDLE_SCHEMA_VERSION = (
    "aworld.self_evolve.target_evidence_bundle.v1"
)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_BEHAVIOR_ITEMS = 128
_MAX_BEHAVIOR_ITEM_LENGTH = 4096
_MAX_PROJECTION_BYTES = 512 * 1024


class ImprovementSignalContractError(ValueError):
    """A stable failure in an optimizer-facing semantic signal."""

    def __init__(self, reason_code: str, message: str) -> None:
        if not _REASON_PATTERN.fullmatch(reason_code):
            raise ValueError("reason_code must be lower_snake_case")
        self.reason_code = reason_code
        super().__init__(message)


class SelfImprovementSignalKind(str, Enum):
    FAILURE_PATTERN = "failure_pattern"
    RECOVERY_PATTERN = "recovery_pattern"
    PREFERENCE_DELTA = "preference_delta"
    METRIC_DELTA = "metric_delta"
    PRESERVE_BEHAVIOR = "preserve_behavior"
    AVOID_BEHAVIOR = "avoid_behavior"
    CAPABILITY_GAP = "capability_gap"


class SignalVerificationStatus(str, Enum):
    VERIFIED = "verified"
    ADVISORY = "advisory"
    INSUFFICIENT = "insufficient"
    CONTRADICTED = "contradicted"


class SignalActionability(str, Enum):
    ACTIONABLE = "actionable"
    ADVISORY = "advisory"
    BLOCKED = "blocked"


class DatasetSplit(str, Enum):
    TRAIN = "train"
    VALIDATION = "validation"
    HELD_OUT = "held_out"


@dataclass(frozen=True)
class BehaviorDeltaV1:
    """A bounded, source-derived contrast between observed executions."""

    preferred_observations: tuple[str, ...]
    non_preferred_observations: tuple[str, ...]
    result_difference: tuple[str, ...]
    source_claim_ids: tuple[str, ...]
    schema_version: str = BEHAVIOR_DELTA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            BEHAVIOR_DELTA_SCHEMA_VERSION,
            "behavior delta",
        )
        _bounded_strings(
            self.preferred_observations,
            field_name="preferred_observations",
        )
        _bounded_strings(
            self.non_preferred_observations,
            field_name="non_preferred_observations",
        )
        _bounded_strings(
            self.result_difference,
            field_name="result_difference",
        )
        _safe_ids(self.source_claim_ids, field_name="source_claim_ids")
        object.__setattr__(
            self,
            "source_claim_ids",
            tuple(sorted(self.source_claim_ids)),
        )

    @property
    def is_contrastive(self) -> bool:
        return bool(
            self.preferred_observations
            and self.non_preferred_observations
            and self.result_difference
            and self.source_claim_ids
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "preferred_observations": list(self.preferred_observations),
            "non_preferred_observations": list(
                self.non_preferred_observations
            ),
            "result_difference": list(self.result_difference),
            "source_claim_ids": list(self.source_claim_ids),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BehaviorDeltaV1":
        _schema(payload, BEHAVIOR_DELTA_SCHEMA_VERSION)
        return cls(
            preferred_observations=_string_tuple(
                payload,
                "preferred_observations",
            ),
            non_preferred_observations=_string_tuple(
                payload,
                "non_preferred_observations",
            ),
            result_difference=_string_tuple(payload, "result_difference"),
            source_claim_ids=_string_tuple(payload, "source_claim_ids"),
        )


@dataclass(frozen=True)
class SelfImprovementSignalV1:
    signal_id: str
    case_id: str
    kind: SelfImprovementSignalKind
    compared_execution_ids: tuple[str, ...]
    preferred_execution_ids: tuple[str, ...]
    supporting_claim_ids: tuple[str, ...]
    opposing_claim_ids: tuple[str, ...]
    behavior_delta: BehaviorDeltaV1
    metric_delta: Mapping[str, float] = field(default_factory=dict)
    desired_behavior: tuple[str, ...] = ()
    avoid_behavior: tuple[str, ...] = ()
    capability_requirement: tuple[str, ...] = ()
    conflict_ids: tuple[str, ...] = ()
    verification_status: SignalVerificationStatus = (
        SignalVerificationStatus.ADVISORY
    )
    actionability: SignalActionability = SignalActionability.ADVISORY
    reason_codes: tuple[str, ...] = ()
    schema_version: str = SELF_IMPROVEMENT_SIGNAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SELF_IMPROVEMENT_SIGNAL_SCHEMA_VERSION,
            "self-improvement signal",
        )
        _safe_id(self.signal_id, field_name="signal_id")
        _safe_id(self.case_id, field_name="case_id")
        object.__setattr__(
            self,
            "kind",
            SelfImprovementSignalKind(self.kind),
        )
        object.__setattr__(
            self,
            "verification_status",
            SignalVerificationStatus(self.verification_status),
        )
        object.__setattr__(
            self,
            "actionability",
            SignalActionability(self.actionability),
        )
        for name in (
            "compared_execution_ids",
            "preferred_execution_ids",
            "supporting_claim_ids",
            "opposing_claim_ids",
            "conflict_ids",
        ):
            _safe_ids(getattr(self, name), field_name=name)
            object.__setattr__(
                self,
                name,
                tuple(sorted(getattr(self, name))),
            )
        if not isinstance(self.behavior_delta, BehaviorDeltaV1):
            raise ImprovementSignalContractError(
                "behavior_delta_invalid",
                "behavior_delta must be BehaviorDeltaV1",
            )
        _metric_delta(self.metric_delta)
        object.__setattr__(
            self,
            "metric_delta",
            MappingProxyType(
                {
                    str(key): float(value)
                    for key, value in self.metric_delta.items()
                }
            ),
        )
        _bounded_strings(
            self.desired_behavior,
            field_name="desired_behavior",
        )
        _bounded_strings(self.avoid_behavior, field_name="avoid_behavior")
        _bounded_strings(
            self.capability_requirement,
            field_name="capability_requirement",
        )
        _reason_codes(self.reason_codes)
        object.__setattr__(
            self,
            "reason_codes",
            tuple(sorted(self.reason_codes)),
        )
        preferred = set(self.preferred_execution_ids)
        compared = set(self.compared_execution_ids)
        if not preferred.issubset(compared):
            raise ImprovementSignalContractError(
                "preferred_execution_outside_comparison",
                "preferred executions must be part of the comparison",
            )
        if self.kind in {
            SelfImprovementSignalKind.PREFERENCE_DELTA,
            SelfImprovementSignalKind.METRIC_DELTA,
        } and len(self.compared_execution_ids) < 2:
            raise ImprovementSignalContractError(
                "comparison_incomplete",
                "comparison signals require at least two executions",
            )
        if (
            self.kind is SelfImprovementSignalKind.PREFERENCE_DELTA
            and not self.preferred_execution_ids
        ):
            raise ImprovementSignalContractError(
                "preference_missing",
                "preference signal requires a preferred execution",
            )
        if self.actionability is SignalActionability.ACTIONABLE:
            self._validate_actionable()

    def _validate_actionable(self) -> None:
        if self.verification_status is not SignalVerificationStatus.VERIFIED:
            raise ImprovementSignalContractError(
                "actionable_signal_unverified",
                "actionable signal must be verified",
            )
        if not self.supporting_claim_ids:
            raise ImprovementSignalContractError(
                "actionable_signal_unsupported",
                "actionable signal requires supporting claims",
            )
        if not self.behavior_delta.is_contrastive:
            raise ImprovementSignalContractError(
                "actionable_signal_contrast_missing",
                "actionable signal requires a source-derived behavior contrast",
            )
        if not (
            self.desired_behavior
            and self.avoid_behavior
            and self.capability_requirement
        ):
            raise ImprovementSignalContractError(
                "actionable_signal_guidance_missing",
                "actionable signal requires desired, avoid, and capability guidance",
            )

    @property
    def fingerprint(self) -> str:
        return _fingerprint_json(self.canonical_dict())

    def validate_against(
        self,
        graph: SelfImprovementEvidenceGraphV1,
        case: SelfImprovementCaseV1,
    ) -> None:
        if self.case_id != case.case_id:
            raise ImprovementSignalContractError(
                "signal_case_mismatch",
                "signal and case identities do not match",
            )
        entities = {item.entity_id: item for item in graph.entities}
        claims = {item.claim_id: item for item in graph.claims}
        verifications = {
            item.verification_id: item
            for item in graph.claim_verifications
        }
        conflicts = {
            item.conflict_id: item for item in graph.conflicts
        }
        case_executions = set(case.execution_entity_ids)
        _refs_exist(
            self.compared_execution_ids,
            entities,
            "dangling_execution_reference",
        )
        if any(
            entities[item].kind is not EvidenceEntityKind.EXECUTION
            for item in self.compared_execution_ids
        ):
            raise ImprovementSignalContractError(
                "signal_execution_invalid",
                "signal execution references must identify executions",
            )
        if not set(self.compared_execution_ids).issubset(case_executions):
            raise ImprovementSignalContractError(
                "signal_execution_outside_case",
                "signal executions must belong to the same case",
            )
        _refs_exist(
            (
                *self.supporting_claim_ids,
                *self.opposing_claim_ids,
                *self.behavior_delta.source_claim_ids,
            ),
            claims,
            "dangling_claim_reference",
        )
        case_claim_ids = {
            *case.input_claim_ids,
            *case.trajectory_claim_ids,
            *case.result_claim_ids,
            *case.comparison_claim_ids,
        }
        referenced_claim_ids = {
            *self.supporting_claim_ids,
            *self.opposing_claim_ids,
            *self.behavior_delta.source_claim_ids,
        }
        if not referenced_claim_ids.issubset(case_claim_ids):
            raise ImprovementSignalContractError(
                "signal_claim_outside_case",
                "signal evidence must belong to the same case",
            )
        _refs_exist(
            self.conflict_ids,
            conflicts,
            "dangling_conflict_reference",
        )
        if not set(self.behavior_delta.source_claim_ids).issubset(
            set(self.supporting_claim_ids)
        ):
            raise ImprovementSignalContractError(
                "behavior_delta_provenance_missing",
                "behavior delta claims must be supporting signal claims",
            )
        if self.actionability is SignalActionability.ACTIONABLE:
            supporting = [
                claims[item] for item in self.supporting_claim_ids
            ]
            if any(
                item.resolution_status
                is not EvidenceResolutionStatus.RESOLVED
                for item in supporting
            ):
                raise ImprovementSignalContractError(
                    "actionable_signal_evidence_unresolved",
                    "actionable signal requires resolved supporting claims",
                )
            for claim in supporting:
                selected = [
                    verifications[item]
                    for item in claim.verification_ids
                ]
                if not any(
                    item.verdict is ClaimVerificationVerdict.ENTAILED
                    for item in selected
                ) or any(
                    item.verdict
                    is ClaimVerificationVerdict.CONTRADICTED
                    for item in selected
                ):
                    raise ImprovementSignalContractError(
                        "actionable_signal_evidence_unverified",
                        "actionable signal evidence must be entailed",
                    )
            if any(
                conflicts[item].status is EvidenceConflictStatus.UNRESOLVED
                for item in self.conflict_ids
            ):
                raise ImprovementSignalContractError(
                    "actionable_signal_conflict_unresolved",
                    "unresolved conflicts block actionable signals",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "signal_id": self.signal_id,
            "case_id": self.case_id,
            "kind": self.kind.value,
            "compared_execution_ids": list(self.compared_execution_ids),
            "preferred_execution_ids": list(self.preferred_execution_ids),
            "supporting_claim_ids": list(self.supporting_claim_ids),
            "opposing_claim_ids": list(self.opposing_claim_ids),
            "behavior_delta": self.behavior_delta.to_dict(),
            "metric_delta": {
                key: float(value)
                for key, value in sorted(self.metric_delta.items())
            },
            "desired_behavior": list(self.desired_behavior),
            "avoid_behavior": list(self.avoid_behavior),
            "capability_requirement": list(
                self.capability_requirement
            ),
            "conflict_ids": list(self.conflict_ids),
            "verification_status": self.verification_status.value,
            "actionability": self.actionability.value,
            "reason_codes": list(self.reason_codes),
        }

    def canonical_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        for name in (
            "compared_execution_ids",
            "preferred_execution_ids",
            "supporting_claim_ids",
            "opposing_claim_ids",
            "conflict_ids",
            "reason_codes",
        ):
            value[name] = sorted(value[name])
        return value

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SelfImprovementSignalV1":
        _schema(payload, SELF_IMPROVEMENT_SIGNAL_SCHEMA_VERSION)
        signal = cls(
            signal_id=str(payload.get("signal_id") or ""),
            case_id=str(payload.get("case_id") or ""),
            kind=SelfImprovementSignalKind(
                str(payload.get("kind") or "")
            ),
            compared_execution_ids=_string_tuple(
                payload,
                "compared_execution_ids",
            ),
            preferred_execution_ids=_string_tuple(
                payload,
                "preferred_execution_ids",
            ),
            supporting_claim_ids=_string_tuple(
                payload,
                "supporting_claim_ids",
            ),
            opposing_claim_ids=_string_tuple(
                payload,
                "opposing_claim_ids",
            ),
            behavior_delta=BehaviorDeltaV1.from_dict(
                _as_mapping(payload.get("behavior_delta", {}))
            ),
            metric_delta={
                str(key): float(value)
                for key, value in _as_mapping(
                    payload.get("metric_delta", {})
                ).items()
            },
            desired_behavior=_string_tuple(payload, "desired_behavior"),
            avoid_behavior=_string_tuple(payload, "avoid_behavior"),
            capability_requirement=_string_tuple(
                payload,
                "capability_requirement",
            ),
            conflict_ids=_string_tuple(payload, "conflict_ids"),
            verification_status=SignalVerificationStatus(
                str(payload.get("verification_status") or "")
            ),
            actionability=SignalActionability(
                str(payload.get("actionability") or "")
            ),
            reason_codes=_string_tuple(payload, "reason_codes"),
        )
        claimed = payload.get("fingerprint")
        if claimed is not None and claimed != signal.fingerprint:
            raise ImprovementSignalContractError(
                "fingerprint_mismatch",
                "self-improvement signal fingerprint mismatch",
            )
        return signal


@dataclass(frozen=True)
class SelfImprovementSignalSetV1:
    signals: tuple[SelfImprovementSignalV1, ...]
    case_splits: Mapping[str, DatasetSplit]
    synthesis_report_refs: tuple[str, ...]
    critic_report_refs: tuple[str, ...]
    evidence_graph_logical_fingerprint: str
    schema_version: str = SELF_IMPROVEMENT_SIGNAL_SET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            SELF_IMPROVEMENT_SIGNAL_SET_SCHEMA_VERSION,
            "self-improvement signal set",
        )
        _index_unique(self.signals, "signal_id")
        _safe_ids(
            self.synthesis_report_refs,
            field_name="synthesis_report_refs",
        )
        _safe_ids(
            self.critic_report_refs,
            field_name="critic_report_refs",
        )
        if not self.synthesis_report_refs or not self.critic_report_refs:
            raise ImprovementSignalContractError(
                "signal_stage_report_missing",
                "signal set requires synthesis and critic stage reports",
            )
        _fingerprint(
            self.evidence_graph_logical_fingerprint,
            field_name="evidence_graph_logical_fingerprint",
        )
        object.__setattr__(
            self,
            "signals",
            tuple(
                sorted(self.signals, key=lambda item: item.signal_id)
            ),
        )
        object.__setattr__(
            self,
            "synthesis_report_refs",
            tuple(sorted(self.synthesis_report_refs)),
        )
        object.__setattr__(
            self,
            "critic_report_refs",
            tuple(sorted(self.critic_report_refs)),
        )
        normalized_splits: dict[str, DatasetSplit] = {}
        for case_id, split in self.case_splits.items():
            _safe_id(case_id, field_name="case_splits")
            normalized_splits[case_id] = DatasetSplit(split)
        object.__setattr__(
            self,
            "case_splits",
            MappingProxyType(normalized_splits),
        )
        missing = sorted(
            {
                signal.case_id
                for signal in self.signals
                if signal.case_id not in normalized_splits
            }
        )
        if missing:
            raise ImprovementSignalContractError(
                "signal_case_split_missing",
                f"signal case has no dataset split: {missing[0]}",
            )

    @property
    def signal_actionability_rate(self) -> float:
        eligible = [
            item
            for item in self.signals
            if self.case_splits[item.case_id]
            in {DatasetSplit.TRAIN, DatasetSplit.VALIDATION}
        ]
        if not eligible:
            return 0.0
        actionable = sum(
            item.actionability is SignalActionability.ACTIONABLE
            for item in eligible
        )
        return actionable / len(eligible)

    @property
    def fingerprint(self) -> str:
        return _fingerprint_json(self.canonical_dict())

    def optimizer_projection(
        self,
        *,
        allowed_splits: Sequence[DatasetSplit] = (DatasetSplit.TRAIN,),
        max_signals: int = 2000,
        max_bytes: int = _MAX_PROJECTION_BYTES,
    ) -> tuple[dict[str, Any], ...]:
        splits = {DatasetSplit(item) for item in allowed_splits}
        if DatasetSplit.HELD_OUT in splits:
            raise ImprovementSignalContractError(
                "held_out_signal_exposure",
                "held-out signals cannot enter optimizer context",
            )
        if max_signals < 1 or max_bytes < 1:
            raise ImprovementSignalContractError(
                "signal_projection_limit_invalid",
                "signal projection limits must be positive",
            )
        selected = [
            item.canonical_dict()
            for item in sorted(
                self.signals,
                key=lambda value: value.signal_id,
            )
            if self.case_splits[item.case_id] in splits
            and item.actionability is SignalActionability.ACTIONABLE
        ]
        if len(selected) > max_signals:
            raise ImprovementSignalContractError(
                "signal_projection_limit_exceeded",
                "optimizer signal count exceeds the configured bound",
            )
        if len(_canonical_bytes(selected)) > max_bytes:
            raise ImprovementSignalContractError(
                "signal_projection_limit_exceeded",
                "optimizer signal bytes exceed the configured bound",
            )
        return tuple(selected)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "signals": [
                item.canonical_dict()
                for item in sorted(
                    self.signals,
                    key=lambda value: value.signal_id,
                )
            ],
            "case_splits": {
                key: value.value
                for key, value in sorted(self.case_splits.items())
            },
            "synthesis_report_refs": sorted(self.synthesis_report_refs),
            "critic_report_refs": sorted(self.critic_report_refs),
            "evidence_graph_logical_fingerprint": (
                self.evidence_graph_logical_fingerprint
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_dict(), "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SelfImprovementSignalSetV1":
        _schema(payload, SELF_IMPROVEMENT_SIGNAL_SET_SCHEMA_VERSION)
        signal_set = cls(
            signals=tuple(
                SelfImprovementSignalV1.from_dict(_as_mapping(item))
                for item in _sequence(payload.get("signals", ()), "signals")
            ),
            case_splits={
                str(key): DatasetSplit(str(value))
                for key, value in _as_mapping(
                    payload.get("case_splits", {})
                ).items()
            },
            synthesis_report_refs=_string_tuple(
                payload,
                "synthesis_report_refs",
            ),
            critic_report_refs=_string_tuple(
                payload,
                "critic_report_refs",
            ),
            evidence_graph_logical_fingerprint=str(
                payload.get("evidence_graph_logical_fingerprint") or ""
            ),
        )
        claimed = payload.get("fingerprint")
        if claimed is not None and claimed != signal_set.fingerprint:
            raise ImprovementSignalContractError(
                "fingerprint_mismatch",
                "self-improvement signal set fingerprint mismatch",
            )
        return signal_set


@dataclass(frozen=True)
class TargetExecutionEvidenceV1:
    case_id: str
    task_entity_id: str
    execution_entity_id: str
    trajectory_claim_id: str
    result_claim_ids: tuple[str, ...]
    trace_ref: str
    trace_fingerprint: str
    schema_version: str = TARGET_EXECUTION_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            TARGET_EXECUTION_EVIDENCE_SCHEMA_VERSION,
            "target execution evidence",
        )
        for name in (
            "case_id",
            "task_entity_id",
            "execution_entity_id",
            "trajectory_claim_id",
            "trace_ref",
        ):
            _safe_id(getattr(self, name), field_name=name)
        _safe_ids(self.result_claim_ids, field_name="result_claim_ids")
        object.__setattr__(
            self,
            "result_claim_ids",
            tuple(sorted(self.result_claim_ids)),
        )
        _fingerprint(
            self.trace_fingerprint,
            field_name="trace_fingerprint",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "task_entity_id": self.task_entity_id,
            "execution_entity_id": self.execution_entity_id,
            "trajectory_claim_id": self.trajectory_claim_id,
            "result_claim_ids": list(self.result_claim_ids),
            "trace_ref": self.trace_ref,
            "trace_fingerprint": self.trace_fingerprint,
        }

    def canonical_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value["result_claim_ids"] = sorted(self.result_claim_ids)
        return value

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "TargetExecutionEvidenceV1":
        _schema(payload, TARGET_EXECUTION_EVIDENCE_SCHEMA_VERSION)
        return cls(
            case_id=str(payload.get("case_id") or ""),
            task_entity_id=str(payload.get("task_entity_id") or ""),
            execution_entity_id=str(
                payload.get("execution_entity_id") or ""
            ),
            trajectory_claim_id=str(
                payload.get("trajectory_claim_id") or ""
            ),
            result_claim_ids=_string_tuple(payload, "result_claim_ids"),
            trace_ref=str(payload.get("trace_ref") or ""),
            trace_fingerprint=str(
                payload.get("trace_fingerprint") or ""
            ),
        )


@dataclass(frozen=True)
class TargetEvidenceBundleV1:
    executions: tuple[TargetExecutionEvidenceV1, ...]
    evidence_graph_logical_fingerprint: str
    schema_version: str = TARGET_EVIDENCE_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.schema_version,
            TARGET_EVIDENCE_BUNDLE_SCHEMA_VERSION,
            "target evidence bundle",
        )
        _fingerprint(
            self.evidence_graph_logical_fingerprint,
            field_name="evidence_graph_logical_fingerprint",
        )
        trajectory_ids = [item.trajectory_claim_id for item in self.executions]
        if len(trajectory_ids) != len(set(trajectory_ids)):
            raise ImprovementSignalContractError(
                "duplicate_trajectory_evidence",
                "trajectory evidence must be unique",
            )
        object.__setattr__(
            self,
            "executions",
            tuple(
                sorted(
                    self.executions,
                    key=lambda item: (
                        item.case_id,
                        item.execution_entity_id,
                        item.trajectory_claim_id,
                    ),
                )
            ),
        )

    @property
    def fingerprint(self) -> str:
        return _fingerprint_json(self.canonical_dict())

    def validate_against(
        self,
        graph: SelfImprovementEvidenceGraphV1,
        cases: Sequence[SelfImprovementCaseV1],
    ) -> None:
        case_by_id = {item.case_id: item for item in cases}
        claims = {item.claim_id: item for item in graph.claims}
        entities = {item.entity_id: item for item in graph.entities}
        seen_cases: set[str] = set()
        for item in self.executions:
            case = case_by_id.get(item.case_id)
            if case is None:
                raise ImprovementSignalContractError(
                    "dangling_case_reference",
                    "target evidence references an unknown case",
                )
            if item.task_entity_id != case.task_entity_id:
                raise ImprovementSignalContractError(
                    "target_task_mismatch",
                    "target evidence task does not match its case",
                )
            if item.execution_entity_id not in case.execution_entity_ids:
                raise ImprovementSignalContractError(
                    "target_execution_outside_case",
                    "target execution does not belong to its case",
                )
            if (
                item.execution_entity_id not in entities
                or entities[item.execution_entity_id].kind
                is not EvidenceEntityKind.EXECUTION
            ):
                raise ImprovementSignalContractError(
                    "target_execution_invalid",
                    "target execution must reference an execution entity",
                )
            seen_cases.add(item.case_id)
            if item.trajectory_claim_id not in case.trajectory_claim_ids:
                raise ImprovementSignalContractError(
                    "target_trajectory_outside_case",
                    "target trajectory does not belong to its case",
                )
            trajectory_claim = claims.get(item.trajectory_claim_id)
            if (
                trajectory_claim is None
                or trajectory_claim.kind
                is not EvidenceClaimKind.EXECUTION_TRAJECTORY
                or trajectory_claim.subject_entity_ids
                != (item.execution_entity_id,)
                or trajectory_claim.resolution_status
                is not EvidenceResolutionStatus.RESOLVED
            ):
                raise ImprovementSignalContractError(
                    "target_trajectory_invalid",
                    "target trajectory kind and execution ownership must match",
                )
            if (
                trajectory_claim.payload.get("trace_ref")
                != item.trace_ref
                or trajectory_claim.payload.get("trace_fingerprint")
                != item.trace_fingerprint
            ):
                raise ImprovementSignalContractError(
                    "target_trace_attestation_mismatch",
                    "target trace ref and fingerprint must match trajectory evidence",
                )
            _refs_exist(
                (item.trajectory_claim_id, *item.result_claim_ids),
                claims,
                "dangling_claim_reference",
            )
            expected_results = {
                claim_id
                for claim_id in case.result_claim_ids
                if (
                    claim_id in claims
                    and claims[claim_id].kind
                    is EvidenceClaimKind.EXECUTION_RESULT
                    and claims[claim_id].subject_entity_ids
                    == (item.execution_entity_id,)
                )
            }
            if set(item.result_claim_ids) != expected_results:
                raise ImprovementSignalContractError(
                    "target_result_evidence_incomplete",
                    "target result evidence must include every matching result",
                )
            if any(
                claims[claim_id].resolution_status
                is not EvidenceResolutionStatus.RESOLVED
                for claim_id in item.result_claim_ids
            ):
                raise ImprovementSignalContractError(
                    "target_result_evidence_unresolved",
                    "target results must be resolved evidence",
                )
        expected_cases = {
            item.case_id for item in cases if item.trajectory_claim_ids
        }
        if seen_cases != expected_cases:
            raise ImprovementSignalContractError(
                "target_case_evidence_incomplete",
                "target bundle must cover every case with trajectory evidence",
            )
        for case in cases:
            expected_trajectories = set(case.trajectory_claim_ids)
            actual_trajectories = {
                item.trajectory_claim_id
                for item in self.executions
                if item.case_id == case.case_id
            }
            if actual_trajectories != expected_trajectories:
                raise ImprovementSignalContractError(
                    "target_trajectory_evidence_incomplete",
                    "target bundle must include every eligible trajectory",
                )
        if (
            self.evidence_graph_logical_fingerprint
            != graph.logical_fingerprint
        ):
            raise ImprovementSignalContractError(
                "evidence_graph_fingerprint_mismatch",
                "target bundle was compiled from a different evidence graph",
            )

    def trace_projection(self) -> tuple[dict[str, Any], ...]:
        """Return every validated trace without preference-derived weights."""

        return tuple(
            {
                "case_id": item.case_id,
                "task_entity_id": item.task_entity_id,
                "execution_entity_id": item.execution_entity_id,
                "trajectory_claim_id": item.trajectory_claim_id,
                "result_claim_ids": sorted(item.result_claim_ids),
                "trace_ref": item.trace_ref,
                "trace_fingerprint": item.trace_fingerprint,
            }
            for item in sorted(
                self.executions,
                key=lambda value: (
                    value.case_id,
                    value.execution_entity_id,
                ),
            )
        )

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "executions": [
                item.canonical_dict()
                for item in sorted(
                    self.executions,
                    key=lambda value: (
                        value.case_id,
                        value.execution_entity_id,
                    ),
                )
            ],
            "evidence_graph_logical_fingerprint": (
                self.evidence_graph_logical_fingerprint
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_dict(), "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "TargetEvidenceBundleV1":
        _schema(payload, TARGET_EVIDENCE_BUNDLE_SCHEMA_VERSION)
        bundle = cls(
            executions=tuple(
                TargetExecutionEvidenceV1.from_dict(_as_mapping(item))
                for item in _sequence(
                    payload.get("executions", ()),
                    "executions",
                )
            ),
            evidence_graph_logical_fingerprint=str(
                payload.get("evidence_graph_logical_fingerprint") or ""
            ),
        )
        claimed = payload.get("fingerprint")
        if claimed is not None and claimed != bundle.fingerprint:
            raise ImprovementSignalContractError(
                "fingerprint_mismatch",
                "target evidence bundle fingerprint mismatch",
            )
        return bundle


def _require_schema(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise ImprovementSignalContractError(
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
        raise ImprovementSignalContractError(
            "schema_invalid",
            f"{field_name} must be a bounded stable identifier",
        )


def _safe_ids(values: Sequence[str], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ImprovementSignalContractError(
            "duplicate_reference",
            f"{field_name} must not contain duplicates",
        )
    for value in values:
        _safe_id(value, field_name=field_name)


def _fingerprint(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise ImprovementSignalContractError(
            "fingerprint_invalid",
            f"{field_name} must be a sha256 fingerprint",
        )


def _reason_codes(values: Sequence[str]) -> None:
    if len(values) != len(set(values)):
        raise ImprovementSignalContractError(
            "duplicate_reason_code",
            "reason_codes must not contain duplicates",
        )
    for value in values:
        if not isinstance(value, str) or not _REASON_PATTERN.fullmatch(value):
            raise ImprovementSignalContractError(
                "reason_code_invalid",
                "reason codes must be lower_snake_case",
            )


def _bounded_strings(values: Sequence[str], *, field_name: str) -> None:
    if len(values) > _MAX_BEHAVIOR_ITEMS:
        raise ImprovementSignalContractError(
            "signal_content_limit_exceeded",
            f"{field_name} contains too many entries",
        )
    for value in values:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _MAX_BEHAVIOR_ITEM_LENGTH
        ):
            raise ImprovementSignalContractError(
                "signal_content_invalid",
                f"{field_name} contains an invalid entry",
            )


def _metric_delta(values: Mapping[str, float]) -> None:
    if len(values) > _MAX_BEHAVIOR_ITEMS:
        raise ImprovementSignalContractError(
            "signal_content_limit_exceeded",
            "metric_delta contains too many entries",
        )
    for key, value in values.items():
        _safe_id(key, field_name="metric_delta")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ImprovementSignalContractError(
                "metric_delta_invalid",
                "metric deltas must be finite numbers",
            )


def _index_unique(
    values: Sequence[Any],
    identity_field: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        identity = getattr(value, identity_field)
        if identity in result:
            raise ImprovementSignalContractError(
                "duplicate_identity",
                f"duplicate {identity_field}: {identity}",
            )
        result[identity] = value
    return result


def _refs_exist(
    references: Sequence[str],
    index: Mapping[str, Any] | set[str],
    reason_code: str,
) -> None:
    missing = [item for item in references if item not in index]
    if missing:
        raise ImprovementSignalContractError(
            reason_code,
            f"unknown reference: {missing[0]}",
        )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ImprovementSignalContractError(
            "schema_invalid",
            "expected an object",
        )
    return value


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise ImprovementSignalContractError(
            "schema_invalid",
            f"{field_name} must be an array",
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
        raise ImprovementSignalContractError(
            "schema_invalid",
            "value is not canonical JSON",
        ) from exc


def _fingerprint_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()
