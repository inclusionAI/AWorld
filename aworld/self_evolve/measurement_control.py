"""Pure contracts and decisions for the self-evolve measurement control plane.

This module deliberately has no runner, replay, store, or Campaign imports.  It
defines the immutable plan that those infrastructure layers must execute and
the deterministic feasibility/admission decisions they may consume.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import InitVar, asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:
    from aworld.core.tool.replay_policy import EvidencePolicyProfileV2
    from aworld.self_evolve.replay_adaptation import IsolationDecision


MEASUREMENT_PLAN_SCHEMA_VERSION = "aworld.self_evolve.measurement_plan.v2"
MEASUREMENT_WORK_UNIT_SCHEMA_VERSION = (
    "aworld.self_evolve.measurement_work_unit.v1"
)
DEADLINE_POLICY_SCHEMA_VERSION = "aworld.self_evolve.deadline_policy.v1"
SAMPLING_STAGE_SCHEMA_VERSION = "aworld.self_evolve.sampling_stage.v1"
ISOLATION_REQUIREMENT_SCHEMA_VERSION = (
    "aworld.self_evolve.isolation_requirement.v1"
)
ISOLATION_SUMMARY_SCHEMA_VERSION = "aworld.self_evolve.isolation_summary.v1"
ADAPTIVE_POLICY_SCHEMA_VERSION = "aworld.self_evolve.adaptive_policy.v1"
WORK_UNIT_JOURNAL_EVENT_SCHEMA_VERSION = (
    "aworld.self_evolve.work_unit_journal_event.v1"
)
MEASUREMENT_CONTROL_INDEX_SCHEMA_VERSION = (
    "aworld.self_evolve.measurement_control_index.v2"
)
MEASUREMENT_CONTROL_OBSERVATION_SCHEMA_VERSION = (
    "aworld.self_evolve.measurement_control_observation.v2"
)
LANE_MATERIALIZATION_ATTESTATION_SCHEMA_VERSION = (
    "aworld.self_evolve.lane_materialization_attestation.v1"
)
MEASUREMENT_CONTROL_SNAPSHOT_SCHEMA_VERSION = (
    "aworld.self_evolve.measurement_control_snapshot.v1"
)
LEGACY_MEASUREMENT_CONTROL_DESCRIPTION_SCHEMA_VERSION = (
    "aworld.self_evolve.legacy_measurement_control_description.v1"
)

_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_WORK_UNIT_ID_RE = re.compile(r"^measurement-unit-[0-9a-f]{32}$")
_MEASUREMENT_PLAN_CONTRACT_SEAL = object()
_CANONICAL_ISOLATION_RESOURCE_DIMENSIONS = (
    "workspace_root",
    "runtime_root",
    "browser_profile",
    "endpoint_namespace",
    "evidence_directory",
    "service_instance",
    "resource_identity",
    "cleanup_ownership",
)


class MeasurementArm(str, Enum):
    CONTROL = "control"
    TREATMENT = "treatment"


class SamplingStageKind(str, Enum):
    SENTINEL = "sentinel"
    EXPANSION = "expansion"
    REGRESSION_TRANSFER = "regression_transfer"
    TIE_BREAK = "tie_break"


class CaseVisibilityRole(str, Enum):
    REPAIR_SCREENING = "repair_screening"
    AUTHORITATIVE_VALIDATION = "authoritative_validation"
    REGRESSION_TRANSFER = "regression_transfer"


class FeasibilityStatus(str, Enum):
    FEASIBLE = "feasible"
    INFEASIBLE_DEADLINE = "infeasible_deadline"
    RESUMABLE_CHUNKED = "resumable_chunked"


class AdaptiveDecisionKind(str, Enum):
    CONTINUE_CURRENT_STAGE = "continue_current_stage"
    ADMIT_EXPANSION = "admit_expansion"
    ADMIT_REQUIRED_REGRESSION_TRANSFER = "admit_required_regression_transfer"
    ADMIT_TIE_BREAK = "admit_tie_break"
    STOP_CONFIDENT_POSITIVE = "stop_confident_positive"
    STOP_NEGATIVE = "stop_negative"
    STOP_REGRESSION = "stop_regression"
    STOP_FUTILITY = "stop_futility"
    STOP_INVALID_CONTROL = "stop_invalid_control"
    STOP_FRAMEWORK_BLOCKED = "stop_framework_blocked"
    STOP_ZERO_YIELD = "stop_zero_yield"
    STOP_INCONCLUSIVE = "stop_inconclusive"
    MEASUREMENT_INCOMPLETE_CHECKPOINT = "measurement_incomplete_checkpoint"
    MEASUREMENT_INCOMPLETE_CAMPAIGN_DEADLINE = (
        "measurement_incomplete_campaign_deadline"
    )


class MeasurementWorkUnitState(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    SUCCEEDED = "succeeded"
    TASK_FAILED = "task_failed"
    MEMBER_TIMED_OUT = "member_timed_out"
    EVIDENCE_INVALID = "evidence_invalid"
    CANCELLED_DECISIVE = "cancelled_decisive"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCEEDED,
            self.TASK_FAILED,
            self.MEMBER_TIMED_OUT,
            self.EVIDENCE_INVALID,
            self.CANCELLED_DECISIVE,
        }


class MeasurementControlEventKind(str, Enum):
    LEASE_ACQUIRED = "lease_acquired"
    EXECUTION_STARTED = "execution_started"
    CHECKPOINT_RECORDED = "checkpoint_recorded"
    LEASE_RECOVERED = "lease_recovered"
    TERMINAL_RECORDED = "terminal_recorded"
    RETRY_SCHEDULED = "retry_scheduled"


class MeasurementControlCorruptionError(ValueError):
    """Fail-closed checkpoint error that can never become candidate feedback."""

    owner = "measurement"

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class DeadlinePolicy:
    """Frozen, non-interchangeable execution deadline layers.

    ``campaign_wall_deadline_seconds=None`` is intentional: a missing operator
    deadline remains absent rather than being synthesized from member timeout.
    """

    attempt_timeout_seconds: float
    member_hard_deadline_seconds: float
    checkpoint_quantum_seconds: float
    evidence_finalization_timeout_seconds: float = 45.0
    campaign_wall_deadline_seconds: float | None = None
    resumable_chunked: bool = False
    schema_version: str = DEADLINE_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DEADLINE_POLICY_SCHEMA_VERSION:
            raise ValueError("unsupported deadline policy schema")
        for field_name in (
            "attempt_timeout_seconds",
            "member_hard_deadline_seconds",
            "checkpoint_quantum_seconds",
            "evidence_finalization_timeout_seconds",
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_number(getattr(self, field_name), field_name),
            )
        if self.campaign_wall_deadline_seconds is not None:
            object.__setattr__(
                self,
                "campaign_wall_deadline_seconds",
                _positive_number(
                    self.campaign_wall_deadline_seconds,
                    "campaign_wall_deadline_seconds",
                ),
            )
        if self.attempt_timeout_seconds > self.member_hard_deadline_seconds:
            raise ValueError("attempt timeout cannot exceed member hard deadline")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_timeout_seconds": self.attempt_timeout_seconds,
            "member_hard_deadline_seconds": self.member_hard_deadline_seconds,
            "checkpoint_quantum_seconds": self.checkpoint_quantum_seconds,
            "evidence_finalization_timeout_seconds": (
                self.evidence_finalization_timeout_seconds
            ),
            "campaign_wall_deadline_seconds": self.campaign_wall_deadline_seconds,
            "resumable_chunked": self.resumable_chunked,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "DeadlinePolicy":
        _require_schema(value, DEADLINE_POLICY_SCHEMA_VERSION, "deadline policy")
        return cls(
            attempt_timeout_seconds=_number(
                value.get("attempt_timeout_seconds"), "attempt_timeout_seconds"
            ),
            member_hard_deadline_seconds=_number(
                value.get("member_hard_deadline_seconds"),
                "member_hard_deadline_seconds",
            ),
            checkpoint_quantum_seconds=_number(
                value.get("checkpoint_quantum_seconds"),
                "checkpoint_quantum_seconds",
            ),
            evidence_finalization_timeout_seconds=_number(
                value.get("evidence_finalization_timeout_seconds", 45.0),
                "evidence_finalization_timeout_seconds",
            ),
            campaign_wall_deadline_seconds=_optional_number(
                value.get("campaign_wall_deadline_seconds"),
                "campaign_wall_deadline_seconds",
            ),
            resumable_chunked=_boolean(
                value.get("resumable_chunked", False), "resumable_chunked"
            ),
        )


@dataclass(frozen=True)
class SamplingStage:
    stage_id: str
    kind: SamplingStageKind
    case_ids: tuple[str, ...]
    minimum_case_count: int
    batch_size: int = 1
    optional: bool = False
    requires_positive_effect: bool = False
    visibility_role: CaseVisibilityRole = (
        CaseVisibilityRole.AUTHORITATIVE_VALIDATION
    )
    schema_version: str = SAMPLING_STAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SAMPLING_STAGE_SCHEMA_VERSION:
            raise ValueError("unsupported sampling stage schema")
        _safe_id(self.stage_id, "stage_id")
        object.__setattr__(self, "kind", SamplingStageKind(self.kind))
        object.__setattr__(
            self, "visibility_role", CaseVisibilityRole(self.visibility_role)
        )
        cases = tuple(self.case_ids)
        if not cases or len(cases) != len(set(cases)):
            raise ValueError("stage case ids must be non-empty and unique")
        for case_id in cases:
            _safe_id(case_id, "case_id")
        object.__setattr__(self, "case_ids", cases)
        minimum = _non_negative_int(self.minimum_case_count, "minimum_case_count")
        if minimum > len(cases):
            raise ValueError("minimum_case_count cannot exceed stage case count")
        if self.kind in {
            SamplingStageKind.SENTINEL,
            SamplingStageKind.REGRESSION_TRANSFER,
        } and minimum == 0:
            raise ValueError("required measurement stages need at least one case")
        _positive_int(self.batch_size, "batch_size")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "stage_id": self.stage_id,
            "kind": self.kind.value,
            "case_ids": list(self.case_ids),
            "minimum_case_count": self.minimum_case_count,
            "batch_size": self.batch_size,
            "optional": self.optional,
            "requires_positive_effect": self.requires_positive_effect,
            "visibility_role": self.visibility_role.value,
        }

    @property
    def contract_fingerprint(self) -> str:
        return stable_control_fingerprint(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SamplingStage":
        _require_schema(value, SAMPLING_STAGE_SCHEMA_VERSION, "sampling stage")
        return cls(
            stage_id=_required_text(value.get("stage_id"), "stage_id"),
            kind=SamplingStageKind(str(value.get("kind"))),
            case_ids=_string_tuple(value.get("case_ids"), "case_ids"),
            minimum_case_count=_non_negative_int(
                value.get("minimum_case_count"), "minimum_case_count"
            ),
            batch_size=_positive_int(value.get("batch_size", 1), "batch_size"),
            optional=_boolean(value.get("optional", False), "optional"),
            requires_positive_effect=_boolean(
                value.get("requires_positive_effect", False),
                "requires_positive_effect",
            ),
            visibility_role=CaseVisibilityRole(
                str(
                    value.get("visibility_role")
                    or CaseVisibilityRole.AUTHORITATIVE_VALIDATION.value
                )
            ),
        )


@dataclass(frozen=True)
class IsolationRequirement:
    requested_lane_ceiling: int
    resource_dimensions: tuple[str, ...]
    schema_version: str = ISOLATION_REQUIREMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ISOLATION_REQUIREMENT_SCHEMA_VERSION:
            raise ValueError("unsupported isolation requirement schema")
        _positive_int(self.requested_lane_ceiling, "requested_lane_ceiling")
        dimensions = tuple(self.resource_dimensions)
        if not dimensions or len(dimensions) != len(set(dimensions)):
            raise ValueError("resource dimensions must be non-empty and unique")
        for dimension in dimensions:
            _safe_id(dimension, "resource dimension")
        object.__setattr__(self, "resource_dimensions", dimensions)

    @property
    def fingerprint(self) -> str:
        return stable_control_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "requested_lane_ceiling": self.requested_lane_ceiling,
            "resource_dimensions": list(self.resource_dimensions),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "IsolationRequirement":
        _require_schema(
            value, ISOLATION_REQUIREMENT_SCHEMA_VERSION, "isolation requirement"
        )
        return cls(
            requested_lane_ceiling=_positive_int(
                value.get("requested_lane_ceiling"), "requested_lane_ceiling"
            ),
            resource_dimensions=_string_tuple(
                value.get("resource_dimensions"), "resource_dimensions"
            ),
        )


@dataclass(frozen=True)
class IsolationSummary:
    requested_lane_ceiling: int
    safe_lane_count: int
    isolation_proven: bool
    isolation_grant_fingerprint: str | None = None
    limiting_reason: str | None = None
    schema_version: str = ISOLATION_SUMMARY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ISOLATION_SUMMARY_SCHEMA_VERSION:
            raise ValueError("unsupported isolation summary schema")
        _positive_int(self.requested_lane_ceiling, "requested_lane_ceiling")
        _positive_int(self.safe_lane_count, "safe_lane_count")
        if self.safe_lane_count > self.requested_lane_ceiling:
            raise ValueError("safe lane count exceeds requested lane ceiling")
        _optional_fingerprint(
            self.isolation_grant_fingerprint, "isolation grant fingerprint"
        )
        if self.safe_lane_count > 1 and (
            not self.isolation_proven
            or self.isolation_grant_fingerprint is None
        ):
            raise ValueError("multiple safe lanes require a verified isolation grant")
        if not self.isolation_proven and self.safe_lane_count != 1:
            raise ValueError("unproven isolation must use one exclusive lane")
        if not self.isolation_proven and not self.limiting_reason:
            raise ValueError("exclusive fallback requires a limiting reason")

    @classmethod
    def isolated(
        cls,
        *,
        requirement: IsolationRequirement,
        safe_lane_count: int,
        isolation_grant_fingerprint: str,
    ) -> "IsolationSummary":
        return cls(
            requested_lane_ceiling=requirement.requested_lane_ceiling,
            safe_lane_count=safe_lane_count,
            isolation_proven=True,
            isolation_grant_fingerprint=isolation_grant_fingerprint,
        )

    @classmethod
    def exclusive_fallback(
        cls,
        *,
        requirement: IsolationRequirement,
        reason: str,
    ) -> "IsolationSummary":
        return cls(
            requested_lane_ceiling=requirement.requested_lane_ceiling,
            safe_lane_count=1,
            isolation_proven=False,
            limiting_reason=_required_text(reason, "fallback reason"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "requested_lane_ceiling": self.requested_lane_ceiling,
            "safe_lane_count": self.safe_lane_count,
            "isolation_proven": self.isolation_proven,
            "isolation_grant_fingerprint": self.isolation_grant_fingerprint,
            "limiting_reason": self.limiting_reason,
        }

    @property
    def execution_decision_fingerprint(self) -> str:
        return stable_control_fingerprint(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "IsolationSummary":
        _require_schema(value, ISOLATION_SUMMARY_SCHEMA_VERSION, "isolation summary")
        return cls(
            requested_lane_ceiling=_positive_int(
                value.get("requested_lane_ceiling"), "requested_lane_ceiling"
            ),
            safe_lane_count=_positive_int(
                value.get("safe_lane_count"), "safe_lane_count"
            ),
            isolation_proven=_boolean(
                value.get("isolation_proven"), "isolation_proven"
            ),
            isolation_grant_fingerprint=_optional_text(
                value.get("isolation_grant_fingerprint")
            ),
            limiting_reason=_optional_text(value.get("limiting_reason")),
        )


def _canonical_measurement_contracts(
    *,
    isolation_decision: "IsolationDecision",
    evidence_policy_profile: "EvidencePolicyProfileV2",
) -> tuple[
    "IsolationDecision",
    "EvidencePolicyProfileV2",
    IsolationRequirement,
    IsolationSummary,
]:
    """Validate the two authority-bearing artifacts and derive plan projections.

    The plan deliberately never accepts caller-supplied fingerprints or lane
    summaries at this boundary.  A digest is an identity, not proof that the
    underlying isolation or evidence contract exists.
    """

    from aworld.core.tool.replay_policy import EvidencePolicyProfileV2
    from aworld.self_evolve.replay_adaptation import IsolationDecision

    if not isinstance(isolation_decision, IsolationDecision):
        raise TypeError("measurement plan requires a canonical IsolationDecision")
    if not isinstance(evidence_policy_profile, EvidencePolicyProfileV2):
        raise TypeError(
            "measurement plan requires a canonical EvidencePolicyProfileV2"
        )
    canonical_decision = IsolationDecision.from_dict(isolation_decision.to_dict())
    canonical_profile = EvidencePolicyProfileV2.from_dict(
        evidence_policy_profile.to_dict()
    )
    if canonical_decision != isolation_decision:
        raise ValueError("isolation decision is not canonical")
    if canonical_profile != evidence_policy_profile:
        raise ValueError("evidence policy profile is not canonical")

    requirement = IsolationRequirement(
        requested_lane_ceiling=canonical_decision.requested_lane_count,
        resource_dimensions=_CANONICAL_ISOLATION_RESOURCE_DIMENSIONS,
    )
    if canonical_decision.fallback is not None:
        summary = IsolationSummary.exclusive_fallback(
            requirement=requirement,
            reason=(
                f"{canonical_decision.fallback.code}:"
                f"{canonical_decision.fallback.limiting_resource}"
            ),
        )
    else:
        # This branch is reachable only when IsolationDecision has a complete
        # canonical grant proof, including the single-lane case.
        summary = IsolationSummary.isolated(
            requirement=requirement,
            safe_lane_count=canonical_decision.safe_lane_count,
            isolation_grant_fingerprint=canonical_decision.grant_set.fingerprint,
        )
    if not canonical_decision.grant_set.grants:
        if summary.safe_lane_count != 1 or summary.isolation_proven:
            raise ValueError("zero-grant isolation must remain exclusive")
    return canonical_decision, canonical_profile, requirement, summary


@dataclass(frozen=True)
class AdaptiveMeasurementPolicy:
    minimum_effect: float
    minimum_independent_cases: int
    maximum_invalid_controls: int
    zero_yield_window: int
    require_regression_transfer: bool = True
    futility_enabled: bool = True
    schema_version: str = ADAPTIVE_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ADAPTIVE_POLICY_SCHEMA_VERSION:
            raise ValueError("unsupported adaptive policy schema")
        object.__setattr__(
            self,
            "minimum_effect",
            _finite_number(self.minimum_effect, "minimum_effect"),
        )
        _positive_int(self.minimum_independent_cases, "minimum_independent_cases")
        _positive_int(self.maximum_invalid_controls, "maximum_invalid_controls")
        _positive_int(self.zero_yield_window, "zero_yield_window")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "minimum_effect": self.minimum_effect,
            "minimum_independent_cases": self.minimum_independent_cases,
            "maximum_invalid_controls": self.maximum_invalid_controls,
            "zero_yield_window": self.zero_yield_window,
            "require_regression_transfer": self.require_regression_transfer,
            "futility_enabled": self.futility_enabled,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AdaptiveMeasurementPolicy":
        _require_schema(value, ADAPTIVE_POLICY_SCHEMA_VERSION, "adaptive policy")
        return cls(
            minimum_effect=_number(value.get("minimum_effect"), "minimum_effect"),
            minimum_independent_cases=_positive_int(
                value.get("minimum_independent_cases"),
                "minimum_independent_cases",
            ),
            maximum_invalid_controls=_positive_int(
                value.get("maximum_invalid_controls"),
                "maximum_invalid_controls",
            ),
            zero_yield_window=_positive_int(
                value.get("zero_yield_window"), "zero_yield_window"
            ),
            require_regression_transfer=_boolean(
                value.get("require_regression_transfer", True),
                "require_regression_transfer",
            ),
            futility_enabled=_boolean(
                value.get("futility_enabled", True), "futility_enabled"
            ),
        )


@dataclass(frozen=True)
class MeasurementWorkUnitV1:
    work_unit_id: str
    measurement_plan_fingerprint: str
    experiment_id: str
    artifact_fingerprint: str
    pairing_control_fingerprint: str
    dataset_fingerprint: str
    case_id: str
    arm: MeasurementArm
    repetition_id: int
    execution_contract_fingerprint: str
    evidence_policy_fingerprint: str
    sampling_contract_fingerprint: str
    isolation_decision_fingerprint: str
    stage_id: str
    depends_on_work_unit_id: str | None = None
    schema_version: str = MEASUREMENT_WORK_UNIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEASUREMENT_WORK_UNIT_SCHEMA_VERSION:
            raise ValueError("unsupported measurement work-unit schema")
        if not _WORK_UNIT_ID_RE.fullmatch(self.work_unit_id):
            raise ValueError("invalid measurement work-unit id")
        _fingerprint(
            self.measurement_plan_fingerprint, "measurement plan fingerprint"
        )
        _safe_id(self.experiment_id, "experiment_id")
        _fingerprint(self.artifact_fingerprint, "artifact fingerprint")
        _fingerprint(
            self.pairing_control_fingerprint, "pairing control fingerprint"
        )
        _fingerprint(self.dataset_fingerprint, "dataset fingerprint")
        _safe_id(self.case_id, "case_id")
        object.__setattr__(self, "arm", MeasurementArm(self.arm))
        _positive_int(self.repetition_id, "repetition_id")
        _fingerprint(
            self.execution_contract_fingerprint,
            "execution contract fingerprint",
        )
        _fingerprint(self.evidence_policy_fingerprint, "evidence policy fingerprint")
        _fingerprint(
            self.sampling_contract_fingerprint, "sampling contract fingerprint"
        )
        _fingerprint(
            self.isolation_decision_fingerprint,
            "isolation decision fingerprint",
        )
        _safe_id(self.stage_id, "stage_id")
        if self.arm is MeasurementArm.CONTROL and self.depends_on_work_unit_id:
            raise ValueError("control work unit cannot depend on treatment")
        if self.arm is MeasurementArm.TREATMENT and not self.depends_on_work_unit_id:
            raise ValueError("treatment work unit requires its paired control")
        if self.depends_on_work_unit_id is not None and not _WORK_UNIT_ID_RE.fullmatch(
            self.depends_on_work_unit_id
        ):
            raise ValueError("invalid dependent work-unit id")

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "artifact_fingerprint": self.artifact_fingerprint,
            "pairing_control_fingerprint": self.pairing_control_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "case_id": self.case_id,
            "arm": self.arm.value,
            "repetition_id": self.repetition_id,
            "execution_contract_fingerprint": self.execution_contract_fingerprint,
            "evidence_policy_fingerprint": self.evidence_policy_fingerprint,
            "sampling_contract_fingerprint": self.sampling_contract_fingerprint,
            "isolation_decision_fingerprint": self.isolation_decision_fingerprint,
            "depends_on_work_unit_id": self.depends_on_work_unit_id,
        }

    @classmethod
    def create(
        cls,
        *,
        measurement_plan_fingerprint: str,
        experiment_id: str,
        artifact_fingerprint: str,
        pairing_control_fingerprint: str,
        dataset_fingerprint: str,
        case_id: str,
        arm: MeasurementArm | str,
        repetition_id: int,
        execution_contract_fingerprint: str,
        evidence_policy_fingerprint: str,
        sampling_contract_fingerprint: str,
        isolation_decision_fingerprint: str,
        stage_id: str,
        depends_on_work_unit_id: str | None = None,
    ) -> "MeasurementWorkUnitV1":
        role = MeasurementArm(arm)
        identity_payload = {
            "schema_version": MEASUREMENT_WORK_UNIT_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "artifact_fingerprint": artifact_fingerprint,
            "pairing_control_fingerprint": pairing_control_fingerprint,
            "dataset_fingerprint": dataset_fingerprint,
            "case_id": case_id,
            "arm": role.value,
            "repetition_id": repetition_id,
            "execution_contract_fingerprint": execution_contract_fingerprint,
            "evidence_policy_fingerprint": evidence_policy_fingerprint,
            "sampling_contract_fingerprint": sampling_contract_fingerprint,
            "isolation_decision_fingerprint": isolation_decision_fingerprint,
            "depends_on_work_unit_id": depends_on_work_unit_id,
        }
        return cls(
            work_unit_id="measurement-unit-" + _digest(identity_payload)[:32],
            measurement_plan_fingerprint=measurement_plan_fingerprint,
            experiment_id=experiment_id,
            artifact_fingerprint=artifact_fingerprint,
            pairing_control_fingerprint=pairing_control_fingerprint,
            dataset_fingerprint=dataset_fingerprint,
            case_id=case_id,
            arm=role,
            repetition_id=repetition_id,
            execution_contract_fingerprint=execution_contract_fingerprint,
            evidence_policy_fingerprint=evidence_policy_fingerprint,
            sampling_contract_fingerprint=sampling_contract_fingerprint,
            isolation_decision_fingerprint=isolation_decision_fingerprint,
            stage_id=stage_id,
            depends_on_work_unit_id=depends_on_work_unit_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "work_unit_id": self.work_unit_id,
            "measurement_plan_fingerprint": self.measurement_plan_fingerprint,
            "experiment_id": self.experiment_id,
            "artifact_fingerprint": self.artifact_fingerprint,
            "pairing_control_fingerprint": self.pairing_control_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "case_id": self.case_id,
            "arm": self.arm.value,
            "repetition_id": self.repetition_id,
            "execution_contract_fingerprint": self.execution_contract_fingerprint,
            "evidence_policy_fingerprint": self.evidence_policy_fingerprint,
            "sampling_contract_fingerprint": self.sampling_contract_fingerprint,
            "isolation_decision_fingerprint": self.isolation_decision_fingerprint,
            "stage_id": self.stage_id,
            "depends_on_work_unit_id": self.depends_on_work_unit_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementWorkUnitV1":
        _require_schema(
            value, MEASUREMENT_WORK_UNIT_SCHEMA_VERSION, "measurement work unit"
        )
        loaded = cls(
            work_unit_id=_required_text(value.get("work_unit_id"), "work_unit_id"),
            measurement_plan_fingerprint=_required_text(
                value.get("measurement_plan_fingerprint"),
                "measurement_plan_fingerprint",
            ),
            experiment_id=_required_text(value.get("experiment_id"), "experiment_id"),
            artifact_fingerprint=_required_text(
                value.get("artifact_fingerprint"), "artifact_fingerprint"
            ),
            pairing_control_fingerprint=_required_text(
                value.get("pairing_control_fingerprint"),
                "pairing_control_fingerprint",
            ),
            dataset_fingerprint=_required_text(
                value.get("dataset_fingerprint"), "dataset_fingerprint"
            ),
            case_id=_required_text(value.get("case_id"), "case_id"),
            arm=MeasurementArm(str(value.get("arm"))),
            repetition_id=_positive_int(
                value.get("repetition_id"), "repetition_id"
            ),
            execution_contract_fingerprint=_required_text(
                value.get("execution_contract_fingerprint"),
                "execution_contract_fingerprint",
            ),
            evidence_policy_fingerprint=_required_text(
                value.get("evidence_policy_fingerprint"),
                "evidence_policy_fingerprint",
            ),
            sampling_contract_fingerprint=_required_text(
                value.get("sampling_contract_fingerprint"),
                "sampling_contract_fingerprint",
            ),
            isolation_decision_fingerprint=_required_text(
                value.get("isolation_decision_fingerprint"),
                "isolation_decision_fingerprint",
            ),
            stage_id=_required_text(value.get("stage_id"), "stage_id"),
            depends_on_work_unit_id=_optional_text(
                value.get("depends_on_work_unit_id")
            ),
        )
        expected = "measurement-unit-" + _digest(loaded.identity_payload)[:32]
        if loaded.work_unit_id != expected:
            raise ValueError("work-unit id does not match frozen identity")
        return loaded

    @property
    def baseline_compatibility_key(self) -> "BaselineCompatibilityKey | None":
        if self.arm is not MeasurementArm.CONTROL:
            return None
        return BaselineCompatibilityKey(
            artifact_fingerprint=self.artifact_fingerprint,
            pairing_control_fingerprint=self.pairing_control_fingerprint,
            dataset_fingerprint=self.dataset_fingerprint,
            case_id=self.case_id,
            repetition_id=self.repetition_id,
            execution_contract_fingerprint=self.execution_contract_fingerprint,
            evidence_policy_fingerprint=self.evidence_policy_fingerprint,
            sampling_contract_fingerprint=self.sampling_contract_fingerprint,
            isolation_decision_fingerprint=self.isolation_decision_fingerprint,
        )


@dataclass(frozen=True)
class BaselineCompatibilityKey:
    artifact_fingerprint: str
    pairing_control_fingerprint: str
    dataset_fingerprint: str
    case_id: str
    repetition_id: int
    execution_contract_fingerprint: str
    evidence_policy_fingerprint: str
    sampling_contract_fingerprint: str
    isolation_decision_fingerprint: str

    def __post_init__(self) -> None:
        for name in (
            "artifact_fingerprint",
            "pairing_control_fingerprint",
            "dataset_fingerprint",
            "execution_contract_fingerprint",
            "evidence_policy_fingerprint",
            "sampling_contract_fingerprint",
            "isolation_decision_fingerprint",
        ):
            _fingerprint(getattr(self, name), name)
        _safe_id(self.case_id, "case_id")
        _positive_int(self.repetition_id, "repetition_id")

    @property
    def fingerprint(self) -> str:
        return stable_control_fingerprint(
            {
                "artifact_fingerprint": self.artifact_fingerprint,
                "pairing_control_fingerprint": self.pairing_control_fingerprint,
                "dataset_fingerprint": self.dataset_fingerprint,
                "case_id": self.case_id,
                "repetition_id": self.repetition_id,
                "execution_contract_fingerprint": (
                    self.execution_contract_fingerprint
                ),
                "evidence_policy_fingerprint": self.evidence_policy_fingerprint,
                "sampling_contract_fingerprint": self.sampling_contract_fingerprint,
                "isolation_decision_fingerprint": (
                    self.isolation_decision_fingerprint
                ),
            }
        )


@dataclass(frozen=True)
class WorkUnitJournalEvent:
    event_id: str
    measurement_plan_fingerprint: str
    work_unit_id: str
    kind: MeasurementControlEventKind
    previous_state: MeasurementWorkUnitState
    new_state: MeasurementWorkUnitState
    occurred_at: str
    attempt_id: str
    lease_expires_at: str | None = None
    observation_fingerprint: str | None = None
    attempt_cost_seconds: float = 0.0
    reason_code: str | None = None
    schema_version: str = WORK_UNIT_JOURNAL_EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != WORK_UNIT_JOURNAL_EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported work-unit journal-event schema")
        if not re.fullmatch(r"measurement-event-[0-9a-f]{32}", self.event_id):
            raise ValueError("invalid work-unit journal event id")
        _fingerprint(
            self.measurement_plan_fingerprint, "measurement plan fingerprint"
        )
        if not _WORK_UNIT_ID_RE.fullmatch(self.work_unit_id):
            raise ValueError("invalid measurement work-unit id")
        object.__setattr__(self, "kind", MeasurementControlEventKind(self.kind))
        object.__setattr__(
            self, "previous_state", MeasurementWorkUnitState(self.previous_state)
        )
        object.__setattr__(
            self, "new_state", MeasurementWorkUnitState(self.new_state)
        )
        _utc_datetime(self.occurred_at, "occurred_at")
        _safe_id(self.attempt_id, "attempt_id")
        if self.lease_expires_at is not None:
            lease_expiry = _utc_datetime(
                self.lease_expires_at, "lease_expires_at"
            )
            if lease_expiry <= _utc_datetime(self.occurred_at, "occurred_at"):
                raise ValueError("lease expiry must be later than event time")
        _optional_fingerprint(
            self.observation_fingerprint, "observation fingerprint"
        )
        object.__setattr__(
            self,
            "attempt_cost_seconds",
            _non_negative_number(
                self.attempt_cost_seconds, "attempt_cost_seconds"
            ),
        )
        if self.reason_code is not None:
            _safe_id(self.reason_code, "reason_code")
        self._validate_transition_shape()

    def _validate_transition_shape(self) -> None:
        if self.kind is MeasurementControlEventKind.LEASE_ACQUIRED:
            if self.previous_state not in {
                MeasurementWorkUnitState.PENDING,
                MeasurementWorkUnitState.CHECKPOINTED,
            } or self.new_state is not MeasurementWorkUnitState.LEASED:
                raise ValueError("lease acquisition has an invalid transition")
            if self.lease_expires_at is None:
                raise ValueError("lease acquisition requires an expiry")
        elif self.kind is MeasurementControlEventKind.EXECUTION_STARTED:
            if (
                self.previous_state is not MeasurementWorkUnitState.LEASED
                or self.new_state is not MeasurementWorkUnitState.RUNNING
            ):
                raise ValueError("execution start has an invalid transition")
            if self.lease_expires_at is None:
                raise ValueError("execution start must retain its lease expiry")
        elif self.kind in {
            MeasurementControlEventKind.CHECKPOINT_RECORDED,
            MeasurementControlEventKind.LEASE_RECOVERED,
        }:
            if self.previous_state not in {
                MeasurementWorkUnitState.LEASED,
                MeasurementWorkUnitState.RUNNING,
            } or self.new_state is not MeasurementWorkUnitState.CHECKPOINTED:
                raise ValueError("checkpoint event has an invalid transition")
            if self.lease_expires_at is not None:
                raise ValueError("checkpoint event cannot retain an active lease")
            if (
                self.kind is MeasurementControlEventKind.LEASE_RECOVERED
                and self.reason_code != "lease_expired"
            ):
                raise ValueError("lease recovery requires lease_expired ownership")
        elif self.kind is MeasurementControlEventKind.TERMINAL_RECORDED:
            if self.previous_state not in {
                MeasurementWorkUnitState.LEASED,
                MeasurementWorkUnitState.RUNNING,
            } or not self.new_state.terminal:
                raise ValueError("terminal event has an invalid transition")
            if self.observation_fingerprint is None:
                raise ValueError("terminal event requires an immutable observation")
            if self.lease_expires_at is not None:
                raise ValueError("terminal event cannot retain an active lease")
        elif self.kind is MeasurementControlEventKind.RETRY_SCHEDULED:
            if self.previous_state not in {
                MeasurementWorkUnitState.MEMBER_TIMED_OUT,
                MeasurementWorkUnitState.EVIDENCE_INVALID,
                MeasurementWorkUnitState.TASK_FAILED,
            } or self.new_state is not MeasurementWorkUnitState.CHECKPOINTED:
                raise ValueError("measurement retry has an invalid transition")
            if self.lease_expires_at is not None:
                raise ValueError("measurement retry cannot retain an active lease")
            if self.reason_code != "measurement_infrastructure_retry":
                raise ValueError(
                    "measurement retry requires infrastructure retry ownership"
                )
        if (
            self.kind is not MeasurementControlEventKind.TERMINAL_RECORDED
            and self.observation_fingerprint is not None
        ):
            raise ValueError("only terminal events may reference an observation")
        if self.kind in {
            MeasurementControlEventKind.LEASE_ACQUIRED,
            MeasurementControlEventKind.EXECUTION_STARTED,
        } and self.attempt_cost_seconds != 0:
            raise ValueError("attempt cost is recorded only when an attempt ends")

    @classmethod
    def create(
        cls,
        *,
        measurement_plan_fingerprint: str,
        work_unit_id: str,
        kind: MeasurementControlEventKind | str,
        previous_state: MeasurementWorkUnitState | str,
        new_state: MeasurementWorkUnitState | str,
        occurred_at: str,
        attempt_id: str,
        lease_expires_at: str | None = None,
        observation_fingerprint: str | None = None,
        attempt_cost_seconds: float = 0.0,
        reason_code: str | None = None,
    ) -> "WorkUnitJournalEvent":
        event_kind = MeasurementControlEventKind(kind)
        old_state = MeasurementWorkUnitState(previous_state)
        next_state = MeasurementWorkUnitState(new_state)
        payload = {
            "schema_version": WORK_UNIT_JOURNAL_EVENT_SCHEMA_VERSION,
            "measurement_plan_fingerprint": measurement_plan_fingerprint,
            "work_unit_id": work_unit_id,
            "kind": event_kind.value,
            "previous_state": old_state.value,
            "new_state": next_state.value,
            "occurred_at": occurred_at,
            "attempt_id": attempt_id,
            "lease_expires_at": lease_expires_at,
            "observation_fingerprint": observation_fingerprint,
            "attempt_cost_seconds": float(attempt_cost_seconds),
            "reason_code": reason_code,
        }
        return cls(
            event_id="measurement-event-" + _digest(payload)[:32],
            measurement_plan_fingerprint=measurement_plan_fingerprint,
            work_unit_id=work_unit_id,
            kind=event_kind,
            previous_state=old_state,
            new_state=next_state,
            occurred_at=occurred_at,
            attempt_id=attempt_id,
            lease_expires_at=lease_expires_at,
            observation_fingerprint=observation_fingerprint,
            attempt_cost_seconds=attempt_cost_seconds,
            reason_code=reason_code,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "measurement_plan_fingerprint": self.measurement_plan_fingerprint,
            "work_unit_id": self.work_unit_id,
            "kind": self.kind.value,
            "previous_state": self.previous_state.value,
            "new_state": self.new_state.value,
            "occurred_at": self.occurred_at,
            "attempt_id": self.attempt_id,
            "lease_expires_at": self.lease_expires_at,
            "observation_fingerprint": self.observation_fingerprint,
            "attempt_cost_seconds": self.attempt_cost_seconds,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "WorkUnitJournalEvent":
        _require_schema(
            value,
            WORK_UNIT_JOURNAL_EVENT_SCHEMA_VERSION,
            "work-unit journal event",
        )
        loaded = cls(
            event_id=_required_text(value.get("event_id"), "event_id"),
            measurement_plan_fingerprint=_required_text(
                value.get("measurement_plan_fingerprint"),
                "measurement_plan_fingerprint",
            ),
            work_unit_id=_required_text(value.get("work_unit_id"), "work_unit_id"),
            kind=MeasurementControlEventKind(str(value.get("kind"))),
            previous_state=MeasurementWorkUnitState(
                str(value.get("previous_state"))
            ),
            new_state=MeasurementWorkUnitState(str(value.get("new_state"))),
            occurred_at=_required_text(value.get("occurred_at"), "occurred_at"),
            attempt_id=_required_text(value.get("attempt_id"), "attempt_id"),
            lease_expires_at=_optional_text(value.get("lease_expires_at")),
            observation_fingerprint=_optional_text(
                value.get("observation_fingerprint")
            ),
            attempt_cost_seconds=_number(
                value.get("attempt_cost_seconds", 0), "attempt_cost_seconds"
            ),
            reason_code=_optional_text(value.get("reason_code")),
        )
        expected = cls.create(
            measurement_plan_fingerprint=loaded.measurement_plan_fingerprint,
            work_unit_id=loaded.work_unit_id,
            kind=loaded.kind,
            previous_state=loaded.previous_state,
            new_state=loaded.new_state,
            occurred_at=loaded.occurred_at,
            attempt_id=loaded.attempt_id,
            lease_expires_at=loaded.lease_expires_at,
            observation_fingerprint=loaded.observation_fingerprint,
            attempt_cost_seconds=loaded.attempt_cost_seconds,
            reason_code=loaded.reason_code,
        ).event_id
        if loaded.event_id != expected:
            raise ValueError("journal event id does not match immutable payload")
        return loaded


@dataclass(frozen=True)
class LaneMaterializationClaim:
    dimension: str
    declared_identity: str
    observed_device: int
    observed_inode: int
    ownership_marker_fingerprint: str

    def __post_init__(self) -> None:
        if self.dimension not in {
            "workspace_root",
            "runtime_root",
            "browser_profile",
            "endpoint_namespace",
            "evidence_directory",
        }:
            raise ValueError("unsupported lane materialization claim dimension")
        _bounded_text(self.declared_identity, "declared_identity", 4096)
        _positive_int(self.observed_device, "observed_device")
        _positive_int(self.observed_inode, "observed_inode")
        _fingerprint(
            self.ownership_marker_fingerprint,
            "ownership marker fingerprint",
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "LaneMaterializationClaim":
        return cls(
            dimension=_required_text(value.get("dimension"), "dimension"),
            declared_identity=_required_text(
                value.get("declared_identity"), "declared_identity"
            ),
            observed_device=_positive_int(
                value.get("observed_device"), "observed_device"
            ),
            observed_inode=_positive_int(
                value.get("observed_inode"), "observed_inode"
            ),
            ownership_marker_fingerprint=_required_text(
                value.get("ownership_marker_fingerprint"),
                "ownership_marker_fingerprint",
            ),
        )


@dataclass(frozen=True)
class LaneMaterializationAttestationV1:
    """Content-addressed proof issued after framework-owned resource probes."""

    attestation_fingerprint: str
    measurement_plan_fingerprint: str
    isolation_decision_fingerprint: str
    evidence_policy_fingerprint: str
    lane_id: int
    isolation_grant_fingerprint: str | None
    topology_fingerprint: str
    topology_json: str
    writer_attestation_fingerprint: str
    writer_attestation_json: str
    claims: tuple[LaneMaterializationClaim, ...]
    recorded_at: str
    authority_public_key_fingerprint: str
    authority_signature: str
    schema_version: str = LANE_MATERIALIZATION_ATTESTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LANE_MATERIALIZATION_ATTESTATION_SCHEMA_VERSION:
            raise ValueError("unsupported lane materialization attestation schema")
        for value, label in (
            (self.attestation_fingerprint, "attestation fingerprint"),
            (self.measurement_plan_fingerprint, "measurement plan fingerprint"),
            (self.isolation_decision_fingerprint, "isolation decision fingerprint"),
            (self.evidence_policy_fingerprint, "evidence policy fingerprint"),
            (self.topology_fingerprint, "topology fingerprint"),
            (self.writer_attestation_fingerprint, "writer attestation fingerprint"),
            (
                self.authority_public_key_fingerprint,
                "authority public key fingerprint",
            ),
        ):
            _fingerprint(value, label)
        _positive_int(self.lane_id, "lane_id")
        _optional_fingerprint(
            self.isolation_grant_fingerprint,
            "isolation grant fingerprint",
        )
        claims = tuple(sorted(self.claims, key=lambda item: item.dimension))
        required = {
            "workspace_root",
            "runtime_root",
            "browser_profile",
            "endpoint_namespace",
            "evidence_directory",
        }
        if {item.dimension for item in claims} != required:
            raise ValueError("lane materialization attestation is missing core claims")
        object.__setattr__(self, "claims", claims)
        _utc_datetime(self.recorded_at, "recorded_at")
        for value, label in (
            (self.topology_json, "topology_json"),
            (self.writer_attestation_json, "writer_attestation_json"),
        ):
            if not isinstance(value, str) or len(value) > 65_536:
                raise ValueError(f"lane attestation {label} must be bounded JSON")
            parsed = json.loads(value)
            if not isinstance(parsed, Mapping) or json.dumps(
                parsed,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ) != value:
                raise ValueError(f"lane attestation {label} must be canonical JSON")
        if not re.fullmatch(r"[0-9a-f]{128}", self.authority_signature):
            raise ValueError("lane attestation authority signature is invalid")
        if self.attestation_fingerprint != stable_control_fingerprint(self._payload()):
            raise ValueError("lane materialization attestation fingerprint mismatch")

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "measurement_plan_fingerprint": self.measurement_plan_fingerprint,
            "isolation_decision_fingerprint": self.isolation_decision_fingerprint,
            "evidence_policy_fingerprint": self.evidence_policy_fingerprint,
            "lane_id": self.lane_id,
            "isolation_grant_fingerprint": self.isolation_grant_fingerprint,
            "topology_fingerprint": self.topology_fingerprint,
            "topology_json": self.topology_json,
            "writer_attestation_fingerprint": self.writer_attestation_fingerprint,
            "writer_attestation_json": self.writer_attestation_json,
            "claims": [item.to_dict() for item in self.claims],
            "recorded_at": self.recorded_at,
            "authority_public_key_fingerprint": (
                self.authority_public_key_fingerprint
            ),
            "authority_signature": self.authority_signature,
        }

    def authority_payload(self) -> dict[str, object]:
        payload = self._payload()
        payload.pop("authority_signature")
        return payload

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "attestation_fingerprint": self.attestation_fingerprint}

    @classmethod
    def create(
        cls,
        *,
        measurement_plan_fingerprint: str,
        isolation_decision_fingerprint: str,
        evidence_policy_fingerprint: str,
        lane_id: int,
        isolation_grant_fingerprint: str | None,
        topology_fingerprint: str,
        topology: Mapping[str, object],
        writer_attestation_fingerprint: str,
        writer_attestation: Mapping[str, object],
        claims: Sequence[LaneMaterializationClaim],
        recorded_at: str,
        authority_public_key_fingerprint: str,
        authority_signature: str,
    ) -> "LaneMaterializationAttestationV1":
        topology_json = json.dumps(
            topology, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        writer_attestation_json = json.dumps(
            writer_attestation,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        payload = {
            "schema_version": LANE_MATERIALIZATION_ATTESTATION_SCHEMA_VERSION,
            "measurement_plan_fingerprint": measurement_plan_fingerprint,
            "isolation_decision_fingerprint": isolation_decision_fingerprint,
            "evidence_policy_fingerprint": evidence_policy_fingerprint,
            "lane_id": lane_id,
            "isolation_grant_fingerprint": isolation_grant_fingerprint,
            "topology_fingerprint": topology_fingerprint,
            "topology_json": topology_json,
            "writer_attestation_fingerprint": writer_attestation_fingerprint,
            "writer_attestation_json": writer_attestation_json,
            "claims": [
                item.to_dict()
                for item in sorted(claims, key=lambda item: item.dimension)
            ],
            "recorded_at": recorded_at,
            "authority_public_key_fingerprint": authority_public_key_fingerprint,
            "authority_signature": authority_signature,
        }
        return cls(
            attestation_fingerprint=stable_control_fingerprint(payload),
            measurement_plan_fingerprint=measurement_plan_fingerprint,
            isolation_decision_fingerprint=isolation_decision_fingerprint,
            evidence_policy_fingerprint=evidence_policy_fingerprint,
            lane_id=lane_id,
            isolation_grant_fingerprint=isolation_grant_fingerprint,
            topology_fingerprint=topology_fingerprint,
            topology_json=topology_json,
            writer_attestation_fingerprint=writer_attestation_fingerprint,
            writer_attestation_json=writer_attestation_json,
            claims=tuple(claims),
            recorded_at=recorded_at,
            authority_public_key_fingerprint=authority_public_key_fingerprint,
            authority_signature=authority_signature,
        )

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "LaneMaterializationAttestationV1":
        raw_claims = value.get("claims")
        if not isinstance(raw_claims, list):
            raise ValueError("lane materialization claims must be an array")
        return cls(
            attestation_fingerprint=_required_text(
                value.get("attestation_fingerprint"), "attestation_fingerprint"
            ),
            measurement_plan_fingerprint=_required_text(
                value.get("measurement_plan_fingerprint"),
                "measurement_plan_fingerprint",
            ),
            isolation_decision_fingerprint=_required_text(
                value.get("isolation_decision_fingerprint"),
                "isolation_decision_fingerprint",
            ),
            evidence_policy_fingerprint=_required_text(
                value.get("evidence_policy_fingerprint"),
                "evidence_policy_fingerprint",
            ),
            lane_id=_positive_int(value.get("lane_id"), "lane_id"),
            isolation_grant_fingerprint=_optional_text(
                value.get("isolation_grant_fingerprint")
            ),
            topology_fingerprint=_required_text(
                value.get("topology_fingerprint"), "topology_fingerprint"
            ),
            topology_json=_required_text(value.get("topology_json"), "topology_json"),
            writer_attestation_fingerprint=_required_text(
                value.get("writer_attestation_fingerprint"),
                "writer_attestation_fingerprint",
            ),
            writer_attestation_json=_required_text(
                value.get("writer_attestation_json"), "writer_attestation_json"
            ),
            claims=tuple(
                LaneMaterializationClaim.from_dict(
                    _mapping(item, "lane materialization claim")
                )
                for item in raw_claims
            ),
            recorded_at=_required_text(value.get("recorded_at"), "recorded_at"),
            authority_public_key_fingerprint=_required_text(
                value.get("authority_public_key_fingerprint"),
                "authority_public_key_fingerprint",
            ),
            authority_signature=_required_text(
                value.get("authority_signature"), "authority_signature"
            ),
            schema_version=_required_text(value.get("schema_version"), "schema_version"),
        )


@dataclass(frozen=True)
class MeasurementControlObservationRecord:
    """Immutable, content-addressed terminal result for exactly one work unit."""

    observation_fingerprint: str
    measurement_plan_fingerprint: str
    work_unit_id: str
    experiment_id: str
    case_id: str
    arm: MeasurementArm
    repetition_id: int
    terminal_state: MeasurementWorkUnitState
    result_fingerprint: str
    isolation_grant_fingerprint: str | None
    lane_materialization_fingerprint: str
    recorded_at: str
    schema_version: str = MEASUREMENT_CONTROL_OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEASUREMENT_CONTROL_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("unsupported measurement-control observation schema")
        _fingerprint(self.observation_fingerprint, "observation fingerprint")
        _fingerprint(
            self.measurement_plan_fingerprint, "measurement plan fingerprint"
        )
        if not _WORK_UNIT_ID_RE.fullmatch(self.work_unit_id):
            raise ValueError("invalid measurement work-unit id")
        _safe_id(self.experiment_id, "experiment_id")
        _safe_id(self.case_id, "case_id")
        object.__setattr__(self, "arm", MeasurementArm(self.arm))
        _positive_int(self.repetition_id, "repetition_id")
        object.__setattr__(
            self, "terminal_state", MeasurementWorkUnitState(self.terminal_state)
        )
        if not self.terminal_state.terminal:
            raise ValueError("observation state must be terminal")
        _fingerprint(self.result_fingerprint, "result fingerprint")
        _optional_fingerprint(
            self.isolation_grant_fingerprint,
            "isolation grant fingerprint",
        )
        _fingerprint(
            self.lane_materialization_fingerprint,
            "lane materialization fingerprint",
        )
        _utc_datetime(self.recorded_at, "recorded_at")
        if self.observation_fingerprint != stable_control_fingerprint(self._payload()):
            raise ValueError("observation fingerprint does not match immutable payload")

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "measurement_plan_fingerprint": self.measurement_plan_fingerprint,
            "work_unit_id": self.work_unit_id,
            "experiment_id": self.experiment_id,
            "case_id": self.case_id,
            "arm": self.arm.value,
            "repetition_id": self.repetition_id,
            "terminal_state": self.terminal_state.value,
            "result_fingerprint": self.result_fingerprint,
            "isolation_grant_fingerprint": self.isolation_grant_fingerprint,
            "lane_materialization_fingerprint": (
                self.lane_materialization_fingerprint
            ),
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def create(
        cls,
        *,
        plan: "MeasurementPlanV2",
        work_unit_id: str,
        terminal_state: MeasurementWorkUnitState | str,
        result_fingerprint: str,
        isolation_grant_fingerprint: str | None,
        lane_materialization_fingerprint: str,
        recorded_at: str,
    ) -> "MeasurementControlObservationRecord":
        unit = next(
            (item for item in plan.work_units if item.work_unit_id == work_unit_id),
            None,
        )
        if unit is None:
            raise ValueError("observation references an unknown work unit")
        state = MeasurementWorkUnitState(terminal_state)
        payload = {
            "schema_version": MEASUREMENT_CONTROL_OBSERVATION_SCHEMA_VERSION,
            "measurement_plan_fingerprint": plan.measurement_plan_fingerprint,
            "work_unit_id": unit.work_unit_id,
            "experiment_id": unit.experiment_id,
            "case_id": unit.case_id,
            "arm": unit.arm.value,
            "repetition_id": unit.repetition_id,
            "terminal_state": state.value,
            "result_fingerprint": result_fingerprint,
            "isolation_grant_fingerprint": isolation_grant_fingerprint,
            "lane_materialization_fingerprint": (
                lane_materialization_fingerprint
            ),
            "recorded_at": recorded_at,
        }
        return cls(
            observation_fingerprint=stable_control_fingerprint(payload),
            measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
            work_unit_id=unit.work_unit_id,
            experiment_id=unit.experiment_id,
            case_id=unit.case_id,
            arm=unit.arm,
            repetition_id=unit.repetition_id,
            terminal_state=state,
            result_fingerprint=result_fingerprint,
            isolation_grant_fingerprint=isolation_grant_fingerprint,
            lane_materialization_fingerprint=lane_materialization_fingerprint,
            recorded_at=recorded_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "observation_fingerprint": self.observation_fingerprint}

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "MeasurementControlObservationRecord":
        _require_schema(
            value,
            MEASUREMENT_CONTROL_OBSERVATION_SCHEMA_VERSION,
            "measurement-control observation",
        )
        return cls(
            observation_fingerprint=_required_text(
                value.get("observation_fingerprint"), "observation_fingerprint"
            ),
            measurement_plan_fingerprint=_required_text(
                value.get("measurement_plan_fingerprint"),
                "measurement_plan_fingerprint",
            ),
            work_unit_id=_required_text(value.get("work_unit_id"), "work_unit_id"),
            experiment_id=_required_text(value.get("experiment_id"), "experiment_id"),
            case_id=_required_text(value.get("case_id"), "case_id"),
            arm=MeasurementArm(str(value.get("arm"))),
            repetition_id=_positive_int(value.get("repetition_id"), "repetition_id"),
            terminal_state=MeasurementWorkUnitState(str(value.get("terminal_state"))),
            result_fingerprint=_required_text(
                value.get("result_fingerprint"), "result_fingerprint"
            ),
            isolation_grant_fingerprint=_optional_text(
                value.get("isolation_grant_fingerprint")
            ),
            lane_materialization_fingerprint=_required_text(
                value.get("lane_materialization_fingerprint"),
                "lane_materialization_fingerprint",
            ),
            recorded_at=_required_text(value.get("recorded_at"), "recorded_at"),
        )


@dataclass(frozen=True)
class FinalizedAttemptRecord:
    attempt_id: str
    finalized_event_id: str
    final_state: MeasurementWorkUnitState
    cost_seconds: float
    started_at: str
    finalized_at: str

    def __post_init__(self) -> None:
        _safe_id(self.attempt_id, "attempt_id")
        if not re.fullmatch(r"measurement-event-[0-9a-f]{32}", self.finalized_event_id):
            raise ValueError("invalid finalized journal event id")
        object.__setattr__(self, "final_state", MeasurementWorkUnitState(self.final_state))
        if self.final_state in {
            MeasurementWorkUnitState.LEASED,
            MeasurementWorkUnitState.RUNNING,
            MeasurementWorkUnitState.PENDING,
        }:
            raise ValueError("finalized attempt must end at checkpoint or terminal")
        object.__setattr__(
            self, "cost_seconds", _non_negative_number(self.cost_seconds, "cost_seconds")
        )
        started = _utc_datetime(self.started_at, "started_at")
        finalized = _utc_datetime(self.finalized_at, "finalized_at")
        if finalized < started:
            raise ValueError("attempt finalized before it started")

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "finalized_event_id": self.finalized_event_id,
            "final_state": self.final_state.value,
            "cost_seconds": self.cost_seconds,
            "started_at": self.started_at,
            "finalized_at": self.finalized_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FinalizedAttemptRecord":
        return cls(
            attempt_id=_required_text(value.get("attempt_id"), "attempt_id"),
            finalized_event_id=_required_text(
                value.get("finalized_event_id"), "finalized_event_id"
            ),
            final_state=MeasurementWorkUnitState(str(value.get("final_state"))),
            cost_seconds=_number(value.get("cost_seconds"), "cost_seconds"),
            started_at=_required_text(value.get("started_at"), "started_at"),
            finalized_at=_required_text(value.get("finalized_at"), "finalized_at"),
        )


@dataclass(frozen=True)
class MeasurementWorkUnitIndexEntry:
    work_unit_id: str
    state: MeasurementWorkUnitState = MeasurementWorkUnitState.PENDING
    last_event_id: str | None = None
    last_occurred_at: str | None = None
    attempt_count: int = 0
    active_attempt_id: str | None = None
    active_attempt_started_at: str | None = None
    lease_expires_at: str | None = None
    observation_fingerprint: str | None = None
    actual_attempt_cost_seconds: float = 0.0
    finalized_attempts: tuple[FinalizedAttemptRecord, ...] = ()

    def __post_init__(self) -> None:
        if not _WORK_UNIT_ID_RE.fullmatch(self.work_unit_id):
            raise ValueError("invalid measurement work-unit id")
        object.__setattr__(self, "state", MeasurementWorkUnitState(self.state))
        if self.last_event_id is not None and not re.fullmatch(
            r"measurement-event-[0-9a-f]{32}", self.last_event_id
        ):
            raise ValueError("invalid last journal event id")
        if self.last_occurred_at is not None:
            _utc_datetime(self.last_occurred_at, "last_occurred_at")
        _non_negative_int(self.attempt_count, "attempt_count")
        if self.active_attempt_id is not None:
            _safe_id(self.active_attempt_id, "active_attempt_id")
        if self.active_attempt_started_at is not None:
            _utc_datetime(self.active_attempt_started_at, "active_attempt_started_at")
        if self.lease_expires_at is not None:
            _utc_datetime(self.lease_expires_at, "lease_expires_at")
        _optional_fingerprint(
            self.observation_fingerprint, "observation fingerprint"
        )
        object.__setattr__(
            self,
            "actual_attempt_cost_seconds",
            _non_negative_number(
                self.actual_attempt_cost_seconds,
                "actual_attempt_cost_seconds",
            ),
        )
        finalized = tuple(self.finalized_attempts)
        if len(finalized) != len({item.attempt_id for item in finalized}):
            raise ValueError("finalized attempt ids must be unique")
        if self.active_attempt_id in {item.attempt_id for item in finalized}:
            raise ValueError("active attempt was already finalized")
        if len(finalized) != self.attempt_count:
            active_count = 1 if self.active_attempt_id is not None else 0
            if len(finalized) + active_count != self.attempt_count:
                raise ValueError("attempt ledger does not match attempt count")
        if not math.isclose(
            sum(item.cost_seconds for item in finalized),
            self.actual_attempt_cost_seconds,
        ):
            raise ValueError("attempt ledger cost does not match work-unit cost")
        object.__setattr__(self, "finalized_attempts", finalized)
        if self.state.terminal and self.observation_fingerprint is None:
            raise ValueError("terminal index entry requires an observation")
        if self.state in {
            MeasurementWorkUnitState.LEASED,
            MeasurementWorkUnitState.RUNNING,
        } and (self.active_attempt_id is None or self.lease_expires_at is None):
            raise ValueError("active index entry requires attempt and lease")
        if self.active_attempt_id is not None and self.active_attempt_started_at is None:
            raise ValueError("active attempt requires a start timestamp")

    def to_dict(self) -> dict[str, object]:
        return {
            "work_unit_id": self.work_unit_id,
            "state": self.state.value,
            "last_event_id": self.last_event_id,
            "last_occurred_at": self.last_occurred_at,
            "attempt_count": self.attempt_count,
            "active_attempt_id": self.active_attempt_id,
            "active_attempt_started_at": self.active_attempt_started_at,
            "lease_expires_at": self.lease_expires_at,
            "observation_fingerprint": self.observation_fingerprint,
            "actual_attempt_cost_seconds": self.actual_attempt_cost_seconds,
            "finalized_attempts": [item.to_dict() for item in self.finalized_attempts],
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "MeasurementWorkUnitIndexEntry":
        return cls(
            work_unit_id=_required_text(value.get("work_unit_id"), "work_unit_id"),
            state=MeasurementWorkUnitState(str(value.get("state"))),
            last_event_id=_optional_text(value.get("last_event_id")),
            last_occurred_at=_optional_text(value.get("last_occurred_at")),
            attempt_count=_non_negative_int(
                value.get("attempt_count", 0), "attempt_count"
            ),
            active_attempt_id=_optional_text(value.get("active_attempt_id")),
            active_attempt_started_at=_optional_text(
                value.get("active_attempt_started_at")
            ),
            lease_expires_at=_optional_text(value.get("lease_expires_at")),
            observation_fingerprint=_optional_text(
                value.get("observation_fingerprint")
            ),
            actual_attempt_cost_seconds=_number(
                value.get("actual_attempt_cost_seconds", 0),
                "actual_attempt_cost_seconds",
            ),
            finalized_attempts=tuple(
                FinalizedAttemptRecord.from_dict(_mapping(item, "finalized attempt"))
                for item in _sequence(value.get("finalized_attempts", ()), "finalized_attempts")
            ),
        )


@dataclass(frozen=True)
class MeasurementControlIndex:
    measurement_plan_fingerprint: str
    work_units: tuple[MeasurementWorkUnitIndexEntry, ...]
    event_count: int
    observation_count: int
    actual_attempt_cost_seconds: float
    journal_byte_count: int
    journal_sha256: str
    journal_file: str = "journal.jsonl"
    compacted_event_count: int = 0
    snapshot_fingerprint: str | None = None
    schema_version: str = MEASUREMENT_CONTROL_INDEX_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEASUREMENT_CONTROL_INDEX_SCHEMA_VERSION:
            raise ValueError("unsupported measurement-control index schema")
        _fingerprint(
            self.measurement_plan_fingerprint, "measurement plan fingerprint"
        )
        entries = tuple(self.work_units)
        if len(entries) != len({entry.work_unit_id for entry in entries}):
            raise ValueError("measurement-control index work units must be unique")
        object.__setattr__(self, "work_units", entries)
        _non_negative_int(self.event_count, "event_count")
        _non_negative_int(self.observation_count, "observation_count")
        _non_negative_int(self.journal_byte_count, "journal_byte_count")
        _non_negative_int(self.compacted_event_count, "compacted_event_count")
        if self.compacted_event_count > self.event_count:
            raise ValueError("compacted event count exceeds total event count")
        _optional_fingerprint(self.snapshot_fingerprint, "snapshot fingerprint")
        if (self.compacted_event_count == 0) != (self.snapshot_fingerprint is None):
            raise ValueError("compacted index requires exactly one verified snapshot")
        object.__setattr__(
            self,
            "actual_attempt_cost_seconds",
            _non_negative_number(
                self.actual_attempt_cost_seconds,
                "actual_attempt_cost_seconds",
            ),
        )
        _fingerprint(self.journal_sha256, "journal_sha256")
        if not re.fullmatch(r"journal(?:-[0-9a-f]{64})?\.jsonl", self.journal_file):
            raise ValueError("invalid measurement journal file identity")
        terminal_count = sum(entry.state.terminal for entry in entries)
        if terminal_count != self.observation_count:
            raise ValueError("index observation count does not match terminal units")
        if not math.isclose(
            sum(entry.actual_attempt_cost_seconds for entry in entries),
            self.actual_attempt_cost_seconds,
        ):
            raise ValueError("index attempt cost does not match work-unit costs")

    @property
    def index_fingerprint(self) -> str:
        return stable_control_fingerprint(self._payload())

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "measurement_plan_fingerprint": self.measurement_plan_fingerprint,
            "work_units": [entry.to_dict() for entry in self.work_units],
            "event_count": self.event_count,
            "observation_count": self.observation_count,
            "actual_attempt_cost_seconds": self.actual_attempt_cost_seconds,
            "journal_byte_count": self.journal_byte_count,
            "journal_sha256": self.journal_sha256,
            "journal_file": self.journal_file,
            "compacted_event_count": self.compacted_event_count,
            "snapshot_fingerprint": self.snapshot_fingerprint,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "index_fingerprint": self.index_fingerprint}

    def entry(self, work_unit_id: str) -> MeasurementWorkUnitIndexEntry:
        for entry in self.work_units:
            if entry.work_unit_id == work_unit_id:
                return entry
        raise KeyError(work_unit_id)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementControlIndex":
        _require_schema(
            value, MEASUREMENT_CONTROL_INDEX_SCHEMA_VERSION, "measurement-control index"
        )
        loaded = cls(
            measurement_plan_fingerprint=_required_text(
                value.get("measurement_plan_fingerprint"),
                "measurement_plan_fingerprint",
            ),
            work_units=tuple(
                MeasurementWorkUnitIndexEntry.from_dict(
                    _mapping(item, "work-unit index entry")
                )
                for item in _sequence(value.get("work_units"), "work_units")
            ),
            event_count=_non_negative_int(value.get("event_count"), "event_count"),
            observation_count=_non_negative_int(
                value.get("observation_count"), "observation_count"
            ),
            actual_attempt_cost_seconds=_number(
                value.get("actual_attempt_cost_seconds"),
                "actual_attempt_cost_seconds",
            ),
            journal_byte_count=_non_negative_int(
                value.get("journal_byte_count"), "journal_byte_count"
            ),
            journal_sha256=_required_text(
                value.get("journal_sha256"), "journal_sha256"
            ),
            journal_file=_required_text(
                value.get("journal_file", "journal.jsonl"), "journal_file"
            ),
            compacted_event_count=_non_negative_int(
                value.get("compacted_event_count", 0), "compacted_event_count"
            ),
            snapshot_fingerprint=_optional_text(value.get("snapshot_fingerprint")),
        )
        if value.get("index_fingerprint") != loaded.index_fingerprint:
            raise ValueError("measurement-control index fingerprint mismatch")
        return loaded


@dataclass(frozen=True)
class WorkUnitReuseCompatibility:
    compatible: bool
    work_unit_id: str
    reason_code: str
    observation_fingerprint: str | None = None
    expected_work_unit_id: str | None = None
    baseline_compatibility_fingerprint: str | None = None


@dataclass(frozen=True)
class MeasurementControlSnapshot:
    snapshot_fingerprint: str
    measurement_plan_fingerprint: str
    compacted_event_count: int
    work_units: tuple[MeasurementWorkUnitIndexEntry, ...]
    observation_count: int
    actual_attempt_cost_seconds: float
    schema_version: str = MEASUREMENT_CONTROL_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEASUREMENT_CONTROL_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported measurement-control snapshot schema")
        _fingerprint(self.snapshot_fingerprint, "snapshot fingerprint")
        _fingerprint(self.measurement_plan_fingerprint, "measurement plan fingerprint")
        _positive_int(self.compacted_event_count, "compacted_event_count")
        entries = tuple(self.work_units)
        if len(entries) != len({entry.work_unit_id for entry in entries}):
            raise ValueError("snapshot work units must be unique")
        object.__setattr__(self, "work_units", entries)
        _non_negative_int(self.observation_count, "observation_count")
        object.__setattr__(
            self,
            "actual_attempt_cost_seconds",
            _non_negative_number(
                self.actual_attempt_cost_seconds, "actual_attempt_cost_seconds"
            ),
        )
        if self.snapshot_fingerprint != stable_control_fingerprint(self._payload()):
            raise ValueError("measurement-control snapshot fingerprint mismatch")

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "measurement_plan_fingerprint": self.measurement_plan_fingerprint,
            "compacted_event_count": self.compacted_event_count,
            "work_units": [entry.to_dict() for entry in self.work_units],
            "observation_count": self.observation_count,
            "actual_attempt_cost_seconds": self.actual_attempt_cost_seconds,
        }

    @classmethod
    def create(cls, index: MeasurementControlIndex) -> "MeasurementControlSnapshot":
        if index.event_count <= 0:
            raise ValueError("cannot compact an empty journal")
        payload = {
            "schema_version": MEASUREMENT_CONTROL_SNAPSHOT_SCHEMA_VERSION,
            "measurement_plan_fingerprint": index.measurement_plan_fingerprint,
            "compacted_event_count": index.event_count,
            "work_units": [entry.to_dict() for entry in index.work_units],
            "observation_count": index.observation_count,
            "actual_attempt_cost_seconds": index.actual_attempt_cost_seconds,
        }
        return cls(
            snapshot_fingerprint=stable_control_fingerprint(payload),
            measurement_plan_fingerprint=index.measurement_plan_fingerprint,
            compacted_event_count=index.event_count,
            work_units=index.work_units,
            observation_count=index.observation_count,
            actual_attempt_cost_seconds=index.actual_attempt_cost_seconds,
        )

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "snapshot_fingerprint": self.snapshot_fingerprint}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementControlSnapshot":
        _require_schema(
            value, MEASUREMENT_CONTROL_SNAPSHOT_SCHEMA_VERSION, "measurement snapshot"
        )
        return cls(
            snapshot_fingerprint=_required_text(
                value.get("snapshot_fingerprint"), "snapshot_fingerprint"
            ),
            measurement_plan_fingerprint=_required_text(
                value.get("measurement_plan_fingerprint"),
                "measurement_plan_fingerprint",
            ),
            compacted_event_count=_positive_int(
                value.get("compacted_event_count"), "compacted_event_count"
            ),
            work_units=tuple(
                MeasurementWorkUnitIndexEntry.from_dict(
                    _mapping(item, "work-unit index entry")
                )
                for item in _sequence(value.get("work_units"), "work_units")
            ),
            observation_count=_non_negative_int(
                value.get("observation_count"), "observation_count"
            ),
            actual_attempt_cost_seconds=_number(
                value.get("actual_attempt_cost_seconds"),
                "actual_attempt_cost_seconds",
            ),
        )


def initial_measurement_control_index(
    plan: "MeasurementPlanV2",
) -> MeasurementControlIndex:
    return MeasurementControlIndex(
        measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
        work_units=tuple(
            MeasurementWorkUnitIndexEntry(work_unit_id=unit.work_unit_id)
            for unit in plan.work_units
        ),
        event_count=0,
        observation_count=0,
        actual_attempt_cost_seconds=0.0,
        journal_byte_count=0,
        journal_sha256=_bytes_fingerprint(b""),
        journal_file="journal.jsonl",
    )


def rebuild_measurement_control_index(
    plan: "MeasurementPlanV2",
    events: Sequence[WorkUnitJournalEvent],
    *,
    journal_bytes: bytes,
    base_index: MeasurementControlIndex | None = None,
) -> MeasurementControlIndex:
    current_index = base_index or initial_measurement_control_index(plan)
    if current_index.measurement_plan_fingerprint != plan.measurement_plan_fingerprint:
        raise MeasurementControlCorruptionError(
            "measurement_snapshot_plan_identity_mismatch",
            "snapshot references a different measurement plan",
        )
    seen_events: set[str] = set()
    for event in events:
        if event.event_id in seen_events:
            raise MeasurementControlCorruptionError(
                "measurement_journal_duplicate_event",
                "measurement journal contains a duplicate event id",
            )
        seen_events.add(event.event_id)
        current_index = advance_measurement_control_index(
            plan,
            current_index,
            event,
            journal_byte_count=0,
            journal_sha256=_bytes_fingerprint(b""),
        )
    return MeasurementControlIndex(
        measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
        work_units=current_index.work_units,
        event_count=current_index.event_count,
        observation_count=current_index.observation_count,
        actual_attempt_cost_seconds=current_index.actual_attempt_cost_seconds,
        journal_byte_count=len(journal_bytes),
        journal_sha256=measurement_control_bytes_fingerprint(journal_bytes),
        journal_file=current_index.journal_file,
        compacted_event_count=current_index.compacted_event_count,
        snapshot_fingerprint=current_index.snapshot_fingerprint,
    )


def advance_measurement_control_index(
    plan: "MeasurementPlanV2",
    index: MeasurementControlIndex,
    event: WorkUnitJournalEvent,
    *,
    journal_byte_count: int,
    journal_sha256: str,
) -> MeasurementControlIndex:
    """Apply one event to a verified canonical index without replaying its prefix."""

    if event.measurement_plan_fingerprint != plan.measurement_plan_fingerprint:
        raise MeasurementControlCorruptionError(
            "measurement_journal_plan_identity_mismatch",
            "journal event references a different measurement plan",
        )
    entries = {entry.work_unit_id: entry for entry in index.work_units}
    current = entries.get(event.work_unit_id)
    if current is None:
        raise MeasurementControlCorruptionError(
            "measurement_journal_unknown_work_unit",
            "journal event references an unknown work unit",
        )
    known_event_ids = {
        item.finalized_event_id
        for entry in index.work_units
        for item in entry.finalized_attempts
    } | {entry.last_event_id for entry in index.work_units if entry.last_event_id}
    if event.event_id in known_event_ids:
        raise MeasurementControlCorruptionError(
            "measurement_journal_duplicate_event",
            "measurement journal contains a duplicate event id",
        )
    if (
        current.state.terminal
        and event.kind is not MeasurementControlEventKind.RETRY_SCHEDULED
    ):
        raise MeasurementControlCorruptionError(
            "measurement_journal_terminal_transition",
            "journal attempts to transition an already terminal work unit",
        )
    if event.previous_state is not current.state:
        raise MeasurementControlCorruptionError(
            "measurement_journal_state_mismatch",
            "journal event previous state does not match canonical index",
        )
    if current.last_occurred_at is not None and _utc_datetime(
        event.occurred_at, "occurred_at"
    ) < _utc_datetime(current.last_occurred_at, "last_occurred_at"):
        raise MeasurementControlCorruptionError(
            "measurement_journal_time_regression",
            "journal event time regressed for one work unit",
        )
    finalized_ids = {
        item.attempt_id
        for entry in index.work_units
        for item in entry.finalized_attempts
    }
    active_ids = {
        entry.active_attempt_id
        for entry in index.work_units
        if entry.active_attempt_id is not None
    }
    if event.kind is MeasurementControlEventKind.LEASE_ACQUIRED:
        if event.attempt_id in finalized_ids or event.attempt_id in active_ids:
            raise MeasurementControlCorruptionError(
                "measurement_attempt_already_finalized",
                "attempt id cannot be leased or charged twice",
            )
        attempt_count = current.attempt_count + 1
        active_attempt = event.attempt_id
        active_started = event.occurred_at
    elif event.kind is MeasurementControlEventKind.RETRY_SCHEDULED:
        attempt_count = current.attempt_count
        active_attempt = None
        active_started = None
    else:
        attempt_count = current.attempt_count
        active_attempt = current.active_attempt_id
        active_started = current.active_attempt_started_at
        if active_attempt != event.attempt_id:
            raise MeasurementControlCorruptionError(
                "measurement_journal_attempt_mismatch",
                "journal event does not belong to the active attempt",
            )
    active = event.new_state in {
        MeasurementWorkUnitState.LEASED,
        MeasurementWorkUnitState.RUNNING,
    }
    finalized = current.finalized_attempts
    if not active and event.kind is not MeasurementControlEventKind.RETRY_SCHEDULED:
        if active_started is None:
            raise MeasurementControlCorruptionError(
                "measurement_attempt_start_missing",
                "finalized attempt has no immutable start time",
            )
        finalized = (*finalized, FinalizedAttemptRecord(
            attempt_id=event.attempt_id,
            finalized_event_id=event.event_id,
            final_state=event.new_state,
            cost_seconds=event.attempt_cost_seconds,
            started_at=active_started,
            finalized_at=event.occurred_at,
        ))
    entries[event.work_unit_id] = MeasurementWorkUnitIndexEntry(
        work_unit_id=event.work_unit_id,
        state=event.new_state,
        last_event_id=event.event_id,
        last_occurred_at=event.occurred_at,
        attempt_count=attempt_count,
        active_attempt_id=active_attempt if active else None,
        active_attempt_started_at=active_started if active else None,
        lease_expires_at=event.lease_expires_at if active else None,
        observation_fingerprint=event.observation_fingerprint,
        actual_attempt_cost_seconds=sum(item.cost_seconds for item in finalized),
        finalized_attempts=finalized,
    )
    ordered = tuple(entries[unit.work_unit_id] for unit in plan.work_units)
    return MeasurementControlIndex(
        measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
        work_units=ordered,
        event_count=index.event_count + 1,
        observation_count=sum(entry.state.terminal for entry in ordered),
        actual_attempt_cost_seconds=sum(
            entry.actual_attempt_cost_seconds for entry in ordered
        ),
        journal_byte_count=journal_byte_count,
        journal_sha256=journal_sha256,
        journal_file=index.journal_file,
        compacted_event_count=index.compacted_event_count,
        snapshot_fingerprint=index.snapshot_fingerprint,
    )


def classify_work_unit_reuse(
    *,
    expected_plan: "MeasurementPlanV2",
    stored_plan: "MeasurementPlanV2",
    stored_entry: MeasurementWorkUnitIndexEntry,
) -> WorkUnitReuseCompatibility:
    stored_unit = next(
        (
            unit
            for unit in stored_plan.work_units
            if unit.work_unit_id == stored_entry.work_unit_id
        ),
        None,
    )
    if stored_unit is None:
        return WorkUnitReuseCompatibility(
            compatible=False,
            work_unit_id=stored_entry.work_unit_id,
            reason_code="stored_index_unit_missing_from_plan",
        )
    expected_unit = next(
        (
            unit
            for unit in expected_plan.work_units
            if (
                unit.case_id,
                unit.arm,
                unit.repetition_id,
            )
            == (
                stored_unit.case_id,
                stored_unit.arm,
                stored_unit.repetition_id,
            )
        ),
        None,
    )
    if expected_unit is None:
        return WorkUnitReuseCompatibility(
            compatible=False,
            work_unit_id=stored_entry.work_unit_id,
            reason_code="work_unit_not_in_expected_plan",
        )
    cross_experiment_baseline = (
        stored_unit.arm is MeasurementArm.CONTROL
        and expected_unit.arm is MeasurementArm.CONTROL
        and stored_unit.experiment_id != expected_unit.experiment_id
    )
    if cross_experiment_baseline:
        stored_key = stored_unit.baseline_compatibility_key
        expected_key = expected_unit.baseline_compatibility_key
        if stored_key is None or stored_key != expected_key:
            return WorkUnitReuseCompatibility(
                compatible=False,
                work_unit_id=stored_unit.work_unit_id,
                expected_work_unit_id=expected_unit.work_unit_id,
                reason_code="baseline_compatibility_key_changed",
            )
        identity_decision = WorkUnitReuseCompatibility(
            compatible=True,
            work_unit_id=stored_unit.work_unit_id,
            expected_work_unit_id=expected_unit.work_unit_id,
            baseline_compatibility_fingerprint=stored_key.fingerprint,
            reason_code="compatible_cross_experiment_baseline",
        )
    else:
        identity_decision = classify_measurement_work_unit_compatibility(
            expected_unit=expected_unit,
            stored_unit=stored_unit,
        )
    if not identity_decision.compatible:
        return identity_decision
    if not stored_entry.state.terminal:
        return WorkUnitReuseCompatibility(
            compatible=False,
            work_unit_id=stored_entry.work_unit_id,
            reason_code="work_unit_not_terminal",
        )
    return WorkUnitReuseCompatibility(
        compatible=True,
        work_unit_id=stored_entry.work_unit_id,
        expected_work_unit_id=expected_unit.work_unit_id,
        reason_code="compatible_terminal_observation",
        observation_fingerprint=stored_entry.observation_fingerprint,
        baseline_compatibility_fingerprint=(
            identity_decision.baseline_compatibility_fingerprint
        ),
    )


def classify_measurement_work_unit_compatibility(
    *,
    expected_unit: MeasurementWorkUnitV1,
    stored_unit: MeasurementWorkUnitV1,
) -> WorkUnitReuseCompatibility:
    """Compare every execution-bearing identity for one immutable unit."""

    fields = (
        ("experiment_id", "experiment_id_changed"),
        ("arm", "arm_changed"),
        ("artifact_fingerprint", "artifact_fingerprint_changed"),
        (
            "pairing_control_fingerprint",
            "pairing_control_fingerprint_changed",
        ),
        ("dataset_fingerprint", "dataset_fingerprint_changed"),
        ("case_id", "case_id_changed"),
        ("repetition_id", "repetition_id_changed"),
        (
            "execution_contract_fingerprint",
            "execution_contract_fingerprint_changed",
        ),
        ("evidence_policy_fingerprint", "evidence_policy_fingerprint_changed"),
        (
            "sampling_contract_fingerprint",
            "sampling_contract_fingerprint_changed",
        ),
        (
            "isolation_decision_fingerprint",
            "isolation_decision_fingerprint_changed",
        ),
        ("depends_on_work_unit_id", "pairing_dependency_changed"),
    )
    for field_name, reason_code in fields:
        if getattr(expected_unit, field_name) != getattr(stored_unit, field_name):
            return WorkUnitReuseCompatibility(
                compatible=False,
                work_unit_id=stored_unit.work_unit_id,
                reason_code=reason_code,
            )
    if expected_unit.work_unit_id != stored_unit.work_unit_id:
        return WorkUnitReuseCompatibility(
            compatible=False,
            work_unit_id=stored_unit.work_unit_id,
            reason_code="work_unit_identity_changed",
        )
    return WorkUnitReuseCompatibility(
        compatible=True,
        work_unit_id=stored_unit.work_unit_id,
        reason_code="compatible_work_unit_identity",
    )


@dataclass(frozen=True)
class MeasurementPlanV2:
    experiment_id: str
    plan_revision: int
    candidate_fingerprint: str
    control_fingerprint: str
    dataset_fingerprint: str
    execution_contract_fingerprint: str
    evidence_policy_fingerprint: str
    isolation_decision_fingerprint: str
    stages: tuple[SamplingStage, ...]
    repetitions_per_case: int
    deadlines: DeadlinePolicy
    isolation_requirement: IsolationRequirement
    isolation_summary: IsolationSummary
    decision_policy: AdaptiveMeasurementPolicy
    estimator_version: str
    decision_policy_version: str
    measurement_plan_fingerprint: str
    work_units: tuple[MeasurementWorkUnitV1, ...]
    schema_version: str = MEASUREMENT_PLAN_SCHEMA_VERSION
    _contract_seal: InitVar[object] = None

    def __post_init__(self, _contract_seal: object) -> None:
        if self.schema_version != MEASUREMENT_PLAN_SCHEMA_VERSION:
            raise ValueError("unsupported measurement plan schema")
        if _contract_seal is not _MEASUREMENT_PLAN_CONTRACT_SEAL:
            raise ValueError(
                "measurement plan must be created from canonical contract artifacts"
            )
        _safe_id(self.experiment_id, "experiment_id")
        _positive_int(self.plan_revision, "plan_revision")
        for name in (
            "candidate_fingerprint",
            "control_fingerprint",
            "dataset_fingerprint",
            "execution_contract_fingerprint",
            "evidence_policy_fingerprint",
            "isolation_decision_fingerprint",
            "measurement_plan_fingerprint",
        ):
            _fingerprint(getattr(self, name), name)
        _safe_id(self.estimator_version, "estimator_version")
        _safe_id(self.decision_policy_version, "decision_policy_version")
        stages = tuple(self.stages)
        if not stages or stages[0].kind is not SamplingStageKind.SENTINEL:
            raise ValueError("measurement plan must begin with a sentinel stage")
        if len({stage.stage_id for stage in stages}) != len(stages):
            raise ValueError("sampling stage ids must be unique")
        all_cases = tuple(case_id for stage in stages for case_id in stage.case_ids)
        if len(all_cases) != len(set(all_cases)):
            raise ValueError("a case may have only one frozen sampling stage")
        object.__setattr__(self, "stages", stages)
        canonical_plan_payload = {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "plan_revision": self.plan_revision,
            "candidate_fingerprint": self.candidate_fingerprint,
            "control_fingerprint": self.control_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "execution_contract_fingerprint": self.execution_contract_fingerprint,
            "evidence_policy_fingerprint": self.evidence_policy_fingerprint,
            "isolation_decision_fingerprint": self.isolation_decision_fingerprint,
            "stages": [stage.to_dict() for stage in stages],
            "repetitions_per_case": self.repetitions_per_case,
            "deadlines": self.deadlines.to_dict(),
            "isolation_requirement": self.isolation_requirement.to_dict(),
            "isolation_summary": self.isolation_summary.to_dict(),
            "decision_policy": self.decision_policy.to_dict(),
            "estimator_version": self.estimator_version,
            "decision_policy_version": self.decision_policy_version,
        }
        if self.measurement_plan_fingerprint != stable_control_fingerprint(
            canonical_plan_payload
        ):
            raise ValueError("measurement plan fingerprint does not match contract")
        _positive_int(self.repetitions_per_case, "repetitions_per_case")
        if (
            self.isolation_requirement.requested_lane_ceiling
            != self.isolation_summary.requested_lane_ceiling
        ):
            raise ValueError("isolation requirement and summary ceilings differ")
        units = tuple(self.work_units)
        expected_unit_count = len(all_cases) * self.repetitions_per_case * 2
        if len(units) != expected_unit_count:
            raise ValueError("measurement plan work-unit matrix is incomplete")
        if len(units) != len({unit.work_unit_id for unit in units}):
            raise ValueError("measurement work-unit ids must be unique")
        if any(
            unit.measurement_plan_fingerprint
            != self.measurement_plan_fingerprint
            for unit in units
        ):
            raise ValueError("work unit references a different measurement plan")
        unit_by_id = {unit.work_unit_id: unit for unit in units}
        stage_by_id = {stage.stage_id: stage for stage in stages}
        isolation_fingerprint = self.isolation_decision_fingerprint
        for unit in units:
            stage = stage_by_id.get(unit.stage_id)
            if stage is None or unit.case_id not in stage.case_ids:
                raise ValueError("work unit is outside its frozen sampling stage")
            expected_artifact = (
                self.control_fingerprint
                if unit.arm is MeasurementArm.CONTROL
                else self.candidate_fingerprint
            )
            if (
                unit.experiment_id != self.experiment_id
                or unit.artifact_fingerprint != expected_artifact
                or unit.pairing_control_fingerprint != self.control_fingerprint
                or unit.dataset_fingerprint != self.dataset_fingerprint
                or unit.execution_contract_fingerprint
                != self.execution_contract_fingerprint
                or unit.evidence_policy_fingerprint
                != self.evidence_policy_fingerprint
                or unit.sampling_contract_fingerprint
                != stage.contract_fingerprint
                or unit.isolation_decision_fingerprint != isolation_fingerprint
            ):
                raise ValueError("work unit identity does not match its plan contract")
            reconstructed = MeasurementWorkUnitV1.create(
                measurement_plan_fingerprint=self.measurement_plan_fingerprint,
                experiment_id=self.experiment_id,
                artifact_fingerprint=expected_artifact,
                pairing_control_fingerprint=self.control_fingerprint,
                dataset_fingerprint=self.dataset_fingerprint,
                case_id=unit.case_id,
                arm=unit.arm,
                repetition_id=unit.repetition_id,
                execution_contract_fingerprint=self.execution_contract_fingerprint,
                evidence_policy_fingerprint=self.evidence_policy_fingerprint,
                sampling_contract_fingerprint=stage.contract_fingerprint,
                isolation_decision_fingerprint=isolation_fingerprint,
                stage_id=stage.stage_id,
                depends_on_work_unit_id=unit.depends_on_work_unit_id,
            )
            if reconstructed != unit:
                raise ValueError("work-unit id does not match complete plan identity")
        for treatment in (
            unit for unit in units if unit.arm is MeasurementArm.TREATMENT
        ):
            control = unit_by_id.get(treatment.depends_on_work_unit_id or "")
            if control is None or control.arm is not MeasurementArm.CONTROL:
                raise ValueError("treatment dependency is not a planned control")
            if (
                control.case_id,
                control.repetition_id,
                control.stage_id,
            ) != (
                treatment.case_id,
                treatment.repetition_id,
                treatment.stage_id,
            ):
                raise ValueError("treatment dependency violates the pairing contract")
        expected_coordinates = {
            (case_id, arm, repetition_id)
            for case_id in all_cases
            for repetition_id in range(1, self.repetitions_per_case + 1)
            for arm in (MeasurementArm.CONTROL, MeasurementArm.TREATMENT)
        }
        actual_coordinates = {
            (unit.case_id, unit.arm, unit.repetition_id) for unit in units
        }
        if actual_coordinates != expected_coordinates:
            raise ValueError("measurement plan work-unit coordinates are incomplete")
        object.__setattr__(self, "work_units", units)

    @classmethod
    def create(
        cls,
        *,
        experiment_id: str,
        plan_revision: int,
        candidate_fingerprint: str,
        control_fingerprint: str,
        dataset_fingerprint: str,
        execution_contract_fingerprint: str,
        isolation_decision: "IsolationDecision",
        evidence_policy_profile: "EvidencePolicyProfileV2",
        stages: Sequence[SamplingStage],
        repetitions_per_case: int,
        deadlines: DeadlinePolicy,
        decision_policy: AdaptiveMeasurementPolicy,
        estimator_version: str,
        decision_policy_version: str,
    ) -> "MeasurementPlanV2":
        (
            canonical_decision,
            canonical_profile,
            isolation_requirement,
            isolation_summary,
        ) = _canonical_measurement_contracts(
            isolation_decision=isolation_decision,
            evidence_policy_profile=evidence_policy_profile,
        )
        evidence_policy_fingerprint = canonical_profile.fingerprint
        isolation_decision_fingerprint = canonical_decision.fingerprint
        frozen_stages = tuple(stages)
        plan_payload = {
            "schema_version": MEASUREMENT_PLAN_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "plan_revision": plan_revision,
            "candidate_fingerprint": candidate_fingerprint,
            "control_fingerprint": control_fingerprint,
            "dataset_fingerprint": dataset_fingerprint,
            "execution_contract_fingerprint": execution_contract_fingerprint,
            "evidence_policy_fingerprint": evidence_policy_fingerprint,
            "isolation_decision_fingerprint": isolation_decision_fingerprint,
            "stages": [stage.to_dict() for stage in frozen_stages],
            "repetitions_per_case": repetitions_per_case,
            "deadlines": deadlines.to_dict(),
            "isolation_requirement": isolation_requirement.to_dict(),
            "isolation_summary": isolation_summary.to_dict(),
            "decision_policy": decision_policy.to_dict(),
            "estimator_version": estimator_version,
            "decision_policy_version": decision_policy_version,
        }
        plan_fingerprint = stable_control_fingerprint(plan_payload)
        units: list[MeasurementWorkUnitV1] = []
        for stage in frozen_stages:
            for case_id in stage.case_ids:
                for repetition_id in range(1, repetitions_per_case + 1):
                    control = MeasurementWorkUnitV1.create(
                        measurement_plan_fingerprint=plan_fingerprint,
                        experiment_id=experiment_id,
                        artifact_fingerprint=control_fingerprint,
                        pairing_control_fingerprint=control_fingerprint,
                        dataset_fingerprint=dataset_fingerprint,
                        case_id=case_id,
                        arm=MeasurementArm.CONTROL,
                        repetition_id=repetition_id,
                        execution_contract_fingerprint=(
                            execution_contract_fingerprint
                        ),
                        evidence_policy_fingerprint=evidence_policy_fingerprint,
                        sampling_contract_fingerprint=stage.contract_fingerprint,
                        isolation_decision_fingerprint=(
                            isolation_decision_fingerprint
                        ),
                        stage_id=stage.stage_id,
                    )
                    treatment = MeasurementWorkUnitV1.create(
                        measurement_plan_fingerprint=plan_fingerprint,
                        experiment_id=experiment_id,
                        artifact_fingerprint=candidate_fingerprint,
                        pairing_control_fingerprint=control_fingerprint,
                        dataset_fingerprint=dataset_fingerprint,
                        case_id=case_id,
                        arm=MeasurementArm.TREATMENT,
                        repetition_id=repetition_id,
                        execution_contract_fingerprint=(
                            execution_contract_fingerprint
                        ),
                        evidence_policy_fingerprint=evidence_policy_fingerprint,
                        sampling_contract_fingerprint=stage.contract_fingerprint,
                        isolation_decision_fingerprint=(
                            isolation_decision_fingerprint
                        ),
                        stage_id=stage.stage_id,
                        depends_on_work_unit_id=control.work_unit_id,
                    )
                    units.extend((control, treatment))
        return cls(
            experiment_id=experiment_id,
            plan_revision=plan_revision,
            candidate_fingerprint=candidate_fingerprint,
            control_fingerprint=control_fingerprint,
            dataset_fingerprint=dataset_fingerprint,
            execution_contract_fingerprint=execution_contract_fingerprint,
            evidence_policy_fingerprint=evidence_policy_fingerprint,
            isolation_decision_fingerprint=isolation_decision_fingerprint,
            stages=frozen_stages,
            repetitions_per_case=repetitions_per_case,
            deadlines=deadlines,
            isolation_requirement=isolation_requirement,
            isolation_summary=isolation_summary,
            decision_policy=decision_policy,
            estimator_version=estimator_version,
            decision_policy_version=decision_policy_version,
            measurement_plan_fingerprint=plan_fingerprint,
            work_units=tuple(units),
            _contract_seal=_MEASUREMENT_PLAN_CONTRACT_SEAL,
        )

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case_id for stage in self.stages for case_id in stage.case_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "plan_revision": self.plan_revision,
            "candidate_fingerprint": self.candidate_fingerprint,
            "control_fingerprint": self.control_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "execution_contract_fingerprint": self.execution_contract_fingerprint,
            "evidence_policy_fingerprint": self.evidence_policy_fingerprint,
            "isolation_decision_fingerprint": self.isolation_decision_fingerprint,
            "stages": [stage.to_dict() for stage in self.stages],
            "repetitions_per_case": self.repetitions_per_case,
            "deadlines": self.deadlines.to_dict(),
            "isolation_requirement": self.isolation_requirement.to_dict(),
            "isolation_summary": self.isolation_summary.to_dict(),
            "decision_policy": self.decision_policy.to_dict(),
            "estimator_version": self.estimator_version,
            "decision_policy_version": self.decision_policy_version,
            "measurement_plan_fingerprint": self.measurement_plan_fingerprint,
            "work_units": [unit.to_dict() for unit in self.work_units],
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
        *,
        isolation_decision: "IsolationDecision",
        evidence_policy_profile: "EvidencePolicyProfileV2",
    ) -> "MeasurementPlanV2":
        _require_schema(value, MEASUREMENT_PLAN_SCHEMA_VERSION, "measurement plan")
        stages = tuple(
            SamplingStage.from_dict(_mapping(item, "sampling stage"))
            for item in _sequence(value.get("stages"), "stages")
        )
        persisted_requirement = IsolationRequirement.from_dict(
            _mapping(value.get("isolation_requirement"), "isolation_requirement")
        )
        persisted_summary = IsolationSummary.from_dict(
            _mapping(value.get("isolation_summary"), "isolation_summary")
        )
        loaded = cls.create(
            experiment_id=_required_text(value.get("experiment_id"), "experiment_id"),
            plan_revision=_positive_int(value.get("plan_revision"), "plan_revision"),
            candidate_fingerprint=_required_text(
                value.get("candidate_fingerprint"), "candidate_fingerprint"
            ),
            control_fingerprint=_required_text(
                value.get("control_fingerprint"), "control_fingerprint"
            ),
            dataset_fingerprint=_required_text(
                value.get("dataset_fingerprint"), "dataset_fingerprint"
            ),
            execution_contract_fingerprint=_required_text(
                value.get("execution_contract_fingerprint"),
                "execution_contract_fingerprint",
            ),
            isolation_decision=isolation_decision,
            evidence_policy_profile=evidence_policy_profile,
            stages=stages,
            repetitions_per_case=_positive_int(
                value.get("repetitions_per_case"), "repetitions_per_case"
            ),
            deadlines=DeadlinePolicy.from_dict(
                _mapping(value.get("deadlines"), "deadlines")
            ),
            decision_policy=AdaptiveMeasurementPolicy.from_dict(
                _mapping(value.get("decision_policy"), "decision_policy")
            ),
            estimator_version=_required_text(
                value.get("estimator_version"), "estimator_version"
            ),
            decision_policy_version=_required_text(
                value.get("decision_policy_version"), "decision_policy_version"
            ),
        )
        if value.get("measurement_plan_fingerprint") != (
            loaded.measurement_plan_fingerprint
        ):
            raise ValueError("measurement plan fingerprint does not match contract")
        if value.get("evidence_policy_fingerprint") != (
            loaded.evidence_policy_fingerprint
        ):
            raise ValueError("measurement plan evidence policy artifact drifted")
        if value.get("isolation_decision_fingerprint") != (
            loaded.isolation_decision_fingerprint
        ):
            raise ValueError("measurement plan isolation decision artifact drifted")
        if persisted_requirement != loaded.isolation_requirement:
            raise ValueError("measurement plan isolation requirement is not canonical")
        if persisted_summary != loaded.isolation_summary:
            raise ValueError("measurement plan isolation summary is not canonical")
        serialized_units = tuple(
            MeasurementWorkUnitV1.from_dict(_mapping(item, "work unit"))
            for item in _sequence(value.get("work_units"), "work_units")
        )
        if serialized_units != loaded.work_units:
            raise ValueError("serialized work units do not match measurement plan")
        return loaded


@dataclass(frozen=True)
class MeasurementFeasibility:
    status: FeasibilityStatus
    total_work_units: int
    reused_work_units: int
    pending_work_units: int
    decision_required_work_units: int
    safe_lane_count: int
    minimum_feasible_wall_seconds: float
    p50_time_to_decision_seconds: float
    p90_time_to_decision_seconds: float
    expected_checkpoint_quanta: int
    estimate_source: str
    estimate_confidence: str
    reason_code: str | None = None


@dataclass(frozen=True)
class PlanAmendmentCompatibility:
    compatible: bool
    reusable_work_unit_ids: tuple[str, ...]
    changed_fields: tuple[str, ...]
    reason_codes: tuple[str, ...]
    unit_decisions: tuple[WorkUnitReuseCompatibility, ...]


@dataclass(frozen=True)
class LegacyMeasurementControlDescription:
    """Bounded diagnostics only; legacy state can never authorize reuse."""

    source_schema_version: str | None
    declared_experiment_id: str | None
    declared_event_count: int | None
    trusted_reuse_allowed: bool = False
    reason_code: str = "legacy_identity_incomplete"
    schema_version: str = LEGACY_MEASUREMENT_CONTROL_DESCRIPTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LEGACY_MEASUREMENT_CONTROL_DESCRIPTION_SCHEMA_VERSION:
            raise ValueError("unsupported legacy measurement description schema")
        if self.source_schema_version is not None:
            _bounded_text(self.source_schema_version, "source_schema_version", 192)
        if self.declared_experiment_id is not None:
            _bounded_text(self.declared_experiment_id, "declared_experiment_id", 192)
        if self.declared_event_count is not None:
            _non_negative_int(self.declared_event_count, "declared_event_count")
        if self.trusted_reuse_allowed is not False:
            raise ValueError("legacy measurement state cannot authorize reuse")
        if self.reason_code != "legacy_identity_incomplete":
            raise ValueError("legacy measurement description must fail closed")


def describe_legacy_measurement_control(
    value: Mapping[str, object],
) -> LegacyMeasurementControlDescription:
    """Read only a bounded descriptive projection from a legacy checkpoint."""

    schema = value.get("schema_version")
    experiment_id = value.get("experiment_id")
    event_count = value.get("event_count")
    return LegacyMeasurementControlDescription(
        source_schema_version=(
            _bounded_text(schema, "schema_version", 192)
            if isinstance(schema, str)
            else None
        ),
        declared_experiment_id=(
            _bounded_text(experiment_id, "experiment_id", 192)
            if isinstance(experiment_id, str)
            else None
        ),
        declared_event_count=(
            _non_negative_int(event_count, "event_count")
            if isinstance(event_count, int) and not isinstance(event_count, bool)
            else None
        ),
    )


@dataclass(frozen=True)
class CaseAdmissionSignal:
    """Bounded candidate-independent signal for choosing an eligible case."""

    case_id: str
    stratum_id: str
    expected_information_value: float
    predicted_cost_seconds: float
    failure_risk: float = 0.0
    prior_variance: float = 1.0

    def __post_init__(self) -> None:
        _safe_id(self.case_id, "case_id")
        _safe_id(self.stratum_id, "stratum_id")
        object.__setattr__(
            self,
            "expected_information_value",
            _non_negative_number(
                self.expected_information_value,
                "expected_information_value",
            ),
        )
        object.__setattr__(
            self,
            "predicted_cost_seconds",
            _positive_number(self.predicted_cost_seconds, "predicted_cost_seconds"),
        )
        risk = _non_negative_number(self.failure_risk, "failure_risk")
        if risk > 1:
            raise ValueError("failure_risk cannot exceed one")
        object.__setattr__(self, "failure_risk", risk)
        variance = _non_negative_number(self.prior_variance, "prior_variance")
        if variance > 100:
            raise ValueError("prior_variance exceeds bounded scheduling range")
        object.__setattr__(self, "prior_variance", variance)

    @property
    def effective_information_value(self) -> float:
        return self.expected_information_value * math.sqrt(self.prior_variance)

    @property
    def score(self) -> float:
        return self.effective_information_value / (
            self.predicted_cost_seconds * (1.0 + self.failure_risk)
        )


@dataclass(frozen=True)
class MeasurementProgressSummary:
    current_stage_id: str
    completed_case_ids: tuple[str, ...]
    comparable_case_ids: tuple[str, ...]
    invalid_control_case_ids: tuple[str, ...]
    confidence_lower_bound: float | None
    point_estimate: float | None
    regression_detected: bool
    negative_effect_detected: bool
    futility_proven: bool
    new_comparable_pairs_in_window: int
    uncertainty_reduction_in_window: float
    current_stage_exhausted: bool
    completed_stage_ids: tuple[str, ...]
    checkpoint_quantum_expired: bool
    campaign_wall_deadline_expired: bool
    resume_safe: bool
    framework_blocked_reason_code: str | None = None
    case_admission_signals: tuple[CaseAdmissionSignal, ...] = ()

    def __post_init__(self) -> None:
        _safe_id(self.current_stage_id, "current_stage_id")
        for field_name in (
            "completed_case_ids",
            "comparable_case_ids",
            "invalid_control_case_ids",
            "completed_stage_ids",
        ):
            values = tuple(getattr(self, field_name))
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
            for value in values:
                _safe_id(value, field_name)
            object.__setattr__(self, field_name, values)
        if not set(self.comparable_case_ids).issubset(self.completed_case_ids):
            raise ValueError("comparable cases must be completed")
        _non_negative_int(
            self.new_comparable_pairs_in_window,
            "new_comparable_pairs_in_window",
        )
        object.__setattr__(
            self,
            "uncertainty_reduction_in_window",
            _non_negative_number(
                self.uncertainty_reduction_in_window,
                "uncertainty_reduction_in_window",
            ),
        )
        for field_name in ("confidence_lower_bound", "point_estimate"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self, field_name, _finite_number(value, field_name)
                )
        if self.checkpoint_quantum_expired and self.campaign_wall_deadline_expired:
            raise ValueError("only one scheduling deadline may terminate a decision")
        if self.framework_blocked_reason_code is not None:
            _safe_id(
                self.framework_blocked_reason_code,
                "framework_blocked_reason_code",
            )
        signals = tuple(self.case_admission_signals)
        if len(signals) != len({item.case_id for item in signals}):
            raise ValueError("case admission signals must be unique by case")
        object.__setattr__(self, "case_admission_signals", signals)


@dataclass(frozen=True)
class AdaptiveDecision:
    kind: AdaptiveDecisionKind
    reason_code: str
    next_stage_id: str | None = None
    admit_case_ids: tuple[str, ...] = ()
    resume_safe: bool = False
    expected_information_value: float | None = None
    remaining_case_budget: int | None = None
    admission_policy: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", AdaptiveDecisionKind(self.kind))
        _safe_id(self.reason_code, "reason_code")
        if self.next_stage_id is not None:
            _safe_id(self.next_stage_id, "next_stage_id")
        object.__setattr__(self, "admit_case_ids", tuple(self.admit_case_ids))
        if self.expected_information_value is not None:
            object.__setattr__(
                self,
                "expected_information_value",
                _non_negative_number(
                    self.expected_information_value,
                    "expected_information_value",
                ),
            )
        if self.remaining_case_budget is not None:
            _non_negative_int(self.remaining_case_budget, "remaining_case_budget")
        if self.admission_policy is not None:
            _safe_id(self.admission_policy, "admission_policy")


def estimate_measurement_feasibility(
    plan: MeasurementPlanV2,
    *,
    reusable_work_unit_ids: Sequence[str] = (),
    minimum_member_seconds: float | None = None,
    p50_member_seconds: float | None = None,
    p90_member_seconds: float | None = None,
    cold_start_seconds: float = 0.0,
    estimate_source: str | None = None,
    estimate_confidence: str | None = None,
) -> MeasurementFeasibility:
    """Estimate time to the first decision boundary without executing work."""

    known_ids = {unit.work_unit_id for unit in plan.work_units}
    reused = tuple(dict.fromkeys(reusable_work_unit_ids))
    unknown = set(reused) - known_ids
    if unknown:
        raise ValueError("reuse references unknown work units")
    cold_start = _non_negative_number(cold_start_seconds, "cold_start_seconds")
    history_available = p50_member_seconds is not None or p90_member_seconds is not None
    if not history_available:
        p50 = p90 = plan.deadlines.member_hard_deadline_seconds
        minimum = plan.deadlines.member_hard_deadline_seconds
        source = estimate_source or "declared_member_hard_deadline"
        confidence = estimate_confidence or "low"
    else:
        if p50_member_seconds is None or p90_member_seconds is None:
            raise ValueError("P50 and P90 member latency must be supplied together")
        p50 = _positive_number(p50_member_seconds, "p50_member_seconds")
        p90 = _positive_number(p90_member_seconds, "p90_member_seconds")
        if p90 < p50:
            raise ValueError("P90 member latency cannot be lower than P50")
        minimum = _positive_number(
            minimum_member_seconds if minimum_member_seconds is not None else p50,
            "minimum_member_seconds",
        )
        if minimum > p50:
            raise ValueError("minimum member latency cannot exceed P50")
        source = estimate_source or "compatible_history"
        confidence = estimate_confidence or "medium"
    _safe_id(source, "estimate_source")
    _safe_id(confidence, "estimate_confidence")

    sentinel = plan.stages[0]
    required_cases = set(sentinel.case_ids[: sentinel.minimum_case_count])
    decision_units = tuple(
        unit for unit in plan.work_units if unit.case_id in required_cases
    )
    reused_set = set(reused)
    pending_decision_units = tuple(
        unit for unit in decision_units if unit.work_unit_id not in reused_set
    )
    lanes = plan.isolation_summary.safe_lane_count
    minimum_wall = cold_start + _pair_lane_schedule_seconds(
        pending_decision_units, lanes, minimum
    )
    p50_wall = cold_start + _pair_lane_schedule_seconds(
        pending_decision_units, lanes, p50
    )
    p90_wall = cold_start + _pair_lane_schedule_seconds(
        pending_decision_units, lanes, p90
    )
    deadline = plan.deadlines.campaign_wall_deadline_seconds
    if deadline is not None and deadline < minimum_wall:
        if plan.deadlines.resumable_chunked:
            status = FeasibilityStatus.RESUMABLE_CHUNKED
            reason = "explicit_deadline_requires_resumable_chunks"
        else:
            status = FeasibilityStatus.INFEASIBLE_DEADLINE
            reason = "explicit_deadline_below_minimum_feasible_wall_time"
    else:
        status = FeasibilityStatus.FEASIBLE
        reason = None
    quantum = plan.deadlines.checkpoint_quantum_seconds
    expected_quanta = max(1, math.ceil(p90_wall / quantum))
    return MeasurementFeasibility(
        status=status,
        total_work_units=len(plan.work_units),
        reused_work_units=len(reused),
        pending_work_units=len(plan.work_units) - len(reused),
        decision_required_work_units=len(decision_units),
        safe_lane_count=lanes,
        minimum_feasible_wall_seconds=minimum_wall,
        p50_time_to_decision_seconds=p50_wall,
        p90_time_to_decision_seconds=p90_wall,
        expected_checkpoint_quanta=expected_quanta,
        estimate_source=source,
        estimate_confidence=confidence,
        reason_code=reason,
    )


def validate_measurement_feasibility(
    plan: MeasurementPlanV2,
    feasibility: MeasurementFeasibility,
) -> None:
    """Fail before rollout when explicit deadline semantics are impossible."""

    if feasibility.status is FeasibilityStatus.INFEASIBLE_DEADLINE:
        raise ValueError(
            "explicit Campaign deadline is below minimum feasible wall time; "
            "select resumable chunked execution or amend the plan"
        )
    if (
        feasibility.status is FeasibilityStatus.RESUMABLE_CHUNKED
        and not plan.deadlines.resumable_chunked
    ):
        raise ValueError("chunked feasibility requires an explicitly chunked plan")


def classify_plan_amendment(
    previous: MeasurementPlanV2,
    amended: MeasurementPlanV2,
) -> PlanAmendmentCompatibility:
    """Classify immutable-observation reuse under an explicit new plan."""

    if amended.plan_revision <= previous.plan_revision:
        raise ValueError("plan amendment revision must increase")
    comparable_fields = (
        "experiment_id",
        "candidate_fingerprint",
        "control_fingerprint",
        "dataset_fingerprint",
        "execution_contract_fingerprint",
        "evidence_policy_fingerprint",
        "stages",
        "repetitions_per_case",
        "deadlines",
        "isolation_requirement",
        "isolation_summary",
        "decision_policy",
        "estimator_version",
        "decision_policy_version",
    )
    changed = tuple(
        field_name
        for field_name in comparable_fields
        if getattr(previous, field_name) != getattr(amended, field_name)
    )
    amended_by_coordinate = {
        (unit.case_id, unit.arm, unit.repetition_id): unit
        for unit in amended.work_units
    }
    decisions: list[WorkUnitReuseCompatibility] = []
    for stored_unit in previous.work_units:
        expected_unit = amended_by_coordinate.get(
            (stored_unit.case_id, stored_unit.arm, stored_unit.repetition_id)
        )
        if expected_unit is None:
            decision = WorkUnitReuseCompatibility(
                compatible=False,
                work_unit_id=stored_unit.work_unit_id,
                reason_code="work_unit_not_in_amended_plan",
            )
        elif (
            stored_unit.arm is MeasurementArm.CONTROL
            and stored_unit.experiment_id != expected_unit.experiment_id
            and stored_unit.baseline_compatibility_key
            == expected_unit.baseline_compatibility_key
        ):
            key = stored_unit.baseline_compatibility_key
            decision = WorkUnitReuseCompatibility(
                compatible=True,
                work_unit_id=stored_unit.work_unit_id,
                expected_work_unit_id=expected_unit.work_unit_id,
                baseline_compatibility_fingerprint=(key.fingerprint if key else None),
                reason_code="compatible_cross_experiment_baseline",
            )
        else:
            decision = classify_measurement_work_unit_compatibility(
                expected_unit=expected_unit,
                stored_unit=stored_unit,
            )
        decisions.append(decision)
    reusable = tuple(
        decision.work_unit_id for decision in decisions if decision.compatible
    )
    reason_codes = tuple(
        dict.fromkeys(
            decision.reason_code
            for decision in decisions
            if not decision.compatible
        )
    )
    return PlanAmendmentCompatibility(
        compatible=bool(reusable),
        reusable_work_unit_ids=reusable,
        changed_fields=changed,
        reason_codes=reason_codes,
        unit_decisions=tuple(decisions),
    )


def decide_staged_measurement(
    plan: MeasurementPlanV2,
    progress: MeasurementProgressSummary,
) -> AdaptiveDecision:
    """Return the next deterministic admission or stop decision.

    The estimator supplies bounded facts (confidence, regression and futility);
    this function only applies the predeclared policy and never inspects raw
    observations.
    """

    stage_by_id = {stage.stage_id: stage for stage in plan.stages}
    if progress.current_stage_id not in stage_by_id:
        raise ValueError("current stage is outside the measurement plan")
    unknown_cases = (
        set(progress.completed_case_ids)
        | set(progress.comparable_case_ids)
        | set(progress.invalid_control_case_ids)
    ) - set(plan.case_ids)
    if unknown_cases:
        raise ValueError("progress references cases outside the measurement plan")
    unknown_stages = set(progress.completed_stage_ids) - set(stage_by_id)
    if unknown_stages:
        raise ValueError("progress references stages outside the measurement plan")
    unknown_signal_cases = {
        signal.case_id for signal in progress.case_admission_signals
    } - set(plan.case_ids)
    if unknown_signal_cases:
        raise ValueError("case admission signal is outside the measurement plan")
    policy = plan.decision_policy

    if progress.framework_blocked_reason_code is not None:
        return _stop(
            AdaptiveDecisionKind.STOP_FRAMEWORK_BLOCKED,
            progress.framework_blocked_reason_code,
        )
    if progress.regression_detected:
        return _stop(AdaptiveDecisionKind.STOP_REGRESSION, "decisive_regression")
    if progress.negative_effect_detected:
        return _stop(AdaptiveDecisionKind.STOP_NEGATIVE, "decisive_negative_effect")
    if len(progress.invalid_control_case_ids) >= policy.maximum_invalid_controls:
        return _stop(
            AdaptiveDecisionKind.STOP_INVALID_CONTROL,
            "repeated_invalid_control",
        )

    confident_positive = (
        progress.confidence_lower_bound is not None
        and progress.confidence_lower_bound >= policy.minimum_effect
        and progress.point_estimate is not None
        and progress.point_estimate > 0.0
        and len(progress.comparable_case_ids) >= policy.minimum_independent_cases
    )
    regression_stage = next(
        (
            stage
            for stage in plan.stages
            if stage.kind is SamplingStageKind.REGRESSION_TRANSFER
        ),
        None,
    )
    if confident_positive:
        if (
            policy.require_regression_transfer
            and regression_stage is not None
            and regression_stage.stage_id not in progress.completed_stage_ids
        ):
            return AdaptiveDecision(
                kind=AdaptiveDecisionKind.ADMIT_REQUIRED_REGRESSION_TRANSFER,
                reason_code=(
                    "positive_effect_requires_independent_regression_transfer"
                ),
                next_stage_id=regression_stage.stage_id,
                admit_case_ids=regression_stage.case_ids,
            )
        return _stop(
            AdaptiveDecisionKind.STOP_CONFIDENT_POSITIVE,
            "positive_confidence_and_coverage_satisfied",
        )

    if policy.futility_enabled and progress.futility_proven:
        return _stop(AdaptiveDecisionKind.STOP_FUTILITY, "positive_policy_unattainable")
    has_replacement_cases = any(
        stage.kind in {SamplingStageKind.EXPANSION, SamplingStageKind.TIE_BREAK}
        and any(
            case_id not in progress.completed_case_ids
            for case_id in stage.case_ids
        )
        for stage in plan.stages
    )
    if (
        len(progress.completed_case_ids) >= policy.zero_yield_window
        and progress.new_comparable_pairs_in_window == 0
        and progress.uncertainty_reduction_in_window == 0
        and not has_replacement_cases
    ):
        return _stop(
            AdaptiveDecisionKind.STOP_ZERO_YIELD,
            "zero_comparable_or_uncertainty_yield",
        )
    if progress.campaign_wall_deadline_expired:
        return AdaptiveDecision(
            kind=AdaptiveDecisionKind.MEASUREMENT_INCOMPLETE_CAMPAIGN_DEADLINE,
            reason_code="explicit_campaign_wall_deadline_expired",
            resume_safe=progress.resume_safe,
        )
    if progress.checkpoint_quantum_expired:
        return AdaptiveDecision(
            kind=AdaptiveDecisionKind.MEASUREMENT_INCOMPLETE_CHECKPOINT,
            reason_code="checkpoint_quantum_expired",
            resume_safe=progress.resume_safe,
        )
    if not progress.current_stage_exhausted:
        return AdaptiveDecision(
            kind=AdaptiveDecisionKind.CONTINUE_CURRENT_STAGE,
            reason_code="current_stage_has_admitted_work",
            next_stage_id=progress.current_stage_id,
        )

    for stage in plan.stages:
        if stage.stage_id in progress.completed_stage_ids:
            continue
        if stage.requires_positive_effect:
            continue
        available = tuple(
            case_id
            for case_id in stage.case_ids
            if case_id not in progress.completed_case_ids
        )
        if not available:
            continue
        if stage.kind is SamplingStageKind.EXPANSION:
            kind = AdaptiveDecisionKind.ADMIT_EXPANSION
        elif stage.kind is SamplingStageKind.TIE_BREAK:
            kind = AdaptiveDecisionKind.ADMIT_TIE_BREAK
        else:
            continue
        ranked_cases, information_by_case = _rank_admission_cases(
            stage=stage,
            available_case_ids=available,
            signals=progress.case_admission_signals,
        )
        admitted = ranked_cases[: stage.batch_size]
        return AdaptiveDecision(
            kind=kind,
            reason_code="inconclusive_evidence_has_positive_information_value",
            next_stage_id=stage.stage_id,
            admit_case_ids=admitted,
            expected_information_value=sum(
                information_by_case[case_id] for case_id in admitted
            ),
            remaining_case_budget=max(0, len(available) - len(admitted)),
            admission_policy="stratified-information-cost-risk-v1",
        )
    return _stop(
        AdaptiveDecisionKind.STOP_INCONCLUSIVE,
        "eligible_measurement_stages_exhausted",
    )


def _rank_admission_cases(
    *,
    stage: SamplingStage,
    available_case_ids: Sequence[str],
    signals: Sequence[CaseAdmissionSignal],
) -> tuple[tuple[str, ...], dict[str, float]]:
    """Rank an already-sealed panel while preserving stratum coverage.

    Missing signals intentionally receive equal neutral estimates, which makes
    the frozen, stratified stage order authoritative.  Runtime estimates can
    refine selection but cannot introduce a case outside the stage.
    """

    available = tuple(available_case_ids)
    if not set(available).issubset(stage.case_ids):
        raise ValueError("admission candidate is outside its frozen stage")
    order = {case_id: index for index, case_id in enumerate(stage.case_ids)}
    provided = {signal.case_id: signal for signal in signals}
    resolved = {
        case_id: provided.get(
            case_id,
            CaseAdmissionSignal(
                case_id=case_id,
                stratum_id="default",
                expected_information_value=1.0,
                predicted_cost_seconds=1.0,
            ),
        )
        for case_id in available
    }
    ranked = sorted(
        available,
        key=lambda case_id: (
            -resolved[case_id].score,
            order[case_id],
            case_id,
        ),
    )
    # Prefer one high-value case from each declared stratum before taking a
    # second case from a stratum.  This is deterministic and bounded by the
    # frozen panel; it cannot leak or invent hidden work.
    first_per_stratum: list[str] = []
    remainder: list[str] = []
    seen_strata: set[str] = set()
    for case_id in ranked:
        stratum = resolved[case_id].stratum_id
        if stratum in seen_strata:
            remainder.append(case_id)
        else:
            seen_strata.add(stratum)
            first_per_stratum.append(case_id)
    return (
        tuple(first_per_stratum + remainder),
        {
            case_id: resolved[case_id].effective_information_value
            for case_id in available
        },
    )


def stable_control_fingerprint(value: object) -> str:
    """Return a path-independent canonical SHA-256 identity."""

    return "sha256:" + _digest(value)


def measurement_control_bytes_fingerprint(value: bytes) -> str:
    """Incremental record-chain fingerprint for complete JSONL bytes."""

    if value and not value.endswith(b"\n"):
        raise ValueError("journal fingerprint requires complete newline records")
    current = _bytes_fingerprint(b"")
    for record in value.splitlines(keepends=True):
        current = extend_measurement_control_journal_fingerprint(current, record)
    return current


def extend_measurement_control_journal_fingerprint(
    previous_fingerprint: str, encoded_record: bytes
) -> str:
    _fingerprint(previous_fingerprint, "previous journal fingerprint")
    if not encoded_record.endswith(b"\n") or encoded_record.count(b"\n") != 1:
        raise ValueError("journal chain accepts exactly one complete record")
    return _bytes_fingerprint(previous_fingerprint.encode("ascii") + encoded_record)


def _pair_lane_schedule_seconds(
    units: Sequence[MeasurementWorkUnitV1],
    lane_count: int,
    member_seconds: float,
) -> float:
    if not units:
        return 0.0
    arm_counts: dict[tuple[str, int], int] = {}
    for unit in units:
        key = (unit.case_id, unit.repetition_id)
        arm_counts[key] = arm_counts.get(key, 0) + 1
    pair_costs = sorted(
        (count * member_seconds for count in arm_counts.values()), reverse=True
    )
    lane_loads = [0.0 for _ in range(min(lane_count, len(pair_costs)))]
    for cost in pair_costs:
        lane_index = min(range(len(lane_loads)), key=lane_loads.__getitem__)
        lane_loads[lane_index] += cost
    return max(lane_loads, default=0.0)


def _stop(kind: AdaptiveDecisionKind, reason_code: str) -> AdaptiveDecision:
    return AdaptiveDecision(kind=kind, reason_code=reason_code)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bytes_fingerprint(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError(
        f"measurement control value is not JSON serializable: {type(value).__name__}"
    )


def _require_schema(value: Mapping[str, object], expected: str, label: str) -> None:
    if value.get("schema_version") != expected:
        raise ValueError(f"unsupported {label} schema")


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array")
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    sequence = _sequence(value, field_name)
    if any(not isinstance(item, str) or not item for item in sequence):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return tuple(sequence)


def _safe_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value) or value in {
        ".",
        "..",
    }:
        raise ValueError(f"invalid {field_name}: {value!r}")


def _fingerprint(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _FINGERPRINT_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a full sha256 fingerprint")


def _optional_fingerprint(value: object, field_name: str) -> None:
    if value is not None:
        _fingerprint(value, field_name)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _bounded_text(value: object, field_name: str, limit: int) -> str:
    text = _required_text(value, field_name)
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text field must be a string")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _utc_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    return parsed


def _number(value: object, field_name: str) -> float:
    return _finite_number(value, field_name)


def _optional_number(value: object, field_name: str) -> float | None:
    return None if value is None else _finite_number(value, field_name)


def _positive_number(value: object, field_name: str) -> float:
    parsed = _finite_number(value, field_name)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _non_negative_number(value: object, field_name: str) -> float:
    parsed = _finite_number(value, field_name)
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return parsed


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value
