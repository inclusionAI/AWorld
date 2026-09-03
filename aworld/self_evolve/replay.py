from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import inspect
import json
import math
import os
import re
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, fields as dataclass_fields, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from urllib.parse import urlsplit

from aworld.core.context.amni.local import LocalIsolatedApplicationContext
from aworld.core.tool.replay_policy import (
    ArtifactPolicy,
    DynamicEndpointBinding,
    EvidenceContractIdentity,
    EvidencePolicyProfileV2,
    EvidencePolicyValidationError,
    FrameworkEvidenceWriterAttestationV2,
    ProducerRegistrationCapabilityV2,
    attest_task_response_v2,
    build_framework_evidence_manifest_v2,
    compile_evidence_policy_profile_v2,
    issue_framework_evidence_writer_attestation_v2,
    issue_producer_registration_capability_v2,
    make_evidence_handle_v2,
    preflight_evidence_policy_v2,
)
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.memory.tool_call_compaction import REPLAY_COMPACTED_ARGUMENT_FAILURE
from aworld.runners.batch import (
    DeterministicTaskBatchExecutor,
    TaskBatchItem,
    TaskResourceClaim,
)
from aworld.self_evolve.concurrency import SelfEvolveConcurrencyPolicy
from aworld.self_evolve.counterexamples import (
    REPLAY_COUNTEREXAMPLE_SCHEMA_VERSION,
)
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    is_framework_meta_trace_pack,
)
from aworld.self_evolve.failure_events import (
    FAILURE_EVENT_SCHEMA_VERSION,
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
    causal_failure_events,
)
from aworld.self_evolve.measurement_control import (
    AdaptiveDecisionKind,
    CaseAdmissionSignal,
    LaneMaterializationAttestationV1,
    MeasurementArm,
    MeasurementPlanV2,
    MeasurementWorkUnitV1,
    stable_control_fingerprint,
)
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    EffectDirection,
    assess_experiment_validity,
    estimate_paired_effect,
    observations_from_replay,
)
from aworld.self_evolve.measurement_control import (
    MeasurementProgressSummary,
    MeasurementWorkUnitState,
    SamplingStageKind,
)
from aworld.self_evolve.replay_adaptation import IsolationDecision
from aworld.self_evolve.replay_adaptation import (
    REPLAY_ARTIFACT_PLACEHOLDER,
    REPLAY_WORKSPACE_PLACEHOLDER,
    ReplayAdaptationBundle,
    ReplayAdapterBinding,
    ReplayCaseAdaptation,
    ReplayDependency,
    replay_adaptation_semantic_fingerprint,
    validate_replay_binding_concurrency,
    materialize_replay_workspace,
)
from aworld.self_evolve.replay_capability import (
    FrozenReplayCapability,
    REPLAY_RESPONSE_RECORD_ID_ENV,
    REPLAY_RESPONSE_REQUIREMENT_ID_ENV,
    REPLAY_RESPONSE_SERVICE_ID_ENV,
    build_replay_resource_limited_command,
    build_replay_sandboxed_command,
    FrozenReplayFile,
    ReplayProtocolProbe,
    ReplayReadinessProbe,
    ReplayServiceSpec,
    fingerprint_skill_package,
    replay_capability_semantic_fingerprint,
    replay_payload_contains_expected_value,
    replay_process_memory_bytes,
    verify_frozen_replay_capability,
)
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.schema_diagnostics import (
    SchemaFieldRepairConstraint,
    SchemaFieldViolation,
    schema_field_diagnostic_details,
    websocket_handshake_http_version_constraint,
)
from aworld.self_evolve.types import CandidateVariant, DatasetRecipe, SelfEvolveTargetRef, to_json_dict

_EVIDENCE_RETRY_LIMIT = 1
_SERVICE_STARTUP_RETRY_LIMIT = 1
_MIN_REPLAY_SERVICE_STARTUP_TIMEOUT_SECONDS = 15.0
_MEMBER_PHASE_TEARDOWN_GRACE_MAX_SECONDS = 5.0
_SYNTHETIC_EVIDENCE_EXCERPT_CHARS = 4000
_MAX_METADATA_EVIDENCE_CHARS = 16_384
_MAX_EVIDENCE_MANIFEST_BYTES = 1024 * 1024
_MAX_EVIDENCE_MANIFEST_ENTRIES = 256
_REPLAY_EVIDENCE_POLICY_MODES = frozenset({"legacy", "required"})
_REPLAY_TRUSTED_MANIFEST_SCHEMA = "aworld.replay.runtime_trust.v2"
_REPLAY_TRUSTED_EVIDENCE_PRODUCER = "replay.task"
_REPLAY_TRUSTED_RESPONSE_PRODUCER = "framework.supervisor"
_REPLAY_TRUSTED_EVIDENCE_TYPE = "task.evidence"
_REPLAY_TRUSTED_RESPONSE_TYPE = "task.response"
_MEASUREMENT_RESULT_PROJECTION_SCHEMA = (
    "aworld.self_evolve.measurement_result_projection.v1"
)
_MEASUREMENT_RESULT_PROJECTION_FILE = "measurement_result_projection.json"
_TASK_RESPONSE_CAPABILITY_FD_ENV = (
    "AWORLD_SELF_EVOLVE_TASK_RESPONSE_CAPABILITY_FD"
)
_REPLAY_TRUST_RESERVED_ENV = frozenset(
    {
        "AWORLD_REPLAY_EVIDENCE_POLICY_PROFILE_JSON",
        "AWORLD_REPLAY_EVIDENCE_POLICY_FINGERPRINT",
        "AWORLD_REPLAY_EVIDENCE_WRITER_ATTESTATION_JSON",
        "AWORLD_REPLAY_EVIDENCE_PRODUCERS_JSON",
        _TASK_RESPONSE_CAPABILITY_FD_ENV,
    }
)
_REPLAY_SERVICE_PROTOCOL_TRACE_NAME = "protocol_trace.jsonl"
_MAX_REPLAY_SERVICE_PROTOCOL_TRACE_BYTES = 64 * 1024
_MAX_REPLAY_SERVICE_PROTOCOL_TRACE_EXCERPT_CHARS = 4_000
_RUNTIME_RESPONSE_CONSTRAINT_SCHEMA_VERSION = (
    "aworld.self_evolve.runtime_response_constraint.v1"
)
_RECORDED_RESPONSE_CONTEXT_INCOMPLETE = (
    "recorded_response_context_incomplete"
)
_RECORDED_RESPONSE_TARGET_MAX_BYTES = 48 * 1024
_LOOPBACK_HTTP_ENDPOINT_PATTERN = re.compile(
    r"(?i)https?://(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])"
    r"(?::\d{1,5})?(?![:\d])"
)
_REPLAY_PROVENANCE_METRIC_KEYS = (
    "adaptation_fingerprint",
    "workspace_seed_fingerprint",
    "task_input_fingerprint",
    "dataset_fingerprint",
    "baseline_skill_fingerprint",
    "adapter_determinism",
    "isolated_workspace_path",
    "replay_capability_id",
    "capability_package_fingerprint",
    "frozen_capability_fingerprint",
    "service_runtime_fingerprint",
    "service_logical_ids",
    "service_endpoint",
    "service_startup_status",
    "service_cleanup_status",
    "evidence_manifest_fingerprint",
    "activated_skill_root",
    "activated_skill_package_fingerprint",
    "skill_activation_attestation_source",
)
_EVIDENCE_COVERAGE_BOOL_METRIC_KEYS = (
    "evidence_strategy_passed",
    "evidence_manifest_present",
    "evidence_manifest_readable",
    "evidence_manifest_valid",
    "evidence_bundle_present",
    "evidence_bundle_valid",
    "evidence_runtime_policy_active",
    "evidence_runtime_policy_passed",
    "evidence_runtime_policy_authoritative_passed",
    "task_completion_established",
    "timeout_evidence_recovered",
    "skill_activation_attested",
)
_EVIDENCE_COVERAGE_NUMERIC_METRIC_KEYS = (
    "evidence_manifest_entry_count",
    "evidence_manifest_invalid_entry_count",
    "evidence_manifest_size_bytes",
    "evidence_bundle_entry_count",
    "evidence_artifact_reference_count",
    "evidence_manifested_artifact_reference_count",
    "evidence_unmanifested_artifact_reference_count",
    "evidence_runtime_policy_violation_count",
    "evidence_runtime_policy_tool_call_attempt_count",
    "evidence_runtime_policy_artifact_file_count",
    "evidence_runtime_policy_artifact_bytes",
    "evidence_runtime_policy_consecutive_failed_action_count",
    "evidence_runtime_policy_max_consecutive_failed_actions",
    "evidence_runtime_policy_allowed_loopback_endpoint_count",
    "evidence_runtime_policy_allowed_control_action_count",
)

_PER_MEMBER_REPETITION_SEMANTICS = "per_member_v3"
_MIGRATED_DISTRIBUTED_REPETITION_SEMANTICS = "distributed_v2_migrated"
_NON_AUTHORITATIVE_V3_REPETITION_SEMANTICS = "per_member_v3_non_authoritative"
_MEMBER_REPLAY_SCHEMA_V3 = "aworld.self_evolve.member_replay.v3"
_LEGACY_MEMBER_REPLAY_SCHEMAS = {
    "aworld.self_evolve.member_replay.v1",
    "aworld.self_evolve.member_replay.v2",
}
_REPLAY_LIFECYCLE_SCHEMA_V3 = "aworld.self_evolve.replay_lifecycle.v3"
_REPLAY_LIFECYCLE_SCHEMA_V2 = "aworld.self_evolve.replay_lifecycle.v2"


@dataclass(frozen=True)
class CandidateReplayRequest:
    run_id: str
    task_id: str
    workspace_root: str
    target: SelfEvolveTargetRef
    candidate_id: str
    overlay_skill_root: str
    task_input: Any
    baseline_skill_root: str | None = None
    baseline_replay_dir: str | None = None
    resume_replay_dir: str | None = None
    agent: str | None = None
    timeout_seconds: float | None = None
    max_steps: int | None = None
    max_tool_calls: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    baseline_repetitions: int = 1
    candidate_repetitions: int = 1
    replay_adaptation: ReplayAdaptationBundle | None = None
    dataset_fingerprint: str | None = None
    baseline_skill_fingerprint: str | None = None
    adaptation_fingerprint: str | None = None
    support_fingerprint: str | None = None
    timeout_envelope_fingerprint: str | None = None
    workspace_seed_fingerprint: str | None = None
    task_input_fingerprint: str | None = None
    verified_candidate_package_fingerprint: str | None = None
    artifact_namespace: str | None = None
    invalid_control_patience: int = 2
    measurement_early_stop_enabled: bool = False
    stop_on_incomparable_member: bool = False
    repetition_policy: str = "configured"
    repetition_semantics: str = _PER_MEMBER_REPETITION_SEMANTICS
    evidence_policy_mode: str = "legacy"
    measurement_plan: MeasurementPlanV2 | None = None
    measurement_isolation_decision: IsolationDecision | None = None
    measurement_evidence_policy_profile: EvidencePolicyProfileV2 | None = None
    measurement_lane_attestations: Mapping[
        str, LaneMaterializationAttestationV1
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            isinstance(self.invalid_control_patience, bool)
            or self.invalid_control_patience <= 0
        ):
            raise ValueError("invalid_control_patience must be positive")
        if not isinstance(self.measurement_early_stop_enabled, bool):
            raise ValueError("measurement_early_stop_enabled must be boolean")
        if not isinstance(self.stop_on_incomparable_member, bool):
            raise ValueError("stop_on_incomparable_member must be boolean")
        if self.repetition_policy not in {
            "configured",
            "independent_case_adaptive",
        }:
            raise ValueError("unsupported replay repetition_policy")
        if self.evidence_policy_mode not in _REPLAY_EVIDENCE_POLICY_MODES:
            raise ValueError("unsupported replay evidence_policy_mode")
        measurement_contracts = (
            self.measurement_plan,
            self.measurement_isolation_decision,
            self.measurement_evidence_policy_profile,
        )
        if any(item is not None for item in measurement_contracts):
            if not all(item is not None for item in measurement_contracts):
                raise ValueError("measurement replay contracts must be complete")
            if self.evidence_policy_mode != "required":
                raise ValueError("measurement replay contracts require evidence policy v2")
            assert self.measurement_plan is not None
            assert self.measurement_isolation_decision is not None
            assert self.measurement_evidence_policy_profile is not None
            verified = MeasurementPlanV2.from_dict(
                self.measurement_plan.to_dict(),
                isolation_decision=self.measurement_isolation_decision,
                evidence_policy_profile=self.measurement_evidence_policy_profile,
            )
            if verified != self.measurement_plan:
                raise ValueError("measurement replay plan is not canonical")
            known_units = {item.work_unit_id for item in verified.work_units}
            for unit_id, attestation in self.measurement_lane_attestations.items():
                if unit_id not in known_units or not isinstance(
                    attestation, LaneMaterializationAttestationV1
                ):
                    raise ValueError("measurement lane attestation is outside the plan")
                if (
                    attestation.measurement_plan_fingerprint
                    != verified.measurement_plan_fingerprint
                    or attestation.isolation_decision_fingerprint
                    != self.measurement_isolation_decision.fingerprint
                    or attestation.evidence_policy_fingerprint
                    != self.measurement_evidence_policy_profile.fingerprint
                ):
                    raise ValueError("measurement lane attestation contract drifted")
        elif self.measurement_lane_attestations:
            raise ValueError("lane attestations require measurement replay contracts")


def _failure_event_from_persisted_mapping(
    payload: Mapping[str, Any],
) -> ReplayFailureEvent:
    """Load canonical or dataclass-projected failure events without losing scope."""

    try:
        return ReplayFailureEvent.from_dict(payload)
    except ValueError:
        if payload.get("schema_version") != FAILURE_EVENT_SCHEMA_VERSION:
            return ReplayFailureEvent.from_legacy_mapping(payload)
        compatibility = payload.get("_compatibility")
        if "semantic_key" in payload or not isinstance(compatibility, Mapping):
            raise
        return ReplayFailureEvent(
            code=str(payload.get("code") or "persisted_replay_failure"),
            owner=FailureOwner(str(payload.get("owner"))),
            stage=FailureStage(str(payload.get("stage"))),
            scope=FailureScope(str(payload.get("scope"))),
            repairable=payload.get("repairable") is True,
            category=str(payload.get("category") or "replay"),
            summary=str(payload.get("summary") or ""),
            diagnostics=(
                payload.get("diagnostics")
                if isinstance(payload.get("diagnostics"), Mapping)
                else {}
            ),
            artifact_refs=tuple(payload.get("artifact_refs") or ()),
            source=FailureEventSource(str(payload.get("source"))),
            causes=tuple(payload.get("causes") or ()),
            capability_id=(
                str(payload["capability_id"])
                if payload.get("capability_id") is not None
                else None
            ),
            requirement_id=(
                str(payload["requirement_id"])
                if payload.get("requirement_id") is not None
                else None
            ),
            contract_fingerprint=(
                str(payload["contract_fingerprint"])
                if payload.get("contract_fingerprint") is not None
                else None
            ),
            capability_identity_digest=(
                str(payload["capability_identity_digest"])
                if payload.get("capability_identity_digest") is not None
                else None
            ),
            requirement_identity_digest=(
                str(payload["requirement_identity_digest"])
                if payload.get("requirement_identity_digest") is not None
                else None
            ),
            contract_identity_digest=(
                str(payload["contract_identity_digest"])
                if payload.get("contract_identity_digest") is not None
                else None
            ),
            event_id=str(payload.get("event_id") or f"replay-event-{uuid.uuid4().hex}"),
            _compatibility=(compatibility if isinstance(compatibility, Mapping) else {}),
        )


@dataclass(frozen=True)
class ReplayVariantResult:
    variant_id: str
    status: ReplayExecutionStatus | str
    trajectory: list[Mapping[str, Any]]
    metrics: Mapping[str, Any] = field(default_factory=dict)
    stdout_path: str | None = None
    stderr_path: str | None = None
    failure: ReplayFailureEvent | Mapping[str, Any] | None = None
    blocked_by: tuple[ReplayFailureEvent, ...] = ()
    repetition_results: tuple["ReplayVariantResult", ...] = ()

    def __post_init__(self) -> None:
        try:
            status = ReplayExecutionStatus(self.status)
        except ValueError as exc:
            raise ValueError(f"unsupported replay execution status: {self.status!r}") from exc
        failure = self.failure
        if isinstance(failure, Mapping) and not isinstance(
            failure, ReplayFailureEvent
        ):
            failure = _failure_event_from_persisted_mapping(failure)
        blocked_by = tuple(self.blocked_by)
        if any(not isinstance(event, ReplayFailureEvent) for event in blocked_by):
            raise ValueError("blocked_by must contain replay failure events")
        if status in {
            ReplayExecutionStatus.SUCCEEDED,
            ReplayExecutionStatus.FAILED,
        } and blocked_by:
            raise ValueError("executed replay variant cannot have blocked_by")
        if status is ReplayExecutionStatus.SUCCEEDED and failure is not None:
            raise ValueError("succeeded replay variant cannot have a failure")
        if status is ReplayExecutionStatus.FAILED and failure is None:
            raise ValueError("failed replay variant requires a failure event")
        if status is ReplayExecutionStatus.BLOCKED:
            if failure is not None:
                raise ValueError("blocked replay variant cannot have an execution failure")
            if not blocked_by:
                raise ValueError("blocked replay variant requires blocked_by")
            if self.trajectory:
                raise ValueError("blocked replay variant cannot contain a trajectory")
        if status is ReplayExecutionStatus.NOT_RUN and (
            failure is not None or blocked_by or self.trajectory
        ):
            raise ValueError("not_run replay variant cannot contain execution output")
        if status in {
            ReplayExecutionStatus.BLOCKED,
            ReplayExecutionStatus.NOT_RUN,
        } and (self.stdout_path or self.stderr_path or self.repetition_results):
            raise ValueError(
                "unexecuted replay variant cannot contain execution artifacts"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "failure", failure)
        object.__setattr__(self, "blocked_by", blocked_by)

    @property
    def succeeded(self) -> bool:
        return self.status is ReplayExecutionStatus.SUCCEEDED

    @property
    def executed(self) -> bool:
        return self.status in {
            ReplayExecutionStatus.SUCCEEDED,
            ReplayExecutionStatus.FAILED,
        }


@dataclass(frozen=True)
class CandidateReplayResult:
    request: CandidateReplayRequest
    baseline: ReplayVariantResult
    candidate: ReplayVariantResult
    # None is reserved for legacy root-level single-member artifacts. New
    # backends always write an explicit tuple, including one-member datasets.
    member_results: tuple["CandidateReplayMemberResult", ...] | None = None
    artifact_failure_events: tuple[ReplayFailureEvent, ...] = ()
    measurement_decision: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(event, ReplayFailureEvent)
            for event in self.artifact_failure_events
        ):
            raise ValueError(
                "artifact_failure_events must contain replay failure events"
            )
        if self.measurement_decision is not None and not isinstance(
            self.measurement_decision, Mapping
        ):
            raise ValueError("measurement_decision must be a mapping")

    @property
    def succeeded(self) -> bool:
        if self.member_results is not None:
            return bool(self.member_results) and all(
                member.succeeded for member in self.member_results
            )
        return self.baseline.succeeded and self.candidate.succeeded


@dataclass(frozen=True)
class CandidateReplayMemberResult:
    case_id: str
    request: CandidateReplayRequest
    baseline: ReplayVariantResult
    candidate: ReplayVariantResult

    @property
    def succeeded(self) -> bool:
        return self.baseline.succeeded and self.candidate.succeeded


@dataclass(frozen=True)
class NormalizedReplayMember:
    case: EvalCase
    request: CandidateReplayRequest
    baseline: ReplayVariantResult
    candidate: ReplayVariantResult

    @property
    def case_id(self) -> str:
        return self.case.case_id

    @property
    def succeeded(self) -> bool:
        return self.baseline.succeeded and self.candidate.succeeded


@dataclass(frozen=True)
class NormalizedReplayMembers:
    members: tuple[NormalizedReplayMember, ...]
    failure_events: tuple[ReplayFailureEvent, ...] = ()
    missing_case_ids: tuple[str, ...] = ()
    duplicate_case_ids: tuple[str, ...] = ()
    unexpected_case_ids: tuple[str, ...] = ()
    request_mismatch_case_ids: tuple[str, ...] = ()
    intentionally_unadmitted_case_ids: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.failure_events


def _member_request_mismatch_fields(
    *,
    root_request: CandidateReplayRequest,
    member_request: CandidateReplayRequest,
    case: EvalCase,
    member_count: int,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    derived_values = {
        "task_id": case.case_id,
        "task_input": _adapted_task_input(root_request, case),
        "task_input_fingerprint": _adapted_task_input_fingerprint(
            root_request,
            case,
        ),
        "baseline_replay_dir": _member_baseline_replay_dir(
            root_request.baseline_replay_dir,
            case.case_id,
        ),
        "baseline_repetitions": _distributed_member_repetitions(
            root_request.baseline_repetitions,
            member_count=member_count,
        ),
        "candidate_repetitions": _distributed_member_repetitions(
            root_request.candidate_repetitions,
            member_count=member_count,
        ),
    }
    for request_field in dataclass_fields(CandidateReplayRequest):
        field_name = request_field.name
        expected = derived_values.get(field_name, getattr(root_request, field_name))
        if (
            field_name == "baseline_replay_dir"
            and root_request.resume_replay_dir is not None
            and member_request.resume_replay_dir
            == root_request.resume_replay_dir
            and member_request.baseline_replay_dir is None
        ):
            # Completed pairs are cloned into the new run and their local
            # request deliberately drops the source cache pointer.  Treat that
            # materialized form as equivalent only inside the exact frozen
            # resume request; every semantic member field is still checked.
            expected = None
        if (
            root_request.resume_replay_dir is not None
            and member_request.resume_replay_dir
            == root_request.resume_replay_dir
            and field_name in {"adaptation_fingerprint", "replay_adaptation"}
            and _resume_adaptation_is_semantically_compatible(
                root_request,
                member_request,
            )
        ):
            expected = getattr(member_request, field_name)
        if to_json_dict(getattr(member_request, field_name)) != to_json_dict(expected):
            mismatches.append(field_name)
    return tuple(sorted(set(mismatches)))


def _normalization_failure(
    *, code: str, summary: str, diagnostics: Mapping[str, Any]
) -> ReplayFailureEvent:
    return ReplayFailureEvent(
        code=code,
        owner=FailureOwner.FRAMEWORK,
        stage=FailureStage.RESULT_NORMALIZATION,
        scope=FailureScope.CANDIDATE,
        repairable=False,
        category="replay_result_contract",
        summary=summary,
        diagnostics=diagnostics,
    )


def normalize_replay_members(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
) -> NormalizedReplayMembers:
    """Normalize legacy/new results to dataset-ordered member records.

    Structural backend violations become typed framework events and therefore
    fail closed without silently changing cardinality.
    """

    replayable_cases = tuple(
        case for case in dataset.cases if _is_replayable_user_task_case(case)
    )
    cases_by_id = {case.case_id: case for case in replayable_cases}
    events: list[ReplayFailureEvent] = []
    missing: list[str] = []
    duplicates: list[str] = []
    unexpected: list[str] = []
    mismatches: list[str] = []
    mismatch_fields: dict[str, tuple[str, ...]] = {}
    root_request = getattr(replay_result, "request", None)
    raw_measurement_decision = getattr(replay_result, "measurement_decision", None)
    measurement_decision_kind = str(
        raw_measurement_decision.get("kind")
        if isinstance(raw_measurement_decision, Mapping)
        else ""
    )
    authoritative_early_stop = bool(
        isinstance(root_request, CandidateReplayRequest)
        and root_request.measurement_plan is not None
        and measurement_decision_kind
        in {
            AdaptiveDecisionKind.STOP_CONFIDENT_POSITIVE.value,
            AdaptiveDecisionKind.STOP_NEGATIVE.value,
            AdaptiveDecisionKind.STOP_REGRESSION.value,
            AdaptiveDecisionKind.STOP_FUTILITY.value,
            AdaptiveDecisionKind.STOP_INVALID_CONTROL.value,
            AdaptiveDecisionKind.STOP_FRAMEWORK_BLOCKED.value,
            AdaptiveDecisionKind.STOP_ZERO_YIELD.value,
            AdaptiveDecisionKind.STOP_INCONCLUSIVE.value,
            AdaptiveDecisionKind.MEASUREMENT_INCOMPLETE_CHECKPOINT.value,
            AdaptiveDecisionKind.MEASUREMENT_INCOMPLETE_CAMPAIGN_DEADLINE.value,
        }
    )
    intentionally_unadmitted: list[str] = []
    if isinstance(root_request, CandidateReplayRequest) and not (
        _has_authoritative_per_member_repetitions(root_request)
    ):
        legacy_migration = (
            root_request.repetition_semantics
            == _MIGRATED_DISTRIBUTED_REPETITION_SEMANTICS
        )
        events.append(
            _normalization_failure(
                code=(
                    "legacy_repetition_semantics_non_authoritative"
                    if legacy_migration
                    else "replay_artifact_non_authoritative"
                ),
                summary=(
                    "legacy distributed replay was migrated for inspection but "
                    "cannot authorize new replay or evaluation"
                    if legacy_migration
                    else "stored replay artifact failed the v3 authority contract"
                ),
                diagnostics={
                    "repetition_semantics": (
                        root_request.repetition_semantics
                    ),
                    "baseline_repetitions_per_member": (
                        root_request.baseline_repetitions
                    ),
                    "candidate_repetitions_per_member": (
                        root_request.candidate_repetitions
                    ),
                },
            )
        )
    # Normalization intentionally accepts backend-compatible replay objects,
    # including older/duck-typed implementations that predate the persisted
    # artifact failure carrier.
    events.extend(getattr(replay_result, "artifact_failure_events", ()))
    raw_members = replay_result.member_results
    if raw_members is None:
        if len(replayable_cases) == 1:
            only_case = replayable_cases[0]
            raw_members = (
                CandidateReplayMemberResult(
                    case_id=only_case.case_id,
                    request=replay_result.request,
                    baseline=replay_result.baseline,
                    candidate=replay_result.candidate,
                ),
            )
        else:
            raw_members = ()

    occurrences_by_case_id: dict[str, list[CandidateReplayMemberResult]] = {
        case.case_id: [] for case in replayable_cases
    }
    for member in raw_members:
        if member.case_id not in cases_by_id:
            unexpected.append(member.case_id)
            continue
        occurrences_by_case_id[member.case_id].append(member)
    unexpected = sorted(set(unexpected))
    normalized: list[NormalizedReplayMember] = []
    for case in replayable_cases:
        occurrences = occurrences_by_case_id[case.case_id]
        if not occurrences:
            if authoritative_early_stop:
                intentionally_unadmitted.append(case.case_id)
            else:
                missing.append(case.case_id)
            continue
        occurrence_mismatch_fields = tuple(
            sorted(
                {
                    field_name
                    for member in occurrences
                    for field_name in _member_request_mismatch_fields(
                        root_request=replay_result.request,
                        member_request=member.request,
                        case=case,
                        member_count=len(replayable_cases),
                    )
                }
            )
        )
        if len(occurrences) > 1:
            duplicates.append(case.case_id)
        if occurrence_mismatch_fields:
            mismatches.append(case.case_id)
            mismatch_fields[case.case_id] = occurrence_mismatch_fields
        if len(occurrences) != 1 or occurrence_mismatch_fields:
            continue
        member = occurrences[0]
        normalized.append(
            NormalizedReplayMember(
                case=case,
                request=member.request,
                baseline=member.baseline,
                candidate=member.candidate,
            )
        )
    anomaly_groups = (
        (
            "missing_replay_member",
            missing,
            "backend omitted dataset replay members",
            {},
        ),
        (
            "duplicate_replay_member",
            duplicates,
            "backend returned duplicate replay members",
            {},
        ),
        (
            "unexpected_replay_member",
            unexpected,
            "backend returned members outside the dataset",
            {},
        ),
        (
            "replay_request_member_mismatch",
            mismatches,
            "member request violated the root/member request contract",
            {
                "fields": tuple(
                    sorted(
                        {
                            field_name
                            for fields_for_case in mismatch_fields.values()
                            for field_name in fields_for_case
                        }
                    )
                ),
                "member_fields": mismatch_fields,
            },
        ),
    )
    for code, case_ids, summary, extra_diagnostics in anomaly_groups:
        if case_ids:
            events.append(
                _normalization_failure(
                    code=code,
                    summary=summary,
                    diagnostics={
                        "case_ids": tuple(case_ids),
                        "count": len(case_ids),
                        **extra_diagnostics,
                    },
                )
            )
    return NormalizedReplayMembers(
        members=tuple(normalized),
        failure_events=tuple(events),
        missing_case_ids=tuple(missing),
        duplicate_case_ids=tuple(duplicates),
        unexpected_case_ids=tuple(unexpected),
        request_mismatch_case_ids=tuple(mismatches),
        intentionally_unadmitted_case_ids=tuple(intentionally_unadmitted),
    )


def iter_replay_members(
    *, dataset: SelfEvolveDataset, replay_result: CandidateReplayResult
) -> tuple[NormalizedReplayMember, ...]:
    return normalize_replay_members(dataset=dataset, replay_result=replay_result).members


def candidate_replay_is_comparable(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    require_adapted: bool = False,
    normalized: NormalizedReplayMembers | None = None,
) -> bool:
    normalized = normalized or normalize_replay_members(
        dataset=dataset,
        replay_result=replay_result,
    )
    if not normalized.valid:
        return False
    if not _candidate_replay_provenance_is_comparable(
        dataset,
        replay_result,
        require_adapted=require_adapted,
        normalized=normalized,
    ):
        return False
    coverage = candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=replay_result,
        normalized=normalized,
    )
    return (
        coverage["member_count"] > 0
        and coverage["comparable_pair_count"] == coverage["member_count"]
    )


def _candidate_replay_provenance_is_comparable(
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    *,
    require_adapted: bool,
    normalized: NormalizedReplayMembers,
) -> bool:
    if replay_result.request.adaptation_fingerprint is None:
        return not require_adapted
    if (
        replay_result.request.replay_adaptation is not None
        and not replay_result.request.replay_adaptation.ready
    ):
        return False
    pairs = tuple(
        (member.request, member.baseline, member.candidate)
        for member in normalized.members
    )
    for request, baseline, candidate in pairs:
        expected = {
            "adaptation_fingerprint": request.adaptation_fingerprint,
            "workspace_seed_fingerprint": request.workspace_seed_fingerprint,
            "task_input_fingerprint": request.task_input_fingerprint,
            "dataset_fingerprint": request.dataset_fingerprint,
            "baseline_skill_fingerprint": request.baseline_skill_fingerprint,
        }
        if any(value is None for value in expected.values()):
            return False
        for variant in (baseline, candidate):
            if any(variant.metrics.get(key) != value for key, value in expected.items()):
                return False
            if variant.metrics.get("adapter_determinism") != "deterministic":
                return False
        replay_capability = (
            request.replay_adaptation.replay_capability
            if request.replay_adaptation is not None
            else None
        )
        if replay_capability is not None:
            capability_expected = {
                "replay_capability_id": replay_capability.capability_id,
                "capability_package_fingerprint": (
                    replay_capability.capability_package_fingerprint
                ),
                "frozen_capability_fingerprint": replay_capability.fingerprint,
                "service_runtime_fingerprint": replay_capability.fingerprint,
            }
            for variant in (baseline, candidate):
                if any(
                    variant.metrics.get(key) != value
                    for key, value in capability_expected.items()
                ):
                    return False
            if replay_capability.services:
                declared_service_ids = {
                    service.service_id for service in replay_capability.services
                }
                baseline_service_ids = _service_logical_id_values(baseline)
                candidate_service_ids = _service_logical_id_values(candidate)
                if (
                    baseline_service_ids is None
                    or candidate_service_ids is None
                    or not baseline_service_ids
                    or baseline_service_ids != candidate_service_ids
                    or not baseline_service_ids <= declared_service_ids
                ):
                    return False
                for variant in (baseline, candidate):
                    if (
                        variant.metrics.get("service_startup_status") != "ready"
                        or variant.metrics.get("service_cleanup_status") != "stopped"
                    ):
                        return False
                baseline_endpoints = _service_endpoint_values(baseline)
                candidate_endpoints = _service_endpoint_values(candidate)
                exclusive_measurement = bool(
                    replay_result.request.measurement_isolation_decision
                    is not None
                    and replay_result.request.measurement_isolation_decision.safe_lane_count
                    == 1
                )
                if (
                    not baseline_endpoints
                    or not candidate_endpoints
                    or (
                        baseline_endpoints & candidate_endpoints
                        and not exclusive_measurement
                    )
                ):
                    return False
        baseline_workspaces = _isolated_workspace_paths(baseline)
        candidate_workspaces = _isolated_workspace_paths(candidate)
        if (
            not baseline_workspaces
            or not candidate_workspaces
            or set(baseline_workspaces) & set(candidate_workspaces)
        ):
            return False
    return True


def _service_logical_id_values(
    variant: ReplayVariantResult,
) -> set[str] | None:
    raw_values = variant.metrics.get("service_logical_ids_values")
    if isinstance(raw_values, list):
        values = raw_values
    else:
        direct = variant.metrics.get("service_logical_ids")
        values = [direct] if isinstance(direct, str) else []
    if not values:
        return set()
    decoded: list[set[str]] = []
    for value in values:
        if not isinstance(value, str):
            return None
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
        if (
            not isinstance(payload, list)
            or not payload
            or any(not isinstance(item, str) or not item for item in payload)
        ):
            return None
        logical_ids = set(payload)
        if len(logical_ids) != len(payload):
            return None
        decoded.append(logical_ids)
    first = decoded[0]
    if any(logical_ids != first for logical_ids in decoded[1:]):
        return None
    return first


def _isolated_workspace_paths(variant: ReplayVariantResult) -> tuple[str, ...]:
    direct = variant.metrics.get("isolated_workspace_path")
    raw_values = variant.metrics.get("isolated_workspace_path_values")
    if isinstance(raw_values, list):
        values = tuple(value for value in raw_values if isinstance(value, str))
    elif isinstance(direct, str):
        values = (direct,)
    else:
        return ()
    repetition_count = variant.metrics.get("repetition_count", len(values))
    if not isinstance(repetition_count, (int, float)):
        return ()
    if int(repetition_count) != len(values) or len(set(values)) != len(values):
        return ()
    if any(not value.strip() or not Path(value).is_absolute() for value in values):
        return ()
    return values


def _service_endpoint_values(variant: ReplayVariantResult) -> set[str]:
    raw_values = variant.metrics.get("service_endpoint_values")
    if isinstance(raw_values, list):
        values = raw_values
    else:
        direct = variant.metrics.get("service_endpoint")
        values = [direct] if isinstance(direct, str) else []
    endpoints: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            endpoints.update(
                endpoint
                for endpoint in payload.values()
                if isinstance(endpoint, str) and endpoint.startswith("http://127.0.0.1:")
            )
    return endpoints


def candidate_replay_pair_coverage(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    normalized: NormalizedReplayMembers | None = None,
) -> dict[str, int]:
    normalized = normalized or normalize_replay_members(
        dataset=dataset,
        replay_result=replay_result,
    )

    strict_pair_count = 0
    task_failure_pair_count = 0
    infrastructure_failure_count = 0
    candidate_failure_count = 0
    baseline_execution_failure_count = 0
    candidate_execution_failure_count = 0
    blocked_variant_count = 0
    blocked_member_count = 0
    not_run_variant_count = 0
    owner_counts = {owner: 0 for owner in FailureOwner}
    for member in normalized.members:
        case = member.case
        baseline = member.baseline
        candidate = member.candidate
        member_blocked = False
        for variant in (baseline, candidate):
            physical_results = variant.repetition_results or (variant,)
            for physical_result in physical_results:
                if (
                    physical_result.status is ReplayExecutionStatus.FAILED
                    and physical_result.failure is not None
                ):
                    owner_counts[physical_result.failure.owner] += 1
                    if physical_result.failure.owner in {
                        FailureOwner.INFRASTRUCTURE,
                        FailureOwner.FRAMEWORK,
                    }:
                        infrastructure_failure_count += 1
            if variant.status is ReplayExecutionStatus.BLOCKED:
                blocked_variant_count += 1
                member_blocked = True
            elif variant.status is ReplayExecutionStatus.NOT_RUN:
                not_run_variant_count += 1
        if member_blocked:
            blocked_member_count += 1
        if baseline.status is ReplayExecutionStatus.FAILED:
            baseline_execution_failure_count += 1
        if candidate.status is ReplayExecutionStatus.FAILED:
            candidate_execution_failure_count += 1
            candidate_failure_count += 1
        if _replay_member_pair_is_comparable(case, baseline, candidate):
            if baseline.succeeded:
                strict_pair_count += 1
            else:
                task_failure_pair_count += 1
    member_count = sum(
        1 for case in dataset.cases if _is_replayable_user_task_case(case)
    )
    comparable_pair_count = strict_pair_count + task_failure_pair_count
    incomparable_pair_count = member_count - comparable_pair_count
    return {
        "member_count": member_count,
        "returned_member_count": len(normalized.members),
        "strict_pair_count": strict_pair_count,
        "task_failure_pair_count": task_failure_pair_count,
        "comparable_pair_count": comparable_pair_count,
        "incomparable_pair_count": incomparable_pair_count,
        "infrastructure_failure_count": infrastructure_failure_count,
        # Compatibility: only candidate variants whose execution actually
        # started and failed are counted. Blocked candidates are excluded.
        "candidate_failure_count": candidate_failure_count,
        "baseline_execution_failure_count": baseline_execution_failure_count,
        "candidate_execution_failure_count": candidate_execution_failure_count,
        "candidate_executed_count": sum(
            1 for member in normalized.members if member.candidate.executed
        ),
        "blocked_variant_count": blocked_variant_count,
        "blocked_member_count": blocked_member_count,
        "not_run_variant_count": not_run_variant_count,
        "missing_member_count": len(normalized.missing_case_ids),
        "intentionally_unadmitted_member_count": len(
            normalized.intentionally_unadmitted_case_ids
        ),
        "duplicate_member_count": len(normalized.duplicate_case_ids),
        "unexpected_member_count": len(normalized.unexpected_case_ids),
        "request_mismatch_count": len(normalized.request_mismatch_case_ids),
        "normalization_failure_count": len(normalized.failure_events),
        "candidate_owned_failure_count": owner_counts[FailureOwner.CANDIDATE],
        "task_owned_failure_count": owner_counts[FailureOwner.TASK],
        "infrastructure_owned_failure_count": owner_counts[FailureOwner.INFRASTRUCTURE],
        "framework_owned_failure_count": owner_counts[FailureOwner.FRAMEWORK]
        + len(normalized.failure_events),
    }


def _replay_member_pair_is_comparable(
    case: EvalCase,
    baseline: ReplayVariantResult,
    candidate: ReplayVariantResult,
) -> bool:
    if not candidate.succeeded:
        return False
    if baseline.succeeded:
        return True
    if baseline.failure is None or not (
        baseline.failure.owner is FailureOwner.TASK
        or (
            baseline.failure.owner is FailureOwner.CANDIDATE
            and baseline.failure.stage is FailureStage.TASK_ROLLOUT
        )
    ):
        return False
    trajectory, _ = _baseline_comparison_trajectory(case, baseline)
    return bool(trajectory)


def _replay_failure_outcome(failure: ReplayFailureEvent | None) -> str:
    if failure is None:
        return "infrastructure_failure"
    if _is_task_rollout_capability_failure(failure):
        return "candidate_failure"
    if failure.owner is FailureOwner.CANDIDATE:
        return "candidate_failure"
    if failure.owner is FailureOwner.TASK:
        return "task_failure"
    return "infrastructure_failure"


def _is_task_rollout_capability_failure(
    failure: ReplayFailureEvent | None,
) -> bool:
    return bool(
        isinstance(failure, ReplayFailureEvent)
        and failure.owner is FailureOwner.CANDIDATE
        and failure.stage is FailureStage.TASK_ROLLOUT
    )


def _baseline_failure_blocks_candidate(
    failure: ReplayFailureEvent | Mapping[str, Any] | None,
) -> bool:
    if failure is None:
        return True
    event = (
        failure
        if isinstance(failure, ReplayFailureEvent)
        else _failure_event_from_persisted_mapping(failure)
    )
    return not (
        event.owner is FailureOwner.TASK
        or (
            event.owner is FailureOwner.CANDIDATE
            and event.stage is FailureStage.TASK_ROLLOUT
        )
        or (
            event.owner is FailureOwner.FRAMEWORK
            and event.scope is FailureScope.MEMBER
            and event.stage is FailureStage.EVALUATION
        )
    )


def _baseline_invalid_for_measurement(result: ReplayVariantResult) -> bool:
    if result.status is ReplayExecutionStatus.SUCCEEDED:
        return False
    if result.status is not ReplayExecutionStatus.FAILED or result.failure is None:
        return True
    return result.failure.owner in {
        FailureOwner.FRAMEWORK,
        FailureOwner.INFRASTRUCTURE,
    } or (
        result.failure.owner is FailureOwner.CANDIDATE
        and result.failure.stage is not FailureStage.TASK_ROLLOUT
    )


def _blocked_variant_result(
    variant_id: str,
    *,
    blocked_by: ReplayFailureEvent,
) -> ReplayVariantResult:
    return ReplayVariantResult(
        variant_id=variant_id,
        status=ReplayExecutionStatus.BLOCKED,
        trajectory=[],
        blocked_by=(blocked_by,),
    )


def _execution_failure_event(
    failure: Mapping[str, Any] | ReplayFailureEvent | None,
    *,
    default_stage: FailureStage,
    service_preflight: bool = False,
) -> ReplayFailureEvent:
    if isinstance(failure, ReplayFailureEvent):
        return failure
    payload = dict(failure or {})
    legacy = ReplayFailureEvent.from_legacy_mapping(payload)
    owner = legacy.owner
    raw_owner = str(payload.get("failure_owner") or "")
    raw_scope = str(payload.get("failure_scope") or "")
    # Executor failures produced by the framework already cross a trusted native
    # boundary here.  Preserve a complete typed ownership projection instead of
    # degrading it through the legacy compatibility importer.  The strict tuple
    # requirement prevents an incomplete/prose-only legacy failure from gaining
    # run-wide stopping authority.
    if (
        payload.get("code")
        and isinstance(payload.get("repairable"), bool)
        and raw_owner in {
            FailureOwner.FRAMEWORK.value,
            FailureOwner.INFRASTRUCTURE.value,
        }
        and raw_scope in {
            FailureScope.MEMBER.value,
            FailureScope.SHARED_RUN.value,
        }
        and str(payload.get("outcome") or "") == f"{raw_owner}_failure"
    ):
        owner = FailureOwner(raw_owner)
    raw_stage = str(payload.get("failure_stage") or "")
    failure_type = str(payload.get("type") or "")
    if service_preflight:
        stage = FailureStage.CAPABILITY_PREFLIGHT
    elif raw_stage == FailureStage.TASK_ROLLOUT.value:
        stage = FailureStage.TASK_ROLLOUT
    elif raw_stage == FailureStage.EVIDENCE_FINALIZATION.value:
        stage = FailureStage.EVIDENCE_FINALIZATION
    elif legacy.owner is FailureOwner.CANDIDATE and failure_type in {
        "ReplayServiceProtocolError",
        "ReplayCapabilityError",
        "ReplayCapabilityPreflightError",
    }:
        stage = FailureStage.CAPABILITY_PREFLIGHT
    elif raw_stage == FailureStage.EVALUATION.value:
        stage = FailureStage.EVALUATION
    else:
        stage = default_stage
    if owner is FailureOwner.CANDIDATE:
        scope = (
            FailureScope.MEMBER
            if stage in {
                FailureStage.TASK_ROLLOUT,
                FailureStage.EVIDENCE_FINALIZATION,
            }
            else FailureScope.CANDIDATE
        )
    elif owner is FailureOwner.TASK:
        scope = FailureScope.MEMBER
    elif owner in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE} and (
        raw_scope in {
            FailureScope.MEMBER.value,
            FailureScope.SHARED_RUN.value,
        }
        and str(payload.get("outcome") or "") == f"{owner.value}_failure"
    ):
        scope = FailureScope(raw_scope)
    elif owner is FailureOwner.INFRASTRUCTURE:
        scope = FailureScope.SHARED_RUN
    elif stage is FailureStage.EVALUATION:
        scope = FailureScope.MEMBER
    else:
        # Unknown current failures fail the candidate closed but cannot acquire
        # run-wide stopping authority from prose alone.
        scope = FailureScope.CANDIDATE
    code = legacy.code
    if code == "legacy_unclassified_failure":
        code = "unclassified_replay_execution_failure"
    diagnostics = dict(legacy.diagnostics)
    # Physical execution evidence may be produced before causal ownership is
    # known.  Promote only bounded, typed termination fields into diagnostics so
    # paired replay can perform attribution without scraping compatibility prose.
    for key in (
        "completed_data_plane_operations",
        "termination_kind",
        "termination_budget_axis",
        "timeout_seconds",
        "max_steps",
        "max_tool_calls",
        "tool_calls_used",
        "terminal_synthesis_attempted",
        "evidence_phase",
    ):
        value = payload.get(key)
        if value is not None:
            diagnostics[key] = value
    return ReplayFailureEvent(
        event_id=f"replay-event-{uuid.uuid4().hex}",
        code=code,
        owner=owner,
        stage=stage,
        scope=scope,
        repairable=legacy.repairable,
        category=legacy.category,
        summary=legacy.summary,
        diagnostics=diagnostics,
        source=FailureEventSource.NATIVE,
        _compatibility=payload,
    )


def _baseline_comparison_trajectory(
    case: EvalCase,
    baseline: ReplayVariantResult,
) -> tuple[list[Mapping[str, Any]], str]:
    del case
    if baseline.trajectory:
        return list(baseline.trajectory), (
            "replay" if baseline.succeeded else "failed_replay"
        )
    if (
        (
            isinstance(baseline.failure, ReplayFailureEvent)
            and (
                baseline.failure.owner is FailureOwner.TASK
                or (
                    baseline.failure.owner is FailureOwner.CANDIDATE
                    and baseline.failure.stage is FailureStage.TASK_ROLLOUT
                )
            )
            and _has_replay_execution_evidence(baseline)
        )
        or _is_task_rollout_capability_failure(baseline.failure)
        or (
            _replay_failure_outcome(baseline.failure) == "task_failure"
            and _has_replay_execution_evidence(baseline)
        )
    ):
        failure_summary = sanitize_text(
            json.dumps(
                baseline.failure,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
            max_chars=_SYNTHETIC_EVIDENCE_EXCERPT_CHARS,
        )
        return [
            {
                "state": {"input": {"content": "Replay execution failed."}},
                "action": {
                    "content": "Replay failed before task completion.",
                    "is_agent_finished": True,
                },
                "reward": {
                    "status": "failed",
                    "failure": failure_summary,
                },
                "meta": {"trajectory_source": "task_failure"},
            }
        ], "replay_failure"
    return [], "unavailable"


def _has_replay_execution_evidence(result: ReplayVariantResult) -> bool:
    """Require evidence that an empty failure came from this replay execution."""

    if result.stdout_path or result.stderr_path:
        return True
    metrics = result.metrics
    return bool(
        isinstance(metrics, Mapping)
        and any(
            key in metrics
            for key in (
                "latency_ms",
                "repetition_count",
                "successful_repetition_count",
                "failed_repetition_count",
                *_REPLAY_PROVENANCE_METRIC_KEYS,
            )
        )
    )


def _trusted_task_response_usage_metrics(
    task_response: Mapping[str, Any] | None,
) -> dict[str, int | bool]:
    """Project usage only from the parent-attested complete call ledger."""

    if not isinstance(task_response, Mapping):
        return {}
    usage = task_response.get("llm_usage")
    if not isinstance(usage, Mapping):
        return {}
    call_count = usage.get("call_count")
    usage_call_count = usage.get("usage_call_count")
    total_tokens = usage.get("total_tokens")
    if (
        usage.get("schema_version") != "aworld.llm_usage_summary.v1"
        or usage.get("coverage_complete") is not True
        or usage.get("ledger_consistent") is not True
        or isinstance(call_count, bool)
        or not isinstance(call_count, int)
        or call_count <= 0
        or usage_call_count != call_count
        or isinstance(total_tokens, bool)
        or not isinstance(total_tokens, int)
        or total_tokens < 0
    ):
        return {}
    return {
        "total_tokens": total_tokens,
        "llm_usage_call_count": call_count,
        "llm_usage_coverage_complete": True,
    }


class CandidateReplayBackend(Protocol):
    async def replay_candidate(
        self,
        request: CandidateReplayRequest,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayResult:
        """Replay baseline/candidate variants and return their trajectories."""


class ReplayEvidenceDispositionKind(str, Enum):
    """Whether replay evidence is executed now or reused from a source run."""

    STORED_SOURCE_REUSE = "stored_source_reuse"


@dataclass(frozen=True)
class ReplayEvidenceReuseDisposition:
    kind: ReplayEvidenceDispositionKind
    source_run_id: str
    source_replay_path: str
    source_dataset_snapshot_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ReplayEvidenceDispositionKind(self.kind))
        for field_name in ("source_run_id", "source_replay_path"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"replay evidence reuse requires {field_name}")
        if (
            self.source_dataset_snapshot_fingerprint is not None
            and not self.source_dataset_snapshot_fingerprint.startswith("sha256:")
        ):
            raise ValueError(
                "replay evidence reuse dataset snapshot fingerprint must be sha256"
            )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind.value,
            "source_run_id": self.source_run_id,
            "source_replay_path": self.source_replay_path,
        }
        if self.source_dataset_snapshot_fingerprint is not None:
            payload["source_dataset_snapshot_fingerprint"] = (
                self.source_dataset_snapshot_fingerprint
            )
        return payload


@runtime_checkable
class CandidateReplayEvidenceReuseBackend(Protocol):
    """Backend that supplies immutable replay evidence without executing replay."""

    def replay_evidence_reuse_disposition(
        self,
    ) -> ReplayEvidenceReuseDisposition:
        """Describe the source evidence and its provenance."""

    async def reuse_replay_evidence(
        self,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayResult:
        """Return stored evidence without starting a replay execution."""


def load_candidate_replay_result(replay_dir: str | Path) -> CandidateReplayResult:
    """Load a previously materialized candidate replay result from disk."""
    root = Path(replay_dir).expanduser()
    request_payload = _load_json_object(root / "request.json")
    request = _candidate_replay_request_from_mapping(request_payload)
    lifecycle_is_per_member_v3 = _stored_lifecycles_use_per_member_v3(root)
    member_manifest_path = root / "members" / "manifest.json"
    if member_manifest_path.exists():
        member_manifest = _load_json_object(member_manifest_path)
        member_schema = str(member_manifest.get("schema_version") or "")
        authoritative_v3_manifest = member_schema == _MEMBER_REPLAY_SCHEMA_V3
        artifact_failures: list[ReplayFailureEvent] = []
        migration_required = (
            member_schema in _LEGACY_MEMBER_REPLAY_SCHEMAS
            or lifecycle_is_per_member_v3 is False
        )
        if authoritative_v3_manifest:
            if (
                member_manifest.get("repetition_semantics")
                != _PER_MEMBER_REPETITION_SEMANTICS
                or not _has_authoritative_per_member_repetitions(request)
            ):
                artifact_failures.append(
                    _replay_artifact_contract_failure(
                        code="replay_v3_manifest_contract_invalid",
                        summary=(
                            "stored v3 member replay is missing per-member "
                            "repetition semantics"
                        ),
                        diagnostics={
                            "manifest_repetition_semantics": member_manifest.get(
                                "repetition_semantics"
                            ),
                            "request_repetition_semantics": (
                                request.repetition_semantics
                            ),
                        },
                    )
                )
        elif member_schema not in _LEGACY_MEMBER_REPLAY_SCHEMAS:
            artifact_failures.append(
                _replay_artifact_contract_failure(
                    code="replay_member_manifest_schema_invalid",
                    summary="stored member replay manifest schema is unsupported",
                    diagnostics={"schema_version": member_schema},
                )
            )
        if authoritative_v3_manifest:
            artifact_failures.extend(
                _v3_lifecycle_contract_failures(
                    root / "baseline",
                    artifact_scope="root_baseline",
                    expected_variant_id="baseline",
                )
            )
            artifact_failures.extend(
                _v3_lifecycle_contract_failures(
                    root / _safe_path(request.candidate_id),
                    artifact_scope="root_candidate",
                    expected_variant_id=request.candidate_id,
                )
            )
        raw_members = member_manifest.get("members")
        if not isinstance(raw_members, list):
            raise ValueError("stored member replay manifest is missing members")
        member_results: list[CandidateReplayMemberResult] = []
        for raw_member in raw_members:
            if not isinstance(raw_member, Mapping):
                raise ValueError("stored member replay entry must be an object")
            case_id = str(raw_member.get("case_id") or "")
            relative_path = str(raw_member.get("path") or "")
            if not case_id or not relative_path:
                raise ValueError("stored member replay entry is missing case_id or path")
            expected_relative_path = _member_artifact_name(case_id)
            if relative_path != expected_relative_path:
                artifact_failures.append(
                    _replay_artifact_contract_failure(
                        code="replay_member_manifest_path_mismatch",
                        summary="stored member replay path does not match case_id",
                        diagnostics={
                            "case_id": case_id,
                            "path": relative_path,
                            "expected_path": expected_relative_path,
                        },
                    )
                )
                relative_path = expected_relative_path
            member_root = root / "members" / relative_path
            member_request = _candidate_replay_request_from_mapping(
                _load_json_object(member_root / "request.json")
            )
            if (
                authoritative_v3_manifest
                and not _has_authoritative_per_member_repetitions(member_request)
            ):
                artifact_failures.append(
                    _replay_artifact_contract_failure(
                        code="replay_v3_member_request_contract_invalid",
                        summary=(
                            "stored v3 member request is missing per-member "
                            "repetition semantics"
                        ),
                        diagnostics={
                            "case_id": case_id,
                            "repetition_semantics": (
                                member_request.repetition_semantics
                            ),
                        },
                    )
                )
            baseline_dir = (
                Path(member_request.baseline_replay_dir)
                if member_request.baseline_replay_dir
                else member_root / "baseline"
            )
            candidate_dir = member_root / _safe_path(request.candidate_id)
            member_lifecycle_states = (
                _stored_lifecycles_use_per_member_v3(baseline_dir),
                _stored_lifecycles_use_per_member_v3(candidate_dir),
            )
            if any(state is False for state in member_lifecycle_states):
                migration_required = True
            baseline = _load_variant_result_from_dir(
                baseline_dir,
                base_variant_id="baseline",
            )
            candidate_result = _load_variant_result_from_dir(
                candidate_dir,
                base_variant_id=request.candidate_id,
            )
            if authoritative_v3_manifest:
                baseline, failures = _validate_v3_member_variant_artifact(
                    baseline_dir,
                    result=baseline,
                    requested_repetitions=member_request.baseline_repetitions,
                    case_id=case_id,
                    variant_role="baseline",
                    expected_variant_id="baseline",
                )
                artifact_failures.extend(failures)
                candidate_result, failures = _validate_v3_member_variant_artifact(
                    candidate_dir,
                    result=candidate_result,
                    requested_repetitions=member_request.candidate_repetitions,
                    case_id=case_id,
                    variant_role="candidate",
                    expected_variant_id=request.candidate_id,
                )
                artifact_failures.extend(failures)
            manifest_statuses = (
                ("baseline", raw_member.get("baseline_status"), baseline.status),
                (
                    "candidate",
                    raw_member.get("candidate_status"),
                    candidate_result.status,
                ),
            )
            if authoritative_v3_manifest:
                for variant_role, manifest_status, loaded_status in manifest_statuses:
                    if manifest_status != loaded_status.value:
                        artifact_failures.append(
                            _replay_artifact_contract_failure(
                                code="replay_v3_manifest_status_mismatch",
                                summary=(
                                    "stored v3 manifest status does not match "
                                    "the member lifecycle"
                                ),
                                diagnostics={
                                    "case_id": case_id,
                                    "variant_role": variant_role,
                                    "manifest_status": manifest_status,
                                    "lifecycle_status": loaded_status.value,
                                },
                            )
                        )
            member_results.append(
                CandidateReplayMemberResult(
                    case_id=case_id,
                    request=member_request,
                    baseline=baseline,
                    candidate=candidate_result,
                )
            )
        members = tuple(member_results)
        if migration_required:
            members = tuple(
                replace(
                    member,
                    request=replace(
                        member.request,
                        repetition_semantics=(
                            _MIGRATED_DISTRIBUTED_REPETITION_SEMANTICS
                        ),
                    ),
                )
                for member in members
            )
            # v1/v2 root counts were divided over members, and any v2
            # lifecycle remains non-authoritative even beside a newer
            # manifest.  Member requests are the faithful per-member view;
            # retain a migration marker so inspection cannot become reuse.
            baseline_counts = {
                member.request.baseline_repetitions for member in members
            }
            candidate_counts = {
                member.request.candidate_repetitions for member in members
            }
            if len(baseline_counts) != 1 or len(candidate_counts) != 1:
                raise ValueError(
                    "stored distributed member replay has inconsistent repetition counts"
                )
            request = replace(
                request,
                baseline_repetitions=next(iter(baseline_counts)),
                candidate_repetitions=next(iter(candidate_counts)),
                repetition_semantics=(
                    _MIGRATED_DISTRIBUTED_REPETITION_SEMANTICS
                ),
            )
        elif artifact_failures:
            request = replace(
                request,
                repetition_semantics=(
                    _NON_AUTHORITATIVE_V3_REPETITION_SEMANTICS
                ),
            )
            members = tuple(
                replace(
                    member,
                    request=replace(
                        member.request,
                        repetition_semantics=(
                            _NON_AUTHORITATIVE_V3_REPETITION_SEMANTICS
                        ),
                    ),
                )
                for member in members
            )
        baseline = _aggregate_member_variant_results(
            base_variant_id="baseline",
            members=members,
            select=lambda member: member.baseline,
            artifact_dir=root / "baseline",
            persist=False,
        )
        candidate = _aggregate_member_variant_results(
            base_variant_id=request.candidate_id,
            members=members,
            select=lambda member: member.candidate,
            artifact_dir=root / _safe_path(request.candidate_id),
            persist=False,
        )
        if authoritative_v3_manifest:
            artifact_failures.extend(
                _validate_v3_root_aggregate_artifact(
                    root / "baseline",
                    expected=baseline,
                    variant_role="baseline",
                )
            )
            artifact_failures.extend(
                _validate_v3_root_aggregate_artifact(
                    root / _safe_path(request.candidate_id),
                    expected=candidate,
                    variant_role="candidate",
                )
            )
        if (
            artifact_failures
            and not migration_required
            and _has_authoritative_per_member_repetitions(request)
        ):
            request = replace(
                request,
                repetition_semantics=(
                    _NON_AUTHORITATIVE_V3_REPETITION_SEMANTICS
                ),
            )
            members = tuple(
                replace(
                    member,
                    request=replace(
                        member.request,
                        repetition_semantics=(
                            _NON_AUTHORITATIVE_V3_REPETITION_SEMANTICS
                        ),
                    ),
                )
                for member in members
            )
        return CandidateReplayResult(
            request=request,
            baseline=baseline,
            candidate=candidate,
            member_results=members,
            artifact_failure_events=tuple(artifact_failures),
        )
    baseline = _load_variant_result_from_dir(root / "baseline", base_variant_id="baseline")
    candidate = _load_variant_result_from_dir(
        root / _safe_path(request.candidate_id),
        base_variant_id=request.candidate_id,
    )
    artifact_failures: tuple[ReplayFailureEvent, ...] = ()
    if lifecycle_is_per_member_v3 is False:
        request = replace(
            request,
            repetition_semantics=_MIGRATED_DISTRIBUTED_REPETITION_SEMANTICS,
        )
    elif _has_authoritative_per_member_repetitions(request):
        artifact_failures = (
            _replay_artifact_contract_failure(
                code="replay_v3_member_manifest_missing",
                summary=(
                    "authoritative v3 replay requires an explicit member manifest"
                ),
                diagnostics={"replay_dir": str(root)},
            ),
        )
        request = replace(
            request,
            repetition_semantics=_NON_AUTHORITATIVE_V3_REPETITION_SEMANTICS,
        )
    return CandidateReplayResult(
        request=request,
        baseline=baseline,
        candidate=candidate,
        artifact_failure_events=artifact_failures,
    )


def _stored_lifecycles_use_per_member_v3(root: Path) -> bool | None:
    """Return v3 proof, explicit legacy evidence, or no lifecycle signal."""

    lifecycle_paths = tuple(root.rglob("lifecycle.json"))
    if not lifecycle_paths:
        return None
    for path in lifecycle_paths:
        lifecycle = _load_json_object(path)
        if (
            lifecycle.get("schema_version") != _REPLAY_LIFECYCLE_SCHEMA_V3
            or lifecycle.get("repetition_semantics")
            != _PER_MEMBER_REPETITION_SEMANTICS
        ):
            return False
    return True


def _replay_artifact_contract_failure(
    *,
    code: str,
    summary: str,
    diagnostics: Mapping[str, Any],
) -> ReplayFailureEvent:
    return _normalization_failure(
        code=code,
        summary=summary,
        diagnostics=diagnostics,
    )


def _v3_lifecycle_contract_failures(
    variant_dir: Path,
    *,
    artifact_scope: str,
    expected_variant_id: str,
) -> tuple[ReplayFailureEvent, ...]:
    lifecycle_path = variant_dir / "lifecycle.json"
    diagnostics: dict[str, Any] = {
        "artifact_scope": artifact_scope,
        "artifact_dir": str(variant_dir),
        "expected_variant_id": expected_variant_id,
    }
    if not lifecycle_path.is_file():
        return (
            _replay_artifact_contract_failure(
                code="replay_v3_lifecycle_missing",
                summary="authoritative v3 replay lifecycle is missing",
                diagnostics=diagnostics,
            ),
        )
    try:
        lifecycle = _load_json_object(lifecycle_path)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        return (
            _replay_artifact_contract_failure(
                code="replay_v3_lifecycle_invalid",
                summary="authoritative v3 replay lifecycle is unreadable",
                diagnostics={**diagnostics, "error_type": type(exc).__name__},
            ),
        )
    mismatches: dict[str, Any] = {}
    if lifecycle.get("schema_version") != _REPLAY_LIFECYCLE_SCHEMA_V3:
        mismatches["schema_version"] = lifecycle.get("schema_version")
    if lifecycle.get("repetition_semantics") != _PER_MEMBER_REPETITION_SEMANTICS:
        mismatches["repetition_semantics"] = lifecycle.get(
            "repetition_semantics"
        )
    if lifecycle.get("variant_id") != expected_variant_id:
        mismatches["variant_id"] = lifecycle.get("variant_id")
    if not mismatches:
        return ()
    return (
        _replay_artifact_contract_failure(
            code="replay_v3_lifecycle_contract_invalid",
            summary="stored replay lifecycle violates the v3 authority contract",
            diagnostics={**diagnostics, "mismatches": mismatches},
        ),
    )


def _validate_v3_member_variant_artifact(
    variant_dir: Path,
    *,
    result: ReplayVariantResult,
    requested_repetitions: int,
    case_id: str,
    variant_role: str,
    expected_variant_id: str,
) -> tuple[ReplayVariantResult, tuple[ReplayFailureEvent, ...]]:
    failures: list[ReplayFailureEvent] = list(
        _v3_lifecycle_contract_failures(
            variant_dir,
            artifact_scope=f"member_{variant_role}",
            expected_variant_id=expected_variant_id,
        )
    )
    repetition_dirs = tuple(_stored_repetition_dirs(variant_dir))
    actual_child_names = tuple(path.name for path in repetition_dirs)
    expected_child_names = (
        tuple(str(index) for index in range(1, requested_repetitions + 1))
        if result.executed and requested_repetitions > 1
        else ()
    )
    duplicate_indexes = tuple(
        sorted(
            index
            for index in {int(name) for name in actual_child_names}
            if sum(int(name) == index for name in actual_child_names) > 1
        )
    )
    if actual_child_names != expected_child_names or duplicate_indexes:
        failures.append(
            _replay_artifact_contract_failure(
                code="replay_v3_repetition_children_invalid",
                summary=(
                    "stored v3 repetition children do not match the member request"
                ),
                diagnostics={
                    "case_id": case_id,
                    "variant_role": variant_role,
                    "requested_repetitions": requested_repetitions,
                    "expected_children": expected_child_names,
                    "actual_children": actual_child_names,
                    "duplicate_indexes": duplicate_indexes,
                },
            )
        )
    for index, child_dir in enumerate(repetition_dirs, start=1):
        expected_child_variant_id = (
            f"{expected_variant_id}-{index}"
            if requested_repetitions > 1
            else expected_variant_id
        )
        failures.extend(
            _v3_lifecycle_contract_failures(
                child_dir,
                artifact_scope=f"member_{variant_role}_repetition",
                expected_variant_id=expected_child_variant_id,
            )
        )

    physical_results = (
        result.repetition_results
        if requested_repetitions > 1
        else ((result,) if result.executed else ())
    )
    actual_counts = {
        "repetition_count": len(physical_results),
        "successful_repetition_count": sum(
            item.status is ReplayExecutionStatus.SUCCEEDED
            for item in physical_results
        ),
        "failed_repetition_count": sum(
            item.status is ReplayExecutionStatus.FAILED
            for item in physical_results
        ),
        "blocked_repetition_count": sum(
            item.status is ReplayExecutionStatus.BLOCKED
            for item in physical_results
        ),
        "not_run_repetition_count": sum(
            item.status is ReplayExecutionStatus.NOT_RUN
            for item in physical_results
        ),
    }
    aggregate_mismatches = {
        key: {"reported": result.metrics.get(key), "actual": actual}
        for key, actual in actual_counts.items()
        if (result.executed or key in result.metrics)
        and result.metrics.get(key) != actual
    }
    expected_actual_count = requested_repetitions if result.executed else 0
    if actual_counts["repetition_count"] != expected_actual_count:
        aggregate_mismatches["requested_repetitions"] = {
            "reported": requested_repetitions,
            "actual": actual_counts["repetition_count"],
        }
    if aggregate_mismatches:
        failures.append(
            _replay_artifact_contract_failure(
                code="replay_v3_repetition_count_mismatch",
                summary=(
                    "stored v3 aggregate counts do not match physical repetitions"
                ),
                diagnostics={
                    "case_id": case_id,
                    "variant_role": variant_role,
                    "mismatches": aggregate_mismatches,
                },
            )
        )
    canonical_metrics = {**dict(result.metrics), **actual_counts}
    return (
        replace(result, metrics=canonical_metrics),
        tuple(failures),
    )


def _validate_v3_root_aggregate_artifact(
    variant_dir: Path,
    *,
    expected: ReplayVariantResult,
    variant_role: str,
) -> tuple[ReplayFailureEvent, ...]:
    failures: list[ReplayFailureEvent] = []
    try:
        lifecycle = _load_json_object(variant_dir / "lifecycle.json")
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        # Exact lifecycle diagnostics are emitted separately.
        return ()
    lifecycle_status = lifecycle.get("status")
    if lifecycle_status != expected.status.value:
        failures.append(
            _replay_artifact_contract_failure(
                code="replay_v3_root_lifecycle_status_mismatch",
                summary=(
                    "stored v3 root lifecycle status does not match its members"
                ),
                diagnostics={
                    "variant_role": variant_role,
                    "lifecycle_status": lifecycle_status,
                    "member_aggregate_status": expected.status.value,
                },
            )
        )
    aggregate_path = variant_dir / "aggregate_metrics.json"
    try:
        aggregate_metrics = _load_json_object(aggregate_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError) as exc:
        return (
            *failures,
            _replay_artifact_contract_failure(
                code="replay_v3_root_aggregate_metrics_missing",
                summary="stored v3 root aggregate metrics are missing or unreadable",
                diagnostics={
                    "variant_role": variant_role,
                    "error_type": type(exc).__name__,
                },
            ),
        )
    generated_keys = (
        "member_count",
        "successful_member_count",
        "failed_member_count",
        "blocked_member_count",
        "not_run_member_count",
        "repetition_count",
        "successful_repetition_count",
        "failed_repetition_count",
    )
    mismatches = {
        key: {
            "reported": aggregate_metrics.get(key),
            "actual": expected.metrics.get(key),
        }
        for key in generated_keys
        if aggregate_metrics.get(key) != expected.metrics.get(key)
    }
    if mismatches:
        failures.append(
            _replay_artifact_contract_failure(
                code="replay_v3_root_aggregate_metrics_mismatch",
                summary=(
                    "stored v3 root aggregate metrics do not match member results"
                ),
                diagnostics={
                    "variant_role": variant_role,
                    "mismatches": mismatches,
                },
            )
        )
    return tuple(failures)


@dataclass(frozen=True)
class ReplayExecutionRequest:
    variant_id: str
    task_id: str
    candidate_id: str
    workspace_root: str
    task_input: Any
    task_text: str
    skill_root: str | None
    artifact_dir: str
    skill_names: tuple[str, ...] = ()
    agent: str | None = None
    timeout_seconds: float | None = None
    max_steps: int | None = None
    max_tool_calls: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    adaptation_fingerprint: str | None = None
    support_fingerprint: str | None = None
    timeout_envelope_fingerprint: str | None = None
    workspace_seed_fingerprint: str | None = None
    task_input_fingerprint: str | None = None
    dataset_fingerprint: str | None = None
    baseline_skill_fingerprint: str | None = None
    expected_skill_package_fingerprint: str | None = None
    adapter_determinism: str | None = None
    isolated_workspace_path: str | None = None
    replay_capability_id: str | None = None
    capability_package_fingerprint: str | None = None
    frozen_capability_fingerprint: str | None = None
    service_runtime_fingerprint: str | None = None
    service_logical_ids: str | None = None
    service_endpoint: str | None = None
    service_startup_status: str | None = None
    framework_endpoint_bindings: Mapping[str, str] = field(default_factory=dict)
    evidence_policy_mode: str = "legacy"
    measurement_plan_fingerprint: str | None = None
    measurement_work_unit: MeasurementWorkUnitV1 | None = None
    measurement_evidence_policy_profile: EvidencePolicyProfileV2 | None = None
    isolation_grant_fingerprint: str | None = None
    lane_materialization_fingerprint: str | None = None
    evidence_finalization_timeout_seconds: float | None = None
    variant_role: str | None = None

    def __post_init__(self) -> None:
        if self.variant_role not in {None, "baseline", "candidate"}:
            raise ValueError("unsupported replay execution variant role")
        if self.evidence_policy_mode not in _REPLAY_EVIDENCE_POLICY_MODES:
            raise ValueError("unsupported replay evidence_policy_mode")
        measurement_values = (
            self.measurement_plan_fingerprint,
            self.measurement_work_unit,
            self.measurement_evidence_policy_profile,
            self.lane_materialization_fingerprint,
        )
        if any(item is not None for item in measurement_values):
            if not all(item is not None for item in measurement_values):
                raise ValueError("runtime measurement identity must be complete")
            if self.evidence_policy_mode != "required":
                raise ValueError("runtime measurement identity requires evidence policy v2")
            assert self.measurement_work_unit is not None
            assert self.measurement_evidence_policy_profile is not None
            if (
                self.measurement_work_unit.measurement_plan_fingerprint
                != self.measurement_plan_fingerprint
                or self.measurement_work_unit.evidence_policy_fingerprint
                != self.measurement_evidence_policy_profile.fingerprint
            ):
                raise ValueError("runtime measurement identity drifted")
            if (
                isinstance(self.evidence_finalization_timeout_seconds, bool)
                or not isinstance(
                    self.evidence_finalization_timeout_seconds, (int, float)
                )
                or not math.isfinite(
                    float(self.evidence_finalization_timeout_seconds)
                )
                or float(self.evidence_finalization_timeout_seconds) <= 0
            ):
                raise ValueError(
                    "runtime measurement finalization timeout must be positive"
                )
        elif self.evidence_finalization_timeout_seconds is not None:
            raise ValueError(
                "finalization timeout requires runtime measurement identity"
            )


def _trusted_skill_activation_metrics(
    request: ReplayExecutionRequest,
    task_response: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project activation only from a signed child TaskResponse.

    Request-side skill selection is intent, not proof. The attestation is
    accepted only when the resolver-reported package is contained by the
    mounted skill root and its bytes still match the frozen candidate digest.
    """

    raw_evidence = (
        task_response.get("skill_activation_evidence")
        if isinstance(task_response, Mapping)
        else None
    )
    activation_evidence = (
        [dict(item) for item in raw_evidence if isinstance(item, Mapping)]
        if isinstance(raw_evidence, list)
        else []
    )
    expected_root: Path | None = None
    if request.skill_root:
        try:
            expected_root = Path(request.skill_root).expanduser().resolve()
        except (OSError, RuntimeError):
            expected_root = None

    def activation_matches(item: Mapping[str, Any]) -> bool:
        canonical_root = item.get("canonical_skill_root")
        if not isinstance(canonical_root, str) or expected_root is None:
            return False
        try:
            observed_root = Path(canonical_root).expanduser().resolve()
            observed_root.relative_to(expected_root)
            observed_fingerprint = fingerprint_skill_package(observed_root)
        except (OSError, RuntimeError, ValueError):
            return False
        return bool(
            item.get("skill_name") in request.skill_names
            and item.get("package_fingerprint") == observed_fingerprint
            and item.get("package_fingerprint")
            == request.expected_skill_package_fingerprint
            and item.get("source")
            == "aworld_cli_skill_activation_resolver"
        )

    observed_activation = next(
        (item for item in activation_evidence if activation_matches(item)),
        None,
    )
    skill_activation_attested = observed_activation is not None
    return {
        "skill_activation_attested": skill_activation_attested,
        "activated_skill_names": sorted(
            {
                str(item.get("skill_name"))
                for item in activation_evidence
                if isinstance(item.get("skill_name"), str)
            }
        ),
        "activated_skill_root": (
            observed_activation.get("canonical_skill_root")
            if observed_activation is not None
            else None
        ),
        "activated_skill_package_fingerprint": (
            observed_activation.get("package_fingerprint")
            if observed_activation is not None
            else None
        ),
        "skill_activation_attestation_source": (
            "aworld_cli_skill_activation_resolver"
            if skill_activation_attested
            else None
        ),
        "skill_activation_evidence_count": len(activation_evidence),
    }


@dataclass(frozen=True)
class _ReplayEvidenceTrustContext:
    profile: EvidencePolicyProfileV2
    writer: FrameworkEvidenceWriterAttestationV2
    producer_capabilities: tuple[ProducerRegistrationCapabilityV2, ...]
    work_unit_fingerprint: str
    signing_key: bytes
    trusted_root: Path
    measurement_plan_fingerprint: str | None = None
    measurement_work_unit_id: str | None = None
    isolation_grant_fingerprint: str | None = None
    lane_materialization_fingerprint: str | None = None


@dataclass(frozen=True)
class ReplayExecutionResult:
    status: str
    trajectory: list[Mapping[str, Any]]
    metrics: Mapping[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    failure: Mapping[str, Any] | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


ReplayExecutor = Callable[[ReplayExecutionRequest], Any]


@dataclass(frozen=True)
class ReplayRepetitionTaskInput:
    backend: "AWorldCliCandidateReplayBackend"
    request: CandidateReplayRequest
    variant_id: str
    skill_root: str | None
    artifact_dir: Path
    measurement_arm: MeasurementArm
    repetition_id: int
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None

    async def execute(self) -> ReplayVariantResult:
        kwargs: dict[str, Any] = {
            "variant_id": self.variant_id,
            "skill_root": self.skill_root,
            "artifact_dir": self.artifact_dir,
            "progress_callback": self.progress_callback,
        }
        if self.request.measurement_plan is not None:
            kwargs.update(
                measurement_arm=self.measurement_arm,
                repetition_id=self.repetition_id,
            )
        return await self.backend._run_variant_with_evidence_retries(
            self.request,
            **kwargs,
        )


def _replay_resource_claims(
    request: CandidateReplayRequest,
) -> tuple[TaskResourceClaim, ...]:
    adaptation = request.replay_adaptation
    if adaptation is None:
        return ()
    bindings = adaptation.case(request.task_id).bindings
    claims_by_key: dict[str, bool] = {}
    for raw_binding in bindings:
        binding = validate_replay_binding_concurrency(raw_binding)
        if binding.concurrency_mode == "isolated":
            continue
        if binding.resource_key is None:  # pragma: no cover - validator fills it
            raise ValueError("non-isolated replay binding requires resource_key")
        exclusive = binding.concurrency_mode == "exclusive"
        claims_by_key[binding.resource_key] = (
            claims_by_key.get(binding.resource_key, False) or exclusive
        )
    return tuple(
        TaskResourceClaim(key=key, exclusive=exclusive)
        for key, exclusive in sorted(claims_by_key.items())
    )


def _legacy_member_baseline_concurrency(
    requests: Sequence[CandidateReplayRequest],
    *,
    concurrency_policy: SelfEvolveConcurrencyPolicy,
) -> int:
    """Allow one speculative control only when every runtime is isolated.

    Legacy replay does not have the lane materializer used by measurement v2,
    but its per-member runtime still creates distinct workspace, HOME, endpoint,
    and artifact roots.  We therefore overlap adjacent controls only when the
    compiled adaptation explicitly proves deterministic isolated bindings and,
    when present, an isolated frozen capability.  Any incomplete or shared
    declaration falls back to the historical serial schedule.
    """

    if len(requests) < 2:
        return 1
    for request in requests:
        # Repetition batches already consume the replay concurrency budget.
        # Pair-level overlap is enabled only when each arm has one work item,
        # so nested schedulers cannot oversubscribe the global limit.
        if (
            request.baseline_repetitions != 1
            or request.candidate_repetitions != 1
        ):
            return 1
        adaptation = request.replay_adaptation
        if adaptation is None or not adaptation.ready:
            return 1
        capability = adaptation.replay_capability
        if capability is not None and (
            not capability.ready
            or not capability.deterministic
            or capability.concurrency_mode != "isolated"
            or capability.resource_key is not None
        ):
            return 1
        try:
            case = adaptation.case(request.task_id)
        except KeyError:
            return 1
        if case.readiness != "ready":
            return 1
        for raw_binding in case.bindings:
            try:
                binding = validate_replay_binding_concurrency(raw_binding)
            except ValueError:
                return 1
            if (
                not binding.deterministic
                or binding.concurrency_mode != "isolated"
                or binding.resource_key is not None
            ):
                return 1
    return min(
        2,
        concurrency_policy.effective_limit("replay", item_count=len(requests)),
    )


@dataclass
class _ReplayServiceProcess:
    process: subprocess.Popen[Any]
    stdout_handle: Any
    stderr_handle: Any
    service_id: str
    stdout_path: Path
    stderr_path: Path


@dataclass
class _ReplayServiceSession:
    endpoints: Mapping[str, str]
    environment: Mapping[str, str]
    processes: list[_ReplayServiceProcess]
    private_root: Path
    diagnostics_root: Path
    monitor_task: asyncio.Task[None] | None = None
    disk_limit_error: str | None = None

    async def stop(self) -> None:
        errors: list[str] = []
        for item in reversed(self.processes):
            process = item.process
            if process.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
                except Exception as exc:
                    errors.append(f"terminate:{type(exc).__name__}:{exc}")
        for item in reversed(self.processes):
            process = item.process
            if process.poll() is None:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(process.wait),
                        timeout=3.0,
                    )
                except asyncio.TimeoutError:
                    try:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(process.wait),
                            timeout=3.0,
                        )
                    except asyncio.TimeoutError:
                        errors.append(f"wait_timeout:pid={process.pid}")
                    except Exception as exc:
                        errors.append(f"wait:{type(exc).__name__}:{exc}")
                except Exception as exc:
                    errors.append(f"stop:{type(exc).__name__}:{exc}")
            try:
                item.stdout_handle.close()
                item.stderr_handle.close()
            except Exception as exc:
                errors.append(f"close:{type(exc).__name__}:{exc}")
        if self.monitor_task is not None:
            self.monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.monitor_task
        for item in self.processes:
            service_dir = self.diagnostics_root / _safe_path(item.service_id)
            service_dir.mkdir(parents=True, exist_ok=True)
            for source, name in (
                (item.stdout_path, "stdout.txt"),
                (item.stderr_path, "stderr.txt"),
            ):
                try:
                    if source.is_file():
                        shutil.copy2(source, service_dir / name)
                except Exception as exc:
                    errors.append(f"diagnostics:{type(exc).__name__}:{exc}")
            protocol_trace = (
                self.private_root
                / "scratch"
                / _safe_path(item.service_id)
                / _REPLAY_SERVICE_PROTOCOL_TRACE_NAME
            )
            try:
                _preserve_replay_service_protocol_trace(
                    protocol_trace,
                    service_dir / "protocol_trace.log",
                )
            except Exception as exc:
                errors.append(
                    f"protocol_trace_diagnostics:{type(exc).__name__}:{exc}"
                )
        shutil.rmtree(self.private_root, ignore_errors=True)
        if self.disk_limit_error is not None:
            errors.append(self.disk_limit_error)
        if errors:
            raise RuntimeError("replay service cleanup failed: " + "; ".join(errors))


def _preserve_replay_service_protocol_trace(
    source: Path,
    destination: Path,
) -> bool:
    """Preserve a bounded, sanitized candidate-owned interaction summary."""

    try:
        if source.is_symlink() or not source.is_file():
            return False
        with source.open("rb") as handle:
            size = source.stat().st_size
            if size > _MAX_REPLAY_SERVICE_PROTOCOL_TRACE_BYTES:
                handle.seek(-_MAX_REPLAY_SERVICE_PROTOCOL_TRACE_BYTES, os.SEEK_END)
            raw = handle.read(_MAX_REPLAY_SERVICE_PROTOCOL_TRACE_BYTES)
    except OSError:
        return False
    trace = sanitize_text(raw.decode("utf-8", errors="replace"))
    if not trace:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(trace, encoding="utf-8")
    return True


def _reset_replay_service_protocol_trace(source: Path) -> None:
    """Separate framework preflight traffic from task-rollout exposure.

    Protocol probes prove that a candidate runtime is valid, but they are not
    evidence that the replayed task exercised that intervention. Clearing the
    bounded trace after preflight lets later causal attribution distinguish the
    two without retaining payloads or changing the runtime protocol.
    """

    if source.is_symlink():
        raise ReplayServiceProtocolError(
            "skill runtime protocol_trace.jsonl cannot be a symlink"
        )
    source.write_text("", encoding="utf-8")


def _normalize_replay_service_protocol_trace_record(
    record: Mapping[str, Any],
    *,
    line_number: int,
) -> dict[str, Any]:
    """Normalize the bounded v0 trace aliases without inventing interactions.

    Early skill runtimes used request/response, message_kind, and
    top_level_fields. Those names carry the same summary semantics as the v1
    fields, so they can be upgraded at the framework boundary. Missing traffic
    cannot be upgraded: the caller still requires both inbound and outbound
    records before a runtime is accepted.
    """

    normalized = dict(record)
    raw_direction = str(normalized.get("direction") or "").strip().lower()
    legacy = bool(
        "message_kind" in normalized
        or "top_level_fields" in normalized
        or raw_direction in {"request", "response"}
    )
    if not legacy:
        return normalized
    if "kind" not in normalized and isinstance(
        normalized.get("message_kind"), str
    ):
        normalized["kind"] = normalized["message_kind"]
    if "fields" not in normalized and isinstance(
        normalized.get("top_level_fields"), list
    ):
        normalized["fields"] = normalized["top_level_fields"]
    normalized.setdefault("sequence", line_number)
    normalized.setdefault("correlation", {})
    if raw_direction == "request":
        normalized["direction"] = "in"
    elif raw_direction == "response":
        normalized["direction"] = "out"
    return normalized


def _protocol_trace_runtime_artifact_constraint() -> dict[str, object]:
    """Return the canonical runtime-owner contract for protocol evidence.

    The trace is validated while the service is alive, after readiness and
    protocol probes but before shutdown.  Therefore a runtime that buffers the
    file until ``finally`` cannot satisfy the contract even if its final bytes
    would be structurally valid.
    """

    return {
        "schema_version": "aworld.self_evolve.runtime_artifact_constraint.v1",
        "artifact_kind": "protocol_trace",
        "relative_path": _REPLAY_SERVICE_PROTOCOL_TRACE_NAME,
        "producer_layer": "runtime",
        "availability_milestone": "post_probe_pre_shutdown",
        "write_mode": "incremental",
        "maximum_bytes": _MAX_REPLAY_SERVICE_PROTOCOL_TRACE_BYTES,
        "require_nonempty": True,
        "required_record_fields": [
            "direction",
            "sequence",
            "kind",
            "fields",
            "correlation",
        ],
        "required_directions": ["in", "out"],
    }


def _validate_replay_service_protocol_trace(trace_path: Path) -> None:
    """Validate the candidate-owned, protocol-neutral replay trace contract."""

    if trace_path.is_symlink() or not trace_path.is_file():
        raise ReplayServiceProtocolError(
            "skill runtime did not write protocol_trace.jsonl under the supplied "
            "scratch directory"
        )
    try:
        size = trace_path.stat().st_size
        if size <= 0:
            raise ReplayServiceProtocolError(
                "skill runtime wrote an empty protocol_trace.jsonl"
            )
        if size > _MAX_REPLAY_SERVICE_PROTOCOL_TRACE_BYTES:
            raise ReplayServiceProtocolError(
                "skill runtime protocol_trace.jsonl exceeded the bounded startup "
                f"limit of {_MAX_REPLAY_SERVICE_PROTOCOL_TRACE_BYTES} bytes"
            )
        raw_lines = trace_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
    except ReplayServiceProtocolError:
        raise
    except OSError as exc:
        raise ReplayServiceProtocolError(
            "skill runtime protocol_trace.jsonl could not be read"
        ) from exc

    directions: set[str] = set()
    record_count = 0
    for line_number, raw_line in enumerate(raw_lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayServiceProtocolError(
                "skill runtime protocol_trace.jsonl must contain one JSON object "
                f"per line (invalid line {line_number})"
            ) from exc
        if not isinstance(record, Mapping):
            raise ReplayServiceProtocolError(
                "skill runtime protocol_trace.jsonl records must be JSON objects"
            )
        record = _normalize_replay_service_protocol_trace_record(
            record,
            line_number=line_number,
        )
        required = {"direction", "sequence", "kind", "fields", "correlation"}
        missing = sorted(required.difference(record))
        if missing:
            raise ReplayServiceProtocolError(
                "skill runtime protocol_trace.jsonl record is missing required "
                f"summary fields: {', '.join(missing)}",
                code="protocol_trace_schema_field_validation_failed",
                details=schema_field_diagnostic_details(
                    tuple(
                        SchemaFieldViolation.create(
                            SchemaFieldRepairConstraint(
                                schema_layer="protocol_trace",
                                field_path=f"records[*].{field_name}",
                                rule="required",
                            ),
                            None,
                        )
                        for field_name in missing
                    )
                ),
            )
        type_violations: list[SchemaFieldViolation] = []
        sequence = record.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            type_violations.append(
                SchemaFieldViolation.create(
                    SchemaFieldRepairConstraint(
                        schema_layer="protocol_trace",
                        field_path="records[*].sequence",
                        rule="type",
                        expected=("non_negative_integer",),
                    ),
                    sequence,
                )
            )
        kind = record.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            type_violations.append(
                SchemaFieldViolation.create(
                    SchemaFieldRepairConstraint(
                        schema_layer="protocol_trace",
                        field_path="records[*].kind",
                        rule="type",
                        expected=("non_empty_string",),
                    ),
                    kind,
                )
            )
        if not isinstance(record.get("fields"), list):
            type_violations.append(
                SchemaFieldViolation.create(
                    SchemaFieldRepairConstraint(
                        schema_layer="protocol_trace",
                        field_path="records[*].fields",
                        rule="type",
                        expected=("array",),
                    ),
                    record.get("fields"),
                )
            )
        elif any(not isinstance(item, str) for item in record["fields"]):
            type_violations.append(
                SchemaFieldViolation.create(
                    SchemaFieldRepairConstraint(
                        schema_layer="protocol_trace",
                        field_path="records[*].fields[*]",
                        rule="type",
                        expected=("string",),
                    ),
                    record.get("fields"),
                )
            )
        if not isinstance(record.get("correlation"), Mapping):
            type_violations.append(
                SchemaFieldViolation.create(
                    SchemaFieldRepairConstraint(
                        schema_layer="protocol_trace",
                        field_path="records[*].correlation",
                        rule="type",
                        expected=("object",),
                    ),
                    record.get("correlation"),
                )
            )
        if type_violations:
            raise ReplayServiceProtocolError(
                "skill runtime protocol_trace.jsonl summary fields have invalid types",
                code="protocol_trace_schema_field_validation_failed",
                details=schema_field_diagnostic_details(type_violations),
            )
        direction = str(record.get("direction") or "").strip().lower()
        if direction in {"in", "inbound", "received", "receive", "recv", "request"}:
            directions.add("in")
        elif direction in {
            "out",
            "outbound",
            "emitted",
            "emit",
            "send",
            "sent",
            "response",
        }:
            directions.add("out")
        else:
            raise ReplayServiceProtocolError(
                "skill runtime protocol_trace.jsonl direction must describe a "
                "received or emitted interaction",
                code="protocol_trace_schema_field_validation_failed",
                details=schema_field_diagnostic_details(
                    (
                        SchemaFieldViolation.create(
                            SchemaFieldRepairConstraint(
                                schema_layer="protocol_trace",
                                field_path="records[*].direction",
                                rule="enum",
                                expected=(
                                    "in",
                                    "inbound",
                                    "received",
                                    "receive",
                                    "recv",
                                    "request",
                                    "out",
                                    "outbound",
                                    "emitted",
                                    "emit",
                                    "send",
                                    "sent",
                                    "response",
                                ),
                            ),
                            record.get("direction"),
                        ),
                    )
                ),
            )
        record_count += 1
    if record_count == 0:
        raise ReplayServiceProtocolError(
            "skill runtime wrote an empty protocol_trace.jsonl"
        )
    if directions != {"in", "out"}:
        raise ReplayServiceProtocolError(
            "skill runtime protocol_trace.jsonl must record both received and "
            "emitted interactions",
            code="protocol_trace_direction_coverage_failed",
            details=schema_field_diagnostic_details(
                (
                    SchemaFieldViolation.create(
                        SchemaFieldRepairConstraint(
                            schema_layer="protocol_trace",
                            field_path="records[*].direction",
                            rule="contains_all",
                            expected=("in", "out"),
                        ),
                        sorted(directions),
                    ),
                )
            ),
        )


async def _wait_for_replay_service_protocol_trace(
    process: subprocess.Popen[Any],
    trace_path: Path,
    *,
    timeout_seconds: float = 1.0,
) -> None:
    """Wait briefly for post-response trace writes to become observable.

    A service may flush the HTTP response before appending its outbound trace
    record. The client can therefore return a few scheduler ticks before the
    post-probe artifact reaches its complete ``in``/``out`` state. Preserve the
    strict validator, but give that already-running producer a small, bounded
    completion window. Missing, malformed, or incomplete traces still raise the
    original typed error at the deadline.
    """

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    last_error: ReplayServiceProtocolError | None = None
    while True:
        try:
            _validate_replay_service_protocol_trace(trace_path)
            return
        except ReplayServiceProtocolError as exc:
            last_error = exc
        if process.poll() is not None or time.monotonic() >= deadline:
            assert last_error is not None
            raise last_error
        await asyncio.sleep(min(0.02, max(0.0, deadline - time.monotonic())))


def _replay_service_protocol_diagnostics(
    artifact_dir: Path,
) -> list[dict[str, str]]:
    root = (artifact_dir / "replay_services").resolve()
    if not root.is_dir():
        return []
    diagnostics: list[dict[str, str]] = []
    for path in sorted(root.glob("*/protocol_trace.log"))[:4]:
        try:
            resolved = path.resolve()
            if path.is_symlink() or not resolved.is_relative_to(root):
                continue
            raw = resolved.read_bytes()[-8_000:]
        except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
            continue
        tail = sanitize_text(raw.decode("utf-8", errors="replace"))
        if len(tail) > _MAX_REPLAY_SERVICE_PROTOCOL_TRACE_EXCERPT_CHARS:
            tail = (
                "…"
                + tail[-(
                    _MAX_REPLAY_SERVICE_PROTOCOL_TRACE_EXCERPT_CHARS - 1
                ) :]
            )
        if not tail:
            continue
        diagnostics.append(
            {
                "path": resolved.relative_to(artifact_dir.resolve()).as_posix(),
                "tail": tail,
            }
        )
    return diagnostics


def _attach_replay_service_protocol_diagnostics(
    result: ReplayExecutionResult,
    *,
    artifact_dir: Path,
) -> ReplayExecutionResult:
    traces = _replay_service_protocol_diagnostics(artifact_dir)
    if not traces:
        return result
    failure = dict(result.failure) if result.failure is not None else None
    current_diagnostics = (
        failure.get("diagnostics") if failure is not None else None
    )
    diagnostics = (
        dict(current_diagnostics)
        if isinstance(current_diagnostics, Mapping)
        else {}
    )
    existing = diagnostics.get("replay_service_protocol_traces")
    combined = [
        dict(item)
        for item in existing
        if isinstance(item, Mapping)
    ] if isinstance(existing, list) else []
    for trace in traces:
        if trace not in combined:
            combined.append(trace)
    if failure is not None:
        diagnostics["replay_service_protocol_traces"] = combined[:4]
        failure["diagnostics"] = diagnostics
    return replace(
        result,
        failure=failure,
        metrics={
            **dict(result.metrics),
            "replay_service_protocol_trace_count": len(combined[:4]),
        },
    )


def _classify_candidate_task_rollout_nontermination(
    result: ReplayExecutionResult,
    *,
    variant_id: str,
) -> ReplayExecutionResult:
    """Record physical timeout progress without assigning causal ownership.

    A single variant cannot prove that a timeout was introduced by a candidate.
    Ownership is assigned only after the baseline and candidate executions are
    paired.  Keeping the historical helper name avoids a persistence/API churn
    while changing its contract from classification to observation.
    """

    failure = result.failure
    if (
        not isinstance(failure, Mapping)
        or failure.get("type") != "TimeoutExpired"
    ):
        return result
    completed_operations = _completed_replay_data_plane_operations(failure)
    if not completed_operations:
        return result
    diagnostics = failure.get("diagnostics")
    physical_diagnostics = dict(diagnostics) if isinstance(diagnostics, Mapping) else {}
    physical_diagnostics["completed_data_plane_operations"] = list(
        completed_operations
    )
    observed = {
        **dict(failure),
        "failure_stage": str(failure.get("failure_stage") or "task_rollout"),
        "completed_data_plane_operations": list(completed_operations),
        "diagnostics": physical_diagnostics,
    }
    # Existing explicit capability attribution remains authoritative.  The
    # generic timeout path, including a candidate variant, remains task-owned
    # until paired comparison is available.
    del variant_id
    return replace(result, failure=observed)


def _completed_replay_data_plane_operations(
    failure: Mapping[str, Any],
) -> tuple[str, ...]:
    diagnostics = failure.get("diagnostics")
    traces = (
        diagnostics.get("replay_service_protocol_traces")
        if isinstance(diagnostics, Mapping)
        else None
    )
    if not isinstance(traces, list):
        return ()
    inbound: set[str] = set()
    outbound: set[str] = set()
    ordered: list[str] = []
    for trace in traces[:8]:
        if not isinstance(trace, Mapping):
            continue
        tail = trace.get("tail")
        if not isinstance(tail, str):
            continue
        for line in tail.splitlines()[-256:]:
            try:
                record = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(record, Mapping):
                continue
            direction = str(record.get("direction") or "").strip().casefold()
            operations = _protocol_trace_operation_names(record)
            if direction in {"in", "inbound", "received", "receive", "recv"}:
                inbound.update(operations)
            elif direction in {
                "out",
                "outbound",
                "emitted",
                "emit",
                "send",
                "sent",
            }:
                outbound.update(operations)
            for operation in operations:
                if operation not in ordered:
                    ordered.append(operation)
    return tuple(
        operation
        for operation in ordered
        if operation in inbound and operation in outbound
    )


def _protocol_trace_operation_names(record: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    control_values = {
        "health",
        "healthz",
        "ready",
        "readiness",
        "startup",
        "status",
    }
    transport_values = {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "http",
        "request",
        "response",
    }

    def append(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if ":" in text or "=" in text:
            separator = ":" if ":" in text else "="
            key, nested = text.split(separator, 1)
            if key.strip().casefold() in {
                "method",
                "operation",
                "path",
                "route",
                "command",
            }:
                append(nested)
                return
        normalized = text.casefold().strip("/")
        is_root_path = text == "/"
        if (
            (normalized or is_root_path)
            and normalized not in control_values
            and normalized not in transport_values
            and text not in values
        ):
            values.append(text)

    def visit(value: Any, *, depth: int = 0) -> None:
        if depth > 4 or len(values) >= 32:
            return
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key).casefold() in {
                    "method",
                    "operation",
                    "path",
                    "route",
                    "command",
                }:
                    append(nested)
                elif isinstance(nested, (Mapping, list, tuple)):
                    visit(nested, depth=depth + 1)
        elif isinstance(value, (list, tuple)):
            for nested in value[:64]:
                if isinstance(nested, (Mapping, list, tuple)):
                    visit(nested, depth=depth + 1)
                else:
                    append(nested)

    visit(record.get("fields"))
    correlation = record.get("correlation")
    if isinstance(correlation, Mapping):
        visit(correlation)
    return tuple(values)


def _emit_replay_member_progress(
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
    **payload: Any,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(dict(payload))
    except Exception as exc:
        logger.debug(
            "self_evolve.replay.progress_callback_failed "
            f"error_type={type(exc).__name__}"
        )


def _emit_replay_attempt_progress(
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
    **payload: Any,
) -> None:
    _emit_replay_member_progress(progress_callback, **payload)


def _scoped_replay_attempt_callback(
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
    **scope: Any,
) -> Callable[[Mapping[str, Any]], None] | None:
    if progress_callback is None:
        return None

    def emit(payload: Mapping[str, Any]) -> None:
        _emit_replay_attempt_progress(
            progress_callback,
            **{**scope, **dict(payload)},
        )

    return emit


def _member_phase_timeout_result(
    *,
    variant_id: str,
    phase: str,
    timeout_seconds: float | None,
) -> ReplayVariantResult:
    return ReplayVariantResult(
        variant_id=variant_id,
        status=ReplayExecutionStatus.FAILED,
        trajectory=[],
        metrics={
            "member_phase_timeout": True,
            "member_phase": phase,
            "member_phase_timeout_seconds": timeout_seconds,
        },
        failure=ReplayFailureEvent(
            code="replay_member_phase_timeout",
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.MEMBER,
            repairable=True,
            category="replay_timeout",
            summary=(
                f"{phase} replay member phase exceeded its hard deadline"
            ),
            diagnostics={
                "phase": phase,
                "timeout_seconds": timeout_seconds,
            },
        ),
    )


def _member_phase_hard_deadline_seconds(
    attempt_timeout_seconds: float | None,
) -> float | None:
    """Leave bounded time for the supervised attempt to persist its outcome."""

    if attempt_timeout_seconds is None:
        return None
    timeout = float(attempt_timeout_seconds)
    grace = min(
        _MEMBER_PHASE_TEARDOWN_GRACE_MAX_SECONDS,
        max(0.10, timeout * 0.05),
    )
    return timeout + grace


def _write_incremental_baseline_manifest(
    members_root: Path,
    *,
    prepared_members: Sequence[tuple[EvalCase, CandidateReplayRequest, Path]],
    completed_case_ids: Sequence[str],
) -> None:
    completed = set(completed_case_ids)
    _write_json(
        members_root / "baseline_cache_manifest.json",
        {
            "schema_version": "aworld.self_evolve.baseline_cache.v1",
            "repetition_semantics": _PER_MEMBER_REPETITION_SEMANTICS,
            "members": [
                {
                    "case_id": case.case_id,
                    "path": _member_artifact_name(case.case_id),
                    "baseline_complete": True,
                    "control_fingerprint": baseline_control_fingerprint(
                        member_request
                    ),
                }
                for case, member_request, _member_dir in prepared_members
                if case.case_id in completed
            ],
        },
    )


def _write_progressive_pair_checkpoint(
    members_root: Path,
    *,
    case_ids: Sequence[str],
    baseline_phase_completed_case_ids: Sequence[str],
    candidate_phase_completed_case_ids: Sequence[str],
    comparable_pair_case_ids: Sequence[str],
    reusable_baseline_case_ids: Sequence[str],
    active_case_id: str | None,
    active_phase: str | None,
    resumed_from_replay_dir: str | None = None,
    resumed_pair_case_ids: Sequence[str] = (),
) -> None:
    """Persist a bounded restart and diagnostic cursor after every phase."""

    completed_candidates = set(candidate_phase_completed_case_ids)
    _write_json(
        members_root / "paired_replay_checkpoint.json",
        {
            "schema_version": "aworld.self_evolve.paired_replay_checkpoint.v1",
            "schedule": "progressive_paired",
            "resume_safe": True,
            "active_case_id": active_case_id,
            "active_phase": active_phase,
            "baseline_phase_completed_case_ids": list(
                baseline_phase_completed_case_ids
            ),
            "candidate_phase_completed_case_ids": list(
                candidate_phase_completed_case_ids
            ),
            "comparable_pair_case_ids": list(comparable_pair_case_ids),
            "reusable_baseline_case_ids": list(reusable_baseline_case_ids),
            "pending_case_ids": [
                case_id
                for case_id in case_ids
                if case_id not in completed_candidates
            ],
            "baseline_cache_manifest": "baseline_cache_manifest.json",
            "resumed_from_replay_dir": resumed_from_replay_dir,
            "resumed_pair_case_ids": list(resumed_pair_case_ids),
        },
    )


def _clone_replay_variant_tree(source: Path, destination: Path) -> None:
    """Materialize immutable replay evidence without duplicating file bytes."""

    if source.is_symlink() or source.resolve() == destination.resolve():
        raise OSError("replay resume source must be a distinct physical directory")
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def copy_file(source_file: str, destination_file: str) -> str:
        try:
            os.link(source_file, destination_file)
        except OSError:
            shutil.copy2(source_file, destination_file)
        return destination_file

    shutil.copytree(source, destination, copy_function=copy_file)


def _materialize_task_skill_mount(
    *,
    skill_root: str | None,
    skill_name: str,
    artifact_dir: Path,
    expected_package_fingerprint: str | None,
) -> str | None:
    """Copy the selected package into the immutable task artifact boundary.

    Candidate overlays are run-scoped control-plane objects.  A child task
    must not keep resolving them through a path whose lifecycle is owned by
    the campaign runner.  Materializing the exact package beside the task
    artifacts gives resolution, execution, and signed activation attestation
    one stable byte identity.
    """

    if (
        not skill_root
        or not skill_name
        or expected_package_fingerprint is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", expected_package_fingerprint
        )
        is None
    ):
        return skill_root
    supplied_root = Path(skill_root).expanduser().resolve()
    package_root = supplied_root / skill_name
    if not (package_root / "SKILL.md").is_file() and not (
        package_root / "skill.md"
    ).is_file():
        package_root = supplied_root
    if not (package_root / "SKILL.md").is_file() and not (
        package_root / "skill.md"
    ).is_file():
        raise ValueError(
            "replay skill mount cannot locate requested package: "
            f"{skill_name} under {supplied_root}"
        )

    observed_fingerprint = fingerprint_skill_package(package_root)
    if (
        expected_package_fingerprint is not None
        and observed_fingerprint != expected_package_fingerprint
    ):
        raise ValueError(
            "replay skill mount source fingerprint drifted: "
            f"expected {expected_package_fingerprint}, observed "
            f"{observed_fingerprint}"
        )

    mount_root = artifact_dir / "task_skill_mount"
    if mount_root.is_symlink():
        raise ValueError("replay task skill mount cannot be a symlink")
    if mount_root.exists():
        shutil.rmtree(mount_root)
    mounted_package = mount_root / skill_name
    mounted_package.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(package_root, mounted_package, symlinks=False)
    mounted_fingerprint = fingerprint_skill_package(mounted_package)
    if mounted_fingerprint != observed_fingerprint:
        raise ValueError("replay task skill mount changed package bytes")
    return str(mount_root)


def _resume_root_is_compatible(
    current: CandidateReplayRequest,
    stored: CandidateReplayRequest,
) -> bool:
    """Compare immutable experiment identity while ignoring run-local bindings."""

    return _resume_adaptation_is_semantically_compatible(current, stored) and all(
        getattr(current, field_name) == getattr(stored, field_name)
        for field_name in (
            "target",
            "candidate_id",
            "dataset_fingerprint",
            "baseline_skill_fingerprint",
            "workspace_seed_fingerprint",
            "verified_candidate_package_fingerprint",
            "baseline_repetitions",
            "candidate_repetitions",
            "repetition_semantics",
            "evidence_policy_mode",
        )
    ) and _resume_measurement_contract_is_compatible(current, stored)


def _resume_member_is_compatible(
    current: CandidateReplayRequest,
    stored: CandidateReplayRequest,
) -> bool:
    return _resume_adaptation_is_semantically_compatible(current, stored) and all(
        getattr(current, field_name) == getattr(stored, field_name)
        for field_name in (
            "task_id",
            "target",
            "candidate_id",
            "task_input_fingerprint",
            "dataset_fingerprint",
            "baseline_skill_fingerprint",
            "workspace_seed_fingerprint",
            "verified_candidate_package_fingerprint",
            "baseline_repetitions",
            "candidate_repetitions",
            "repetition_semantics",
            "evidence_policy_mode",
        )
    ) and _resume_measurement_contract_is_compatible(current, stored)


def _resume_measurement_contract_is_compatible(
    current: CandidateReplayRequest,
    stored: CandidateReplayRequest,
) -> bool:
    """Prevent legacy/checkpoint evidence from crossing a v2 plan boundary."""

    current_plan = current.measurement_plan
    stored_plan = stored.measurement_plan
    if current_plan is None or stored_plan is None:
        return current_plan is None and stored_plan is None
    return bool(
        current_plan.measurement_plan_fingerprint
        == stored_plan.measurement_plan_fingerprint
        and current_plan.evidence_policy_fingerprint
        == stored_plan.evidence_policy_fingerprint
        and current_plan.isolation_decision_fingerprint
        == stored_plan.isolation_decision_fingerprint
    )


def _resume_adaptation_is_semantically_compatible(
    current: CandidateReplayRequest,
    stored: CandidateReplayRequest,
) -> bool:
    """Compare logical replay capability without run-local paths or bindings."""

    current_bundle = current.replay_adaptation
    stored_bundle = stored.replay_adaptation
    if current_bundle is None or stored_bundle is None:
        return current_bundle is None and stored_bundle is None
    current_capability = current_bundle.replay_capability
    stored_capability = stored_bundle.replay_capability
    if current_capability is None or stored_capability is None:
        capability_identity_matches = (
            current_capability is None and stored_capability is None
        )
    else:
        def capability_identity(capability: FrozenReplayCapability) -> object:
            return {
                "capability_package_fingerprint": (
                    capability.capability_package_fingerprint
                ),
                "handled_requirements": capability.handled_requirements,
                "unhandled_requirements": capability.unhandled_requirements,
                "deterministic": capability.deterministic,
                "concurrency_mode": capability.concurrency_mode,
                "fixtures": [
                    (item.sha256, item.size) for item in capability.fixtures
                ],
                "runtime_files": [
                    (item.sha256, item.size) for item in capability.runtime_files
                ],
                "services": [
                    {
                        "service_id": service.service_id,
                        "requirement_id": service.requirement_id,
                        "transport": service.transport,
                        "response_fixture": service.response_fixture,
                        "readiness": to_json_dict(service.readiness),
                        "protocol_probes": to_json_dict(service.protocol_probes),
                    }
                    for service in capability.services
                ],
            }

        capability_identity_matches = (
            capability_identity(current_capability)
            == capability_identity(stored_capability)
        )
    return bool(
        capability_identity_matches
        and current_bundle.ready
        and stored_bundle.ready
        and current_bundle.environment_fingerprint
        == stored_bundle.environment_fingerprint
        and current_bundle.workspace_seed_fingerprint
        == stored_bundle.workspace_seed_fingerprint
        and [
            (case.case_id, case.task_input_fingerprint, case.readiness)
            for case in current_bundle.cases
        ]
        == [
            (case.case_id, case.task_input_fingerprint, case.readiness)
            for case in stored_bundle.cases
        ]
    )


def _load_resumable_member_pairs(
    request: CandidateReplayRequest,
    *,
    prepared_members: Sequence[tuple[EvalCase, CandidateReplayRequest, Path]],
) -> dict[str, CandidateReplayMemberResult]:
    """Load complete compatible member pairs from an earlier partial replay."""

    if request.resume_replay_dir is None:
        return {}
    resume_root = Path(request.resume_replay_dir).expanduser()
    if resume_root.is_symlink():
        logger.info(
            "self_evolve.replay.resume.skip reason=symlink_source "
            f"source={resume_root}"
        )
        return {}
    checkpoint_path = resume_root / "members" / "paired_replay_checkpoint.json"
    try:
        stored_root_request = _candidate_replay_request_from_mapping(
            _load_json_object(resume_root / "request.json")
        )
        checkpoint = _load_json_object(checkpoint_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        logger.info(
            "self_evolve.replay.resume.skip reason=checkpoint_unreadable "
            f"source={resume_root}"
        )
        return {}
    if (
        checkpoint.get("resume_safe") is not True
        or not _resume_root_is_compatible(request, stored_root_request)
    ):
        logger.info(
            "self_evolve.replay.resume.skip reason=experiment_identity_mismatch "
            f"source={resume_root}"
        )
        return {}
    raw_completed = checkpoint.get("candidate_phase_completed_case_ids")
    if not isinstance(raw_completed, list):
        return {}
    completed = {
        item for item in raw_completed if isinstance(item, str) and item
    }
    resumed: dict[str, CandidateReplayMemberResult] = {}
    for case, member_request, member_dir in prepared_members:
        if case.case_id not in completed:
            continue
        source_member_dir = (
            resume_root / "members" / _member_artifact_name(case.case_id)
        )
        try:
            stored_member_request = _candidate_replay_request_from_mapping(
                _load_json_object(source_member_dir / "request.json")
            )
        except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
            continue
        if not _resume_member_is_compatible(member_request, stored_member_request):
            continue
        source_baseline_dir = (
            Path(stored_member_request.baseline_replay_dir)
            if stored_member_request.baseline_replay_dir
            else source_member_dir / "baseline"
        )
        source_candidate_dir = source_member_dir / _safe_path(request.candidate_id)
        try:
            baseline = _load_variant_result_from_dir(
                source_baseline_dir,
                base_variant_id="baseline",
            )
            candidate_result = _load_variant_result_from_dir(
                source_candidate_dir,
                base_variant_id=request.candidate_id,
            )
            baseline, baseline_failures = _validate_v3_member_variant_artifact(
                source_baseline_dir,
                result=baseline,
                requested_repetitions=member_request.baseline_repetitions,
                case_id=case.case_id,
                variant_role="baseline",
                expected_variant_id="baseline",
            )
            candidate_result, candidate_failures = (
                _validate_v3_member_variant_artifact(
                    source_candidate_dir,
                    result=candidate_result,
                    requested_repetitions=member_request.candidate_repetitions,
                    case_id=case.case_id,
                    variant_role="candidate",
                    expected_variant_id=request.candidate_id,
                )
            )
        except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
            continue
        if baseline_failures or candidate_failures:
            continue
        if (
            member_request.evidence_policy_mode == "required"
            and isinstance(
                member_request.verified_candidate_package_fingerprint,
                str,
            )
            and not _variant_has_attested_skill_activation(
                candidate_result,
                expected_package_fingerprint=(
                    member_request.verified_candidate_package_fingerprint
                ),
            )
        ):
            # A pre-fix checkpoint can contain successful task output whose
            # CLI never transported resolver evidence. Re-run only the
            # treatment arm instead of making an unprovable intervention
            # immortal through checkpoint reuse.
            continue
        if (
            baseline.status is ReplayExecutionStatus.BLOCKED
            or candidate_result.status is ReplayExecutionStatus.BLOCKED
        ):
            # Older checkpoints marked scheduler-produced blocked placeholders
            # as completed pairs. They contain no arm observation and cannot
            # authorize terminal reuse under the current execution contract.
            continue
        try:
            _clone_replay_variant_tree(
                source_baseline_dir,
                member_dir / "baseline",
            )
            _clone_replay_variant_tree(
                source_candidate_dir,
                member_dir / _safe_path(request.candidate_id),
            )
            baseline = _load_variant_result_from_dir(
                member_dir / "baseline",
                base_variant_id="baseline",
            )
            candidate_result = _load_variant_result_from_dir(
                member_dir / _safe_path(request.candidate_id),
                base_variant_id=request.candidate_id,
            )
        except OSError:
            shutil.rmtree(member_dir / "baseline", ignore_errors=True)
            shutil.rmtree(
                member_dir / _safe_path(request.candidate_id),
                ignore_errors=True,
            )
            continue
        local_request = replace(
            member_request,
            baseline_replay_dir=None,
            adaptation_fingerprint=(
                stored_member_request.adaptation_fingerprint
            ),
            replay_adaptation=stored_member_request.replay_adaptation,
        )
        _write_json(member_dir / "request.json", local_request)
        resumed[case.case_id] = CandidateReplayMemberResult(
            case_id=case.case_id,
            request=local_request,
            baseline=baseline,
            candidate=candidate_result,
        )
    logger.info(
        "self_evolve.replay.resume.loaded "
        f"source={resume_root} completed_pairs={len(resumed)} "
        f"checkpoint_pairs={len(completed)}"
    )
    return resumed


def _variant_has_attested_skill_activation(
    result: ReplayVariantResult,
    *,
    expected_package_fingerprint: str,
) -> bool:
    observations = result.repetition_results or (result,)
    return bool(observations) and all(
        observation.metrics.get("skill_activation_attested") is True
        and observation.metrics.get("activated_skill_package_fingerprint")
        == expected_package_fingerprint
        for observation in observations
    )


def _resumable_baseline_cache_root(
    request: CandidateReplayRequest,
) -> str | None:
    """Expose verified partial controls as a normal per-member baseline cache."""

    if request.resume_replay_dir is None:
        return None
    resume_root = Path(request.resume_replay_dir).expanduser()
    if resume_root.is_symlink():
        return None
    members_root = resume_root / "members"
    checkpoint_path = members_root / "paired_replay_checkpoint.json"
    try:
        stored_root_request = _candidate_replay_request_from_mapping(
            _load_json_object(resume_root / "request.json")
        )
        checkpoint = _load_json_object(checkpoint_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return None
    if (
        checkpoint.get("resume_safe") is not True
        or not _resume_root_is_compatible(request, stored_root_request)
        or checkpoint.get("baseline_cache_manifest")
        != "baseline_cache_manifest.json"
    ):
        return None
    manifest_path = members_root / "baseline_cache_manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.parent.resolve() != members_root.resolve()
    ):
        return None
    return str(members_root)


def _member_resumable_baseline_replay_dir(
    baseline_cache_root: str | None,
    case_id: str,
) -> str | None:
    """Resolve only members committed by the incremental baseline manifest."""

    if baseline_cache_root is None:
        return None
    root = Path(baseline_cache_root)
    try:
        manifest = _load_json_object(root / "baseline_cache_manifest.json")
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return None
    if (
        manifest.get("schema_version")
        != "aworld.self_evolve.baseline_cache.v1"
        or manifest.get("repetition_semantics")
        != _PER_MEMBER_REPETITION_SEMANTICS
    ):
        return None
    members = manifest.get("members")
    if not isinstance(members, list):
        return None
    for member in members:
        if (
            not isinstance(member, Mapping)
            or member.get("case_id") != case_id
            or member.get("baseline_complete") is not True
            or member.get("path") != _member_artifact_name(case_id)
        ):
            continue
        return _stored_member_baseline_replay_dir(
            root / _member_artifact_name(case_id),
            case_id=case_id,
        )
    return None


def candidate_replay_artifact_directory(
    *,
    workspace_root: str | Path,
    run_id: str,
    candidate_id: str,
    artifact_namespace: str | None = None,
) -> Path:
    """Return the one canonical artifact root shared by planning and replay."""

    replay_root = (
        Path(workspace_root)
        / ".aworld"
        / "self_evolve"
        / _safe_path(run_id)
    )
    if artifact_namespace is not None:
        replay_root = replay_root.joinpath(
            *_safe_artifact_namespace(artifact_namespace)
        )
    return replay_root / "replay" / _safe_path(candidate_id)


def _measurement_terminal_state_for_variant(
    result: ReplayVariantResult,
) -> MeasurementWorkUnitState:
    """Map typed replay ownership to durable retry semantics."""

    if result.status is ReplayExecutionStatus.SUCCEEDED:
        return MeasurementWorkUnitState.SUCCEEDED
    events = tuple(
        event
        for event in (result.failure, *result.blocked_by)
        if isinstance(event, ReplayFailureEvent)
    )
    if any(event.code == "replay_member_phase_timeout" for event in events):
        return MeasurementWorkUnitState.MEMBER_TIMED_OUT
    if any(
        event.stage is FailureStage.EVIDENCE_FINALIZATION
        and event.owner in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE}
        for event in events
    ):
        return MeasurementWorkUnitState.EVIDENCE_INVALID
    return MeasurementWorkUnitState.TASK_FAILED


class AWorldCliCandidateReplayBackend:
    supports_member_progress = True

    def __init__(
        self,
        *,
        executor: ReplayExecutor | None = None,
        concurrency_policy: SelfEvolveConcurrencyPolicy | None = None,
        task_batch_executor: DeterministicTaskBatchExecutor | None = None,
    ) -> None:
        self.executor = executor or AWorldCliReplayExecutor()
        self.concurrency_policy = concurrency_policy or SelfEvolveConcurrencyPolicy()
        self.task_batch_executor = (
            task_batch_executor or DeterministicTaskBatchExecutor()
        )
        self.last_replay_batch_observability: Mapping[str, Any] = {}
        self.replay_batch_observability: list[Mapping[str, Any]] = []

    async def replay_candidate(
        self,
        request: CandidateReplayRequest,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> CandidateReplayResult:
        if request.measurement_plan is not None:
            return await self._replay_candidate_measurement_v2(
                request,
                candidate=candidate,
                dataset=dataset,
                progress_callback=progress_callback,
            )
        if not _has_authoritative_per_member_repetitions(request):
            raise ValueError(
                "candidate replay execution requires explicit per-member "
                "repetition semantics"
            )
        replay_dir = candidate_replay_artifact_directory(
            workspace_root=request.workspace_root,
            run_id=request.run_id,
            candidate_id=candidate.candidate_id,
            artifact_namespace=request.artifact_namespace,
        )
        replay_dir.mkdir(parents=True, exist_ok=True)
        _write_json(replay_dir / "request.json", request)
        replay_cases = tuple(
            case for case in dataset.cases if _is_replayable_user_task_case(case)
        )
        if not replay_cases:
            raise ValueError(
                "candidate replay requires at least one user task eval case; "
                "framework-generated evaluation contracts are not replayable"
            )
        logger.info(
            "self_evolve.replay.start "
            f"run_id={request.run_id} task_id={request.task_id} "
            f"candidate_id={candidate.candidate_id} "
            f"baseline_repetitions={request.baseline_repetitions} "
            f"candidate_repetitions={request.candidate_repetitions}"
        )

        members_root = replay_dir / "members"
        member_items: list[CandidateReplayMemberResult] = []
        member_baseline_repetitions = _distributed_member_repetitions(
            request.baseline_repetitions,
            member_count=len(replay_cases),
        )
        member_candidate_repetitions = _distributed_member_repetitions(
            request.candidate_repetitions,
            member_count=len(replay_cases),
        )
        candidate_blocking_event: ReplayFailureEvent | None = None
        prepared_members: list[tuple[EvalCase, CandidateReplayRequest, Path]] = []
        resumable_baseline_root = _resumable_baseline_cache_root(request)
        for case in replay_cases:
            adapted_task_input = _adapted_task_input(request, case)
            member_baseline_replay_dir = _member_baseline_replay_dir(
                request.baseline_replay_dir,
                case.case_id,
            )
            if member_baseline_replay_dir is None:
                member_baseline_replay_dir = (
                    _member_resumable_baseline_replay_dir(
                        resumable_baseline_root,
                        case.case_id,
                    )
                )
            member_request = replace(
                request,
                task_id=case.case_id,
                task_input=adapted_task_input,
                task_input_fingerprint=_adapted_task_input_fingerprint(request, case),
                baseline_replay_dir=member_baseline_replay_dir,
                baseline_repetitions=member_baseline_repetitions,
                candidate_repetitions=member_candidate_repetitions,
            )
            member_dir = members_root / _member_artifact_name(case.case_id)
            member_dir.mkdir(parents=True, exist_ok=True)
            _write_json(member_dir / "request.json", member_request)
            prepared_members.append((case, member_request, member_dir))

        member_count = len(prepared_members)
        invalid_control_streak = 0
        replay_measurement_stop: Mapping[str, Any] | None = None
        authoritative_stop: Mapping[str, Any] | None = None
        candidate_frontier_stop_event: ReplayFailureEvent | None = None
        case_ids = tuple(case.case_id for case, _request, _dir in prepared_members)
        resumed_members = _load_resumable_member_pairs(
            request,
            prepared_members=prepared_members,
        )
        resumed_pair_case_ids = [
            case_id for case_id in case_ids if case_id in resumed_members
        ]
        member_items.extend(
            resumed_members[case_id] for case_id in resumed_pair_case_ids
        )
        baseline_phase_completed_case_ids: list[str] = list(
            resumed_pair_case_ids
        )
        candidate_phase_completed_case_ids: list[str] = list(
            resumed_pair_case_ids
        )
        comparable_pair_case_ids: list[str] = [
            case_id
            for case_id in resumed_pair_case_ids
            if _replay_member_pair_is_comparable(
                next(case for case in replay_cases if case.case_id == case_id),
                resumed_members[case_id].baseline,
                resumed_members[case_id].candidate,
            )
        ]
        reusable_baseline_case_ids: list[str] = [
            case_id
            for case_id in resumed_pair_case_ids
            if _baseline_replay_is_reusable(
                resumed_members[case_id].baseline,
                requested_repetitions=(
                    resumed_members[case_id].request.baseline_repetitions
                ),
            )
        ]
        for case_id in resumed_pair_case_ids:
            resumed_failure = resumed_members[case_id].candidate.failure
            if (
                isinstance(resumed_failure, ReplayFailureEvent)
                and resumed_failure.scope
                in {FailureScope.CANDIDATE, FailureScope.SHARED_RUN}
                and resumed_failure.stage is not FailureStage.TASK_ROLLOUT
            ):
                candidate_blocking_event = resumed_failure
        if resumed_pair_case_ids:
            _emit_replay_member_progress(
                progress_callback,
                event="checkpoint_pairs_reused",
                candidate_id=candidate.candidate_id,
                case_id=resumed_pair_case_ids[-1],
                case_index=len(resumed_pair_case_ids),
                case_count=member_count,
                phase="checkpoint",
                reused_case_count=len(resumed_pair_case_ids),
                pending_case_count=member_count - len(resumed_pair_case_ids),
                source_replay_dir=request.resume_replay_dir,
            )
        _write_incremental_baseline_manifest(
            members_root,
            prepared_members=prepared_members,
            completed_case_ids=reusable_baseline_case_ids,
        )
        _write_progressive_pair_checkpoint(
            members_root,
            case_ids=case_ids,
            baseline_phase_completed_case_ids=baseline_phase_completed_case_ids,
            candidate_phase_completed_case_ids=candidate_phase_completed_case_ids,
            comparable_pair_case_ids=comparable_pair_case_ids,
            reusable_baseline_case_ids=reusable_baseline_case_ids,
            active_case_id=None,
            active_phase=None,
            resumed_from_replay_dir=request.resume_replay_dir,
            resumed_pair_case_ids=resumed_pair_case_ids,
        )

        async def run_baseline_phase(
            *,
            member_index: int,
            case: EvalCase,
            member_request: CandidateReplayRequest,
            member_dir: Path,
            blocking_event: ReplayFailureEvent | None,
        ) -> ReplayVariantResult:
            _emit_replay_member_progress(
                progress_callback,
                event="member_phase_started",
                candidate_id=candidate.candidate_id,
                case_id=case.case_id,
                case_index=member_index,
                case_count=member_count,
                phase="baseline",
                repetition_count=member_request.baseline_repetitions,
                baseline_cache_offered=(
                    member_request.baseline_replay_dir is not None
                ),
                phase_timeout_seconds=_member_phase_hard_deadline_seconds(
                    member_request.timeout_seconds
                ),
            )
            if blocking_event is not None:
                baseline = _blocked_variant_result(
                    "baseline", blocked_by=blocking_event
                )
                _persist_variant_lifecycle(member_dir / "baseline", baseline)
            else:
                try:
                    async with asyncio.timeout(
                        _member_phase_hard_deadline_seconds(
                            member_request.timeout_seconds
                        )
                    ):
                        baseline = await self._load_or_run_baseline(
                            member_request,
                            candidate=candidate,
                            replay_dir=member_dir,
                            progress_callback=_scoped_replay_attempt_callback(
                                progress_callback,
                                candidate_id=candidate.candidate_id,
                                case_id=case.case_id,
                                case_index=member_index,
                                case_count=member_count,
                                phase="baseline",
                            ),
                        )
                except TimeoutError:
                    baseline = _member_phase_timeout_result(
                        variant_id="baseline",
                        phase="baseline",
                        timeout_seconds=_member_phase_hard_deadline_seconds(
                            member_request.timeout_seconds
                        ),
                    )
                    _persist_variant_lifecycle(
                        member_dir / "baseline", baseline
                    )
            _emit_replay_member_progress(
                progress_callback,
                event="member_phase_completed",
                candidate_id=candidate.candidate_id,
                case_id=case.case_id,
                case_index=member_index,
                case_count=member_count,
                phase="baseline",
                status=baseline.status.value,
                baseline_cache_status=str(
                    baseline.metrics.get("baseline_cache_status") or "unknown"
                ),
            )
            return baseline

        async def run_candidate_phase(
            *,
            member_index: int,
            case: EvalCase,
            member_request: CandidateReplayRequest,
            member_dir: Path,
            baseline: ReplayVariantResult,
        ) -> ReplayVariantResult:
            nonlocal candidate_blocking_event
            nonlocal candidate_frontier_stop_event
            nonlocal authoritative_stop

            _emit_replay_member_progress(
                progress_callback,
                event="member_phase_started",
                candidate_id=candidate.candidate_id,
                case_id=case.case_id,
                case_index=member_index,
                case_count=member_count,
                phase="candidate",
                repetition_count=member_request.candidate_repetitions,
                phase_timeout_seconds=_member_phase_hard_deadline_seconds(
                    member_request.timeout_seconds
                ),
            )
            blocking_event = candidate_frontier_stop_event
            if (
                blocking_event is None
                and candidate_blocking_event is None
                and member_request.stop_on_incomparable_member
                and _baseline_invalid_for_measurement(baseline)
            ):
                blocking_event = ReplayFailureEvent(
                    code="authoritative_replay_invalid_control",
                    owner=FailureOwner.FRAMEWORK,
                    stage=FailureStage.EVALUATION,
                    scope=FailureScope.SHARED_RUN,
                    repairable=True,
                    category="measurement_stopping",
                    summary=(
                        "authoritative replay stopped because the control "
                        "member is not comparable"
                    ),
                    diagnostics={
                        "trigger": "invalid_control_member",
                        "case_index": member_index,
                        "case_count": member_count,
                        "unused_case_count": member_count - member_index,
                    },
                )
                candidate_frontier_stop_event = blocking_event
                authoritative_stop = {
                    "trigger": "invalid_control_member",
                    "owner": FailureOwner.FRAMEWORK.value,
                    "case_index": member_index,
                    "case_count": member_count,
                    "unused_case_count": member_count - member_index,
                    "resume_safe": True,
                }
                _emit_replay_member_progress(
                    progress_callback,
                    event="authoritative_stop_triggered",
                    candidate_id=candidate.candidate_id,
                    case_id=case.case_id,
                    case_index=member_index,
                    case_count=member_count,
                    phase="candidate",
                    trigger=authoritative_stop["trigger"],
                    unused_case_count=authoritative_stop[
                        "unused_case_count"
                    ],
                    resume_safe=authoritative_stop["resume_safe"],
                )
            if blocking_event is None:
                blocking_event = candidate_blocking_event
            if (
                blocking_event is None
                and _baseline_invalid_for_measurement(baseline)
            ):
                # A candidate cannot form a pair for this member when its
                # control is invalid. Continue to the next independent
                # control until the configured frontier policy stops the run,
                # but never spend candidate execution on an unusable pair.
                assert baseline.failure is not None
                blocking_event = baseline.failure
            if (
                blocking_event is None
                and baseline.status is ReplayExecutionStatus.FAILED
                and _baseline_failure_blocks_candidate(baseline.failure)
            ):
                assert baseline.failure is not None
                blocking_event = baseline.failure
            candidate_dir = member_dir / _safe_path(candidate.candidate_id)
            if blocking_event is not None:
                candidate_result = _blocked_variant_result(
                    candidate.candidate_id,
                    blocked_by=blocking_event,
                )
                _persist_variant_lifecycle(candidate_dir, candidate_result)
            else:
                try:
                    async with asyncio.timeout(
                        _member_phase_hard_deadline_seconds(
                            member_request.timeout_seconds
                        )
                    ):
                        candidate_result = await self._run_repetitions(
                            member_request,
                            base_variant_id=candidate.candidate_id,
                            skill_root=member_request.overlay_skill_root,
                            artifact_dir=candidate_dir,
                            repetitions=member_request.candidate_repetitions,
                            progress_callback=_scoped_replay_attempt_callback(
                                progress_callback,
                                candidate_id=candidate.candidate_id,
                                case_id=case.case_id,
                                case_index=member_index,
                                case_count=member_count,
                                phase="candidate",
                            ),
                        )
                except TimeoutError:
                    candidate_result = _member_phase_timeout_result(
                        variant_id=candidate.candidate_id,
                        phase="candidate",
                        timeout_seconds=_member_phase_hard_deadline_seconds(
                            member_request.timeout_seconds
                        ),
                    )
                    _persist_variant_lifecycle(
                        candidate_dir,
                        candidate_result,
                    )
                if (
                    candidate_result.status is ReplayExecutionStatus.FAILED
                    and candidate_result.failure is not None
                    and candidate_result.failure.scope
                    in {FailureScope.CANDIDATE, FailureScope.SHARED_RUN}
                    and candidate_result.failure.stage
                    is not FailureStage.TASK_ROLLOUT
                ):
                    candidate_blocking_event = candidate_result.failure
                if (
                    member_request.stop_on_incomparable_member
                    and candidate_frontier_stop_event is None
                    and not _replay_member_pair_is_comparable(
                        case,
                        baseline,
                        candidate_result,
                    )
                ):
                    candidate_frontier_stop_event = ReplayFailureEvent(
                        code="authoritative_candidate_frontier_unreachable",
                        owner=FailureOwner.CANDIDATE,
                        stage=FailureStage.TASK_ROLLOUT,
                        scope=FailureScope.CANDIDATE,
                        repairable=True,
                        category="authoritative_early_stop",
                        summary=(
                            "authoritative replay stopped because full "
                            "member comparability is no longer reachable"
                        ),
                        diagnostics={
                            "trigger": "incomparable_candidate_member",
                            "case_index": member_index,
                            "case_count": member_count,
                            "unused_case_count": member_count - member_index,
                            "underlying_failure_code": (
                                candidate_result.failure.code
                                if candidate_result.failure is not None
                                else None
                            ),
                        },
                    )
                    authoritative_stop = {
                        "trigger": "incomparable_candidate_member",
                        "owner": FailureOwner.CANDIDATE.value,
                        "case_index": member_index,
                        "case_count": member_count,
                        "unused_case_count": member_count - member_index,
                        "resume_safe": False,
                    }
                    _emit_replay_member_progress(
                        progress_callback,
                        event="authoritative_stop_triggered",
                        candidate_id=candidate.candidate_id,
                        case_id=case.case_id,
                        case_index=member_index,
                        case_count=member_count,
                        phase="candidate",
                        trigger=authoritative_stop["trigger"],
                        unused_case_count=authoritative_stop[
                            "unused_case_count"
                        ],
                        resume_safe=authoritative_stop["resume_safe"],
                    )
            _emit_replay_member_progress(
                progress_callback,
                event="member_phase_completed",
                candidate_id=candidate.candidate_id,
                case_id=case.case_id,
                case_index=member_index,
                case_count=member_count,
                phase="candidate",
                status=candidate_result.status.value,
            )
            return candidate_result

        pending_members = [
            (member_index, case, member_request, member_dir)
            for member_index, (case, member_request, member_dir) in enumerate(
                prepared_members,
                start=1,
            )
            if case.case_id not in resumed_members
        ]
        baseline_concurrency = _legacy_member_baseline_concurrency(
            [item[2] for item in pending_members],
            concurrency_policy=self.concurrency_policy,
        )
        baseline_tasks: dict[str, asyncio.Task[ReplayVariantResult]] = {}

        async def cancel_pending_baselines() -> None:
            pending_tasks = tuple(baseline_tasks.values())
            baseline_tasks.clear()
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

        def schedule_baseline(
            item: tuple[int, EvalCase, CandidateReplayRequest, Path],
        ) -> None:
            member_index, case, member_request, member_dir = item
            if case.case_id in baseline_tasks:
                return
            baseline_tasks[case.case_id] = asyncio.create_task(
                run_baseline_phase(
                    member_index=member_index,
                    case=case,
                    member_request=member_request,
                    member_dir=member_dir,
                    blocking_event=(
                        candidate_blocking_event
                        or candidate_frontier_stop_event
                    ),
                ),
                name=f"self-evolve-baseline-{_safe_path(case.case_id)}",
            )

        for pending_index, (
            member_index,
            case,
            member_request,
            member_dir,
        ) in enumerate(pending_members):
            _write_progressive_pair_checkpoint(
                members_root,
                case_ids=case_ids,
                baseline_phase_completed_case_ids=(
                    baseline_phase_completed_case_ids
                ),
                candidate_phase_completed_case_ids=(
                    candidate_phase_completed_case_ids
                ),
                comparable_pair_case_ids=comparable_pair_case_ids,
                reusable_baseline_case_ids=reusable_baseline_case_ids,
                active_case_id=case.case_id,
                active_phase="baseline",
                resumed_from_replay_dir=request.resume_replay_dir,
                resumed_pair_case_ids=resumed_pair_case_ids,
            )
            schedule_baseline(pending_members[pending_index])
            if (
                baseline_concurrency > 1
                and pending_index + 1 < len(pending_members)
                and candidate_blocking_event is None
                and candidate_frontier_stop_event is None
            ):
                # At most one adjacent control is speculative.  This overlaps
                # the dominant control runtime without weakening canonical
                # early-stop decisions or starting an unbounded wave.
                schedule_baseline(pending_members[pending_index + 1])
            try:
                baseline = await baseline_tasks.pop(case.case_id)
            except BaseException:
                await cancel_pending_baselines()
                raise
            if (
                baseline.status is ReplayExecutionStatus.FAILED
                and _baseline_failure_blocks_candidate(baseline.failure)
            ):
                assert baseline.failure is not None
                if baseline.failure.scope in {
                    FailureScope.CANDIDATE,
                    FailureScope.SHARED_RUN,
                }:
                    candidate_blocking_event = baseline.failure
            if _baseline_invalid_for_measurement(baseline):
                invalid_control_streak += 1
            else:
                invalid_control_streak = 0
            if (
                member_request.stop_on_incomparable_member
                and baseline.status is ReplayExecutionStatus.FAILED
                and _baseline_invalid_for_measurement(baseline)
            ):
                underlying_failure = baseline.failure
                candidate_blocking_event = ReplayFailureEvent(
                    code="authoritative_replay_invalid_control",
                    owner=FailureOwner.FRAMEWORK,
                    stage=FailureStage.EVALUATION,
                    scope=FailureScope.SHARED_RUN,
                    repairable=True,
                    category="measurement_stopping",
                    summary=(
                        "authoritative replay stopped because the control "
                        "member is not comparable"
                    ),
                    diagnostics={
                        "trigger": "invalid_control_member",
                        "case_index": member_index,
                        "case_count": member_count,
                        "unused_case_count": member_count - member_index,
                    },
                    causes=(
                        (underlying_failure.event_id,)
                        if isinstance(underlying_failure, ReplayFailureEvent)
                        else ()
                    ),
                )
                authoritative_stop = {
                    "trigger": "invalid_control_member",
                    "owner": FailureOwner.FRAMEWORK.value,
                    "case_index": member_index,
                    "case_count": member_count,
                    "unused_case_count": member_count - member_index,
                    "resume_safe": True,
                }
                _emit_replay_member_progress(
                    progress_callback,
                    event="authoritative_stop_triggered",
                    candidate_id=candidate.candidate_id,
                    case_id=case.case_id,
                    case_index=member_index,
                    case_count=member_count,
                    phase="baseline",
                    trigger=authoritative_stop["trigger"],
                    unused_case_count=authoritative_stop[
                        "unused_case_count"
                    ],
                    resume_safe=authoritative_stop["resume_safe"],
                )
            elif (
                member_request.measurement_early_stop_enabled
                and candidate_blocking_event is None
                and invalid_control_streak
                >= member_request.invalid_control_patience
            ):
                candidate_blocking_event = ReplayFailureEvent(
                    code="trusted_measurement_invalid_control_frontier",
                    owner=FailureOwner.FRAMEWORK,
                    stage=FailureStage.EVALUATION,
                    scope=FailureScope.SHARED_RUN,
                    repairable=True,
                    category="measurement_stopping",
                    summary=(
                        "replay stopped after repeated invalid control members"
                    ),
                    diagnostics={
                        "trigger": "repeated_control_invalidity",
                        "patience": member_request.invalid_control_patience,
                        "case_index": member_index,
                        "case_count": member_count,
                        "unused_case_count": member_count - member_index,
                    },
                )
                replay_measurement_stop = {
                    "trigger": "repeated_control_invalidity",
                    "patience": member_request.invalid_control_patience,
                    "case_index": member_index,
                    "case_count": member_count,
                    "unused_case_count": member_count - member_index,
                    "resume_safe": True,
                }
                _emit_replay_member_progress(
                    progress_callback,
                    event="measurement_stop_triggered",
                    candidate_id=candidate.candidate_id,
                    case_id=case.case_id,
                    case_index=member_index,
                    case_count=member_count,
                    phase="baseline",
                    trigger=replay_measurement_stop["trigger"],
                    patience=replay_measurement_stop["patience"],
                    unused_case_count=replay_measurement_stop[
                        "unused_case_count"
                    ],
                    resume_safe=replay_measurement_stop["resume_safe"],
                )
            if baseline.status is not ReplayExecutionStatus.BLOCKED:
                baseline_phase_completed_case_ids.append(case.case_id)
            if _baseline_replay_is_reusable(
                baseline,
                requested_repetitions=member_request.baseline_repetitions,
            ):
                reusable_baseline_case_ids.append(case.case_id)
            _write_incremental_baseline_manifest(
                members_root,
                prepared_members=prepared_members,
                completed_case_ids=reusable_baseline_case_ids,
            )
            _write_progressive_pair_checkpoint(
                members_root,
                case_ids=case_ids,
                baseline_phase_completed_case_ids=(
                    baseline_phase_completed_case_ids
                ),
                candidate_phase_completed_case_ids=(
                    candidate_phase_completed_case_ids
                ),
                comparable_pair_case_ids=comparable_pair_case_ids,
                reusable_baseline_case_ids=reusable_baseline_case_ids,
                active_case_id=case.case_id,
                active_phase="candidate",
                resumed_from_replay_dir=request.resume_replay_dir,
                resumed_pair_case_ids=resumed_pair_case_ids,
            )
            try:
                candidate_result = await run_candidate_phase(
                    member_index=member_index,
                    case=case,
                    member_request=member_request,
                    member_dir=member_dir,
                    baseline=baseline,
                )
            except BaseException:
                await cancel_pending_baselines()
                raise
            member_items.append(
                CandidateReplayMemberResult(
                    case_id=case.case_id,
                    request=member_request,
                    baseline=baseline,
                    candidate=candidate_result,
                )
            )
            if candidate_result.status is not ReplayExecutionStatus.BLOCKED:
                candidate_phase_completed_case_ids.append(case.case_id)
            if _replay_member_pair_is_comparable(
                case,
                baseline,
                candidate_result,
            ):
                comparable_pair_case_ids.append(case.case_id)
            next_case_id = next(
                (
                    pending_case_id
                    for pending_case_id in case_ids[member_index:]
                    if pending_case_id not in resumed_members
                ),
                None,
            )
            _write_progressive_pair_checkpoint(
                members_root,
                case_ids=case_ids,
                baseline_phase_completed_case_ids=(
                    baseline_phase_completed_case_ids
                ),
                candidate_phase_completed_case_ids=(
                    candidate_phase_completed_case_ids
                ),
                comparable_pair_case_ids=comparable_pair_case_ids,
                reusable_baseline_case_ids=reusable_baseline_case_ids,
                active_case_id=next_case_id,
                active_phase=("baseline" if next_case_id is not None else None),
                resumed_from_replay_dir=request.resume_replay_dir,
                resumed_pair_case_ids=resumed_pair_case_ids,
            )
        member_results_by_case = {
            member.case_id: member for member in member_items
        }
        member_results = tuple(
            member_results_by_case[case_id]
            for case_id in case_ids
            if case_id in member_results_by_case
        )
        _write_json(
            members_root / "manifest.json",
            {
                "schema_version": _MEMBER_REPLAY_SCHEMA_V3,
                "repetition_semantics": _PER_MEMBER_REPETITION_SEMANTICS,
                "measurement_stop": replay_measurement_stop,
                "authoritative_stop": authoritative_stop,
                "members": [
                    {
                        "case_id": member.case_id,
                        "path": _member_artifact_name(member.case_id),
                        "baseline_status": member.baseline.status,
                        "candidate_status": member.candidate.status,
                        "blocked_by": list(
                            dict.fromkeys(
                                event.event_id
                                for event in (
                                    *member.baseline.blocked_by,
                                    *member.candidate.blocked_by,
                                )
                            )
                        ),
                    }
                    for member in member_results
                ],
            },
        )
        baseline = _aggregate_member_variant_results(
            base_variant_id="baseline",
            members=member_results,
            select=lambda member: member.baseline,
            artifact_dir=replay_dir / "baseline",
        )
        candidate_result = _aggregate_member_variant_results(
            base_variant_id=candidate.candidate_id,
            members=member_results,
            select=lambda member: member.candidate,
            artifact_dir=replay_dir / _safe_path(candidate.candidate_id),
        )
        logger.info(
            "self_evolve.replay.end "
            f"run_id={request.run_id} task_id={request.task_id} "
            f"candidate_id={candidate.candidate_id} "
            f"baseline_status={baseline.status} candidate_status={candidate_result.status}"
        )
        return CandidateReplayResult(
            request=request,
            baseline=baseline,
            candidate=candidate_result,
            member_results=member_results,
        )

    async def _replay_candidate_measurement_v2(
        self,
        request: CandidateReplayRequest,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
        progress_callback: Callable[[Mapping[str, Any]], None] | None,
    ) -> CandidateReplayResult:
        """Execute a frozen plan through durable, adaptive pair work units."""

        # Keep the control-plane persistence graph out of replay's module import
        # path: store imports regression, which imports this module for dataset
        # identity.  The authoritative path resolves these dependencies only at
        # execution time, after module initialization is complete.
        from aworld.self_evolve.measurement_execution import (
            MeasurementExecutionJournal,
        )
        from aworld.self_evolve.measurement_orchestrator import (
            schedule_staged_measurement,
        )
        from aworld.self_evolve.measurement_scheduler import (
            FrameworkFilesystemLaneMaterializer,
            ResolvedControl,
        )
        from aworld.self_evolve.store import FilesystemSelfEvolveStore

        assert request.measurement_plan is not None
        plan = request.measurement_plan
        store = FilesystemSelfEvolveStore(request.workspace_root)
        stored = store.read_measurement_control_plan(
            request.run_id, plan.measurement_plan_fingerprint
        )
        if stored != plan:
            raise ValueError("measurement replay plan differs from persisted plan")
        experiment = store.read_measurement_experiment(
            request.run_id, plan.experiment_id
        )
        journal = MeasurementExecutionJournal(
            store=store,
            run_id=request.run_id,
            plan=plan,
        )
        journal.recover_expired(now=_utc_now())
        replay_cases = {
            case.case_id: case
            for case in dataset.cases
            if _is_replayable_user_task_case(case)
            and case.case_id in plan.case_ids
        }
        if set(plan.case_ids) - set(replay_cases):
            raise ValueError("measurement plan references unavailable replay cases")
        replay_dir = candidate_replay_artifact_directory(
            workspace_root=request.workspace_root,
            run_id=request.run_id,
            candidate_id=candidate.candidate_id,
            artifact_namespace=request.artifact_namespace,
        )
        members_root = replay_dir / "members"
        replay_dir.mkdir(parents=True, exist_ok=True)
        _write_json(replay_dir / "request.json", request)

        def member_request(item: PairLaneWorkItem) -> CandidateReplayRequest:
            case = replay_cases[item.case_id]
            return replace(
                request,
                task_id=case.case_id,
                task_input=_adapted_task_input(request, case),
                task_input_fingerprint=_adapted_task_input_fingerprint(
                    request, case
                ),
                baseline_repetitions=plan.repetitions_per_case,
                candidate_repetitions=plan.repetitions_per_case,
                measurement_lane_attestations={},
            )

        async def execute_arm(
            item: PairLaneWorkItem,
            context: LaneExecutionContext,
            *,
            arm: MeasurementArm,
        ) -> Mapping[str, Any]:
            work_unit_id = (
                item.control_work_unit_id
                if arm is MeasurementArm.CONTROL
                else item.treatment_work_unit_id
            )
            if context.lane_attestation is None:
                raise ValueError("measurement lane has no materialization proof")
            local_request = replace(
                member_request(item),
                measurement_lane_attestations={
                    work_unit_id: context.lane_attestation
                },
            )
            member_dir = members_root / _member_artifact_name(item.case_id)
            variant_id = (
                "baseline"
                if arm is MeasurementArm.CONTROL
                else candidate.candidate_id
            )
            arm_root = (
                member_dir / "baseline"
                if arm is MeasurementArm.CONTROL
                else member_dir / _safe_path(candidate.candidate_id)
            )
            artifact_dir = arm_root / str(item.repetition_id)
            attempt_id = "measurement-attempt-" + uuid.uuid4().hex
            started = time.monotonic()
            handle = journal.begin(
                work_unit_id=work_unit_id,
                attempt_id=attempt_id,
                now=_utc_now(),
            )
            try:
                try:
                    async with asyncio.timeout(
                        plan.deadlines.member_hard_deadline_seconds
                    ):
                        result = await self._run_variant_with_evidence_retries(
                            local_request,
                            variant_id=variant_id,
                            skill_root=(
                                local_request.baseline_skill_root
                                or _infer_baseline_skill_root(local_request)
                                if arm is MeasurementArm.CONTROL
                                else local_request.overlay_skill_root
                            ),
                            artifact_dir=artifact_dir,
                            measurement_arm=arm,
                            repetition_id=item.repetition_id,
                            progress_callback=_scoped_replay_attempt_callback(
                                progress_callback,
                                candidate_id=candidate.candidate_id,
                                case_id=item.case_id,
                                case_index=plan.case_ids.index(item.case_id) + 1,
                                case_count=len(plan.case_ids),
                                phase=(
                                    "baseline"
                                    if arm is MeasurementArm.CONTROL
                                    else "candidate"
                                ),
                            ),
                        )
                except TimeoutError:
                    result = _member_phase_timeout_result(
                        variant_id=variant_id,
                        phase=(
                            "baseline"
                            if arm is MeasurementArm.CONTROL
                            else "candidate"
                        ),
                        timeout_seconds=plan.deadlines.member_hard_deadline_seconds,
                    )
                    _persist_variant_lifecycle(artifact_dir, result)
                resolved = _persist_measurement_result_projection(
                    artifact_dir,
                    result=result,
                )
                journal.terminal(
                    handle,
                    terminal_state=_measurement_terminal_state_for_variant(
                        result
                    ),
                    result_fingerprint=resolved.result_fingerprint,
                    lane_attestation=context.lane_attestation,
                    now=_utc_now(),
                    attempt_cost_seconds=max(0.0, time.monotonic() - started),
                    reason_code=(
                        None if result.succeeded else "replay_member_failed"
                    ),
                )
                return resolved.value
            except (Exception, asyncio.CancelledError) as exc:
                # Once a lease has entered RUNNING every non-terminal exit must
                # leave a resumable journal record.  Large result projection,
                # persistence, and cancellation failures previously escaped
                # here and stranded the unit in RUNNING forever.
                _persist_measurement_execution_error(
                    artifact_dir,
                    exc=exc,
                    work_unit_id=work_unit_id,
                    arm=arm,
                )
                try:
                    journal.checkpoint(
                        handle,
                        now=_utc_now(),
                        attempt_cost_seconds=max(0.0, time.monotonic() - started),
                        reason_code="measurement_work_unit_finalization_failed",
                    )
                except (OSError, TypeError, ValueError):
                    logger.exception(
                        "self_evolve.measurement.work_unit_checkpoint_failed "
                        f"run_id={request.run_id} work_unit_id={work_unit_id}"
                    )
                raise

        async def run_control(
            item: PairLaneWorkItem, context: LaneExecutionContext
        ) -> Mapping[str, Any]:
            return await execute_arm(item, context, arm=MeasurementArm.CONTROL)

        async def run_treatment(
            item: PairLaneWorkItem,
            context: LaneExecutionContext,
            _control: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            return await execute_arm(item, context, arm=MeasurementArm.TREATMENT)

        async def resolve_control(observation, _context):
            unit = next(
                unit
                for unit in plan.work_units
                if unit.work_unit_id == observation.work_unit_id
            )
            artifact_dir = (
                members_root
                / _member_artifact_name(unit.case_id)
                / "baseline"
                / str(unit.repetition_id)
            )
            return _load_measurement_result_projection(artifact_dir)

        def baseline_control_blocker(
            control: Mapping[str, Any],
        ) -> ReplayFailureEvent | None:
            failure = control.get("failure")
            if not isinstance(failure, Mapping):
                return None
            event = _failure_event_from_persisted_mapping(failure)
            if event.code not in {
                "evidence_policy_v2_attestation_failed",
                "replay_evidence_runtime_policy_violation",
            }:
                return None
            if not (
                event.owner in {
                    FailureOwner.FRAMEWORK,
                    FailureOwner.INFRASTRUCTURE,
                }
                and event.scope is FailureScope.SHARED_RUN
            ):
                # The code describes the policy surface, not causal ownership.
                # A task/member producer failure is a valid negative control
                # and must be allowed to admit its candidate treatment.
                return None
            return ReplayFailureEvent(
                code="baseline_evidence_policy_infeasible",
                owner=FailureOwner.FRAMEWORK,
                stage=FailureStage.EVALUATION,
                scope=FailureScope.SHARED_RUN,
                repairable=True,
                category="measurement_control",
                summary=(
                    "the frozen evidence policy rejected the unchanged control "
                    "before candidate comparison"
                ),
                diagnostics={
                    "control_failure_code": event.code,
                    "control_failure_semantic_key": event.semantic_key,
                    "required_action": (
                        "repair_framework_evidence_contract_from_control"
                    ),
                    "evidence_policy_fingerprint": (
                        plan.evidence_policy_fingerprint
                    ),
                },
                causes=(event.event_id,),
            )

        def control_allows(
            _item: PairLaneWorkItem, control: Mapping[str, Any]
        ) -> bool:
            if baseline_control_blocker(control) is not None:
                return False
            status = control.get("status")
            if status == ReplayExecutionStatus.SUCCEEDED.value:
                return True
            failure = control.get("failure")
            if not isinstance(failure, Mapping):
                return False
            event = _failure_event_from_persisted_mapping(failure)
            return not (
                event.owner in {
                    FailureOwner.FRAMEWORK,
                    FailureOwner.INFRASTRUCTURE,
                }
                or (
                    event.owner is FailureOwner.CANDIDATE
                    and event.stage is not FailureStage.TASK_ROLLOUT
                )
            )

        def stop_on_shared_framework_failure(completed) -> str | None:
            for pair in completed:
                control_blocker = baseline_control_blocker(pair.control)
                if control_blocker is not None:
                    return control_blocker.code
                for result in (pair.control, pair.treatment):
                    if not isinstance(result, Mapping):
                        continue
                    failure = result.get("failure")
                    if not isinstance(failure, Mapping):
                        continue
                    event = _failure_event_from_persisted_mapping(failure)
                    if (
                        event.owner is FailureOwner.INFRASTRUCTURE
                        or event.scope is FailureScope.SHARED_RUN
                    ):
                        return event.code
            return None

        def materialize_skipped_treatments(schedules) -> None:
            entries = {entry.work_unit_id: entry for entry in journal.index_entries()}
            for schedule in schedules:
                resumable_coordinates = {
                    item.coordinate for item in schedule.pending
                }
                for pair in schedule.completed:
                    if (
                        pair.treatment_admitted
                        or pair.item.coordinate in resumable_coordinates
                    ):
                        continue
                    entry = entries[pair.item.treatment_work_unit_id]
                    if entry.state.terminal:
                        continue
                    assert pair.context.lane_attestation is not None
                    baseline = _load_variant_result_from_dir(
                        members_root
                        / _member_artifact_name(pair.item.case_id)
                        / "baseline"
                        / str(pair.item.repetition_id),
                        base_variant_id="baseline",
                    )
                    failure = baseline.failure or ReplayFailureEvent(
                        code="measurement_control_invalid",
                        owner=FailureOwner.FRAMEWORK,
                        stage=FailureStage.EVALUATION,
                        scope=FailureScope.MEMBER,
                        repairable=True,
                        category="measurement_control",
                        summary="treatment was not admitted because control was invalid",
                    )
                    blocked = _blocked_variant_result(
                        candidate.candidate_id, blocked_by=failure
                    )
                    artifact_dir = (
                        members_root
                        / _member_artifact_name(pair.item.case_id)
                        / _safe_path(candidate.candidate_id)
                        / str(pair.item.repetition_id)
                    )
                    _persist_variant_lifecycle(artifact_dir, blocked)
                    handle = journal.begin(
                        work_unit_id=pair.item.treatment_work_unit_id,
                        attempt_id="measurement-cancel-" + uuid.uuid4().hex,
                        now=_utc_now(),
                    )
                    journal.terminal(
                        handle,
                        terminal_state=_measurement_terminal_state_for_variant(
                            blocked
                        ),
                        result_fingerprint=ResolvedControl.from_value(
                            to_json_dict(blocked)
                        ).result_fingerprint,
                        lane_attestation=pair.context.lane_attestation,
                        now=_utc_now(),
                        attempt_cost_seconds=0.0,
                        reason_code="invalid_control_treatment_not_admitted",
                    )

        def load_member_arm(
            case_id: str,
            *,
            arm_name: str,
            base_variant_id: str,
            repetition_ids: tuple[int, ...] | None = None,
        ) -> ReplayVariantResult:
            arm_root = (
                members_root
                / _member_artifact_name(case_id)
                / arm_name
            )
            selected_repetitions = repetition_ids or tuple(
                range(1, plan.repetitions_per_case + 1)
            )
            physical = [
                _load_variant_result_from_dir(
                    arm_root / str(repetition_id),
                    base_variant_id=(
                        base_variant_id
                        if plan.repetitions_per_case == 1
                        else f"{base_variant_id}-{repetition_id}"
                    ),
                )
                for repetition_id in selected_repetitions
            ]
            return _aggregate_variant_results(
                base_variant_id=base_variant_id,
                results=physical,
                artifact_dir=arm_root,
            )

        def current_members(
            *, include_partial: bool = False
        ) -> tuple[CandidateReplayMemberResult, ...]:
            entries = {entry.work_unit_id: entry for entry in journal.index_entries()}
            members: list[CandidateReplayMemberResult] = []
            for case_id in plan.case_ids:
                units = [unit for unit in plan.work_units if unit.case_id == case_id]
                if not units:
                    continue
                terminal_repetitions = tuple(
                    repetition_id
                    for repetition_id in range(1, plan.repetitions_per_case + 1)
                    if (
                        repetition_units := tuple(
                            unit
                            for unit in units
                            if unit.repetition_id == repetition_id
                        )
                    )
                    and all(
                        entries[unit.work_unit_id].state.terminal
                        for unit in repetition_units
                    )
                )
                if not terminal_repetitions or (
                    not include_partial
                    and len(terminal_repetitions) != plan.repetitions_per_case
                ):
                    continue
                item_request = replace(
                    request,
                    task_id=case_id,
                    task_input=_adapted_task_input(request, replay_cases[case_id]),
                    task_input_fingerprint=_adapted_task_input_fingerprint(
                        request, replay_cases[case_id]
                    ),
                    measurement_lane_attestations={},
                )
                members.append(
                    CandidateReplayMemberResult(
                        case_id=case_id,
                        request=item_request,
                        baseline=load_member_arm(
                            case_id,
                            arm_name="baseline",
                            base_variant_id="baseline",
                            repetition_ids=terminal_repetitions,
                        ),
                        candidate=load_member_arm(
                            case_id,
                            arm_name=_safe_path(candidate.candidate_id),
                            base_variant_id=candidate.candidate_id,
                            repetition_ids=terminal_repetitions,
                        ),
                    )
                )
            return tuple(members)

        def framework_blocked_member(
            reason_code: str,
        ) -> CandidateReplayMemberResult:
            case_id = plan.case_ids[0]
            failure = ReplayFailureEvent(
                code=reason_code,
                owner=FailureOwner.FRAMEWORK,
                stage=FailureStage.EVALUATION,
                scope=FailureScope.SHARED_RUN,
                repairable=True,
                category="measurement_control",
                summary=(
                    "authoritative measurement stopped before a complete "
                    "case aggregate was available"
                ),
                diagnostics={
                    "measurement_plan_fingerprint": (
                        plan.measurement_plan_fingerprint
                    ),
                    "resume_safe": True,
                },
            )
            item_request = replace(
                request,
                task_id=case_id,
                task_input=_adapted_task_input(request, replay_cases[case_id]),
                task_input_fingerprint=_adapted_task_input_fingerprint(
                    request, replay_cases[case_id]
                ),
                measurement_lane_attestations={},
            )
            return CandidateReplayMemberResult(
                case_id=case_id,
                request=item_request,
                baseline=_blocked_variant_result(
                    "baseline", blocked_by=failure
                ),
                candidate=_blocked_variant_result(
                    candidate.candidate_id, blocked_by=failure
                ),
            )

        def partial_result(
            members: tuple[CandidateReplayMemberResult, ...],
            *,
            measurement_decision: Mapping[str, Any] | None = None,
        ) -> CandidateReplayResult:
            return CandidateReplayResult(
                request=request,
                baseline=_aggregate_member_variant_results(
                    base_variant_id="baseline",
                    members=members,
                    select=lambda member: member.baseline,
                    artifact_dir=replay_dir / "baseline",
                ),
                candidate=_aggregate_member_variant_results(
                    base_variant_id=candidate.candidate_id,
                    members=members,
                    select=lambda member: member.candidate,
                    artifact_dir=replay_dir / _safe_path(candidate.candidate_id),
                ),
                member_results=members,
                measurement_decision=measurement_decision,
            )

        primary_effect_case_ids = {
            case_id
            for stage in plan.stages
            if stage.kind is not SamplingStageKind.REGRESSION_TRANSFER
            for case_id in stage.case_ids
        }
        latest_primary_effect = None

        def primary_metric_value(result: ReplayVariantResult) -> float | None:
            metric = experiment.outcomes.primary_metric
            if metric == "task_success":
                return 1.0 if result.succeeded else 0.0 if result.executed else None
            value = result.metrics.get(metric)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            ):
                return float(value)
            return None

        def build_progress(_plan, current_stage_id, schedules):
            nonlocal latest_primary_effect
            materialize_skipped_treatments(schedules)
            members = current_members()
            completed_case_ids = tuple(member.case_id for member in members)
            comparable_case_ids = tuple(
                member.case_id
                for member in members
                if _replay_member_pair_is_comparable(
                    replay_cases[member.case_id],
                    member.baseline,
                    member.candidate,
                )
            )
            invalid_control_case_ids = tuple(
                member.case_id
                for member in members
                if _baseline_invalid_for_measurement(member.baseline)
            )
            framework_blocker = next(
                (
                    blocker
                    for member in members
                    for blocker in (
                        baseline_control_blocker(
                            _measurement_result_projection(
                                member.baseline,
                                artifact_dir=(
                                    members_root
                                    / _member_artifact_name(member.case_id)
                                    / "baseline"
                                ),
                                include_artifact_references=False,
                            )
                        ),
                        member.baseline.failure,
                        member.candidate.failure,
                    )
                    if isinstance(blocker, ReplayFailureEvent)
                    and (
                        blocker.owner is FailureOwner.INFRASTRUCTURE
                        or blocker.scope is FailureScope.SHARED_RUN
                    )
                ),
                None,
            )
            primary_members = tuple(
                member
                for member in members
                if member.case_id in primary_effect_case_ids
                and member.case_id in comparable_case_ids
            )
            if primary_members:
                observations = observations_from_replay(
                    experiment,
                    dataset=dataset,
                    replay_result=partial_result(primary_members),
                    run_root=replay_dir,
                )
                validity = assess_experiment_validity(
                    experiment,
                    observations,
                    admitted_primary_case_ids=tuple(
                        member.case_id for member in primary_members
                    ),
                )
                latest_primary_effect = estimate_paired_effect(
                    experiment,
                    observations,
                    validity=validity,
                )
            effect = latest_primary_effect
            completed_stage_ids = tuple(
                stage.stage_id
                for stage in plan.stages
                if set(stage.case_ids).issubset(completed_case_ids)
            )
            current_stage = next(
                stage for stage in plan.stages if stage.stage_id == current_stage_id
            )
            current_schedule = schedules[-1]
            elapsed_samples = tuple(
                max(0.001, pair.elapsed_seconds)
                for schedule in schedules
                for pair in schedule.completed
            )
            default_predicted_cost = (
                sum(elapsed_samples) / len(elapsed_samples)
                if elapsed_samples
                else plan.deadlines.member_hard_deadline_seconds
            )
            invalid_rate = (
                len(invalid_control_case_ids) / len(completed_case_ids)
                if completed_case_ids
                else 0.0
            )

            def case_stratum(case: EvalCase) -> str:
                values: list[str] = []
                for namespace, metadata in (
                    ("metadata", case.metadata),
                    ("source", case.source),
                ):
                    for key in (
                        "task_type",
                        "category",
                        "cluster",
                        "domain",
                        "kind",
                        "role",
                    ):
                        value = metadata.get(key)
                        if isinstance(value, (str, int, float)) and not isinstance(
                            value, bool
                        ):
                            values.append(f"{namespace}:{key}:{value}")
                identity = "|".join(sorted(values)) or "default"
                return "stratum-" + hashlib.sha256(
                    identity.encode("utf-8")
                ).hexdigest()[:16]

            completed_strata: dict[str, int] = {}
            for case_id in completed_case_ids:
                stratum = case_stratum(replay_cases[case_id])
                completed_strata[stratum] = completed_strata.get(stratum, 0) + 1

            def measurement_hint(
                case: EvalCase,
                key: str,
                *,
                lower: float,
                upper: float,
                fallback: float,
            ) -> float:
                contract = case.metadata.get("measurement")
                raw = contract.get(key) if isinstance(contract, Mapping) else None
                if (
                    isinstance(raw, (int, float))
                    and not isinstance(raw, bool)
                    and math.isfinite(float(raw))
                    and lower <= float(raw) <= upper
                ):
                    return float(raw)
                return fallback

            admission_signals: list[CaseAdmissionSignal] = []
            for case_id in plan.case_ids:
                if case_id in completed_case_ids:
                    continue
                case = replay_cases[case_id]
                stratum = case_stratum(case)
                novelty = 1.0 / (1.0 + completed_strata.get(stratum, 0))
                expected_information = measurement_hint(
                    case,
                    "expected_information_value",
                    lower=0.0,
                    upper=100.0,
                    fallback=1.0,
                ) * novelty
                predicted_cost = measurement_hint(
                    case,
                    "predicted_cost_seconds",
                    lower=0.001,
                    upper=max(
                        0.001,
                        plan.deadlines.member_hard_deadline_seconds * 2.0,
                    ),
                    fallback=default_predicted_cost,
                )
                failure_risk = measurement_hint(
                    case,
                    "failure_risk",
                    lower=0.0,
                    upper=1.0,
                    fallback=invalid_rate,
                )
                prior_variance = measurement_hint(
                    case,
                    "prior_variance",
                    lower=0.0,
                    upper=100.0,
                    fallback=1.0,
                )
                admission_signals.append(
                    CaseAdmissionSignal(
                        case_id=case_id,
                        stratum_id=stratum,
                        expected_information_value=expected_information,
                        predicted_cost_seconds=predicted_cost,
                        failure_risk=failure_risk,
                        prior_variance=prior_variance,
                    )
                )
            regression_detected = False
            if current_stage.kind is SamplingStageKind.REGRESSION_TRANSFER:
                transfer_members = tuple(
                    member
                    for member in members
                    if member.case_id in current_stage.case_ids
                )
                for member in transfer_members:
                    control_value = primary_metric_value(member.baseline)
                    treatment_value = primary_metric_value(member.candidate)
                    if control_value is None or treatment_value is None:
                        regression_detected = True
                        break
                    signed_delta = treatment_value - control_value
                    if not experiment.outcomes.higher_is_better:
                        signed_delta = -signed_delta
                    if signed_delta < -experiment.outcomes.non_regression_threshold:
                        regression_detected = True
                        break
            return MeasurementProgressSummary(
                current_stage_id=current_stage_id,
                completed_case_ids=completed_case_ids,
                comparable_case_ids=comparable_case_ids,
                invalid_control_case_ids=invalid_control_case_ids,
                confidence_lower_bound=(
                    effect.confidence_lower_bound if effect is not None else None
                ),
                point_estimate=(effect.point_estimate if effect is not None else None),
                regression_detected=regression_detected,
                negative_effect_detected=(
                    effect is not None and effect.direction is EffectDirection.NEGATIVE
                ),
                futility_proven=False,
                new_comparable_pairs_in_window=sum(
                    1
                    for pair in current_schedule.completed
                    if pair.treatment_admitted
                ),
                uncertainty_reduction_in_window=(
                    1.0 if current_schedule.completed else 0.0
                ),
                current_stage_exhausted=not current_schedule.pending,
                completed_stage_ids=completed_stage_ids,
                checkpoint_quantum_expired=False,
                campaign_wall_deadline_expired=False,
                resume_safe=True,
                framework_blocked_reason_code=(
                    framework_blocker.code if framework_blocker is not None else None
                ),
                case_admission_signals=tuple(admission_signals),
            )

        staged = await schedule_staged_measurement(
            store,
            run_id=request.run_id,
            measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
            run_control=run_control,
            run_treatment=run_treatment,
            progress_builder=build_progress,
            lane_materializer=FrameworkFilesystemLaneMaterializer(
                replay_dir / "measurement-lanes"
            ),
            resolve_reused_control=resolve_control,
            control_allows_treatment=control_allows,
            pair_should_stop=stop_on_shared_framework_failure,
            pair_decisive_stop_kind=(
                AdaptiveDecisionKind.STOP_FRAMEWORK_BLOCKED
            ),
            checkpoint_quantum_seconds=plan.deadlines.checkpoint_quantum_seconds,
            campaign_deadline_monotonic=(
                time.monotonic() + plan.deadlines.campaign_wall_deadline_seconds
                if plan.deadlines.campaign_wall_deadline_seconds is not None
                else None
            ),
            materialization_timeout_seconds=plan.deadlines.attempt_timeout_seconds,
            configured_lane_limit=2,
        )
        framework_stopped = (
            staged.decision.kind
            is AdaptiveDecisionKind.STOP_FRAMEWORK_BLOCKED
        )
        members = current_members(include_partial=framework_stopped)
        if not members and framework_stopped:
            members = (
                framework_blocked_member(staged.decision.reason_code),
            )
        if not members:
            raise RuntimeError(
                "measurement scheduler stopped before producing a complete pair"
            )
        invalid_control_case_ids = tuple(
            member.case_id
            for member in members
            if _baseline_invalid_for_measurement(member.baseline)
        )
        baseline_qualified_members = tuple(
            member
            for member in members
            if member.case_id not in invalid_control_case_ids
        )
        minimum_independent_cases = plan.decision_policy.minimum_independent_cases
        result_members = (
            baseline_qualified_members
            if len(baseline_qualified_members) >= minimum_independent_cases
            else members
        )
        measurement_decision = {
            **to_json_dict(staged.decision),
            "invalid_control_case_ids": list(invalid_control_case_ids),
            "baseline_qualified_case_ids": [
                member.case_id for member in baseline_qualified_members
            ],
            "minimum_independent_cases": minimum_independent_cases,
        }
        framework_blocker = next(
            (
                blocker
                for member in members
                for blocker in (
                    baseline_control_blocker(
                        _measurement_result_projection(
                            member.baseline,
                            artifact_dir=(
                                members_root
                                / _member_artifact_name(member.case_id)
                                / "baseline"
                            ),
                            include_artifact_references=False,
                        )
                    ),
                    member.baseline.failure,
                    member.candidate.failure,
                )
                if isinstance(blocker, ReplayFailureEvent)
                and (
                    blocker.owner is FailureOwner.INFRASTRUCTURE
                    or blocker.scope is FailureScope.SHARED_RUN
                )
            ),
            None,
        )
        if framework_stopped or framework_blocker is not None:
            # Invalid controls caused by the measurement framework are not
            # statistical invalidity and must never spend a candidate or
            # measurement-retry opportunity. Preserve the scheduler stop as a
            # diagnostic while projecting the actual causal owner to Campaign.
            measurement_decision = {
                "kind": "stop_framework_blocked",
                "reason_code": (
                    staged.decision.reason_code
                    if framework_stopped
                    else framework_blocker.code
                ),
                "resume_safe": staged.decision.resume_safe,
                "failure_owner": (
                    framework_blocker.owner.value
                    if framework_blocker is not None
                    else FailureOwner.FRAMEWORK.value
                ),
                "failure_scope": (
                    framework_blocker.scope.value
                    if framework_blocker is not None
                    else FailureScope.SHARED_RUN.value
                ),
                "scheduler_decision": to_json_dict(staged.decision),
            }
        result = partial_result(
            result_members,
            measurement_decision=measurement_decision,
        )
        _write_json(
            members_root / "measurement_schedule.json",
            {
                "schema_version": "aworld.self_evolve.measurement_schedule.v2",
                "decision": measurement_decision,
                "decision_history": [
                    to_json_dict(decision)
                    for decision in staged.decision_history[:64]
                ],
                "decision_history_truncated": len(staged.decision_history) > 64,
                "admitted_case_ids": list(staged.admitted_case_ids),
                "schedule_count": len(staged.schedules),
                "scheduling_policy": "information-cost-risk-v1",
                "schedules": [
                    {
                        "stop_kind": schedule.stop_kind.value,
                        "stop_reason": schedule.stop_reason,
                        "safe_lane_count": schedule.safe_lane_count,
                        "completed_pair_count": len(schedule.completed),
                        "pending_pair_count": len(schedule.pending),
                        "elapsed_seconds": schedule.elapsed_seconds,
                        "pairs": [
                            {
                                "case_id": pair.item.case_id,
                                "repetition_id": pair.item.repetition_id,
                                "treatment_admitted": pair.treatment_admitted,
                                "elapsed_seconds": pair.elapsed_seconds,
                                "scheduling_score": pair.scheduling_score,
                            }
                            for pair in schedule.completed[:128]
                        ],
                        "pair_projection_truncated": len(schedule.completed) > 128,
                    }
                    for schedule in staged.schedules[:32]
                ],
                "schedule_projection_truncated": len(staged.schedules) > 32,
            },
        )
        return result

    async def _load_or_run_baseline(
        self,
        request: CandidateReplayRequest,
        *,
        candidate: CandidateVariant,
        replay_dir: Path,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> ReplayVariantResult:
        baseline_cache_status = "not_offered"
        if request.baseline_replay_dir and _stored_baseline_matches_request(request):
            baseline_cache_status = "hit"
            baseline = _load_variant_result_from_dir(
                Path(request.baseline_replay_dir),
                base_variant_id="baseline",
            )
            logger.info(
                "self_evolve.replay.baseline.reuse "
                f"run_id={request.run_id} task_id={request.task_id} "
                f"candidate_id={candidate.candidate_id} "
                f"baseline_replay_dir={request.baseline_replay_dir}"
            )
        else:
            if request.baseline_replay_dir:
                baseline_cache_status = "rejected"
                logger.info(
                    "self_evolve.replay.baseline.reuse_skip "
                    f"run_id={request.run_id} task_id={request.task_id} "
                    f"candidate_id={candidate.candidate_id} "
                    "reason=missing_or_mismatched_replay_provenance"
                )
            baseline = await self._run_repetitions(
                request,
                base_variant_id="baseline",
                skill_root=request.baseline_skill_root or _infer_baseline_skill_root(request),
                artifact_dir=replay_dir / "baseline",
                repetitions=request.baseline_repetitions,
                progress_callback=progress_callback,
            )
        return replace(
            baseline,
            metrics={
                **dict(baseline.metrics),
                "baseline_cache_status": baseline_cache_status,
                "baseline_control_fingerprint": (
                    baseline_control_fingerprint(request)
                ),
            },
        )

    async def _run_repetitions(
        self,
        request: CandidateReplayRequest,
        *,
        base_variant_id: str,
        skill_root: str | None,
        artifact_dir: Path,
        repetitions: int,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> ReplayVariantResult:
        if repetitions <= 0:
            raise ValueError("replay repetitions must be positive")
        logger.info(
            "self_evolve.replay.repetitions.start "
            f"run_id={request.run_id} task_id={request.task_id} "
            f"variant_id={base_variant_id} repetitions={repetitions}"
        )
        task_items: list[TaskBatchItem] = []
        for index in range(1, repetitions + 1):
            variant_id = base_variant_id if repetitions == 1 else f"{base_variant_id}-{index}"
            repetition_dir = artifact_dir if repetitions == 1 else artifact_dir / str(index)
            logger.info(
                "self_evolve.replay.repetition.start "
                f"run_id={request.run_id} task_id={request.task_id} "
                f"variant_id={variant_id} index={index}/{repetitions}"
            )
            task_input = ReplayRepetitionTaskInput(
                backend=self,
                request=request,
                variant_id=variant_id,
                skill_root=skill_root,
                artifact_dir=repetition_dir,
                measurement_arm=(
                    MeasurementArm.CONTROL
                    if base_variant_id == "baseline"
                    else MeasurementArm.TREATMENT
                ),
                repetition_id=index,
                progress_callback=progress_callback,
            )
            task_id = (
                f"self-evolve-replay-{_safe_path(request.run_id)}-"
                f"{_safe_path(request.task_id)}-{_safe_path(variant_id)}"
            )
            task_items.append(
                TaskBatchItem(
                    index=index - 1,
                    task=Task(
                        id=task_id,
                        session_id=task_id,
                        input=task_input,
                        context=LocalIsolatedApplicationContext.create(
                            task_id=task_id,
                            session_id=task_id,
                            task_content="isolated self-evolve replay repetition",
                        ),
                        runner_cls=(
                            "aworld.self_evolve.runtime.SelfEvolveReplayTaskRunner"
                        ),
                    ),
                    resource_claims=_replay_resource_claims(request),
                )
            )
        batch_results = await self.task_batch_executor.run(
            task_items,
            max_concurrency=self.concurrency_policy.effective_limit(
                "replay",
                item_count=len(task_items),
            ),
            failure_policy="collect_all",
        )
        self.last_replay_batch_observability = dict(
            self.task_batch_executor.last_run_observability
        )
        self.replay_batch_observability.append(
            dict(self.last_replay_batch_observability)
        )
        results: list[ReplayVariantResult] = []
        for index, batch_result in enumerate(batch_results, start=1):
            if (
                batch_result.status != "succeeded"
                or batch_result.response is None
                or not isinstance(batch_result.response.answer, ReplayVariantResult)
            ):
                raise RuntimeError(
                    "required replay repetition failed "
                    f"at index {index} ({batch_result.error_type or 'TaskFailed'})"
                )
            results.append(batch_result.response.answer)
            variant_id = results[-1].variant_id
            logger.info(
                "self_evolve.replay.repetition.end "
                f"run_id={request.run_id} task_id={request.task_id} "
                f"variant_id={variant_id} index={index}/{repetitions} "
                f"status={results[-1].status}"
            )
        aggregated = _aggregate_variant_results(
            base_variant_id=base_variant_id,
            results=results,
            artifact_dir=artifact_dir,
        )
        logger.info(
            "self_evolve.replay.repetitions.end "
            f"run_id={request.run_id} task_id={request.task_id} "
            f"variant_id={base_variant_id} repetitions={repetitions} "
            f"status={aggregated.status}"
        )
        return aggregated

    async def _run_variant_with_evidence_retries(
        self,
        request: CandidateReplayRequest,
        *,
        variant_id: str,
        skill_root: str | None,
        artifact_dir: Path,
        measurement_arm: MeasurementArm | None = None,
        repetition_id: int = 1,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> ReplayVariantResult:
        attempts: list[ReplayVariantResult] = []
        if measurement_arm is None:
            measurement_arm = (
                MeasurementArm.CONTROL
                if variant_id == "baseline" or variant_id.startswith("baseline-")
                else MeasurementArm.TREATMENT
            )
        retry_attempt_limit = max(
            _EVIDENCE_RETRY_LIMIT,
            _SERVICE_STARTUP_RETRY_LIMIT,
        )
        for attempt_index in range(1, retry_attempt_limit + 2):
            attempt_variant_id = (
                variant_id
                if attempt_index == 1
                else f"{variant_id}__evidence_retry_{attempt_index}"
            )
            attempt_dir = (
                artifact_dir
                if attempt_index == 1
                else artifact_dir / f"evidence_retry_{attempt_index}"
            )
            _emit_replay_attempt_progress(
                progress_callback,
                event="replay_attempt_started",
                variant_id=variant_id,
                attempt_index=attempt_index,
                attempt_limit=retry_attempt_limit + 1,
                attempt_timeout_seconds=request.timeout_seconds,
            )
            run_kwargs: dict[str, Any] = {
                "variant_id": attempt_variant_id,
                "skill_root": skill_root,
                "artifact_dir": attempt_dir,
                "measurement_arm": measurement_arm,
                "repetition_id": repetition_id,
            }
            result = await self._run_variant(request, **run_kwargs)
            attempts.append(result)
            _emit_replay_attempt_progress(
                progress_callback,
                event="replay_attempt_completed",
                variant_id=variant_id,
                attempt_index=attempt_index,
                attempt_limit=retry_attempt_limit + 1,
                attempt_timeout_seconds=request.timeout_seconds,
                status=result.status.value,
            )
            evidence_failure = _is_evidence_quality_failure(result)
            framework_capture_failure = (
                _is_retryable_framework_capture_failure(result)
            )
            service_startup_failure = (
                _is_retryable_replay_service_startup_failure(result)
            )
            if (
                not evidence_failure
                and not framework_capture_failure
                and not service_startup_failure
            ):
                return _merge_replay_attempt_metrics(
                    result,
                    attempts=attempts,
                    canonical_variant_id=variant_id,
                )
            retry_limit = (
                _SERVICE_STARTUP_RETRY_LIMIT
                if service_startup_failure
                else _EVIDENCE_RETRY_LIMIT
            )
            if attempt_index <= retry_limit:
                retry_event = (
                    "self_evolve.replay.evidence_retry"
                    if evidence_failure
                    else (
                        "self_evolve.replay.framework_capture_retry"
                        if framework_capture_failure
                        else "self_evolve.replay.service_startup_retry"
                    )
                )
                logger.info(
                    f"{retry_event} "
                    f"run_id={request.run_id} task_id={request.task_id} "
                    f"variant_id={variant_id} attempt={attempt_index + 1}"
                )
        return _merge_replay_attempt_metrics(
            attempts[-1],
            attempts=attempts,
            canonical_variant_id=variant_id,
        )

    async def _run_variant(
        self,
        request: CandidateReplayRequest,
        *,
        variant_id: str,
        skill_root: str | None,
        artifact_dir: Path,
        measurement_arm: MeasurementArm | None = None,
        repetition_id: int = 1,
    ) -> ReplayVariantResult:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        workspace_root = request.workspace_root
        task_input = request.task_input
        environment: dict[str, str] = {}
        adaptation_fingerprint: str | None = None
        workspace_seed_fingerprint: str | None = None
        task_input_fingerprint: str | None = None
        adapter_determinism: str | None = None
        isolated_workspace_path: str | None = None
        case_adaptation = None
        if request.replay_adaptation is not None:
            case_adaptation = request.replay_adaptation.case(request.task_id)
            isolated_workspace = materialize_replay_workspace(
                request.replay_adaptation,
                artifact_dir / "workspace",
            )
            workspace_root = str(isolated_workspace)
            task_input = _expand_replay_placeholders(
                request.task_input,
                workspace_root=isolated_workspace,
                artifact_dir=artifact_dir,
            )
            environment = _adapter_environment(case_adaptation.bindings)
            environment = {
                key: str(
                    _expand_replay_placeholders(
                        value,
                        workspace_root=isolated_workspace,
                        artifact_dir=artifact_dir,
                    )
                )
                for key, value in environment.items()
            }
            environment.update(
                {
                    "AWORLD_REPLAY_WORKSPACE": str(isolated_workspace),
                    "AWORLD_REPLAY_ARTIFACT_DIR": str(artifact_dir),
                }
            )
            adaptation_fingerprint = request.replay_adaptation.adaptation_fingerprint
            workspace_seed_fingerprint = (
                request.replay_adaptation.workspace_seed_fingerprint
            )
            task_input_fingerprint = case_adaptation.task_input_fingerprint
            adapter_determinism = (
                "deterministic"
                if case_adaptation.readiness == "ready"
                and all(binding.deterministic for binding in case_adaptation.bindings)
                else "non_deterministic"
            )
            isolated_workspace_path = str(isolated_workspace)
        effective_measurement_arm = measurement_arm or (
            MeasurementArm.CONTROL
            if (
                variant_id == "baseline"
                or variant_id.startswith("baseline-")
                or variant_id.startswith("baseline__evidence_retry_")
            )
            else MeasurementArm.TREATMENT
        )
        expected_skill_package_fingerprint = (
            request.verified_candidate_package_fingerprint
            if effective_measurement_arm is MeasurementArm.TREATMENT
            else request.baseline_skill_fingerprint
        )
        execution_skill_root = _materialize_task_skill_mount(
            skill_root=skill_root,
            skill_name=request.target.target_id,
            artifact_dir=artifact_dir,
            expected_package_fingerprint=(
                expected_skill_package_fingerprint
            ),
        )
        service_session: _ReplayServiceSession | None = None
        service_failure: Mapping[str, Any] | None = None
        service_cleanup_status = "not_required"
        service_cleanup_failure: Mapping[str, Any] | None = None
        replay_capability = (
            request.replay_adaptation.replay_capability
            if request.replay_adaptation is not None
            else None
        )
        execution_replay_capability = replay_capability
        if (
            replay_capability is not None
            and case_adaptation is not None
            and request.measurement_evidence_policy_profile is None
        ):
            execution_replay_capability = _project_replay_capability_for_case(
                replay_capability,
                task_input=task_input,
                dependency_ids=tuple(
                    binding.dependency_id
                    for binding in case_adaptation.bindings
                ),
            )
        replay_services_required = bool(
            execution_replay_capability is not None
            and execution_replay_capability.services
        )
        if replay_services_required:
            assert execution_replay_capability is not None
            try:
                service_session = await _start_replay_services(
                    execution_replay_capability,
                    artifact_dir=artifact_dir,
                    endpoint_bindings=(
                        request.measurement_evidence_policy_profile.endpoint_bindings
                        if request.measurement_evidence_policy_profile is not None
                        else ()
                    ),
                    integrity_capability=(
                        replay_capability
                        if execution_replay_capability is not replay_capability
                        else None
                    ),
                )
                endpoint_urls = {
                    source: service_session.endpoints[service_id]
                    for source, service_id in (
                        execution_replay_capability.endpoint_replacements.items()
                    )
                }
                task_input = _replace_replay_endpoints(task_input, endpoint_urls)
                environment.update(service_session.environment)
            except Exception as exc:
                service_failure_details = _replay_service_start_failure_details(
                    exc,
                    replay_capability=execution_replay_capability,
                )
                fixture_summaries = _replay_capability_fixture_summaries(
                    execution_replay_capability
                )
                if fixture_summaries:
                    diagnostics = dict(
                        service_failure_details.get("diagnostics") or {}
                    )
                    diagnostics.update({
                        "replay_fixture_summaries": fixture_summaries,
                    })
                    service_failure_details["diagnostics"] = diagnostics
                service_failure = service_failure_details
        measurement_work_unit = _measurement_work_unit_for_replay(
            request,
            arm=effective_measurement_arm,
            repetition_id=repetition_id,
        )
        if service_session is not None:
            service_startup_status = "ready"
        elif replay_services_required:
            service_startup_status = "failed"
        elif replay_capability is not None:
            service_startup_status = "not_required"
        else:
            service_startup_status = None
        execution_request = ReplayExecutionRequest(
            variant_id=variant_id,
            task_id=request.task_id,
            candidate_id=request.candidate_id,
            workspace_root=workspace_root,
            task_input=task_input,
            task_text=_task_text(task_input),
            skill_root=execution_skill_root,
            artifact_dir=str(artifact_dir),
            variant_role=(
                "baseline"
                if effective_measurement_arm is MeasurementArm.CONTROL
                else "candidate"
            ),
            skill_names=(
                (request.target.target_id,)
                if skill_root and request.target.target_id
                else ()
            ),
            agent=request.agent,
            timeout_seconds=request.timeout_seconds,
            max_steps=request.max_steps,
            max_tool_calls=request.max_tool_calls,
            max_tokens=request.max_tokens,
            max_cost_usd=request.max_cost_usd,
            environment=environment,
            adaptation_fingerprint=adaptation_fingerprint,
            support_fingerprint=request.support_fingerprint,
            timeout_envelope_fingerprint=(
                request.timeout_envelope_fingerprint
            ),
            workspace_seed_fingerprint=workspace_seed_fingerprint,
            task_input_fingerprint=task_input_fingerprint,
            dataset_fingerprint=request.dataset_fingerprint,
            baseline_skill_fingerprint=request.baseline_skill_fingerprint,
            expected_skill_package_fingerprint=(
                expected_skill_package_fingerprint
            ),
            adapter_determinism=adapter_determinism,
            isolated_workspace_path=isolated_workspace_path,
            replay_capability_id=(
                replay_capability.capability_id
                if replay_capability is not None
                else None
            ),
            capability_package_fingerprint=(
                replay_capability.capability_package_fingerprint
                if replay_capability is not None
                else None
            ),
            frozen_capability_fingerprint=(
                replay_capability.fingerprint
                if replay_capability is not None
                else None
            ),
            service_runtime_fingerprint=(
                replay_capability.fingerprint
                if replay_capability is not None
                else None
            ),
            service_logical_ids=(
                json.dumps(
                    sorted(service_session.endpoints),
                    separators=(",", ":"),
                )
                if service_session is not None
                else None
            ),
            service_endpoint=(
                json.dumps(
                    dict(sorted(service_session.endpoints.items())),
                    separators=(",", ":"),
                )
                if service_session is not None
                else None
            ),
            service_startup_status=service_startup_status,
            framework_endpoint_bindings=(
                _framework_resolved_endpoint_bindings(
                    request.measurement_evidence_policy_profile,
                    environment=environment,
                    service_endpoints=(
                        service_session.endpoints
                        if service_session is not None
                        else {}
                    ),
                )
            ),
            evidence_policy_mode=request.evidence_policy_mode,
            measurement_plan_fingerprint=(
                request.measurement_plan.measurement_plan_fingerprint
                if request.measurement_plan is not None
                else None
            ),
            measurement_work_unit=measurement_work_unit,
            measurement_evidence_policy_profile=(
                request.measurement_evidence_policy_profile
            ),
            isolation_grant_fingerprint=(
                request.measurement_lane_attestations[
                    measurement_work_unit.work_unit_id
                ].isolation_grant_fingerprint
                if measurement_work_unit is not None
                else None
            ),
            lane_materialization_fingerprint=(
                request.measurement_lane_attestations[
                    measurement_work_unit.work_unit_id
                ].attestation_fingerprint
                if measurement_work_unit is not None
                else None
            ),
            evidence_finalization_timeout_seconds=(
                request.measurement_plan.deadlines.evidence_finalization_timeout_seconds
                if request.measurement_plan is not None
                else None
            ),
        )
        _write_json(artifact_dir / "execution_request.json", execution_request)
        started_at = time.monotonic()
        try:
            if service_failure is not None:
                execution_result = ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    failure=service_failure,
                )
            else:
                execution_result = self.executor(execution_request)
                if inspect.isawaitable(execution_result):
                    execution_result = await execution_result
        except Exception as exc:
            execution_result = ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": type(exc).__name__,
                    "reason": str(exc),
                },
            )
        finally:
            if service_session is not None:
                try:
                    await service_session.stop()
                    service_cleanup_status = "stopped"
                except Exception as exc:
                    service_cleanup_status = "failed"
                    service_cleanup_failure = {
                        "type": type(exc).__name__,
                        "reason": str(exc),
                        "outcome": "infrastructure_failure",
                    }

        if service_cleanup_failure is not None:
            execution_result = ReplayExecutionResult(
                status="failed",
                trajectory=execution_result.trajectory,
                metrics=execution_result.metrics,
                stdout=execution_result.stdout,
                stderr=execution_result.stderr,
                failure=service_cleanup_failure,
            )
        if not isinstance(execution_result, ReplayExecutionResult):
            raise ValueError("replay executor must return ReplayExecutionResult")
        execution_result = _attach_replay_service_protocol_diagnostics(
            execution_result,
            artifact_dir=artifact_dir,
        )
        execution_result = _classify_candidate_task_rollout_nontermination(
            execution_result,
            variant_id=variant_id,
        )

        metrics = {
            "latency_ms": (time.monotonic() - started_at) * 1000,
            **dict(execution_result.metrics),
            **_replay_execution_provenance(execution_request),
        }
        if replay_capability is not None:
            metrics["service_cleanup_status"] = service_cleanup_status
        status = execution_result.status
        failure = execution_result.failure
        if status == "succeeded" and not execution_result.trajectory:
            status = "failed"
            failure = {
                "code": "trajectory_capture_unavailable",
                "outcome": "framework_failure",
                "failure_stage": "evaluation",
                "repairable": True,
                "reason": "trajectory_capture_unavailable",
                "detail": "replay executor succeeded but did not return trajectory evidence",
            }
        evidence_failure = _evidence_quality_failure(
            metrics,
            variant_id=variant_id,
            variant_role=execution_request.variant_role,
        )
        if status == "succeeded" and evidence_failure is not None:
            status = "failed"
            failure = evidence_failure

        stdout_path = artifact_dir / "stdout.txt"
        stderr_path = artifact_dir / "stderr.txt"
        stdout_path.write_text(execution_result.stdout, encoding="utf-8")
        stderr_path.write_text(execution_result.stderr, encoding="utf-8")
        _write_json(artifact_dir / "metrics.json", metrics)
        _write_json(artifact_dir / "trajectory.json", execution_result.trajectory)
        if status not in {
            ReplayExecutionStatus.SUCCEEDED.value,
            ReplayExecutionStatus.FAILED.value,
        }:
            status = ReplayExecutionStatus.FAILED.value
            failure = {
                "type": "ReplayExecutionContractError",
                "reason": "replay executor returned an unsupported execution status",
            }
        failure_event = (
            _execution_failure_event(
                failure,
                default_stage=FailureStage.TASK_ROLLOUT,
                service_preflight=service_failure is not None,
            )
            if status == ReplayExecutionStatus.FAILED.value
            else None
        )
        result = ReplayVariantResult(
            variant_id=variant_id,
            status=status,
            trajectory=execution_result.trajectory,
            metrics=metrics,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            failure=failure_event,
        )
        _persist_variant_lifecycle(artifact_dir, result)
        return result


def _stored_baseline_matches_request(request: CandidateReplayRequest) -> bool:
    if request.baseline_replay_dir is None:
        return False
    provenance_keys = (
        "baseline_skill_fingerprint",
        "dataset_fingerprint",
        "support_fingerprint",
        "timeout_envelope_fingerprint",
        "workspace_seed_fingerprint",
        "task_input_fingerprint",
    )
    if any(getattr(request, key) is None for key in provenance_keys):
        return False
    if (
        request.support_fingerprint
        != replay_support_fingerprint(request.replay_adaptation)
        or request.timeout_envelope_fingerprint
        != replay_timeout_envelope_fingerprint(
            timeout_seconds=request.timeout_seconds,
            max_steps=request.max_steps,
            max_tool_calls=request.max_tool_calls,
        )
    ):
        return False
    request_path = Path(request.baseline_replay_dir).parent / "request.json"
    if not request_path.is_file():
        return False
    try:
        stored = _candidate_replay_request_from_mapping(
            _load_json_object(request_path)
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return False
    if stored.task_id != request.task_id:
        return False
    if not (
        _has_authoritative_per_member_repetitions(request)
        and _has_authoritative_per_member_repetitions(stored)
    ):
        return False
    if (
        stored.target.target_type != request.target.target_type
        or stored.target.target_id != request.target.target_id
    ):
        return False
    if stored.baseline_repetitions != request.baseline_repetitions:
        return False
    if not all(
        getattr(stored, key) == getattr(request, key)
        for key in provenance_keys
    ):
        return False
    if baseline_control_fingerprint(stored) != baseline_control_fingerprint(
        request
    ):
        return False
    if (
        stored.support_fingerprint
        != replay_support_fingerprint(stored.replay_adaptation)
        or stored.timeout_envelope_fingerprint
        != replay_timeout_envelope_fingerprint(
            timeout_seconds=stored.timeout_seconds,
            max_steps=stored.max_steps,
            max_tool_calls=stored.max_tool_calls,
        )
    ):
        return False
    try:
        baseline = _load_variant_result_from_dir(
            Path(request.baseline_replay_dir),
            base_variant_id="baseline",
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return False
    baseline, artifact_failures = _validate_v3_member_variant_artifact(
        Path(request.baseline_replay_dir),
        result=baseline,
        requested_repetitions=request.baseline_repetitions,
        case_id=request.task_id,
        variant_role="baseline",
        expected_variant_id="baseline",
    )
    if artifact_failures:
        return False
    return _baseline_replay_is_reusable(
        baseline,
        requested_repetitions=request.baseline_repetitions,
    )


def baseline_control_fingerprint(request: CandidateReplayRequest) -> str:
    """Identify an immutable control independently of the candidate package.

    ``adaptation_fingerprint`` includes compilation/artifact identity and can
    vary between candidates even when the executable support surface is
    semantically identical. ``support_fingerprint`` already commits to that
    semantic surface. The remaining fields freeze the baseline skill, panel,
    workspace, member input, repetitions, and execution envelope.
    """

    return stable_control_fingerprint(
        {
            "schema_version": "aworld.self_evolve.baseline_control.v1",
            "target": {
                "target_type": request.target.target_type,
                "target_id": request.target.target_id,
            },
            "task_id": request.task_id,
            "task_input_fingerprint": request.task_input_fingerprint,
            "baseline_skill_fingerprint": request.baseline_skill_fingerprint,
            "dataset_fingerprint": request.dataset_fingerprint,
            "workspace_seed_fingerprint": request.workspace_seed_fingerprint,
            "support_fingerprint": request.support_fingerprint,
            "timeout_envelope_fingerprint": (
                request.timeout_envelope_fingerprint
            ),
            "baseline_repetitions": request.baseline_repetitions,
            "repetition_semantics": request.repetition_semantics,
        }
    )


def _successful_repetition_count(result: ReplayVariantResult) -> int:
    count = result.metrics.get("successful_repetition_count")
    if isinstance(count, (int, float)):
        return int(count)
    if result.repetition_results:
        return sum(1 for repetition in result.repetition_results if repetition.succeeded)
    return 1 if result.succeeded else 0


def _baseline_replay_is_reusable(
    result: ReplayVariantResult,
    *,
    requested_repetitions: int,
) -> bool:
    """Reuse complete baselines, including attributable task-level failures."""

    if requested_repetitions <= 0 or not result.executed:
        return False
    repetition_count = result.metrics.get("repetition_count")
    if isinstance(repetition_count, bool) or not isinstance(
        repetition_count, (int, float)
    ):
        repetition_count = (
            len(result.repetition_results) if result.repetition_results else 1
        )
    if int(repetition_count) < requested_repetitions:
        return False
    if result.succeeded:
        return _successful_repetition_count(result) >= requested_repetitions
    return bool(
        isinstance(result.failure, ReplayFailureEvent)
        and result.failure.owner is FailureOwner.TASK
    )


def _short_runtime_root(prefix: str) -> Path:
    """Create an isolated runtime root short enough for Unix-domain sockets."""

    preferred_parent = Path("/tmp")
    if preferred_parent.is_dir():
        try:
            return Path(
                tempfile.mkdtemp(prefix=prefix, dir=str(preferred_parent))
            )
        except OSError:
            pass
    return Path(tempfile.mkdtemp(prefix=prefix))


def _with_loopback_proxy_bypass(
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Keep local replay services local even when the host uses a proxy."""

    normalized = {str(name): str(value) for name, value in environment.items()}
    loopback_hosts = ("127.0.0.1", "localhost", "::1")
    for name in ("NO_PROXY", "no_proxy"):
        entries = [
            entry.strip()
            for entry in normalized.get(name, "").split(",")
            if entry.strip()
        ]
        for host in loopback_hosts:
            if host not in entries:
                entries.append(host)
        normalized[name] = ",".join(entries)
    return normalized


def _receive_task_response_capability(
    descriptor: int,
    *,
    destination: Path,
    attestation_key: bytes,
    completed: threading.Event,
    max_bytes: int | None = None,
) -> None:
    """Receive one CLI-owned response and attest it with a parent-held key."""

    try:
        if max_bytes is None:
            max_bytes = _MAX_SELF_EVOLVE_TASK_RESPONSE_BYTES
        payload_bytes = bytearray()
        while len(payload_bytes) <= max_bytes:
            chunk = os.read(
                descriptor,
                min(
                    64 * 1024,
                    max_bytes + 1 - len(payload_bytes),
                ),
            )
            if not chunk:
                break
            payload_bytes.extend(chunk)
        if not payload_bytes or len(payload_bytes) > max_bytes:
            return
        payload = json.loads(bytes(payload_bytes).decode("utf-8"))
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != _SELF_EVOLVE_TASK_RESPONSE_SCHEMA
            or payload.get("trajectory_capture_mode") != "task_response"
            or not isinstance(payload.get("trajectory"), list)
            or not payload["trajectory"]
            or "framework_attestation" in payload
        ):
            return
        attested = dict(payload)
        attested["framework_attestation"] = {
            "schema_version": (
                "aworld.self_evolve.task_response_attestation.v2"
            ),
            "signature": _task_response_signature(payload, attestation_key),
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps(attested, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return
    finally:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        completed.set()


def _run_replay_cli(
    command: Sequence[str],
    *,
    cwd: str,
    text: bool,
    capture_output: bool,
    timeout: float,
    start_new_session: bool,
    env: Mapping[str, str],
    artifact_dir: Path,
    execution_started_at: float,
    replay_environment: Mapping[str, str],
    cancellation_event: threading.Event | None = None,
    evidence_manifest: Path | None = None,
    task_response_path: Path | None = None,
    task_response_capability_fd: int | None = None,
    task_response_capability_reader_fd: int | None = None,
    task_response_attestation_key: bytes | None = None,
    evidence_finalization_timeout_seconds: float | None = None,
    task_response_max_bytes: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a replay CLI process while supervising terminal task diagnostics."""

    if not capture_output:
        raise ValueError("replay CLI supervision requires captured output")
    if evidence_finalization_timeout_seconds is None:
        evidence_finalization_timeout_seconds = (
            _EVIDENCE_FINALIZATION_GRACE_SECONDS
        )
    if (
        isinstance(evidence_finalization_timeout_seconds, bool)
        or not isinstance(evidence_finalization_timeout_seconds, (int, float))
        or not math.isfinite(float(evidence_finalization_timeout_seconds))
        or float(evidence_finalization_timeout_seconds) <= 0
    ):
        raise ValueError("evidence finalization timeout must be positive")
    if task_response_max_bytes is None:
        task_response_max_bytes = _MAX_SELF_EVOLVE_TASK_RESPONSE_BYTES
    if isinstance(task_response_max_bytes, bool) or task_response_max_bytes <= 0:
        raise ValueError("task response byte limit must be positive")
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "text": text,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "start_new_session": start_new_session,
        "env": dict(env),
    }
    if task_response_capability_fd is not None:
        if (
            os.name != "posix"
            or task_response_capability_reader_fd is None
            or task_response_attestation_key is None
            or task_response_path is None
        ):
            raise ValueError("task-response capability fd requires POSIX")
        popen_kwargs["pass_fds"] = (task_response_capability_fd,)
    try:
        process = subprocess.Popen(list(command), **popen_kwargs)
    except Exception:
        for descriptor in (
            task_response_capability_fd,
            task_response_capability_reader_fd,
        ):
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
        raise
    task_response_received = threading.Event()
    if task_response_capability_fd is not None:
        os.close(task_response_capability_fd)
        receiver = threading.Thread(
            target=_receive_task_response_capability,
            args=(task_response_capability_reader_fd,),
            kwargs={
                "destination": task_response_path,
                "attestation_key": task_response_attestation_key,
                "completed": task_response_received,
                "max_bytes": task_response_max_bytes,
            },
            daemon=True,
            name="aworld-replay-task-response-attestor",
        )
        receiver.start()
    deadline = time.monotonic() + max(float(timeout), 0.0)
    evidence_ready_at: float | None = None
    manifest_signature: tuple[int, int] | None = None
    manifest_valid = False
    while True:
        if cancellation_event is not None and cancellation_event.is_set():
            stdout, stderr = _stop_replay_cli_process(
                process,
                start_new_session=start_new_session,
            )
            raise subprocess.TimeoutExpired(
                cmd=list(command),
                timeout=timeout,
                output=stdout,
                stderr=stderr,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stdout, stderr = _stop_replay_cli_process(
                process,
                start_new_session=start_new_session,
            )
            raise subprocess.TimeoutExpired(
                cmd=list(command),
                timeout=timeout,
                output=stdout,
                stderr=stderr,
            )
        try:
            stdout, stderr = process.communicate(timeout=min(0.5, remaining))
            if task_response_capability_fd is not None:
                task_response_received.wait(timeout=2.0)
            return subprocess.CompletedProcess(
                list(command),
                process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        except subprocess.TimeoutExpired as exc:
            try:
                if evidence_manifest is None:
                    raise FileNotFoundError
                manifest_stat = evidence_manifest.stat()
                current_manifest_signature = (
                    int(manifest_stat.st_mtime_ns),
                    int(manifest_stat.st_size),
                )
            except OSError:
                current_manifest_signature = None
            if (
                current_manifest_signature is not None
                and current_manifest_signature != manifest_signature
            ):
                manifest_signature = current_manifest_signature
                manifest_metrics = _evidence_manifest_metrics(
                    artifact_dir=evidence_manifest.parent,
                    evidence_manifest=evidence_manifest,
                    workspace_root=Path(cwd),
                )
                manifest_valid = _has_valid_artifact_backed_timeout_evidence(
                    manifest_metrics
                )
                if manifest_valid and evidence_ready_at is None:
                    evidence_ready_at = time.monotonic()
            if manifest_valid:
                task_response_payload = (
                    _load_self_evolve_task_response(
                        task_response_path,
                        attestation_key=task_response_attestation_key,
                        max_bytes=task_response_max_bytes,
                    )
                    if task_response_path is not None
                    else None
                )
                if task_response_payload is not None:
                    stdout, stderr = _stop_replay_cli_process(
                        process,
                        start_new_session=start_new_session,
                    )
                    framed_payload = json.dumps(
                        task_response_payload,
                        ensure_ascii=False,
                    )
                    completed = subprocess.CompletedProcess(
                        list(command),
                        0,
                        stdout=(stdout.rstrip("\n") + "\n" + framed_payload + "\n"),
                        stderr=stderr,
                    )
                    completed.evidence_ready_early_stop = True
                    return completed
                if (
                    evidence_ready_at is not None
                    and time.monotonic() - evidence_ready_at
                    >= evidence_finalization_timeout_seconds
                ):
                    stdout, stderr = _stop_replay_cli_process(
                        process,
                        start_new_session=start_new_session,
                    )
                    failure = subprocess.TimeoutExpired(
                        cmd=list(command),
                        timeout=timeout,
                        output=stdout,
                        stderr=stderr,
                    )
                    failure.evidence_finalization_deadline = True
                    raise failure
            artifact_diagnostics = _terminal_replay_artifact_diagnostics(
                artifact_dir=artifact_dir,
                since=execution_started_at,
            )
            partial_details: dict[str, object] = {}
            partial_stdout = _text_output(exc.output)
            partial_stderr = _text_output(exc.stderr)
            if partial_stdout.strip():
                partial_details["stdout_tail"] = sanitize_text(
                    partial_stdout[-4_000:],
                    max_chars=2_000,
                )
            if partial_stderr.strip():
                partial_details["stderr_tail"] = sanitize_text(
                    partial_stderr[-2_000:],
                    max_chars=1_000,
                )
            partial_diagnostics = (
                {"diagnostics": partial_details}
                if partial_details
                else {}
            )
            artifact_failure = _diagnostics_indicate_replay_dependency_failure(
                artifact_diagnostics,
                environment=replay_environment,
                live=True,
            )
            partial_failure = _partial_process_diagnostics_indicate_replay_failure(
                partial_diagnostics,
                environment=replay_environment,
            )
            if (
                not artifact_failure
                and not partial_failure
            ):
                continue
            stdout, stderr = _stop_replay_cli_process(
                process,
                start_new_session=start_new_session,
            )
            failure = subprocess.TimeoutExpired(
                cmd=list(command),
                timeout=timeout,
                output=stdout,
                stderr=stderr,
            )
            failure.terminal_diagnostic = True
            raise failure


_SELF_EVOLVE_TASK_RESPONSE_SCHEMA = "aworld.self_evolve.task_response.v1"
_MAX_SELF_EVOLVE_TASK_RESPONSE_BYTES = 8_000_000
_EVIDENCE_FINALIZATION_GRACE_SECONDS = 45.0
_DEFAULT_TRUSTED_EVIDENCE_SOURCE_BYTE_LIMIT = 64_000_000
_DEFAULT_EVIDENCE_SCRATCH_FILE_LIMIT = 64
_DEFAULT_EVIDENCE_SCRATCH_BYTE_LIMIT = 256_000_000


def _load_self_evolve_task_response(
    path: Path,
    *,
    attestation_key: bytes | None = None,
    max_bytes: int = _MAX_SELF_EVOLVE_TASK_RESPONSE_BYTES,
) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or path.stat().st_size > max_bytes:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != _SELF_EVOLVE_TASK_RESPONSE_SCHEMA
        or payload.get("trajectory_capture_mode") != "task_response"
        or not isinstance(payload.get("trajectory"), list)
        or not payload["trajectory"]
    ):
        return None
    if attestation_key is not None and not _task_response_is_attested(
        payload, attestation_key
    ):
        return None
    normalized = dict(payload)
    normalized["trajectory"] = [
        item for item in payload["trajectory"] if isinstance(item, Mapping)
    ]
    return normalized


def _stop_replay_cli_process(
    process: subprocess.Popen[str],
    *,
    start_new_session: bool,
) -> tuple[str, str]:
    if process.poll() is None:
        try:
            if start_new_session and os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except (OSError, ProcessLookupError):
            pass
    try:
        return process.communicate(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            if start_new_session and os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass
        return process.communicate()


def _terminal_replay_artifact_diagnostics(
    *,
    artifact_dir: Path,
    since: float,
) -> dict[str, object]:
    if not artifact_dir.is_dir():
        return {}
    task_artifacts: list[dict[str, str]] = []
    scan_roots = (artifact_dir, artifact_dir / "workspace")
    for scan_root in scan_roots:
        try:
            paths = sorted(scan_root.iterdir(), key=lambda path: path.name)
        except OSError:
            continue
        for path in paths[:256]:
            lowered = path.name.lower()
            if (
                not path.is_file()
                or path.suffix.lower() not in _TASK_DIAGNOSTIC_SUFFIXES
            ):
                continue
            if not any(
                marker in lowered
                for marker in _TASK_DIAGNOSTIC_NAME_MARKERS
            ):
                continue
            try:
                stat = path.stat()
                if (
                    stat.st_mtime < since - 2.0
                    or stat.st_size <= 0
                    or stat.st_size > 1_000_000
                ):
                    continue
                tail = sanitize_text(
                    path.read_bytes()[-4_000:].decode(
                        "utf-8",
                        errors="replace",
                    ),
                    max_chars=1_600,
                )
            except OSError:
                continue
            if tail:
                relative_path = path.relative_to(artifact_dir).as_posix()
                task_artifacts.append(
                    {"path": f"artifact/{relative_path}", "tail": tail}
                )
            if len(task_artifacts) >= 4:
                break
        if len(task_artifacts) >= 4:
            break
    if not task_artifacts:
        return {}
    return {"diagnostics": {"task_artifacts": task_artifacts}}


def _timeout_termination_diagnostics(
    request: ReplayExecutionRequest,
    evidence_metrics: Mapping[str, Any],
    *,
    default_tool_call_limit: int,
) -> dict[str, Any]:
    """Describe the exhausted execution envelope without inferring blame."""

    raw_used = evidence_metrics.get(
        "evidence_runtime_policy_tool_call_attempt_count"
    )
    tool_calls_used = (
        int(raw_used)
        if isinstance(raw_used, (int, float)) and not isinstance(raw_used, bool)
        else 0
    )
    max_tool_calls = request.max_tool_calls or default_tool_call_limit
    evidence_phase = str(
        evidence_metrics.get("evidence_runtime_policy_phase") or "collecting"
    )
    if tool_calls_used >= max_tool_calls:
        budget_axis = "tool_calls"
    else:
        budget_axis = "wall_time"
    return {
        "termination_kind": "budget_exhausted",
        "termination_budget_axis": budget_axis,
        "timeout_seconds": request.timeout_seconds,
        "max_steps": request.max_steps,
        "max_tool_calls": max_tool_calls,
        "tool_calls_used": tool_calls_used,
        "terminal_synthesis_attempted": evidence_phase == "finalizing",
        "evidence_phase": evidence_phase,
    }


def _runtime_trust_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _runtime_endpoint_bindings(
    environment: Mapping[str, str],
) -> tuple[DynamicEndpointBinding, ...]:
    bindings: list[DynamicEndpointBinding] = []
    for name, endpoint in sorted(environment.items()):
        if not name.startswith("AWORLD_REPLAY_ENDPOINT_"):
            continue
        suffix = name.removeprefix("AWORLD_REPLAY_ENDPOINT_").casefold()
        binding_id = "runtime." + re.sub(r"[^a-z0-9_.-]+", ".", suffix)
        bindings.append(
            DynamicEndpointBinding(
                binding_id=binding_id,
                service_identity="replay." + suffix.replace("_", "."),
                endpoint=str(endpoint),
                path_scope="prefix",
            )
        )
    return tuple(bindings)


def _framework_resolved_endpoint_bindings(
    profile: EvidencePolicyProfileV2 | None,
    *,
    environment: Mapping[str, str],
    service_endpoints: Mapping[str, str],
) -> Mapping[str, str]:
    """Freeze the endpoints resolved by framework-owned replay setup.

    Environment names are a transport detail and are not a stable logical
    identity for skill-owned replay services.  Preserve adapter-provided
    bindings from the environment, then let the service supervisor's explicit
    ``service_id -> endpoint`` result override the corresponding logical
    binding.  The evidence-policy preflight still performs the authoritative
    exact endpoint comparison.
    """

    if profile is None:
        return dict(
            sorted(
                (str(binding_id), str(endpoint))
                for binding_id, endpoint in service_endpoints.items()
            )
        )
    resolved = {
        binding.binding_id: str(environment[binding.environment_name])
        for binding in profile.endpoint_bindings
        if binding.environment_name in environment
    }
    known_binding_ids = {
        binding.binding_id for binding in profile.endpoint_bindings
    }
    resolved.update(
        {
            str(binding_id): str(endpoint)
            for binding_id, endpoint in service_endpoints.items()
            if str(binding_id) in known_binding_ids
        }
    )
    return dict(sorted(resolved.items()))


def _runtime_resolved_endpoint_bindings(
    request: ReplayExecutionRequest,
    profile: EvidencePolicyProfileV2,
) -> Mapping[str, str]:
    """Resolve only framework-attested bindings for evidence preflight."""

    framework_resolved = {
        str(binding_id): str(endpoint)
        for binding_id, endpoint in request.framework_endpoint_bindings.items()
    }
    resolved: dict[str, str] = {}
    for binding in profile.endpoint_bindings:
        endpoint = framework_resolved.get(binding.binding_id)
        runtime_alias = (
            binding.binding_id.removeprefix("runtime.")
            if binding.binding_id.startswith("runtime.")
            else None
        )
        if endpoint is None and runtime_alias is not None:
            endpoint = framework_resolved.get(runtime_alias)
        if endpoint is None:
            environment_names = [binding.environment_name]
            if runtime_alias is not None:
                environment_names.append(
                    "AWORLD_REPLAY_ENDPOINT_"
                    + re.sub(r"[^A-Z0-9]+", "_", runtime_alias.upper()).strip("_")
                )
            endpoint = next(
                (
                    str(request.environment[name])
                    for name in environment_names
                    if name in request.environment
                ),
                None,
            )
        if endpoint is not None:
            resolved[binding.binding_id] = endpoint
    return dict(sorted(resolved.items()))


def compile_replay_evidence_policy_profile_v2(
    *,
    endpoint_bindings: Sequence[DynamicEndpointBinding] = (),
    contract_identities: Sequence[EvidenceContractIdentity] = (),
    artifact_file_limit: int | None = None,
    artifact_byte_limit: int | None = None,
    task_response_byte_limit: int = _MAX_SELF_EVOLVE_TASK_RESPONSE_BYTES,
    scratch_file_limit: int = _DEFAULT_EVIDENCE_SCRATCH_FILE_LIMIT,
    scratch_byte_limit: int = _DEFAULT_EVIDENCE_SCRATCH_BYTE_LIMIT,
) -> EvidencePolicyProfileV2:
    """Compile the canonical evidence contract shared by plan and runtime."""

    artifact_file_limit = (
        AWorldCliReplayExecutor._DEFAULT_ARTIFACT_FILE_LIMIT
        if artifact_file_limit is None
        else artifact_file_limit
    )
    artifact_byte_limit = (
        _DEFAULT_TRUSTED_EVIDENCE_SOURCE_BYTE_LIMIT
        if artifact_byte_limit is None
        else artifact_byte_limit
    )

    return compile_evidence_policy_profile_v2(
        artifact_policies=(
            ArtifactPolicy(
                artifact_type=_REPLAY_TRUSTED_EVIDENCE_TYPE,
                registered_producers=(_REPLAY_TRUSTED_EVIDENCE_PRODUCER,),
                max_files=artifact_file_limit,
                max_items=_MAX_EVIDENCE_MANIFEST_ENTRIES,
                max_bytes=artifact_byte_limit,
                projection="summary",
                projection_byte_limit=64_000,
                required=True,
            ),
            ArtifactPolicy(
                artifact_type=_REPLAY_TRUSTED_RESPONSE_TYPE,
                registered_producers=(_REPLAY_TRUSTED_RESPONSE_PRODUCER,),
                max_files=1,
                max_items=1,
                max_bytes=task_response_byte_limit,
                projection="summary",
                projection_byte_limit=64_000,
                required=True,
            ),
        ),
        endpoint_bindings=endpoint_bindings,
        contract_identities=contract_identities,
        required_task_response_fields=(
            "schema_version",
            "task_response_digest",
            "trajectory_capture_mode",
        ),
        allowed_control_actions=("browser:close",),
        scratch_max_files=scratch_file_limit,
        scratch_max_bytes=scratch_byte_limit,
    )


def compile_authoritative_replay_evidence_policy_profile_v2(
    *,
    experiment: ControlledExperimentSpec,
    target: SelfEvolveTargetRef,
    replay_adaptation: ReplayAdaptationBundle,
    member_timeout_seconds: float,
    target_adapter_identity: Mapping[str, object] | None = None,
) -> EvidencePolicyProfileV2:
    """Compile all authority-bearing replay inputs into one frozen profile."""

    if not isinstance(experiment, ControlledExperimentSpec):
        raise TypeError("authoritative evidence policy requires an experiment")
    if not isinstance(target, SelfEvolveTargetRef):
        raise TypeError("authoritative evidence policy requires a target reference")
    if not isinstance(replay_adaptation, ReplayAdaptationBundle):
        raise TypeError("authoritative evidence policy requires replay adaptation")
    if (
        isinstance(member_timeout_seconds, bool)
        or not isinstance(member_timeout_seconds, (int, float))
        or not math.isfinite(float(member_timeout_seconds))
        or member_timeout_seconds <= 0
    ):
        raise ValueError("member timeout must be a finite positive number")

    endpoints = _authoritative_replay_endpoint_bindings(
        experiment=experiment,
        replay_adaptation=replay_adaptation,
    )
    evaluator_fingerprint = experiment.frozen_identities.evaluator
    if evaluator_fingerprint is None:
        raise ValueError("authoritative evidence policy requires evaluator identity")
    identities = (
        EvidenceContractIdentity(
            "task_observation",
            stable_control_fingerprint(
                {
                    "outcomes": experiment.outcomes.to_dict(),
                    "sampling": experiment.sampling.to_dict(),
                }
            ),
        ),
        EvidenceContractIdentity(
            "target_adapter",
            stable_control_fingerprint(
                {
                    "target_type": target.target_type,
                    "target_id": target.target_id,
                    "adapter": dict(
                        target_adapter_identity
                        or {
                            "contract_version": (
                                "aworld.self_evolve.target_adapter.v1"
                            )
                        }
                    ),
                }
            ),
        ),
        EvidenceContractIdentity(
            "replay_capability",
            stable_control_fingerprint(
                {
                    "adaptation_fingerprint": replay_adaptation.adaptation_fingerprint,
                    "capability_fingerprint": (
                        replay_adaptation.replay_capability.fingerprint
                        if replay_adaptation.replay_capability is not None
                        else None
                    ),
                    "binding_fingerprints": sorted(
                        {
                            binding.binding_fingerprint
                            for case in replay_adaptation.cases
                            for binding in case.bindings
                            if binding.binding_fingerprint is not None
                        }
                    ),
                }
            ),
        ),
        EvidenceContractIdentity("evaluator", evaluator_fingerprint),
        EvidenceContractIdentity(
            "resource_policy",
            stable_control_fingerprint(
                {
                    "member_timeout_seconds": float(member_timeout_seconds),
                    "artifact_file_limit": (
                        AWorldCliReplayExecutor._DEFAULT_ARTIFACT_FILE_LIMIT
                    ),
                    "artifact_byte_limit": (
                        _DEFAULT_TRUSTED_EVIDENCE_SOURCE_BYTE_LIMIT
                    ),
                    "task_response_byte_limit": (
                        _MAX_SELF_EVOLVE_TASK_RESPONSE_BYTES
                    ),
                    "scratch_file_limit": (
                        _DEFAULT_EVIDENCE_SCRATCH_FILE_LIMIT
                    ),
                    "scratch_byte_limit": (
                        _DEFAULT_EVIDENCE_SCRATCH_BYTE_LIMIT
                    ),
                }
            ),
        ),
    )
    return compile_replay_evidence_policy_profile_v2(
        endpoint_bindings=endpoints,
        contract_identities=identities,
    )


def _authoritative_replay_endpoint_bindings(
    *,
    experiment: ControlledExperimentSpec,
    replay_adaptation: ReplayAdaptationBundle,
) -> tuple[DynamicEndpointBinding, ...]:
    bindings: dict[str, DynamicEndpointBinding] = {}
    for case in replay_adaptation.cases:
        for binding in _runtime_endpoint_bindings(
            {
                key: value
                for adapter_binding in case.bindings
                for key, value in adapter_binding.environment.items()
                if key.startswith("AWORLD_REPLAY_ENDPOINT_")
            }
        ):
            previous = bindings.get(binding.binding_id)
            if previous is not None and previous != binding:
                raise ValueError("replay endpoint binding drifted across cases")
            bindings[binding.binding_id] = binding

    capability = replay_adaptation.replay_capability
    if capability is not None:
        used_ports = {
            urlsplit(item.endpoint).port for item in bindings.values()
        }
        for service in sorted(capability.services, key=lambda item: item.service_id):
            seed = stable_control_fingerprint(
                {
                    "experiment_id": experiment.experiment_id,
                    "capability_fingerprint": capability.fingerprint,
                    "service_id": service.service_id,
                }
            )
            port = 20_000 + int(seed.removeprefix("sha256:")[:8], 16) % 40_000
            while port in used_ports:
                port = 20_000 + ((port - 20_000 + 1) % 40_000)
            used_ports.add(port)
            binding = DynamicEndpointBinding(
                binding_id=service.service_id,
                service_identity=(
                    "replay."
                    + re.sub(
                        r"[^a-z0-9_.-]+",
                        ".",
                        f"{capability.capability_id}.{service.service_id}".casefold(),
                    ).strip(".")
                ),
                endpoint=(
                    f"http://127.0.0.1:{port}"
                    + (
                        service.task_entry_path
                        if service.task_entry_path not in {None, "/"}
                        else ""
                    )
                ),
                path_scope="prefix",
            )
            previous = bindings.get(binding.binding_id)
            if previous is not None and previous != binding:
                raise ValueError("capability endpoint binding identity conflicts")
            bindings[binding.binding_id] = binding
    return tuple(bindings[key] for key in sorted(bindings))


def _prepare_replay_evidence_trust(
    request: ReplayExecutionRequest,
    *,
    artifact_dir: Path,
    evidence_dir: Path,
) -> _ReplayEvidenceTrustContext:
    if os.name != "posix":
        raise ValueError("required replay evidence trust needs POSIX pass_fds")
    injected = sorted(_REPLAY_TRUST_RESERVED_ENV.intersection(request.environment))
    if injected:
        raise ValueError(
            "reserved replay evidence trust environment was supplied: "
            + ",".join(injected)
        )
    trusted_root = artifact_dir / "framework-trusted"
    trusted_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if trusted_root.is_symlink() or any(trusted_root.iterdir()):
        raise ValueError("framework evidence trust namespace is not empty")
    compiled_profile = compile_replay_evidence_policy_profile_v2(
        endpoint_bindings=_runtime_endpoint_bindings(request.environment),
    )
    if request.measurement_evidence_policy_profile is not None:
        profile = EvidencePolicyProfileV2.from_dict(
            request.measurement_evidence_policy_profile.to_dict()
        )
        if profile.fingerprint != request.measurement_work_unit.evidence_policy_fingerprint:  # type: ignore[union-attr]
            raise ValueError("runtime evidence profile differs from measurement unit")
        work_unit_fingerprint = stable_control_fingerprint(
            request.measurement_work_unit.identity_payload  # type: ignore[union-attr]
        )
        isolation_identity = (
            request.isolation_grant_fingerprint
            or request.measurement_work_unit.isolation_decision_fingerprint  # type: ignore[union-attr]
        )
        resource_identity = request.lane_materialization_fingerprint
        assert resource_identity is not None
    else:
        profile = compiled_profile
        work_unit_fingerprint = _runtime_trust_fingerprint(
            {
            "schema_version": "aworld.replay.runtime_work_unit.v2",
            "profile_fingerprint": profile.fingerprint,
            "variant_id": request.variant_id,
            "task_id": request.task_id,
            "candidate_id": request.candidate_id,
            "dataset_fingerprint": request.dataset_fingerprint,
            "adaptation_fingerprint": request.adaptation_fingerprint,
            "support_fingerprint": request.support_fingerprint,
            "timeout_envelope_fingerprint": (
                request.timeout_envelope_fingerprint
            ),
            "task_input_fingerprint": request.task_input_fingerprint,
            "baseline_skill_fingerprint": request.baseline_skill_fingerprint,
            "capability_package_fingerprint": (
                request.capability_package_fingerprint
            ),
            "frozen_capability_fingerprint": (
                request.frozen_capability_fingerprint
            ),
            }
        )
        isolation_identity = "isolation." + uuid.uuid4().hex
        resource_identity = "resource." + work_unit_fingerprint[-32:]
    writer = issue_framework_evidence_writer_attestation_v2(
        profile,
        writer_identity="framework.replay-supervisor",
        isolation_identity=isolation_identity,
        resource_identity=resource_identity,
    )
    producer_capabilities = (
        issue_producer_registration_capability_v2(
            profile,
            writer,
            producer_id=_REPLAY_TRUSTED_EVIDENCE_PRODUCER,
            artifact_roots={_REPLAY_TRUSTED_EVIDENCE_TYPE: "evidence"},
        ),
        issue_producer_registration_capability_v2(
            profile,
            writer,
            producer_id=_REPLAY_TRUSTED_RESPONSE_PRODUCER,
            artifact_roots={
                _REPLAY_TRUSTED_RESPONSE_TYPE: "framework-trusted"
            },
        ),
    )
    resolved = _runtime_resolved_endpoint_bindings(request, profile)
    preflight = preflight_evidence_policy_v2(
        profile,
        artifact_root=artifact_dir,
        available_producers=(
            _REPLAY_TRUSTED_EVIDENCE_PRODUCER,
            _REPLAY_TRUSTED_RESPONSE_PRODUCER,
        ),
        resolved_endpoint_bindings=resolved,
        producer_capabilities=producer_capabilities,
    )
    if not preflight.passed:
        issues = ",".join(
            f"{item.code}:{item.field}" for item in preflight.issues
        )
        raise ValueError("replay evidence v2 preflight failed: " + issues)
    return _ReplayEvidenceTrustContext(
        profile=profile,
        writer=writer,
        producer_capabilities=producer_capabilities,
        work_unit_fingerprint=work_unit_fingerprint,
        signing_key=secrets.token_bytes(32),
        trusted_root=trusted_root,
        measurement_plan_fingerprint=request.measurement_plan_fingerprint,
        measurement_work_unit_id=(
            request.measurement_work_unit.work_unit_id
            if request.measurement_work_unit is not None
            else None
        ),
        isolation_grant_fingerprint=request.isolation_grant_fingerprint,
        lane_materialization_fingerprint=request.lane_materialization_fingerprint,
    )


def _task_response_attestation_payload(
    payload: Mapping[str, Any],
) -> bytes:
    unsigned = {
        str(key): value
        for key, value in payload.items()
        if key != "framework_attestation"
    }
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _task_response_signature(payload: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(
        key,
        _task_response_attestation_payload(payload),
        hashlib.sha256,
    ).hexdigest()


def _task_response_is_attested(
    payload: Mapping[str, Any], key: bytes
) -> bool:
    attestation = payload.get("framework_attestation")
    return bool(
        isinstance(attestation, Mapping)
        and attestation.get("schema_version")
        == "aworld.self_evolve.task_response_attestation.v2"
        and isinstance(attestation.get("signature"), str)
        and hmac.compare_digest(
            str(attestation["signature"]),
            _task_response_signature(payload, key),
        )
    )


def _digest_regular_file(path: Path, *, max_bytes: int) -> tuple[int, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("trusted evidence source is not a regular file")
    size = path.stat().st_size
    if size < 0 or size > max_bytes:
        raise ValueError("trusted evidence source exceeds its policy budget")
    digest = hashlib.sha256()
    consumed = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(min(64 * 1024, max_bytes + 1 - consumed))
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > max_bytes:
                raise ValueError("trusted evidence source exceeds its policy budget")
            digest.update(chunk)
    if consumed != size:
        raise ValueError("trusted evidence source changed while hashing")
    return consumed, "sha256:" + digest.hexdigest()


def _write_runtime_projection(
    trusted_root: Path,
    *,
    name: str,
    source_digest: str,
    source_bytes: int,
    source_path: Path | None = None,
    projection_byte_limit: int = 64_000,
) -> tuple[str, str]:
    path = trusted_root / name
    payload = {
        "schema_version": "aworld.evidence_projection.v2",
        "source_digest": source_digest,
        "source_bytes": source_bytes,
    }
    if source_path is not None:
        preview_limit = min(max(projection_byte_limit // 4, 1), 16_000)
        with source_path.open("rb") as stream:
            preview = stream.read(preview_limit + 1)
        decoded = preview[:preview_limit].decode(
            "utf-8", errors="replace"
        ).strip()
        replacement_ratio = (
            decoded.count("\ufffd") / max(len(decoded), 1)
            if decoded
            else 1.0
        )
        if decoded and replacement_ratio <= 0.02:
            payload["bounded_excerpt"] = decoded
            payload["truncated"] = source_bytes > preview_limit
        else:
            payload["projection_kind"] = "framework_binary_identity"
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > projection_byte_limit:
        payload.pop("bounded_excerpt", None)
        payload.pop("truncated", None)
        payload["projection_kind"] = "framework_bounded_identity"
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    path.write_bytes(encoded)
    return (
        path.relative_to(trusted_root.parent).as_posix(),
        "sha256:" + hashlib.sha256(encoded).hexdigest(),
    )


_FRAMEWORK_CANONICAL_EVIDENCE_MANIFEST = (
    "framework_canonical_evidence_manifest.jsonl"
)


class ReplayEvidenceProducerError(ValueError):
    """A completed replay variant did not populate its evidence namespace.

    This error is intentionally distinct from parent-owned attestation and
    persistence failures. The producer namespace remains untrusted and all
    containment checks still fail closed; the type only records causal
    ownership so a broken baseline can remain a negative control.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostics = dict(diagnostics or {})


def _profile_artifact_policy(
    profile: EvidencePolicyProfileV2,
    artifact_type: str,
) -> ArtifactPolicy:
    matches = tuple(
        item for item in profile.artifact_policies
        if item.artifact_type == artifact_type
    )
    if len(matches) != 1:
        raise ValueError(
            f"evidence profile must define exactly one {artifact_type} policy"
        )
    return matches[0]


def _profile_scratch_limits(
    profile: EvidencePolicyProfileV2,
) -> tuple[int, int]:
    """Derive the bounded producer namespace from the frozen source policy.

    Scratch is deliberately larger than the trusted handle budget.  Raw browser
    exports may need a deterministic projection before they become trusted
    evidence, so charging them directly to the evaluator budget makes the
    projection path unreachable.
    """

    return profile.scratch_max_files, profile.scratch_max_bytes


def _framework_projection_payload(
    path: Path,
    *,
    source_byte_limit: int,
    projection_byte_limit: int,
) -> dict[str, object]:
    """Build candidate-independent bounded semantics from the actual source."""

    byte_count, digest = _digest_regular_file(path, max_bytes=source_byte_limit)
    preview_limit = min(max(projection_byte_limit, 1), 64_000)
    with path.open("rb") as stream:
        preview = stream.read(preview_limit + 1)
    decoded = preview[:preview_limit].decode("utf-8", errors="replace").strip()
    replacement_ratio = (
        decoded.count("\ufffd") / max(len(decoded), 1)
        if decoded
        else 1.0
    )
    if decoded and replacement_ratio <= 0.02:
        return {
            "bounded_excerpt": decoded,
            "fields_used": ["framework_bounded_source_preview"],
            "truncated": byte_count > preview_limit,
        }
    return {
        "structured_summary": {
            "content_digest": digest,
            "byte_count": byte_count,
            "file_suffix": path.suffix.casefold()[:32],
            "projection_kind": "framework_binary_identity",
        },
        "fields_used": ["framework_binary_identity"],
    }


def _candidate_manifest_inventory_path(
    value: object,
    *,
    evidence_root: Path,
) -> Path | None:
    """Resolve an advisory path only when it names the frozen inventory root.

    Older skills commonly write ``evidence/foo`` even though their manifest is
    already inside the evidence directory.  Accept that single redundant root
    component, but never use a candidate path to discover a file.
    """

    raw = str(value or "").strip()
    if not raw:
        return None
    supplied = Path(raw)
    candidates = (
        [supplied]
        if supplied.is_absolute()
        else [evidence_root / supplied]
    )
    if (
        not supplied.is_absolute()
        and supplied.parts
        and supplied.parts[0] == evidence_root.name
        and len(supplied.parts) > 1
    ):
        candidates.append(evidence_root.joinpath(*supplied.parts[1:]))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(evidence_root)
        except (OSError, ValueError):
            continue
        if not candidate.is_file() or candidate.is_symlink():
            continue
        return resolved
    return None


def _framework_canonical_evidence_manifest(
    *,
    evidence_dir: Path,
    candidate_manifest: Path,
    profile: EvidencePolicyProfileV2,
    task_response_only_digest: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Build the parent-owned evidence truth from the producer namespace.

    The replay child runs in shadow mode and is not an evidence authority. Once
    it exits, the parent inventories only regular files inside the dedicated
    evidence namespace. A child manifest may annotate an already inventoried
    file, but it cannot add, remove, or invalidate inventory identities.
    """

    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise ReplayEvidenceProducerError(
            "canonical_evidence_namespace_invalid",
            "candidate evidence root is not a regular directory",
        )
    candidate_manifest_is_symlink = candidate_manifest.is_symlink()
    candidate_manifest_present = (
        candidate_manifest.exists() or candidate_manifest_is_symlink
    )
    policy = _profile_artifact_policy(profile, _REPLAY_TRUSTED_EVIDENCE_TYPE)
    scratch_file_limit, scratch_byte_limit = _profile_scratch_limits(profile)
    root = evidence_dir.resolve()
    inventory: list[tuple[Path, Path, int]] = []
    total_bytes = 0
    for current_root, directories, filenames in os.walk(
        evidence_dir,
        followlinks=False,
    ):
        current = Path(current_root)
        for directory in tuple(directories):
            if (current / directory).is_symlink():
                raise ReplayEvidenceProducerError(
                    "candidate_evidence_symlink_directory",
                    "candidate evidence contains a symlink directory",
                )
        directories[:] = sorted(directories)
        for filename in sorted(filenames):
            if filename in _REPLAY_POLICY_CONTROL_FILES:
                continue
            path = current / filename
            if path.is_symlink() or not path.is_file():
                raise ReplayEvidenceProducerError(
                    "candidate_evidence_non_regular_file",
                    "candidate evidence contains a non-regular file",
                )
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(root)
            except ValueError as exc:
                raise ReplayEvidenceProducerError(
                    "candidate_evidence_escaped_producer_root",
                    "candidate evidence escaped its producer root",
                ) from exc
            size = path.stat().st_size
            if size < 0:
                raise ReplayEvidenceProducerError(
                    "candidate_evidence_invalid_size",
                    "candidate evidence has an invalid size",
                )
            total_bytes += size
            if (
                len(inventory) >= scratch_file_limit
                or total_bytes > scratch_byte_limit
            ):
                raise ReplayEvidenceProducerError(
                    "candidate_evidence_scratch_budget_exceeded",
                    "candidate evidence exceeds its scratch budget",
                    diagnostics={
                        "framework_evidence_inventory_file_count": len(inventory),
                        "framework_evidence_inventory_bytes": total_bytes,
                        "framework_scratch_file_limit": scratch_file_limit,
                        "framework_scratch_byte_limit": scratch_byte_limit,
                    },
                )
            inventory.append((resolved, relative, size))
    task_response_only_evidence = False
    if not inventory and task_response_only_digest is not None:
        receipt = evidence_dir / "framework_task_response_only_evidence.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": (
                        "aworld.self_evolve.task_response_only_evidence.v1"
                    ),
                    "source": "framework.parent",
                    "external_tool_call_count": 0,
                    "task_response_content_digest": task_response_only_digest,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        resolved_receipt = receipt.resolve()
        receipt_size = receipt.stat().st_size
        inventory.append(
            (resolved_receipt, resolved_receipt.relative_to(root), receipt_size)
        )
        total_bytes += receipt_size
        task_response_only_evidence = True
    if not inventory:
        raise ReplayEvidenceProducerError(
            "canonical_evidence_inventory_empty",
            "framework evidence inventory is empty",
            diagnostics={
                "framework_evidence_inventory_file_count": 0,
                "framework_evidence_inventory_bytes": 0,
                "framework_scratch_file_limit": scratch_file_limit,
                "framework_scratch_byte_limit": scratch_byte_limit,
            },
        )

    inventory_paths = {resolved for resolved, _, _ in inventory}
    annotations: dict[Path, Mapping[str, Any]] = {}
    advisory_invalid_reasons: list[str] = []
    advisory_entry_count = 0
    if candidate_manifest_is_symlink:
        advisory_invalid_reasons.append(
            "manifest is a symlink and was ignored"
        )
    elif candidate_manifest.exists():
        try:
            manifest_size = candidate_manifest.stat().st_size
            if manifest_size > _MAX_EVIDENCE_MANIFEST_BYTES:
                advisory_invalid_reasons.append("manifest exceeds bounded byte limit")
            else:
                raw = candidate_manifest.read_bytes()
                text = raw.decode("utf-8", errors="replace")
                for line_number, entry, decode_error in _decode_evidence_manifest_stream(
                    text
                ):
                    if advisory_entry_count >= _MAX_EVIDENCE_MANIFEST_ENTRIES:
                        advisory_invalid_reasons.append(
                            "manifest exceeds bounded entry limit"
                        )
                        break
                    advisory_entry_count += 1
                    if decode_error is not None or not isinstance(entry, Mapping):
                        advisory_invalid_reasons.append(
                            f"line {line_number}: {decode_error or 'entry is not an object'}"
                        )
                        continue
                    if _manifest_evidence_type(entry) == "metadata":
                        advisory_invalid_reasons.append(
                            f"line {line_number}: metadata-only advisory is not "
                            "authoritative evidence"
                        )
                        continue
                    resolved = _candidate_manifest_inventory_path(
                        entry.get("artifact_path"),
                        evidence_root=root,
                    )
                    if resolved not in inventory_paths:
                        advisory_invalid_reasons.append(
                            f"line {line_number}: artifact is not in canonical inventory"
                        )
                        continue
                    annotations.setdefault(resolved, dict(entry))
        except OSError as exc:
            advisory_invalid_reasons.append(
                f"manifest is not readable: {exc.__class__.__name__}"
            )

    # Selection is framework-owned and deterministic. Candidate annotations
    # remain diagnostics only: they may not decide which source becomes trusted
    # evidence or smuggle semantic claims into the canonical manifest.
    selected_inventory: list[tuple[Path, Path, int]] = []
    selected_bytes = 0
    for item in inventory:
        size = item[2]
        if len(selected_inventory) >= policy.max_files:
            break
        if size > policy.max_bytes or selected_bytes + size > policy.max_bytes:
            continue
        selected_inventory.append(item)
        selected_bytes += size
    if not selected_inventory:
        raise ReplayEvidenceProducerError(
            "candidate_evidence_outside_trusted_profile",
            "no scratch artifact fits the trusted evidence profile",
            diagnostics={
                "framework_evidence_inventory_file_count": len(inventory),
                "framework_evidence_inventory_bytes": total_bytes,
                "framework_trusted_evidence_file_count": 0,
                "framework_trusted_evidence_bytes": 0,
            },
        )

    entries: list[dict[str, object]] = []
    for index, (resolved, relative, _size) in enumerate(
        selected_inventory, start=1
    ):
        entry: dict[str, object] = {
            "source_id": f"framework.inventory.{index}",
            "evidence_type": "file",
            "artifact_path": relative.as_posix(),
            "extraction_method": "framework_deterministic_projection",
        }
        entry.update(
            _framework_projection_payload(
                resolved,
                source_byte_limit=policy.max_bytes,
                projection_byte_limit=policy.projection_byte_limit,
            )
        )
        entries.append(entry)
    canonical_manifest = evidence_dir / _FRAMEWORK_CANONICAL_EVIDENCE_MANIFEST
    encoded = "".join(
        json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        for entry in entries
    )
    temporary = canonical_manifest.with_name(
        f".{canonical_manifest.name}.{secrets.token_hex(6)}.tmp"
    )
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, canonical_manifest)
    diagnostics: dict[str, Any] = {
        "candidate_evidence_manifest_present": candidate_manifest_present,
        "candidate_evidence_manifest_advisory": True,
        "candidate_evidence_manifest_entry_count": advisory_entry_count,
        "candidate_evidence_manifest_matched_artifact_count": len(annotations),
        "framework_evidence_inventory_file_count": len(inventory),
        "framework_evidence_inventory_bytes": total_bytes,
        "framework_trusted_evidence_file_count": len(selected_inventory),
        "framework_trusted_evidence_bytes": selected_bytes,
        "framework_scratch_file_limit": scratch_file_limit,
        "framework_scratch_byte_limit": scratch_byte_limit,
        "framework_evidence_manifest_path": str(canonical_manifest),
        "framework_task_response_only_evidence": task_response_only_evidence,
    }
    if advisory_invalid_reasons:
        diagnostics["candidate_evidence_manifest_diagnostic_count"] = len(
            advisory_invalid_reasons
        )
        diagnostics["candidate_evidence_manifest_diagnostics"] = (
            advisory_invalid_reasons[:16]
        )
    return canonical_manifest, diagnostics


def _finalize_replay_evidence_trust(
    context: _ReplayEvidenceTrustContext,
    *,
    artifact_dir: Path,
    evidence_dir: Path,
    task_response_path: Path,
) -> dict[str, Any]:
    evidence_policy = _profile_artifact_policy(
        context.profile, _REPLAY_TRUSTED_EVIDENCE_TYPE
    )
    response_policy = _profile_artifact_policy(
        context.profile, _REPLAY_TRUSTED_RESPONSE_TYPE
    )
    trusted_root = context.trusted_root
    if (
        trusted_root.is_symlink()
        or not trusted_root.is_dir()
        or any(trusted_root.iterdir())
    ):
        raise ValueError("framework evidence trust namespace was modified")
    task_response = _load_self_evolve_task_response(
        task_response_path,
        attestation_key=context.signing_key,
        max_bytes=response_policy.max_bytes,
    )
    if task_response is None:
        raise ValueError("framework task response attestation is missing or invalid")
    response_bytes = task_response_path.read_bytes()
    if len(response_bytes) > response_policy.max_bytes:
        raise ValueError("framework task response exceeds its policy budget")
    response_digest = "sha256:" + hashlib.sha256(response_bytes).hexdigest()
    response_copy = trusted_root / "task-response.json"
    response_copy.write_bytes(response_bytes)

    bundle_path = evidence_dir / "evidence_bundle.json"
    bundle = _load_json_object(bundle_path)
    entries = bundle.get("entries")
    if bundle.get("valid") is not True or not isinstance(entries, list) or not entries:
        raise ValueError("candidate evidence bundle is not valid")
    if len(entries) > evidence_policy.max_files:
        raise ValueError("candidate evidence exceeds the trusted handle budget")
    handles = []
    artifact_root = artifact_dir.resolve()
    evidence_root = evidence_dir.resolve()
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, Mapping):
            raise ValueError("candidate evidence entry is not an object")
        artifact_path_value = raw_entry.get("artifact_path")
        if isinstance(artifact_path_value, str) and artifact_path_value:
            source = Path(artifact_path_value)
            if not source.is_absolute():
                source = evidence_dir / source
            source = source.resolve()
            try:
                source.relative_to(evidence_root)
            except ValueError as exc:
                raise ValueError("candidate evidence escaped its producer root") from exc
        else:
            source = evidence_dir / (
                f"framework-evidence-{index + 1:04d}-"
                f"{secrets.token_hex(4)}.json"
            )
            source.write_text(
                json.dumps(
                    raw_entry,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        byte_count, digest = _digest_regular_file(
            source,
            max_bytes=evidence_policy.max_bytes,
        )
        relative_path = source.relative_to(artifact_root).as_posix()
        projection_path = None
        projection_digest = None
        if byte_count > evidence_policy.projection_byte_limit:
            projection_path, projection_digest = _write_runtime_projection(
                trusted_root,
                name=f"evidence-{index + 1:04d}.projection.json",
                source_digest=digest,
                source_bytes=byte_count,
                source_path=source,
                projection_byte_limit=evidence_policy.projection_byte_limit,
            )
        handles.append(
            make_evidence_handle_v2(
                handle_id=f"evidence.{index + 1:04d}",
                artifact_type=_REPLAY_TRUSTED_EVIDENCE_TYPE,
                producer_id=_REPLAY_TRUSTED_EVIDENCE_PRODUCER,
                relative_path=relative_path,
                content_digest=digest,
                byte_count=byte_count,
                item_count=1,
                projection_relative_path=projection_path,
                projection_digest=projection_digest,
            )
        )
    response_projection_path = None
    response_projection_digest = None
    if len(response_bytes) > response_policy.projection_byte_limit:
        response_projection_path, response_projection_digest = (
            _write_runtime_projection(
                trusted_root,
                name="task-response.projection.json",
                source_digest=response_digest,
                source_bytes=len(response_bytes),
                source_path=response_copy,
                projection_byte_limit=response_policy.projection_byte_limit,
            )
        )
    handles.append(
        make_evidence_handle_v2(
            handle_id="task.response",
            artifact_type=_REPLAY_TRUSTED_RESPONSE_TYPE,
            producer_id=_REPLAY_TRUSTED_RESPONSE_PRODUCER,
            relative_path=response_copy.relative_to(artifact_root).as_posix(),
            content_digest=response_digest,
            byte_count=len(response_bytes),
            item_count=1,
            projection_relative_path=response_projection_path,
            projection_digest=response_projection_digest,
        )
    )
    task_summary = {
        "schema_version": task_response.get("schema_version"),
        "trajectory_capture_mode": task_response.get("trajectory_capture_mode"),
        "task_response_digest": response_digest,
    }
    manifest = build_framework_evidence_manifest_v2(
        context.profile,
        handles,
        task_summary,
        artifact_root=artifact_dir,
        writer_attestation=context.writer,
        producer_capabilities=context.producer_capabilities,
        task_response_attestation=attest_task_response_v2(
            context.profile, context.writer, task_summary
        ),
    )
    manifest_fingerprint = _runtime_trust_fingerprint(manifest)
    envelope = {
        "schema_version": _REPLAY_TRUSTED_MANIFEST_SCHEMA,
        "evidence_policy_fingerprint": context.profile.fingerprint,
        "work_unit_fingerprint": context.work_unit_fingerprint,
        "manifest_fingerprint": manifest_fingerprint,
        "task_response_digest": response_digest,
    }
    envelope["signature"] = hmac.new(
        context.signing_key,
        json.dumps(
            envelope, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    trusted_manifest = {
        **manifest,
        "runtime_trust_envelope": envelope,
    }
    destination = trusted_root / (
        "evidence-manifest-" + secrets.token_hex(8) + ".v2.json"
    )
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(trusted_manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    persisted = _load_json_object(destination)
    persisted_envelope = persisted.get("runtime_trust_envelope")
    if not isinstance(persisted_envelope, Mapping):
        raise ValueError("trusted evidence manifest envelope is missing")
    supplied_signature = str(persisted_envelope.get("signature") or "")
    unsigned_envelope = {
        key: value
        for key, value in persisted_envelope.items()
        if key != "signature"
    }
    expected_signature = hmac.new(
        context.signing_key,
        json.dumps(
            unsigned_envelope, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    persisted_manifest = {
        key: value
        for key, value in persisted.items()
        if key != "runtime_trust_envelope"
    }
    if (
        not hmac.compare_digest(supplied_signature, expected_signature)
        or unsigned_envelope.get("evidence_policy_fingerprint")
        != context.profile.fingerprint
        or unsigned_envelope.get("work_unit_fingerprint")
        != context.work_unit_fingerprint
        or unsigned_envelope.get("manifest_fingerprint")
        != _runtime_trust_fingerprint(persisted_manifest)
    ):
        raise ValueError("trusted evidence runtime identity mismatch")
    return {
        "evidence_policy_v2_required": True,
        "evidence_policy_v2_preflight_passed": True,
        "evidence_policy_v2_runtime_trust_passed": True,
        "evidence_policy_v2_profile_fingerprint": context.profile.fingerprint,
        "evidence_policy_v2_work_unit_fingerprint": (
            context.work_unit_fingerprint
        ),
        "evidence_policy_v2_manifest_fingerprint": manifest_fingerprint,
        "evidence_policy_v2_manifest_path": str(destination),
        "evidence_policy_v2_task_response_digest": response_digest,
        "evidence_policy_v2_writer_attestation_fingerprint": (
            context.writer.fingerprint
        ),
        "measurement_plan_fingerprint": context.measurement_plan_fingerprint,
        "measurement_work_unit_id": context.measurement_work_unit_id,
        "isolation_grant_fingerprint": context.isolation_grant_fingerprint,
        "lane_materialization_fingerprint": (
            context.lane_materialization_fingerprint
        ),
    }


def _replay_execution_variant_role(request: ReplayExecutionRequest) -> str:
    if request.variant_role is not None:
        return request.variant_role
    if request.measurement_work_unit is not None:
        arm = request.measurement_work_unit.arm
        return (
            "baseline"
            if arm is MeasurementArm.CONTROL
            else "candidate"
        )
    if (
        request.variant_id == "baseline"
        or request.variant_id.startswith("baseline-")
        or request.variant_id.startswith("baseline__evidence_retry_")
    ):
        return "baseline"
    return "candidate"


class AWorldCliReplayExecutor:
    _DEFAULT_TOOL_CALL_LIMIT = 24
    _DEFAULT_RESERVED_OUTPUT_TOKENS = 4096
    _DEFAULT_ARTIFACT_FILE_LIMIT = 8
    _DEFAULT_ARTIFACT_BYTE_LIMIT = 2_000_000
    _DEFAULT_MAX_CONSECUTIVE_FAILED_ACTIONS = 2

    async def __call__(self, request: ReplayExecutionRequest) -> ReplayExecutionResult:
        artifact_dir = Path(request.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        # Keep agent-owned evidence in a dedicated, initially empty namespace.
        # ``artifact_dir`` also contains the seeded replay workspace, framework
        # logs, service diagnostics, and lifecycle records.  Counting that
        # shared state against the agent evidence quota makes a sufficiently
        # large workspace fail before the first tool call can execute.
        evidence_dir = artifact_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_manifest = evidence_dir / "evidence_manifest.jsonl"
        task_response_path = artifact_dir / "framework_task_response.json"
        trust_context: _ReplayEvidenceTrustContext | None = None
        if request.evidence_policy_mode == "required":
            try:
                trust_context = _prepare_replay_evidence_trust(
                    request,
                    artifact_dir=artifact_dir,
                    evidence_dir=evidence_dir,
                )
            except (EvidencePolicyValidationError, OSError, ValueError) as exc:
                return ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    failure={
                        "code": "evidence_policy_v2_preflight_failed",
                        "outcome": "infrastructure_failure",
                        "failure_class": "measurement_runtime_trust",
                        "failure_stage": "replay_preflight",
                        "repairable": False,
                        "reason": str(exc),
                    },
                    metrics={
                        "evidence_policy_v2_required": True,
                        "evidence_policy_v2_preflight_passed": False,
                    },
                )
        if trust_context is not None:
            scratch_file_limit, scratch_byte_limit = _profile_scratch_limits(
                trust_context.profile
            )
            task_response_max_bytes = _profile_artifact_policy(
                trust_context.profile,
                _REPLAY_TRUSTED_RESPONSE_TYPE,
            ).max_bytes
            max_consecutive_failed_actions = (
                trust_context.profile.max_consecutive_failed_actions
            )
        else:
            scratch_file_limit = self._DEFAULT_ARTIFACT_FILE_LIMIT
            scratch_byte_limit = self._DEFAULT_ARTIFACT_BYTE_LIMIT
            task_response_max_bytes = _MAX_SELF_EVOLVE_TASK_RESPONSE_BYTES
            max_consecutive_failed_actions = (
                self._DEFAULT_MAX_CONSECUTIVE_FAILED_ACTIONS
            )
        _initialize_replay_evidence_policy_state(
            evidence_dir,
            artifact_file_limit=scratch_file_limit,
            artifact_byte_limit=scratch_byte_limit,
            max_consecutive_failed_actions=max_consecutive_failed_actions,
        )
        # Keep process-local roots short as well as isolated. Unix-domain socket
        # consumers (browser drivers in particular) commonly impose path limits
        # near 100 bytes, while replay artifact paths are intentionally verbose.
        runtime_root = _short_runtime_root("aworld-replay-runtime-")
        isolated_runtime_paths = {
            "HOME": runtime_root / "home",
            "XDG_CONFIG_HOME": runtime_root / "xdg-config",
            "XDG_CACHE_HOME": runtime_root / "xdg-cache",
            "XDG_DATA_HOME": runtime_root / "xdg-data",
            "XDG_STATE_HOME": runtime_root / "xdg-state",
            "TMPDIR": runtime_root / "tmp",
            "AWORLD_MEMORY_ROOT": runtime_root / "memory",
        }
        for path in isolated_runtime_paths.values():
            path.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "run",
            "--task",
            _replay_task_text(
                request.task_text,
                artifact_dir=evidence_dir,
                evidence_manifest=evidence_manifest,
                workspace_root=Path(request.workspace_root),
            ),
            "--non-interactive",
            "--emit-trajectory",
        ]
        if request.agent:
            command.extend(["--agent", request.agent])
        if request.skill_root:
            command.extend(["--skill-path", request.skill_root])
        for skill_name in request.skill_names:
            command.extend(["--skill", skill_name])
        if request.max_steps is not None:
            command.extend(["--max-runs", str(request.max_steps)])
        if request.max_cost_usd is not None:
            command.extend(["--max-cost", str(request.max_cost_usd)])

        execution_environment = _with_loopback_proxy_bypass(
            {
                **os.environ,
                **dict(request.environment),
                **{
                    name: str(path)
                    for name, path in isolated_runtime_paths.items()
                },
                "AWORLD_SELF_EVOLVE_AUTO_DRAIN": "0",
                "AWORLD_SELF_EVOLVE_DISABLE_PROVIDER_RETRIES": (
                    "1" if trust_context is not None else "0"
                ),
                "AWORLD_REPLAY_ARTIFACT_DIR": str(evidence_dir),
                "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR": str(evidence_dir),
                "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST": str(evidence_manifest),
                "AWORLD_SELF_EVOLVE_TASK_RESPONSE_PATH": str(
                    task_response_path
                ),
                "AWORLD_SELF_EVOLVE_TASK_RESPONSE_CAPABILITY_MAX_BYTES": str(
                    task_response_max_bytes
                ),
                # This root is already copied into a private replay workspace.
                # Mark it as the only unpublished candidate source that the
                # child resolver may prioritize.  Acceptance remains bound to
                # the signed activation evidence and expected package digest.
                "AWORLD_SELF_EVOLVE_ISOLATED_SKILL_ROOTS": str(
                    request.skill_root or ""
                ),
                "AWORLD_REPLAY_EVIDENCE_POLICY": "1",
                "AWORLD_REPLAY_EVIDENCE_POLICY_MODE": (
                    "shadow" if trust_context is not None else "legacy"
                ),
                "AWORLD_REPLAY_ARTIFACT_FILE_LIMIT": str(
                    scratch_file_limit
                ),
                "AWORLD_REPLAY_ARTIFACT_BYTE_LIMIT": str(
                    scratch_byte_limit
                ),
                "AWORLD_REPLAY_MAX_CONSECUTIVE_FAILED_ACTIONS": str(
                    max_consecutive_failed_actions
                ),
                "AWORLD_LOG_PATH": str(artifact_dir / "logs"),
                "AWORLD_TRAJECTORY_LOG_DISABLED": "1",
                "AWORLD_TOOL_CALL_LIMIT": str(
                    request.max_tool_calls or self._DEFAULT_TOOL_CALL_LIMIT
                ),
                "AWORLD_PROMPT_BUDGET_RESERVED_OUTPUT_TOKENS": str(
                    self._DEFAULT_RESERVED_OUTPUT_TOKENS
                ),
                "AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES": "AWORLD_REPLAY_",
            }
        )
        for reserved_name in _REPLAY_TRUST_RESERVED_ENV:
            execution_environment.pop(reserved_name, None)
        task_response_capability_fd: int | None = None
        task_response_capability_reader_fd: int | None = None
        if trust_context is not None:
            (
                task_response_capability_reader_fd,
                task_response_capability_fd,
            ) = os.pipe()
            os.set_inheritable(task_response_capability_fd, True)
            execution_environment[_TASK_RESPONSE_CAPABILITY_FD_ENV] = str(
                task_response_capability_fd
            )
        execution_started_at = time.time()
        cancellation_event = threading.Event()
        try:
            execution_task = asyncio.create_task(
                asyncio.to_thread(
                    _run_replay_cli,
                    command,
                    cwd=request.workspace_root,
                    text=True,
                    capture_output=True,
                    timeout=request.timeout_seconds,
                    start_new_session=True,
                    env=execution_environment,
                    artifact_dir=artifact_dir,
                    evidence_manifest=evidence_manifest,
                    task_response_path=task_response_path,
                    execution_started_at=execution_started_at,
                    replay_environment=request.environment,
                    cancellation_event=cancellation_event,
                    task_response_capability_fd=(
                        task_response_capability_fd
                    ),
                    task_response_capability_reader_fd=(
                        task_response_capability_reader_fd
                    ),
                    task_response_attestation_key=(
                        trust_context.signing_key
                        if trust_context is not None
                        else None
                    ),
                    evidence_finalization_timeout_seconds=(
                        request.evidence_finalization_timeout_seconds
                        or _EVIDENCE_FINALIZATION_GRACE_SECONDS
                    ),
                    task_response_max_bytes=task_response_max_bytes,
                )
            )
            # The supervisor thread now owns both pipe descriptors. Clearing
            # the outer references prevents a reused descriptor number from
            # being closed after the subprocess has already released it.
            task_response_capability_fd = None
            task_response_capability_reader_fd = None
            try:
                # Shield the worker so cancellation can first signal the
                # subprocess supervisor and then wait for process-group
                # teardown instead of leaving replay work behind.
                completed = await asyncio.shield(execution_task)
            except asyncio.CancelledError:
                cancellation_event.set()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.shield(execution_task),
                        timeout=3.5,
                    )
                raise
        except subprocess.TimeoutExpired as exc:
            stdout = _text_output(exc.stdout)
            stderr = _text_output(exc.stderr)
            evidence_metrics = _replay_evidence_metrics(
                stdout=stdout,
                stderr=stderr,
                trajectory=[],
                artifact_dir=evidence_dir,
                evidence_manifest=evidence_manifest,
                workspace_root=Path(request.workspace_root),
                variant_id=request.variant_id,
                variant_role=_replay_execution_variant_role(request),
            )
            termination_diagnostics = _timeout_termination_diagnostics(
                request,
                evidence_metrics,
                default_tool_call_limit=self._DEFAULT_TOOL_CALL_LIMIT,
            )
            if getattr(exc, "evidence_finalization_deadline", False):
                termination_diagnostics.update(
                    {
                        "termination_kind": "evidence_finalization_deadline",
                        "termination_budget_axis": "finalization_grace",
                        "evidence_finalization_grace_seconds": (
                            request.evidence_finalization_timeout_seconds
                            or _EVIDENCE_FINALIZATION_GRACE_SECONDS
                        ),
                    }
                )
            compacted_argument_failure = _compacted_argument_replay_failure(
                evidence_metrics
            )
            if compacted_argument_failure is not None:
                return ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    stdout=stdout,
                    stderr=stderr,
                    failure=compacted_argument_failure,
                    metrics=evidence_metrics,
                )
            evidence_policy_failure = _evidence_quality_failure(
                evidence_metrics,
                variant_id=request.variant_id,
                variant_role=_replay_execution_variant_role(request),
            )
            if (
                evidence_policy_failure is not None
                and evidence_policy_failure.get("code")
                == "replay_evidence_runtime_policy_violation"
            ):
                return ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    stdout=stdout,
                    stderr=stderr,
                    failure=evidence_policy_failure,
                    metrics=evidence_metrics,
                )
            process_diagnostics = _bounded_process_output_diagnostics(
                stdout=stdout,
                stderr=stderr,
                workspace_root=Path(request.workspace_root),
                artifact_dir=artifact_dir,
                since=execution_started_at,
            )
            process_diagnostics = {
                **process_diagnostics,
                "diagnostics": {
                    **(
                        dict(process_diagnostics.get("diagnostics", {}))
                        if isinstance(process_diagnostics.get("diagnostics"), Mapping)
                        else {}
                    ),
                    **termination_diagnostics,
                },
            }
            if _diagnostics_indicate_replay_dependency_failure(
                process_diagnostics,
                environment=request.environment,
            ):
                return ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    stdout=stdout,
                    stderr=stderr,
                    failure={
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "outcome": "candidate_failure",
                        "failure_class": "candidate_replay_capability",
                        "failure_stage": "task_rollout",
                        "repairable": True,
                        **termination_diagnostics,
                        **process_diagnostics,
                    },
                    metrics={**evidence_metrics, **termination_diagnostics},
                )
            if _has_valid_artifact_backed_timeout_evidence(evidence_metrics):
                metrics = {
                    "trajectory_capture_mode": "evidence_only",
                    "timeout_evidence_recovered": True,
                    "task_completion_established": False,
                    **evidence_metrics,
                }
                counterexample = _timeout_evidence_counterexample(
                    metrics,
                    finalization_deadline=bool(
                        getattr(exc, "evidence_finalization_deadline", False)
                    ),
                )
                metrics = {
                    **metrics,
                    "replay_counterexamples": [counterexample],
                }
                return ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    stdout=stdout,
                    stderr=stderr,
                    failure={
                        "code": (
                            "replay_evidence_finalization_timeout"
                            if getattr(
                                exc,
                                "evidence_finalization_deadline",
                                False,
                            )
                            else "replay_task_timeout_with_recoverable_evidence"
                        ),
                        "type": "TimeoutExpired",
                        "outcome": "task_failure",
                        "failure_class": "task_timeout_with_recoverable_evidence",
                        "failure_stage": "task_rollout",
                        "repairable": bool(
                            getattr(
                                exc,
                                "evidence_finalization_deadline",
                                False,
                            )
                        ),
                        "category": "task_completion",
                        "reason": (
                            "replay timed out after persisting recoverable evidence; "
                            "task completion was not established"
                        ),
                        "diagnostics": {
                            "evidence_recoverable": True,
                            "task_completion_established": False,
                            "replay_counterexamples": [counterexample],
                            **termination_diagnostics,
                        },
                        **termination_diagnostics,
                    },
                    metrics={**metrics, **termination_diagnostics},
                )
            failure: dict[str, Any] = {
                "type": "TimeoutExpired",
                "reason": "replay timed out",
                "failure_stage": "task_rollout",
                **termination_diagnostics,
            }
            if _diagnostics_indicate_replay_dependency_failure(
                process_diagnostics,
                environment=request.environment,
            ):
                failure["outcome"] = "candidate_failure"
                failure["failure_class"] = "candidate_replay_capability"
                failure["failure_stage"] = "task_rollout"
                failure["repairable"] = True
            failure.update(process_diagnostics)
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                stdout=stdout,
                stderr=stderr,
                failure=failure,
                metrics={**evidence_metrics, **termination_diagnostics},
            )
        finally:
            if task_response_capability_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(task_response_capability_fd)
            if task_response_capability_reader_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(task_response_capability_reader_fd)
            shutil.rmtree(runtime_root, ignore_errors=True)

        stdout = _text_output(completed.stdout)
        stderr = _text_output(completed.stderr)
        trajectory_payload = _extract_trajectory_payload_from_stdout(stdout)
        trajectory = trajectory_payload["trajectory"]
        capture_mode = trajectory_payload["trajectory_capture_mode"]
        evidence_metrics: dict[str, Any] = {}
        trust_metrics: dict[str, Any] = {}
        framework_evidence_metrics: dict[str, Any] = {}
        trusted_usage_metrics: dict[str, int | bool] = {}
        trusted_task_response: Mapping[str, Any] | None = None
        activation_metrics = _trusted_skill_activation_metrics(request, None)
        signed_task_response_validated = False
        try:
            effective_evidence_manifest = evidence_manifest
            if trust_context is not None:
                response_policy = _profile_artifact_policy(
                    trust_context.profile,
                    _REPLAY_TRUSTED_RESPONSE_TYPE,
                )
                trusted_task_response = _load_self_evolve_task_response(
                    task_response_path,
                    attestation_key=trust_context.signing_key,
                    max_bytes=response_policy.max_bytes,
                )
                if trusted_task_response is None:
                    raise ValueError(
                        "framework task response attestation is missing or invalid"
                    )
                signed_task_response_validated = True
                activation_metrics = _trusted_skill_activation_metrics(
                    request,
                    trusted_task_response,
                )
                trusted_trajectory_value = (
                    trusted_task_response.get("trajectory")
                    if isinstance(trusted_task_response, Mapping)
                    else None
                )
                trusted_trajectory = (
                    [
                        item
                        for item in trusted_trajectory_value
                        if isinstance(item, Mapping)
                    ]
                    if isinstance(trusted_trajectory_value, list)
                    else []
                )
                # The signed TaskResponse is the parent-owned trajectory
                # authority in required mode.  A zero exit after reaching
                # ``--max-runs`` is not task completion when the last action
                # still requests another agent turn.  Detect that before
                # evidence finalization so an ordinary rollout budget censor
                # cannot be misreported as a framework attestation failure.
                trajectory = trusted_trajectory
                trusted_capture_mode = (
                    trusted_task_response.get("trajectory_capture_mode")
                    if isinstance(trusted_task_response, Mapping)
                    else None
                )
                if isinstance(trusted_capture_mode, str):
                    capture_mode = trusted_capture_mode
                trusted_usage_metrics = _trusted_task_response_usage_metrics(
                    trusted_task_response
                )
                if (
                    isinstance(trusted_task_response, Mapping)
                    and not _trajectory_task_completion_established(
                        trusted_trajectory,
                        capture_mode=capture_mode,
                    )
                ):
                    metrics = {
                        "returncode": completed.returncode,
                        "evidence_ready_early_stop": bool(
                            getattr(completed, "evidence_ready_early_stop", False)
                        ),
                        "trajectory_capture_mode": capture_mode,
                        "task_completion_established": False,
                        "timeout_evidence_recovered": False,
                        "evidence_policy_v2_required": True,
                        "evidence_policy_v2_preflight_passed": True,
                        "evidence_policy_v2_runtime_trust_passed": False,
                        "signed_task_response_validated": True,
                        **activation_metrics,
                    }
                    boundary_failure = _replay_dependency_boundary_failure(
                        trusted_trajectory,
                        environment=request.environment,
                    )
                    if boundary_failure is not None:
                        return ReplayExecutionResult(
                            status="failed",
                            trajectory=trusted_trajectory,
                            stdout=stdout,
                            stderr=stderr,
                            metrics={
                                **metrics,
                                "replay_dependency_boundary_passed": False,
                                "undeclared_loopback_endpoint_count": len(
                                    boundary_failure[
                                        "undeclared_loopback_endpoints"
                                    ]
                                ),
                            },
                            failure=boundary_failure,
                        )
                    return _task_completion_not_established_result(
                        request=request,
                        trajectory=trusted_trajectory,
                        stdout=stdout,
                        stderr=stderr,
                        metrics={
                            **metrics,
                            "replay_dependency_boundary_passed": True,
                            "undeclared_loopback_endpoint_count": 0,
                        },
                        trigger="agent_not_finished",
                        required_transition=(
                            "continue_rollout_until_terminal_action"
                        ),
                    )
                final_answer = _replay_final_answer(trusted_trajectory)
                task_response_only_digest = (
                    "sha256:"
                    + hashlib.sha256(final_answer.encode("utf-8")).hexdigest()
                    if final_answer
                    and _trajectory_external_tool_call_count(trusted_trajectory)
                    == 0
                    else None
                )
                (
                    effective_evidence_manifest,
                    framework_evidence_metrics,
                ) = _framework_canonical_evidence_manifest(
                    evidence_dir=evidence_dir,
                    candidate_manifest=evidence_manifest,
                    profile=trust_context.profile,
                    task_response_only_digest=task_response_only_digest,
                )
            evidence_metrics = _replay_evidence_metrics(
                stdout=stdout,
                stderr=stderr,
                trajectory=trajectory,
                artifact_dir=evidence_dir,
                evidence_manifest=effective_evidence_manifest,
                workspace_root=Path(request.workspace_root),
                variant_id=request.variant_id,
                variant_role=_replay_execution_variant_role(request),
            )
            evidence_metrics.update(framework_evidence_metrics)
            if trust_context is not None:
                trust_metrics = _finalize_replay_evidence_trust(
                    trust_context,
                    artifact_dir=artifact_dir,
                    evidence_dir=evidence_dir,
                    task_response_path=task_response_path,
                )
        except ReplayEvidenceProducerError as exc:
            variant_role = _replay_execution_variant_role(request)
            producer_metrics = dict(exc.diagnostics)
            required_action = (
                "repair_target_evidence_production"
                if variant_role == "baseline"
                else "repair_candidate_evidence_production"
            )
            return ReplayExecutionResult(
                status="failed",
                trajectory=trajectory,
                stdout=stdout,
                stderr=stderr,
                failure={
                    "code": "replay_evidence_production_failed",
                    "outcome": (
                        "task_failure"
                        if variant_role == "baseline"
                        else "candidate_failure"
                    ),
                    "failure_class": (
                        "baseline_evidence_production"
                        if variant_role == "baseline"
                        else "candidate_evidence_production"
                    ),
                    "failure_owner": (
                        FailureOwner.TASK.value
                        if variant_role == "baseline"
                        else FailureOwner.CANDIDATE.value
                    ),
                    "failure_scope": FailureScope.MEMBER.value,
                    "failure_stage": "evidence_finalization",
                    "repairable": variant_role == "candidate",
                    "category": "evidence_production",
                    "reason": str(exc),
                    "diagnostics": {
                        "required_action": required_action,
                        "producer_failure_code": exc.code,
                        **producer_metrics,
                    },
                },
                metrics={
                    **evidence_metrics,
                    **framework_evidence_metrics,
                    **producer_metrics,
                    **activation_metrics,
                    "evidence_policy_v2_required": True,
                    "evidence_policy_v2_preflight_passed": True,
                    "evidence_policy_v2_runtime_trust_passed": False,
                    "signed_task_response_validated": (
                        signed_task_response_validated
                    ),
                },
            )
        except (EvidencePolicyValidationError, OSError, ValueError) as exc:
            return ReplayExecutionResult(
                status="failed",
                trajectory=trajectory,
                stdout=stdout,
                stderr=stderr,
                failure={
                    "code": "evidence_policy_v2_attestation_failed",
                    "outcome": "framework_failure",
                    "failure_class": "measurement_runtime_trust",
                    "failure_owner": FailureOwner.FRAMEWORK.value,
                    "failure_scope": FailureScope.SHARED_RUN.value,
                    "failure_stage": "evidence_finalization",
                    "repairable": True,
                    "reason": str(exc),
                    "diagnostics": {
                        "required_action": (
                            "repair_framework_evidence_finalization_contract"
                        ),
                        **framework_evidence_metrics,
                    },
                },
                metrics={
                    **evidence_metrics,
                    **framework_evidence_metrics,
                    **activation_metrics,
                    "evidence_policy_v2_required": True,
                    "evidence_policy_v2_preflight_passed": True,
                    "evidence_policy_v2_runtime_trust_passed": False,
                    "signed_task_response_validated": (
                        signed_task_response_validated
                    ),
                },
            )
        metrics = {
            "returncode": completed.returncode,
            "evidence_ready_early_stop": bool(
                getattr(completed, "evidence_ready_early_stop", False)
            ),
            "trajectory_capture_mode": capture_mode,
            "task_completion_established": bool(
                completed.returncode == 0
                and _trajectory_task_completion_established(
                    trajectory,
                    capture_mode=capture_mode,
                )
            ),
            "timeout_evidence_recovered": False,
            **trusted_usage_metrics,
            **evidence_metrics,
            **trust_metrics,
            **activation_metrics,
        }
        compacted_argument_failure = _compacted_argument_replay_failure(metrics)
        if compacted_argument_failure is not None:
            return ReplayExecutionResult(
                status="failed",
                trajectory=trajectory,
                stdout=stdout,
                stderr=stderr,
                metrics=metrics,
                failure=compacted_argument_failure,
            )
        boundary_failure = _replay_dependency_boundary_failure(
            trajectory,
            environment=request.environment,
        )
        metrics.update(
            {
                "replay_dependency_boundary_passed": boundary_failure is None,
                "undeclared_loopback_endpoint_count": (
                    0
                    if boundary_failure is None
                    else len(boundary_failure["undeclared_loopback_endpoints"])
                ),
            }
        )
        if boundary_failure is not None:
            return ReplayExecutionResult(
                status="failed",
                trajectory=trajectory,
                stdout=stdout,
                stderr=stderr,
                metrics=metrics,
                failure=boundary_failure,
            )
        if completed.returncode != 0:
            return ReplayExecutionResult(
                status="failed",
                trajectory=trajectory,
                stdout=stdout,
                stderr=stderr,
                metrics=metrics,
                failure={
                    "type": "ProcessError",
                    "reason": "aworld-cli run failed",
                    "returncode": completed.returncode,
                    "command": command,
                },
            )
        evidence_failure = _evidence_quality_failure(
            metrics,
            variant_id=request.variant_id,
            variant_role=_replay_execution_variant_role(request),
        )
        if evidence_failure is not None:
            return ReplayExecutionResult(
                status="failed",
                trajectory=trajectory,
                stdout=stdout,
                stderr=stderr,
                metrics=metrics,
                failure=evidence_failure,
            )
        if metrics.get("task_completion_established") is not True:
            return _task_completion_not_established_result(
                request=request,
                trajectory=trajectory,
                stdout=stdout,
                stderr=stderr,
                metrics=metrics,
                trigger=(
                    "trajectory_unavailable"
                    if not trajectory
                    else (
                        "unsupported_trajectory_capture_mode"
                        if capture_mode != "task_response"
                        else "agent_not_finished"
                    )
                ),
                required_transition=(
                    "emit_task_response_trajectory"
                    if not trajectory or capture_mode != "task_response"
                    else "continue_rollout_until_terminal_action"
                ),
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=trajectory,
            stdout=stdout,
            stderr=stderr,
            metrics=metrics,
        )


def _bounded_process_output_diagnostics(
    *,
    stdout: str,
    stderr: str,
    workspace_root: Path,
    artifact_dir: Path,
    since: float,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    operational_stdout = _operational_replay_stdout(stdout)
    if operational_stdout.strip():
        diagnostics["stdout_tail"] = sanitize_text(
            operational_stdout[-6_000:],
            max_chars=4_000,
        )
    if stderr.strip():
        diagnostics["stderr_tail"] = sanitize_text(
            stderr[-3_000:],
            max_chars=2_000,
        )
    task_artifacts = _recent_task_artifact_diagnostics(
        workspace_root=workspace_root,
        artifact_dir=artifact_dir,
        since=since,
    )
    if task_artifacts:
        diagnostics["task_artifacts"] = task_artifacts
    return {"diagnostics": diagnostics} if diagnostics else {}


def _operational_replay_stdout(stdout: str) -> str:
    """Exclude the echoed task contract from timeout classification."""

    history_marker = "No history file. Start chatting to generate history."
    if history_marker in stdout:
        return stdout.rsplit(history_marker, 1)[-1]
    task_marker = "🔄 Running task:"
    if task_marker not in stdout:
        return stdout
    after_marker = stdout.rsplit(task_marker, 1)[-1]
    _, separator, operational_stdout = after_marker.partition("\n")
    return operational_stdout if separator else ""


_REPLAY_DEPENDENCY_STRONG_FAILURE_SIGNALS = (
    "does not implement",
    "no websocket",
    "protocol error",
    "protocol mismatch",
    "replay capability mismatch",
    "unexpected status",
    "cdp response channel closed",
    "operation timed out. the page may still be loading",
)
_REPLAY_DEPENDENCY_ENDPOINT_FAILURE_SIGNALS = (
    "connection refused",
    "discovery methods failed",
    "this is a protocol signal",
    "prerequisite unavailable",
    "stuck while connecting",
    "hung during navigation",
    "still navigating",
    "exited without producing output",
    "waiting for the page to load",
    "正在导航",
    "仍在导航",
    "等待页面加载",
    "unresponsive",
    "failed to deserialize",
    "missing field",
    "doesn't implement the full",
    "does not implement the full",
)
_REPLAY_DEPENDENCY_LIVE_PROGRESS_SIGNALS = frozenset(
    {
        "waiting for the page to load",
        "正在导航",
        "等待页面加载",
    }
)
_REPLAY_DEPENDENCY_LIVE_ENDPOINT_FAILURE_SIGNALS = tuple(
    signal
    for signal in _REPLAY_DEPENDENCY_ENDPOINT_FAILURE_SIGNALS
    if signal not in _REPLAY_DEPENDENCY_LIVE_PROGRESS_SIGNALS
)


def _partial_process_diagnostics_indicate_replay_failure(
    diagnostics: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
) -> bool:
    """Classify only live operational output, excluding static task contracts."""

    replay_endpoints = tuple(
        value.rstrip("/")
        for name, value in environment.items()
        if name.startswith("AWORLD_REPLAY_ENDPOINT_") and value.strip()
    )
    if not replay_endpoints:
        return False
    diagnostic_text = _flatten_diagnostic_text(diagnostics).lower()
    if not _diagnostics_reference_replay_endpoint(
        diagnostic_text,
        replay_endpoints=replay_endpoints,
    ):
        return False
    live_signals = (
        *_REPLAY_DEPENDENCY_LIVE_ENDPOINT_FAILURE_SIGNALS,
        "cdp response channel closed",
        "operation timed out. the page may still be loading",
    )
    return any(signal in diagnostic_text for signal in live_signals)


def _diagnostics_indicate_replay_dependency_failure(
    diagnostics: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
    live: bool = False,
) -> bool:
    replay_endpoints = tuple(
        value.rstrip("/")
        for name, value in environment.items()
        if name.startswith("AWORLD_REPLAY_ENDPOINT_") and value.strip()
    )
    if not replay_endpoints:
        return False
    diagnostic_text = _flatten_diagnostic_text(diagnostics).lower()
    if any(
        signal in diagnostic_text
        for signal in _REPLAY_DEPENDENCY_STRONG_FAILURE_SIGNALS
    ):
        return True
    if _scoped_task_artifacts_indicate_replay_dependency_failure(
        diagnostics,
        live=live,
    ):
        return True
    if re.search(
        r"does not look like (?:an? )?[^.\n]{1,80} server",
        diagnostic_text,
    ):
        return True
    endpoint_referenced = _diagnostics_reference_replay_endpoint(
        diagnostic_text,
        replay_endpoints=replay_endpoints,
    )
    if endpoint_referenced and re.search(
        r"\bnot\s+(?:an?\s+)?[^.\n,;]{1,80}\s+endpoint\b",
        diagnostic_text,
    ):
        return True
    return bool(
        endpoint_referenced
        and any(
            signal in diagnostic_text
            for signal in (
                _REPLAY_DEPENDENCY_LIVE_ENDPOINT_FAILURE_SIGNALS
                if live
                else _REPLAY_DEPENDENCY_ENDPOINT_FAILURE_SIGNALS
            )
        )
    )


def _scoped_task_artifacts_indicate_replay_dependency_failure(
    diagnostics: Mapping[str, Any],
    *,
    live: bool = False,
) -> bool:
    nested = diagnostics.get("diagnostics")
    if not isinstance(nested, Mapping):
        return False
    task_artifacts = nested.get("task_artifacts")
    if not isinstance(task_artifacts, list) or not task_artifacts:
        return False
    artifact_text = _flatten_diagnostic_text(
        {"task_artifacts": task_artifacts}
    ).lower()
    return any(
        signal in artifact_text
        for signal in (
            *_REPLAY_DEPENDENCY_STRONG_FAILURE_SIGNALS,
            *(
                _REPLAY_DEPENDENCY_LIVE_ENDPOINT_FAILURE_SIGNALS
                if live
                else _REPLAY_DEPENDENCY_ENDPOINT_FAILURE_SIGNALS
            ),
        )
    )


def _diagnostics_reference_replay_endpoint(
    diagnostic_text: str,
    *,
    replay_endpoints: tuple[str, ...],
) -> bool:
    for endpoint in replay_endpoints:
        endpoint_text = endpoint.lower()
        if endpoint_text in diagnostic_text:
            return True
        port_match = re.search(r":(\d{1,5})(?:/|$)", endpoint_text)
        if port_match is None:
            continue
        port = re.escape(port_match.group(1))
        if re.search(
            rf"(?:\bport\s+{port}\b|\b(?:127\.0\.0\.1|localhost):{port}\b)",
            diagnostic_text,
        ):
            return True
    return False


def _flatten_diagnostic_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return "\n".join(
            _flatten_diagnostic_text(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return "\n".join(_flatten_diagnostic_text(item) for item in value)
    if isinstance(value, str):
        return value
    return ""


_TASK_DIAGNOSTIC_SUFFIXES = frozenset({".log", ".out", ".err", ".txt", ".json"})
_TASK_DIAGNOSTIC_NAME_MARKERS = (
    "diag",
    "capability_mismatch",
    "output",
    "result",
    "error",
    "failure",
    "stderr",
    "stdout",
    "log",
)
_FRAMEWORK_DIAGNOSTIC_LOG_NAMES = frozenset(
    {
        "aworld.log",
        "aworld_error.log",
        "asyncio_monitor.log",
        "digest_logger.log",
        "gateway.log",
        "llm.log",
        "prompt_logger.log",
        "trace.log",
        "trajectory.log",
    }
)


def _recent_task_artifact_diagnostics(
    *,
    workspace_root: Path,
    artifact_dir: Path,
    since: float,
) -> list[dict[str, str]]:
    candidates: list[tuple[float, str, Path]] = []
    seen: set[Path] = set()
    inspected = 0
    for label, root in (("artifact", artifact_dir), ("workspace", workspace_root)):
        if not root.is_dir():
            continue
        root = root.resolve()
        for current, dirnames, filenames in os.walk(root):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                continue
            dirnames[:] = [
                name
                for name in dirnames
                if name not in {".git", ".venv", "node_modules", "__pycache__"}
            ]
            if depth >= 3:
                dirnames[:] = []
            for filename in filenames:
                inspected += 1
                if inspected > 2_500:
                    break
                lowered = filename.lower()
                path = current_path / filename
                if lowered in _FRAMEWORK_DIAGNOSTIC_LOG_NAMES:
                    continue
                if path.suffix.lower() not in _TASK_DIAGNOSTIC_SUFFIXES:
                    continue
                if not any(marker in lowered for marker in _TASK_DIAGNOSTIC_NAME_MARKERS):
                    continue
                try:
                    stat = path.stat()
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in seen or stat.st_mtime < since - 2.0:
                    continue
                if stat.st_size <= 0 or stat.st_size > 1_000_000:
                    continue
                seen.add(resolved)
                relative = path.relative_to(root).as_posix()
                candidates.append((stat.st_mtime, f"{label}/{relative}", path))
            if inspected > 2_500:
                break
        if inspected > 2_500:
            break

    result: list[dict[str, str]] = []
    for _, label, path in sorted(candidates, key=lambda item: (-item[0], item[1]))[:4]:
        try:
            raw = path.read_bytes()[-4_000:]
        except OSError:
            continue
        tail = sanitize_text(
            raw.decode("utf-8", errors="replace"),
            max_chars=1_600,
        )
        if tail:
            result.append({"path": label, "tail": tail})
    return result


def _replay_dependency_boundary_failure(
    trajectory: Sequence[Mapping[str, Any]],
    *,
    environment: Mapping[str, str],
) -> Mapping[str, Any] | None:
    allowed_endpoints = {
        match.group(0).lower()
        for key, value in environment.items()
        if key.startswith("AWORLD_REPLAY_ENDPOINT_")
        for match in [_LOOPBACK_HTTP_ENDPOINT_PATTERN.search(value)]
        if match is not None
    }
    observed_endpoints: set[str] = set()
    for step in trajectory:
        action = step.get("action")
        if not isinstance(action, Mapping):
            continue
        tool_calls = action.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            if not isinstance(function, Mapping):
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                serialized = arguments
            elif isinstance(arguments, (Mapping, list, tuple)):
                serialized = json.dumps(
                    arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            else:
                continue
            observed_endpoints.update(
                match.group(0).lower()
                for match in _LOOPBACK_HTTP_ENDPOINT_PATTERN.finditer(serialized)
            )
    undeclared = sorted(observed_endpoints - allowed_endpoints)
    if not undeclared:
        return None
    return {
        "type": "ReplayBoundaryViolation",
        "reason": "replay_dependency_boundary_violation",
        "outcome": "task_failure",
        "undeclared_loopback_endpoints": undeclared,
    }


def replay_support_fingerprint(
    replay_adaptation: ReplayAdaptationBundle | None,
) -> str | None:
    """Identify the executable support surface shared by both replay arms."""

    if replay_adaptation is None:
        return None
    capability = replay_adaptation.replay_capability
    payload = {
        "schema_version": "aworld.self_evolve.replay_support_identity.v1",
        "capability_package_fingerprint": (
            capability.capability_package_fingerprint
            if capability is not None
            else "framework-only"
        ),
        "replay_capability_fingerprint": (
            replay_capability_semantic_fingerprint(capability)
            if capability is not None
            else "framework-only"
        ),
        "adaptation_fingerprint": replay_adaptation_semantic_fingerprint(
            replay_adaptation
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def replay_timeout_envelope_fingerprint(
    *,
    timeout_seconds: float | None,
    max_steps: int | None,
    max_tool_calls: int | None,
) -> str:
    payload = {
        "schema_version": "aworld.self_evolve.replay_timeout_envelope.v1",
        "timeout_seconds": (
            float(timeout_seconds) if timeout_seconds is not None else None
        ),
        "max_steps": max_steps,
        "max_tool_calls": max_tool_calls,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_replay_request(
    *,
    run_id: str,
    workspace_root: str | Path,
    target: SelfEvolveTargetRef,
    candidate: CandidateVariant,
    overlay_skill_root: str | Path,
    dataset: SelfEvolveDataset,
    agent: str | None = None,
    timeout_seconds: float | None = None,
    max_steps: int | None = None,
    max_tool_calls: int | None = None,
    max_tokens: int | None = None,
    max_cost_usd: float | None = None,
    baseline_repetitions: int = 1,
    candidate_repetitions: int = 1,
    baseline_replay_dir: str | Path | None = None,
    resume_replay_dir: str | Path | None = None,
    replay_adaptation: ReplayAdaptationBundle | None = None,
    verified_candidate_package_fingerprint: str | None = None,
    artifact_namespace: str | None = None,
    invalid_control_patience: int = 2,
    measurement_early_stop_enabled: bool = False,
    stop_on_incomparable_member: bool = False,
    repetition_policy: str = "configured",
    evidence_policy_mode: str = "legacy",
    measurement_plan: MeasurementPlanV2 | None = None,
    measurement_isolation_decision: IsolationDecision | None = None,
    measurement_evidence_policy_profile: EvidencePolicyProfileV2 | None = None,
    measurement_lane_attestations: Mapping[
        str, LaneMaterializationAttestationV1
    ] | None = None,
) -> CandidateReplayRequest:
    if not dataset.cases:
        raise ValueError("candidate replay requires at least one eval case")
    if max_tool_calls is not None and (
        isinstance(max_tool_calls, bool) or max_tool_calls <= 0
    ):
        raise ValueError("candidate replay max_tool_calls must be positive")
    case = _select_replay_case(dataset)
    if replay_adaptation is not None:
        for replay_case in dataset.cases:
            if not _is_replayable_user_task_case(replay_case):
                continue
            replay_adaptation.case(replay_case.case_id)
        task_input = replay_adaptation.case(case.case_id).adapted_task_input
        adaptation_fingerprint = replay_adaptation.adaptation_fingerprint
        workspace_seed_fingerprint = replay_adaptation.workspace_seed_fingerprint
        task_input_fingerprint = replay_adaptation.case(
            case.case_id
        ).task_input_fingerprint
    else:
        task_input = case.input
        adaptation_fingerprint = None
        workspace_seed_fingerprint = None
        task_input_fingerprint = None
    support_fingerprint = replay_support_fingerprint(replay_adaptation)
    timeout_envelope_fingerprint = replay_timeout_envelope_fingerprint(
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
    )
    return CandidateReplayRequest(
        run_id=run_id,
        task_id=case.case_id,
        workspace_root=str(Path(workspace_root)),
        target=target,
        candidate_id=candidate.candidate_id,
        overlay_skill_root=str(Path(overlay_skill_root)),
        baseline_skill_root=_infer_baseline_skill_root_from_target(target),
        baseline_replay_dir=(
            str(Path(baseline_replay_dir)) if baseline_replay_dir is not None else None
        ),
        resume_replay_dir=(
            str(Path(resume_replay_dir)) if resume_replay_dir is not None else None
        ),
        task_input=task_input,
        agent=agent,
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
        baseline_repetitions=baseline_repetitions,
        candidate_repetitions=candidate_repetitions,
        replay_adaptation=replay_adaptation,
        dataset_fingerprint=replay_dataset_fingerprint(dataset),
        baseline_skill_fingerprint=candidate.target_fingerprint,
        adaptation_fingerprint=adaptation_fingerprint,
        support_fingerprint=support_fingerprint,
        timeout_envelope_fingerprint=timeout_envelope_fingerprint,
        workspace_seed_fingerprint=workspace_seed_fingerprint,
        task_input_fingerprint=task_input_fingerprint,
        verified_candidate_package_fingerprint=(
            verified_candidate_package_fingerprint
        ),
        artifact_namespace=artifact_namespace,
        invalid_control_patience=invalid_control_patience,
        measurement_early_stop_enabled=measurement_early_stop_enabled,
        stop_on_incomparable_member=stop_on_incomparable_member,
        repetition_policy=repetition_policy,
        evidence_policy_mode=evidence_policy_mode,
        measurement_plan=measurement_plan,
        measurement_isolation_decision=measurement_isolation_decision,
        measurement_evidence_policy_profile=measurement_evidence_policy_profile,
        measurement_lane_attestations=dict(measurement_lane_attestations or {}),
    )


def _adapted_task_input(request: CandidateReplayRequest, case: EvalCase) -> Any:
    if request.replay_adaptation is None:
        return case.input
    return request.replay_adaptation.case(case.case_id).adapted_task_input


def _measurement_work_unit_for_replay(
    request: CandidateReplayRequest,
    *,
    arm: MeasurementArm,
    repetition_id: int,
) -> MeasurementWorkUnitV1 | None:
    plan = request.measurement_plan
    if plan is None:
        return None
    matches = tuple(
        unit
        for unit in plan.work_units
        if unit.case_id == request.task_id
        and unit.arm is arm
        and unit.repetition_id == repetition_id
    )
    if len(matches) != 1:
        raise ValueError(
            "replay member does not map to exactly one frozen measurement work unit"
        )
    unit = matches[0]
    if unit.work_unit_id not in request.measurement_lane_attestations:
        raise ValueError("replay member has no materialized lane attestation")
    return unit


def _adapted_task_input_fingerprint(
    request: CandidateReplayRequest,
    case: EvalCase,
) -> str | None:
    if request.replay_adaptation is None:
        return request.task_input_fingerprint
    return request.replay_adaptation.case(case.case_id).task_input_fingerprint


def replay_dataset_fingerprint(dataset: SelfEvolveDataset) -> str:
    payload = {
        "cases": [
            {
                "case_id": case.case_id,
                "input": case.input,
                "expected_output": case.expected_output,
                "verification_command": case.verification_command,
                "metadata": case.metadata,
                "source": case.source,
                "context_snapshot_fingerprint": (
                    case.context_snapshot.fingerprint
                    if case.context_snapshot is not None
                    else None
                ),
            }
            for case in dataset.cases
        ],
        "recipe": to_json_dict(dataset.recipe),
    }
    encoded = json.dumps(
        to_json_dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _replay_execution_provenance(
    request: ReplayExecutionRequest,
) -> dict[str, str]:
    return {
        key: value
        for key, value in (
            ("adaptation_fingerprint", request.adaptation_fingerprint),
            ("support_fingerprint", request.support_fingerprint),
            (
                "timeout_envelope_fingerprint",
                request.timeout_envelope_fingerprint,
            ),
            ("workspace_seed_fingerprint", request.workspace_seed_fingerprint),
            ("task_input_fingerprint", request.task_input_fingerprint),
            ("dataset_fingerprint", request.dataset_fingerprint),
            ("baseline_skill_fingerprint", request.baseline_skill_fingerprint),
            ("adapter_determinism", request.adapter_determinism),
            ("isolated_workspace_path", request.isolated_workspace_path),
            ("replay_capability_id", request.replay_capability_id),
            (
                "capability_package_fingerprint",
                request.capability_package_fingerprint,
            ),
            (
                "frozen_capability_fingerprint",
                request.frozen_capability_fingerprint,
            ),
            (
                "service_runtime_fingerprint",
                request.service_runtime_fingerprint,
            ),
            ("service_logical_ids", request.service_logical_ids),
            ("service_endpoint", request.service_endpoint),
            ("service_startup_status", request.service_startup_status),
        )
        if value is not None
    }


def _expand_replay_placeholders(
    value: Any,
    *,
    workspace_root: Path,
    artifact_dir: Path,
) -> Any:
    def expand(text: str) -> str:
        return text.replace(
            REPLAY_WORKSPACE_PLACEHOLDER,
            str(workspace_root),
        ).replace(
            REPLAY_ARTIFACT_PLACEHOLDER,
            str(artifact_dir),
        )

    if isinstance(value, str):
        return expand(value)
    if isinstance(value, Mapping):
        return {
            str(key): _expand_replay_placeholders(
                item,
                workspace_root=workspace_root,
                artifact_dir=artifact_dir,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _expand_replay_placeholders(
                item,
                workspace_root=workspace_root,
                artifact_dir=artifact_dir,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _expand_replay_placeholders(
                item,
                workspace_root=workspace_root,
                artifact_dir=artifact_dir,
            )
            for item in value
        )
    return value


async def _start_replay_services(
    capability: FrozenReplayCapability,
    *,
    artifact_dir: Path,
    endpoint_bindings: Sequence[DynamicEndpointBinding] = (),
    required_nonempty_probe_operations: Sequence[str] = (),
    required_recorded_probe_operations: Sequence[str] = (),
    integrity_capability: FrozenReplayCapability | None = None,
) -> _ReplayServiceSession:
    if not capability.ready or not capability.deterministic:
        raise ValueError("skill-owned replay capability is not ready")
    if integrity_capability is None:
        verify_frozen_replay_capability(capability)
    else:
        verify_frozen_replay_capability(integrity_capability)
        if (
            capability.capability_id != integrity_capability.capability_id
            or capability.capability_package_fingerprint
            != integrity_capability.capability_package_fingerprint
            or Path(capability.frozen_root).expanduser().resolve()
            != Path(integrity_capability.frozen_root).expanduser().resolve()
            or not capability.services
            or any(
                integrity_capability.endpoint_replacements.get(source)
                != service_id
                for source, service_id in capability.endpoint_replacements.items()
            )
            or any(
                service_id
                not in {service.service_id for service in capability.services}
                for service_id in capability.endpoint_replacements.values()
            )
            or any(
                not any(
                    replace(
                        service,
                        protocol_probes=original.protocol_probes,
                    )
                    == original
                    and all(
                        probe in original.protocol_probes
                        for probe in service.protocol_probes
                    )
                    for original in integrity_capability.services
                )
                for service in capability.services
            )
        ):
            raise ValueError(
                "replay execution projection is not a subset of its verified capability"
            )
    source_frozen_root = Path(capability.frozen_root).expanduser().resolve()
    if not (source_frozen_root / "runtime").is_dir() or not (
        source_frozen_root / "fixtures"
    ).is_dir():
        raise ValueError("frozen replay capability directories are missing")
    private_root = _short_runtime_root("aworld-replay-service-")
    frozen_root = private_root / "capability"
    shutil.copytree(source_frozen_root, frozen_root, symlinks=False)
    fixture_root = (frozen_root / "fixtures").resolve()
    runtime_root = (frozen_root / "runtime").resolve()
    scratch_root = private_root / "scratch"
    service_logs = scratch_root / "logs"
    service_logs.mkdir(parents=True, exist_ok=True)
    diagnostics_root = artifact_dir / "replay_services"
    diagnostics_root.mkdir(parents=True, exist_ok=True)
    session = _ReplayServiceSession(
        endpoints={},
        environment={},
        processes=[],
        private_root=private_root,
        diagnostics_root=diagnostics_root,
    )
    session.monitor_task = asyncio.create_task(
        _monitor_replay_service_disk(session, max_bytes=96 * 1024 * 1024)
    )
    endpoints: dict[str, str] = {}
    environment: dict[str, str] = {}
    services_by_id = {service.service_id: service for service in capability.services}
    declared_endpoints = {
        binding.binding_id: binding
        for binding in endpoint_bindings
        if binding.binding_id in services_by_id
    }
    if endpoint_bindings and set(declared_endpoints) != set(services_by_id):
        raise ValueError(
            "measurement evidence policy does not bind every replay service endpoint"
        )
    if len(declared_endpoints) != len(
        [binding for binding in endpoint_bindings if binding.binding_id in services_by_id]
    ):
        raise ValueError("measurement replay service endpoint bindings are duplicated")
    declared_ports: set[int] = set()
    for service_id, binding in declared_endpoints.items():
        parsed = urlsplit(binding.endpoint)
        expected_task_path = services_by_id[service_id].task_entry_path or "/"
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port is None
            or (parsed.path or "/") != expected_task_path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"measurement endpoint for {service_id} does not bind its exact "
                "loopback task entry"
            )
        if parsed.port in declared_ports:
            raise ValueError("measurement replay service ports must be unique")
        declared_ports.add(parsed.port)
    # Read the operation-indexed response evidence for every preflight.  Strict
    # task-plane probes use the values as acceptance requirements; ordinary
    # probes use them only to classify compiler/runtime selector drift without
    # exposing recorded payloads in diagnostics.
    recorded_response_values = _replay_capability_recorded_response_values(
        capability
    )
    recorded_response_records = _replay_capability_recorded_response_records(
        capability
    )
    fixture_service = Path(__file__).with_name("fixture_service.py").resolve()
    try:
        for service in capability.services:
            declared_binding = declared_endpoints.get(service.service_id)
            port = (
                urlsplit(declared_binding.endpoint).port
                if declared_binding is not None
                else _reserve_loopback_port()
            )
            assert port is not None
            fixture_path = (fixture_root / service.response_fixture).resolve(
                strict=True
            )
            if not fixture_path.is_relative_to(fixture_root) or not fixture_path.is_file():
                raise ValueError(
                    f"replay service fixture escapes frozen fixtures: {service.service_id}"
                )
            service_scratch = scratch_root / _safe_path(service.service_id)
            service_scratch.mkdir(parents=True, exist_ok=True)
            if service.transport == "skill_runtime":
                if service.runtime_entrypoint is None:
                    raise ValueError("skill runtime service lacks an entrypoint")
                runtime_entrypoint = (
                    runtime_root / service.runtime_entrypoint
                ).resolve(strict=True)
                if (
                    not runtime_entrypoint.is_relative_to(runtime_root)
                    or not runtime_entrypoint.is_file()
                ):
                    raise ValueError("skill runtime entrypoint escapes frozen runtime")
                command = [
                    sys.executable,
                    "-I",
                    str(runtime_entrypoint),
                    "--port",
                    str(port),
                    "--fixture",
                    str(fixture_path),
                    "--scratch",
                    str(service_scratch),
                ]
                command_read_roots = (runtime_root, fixture_root)
            else:
                command = [
                    sys.executable,
                    "-I",
                    str(fixture_service),
                    "--port",
                    str(port),
                    "--transport",
                    service.transport,
                    "--fixture",
                    str(fixture_path),
                ]
                command_read_roots = (fixture_service, fixture_root)
            command = build_replay_sandboxed_command(
                command,
                read_roots=command_read_roots,
                writable_roots=(service_scratch,),
                allow_loopback=True,
            )
            command = build_replay_resource_limited_command(
                command,
                max_file_bytes=8 * 1024 * 1024,
                max_memory_bytes=512 * 1024 * 1024,
                cpu_seconds=600,
            )
            service_environment = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            # The response index is a framework-generated sidecar next to the
            # frozen fixture.  Expose its path explicitly instead of making a
            # skill runtime guess where replay metadata lives.  Adapters remain
            # skill-owned: the framework only supplies immutable evidence and
            # the generic operation-to-record binding.
            response_index_path = fixture_path.with_suffix(".responses.json")
            if response_index_path.is_file():
                service_environment["AWORLD_REPLAY_RESPONSE_INDEX"] = str(
                    response_index_path
                )
            response_record_ids = tuple(
                dict.fromkeys(
                    probe.response_record_id
                    for probe in service.protocol_probes
                    if isinstance(probe.response_record_id, str)
                    and probe.response_record_id
                )
            )
            if len(response_record_ids) == 1:
                service_environment[REPLAY_RESPONSE_RECORD_ID_ENV] = (
                    response_record_ids[0]
                )
            service_environment[REPLAY_RESPONSE_REQUIREMENT_ID_ENV] = (
                service.requirement_id
            )
            service_environment[REPLAY_RESPONSE_SERVICE_ID_ENV] = (
                service.service_id
            )
            service_environment["AWORLD_REPLAY_FIXTURE_PATH"] = str(fixture_path)
            service_dir = service_logs / _safe_path(service.service_id)
            service_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = service_dir / "stdout.txt"
            stderr_path = service_dir / "stderr.txt"
            diagnostics_service_dir = (
                diagnostics_root / _safe_path(service.service_id)
            )
            launch_diagnostic_path = diagnostics_service_dir / "launch.json"
            launch_started_at = time.time()
            _write_replay_service_launch_diagnostic(
                launch_diagnostic_path,
                service=service,
                command=command,
                environment_keys=tuple(service_environment),
                host="127.0.0.1",
                port=port,
                status="prepared",
                started_at=launch_started_at,
            )
            stdout_handle = stdout_path.open("wb")
            stderr_handle = stderr_path.open("wb")
            try:
                process = subprocess.Popen(
                    command,
                    cwd=private_root,
                    env=service_environment,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    start_new_session=True,
                )
            except Exception as exc:
                stdout_handle.close()
                stderr_handle.close()
                _write_replay_service_launch_diagnostic(
                    launch_diagnostic_path,
                    service=service,
                    command=command,
                    environment_keys=tuple(service_environment),
                    host="127.0.0.1",
                    port=port,
                    status="launch_failed",
                    started_at=launch_started_at,
                    error_type=type(exc).__name__,
                )
                raise
            _write_replay_service_launch_diagnostic(
                launch_diagnostic_path,
                service=service,
                command=command,
                environment_keys=tuple(service_environment),
                host="127.0.0.1",
                port=port,
                status="started",
                started_at=launch_started_at,
                process_id=process.pid,
            )
            session.processes.append(
                _ReplayServiceProcess(
                    process=process,
                    stdout_handle=stdout_handle,
                    stderr_handle=stderr_handle,
                    service_id=service.service_id,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )
            )
            endpoint = f"http://127.0.0.1:{port}"
            try:
                await _wait_for_replay_service(
                    process,
                    host="127.0.0.1",
                    port=port,
                    kind=service.readiness.kind,
                    path=service.readiness.path,
                    timeout_seconds=max(
                        service.readiness.timeout_seconds,
                        _MIN_REPLAY_SERVICE_STARTUP_TIMEOUT_SECONDS,
                    ),
                    phase="startup",
                    service_id=service.service_id,
                    transport=service.transport,
                    validate_advertised_websockets=(
                        service.transport == "skill_runtime"
                    ),
                )
                for protocol_probe in service.protocol_probes:
                    nonempty_probe_operations = tuple(
                        operation
                        for operation in required_nonempty_probe_operations
                        if _request_declares_operation(
                            protocol_probe.request_text,
                            operation,
                        )
                    )
                    recorded_probe_operations = tuple(
                        operation
                        for operation in required_recorded_probe_operations
                        if _request_declares_operation(
                            protocol_probe.request_text,
                            operation,
                        )
                    )
                    require_nonempty_correlated_response = bool(
                        nonempty_probe_operations
                    )
                    require_recorded_response = bool(recorded_probe_operations)
                    fixture_operation_values = recorded_response_values.get(
                        service.response_fixture,
                        {},
                    )
                    fixture_record_values = recorded_response_records.get(
                        service.response_fixture,
                        {},
                    )
                    diagnostic_recorded_response_values = tuple(
                        value
                        for values in fixture_operation_values.values()
                        for value in values
                    )
                    required_recorded_response_values: tuple[str, ...] = ()
                    if protocol_probe.response_record_id is not None:
                        required_recorded_response_values = tuple(
                            fixture_record_values.get(
                                protocol_probe.response_record_id,
                                (),
                            )
                        )
                        if not required_recorded_response_values:
                            raise ReplayServiceProtocolError(
                                "framework-bound response record is missing from "
                                "the immutable replay sidecar",
                                code="framework_response_record_missing",
                                details={
                                    "response_record_id": (
                                        protocol_probe.response_record_id
                                    ),
                                },
                            )
                    elif require_recorded_response:
                        required_recorded_response_values = tuple(
                            value
                            for operation in recorded_probe_operations
                            for value in fixture_operation_values.get(operation, ())
                        )
                        if not required_recorded_response_values:
                            raise ReplayServiceProtocolError(
                                "recorded response context is missing for required "
                                "probe operation"
                            )
                    elif (
                        service.transport == "skill_runtime"
                        and protocol_probe.kind == "http"
                        and fixture_operation_values
                    ):
                        # A compiler's response_contains is only a bounded
                        # fixture-derived assertion leaf. For a skill runtime,
                        # the framework-owned sidecar is the authoritative
                        # response contract. Default an operation-less HTTP
                        # data-plane probe to the first recorded operation,
                        # matching the runtime's deterministic initial cursor.
                        required_recorded_response_values = next(
                            iter(fixture_operation_values.values())
                        )
                    effective_response_contains = protocol_probe.response_contains
                    if (
                        protocol_probe.kind == "http"
                        and required_recorded_response_values
                    ):
                        effective_response_contains = None
                    await _wait_for_replay_service(
                        process,
                        host="127.0.0.1",
                        port=port,
                        kind=protocol_probe.kind,
                        path=protocol_probe.path,
                        timeout_seconds=protocol_probe.timeout_seconds,
                        phase="protocol_probe",
                        service_id=service.service_id,
                        transport=service.transport,
                        validate_advertised_websockets=(
                            protocol_probe.validate_advertised_websockets
                        ),
                        request_text=protocol_probe.request_text,
                        response_contains=effective_response_contains,
                        require_nonempty_correlated_response=(
                            require_nonempty_correlated_response
                        ),
                        required_recorded_response_values=(
                            required_recorded_response_values
                        ),
                        diagnostic_recorded_response_values=(
                            diagnostic_recorded_response_values
                        ),
                    )
                if service.transport == "skill_runtime":
                    protocol_trace = (
                        service_scratch / _REPLAY_SERVICE_PROTOCOL_TRACE_NAME
                    )
                    await _wait_for_replay_service_protocol_trace(
                        process,
                        protocol_trace,
                    )
                    _reset_replay_service_protocol_trace(protocol_trace)
            except Exception as exc:
                if isinstance(exc, ReplayServiceProtocolError):
                    exc = ReplayServiceProtocolError(
                        str(exc),
                        code=exc.code or "protocol_trace_contract_failed",
                        details={
                            **exc.details,
                            "service_id": service.service_id,
                            "transport": service.transport,
                            "runtime_artifact_constraints": [
                                _protocol_trace_runtime_artifact_constraint()
                            ],
                        },
                    )
                raise _replay_service_failure_with_stderr(
                    exc,
                    stderr_path=stderr_path,
                ) from exc
            task_entry_path = service.task_entry_path or "/"
            task_endpoint = (
                declared_binding.endpoint.rstrip("/")
                if declared_binding is not None
                else (
                    endpoint
                    if task_entry_path == "/"
                    else endpoint + task_entry_path
                )
            )
            endpoints[service.service_id] = task_endpoint
            environment[
                "AWORLD_REPLAY_ENDPOINT_"
                + re.sub(r"[^A-Za-z0-9]+", "_", service.service_id).strip("_").upper()
            ] = task_endpoint
    except Exception:
        await session.stop()
        raise
    session.endpoints = endpoints
    session.environment = environment
    return session


def _write_replay_service_launch_diagnostic(
    path: Path,
    *,
    service: ReplayServiceSpec,
    command: Sequence[str],
    environment_keys: Sequence[str],
    host: str,
    port: int,
    status: str,
    started_at: float,
    process_id: int | None = None,
    error_type: str | None = None,
) -> None:
    """Persist a bounded, payload-free service launch record for failed runs."""

    raw_command = json.dumps(
        list(command),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    payload = {
        "schema_version": "aworld.replay.service_launch_diagnostic.v1",
        "service_id": service.service_id,
        "requirement_id": service.requirement_id,
        "transport": service.transport,
        "runtime_entrypoint": service.runtime_entrypoint,
        "status": status,
        "started_at": started_at,
        "host": host,
        "port": port,
        "process_id": process_id,
        "error_type": error_type,
        "command_fingerprint": (
            "sha256:" + hashlib.sha256(raw_command.encode("utf-8")).hexdigest()
        ),
        "command": [
            sanitize_text(argument, max_chars=4096)
            for argument in list(command)[:32]
        ],
        "command_truncated": len(command) > 32,
        "environment_keys": sorted(set(environment_keys)),
        "readiness": {
            "kind": service.readiness.kind,
            "path": service.readiness.path,
            "timeout_seconds": service.readiness.timeout_seconds,
        },
        "protocol_probe_count": len(service.protocol_probes),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        # Launch correctness must not depend on best-effort diagnostics.
        return


async def preflight_frozen_replay_capability(
    capability: FrozenReplayCapability,
    *,
    artifact_dir: str | Path,
    required_nonempty_probe_operations: Sequence[str] = (),
    required_recorded_probe_operations: Sequence[str] = (),
    integrity_capability: FrozenReplayCapability | None = None,
) -> Mapping[str, str]:
    """Start a frozen capability, execute every declared probe, then stop it.

    This is the same isolated service lifecycle used by task replay, exposed as a
    bounded pre-rollout conformance check. Candidate code still runs only in the
    replay subprocess sandbox.
    """

    resolved_artifact_dir = Path(artifact_dir).expanduser().resolve()
    resolved_artifact_dir.mkdir(parents=True, exist_ok=True)
    for attempt_index in range(_SERVICE_STARTUP_RETRY_LIMIT + 1):
        attempt_dir = (
            resolved_artifact_dir
            if attempt_index == 0
            else resolved_artifact_dir / f"startup_retry_{attempt_index + 1}"
        )
        try:
            session = await _start_replay_services(
                capability,
                artifact_dir=attempt_dir,
                required_nonempty_probe_operations=(
                    required_nonempty_probe_operations
                ),
                required_recorded_probe_operations=(
                    required_recorded_probe_operations
                ),
                integrity_capability=integrity_capability,
            )
        except ReplayServiceReadinessTimeout as exc:
            if (
                exc.phase != "startup"
                or attempt_index >= _SERVICE_STARTUP_RETRY_LIMIT
            ):
                raise
            logger.info(
                "self_evolve.replay.capability_preflight_startup_retry "
                f"capability_id={capability.capability_id} "
                f"attempt={attempt_index + 2}"
            )
            continue
        try:
            return dict(session.endpoints)
        finally:
            await session.stop()
    raise RuntimeError("replay capability preflight retry loop was exhausted")


def _replay_capability_fixture_summaries(
    capability: FrozenReplayCapability,
) -> list[dict[str, object]]:
    """Describe frozen fixture shapes without exposing their payload content."""

    fixture_root = (
        Path(capability.frozen_root).expanduser().resolve() / "fixtures"
    ).resolve()
    summaries: list[dict[str, object]] = []
    for service in capability.services[:16]:
        try:
            fixture_path = (fixture_root / service.response_fixture).resolve(
                strict=True
            )
            if (
                not fixture_path.is_relative_to(fixture_root)
                or not fixture_path.is_file()
                or fixture_path.is_symlink()
            ):
                continue
            fixture_bytes = fixture_path.stat().st_size
            if fixture_bytes > 2 * 1024 * 1024:
                root_type = "oversized"
            else:
                try:
                    value = json.loads(fixture_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    root_type = "non_json"
                else:
                    if isinstance(value, Mapping):
                        root_type = "object"
                    elif isinstance(value, list):
                        root_type = "array"
                    elif isinstance(value, str):
                        root_type = "string"
                    elif isinstance(value, bool):
                        root_type = "boolean"
                    elif value is None:
                        root_type = "null"
                    elif isinstance(value, (int, float)):
                        root_type = "number"
                    else:  # pragma: no cover - json.loads exhausts JSON roots
                        root_type = "unknown"
            summaries.append(
                {
                    "service_id": service.service_id,
                    "fixture_bytes": fixture_bytes,
                    "json_root_type": root_type,
                }
            )
        except OSError:
            continue
    return summaries


def replay_capability_fixture_summaries(
    capability: FrozenReplayCapability,
) -> list[dict[str, object]]:
    """Return bounded, payload-free fixture shape evidence for repair feedback."""

    return _replay_capability_fixture_summaries(capability)


def replay_capability_fixture_leaf_values(
    capability: FrozenReplayCapability,
) -> dict[str, tuple[str, ...]]:
    """Read bounded scalar values from arbitrarily nested frozen fixtures.

    Values are used only by repair conformance to prove that a declared task-plane
    probe returns recorded content rather than an object key, placeholder, or empty
    schema. They are never included in diagnostics or persisted separately.
    """

    collected, _ = _replay_capability_fixture_value_evidence(capability)
    return collected


def replay_capability_fixture_response_leaf_values(
    capability: FrozenReplayCapability,
) -> dict[str, tuple[str, ...]]:
    """Return scalar values proven to originate in recorded output contexts.

    Trajectory context fixtures often wrap tool outputs in ``action_result`` or
    ``tool_outputs`` containers and encode the actual response as a JSON string.
    This generic extractor follows those output containers and recursively
    decodes bounded JSON string layers without knowing the external protocol.
    """

    _, response_values = _replay_capability_fixture_value_evidence(capability)
    return {
        path: values
        for path, values in response_values.items()
        if values
    }


def _replay_capability_recorded_response_values(
    capability: FrozenReplayCapability,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Read bounded strict-probe expectations from operation-indexed sidecars.

    The first non-empty record for an operation is the response a fresh replay
    runtime's deterministic per-operation cursor must serve. Keeping the values
    grouped by operation prevents unrelated fixture outputs from satisfying (or
    over-constraining) a task-plane probe.
    """

    fixture_root = (
        Path(capability.frozen_root).expanduser().resolve() / "fixtures"
    ).resolve()
    collected: dict[str, dict[str, tuple[str, ...]]] = {}
    for service in capability.services[:16]:
        relative = service.response_fixture
        if relative in collected:
            continue
        try:
            fixture_path = (fixture_root / relative).resolve(strict=True)
            if (
                not fixture_path.is_relative_to(fixture_root)
                or not fixture_path.is_file()
                or fixture_path.is_symlink()
            ):
                continue
            sidecar_path = fixture_path.with_suffix(".responses.json")
            if (
                not sidecar_path.is_file()
                or sidecar_path.is_symlink()
                or sidecar_path.stat().st_size > 8 * 1024 * 1024
            ):
                continue
            index = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(index, Mapping):
            continue
        records = index.get("records")
        if not isinstance(records, list):
            continue
        operation_values: dict[str, tuple[str, ...]] = {}
        for record in records[:4096]:
            if not isinstance(record, Mapping) or record.get("non_empty") is not True:
                continue
            operation = record.get("operation")
            if (
                not isinstance(operation, str)
                or not operation.strip()
                or operation in operation_values
                or "value" not in record
            ):
                continue
            values = _recorded_response_value_probe_values(record.get("value"))
            if values:
                operation_values[operation] = values
        if operation_values:
            collected[relative] = operation_values
    return collected


def _replay_capability_recorded_response_records(
    capability: FrozenReplayCapability,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Return probe values keyed by the framework's stable response record id."""

    fixture_root = (
        Path(capability.frozen_root).expanduser().resolve() / "fixtures"
    ).resolve()
    collected: dict[str, dict[str, tuple[str, ...]]] = {}
    for service in capability.services[:16]:
        relative = service.response_fixture
        if relative in collected:
            continue
        try:
            fixture_path = (fixture_root / relative).resolve(strict=True)
            if (
                not fixture_path.is_relative_to(fixture_root)
                or not fixture_path.is_file()
                or fixture_path.is_symlink()
            ):
                continue
            sidecar_path = fixture_path.with_suffix(".responses.json")
            if (
                not sidecar_path.is_file()
                or sidecar_path.is_symlink()
                or sidecar_path.stat().st_size > 8 * 1024 * 1024
            ):
                continue
            index = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        records = index.get("records") if isinstance(index, Mapping) else None
        if not isinstance(records, list):
            continue
        record_values: dict[str, tuple[str, ...]] = {}
        for record in records[:4096]:
            if (
                not isinstance(record, Mapping)
                or record.get("non_empty") is not True
                or "value" not in record
            ):
                continue
            record_id = record.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                continue
            values = _recorded_response_value_probe_values(record.get("value"))
            if values:
                record_values[record_id] = values
        if record_values:
            collected[relative] = record_values
    return collected


def _recorded_response_value_probe_values(value: Any) -> tuple[str, ...]:
    """Return one container assertion plus bounded descendant scalar assertions."""

    selected: list[str] = []

    def append(text: str) -> None:
        normalized = text.strip()
        if (
            normalized
            and len(normalized) <= 4096
            and normalized not in selected
            and len(selected) < 512
        ):
            selected.append(normalized)

    pending: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    recorded_container = False
    while pending and visited < 4096 and len(selected) < 512:
        current, decoded_depth = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            if not recorded_container:
                encoded = json.dumps(
                    current,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                append(encoded)
                recorded_container = True
            pending.extend(
                (nested, decoded_depth)
                for nested in reversed(list(current.values())[:512])
            )
            continue
        if isinstance(current, (list, tuple)):
            if not recorded_container:
                encoded = json.dumps(
                    current,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                append(encoded)
                recorded_container = True
            pending.extend(
                (nested, decoded_depth)
                for nested in reversed(list(current)[:512])
            )
            continue
        if isinstance(current, str):
            stripped = current.strip()
            if (
                decoded_depth < 4
                and stripped[:1] in {"{", "["}
                and len(stripped) <= 64 * 1024
            ):
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, (Mapping, list)):
                    pending.append((decoded, decoded_depth + 1))
                    continue
            append(stripped)
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            append(json.dumps(current, ensure_ascii=False))
    return tuple(selected)


_RECORDED_RESPONSE_CONTAINER_KEYS = frozenset(
    {
        "action_result",
        "output",
        "outputs",
        "response",
        "responses",
        "result",
        "results",
        "tool_outputs",
    }
)
_TRAJECTORY_RESPONSE_PAYLOAD_KEYS = frozenset(
    {
        "body",
        "content",
        "data",
        "output",
        "outputs",
        "response",
        "responses",
        "result",
        "results",
    }
)
_TRAJECTORY_RESPONSE_METADATA_KEYS = frozenset(
    {
        "call_id",
        "duration",
        "error",
        "id",
        "is_done",
        "name",
        "role",
        "success",
        "session_id",
        "sessionid",
        "status",
        "timestamp",
        "tool_call_id",
        "tool_name",
        "type",
    }
)
_TRAJECTORY_RECORD_KEYS = frozenset(
    {"action", "meta", "reward", "state"}
)


def _replay_capability_fixture_value_evidence(
    capability: FrozenReplayCapability,
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
]:
    fixture_root = (
        Path(capability.frozen_root).expanduser().resolve() / "fixtures"
    ).resolve()
    collected: dict[str, tuple[str, ...]] = {}
    response_collected: dict[str, tuple[str, ...]] = {}
    for service in capability.services[:16]:
        relative = service.response_fixture
        if relative in collected:
            continue
        try:
            fixture_path = (fixture_root / relative).resolve(strict=True)
            if (
                not fixture_path.is_relative_to(fixture_root)
                or not fixture_path.is_file()
                or fixture_path.is_symlink()
                or fixture_path.stat().st_size > 2 * 1024 * 1024
            ):
                continue
            raw_text = fixture_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        roots: list[object] = []
        try:
            roots.append(json.loads(raw_text))
        except json.JSONDecodeError:
            for line in raw_text.splitlines()[:4096]:
                if not line.strip():
                    continue
                try:
                    roots.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        # A trajectory context is not required to expose ``action``, ``state`` or
        # other envelope keys at its root.  Recorded snapshots are frequently
        # nested below a task/context wrapper (and may be JSON-encoded more than
        # once), so discover response gateways independently before selecting
        # scalar leaves.  Without this pass an ``action_result`` nested below a
        # single ``state`` key is treated as a generic response container and
        # metadata such as tool names and success flags leaks into the recorded
        # value catalog.
        trajectory_envelope = _looks_like_trajectory_context(roots) or (
            _contains_trajectory_response_gateway(roots)
        )
        values: list[str] = []
        response_values: list[str] = []
        seen: set[str] = set()
        response_seen: set[str] = set()
        pending: list[tuple[object, int, int, int]] = [
            (root, 0, 0, 0) for root in reversed(roots[:4096])
        ]
        visited = 0
        while (
            pending
            and visited < 100_000
            and (len(values) < 4096 or len(response_values) < 4096)
        ):
            value, depth, response_stage, decoded_depth = pending.pop()
            visited += 1
            if isinstance(value, Mapping):
                items = list(value.items())[:4096]
                payload_keys_present = (
                    response_stage == 3
                    and any(
                        str(item_key).strip().casefold()
                        in _TRAJECTORY_RESPONSE_PAYLOAD_KEYS
                        for item_key, _ in items
                    )
                )
                for key, nested in reversed(items):
                    normalized_key = str(key).strip().casefold()
                    # Once a trajectory gateway's payload key (usually
                    # ``content``) contains an encoded protocol envelope, keep
                    # metadata siblings such as ``type``, ``success`` and
                    # ``is_done`` out of the response catalog.  If the decoded
                    # object has no recognized payload key of its own, it is
                    # already the recorded payload container and arbitrary
                    # descendants remain eligible.
                    if (
                        trajectory_envelope
                        and response_stage >= 1
                        and normalized_key in _TRAJECTORY_RESPONSE_METADATA_KEYS
                    ):
                        continue
                    if payload_keys_present and (
                        normalized_key not in _TRAJECTORY_RESPONSE_PAYLOAD_KEYS
                    ):
                        continue
                    nested_response_stage = response_stage
                    if trajectory_envelope:
                        if normalized_key == "tool_outputs":
                            # ``tool_outputs`` is a trajectory gateway just like
                            # ``action_result``.  Entering it is not sufficient
                            # evidence that every nested scalar is response data:
                            # tool names, call ids and success flags commonly sit
                            # beside the actual ``content``/``response`` payload.
                            # Keep the traversal in gateway phase until a known
                            # payload key is reached.
                            nested_response_stage = max(response_stage, 1)
                        elif normalized_key == "action_result":
                            nested_response_stage = max(response_stage, 1)
                        elif (
                            response_stage == 1
                            and normalized_key
                            in _TRAJECTORY_RESPONSE_PAYLOAD_KEYS
                        ):
                            # Stage 3 denotes a payload container reached via a
                            # trajectory gateway.  It enables the metadata
                            # filtering pass above while retaining arbitrary
                            # recorded fields when no nested payload key exists.
                            nested_response_stage = 3
                    elif normalized_key in _RECORDED_RESPONSE_CONTAINER_KEYS:
                        nested_response_stage = 2
                    pending.append(
                        (
                            nested,
                            depth + 1,
                            nested_response_stage,
                            decoded_depth,
                        )
                    )
                continue
            if isinstance(value, list):
                pending.extend(
                    (nested, depth + 1, response_stage, decoded_depth)
                    for nested in reversed(value[:4096])
                )
                continue
            if isinstance(value, str):
                stripped = value.strip()
                if (
                    decoded_depth < 4
                    and stripped[:1] in {"{", "["}
                    and len(stripped) <= 2 * 1024 * 1024
                ):
                    try:
                        decoded = json.loads(stripped)
                    except json.JSONDecodeError:
                        decoded = None
                    if isinstance(decoded, (Mapping, list)):
                        encoded_container = stripped[:4096]
                        if (
                            response_stage >= 2
                            and encoded_container
                            and encoded_container not in response_seen
                            and len(response_values) < 4096
                        ):
                            response_seen.add(encoded_container)
                            response_values.append(encoded_container)
                        pending.append(
                            (
                                decoded,
                                depth + 1,
                                response_stage,
                                decoded_depth + 1,
                            )
                        )
                        continue
                normalized = stripped[:4096]
            elif isinstance(value, bool):
                normalized = "true" if value else "false"
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                normalized = json.dumps(value, ensure_ascii=False)
            else:
                normalized = ""
            if (
                len(values) < 4096
                and normalized
                and normalized not in seen
            ):
                seen.add(normalized)
                values.append(normalized)
            if (
                response_stage >= 2
                and len(response_values) < 4096
                and normalized
                and normalized not in response_seen
            ):
                response_seen.add(normalized)
                response_values.append(normalized)
        collected[relative] = tuple(values)
        response_collected[relative] = tuple(response_values)
    return collected, response_collected


def _looks_like_trajectory_context(roots: Sequence[object]) -> bool:
    candidates: list[object] = []
    for root in roots[:64]:
        if isinstance(root, Mapping):
            candidates.append(root)
        elif isinstance(root, list):
            candidates.extend(root[:64])
    for candidate in candidates[:256]:
        if not isinstance(candidate, Mapping):
            continue
        keys = {str(key).strip().casefold() for key in candidate.keys()}
        if len(keys & _TRAJECTORY_RECORD_KEYS) >= 2:
            return True
    return False


def _contains_trajectory_response_gateway(roots: Sequence[object]) -> bool:
    """Return whether arbitrary nested fixture data contains an output gateway.

    ``_looks_like_trajectory_context`` intentionally checks the conventional
    top-level trajectory keys, but dataset fixtures can wrap those records in
    arbitrary context objects or encode them as JSON strings.  This bounded
    discovery pass only records gateway presence; payload scalar selection is
    still performed by the second traversal in
    ``_replay_capability_fixture_value_evidence``.
    """

    gateway_keys = {"action_result", "tool_outputs"}
    pending: list[tuple[object, int, int]] = [
        (root, 0, 0) for root in reversed(roots[:4096])
    ]
    visited = 0
    while pending and visited < 100_000:
        value, depth, decoded_depth = pending.pop()
        visited += 1
        if isinstance(value, Mapping):
            for key, nested in list(value.items())[:4096]:
                if str(key).strip().casefold() in gateway_keys:
                    return True
                pending.append((nested, depth + 1, decoded_depth))
            continue
        if isinstance(value, (list, tuple)):
            pending.extend(
                (nested, depth + 1, decoded_depth)
                for nested in reversed(value[:4096])
            )
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if (
                decoded_depth < 4
                and stripped[:1] in {"{", "["}
                and len(stripped) <= 2 * 1024 * 1024
            ):
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, (Mapping, list)):
                    pending.append((decoded, depth + 1, decoded_depth + 1))
    return False


def _replay_service_failure_with_stderr(
    exc: Exception,
    *,
    stderr_path: Path,
) -> Exception:
    try:
        stderr = stderr_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return exc
    stderr = sanitize_text(stderr[-2_000:], max_chars=1_200)
    if not stderr:
        return exc
    message = f"{exc}; service stderr: {stderr}"
    if isinstance(exc, ReplayServiceProtocolError):
        # Stderr enrichment is a presentation concern and must not erase the
        # validator's executable diagnostic contract.  Preflight consumes the
        # structured code/details to merge newly observed schema constraints
        # into causal feedback for the next repair candidate.
        return ReplayServiceProtocolError(
            message,
            code=exc.code,
            details=exc.details,
        )
    if isinstance(exc, ReplayServiceReadinessTimeout):
        return ReplayServiceReadinessTimeout(
            message,
            phase=exc.phase,
            timeout_seconds=exc.timeout_seconds,
            service_id=exc.service_id,
            transport=exc.transport,
            last_error_type=exc.last_error_type,
            last_error_errno=exc.last_error_errno,
            process_returncode=exc.process_returncode,
        )
    if isinstance(exc, ReplayServiceProcessExitedError):
        return ReplayServiceProcessExitedError(
            message,
            phase=exc.phase,
            service_id=exc.service_id,
            transport=exc.transport,
            process_returncode=exc.process_returncode,
        )
    if isinstance(exc, TimeoutError):
        return TimeoutError(message)
    if isinstance(exc, RuntimeError):
        return RuntimeError(message)
    return ReplayServiceProtocolError(message)


async def _monitor_replay_service_disk(
    session: _ReplayServiceSession,
    *,
    max_bytes: int,
) -> None:
    while True:
        await asyncio.sleep(0.02)
        if _directory_size_bytes(session.private_root) <= max_bytes:
            memory_exceeded = any(
                replay_process_memory_bytes(item.process.pid) > 512 * 1024 * 1024
                for item in session.processes
                if item.process.poll() is None
            )
            if not memory_exceeded:
                continue
            session.disk_limit_error = "replay service exceeded memory limit"
        else:
            session.disk_limit_error = "replay service exceeded total disk limit"
        for item in session.processes:
            if item.process.poll() is not None:
                continue
            with contextlib.suppress(ProcessLookupError):
                if os.name == "posix":
                    os.killpg(item.process.pid, signal.SIGKILL)
                else:
                    item.process.kill()
        return


def _directory_size_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ReplayServiceReadinessTimeout(TimeoutError):
    """Typed readiness timeout whose phase determines causal ownership."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        timeout_seconds: float,
        service_id: str | None,
        transport: str | None,
        last_error_type: str | None,
        last_error_errno: int | None,
        process_returncode: int | None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.timeout_seconds = timeout_seconds
        self.service_id = service_id
        self.transport = transport
        self.last_error_type = last_error_type
        self.last_error_errno = last_error_errno
        self.process_returncode = process_returncode

    def diagnostics(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "phase": self.phase,
                "timeout_seconds": self.timeout_seconds,
                "service_id": self.service_id,
                "transport": self.transport,
                "last_error_type": self.last_error_type,
                "last_error_errno": self.last_error_errno,
                "process_returncode": self.process_returncode,
            }.items()
            if value is not None
        }


class ReplayServiceProcessExitedError(RuntimeError):
    """Typed early service exit with enough context for causal ownership."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        service_id: str | None,
        transport: str | None,
        process_returncode: int | None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.service_id = service_id
        self.transport = transport
        self.process_returncode = process_returncode

    def diagnostics(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "phase": self.phase,
                "service_id": self.service_id,
                "transport": self.transport,
                "process_returncode": self.process_returncode,
            }.items()
            if value is not None
        }


def _replay_service_start_failure_details(
    exc: Exception,
    *,
    replay_capability: FrozenReplayCapability,
) -> dict[str, Any]:
    has_candidate_runtime = any(
        service.transport == "skill_runtime"
        for service in replay_capability.services
    )
    diagnostics: dict[str, Any] = {}
    if isinstance(exc, ReplayServiceReadinessTimeout):
        diagnostics.update(exc.diagnostics())
        candidate_owned = (
            exc.phase == "protocol_probe"
            and exc.transport == "skill_runtime"
        )
        code = (
            "replay_service_protocol_probe_timeout"
            if candidate_owned
            else "replay_service_startup_timeout"
        )
        category = (
            "replay_service_protocol"
            if candidate_owned
            else "replay_service_startup"
        )
        repairable = True
    elif isinstance(exc, ReplayServiceProtocolError):
        transport = exc.details.get("transport")
        candidate_owned = (
            transport == "skill_runtime"
            if isinstance(transport, str)
            else has_candidate_runtime
        )
        code = exc.code or "replay_service_protocol_error"
        category = "replay_service_protocol"
        repairable = True
        diagnostics.update(exc.details)
    elif isinstance(exc, ReplayServiceProcessExitedError):
        diagnostics.update(exc.diagnostics())
        candidate_owned = exc.transport == "skill_runtime"
        code = (
            "replay_service_candidate_runtime_exited"
            if candidate_owned
            else "replay_service_startup_process_exited"
        )
        category = "replay_service_startup"
        repairable = True
    elif isinstance(exc, TimeoutError):
        # Untyped timeouts cannot prove a candidate defect. Keep them on the
        # system side so prose or exception inheritance cannot stop a campaign.
        candidate_owned = False
        code = "replay_service_startup_timeout"
        category = "replay_service_startup"
        repairable = True
    else:
        # Generic exceptions do not carry executable evidence that candidate
        # content caused the failure. Candidate ownership is reserved for the
        # typed protocol and runtime-exit events above.
        candidate_owned = False
        code = "replay_service_infrastructure_failed"
        category = "replay_service_preflight"
        repairable = False
    details: dict[str, Any] = {
        "type": type(exc).__name__,
        "code": code,
        "reason": str(exc),
        "outcome": (
            "candidate_failure"
            if candidate_owned
            else "infrastructure_failure"
        ),
        "failure_stage": FailureStage.CAPABILITY_PREFLIGHT.value,
        "repairable": repairable,
        "category": category,
    }
    if diagnostics:
        details["diagnostics"] = diagnostics
    return details


async def _wait_for_replay_service(
    process: subprocess.Popen[Any],
    *,
    host: str,
    port: int,
    kind: str,
    path: str,
    timeout_seconds: float,
    phase: str = "startup",
    service_id: str | None = None,
    transport: str | None = None,
    validate_advertised_websockets: bool = False,
    request_text: str | None = None,
    response_contains: str | None = None,
    require_nonempty_correlated_response: bool = False,
    required_recorded_response_values: Sequence[str] = (),
    diagnostic_recorded_response_values: Sequence[str] = (),
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ReplayServiceProcessExitedError(
                f"replay service exited before readiness (exit={process.returncode})",
                phase=phase,
                service_id=service_id,
                transport=transport,
                process_returncode=process.returncode,
            )
        try:
            await asyncio.to_thread(
                _probe_replay_service,
                host,
                port,
                kind,
                path,
                validate_advertised_websockets=validate_advertised_websockets,
                request_text=request_text,
                response_contains=response_contains,
                require_nonempty_correlated_response=(
                    require_nonempty_correlated_response
                ),
                required_recorded_response_values=(
                    required_recorded_response_values
                ),
                diagnostic_recorded_response_values=(
                    diagnostic_recorded_response_values
                ),
            )
            return
        except OSError as exc:
            last_error = exc
            if isinstance(exc, ReplayServiceProtocolError):
                exc.details.update(
                    {
                        "probe_phase": phase,
                        "service_id": service_id,
                        "transport": transport,
                    }
                )
                if phase == "protocol_probe":
                    if (
                        transport == "skill_runtime"
                        and exc.code == "replay_service_http_status_mismatch"
                    ):
                        exc.details["runtime_route_constraints"] = [
                            {
                                "schema_version": (
                                    "aworld.self_evolve.runtime_route_constraint.v1"
                                ),
                                "constraint_kind": (
                                    "framework_bound_task_entry_route"
                                ),
                                "transport": "skill_runtime",
                                "probe_kind": "http",
                                "path_source": "requirement_identifier_path",
                                "required_status_class": "2xx",
                                "routing_behavior": (
                                    "serve_framework_bound_path"
                                ),
                            }
                        ]
                    raise
            await asyncio.sleep(0.02)
    raise ReplayServiceReadinessTimeout(
        f"replay service readiness timed out after {timeout_seconds}s: {last_error}",
        phase=phase,
        timeout_seconds=timeout_seconds,
        service_id=service_id,
        transport=transport,
        last_error_type=(type(last_error).__name__ if last_error is not None else None),
        last_error_errno=getattr(last_error, "errno", None),
        process_returncode=process.poll(),
    )


class ReplayServiceProtocolError(OSError):
    """Candidate-owned protocol error with optional payload-free diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _probe_replay_service(
    host: str,
    port: int,
    kind: str,
    path: str,
    *,
    validate_advertised_websockets: bool = False,
    request_text: str | None = None,
    response_contains: str | None = None,
    require_nonempty_correlated_response: bool = False,
    required_recorded_response_values: Sequence[str] = (),
    diagnostic_recorded_response_values: Sequence[str] = (),
) -> None:
    if kind == "websocket":
        _probe_websocket_handshake(
            host,
            port,
            path,
            query="",
            request_text=request_text,
            response_contains=response_contains,
            require_nonempty_correlated_response=(
                require_nonempty_correlated_response
            ),
            required_recorded_response_values=(
                required_recorded_response_values
            ),
            diagnostic_recorded_response_values=(
                diagnostic_recorded_response_values
            ),
        )
        return
    response = b""
    with socket.create_connection((host, port), timeout=0.25) as connection:
        if kind == "http":
            connection.sendall(
                (
                    f"GET {path} HTTP/1.0\r\n"
                    f"Host: {host}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
            )
            response = _bounded_socket_response(connection, max_bytes=64 * 1024)
            if not response.startswith(b"HTTP/"):
                raise ReplayServiceProtocolError(
                    "HTTP replay probe returned an invalid response",
                    code="replay_service_http_response_invalid",
                    details={
                        "probe_kind": "http",
                        "probe_path": sanitize_text(path, max_chars=160),
                    },
                )
            status_line = response.split(b"\r\n", 1)[0]
            status_parts = status_line.split(b" ", 2)
            try:
                observed_status = int(status_parts[1])
            except (IndexError, ValueError) as exc:
                raise ReplayServiceProtocolError(
                    "HTTP replay probe returned an invalid status line",
                    code="replay_service_http_response_invalid",
                    details={
                        "probe_kind": "http",
                        "probe_path": sanitize_text(path, max_chars=160),
                    },
                ) from exc
            if not 200 <= observed_status < 300:
                raise ReplayServiceProtocolError(
                    (
                        "HTTP replay probe returned status "
                        f"{observed_status}; expected 2xx"
                    ),
                    code="replay_service_http_status_mismatch",
                    details={
                        "probe_kind": "http",
                        "probe_path": sanitize_text(path, max_chars=160),
                        "observed_http_status": observed_status,
                        "required_http_status_class": "2xx",
                    },
                )
        elif kind == "tcp" and request_text is not None:
            connection.sendall(request_text.encode("utf-8"))
            response = _bounded_protocol_response(
                connection,
                max_bytes=64 * 1024,
                expected=(
                    response_contains.encode("utf-8")
                    if response_contains is not None
                    else None
                ),
            )
            if require_nonempty_correlated_response:
                _validate_nonempty_correlated_json_response(
                    request_text=request_text,
                    response_payload=response,
                    response_contains=response_contains,
                )
    match_payload = (
        response.partition(b"\r\n\r\n")[2]
        if kind == "http" and b"\r\n\r\n" in response
        else response
    )
    if response_contains is not None and not replay_payload_contains_expected_value(
        response_contains,
        match_payload,
    ):
        raise ReplayServiceProtocolError(
            _protocol_probe_response_mismatch(
                kind=kind,
                path=path,
                expected=response_contains,
                response=response,
                diagnostic_recorded_response_values=(
                    diagnostic_recorded_response_values
                ),
            )
        )
    recorded_values = _bounded_recorded_response_probe_values(
        required_recorded_response_values
    )
    required_matches = min(2, len(recorded_values))
    observed_matches = sum(
        1
        for value in recorded_values
        if replay_payload_contains_expected_value(value, match_payload)
    )
    if required_matches and observed_matches < required_matches:
        raise _recorded_response_context_protocol_error(
            "HTTP data-plane probe must return surrounding recorded response context",
            probe_kind=kind,
            probe_path=path,
            required_matches=required_matches,
            observed_matches=observed_matches,
            response_payload=match_payload,
        )
    if kind == "http" and (
        validate_advertised_websockets or b"ws://" in response
    ):
        _probe_advertised_websockets(
            response,
            expected_host=host,
            expected_port=port,
        )


def _bounded_socket_response(
    connection: socket.socket,
    *,
    max_bytes: int,
) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size < max_bytes:
        chunk = connection.recv(min(4096, max_bytes - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        response = b"".join(chunks)
        header_block, separator, body = response.partition(b"\r\n\r\n")
        if not separator:
            continue
        header_lines = header_block.split(b"\r\n")
        if header_lines and b" 101 " in header_lines[0]:
            break
        content_length: int | None = None
        for line in header_lines[1:]:
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    content_length = None
                break
        if content_length is not None and len(body) >= content_length:
            break
    return b"".join(chunks)


def _bounded_protocol_response(
    connection: socket.socket,
    *,
    max_bytes: int,
    expected: bytes | None,
) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size < max_bytes:
        chunk = connection.recv(min(4096, max_bytes - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        response = b"".join(chunks)
        if expected is not None and expected in response:
            break
    return b"".join(chunks)


def _probe_advertised_websockets(
    response: bytes,
    *,
    expected_host: str,
    expected_port: int,
) -> None:
    _, separator, body = response.partition(b"\r\n\r\n")
    if not separator or not body:
        return
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    for websocket_url in _json_websocket_urls(payload):
        parsed = urlsplit(websocket_url)
        try:
            advertised_port = parsed.port
        except ValueError as exc:
            raise ReplayServiceProtocolError(
                "advertised WebSocket URL has an invalid port; construct it from "
                "the supplied --port integer"
            ) from exc
        if (
            parsed.scheme != "ws"
            or parsed.hostname != expected_host
            or advertised_port != expected_port
        ):
            raise ReplayServiceProtocolError(
                "advertised WebSocket escapes the allocated replay endpoint"
            )
        _probe_websocket_handshake(
            expected_host,
            expected_port,
            parsed.path or "/",
            query=parsed.query,
        )


def _json_websocket_urls(value: Any) -> tuple[str, ...]:
    urls: list[str] = []
    pending: list[Any] = [value]
    while pending and len(urls) < 16:
        current = pending.pop()
        if isinstance(current, str) and current.startswith("ws://"):
            urls.append(current)
        elif isinstance(current, Mapping):
            pending.extend(list(current.values())[:32])
        elif isinstance(current, (list, tuple)):
            pending.extend(list(current)[:32])
    return tuple(dict.fromkeys(urls))


def _probe_websocket_handshake(
    host: str,
    port: int,
    path: str,
    *,
    query: str,
    request_text: str | None = None,
    response_contains: str | None = None,
    require_nonempty_correlated_response: bool = False,
    required_recorded_response_values: Sequence[str] = (),
    diagnostic_recorded_response_values: Sequence[str] = (),
) -> None:
    request_path = path + (f"?{query}" if query else "")
    raw_key = b"aworld-replay-v1"
    websocket_key = base64.b64encode(raw_key).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1(
            (websocket_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(
                "ascii"
            )
        ).digest()
    ).decode("ascii")
    try:
        with socket.create_connection((host, port), timeout=0.5) as connection:
            connection.sendall(
                (
                    f"GET {request_path} HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {websocket_key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                ).encode("ascii")
            )
            response = _bounded_socket_response(connection, max_bytes=8 * 1024)
            _validate_websocket_handshake_response(
                response,
                expected_accept=expected_accept,
            )
            _probe_websocket_ping(connection)
            if request_text is not None:
                _probe_websocket_text_exchange(
                    connection,
                    path=request_path,
                    request_text=request_text,
                    response_contains=response_contains,
                    require_nonempty_correlated_response=(
                        require_nonempty_correlated_response
                    ),
                    required_recorded_response_values=(
                        required_recorded_response_values
                    ),
                    diagnostic_recorded_response_values=(
                        diagnostic_recorded_response_values
                    ),
                )
    except ReplayServiceProtocolError:
        raise
    except OSError as exc:
        raise ReplayServiceProtocolError(
            "advertised WebSocket handshake failed"
        ) from exc


def _validate_websocket_handshake_response(
    response: bytes,
    *,
    expected_accept: str,
) -> None:
    header_block = response.partition(b"\r\n\r\n")[0]
    header_lines = header_block.split(b"\r\n")
    headers = {
        name.strip().lower(): value.strip()
        for line in header_lines[1:]
        if b":" in line
        for name, value in [line.split(b":", 1)]
    }
    if not header_lines or not header_lines[0].startswith(b"HTTP/1.1 "):
        raise ReplayServiceProtocolError(
            "advertised WebSocket handshake requires HTTP/1.1",
            code="websocket_handshake_http_version_invalid",
            details={
                "schema_field_constraints": [
                    websocket_handshake_http_version_constraint().to_dict()
                ],
            },
        )
    if (
        re.match(br"HTTP/1\.1 101(?: |$)", header_lines[0]) is None
        or headers.get(b"sec-websocket-accept", b"").decode(
            "ascii", errors="ignore"
        )
        != expected_accept
    ):
        raise ReplayServiceProtocolError(
            "advertised WebSocket handshake failed: "
            f"response_bytes={len(response)} "
            f"response_sha256={hashlib.sha256(response).hexdigest()} "
            f"response_shape={_protocol_payload_shape(response)}"
        )


def _probe_websocket_ping(connection: socket.socket) -> None:
    payload = b"aworld-replay"
    _send_masked_websocket_frame(connection, opcode=0x9, payload=payload)
    try:
        opcode, response_payload = _read_websocket_frame(connection)
        if opcode != 0x0A:
            raise ReplayServiceProtocolError("WebSocket control frame failed")
        if response_payload != payload:
            raise ReplayServiceProtocolError("WebSocket control frame failed")
    except ReplayServiceProtocolError:
        raise
    except OSError as exc:
        raise ReplayServiceProtocolError(
            "WebSocket control frame failed"
        ) from exc


def _probe_websocket_text_exchange(
    connection: socket.socket,
    *,
    path: str,
    request_text: str,
    response_contains: str | None,
    require_nonempty_correlated_response: bool = False,
    required_recorded_response_values: Sequence[str] = (),
    diagnostic_recorded_response_values: Sequence[str] = (),
) -> None:
    _send_masked_websocket_frame(
        connection,
        opcode=0x1,
        payload=request_text.encode("utf-8"),
    )
    try:
        opcode, response_payload = _read_websocket_frame(connection)
    except ReplayServiceProtocolError:
        raise
    except OSError as exc:
        raise ReplayServiceProtocolError(
            "WebSocket data-plane frame failed"
        ) from exc
    if opcode != 0x1:
        raise ReplayServiceProtocolError("WebSocket data-plane frame failed")
    if require_nonempty_correlated_response:
        _validate_nonempty_correlated_json_response(
            request_text=request_text,
            response_payload=response_payload,
            response_contains=response_contains,
            required_recorded_response_values=(
                required_recorded_response_values
            ),
        )
    if response_contains is not None and not replay_payload_contains_expected_value(
        response_contains,
        response_payload,
    ):
        raise ReplayServiceProtocolError(
            _protocol_probe_response_mismatch(
                kind="websocket",
                path=path,
                expected=response_contains,
                response=response_payload,
                diagnostic_recorded_response_values=(
                    diagnostic_recorded_response_values
                ),
            )
        )


def _request_declares_operation(
    request_text: str | None,
    operation: str,
) -> bool:
    if not isinstance(request_text, str) or not operation:
        return False
    try:
        payload = json.loads(request_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return operation in request_text
    pending: list[Any] = [payload]
    operation_keys = {"action", "command", "method", "operation", "path", "route"}
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            for key, value in current.items():
                if str(key).lower() in operation_keys and value == operation:
                    return True
                if isinstance(value, (Mapping, list, tuple)):
                    pending.append(value)
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return False


def _validate_nonempty_correlated_json_response(
    *,
    request_text: str,
    response_payload: bytes,
    response_contains: str | None,
    required_recorded_response_values: Sequence[str] = (),
) -> None:
    """Validate a generic JSON request/result envelope for task-plane probes.

    The framework does not interpret domain operations. It only proves that a
    declared JSON request with correlation metadata receives a matching,
    non-error, non-empty result and that recorded probe content is part of that
    result rather than unrelated envelope metadata.
    """

    try:
        request = json.loads(request_text)
        response = json.loads(response_payload.decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayServiceProtocolError(
            "task-plane correlated probe requires JSON request and response envelopes"
        ) from exc
    if not isinstance(request, Mapping) or not isinstance(response, Mapping):
        raise ReplayServiceProtocolError(
            "task-plane correlated probe requires JSON object envelopes"
        )
    request_id = request.get("id")
    if request_id is None or response.get("id") != request_id:
        raise ReplayServiceProtocolError(
            "task-plane correlated probe response id does not match request id"
        )
    if response.get("error") is not None:
        raise ReplayServiceProtocolError(
            "task-plane correlated probe returned an error envelope"
        )
    result = response.get("result") if "result" in response else None
    if (
        not _nonempty_protocol_result(result)
        or not isinstance(response_contains, str)
        or not response_contains
        or not _protocol_result_contains(result, response_contains)
    ):
        raise ReplayServiceProtocolError(
            "fixture-derived content must be inside a non-empty correlated result"
        )
    recorded_values = _bounded_recorded_response_probe_values(
        required_recorded_response_values
    )
    required_matches = min(2, len(recorded_values))
    observed_matches = sum(
        1
        for value in recorded_values
        if _protocol_result_contains(result, value)
    )
    if required_matches and observed_matches < required_matches:
        serialized_result = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        raise _recorded_response_context_protocol_error(
            "task-plane probe must return surrounding recorded response context",
            probe_kind="task_plane_json",
            probe_path="/",
            required_matches=required_matches,
            observed_matches=observed_matches,
            response_payload=serialized_result,
        )


def _recorded_response_context_protocol_error(
    message: str,
    *,
    probe_kind: str,
    probe_path: str,
    required_matches: int,
    observed_matches: int,
    response_payload: bytes,
) -> ReplayServiceProtocolError:
    """Describe response-context loss without retaining recorded payload values."""

    constraint = {
        "schema_version": _RUNTIME_RESPONSE_CONSTRAINT_SCHEMA_VERSION,
        "constraint_kind": "recorded_response_context",
        "response_source": "AWORLD_REPLAY_RESPONSE_INDEX",
        "minimum_recorded_value_matches": required_matches,
        "maximum_response_bytes": _RECORDED_RESPONSE_TARGET_MAX_BYTES,
        "preserve_decoded_container": True,
        "allow_bounded_projection": True,
        "projection_minimum_scalar_descendants": required_matches,
        "probe_kind": sanitize_text(probe_kind, max_chars=40),
        "probe_path": sanitize_text(probe_path, max_chars=160),
    }
    observation = {
        "schema_version": (
            "aworld.self_evolve.runtime_response_observation.v1"
        ),
        "constraint_kind": "recorded_response_context",
        "observed_recorded_value_matches": max(0, observed_matches),
        "response_payload_bytes": len(response_payload),
        "response_shape": _protocol_payload_shape(response_payload),
    }
    return ReplayServiceProtocolError(
        message,
        code=_RECORDED_RESPONSE_CONTEXT_INCOMPLETE,
        details={
            "runtime_response_constraints": [constraint],
            "runtime_response_observation": observation,
        },
    )


def _bounded_recorded_response_probe_values(
    values: Sequence[str],
) -> tuple[str, ...]:
    selected: list[str] = []
    for value in values[:4096]:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > 4096
            or normalized in selected
        ):
            continue
        selected.append(normalized)
        if len(selected) >= 512:
            break
    return tuple(selected)


def _nonempty_protocol_result(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, list, tuple)):
        return bool(value)
    return True


def _protocol_result_contains(value: Any, expected: str) -> bool:
    expected_container: Mapping[str, Any] | list[Any] | None = None
    stripped_expected = expected.strip()
    if (
        stripped_expected[:1] in {"{", "["}
        and len(stripped_expected) <= 4096
    ):
        try:
            decoded_expected = json.loads(stripped_expected)
        except json.JSONDecodeError:
            decoded_expected = None
        if isinstance(decoded_expected, (Mapping, list)):
            expected_container = decoded_expected

    pending: list[Any] = [value]
    visited = 0
    while pending and visited < 4096:
        current = pending.pop()
        visited += 1
        if isinstance(current, str):
            if expected_container is not None and len(current) <= 64 * 1024:
                try:
                    decoded_current = json.loads(current)
                except json.JSONDecodeError:
                    decoded_current = None
                if decoded_current == expected_container:
                    return True
            if expected in current:
                return True
        elif isinstance(current, Mapping):
            if expected_container is not None and current == expected_container:
                return True
            pending.extend(list(current.values())[:512])
        elif isinstance(current, (list, tuple)):
            if expected_container is not None and list(current) == expected_container:
                return True
            pending.extend(list(current)[:512])
        elif current is not None and expected in str(current):
            return True
    return False


def _protocol_probe_response_mismatch(
    *,
    kind: str,
    path: str,
    expected: str,
    response: bytes,
    diagnostic_recorded_response_values: Sequence[str] = (),
) -> str:
    expected_bytes = expected.encode("utf-8")
    payload_bytes = (
        response.partition(b"\r\n\r\n")[2]
        if kind == "http" and b"\r\n\r\n" in response
        else response
    )
    selector_drift = _recorded_response_selector_drift(
        expected=expected,
        response=payload_bytes,
        recorded_response_values=diagnostic_recorded_response_values,
    )
    classification = (
        " classification=recorded_response_selector_drift"
        " required_change=align_compiler_runtime_recorded_response_selection"
        if selector_drift
        else ""
    )
    return (
        "protocol probe response mismatch: "
        f"kind={sanitize_text(kind, max_chars=24)} "
        f"path={sanitize_text(path, max_chars=160)} "
        "match=substring "
        f"expected_sha256={hashlib.sha256(expected_bytes).hexdigest()} "
        f"expected_bytes={len(expected_bytes)} "
        f"expected_shape={_protocol_payload_shape(expected_bytes)} "
        f"response_bytes={len(response)} "
        f"response_payload_bytes={len(payload_bytes)} "
        f"response_sha256={hashlib.sha256(payload_bytes).hexdigest()} "
        f"response_shape={_protocol_payload_shape(payload_bytes)}"
        f"{classification}"
    )


def _protocol_payload_shape(payload: bytes) -> str:
    """Return a content-free, single-token protocol payload classification."""

    if not payload:
        return "empty"
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    stripped = text.strip()
    if not stripped:
        return "utf8_whitespace"
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        return "utf8_text"
    if isinstance(decoded, Mapping):
        return "json_object"
    if isinstance(decoded, list):
        return "json_array"
    if isinstance(decoded, str):
        return "json_string"
    if isinstance(decoded, bool):
        return "json_boolean"
    if decoded is None:
        return "json_null"
    return "json_number"


def _recorded_response_selector_drift(
    *,
    expected: str,
    response: bytes,
    recorded_response_values: Sequence[str],
) -> bool:
    """Classify a probe whose two candidate-owned selectors chose differently.

    The framework does not choose a replacement assertion. It only observes
    that the runtime response contains immutable recorded-response evidence
    while the compiler-declared assertion is not part of that indexed evidence.
    This gives the next repair a precise, payload-free failure class.
    """

    values = _bounded_recorded_response_probe_values(recorded_response_values)
    if not values:
        return False
    expected_is_recorded = any(
        _protocol_probe_values_equivalent(expected, value)
        for value in values
    )
    if expected_is_recorded:
        return False
    return any(
        replay_payload_contains_expected_value(value, response)
        for value in values
    )


def _protocol_probe_values_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    try:
        decoded_left = json.loads(left)
        decoded_right = json.loads(right)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(decoded_left, (Mapping, list))
        and isinstance(decoded_right, (Mapping, list))
        and decoded_left == decoded_right
    )


def _send_masked_websocket_frame(
    connection: socket.socket,
    *,
    opcode: int,
    payload: bytes,
) -> None:
    if len(payload) > 64 * 1024:
        raise ReplayServiceProtocolError("WebSocket probe frame is too large")
    mask = b"\x13\x37\x42\x99"
    masked_payload = bytes(
        value ^ mask[index % 4]
        for index, value in enumerate(payload)
    )
    if len(payload) < 126:
        header = bytes([0x80 | opcode, 0x80 | len(payload)])
    elif len(payload) <= 0xFFFF:
        header = bytes([0x80 | opcode, 0x80 | 126]) + len(payload).to_bytes(
            2, "big"
        )
    else:
        header = bytes([0x80 | opcode, 0x80 | 127]) + len(payload).to_bytes(
            8, "big"
        )
    connection.sendall(header + mask + masked_payload)


def _read_websocket_frame(connection: socket.socket) -> tuple[int, bytes]:
    header = _recv_socket_exact(connection, 2)
    if len(header) != 2:
        raise ReplayServiceProtocolError("WebSocket frame is incomplete")
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        raw_length = _recv_socket_exact(connection, 2)
        if len(raw_length) != 2:
            raise ReplayServiceProtocolError("WebSocket frame is incomplete")
        length = int.from_bytes(raw_length, "big")
    elif length == 127:
        raw_length = _recv_socket_exact(connection, 8)
        if len(raw_length) != 8:
            raise ReplayServiceProtocolError("WebSocket frame is incomplete")
        length = int.from_bytes(raw_length, "big")
    if length > 64 * 1024:
        raise ReplayServiceProtocolError("WebSocket probe response is too large")
    if header[1] & 0x80:
        raise ReplayServiceProtocolError(
            "WebSocket server frame must not be masked"
        )
    payload = _recv_socket_exact(connection, length)
    if len(payload) != length:
        raise ReplayServiceProtocolError("WebSocket frame is incomplete")
    return opcode, payload


def _recv_socket_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = connection.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _replace_replay_endpoints(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for source, destination in replacements.items():
            result = result.replace(source, destination)
        return result
    if isinstance(value, Mapping):
        return {
            str(key): _replace_replay_endpoints(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_replay_endpoints(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_replay_endpoints(item, replacements) for item in value)
    return value


def _project_replay_capability_for_case(
    capability: FrozenReplayCapability,
    *,
    task_input: Any,
    dependency_ids: Sequence[str],
) -> FrozenReplayCapability:
    """Limit task rollout to replay services reachable from one case.

    Capability compilation is dataset-wide, but a member normally references
    only a small subset of the captured dependencies. Starting every service
    for every baseline/candidate member multiplies sandbox startup cost and can
    exhaust the campaign deadline before the candidate is exercised. Keep the
    full frozen capability as the integrity authority while projecting the
    execution surface to dependencies bound by this case or still present in
    its adapted task input.
    """

    serialized_task_input = json.dumps(
        task_input,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    bound_dependencies = {str(value) for value in dependency_ids if value}
    required_sources = {
        source
        for source in capability.endpoint_replacements
        if source in bound_dependencies or source in serialized_task_input
    }
    required_service_ids = {
        capability.endpoint_replacements[source]
        for source in required_sources
    }
    if required_service_ids == {
        service.service_id for service in capability.services
    }:
        return capability
    return replace(
        capability,
        endpoint_replacements={
            source: service_id
            for source, service_id in capability.endpoint_replacements.items()
            if source in required_sources
        },
        services=tuple(
            service
            for service in capability.services
            if service.service_id in required_service_ids
        ),
    )


def _adapter_environment(bindings: Sequence[Any]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for binding in bindings:
        for key, value in binding.environment.items():
            existing = environment.get(key)
            if existing is not None and existing != value:
                raise ValueError(f"conflicting replay adapter environment value: {key}")
            environment[key] = value
    return environment


def _select_replay_case(dataset: SelfEvolveDataset) -> EvalCase:
    for case in dataset.cases:
        if _is_replayable_user_task_case(case):
            return case
    raise ValueError(
        "candidate replay requires at least one user task eval case; "
        "framework-generated evaluation contracts are not replayable"
    )


def _is_replayable_user_task_case(case: EvalCase) -> bool:
    if _mapping_bool(case.metadata, "framework_meta_trajectory"):
        return False
    if _mapping_bool(case.source, "framework_meta_trajectory"):
        return False
    if _mapping_bool(case.source, "framework_generated"):
        return False
    if case.trace_pack is not None and is_framework_meta_trace_pack(case.trace_pack):
        return False
    return not _looks_like_framework_generated_task_input(case.input)


def _mapping_bool(value: Mapping[str, Any], key: str) -> bool:
    return value.get(key) is True


_FRAMEWORK_GENERATED_TASK_MARKERS = (
    "evaluation_runtime_contract",
    "artifact_backed_evidence",
    "do_not_call_external_tools",
    "report_output_path",
    "trajectory_log_path",
    "aworld_self_evolve_replay_artifact_dir",
    "aworld_self_evolve_evidence_manifest",
    ".aworld/self_evolve/evaluator",
)


def _looks_like_framework_generated_task_input(task_input: Any) -> bool:
    haystack = _task_text(task_input).lower()
    if not haystack:
        return False
    marker_count = sum(
        1 for marker in _FRAMEWORK_GENERATED_TASK_MARKERS if marker in haystack
    )
    if marker_count >= 2:
        return True
    return marker_count >= 1 and (
        "self-evolve" in haystack
        or "self_evolve" in haystack
        or "trajectory-evaluator" in haystack
        or "judge" in haystack
    )


def _infer_baseline_skill_root(request: CandidateReplayRequest) -> str | None:
    if request.baseline_skill_root:
        return request.baseline_skill_root
    return _infer_baseline_skill_root_from_target(request.target)


def _member_baseline_replay_dir(
    baseline_replay_dir: str | None,
    case_id: str,
) -> str | None:
    if baseline_replay_dir is None:
        return None
    root = Path(baseline_replay_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        incremental_manifest = root / "baseline_cache_manifest.json"
        if incremental_manifest.exists():
            manifest_path = incremental_manifest
    if not manifest_path.exists():
        legacy_member_dir = _legacy_member_replay_dir(root, case_id)
        if legacy_member_dir is not None:
            return str(legacy_member_dir / "baseline")
        return baseline_replay_dir
    manifest = _load_json_object(manifest_path)
    members = manifest.get("members")
    if not isinstance(members, list):
        return None
    for member in members:
        if not isinstance(member, Mapping) or member.get("case_id") != case_id:
            continue
        relative_path = member.get("path")
        if (
            isinstance(relative_path, str)
            and relative_path == _member_artifact_name(case_id)
        ):
            return _stored_member_baseline_replay_dir(
                root / relative_path,
                case_id=case_id,
            )
    return None


def _stored_member_baseline_replay_dir(
    member_root: Path,
    *,
    case_id: str,
) -> str | None:
    request_path = member_root / "request.json"
    if not request_path.exists():
        return None
    try:
        member_request = _load_json_object(request_path)
    except (ValueError, json.JSONDecodeError, OSError):
        return None
    if member_request.get("task_id") != case_id:
        return None
    requested_repetitions = _stored_requested_baseline_repetitions(member_request)

    local_baseline = member_root / "baseline"
    if _stored_replay_baseline_is_reusable(
        local_baseline,
        requested_repetitions=requested_repetitions,
    ):
        return str(local_baseline)

    raw_baseline_dir = member_request.get("baseline_replay_dir")
    if not isinstance(raw_baseline_dir, str) or not raw_baseline_dir.strip():
        return None
    baseline_dir = Path(raw_baseline_dir).expanduser()
    if not baseline_dir.is_dir():
        return None

    owner_request_path = baseline_dir.parent / "request.json"
    if not owner_request_path.exists():
        return None
    try:
        owner_request = _load_json_object(owner_request_path)
    except (ValueError, json.JSONDecodeError, OSError):
        return None
    if owner_request.get("task_id") != case_id:
        return None
    if _stored_replay_baseline_is_reusable(
        baseline_dir,
        requested_repetitions=requested_repetitions,
    ):
        return str(baseline_dir)
    return None


def _stored_requested_baseline_repetitions(
    request: Mapping[str, Any],
) -> int:
    value = request.get("baseline_repetitions", 1)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1
    return max(1, int(value))


def _stored_replay_baseline_is_reusable(
    variant_dir: Path,
    *,
    requested_repetitions: int,
) -> bool:
    if not variant_dir.is_dir():
        return False
    try:
        result = _load_variant_result_from_dir(
            variant_dir,
            base_variant_id="baseline",
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return False
    return _baseline_replay_is_reusable(
        result,
        requested_repetitions=requested_repetitions,
    )


def _legacy_member_replay_dir(root: Path, case_id: str) -> Path | None:
    for member_dir in sorted(root.iterdir()) if root.exists() else ():
        if not member_dir.is_dir():
            continue
        request_path = member_dir / "request.json"
        if not request_path.exists():
            continue
        try:
            payload = _load_json_object(request_path)
        except (ValueError, json.JSONDecodeError, OSError):
            continue
        if payload.get("task_id") == case_id:
            return member_dir
    return None


def _distributed_member_repetitions(repetitions: int, *, member_count: int) -> int:
    if member_count <= 0:
        raise ValueError("member_count must be positive")
    if repetitions <= 0:
        raise ValueError("replay repetitions must be positive")
    # Repetitions are configured per normalized replay member.  Keep the
    # historical helper name because runner-side baseline reuse imports it,
    # but never divide an explicit repetition count by trajectory cardinality.
    return repetitions


def _has_authoritative_per_member_repetitions(
    request: CandidateReplayRequest,
) -> bool:
    """Return whether a request can authorize new per-member replay work."""

    return request.repetition_semantics == _PER_MEMBER_REPETITION_SEMANTICS


def _infer_baseline_skill_root_from_target(target: SelfEvolveTargetRef) -> str | None:
    if not target.path:
        return None
    path = Path(target.path)
    if path.name.lower() != "skill.md":
        return None
    if _is_self_evolve_draft_skill_path(path):
        return None
    return str(path.parent.parent)


def _is_self_evolve_draft_skill_path(path: Path) -> bool:
    normalized_parts = tuple(part.lower() for part in path.parts)
    legacy_marker = (".aworld", "self_evolve", "drafts", "skills")
    if any(
        normalized_parts[index : index + len(legacy_marker)] == legacy_marker
        for index in range(0, len(normalized_parts) - len(legacy_marker) + 1)
    ):
        return True
    return any(
        normalized_parts[index : index + 2] == (".aworld", "self_evolve")
        and index + 5 < len(normalized_parts)
        and normalized_parts[index + 3] == "draft_target"
        and normalized_parts[index + 5] == "skill.md"
        for index in range(0, max(0, len(normalized_parts) - 5))
    )


def _task_text(task_input: Any) -> str:
    if isinstance(task_input, str):
        return task_input
    if isinstance(task_input, Mapping):
        for key in ("content", "task", "prompt", "input"):
            value = task_input.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(to_json_dict(task_input), ensure_ascii=False, sort_keys=True)
    return str(task_input)


_REPLAY_EVIDENCE_POLICY = """

Self-evolve replay evidence requirements:
- Preserve the user task and use artifact-first evidence. Save large or unknown-size output under AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR ({artifact_dir}); never stream full pages, documents, JSON, or logs.
- Inspect only explicit byte-bounded excerpts or selected fields; `head -N` is not a byte bound.
- Append one compact JSON line per source to AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST ({evidence_manifest}). Include source_id, extraction_method, bounded fields/excerpt, and artifact_path for files; use evidence_type="metadata" plus one object for non-file evidence.
- Reject compacted, truncated, invalid, or unbounded evidence; retry once with a narrower extraction.
- Persist every valid artifact-backed sample and its manifest entry immediately. Continue only until every evidence subject required by the user task is covered (for example, every item in a comparison), or until one materially different bounded attempt establishes that a subject is unavailable. Then stop collecting and return the answer with artifact paths, subject coverage counts, explicit missing subjects, and a concise claim ledger. Omit unsupported claims.
""".strip()


_REPLAY_RUNTIME_POLICY = """
Self-evolve replay runtime contract:
- Keep the original authorization boundary. Required task-plane actions are allowed; control-plane actions require explicit task authorization.
- External prerequisites are attach-only: never terminate, restart, reconfigure, replace, or copy host credentials/sessions/private state.
- Preserve supplied HOME, TMPDIR, XDG_*, and runtime roots. Use only AWORLD_REPLAY_ENDPOINT_* endpoints; never discover alternate host ports.
- On terminal protocol mismatch, persist one bounded diagnostic and stop. If a bounded non-mutating prerequisite probe fails, return prerequisite-unavailable.
- Keep created files in the workspace/artifact directory. Switch strategy once when no new evidence is possible, otherwise fail with the observed reason.
- Evidence completion is terminal: once every required evidence subject is covered, or bounded insufficiency is established for the missing subjects, make no further tool call and synthesize the final response. A single sample is terminal only for a genuinely single-subject task.
""".strip()


def _replay_task_text(
    task_text: str,
    *,
    artifact_dir: Path | None = None,
    evidence_manifest: Path | None = None,
    workspace_root: Path | None = None,
) -> str:
    task_text = _normalize_replay_workspace_paths(
        task_text,
        workspace_root=workspace_root,
    )
    artifact_dir_text = str(artifact_dir) if artifact_dir is not None else "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"
    evidence_manifest_text = (
        str(evidence_manifest)
        if evidence_manifest is not None
        else "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST"
    )
    policies: list[str] = []
    if "Self-evolve replay evidence requirements:" not in task_text:
        policies.append(
            _REPLAY_EVIDENCE_POLICY.format(
                artifact_dir=artifact_dir_text,
                evidence_manifest=evidence_manifest_text,
            )
        )
    if "Self-evolve replay runtime contract:" not in task_text:
        policies.append(_REPLAY_RUNTIME_POLICY)
    if not policies:
        return task_text
    return task_text.rstrip() + "\n\n" + "\n\n".join(policies)


def _normalize_replay_workspace_paths(
    task_text: str,
    *,
    workspace_root: Path | None,
) -> str:
    if workspace_root is None:
        return task_text
    workspace = workspace_root.expanduser().resolve()
    repo_name = workspace.name
    if not repo_name:
        return task_text
    stale_workspace_root_pattern = (
        rf"/(?:Users|home)/[^/\s]+/Documents/workspace/{re.escape(repo_name)}"
    )
    return re.sub(stale_workspace_root_pattern, str(workspace), task_text)


def _extract_trajectory_from_stdout(stdout: str) -> list[Mapping[str, Any]]:
    return _extract_trajectory_payload_from_stdout(stdout)["trajectory"]


def _extract_trajectory_payload_from_stdout(stdout: str) -> dict[str, Any]:
    # ``aworld-cli --emit-trajectory`` is a JSONL protocol framed by the ASCII
    # LF byte. Do not use ``str.splitlines()`` here: it also treats Unicode
    # separators such as U+0085 as record boundaries. Those code points can
    # legitimately occur inside a JSON string (and frequently appear in
    # mojibake from browser output), which used to split a valid multi-megabyte
    # trajectory object and silently report capture as unavailable.
    for line in reversed(stdout.split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        trajectory = payload.get("trajectory") if isinstance(payload, Mapping) else None
        if isinstance(trajectory, list):
            capture_mode = str(
                payload.get("trajectory_capture_mode") or "unknown"
            ).strip()
            return {
                "trajectory": [item for item in trajectory if isinstance(item, Mapping)],
                "trajectory_capture_mode": capture_mode or "unknown",
            }
    return {"trajectory": [], "trajectory_capture_mode": "unavailable"}


def _replay_evidence_metrics(
    *,
    stdout: str,
    stderr: str,
    trajectory: list[Mapping[str, Any]],
    artifact_dir: Path | None = None,
    evidence_manifest: Path | None = None,
    workspace_root: Path | None = None,
    variant_id: str | None = None,
    variant_role: str | None = None,
) -> dict[str, Any]:
    signal_text = "\n".join(
        text
        for text in (
            stdout,
            stderr,
            json.dumps(to_json_dict(trajectory), ensure_ascii=False),
        )
        if text
    ).lower()
    signals: list[str] = []
    compacted_markers = (
        "tool output compacted",
        "compacted for context reuse",
        "compacted_string_field",
    )
    if any(marker in signal_text for marker in compacted_markers):
        signals.append("tool_output_compacted")
    truncated_markers = (
        "truncated",
        "too large to inspect",
        "output was truncated",
    )
    if any(marker in signal_text for marker in truncated_markers):
        signals.append("tool_output_truncated")
    compacted = bool(signals)
    replay_compacted_argument_blocked = (
        REPLAY_COMPACTED_ARGUMENT_FAILURE in signal_text
    )
    manifest_metrics = _evidence_manifest_metrics(
        artifact_dir=artifact_dir,
        evidence_manifest=evidence_manifest,
        workspace_root=workspace_root,
    )
    reference_metrics = _final_answer_artifact_reference_metrics(
        trajectory=trajectory,
        artifact_dir=artifact_dir,
    )
    runtime_policy_metrics = _replay_evidence_runtime_policy_metrics(
        artifact_dir,
        owner=(
            "task"
            if variant_role == "baseline" or variant_id == "baseline"
            else "candidate"
            if variant_role == "candidate" or variant_id is not None
            else None
        ),
    )
    manifest_valid = manifest_metrics.get("evidence_manifest_valid") is True
    manifest_invalid_count = manifest_metrics.get("evidence_manifest_invalid_entry_count")
    manifest_fully_valid = manifest_valid and not (
        isinstance(manifest_invalid_count, (int, float)) and manifest_invalid_count > 0
    )
    runtime_policy_authoritative_passed = runtime_policy_metrics.get(
        "evidence_runtime_policy_authoritative_passed",
        runtime_policy_metrics.get("evidence_runtime_policy_passed", True),
    )
    metrics = {
        "evidence_compacted": compacted,
        "evidence_strategy_passed": (
            ((not compacted) or manifest_fully_valid)
            and runtime_policy_authoritative_passed
        ),
        "evidence_compaction_signals": signals,
        **manifest_metrics,
        **reference_metrics,
        **runtime_policy_metrics,
    }
    if replay_compacted_argument_blocked:
        metrics["replay_compacted_argument_blocked"] = True
    return metrics


_REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION = "aworld.replay.evidence_policy.v1"
_REPLAY_POLICY_CONTROL_FILES = frozenset(
    {
        "evidence_manifest.jsonl",
        _FRAMEWORK_CANONICAL_EVIDENCE_MANIFEST,
        "evidence_bundle.json",
        "execution_request.json",
        "framework_evidence_policy.jsonl",
        "framework_evidence_state.json",
        "metrics.json",
        "trajectory.json",
        "stdout.txt",
        "stderr.txt",
    }
)


def _initialize_replay_evidence_policy_state(
    artifact_dir: Path,
    *,
    artifact_file_limit: int,
    artifact_byte_limit: int,
    max_consecutive_failed_actions: int,
) -> None:
    payload = {
        "schema_version": _REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION,
        "enforcement": "tool_boundary",
        "phase": "collecting",
        "tool_call_attempt_count": 0,
        "manifest_entry_count": 0,
        "artifact_file_count": 0,
        "artifact_bytes": 0,
        "artifact_file_limit": artifact_file_limit,
        "artifact_byte_limit": artifact_byte_limit,
        "max_consecutive_failed_actions": max_consecutive_failed_actions,
        "consecutive_failed_action_count": 0,
    }
    _write_json(artifact_dir / "framework_evidence_state.json", payload)


def _replay_evidence_runtime_policy_metrics(
    artifact_dir: Path | None,
    *,
    owner: str | None = None,
) -> dict[str, Any]:
    if artifact_dir is None:
        return {}
    state_path = artifact_dir / "framework_evidence_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (
        not isinstance(state, Mapping)
        or state.get("schema_version")
        != _REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION
    ):
        return {}
    violations = _read_replay_evidence_policy_violations(artifact_dir)
    policy_mode = str(state.get("evidence_policy_mode") or "legacy").casefold()
    policy_authority = str(
        state.get("evidence_policy_authority")
        or ("advisory" if policy_mode == "shadow" else "authoritative")
    ).casefold()
    if policy_authority not in {"advisory", "authoritative"}:
        # Historical legacy state was enforcement-capable. Preserve that
        # behavior unless the persisted state explicitly proves shadow mode.
        policy_authority = "authoritative"
    artifact_file_count, artifact_bytes = _replay_agent_artifact_inventory(
        artifact_dir
    )
    artifact_file_limit = _positive_metric_int(
        state.get("artifact_file_limit"),
        default=AWorldCliReplayExecutor._DEFAULT_ARTIFACT_FILE_LIMIT,
    )
    artifact_byte_limit = _positive_metric_int(
        state.get("artifact_byte_limit"),
        default=AWorldCliReplayExecutor._DEFAULT_ARTIFACT_BYTE_LIMIT,
    )
    observed_codes = {
        str(item.get("code") or "")
        for item in violations
        if isinstance(item, Mapping)
    }
    if artifact_file_count > artifact_file_limit and (
        "artifact_file_limit_exhausted" not in observed_codes
    ):
        violations.append(
            {
                "code": "artifact_file_limit_exhausted",
                "phase": str(state.get("phase") or "collecting"),
                "artifact_file_count": artifact_file_count,
                "artifact_bytes": artifact_bytes,
                "artifact_file_limit": artifact_file_limit,
                "artifact_byte_limit": artifact_byte_limit,
                "tool_call_attempt_count": state.get(
                    "tool_call_attempt_count",
                    0,
                ),
                "manifest_entry_count": state.get("manifest_entry_count", 0),
                "required_transition": "reduce_collection_and_persist_evidence",
            }
        )
    if artifact_bytes > artifact_byte_limit and (
        "artifact_byte_limit_exhausted" not in observed_codes
    ):
        violations.append(
            {
                "code": "artifact_byte_limit_exhausted",
                "phase": str(state.get("phase") or "collecting"),
                "artifact_file_count": artifact_file_count,
                "artifact_bytes": artifact_bytes,
                "artifact_file_limit": artifact_file_limit,
                "artifact_byte_limit": artifact_byte_limit,
                "tool_call_attempt_count": state.get(
                    "tool_call_attempt_count",
                    0,
                ),
                "manifest_entry_count": state.get("manifest_entry_count", 0),
                "required_transition": "reduce_collection_and_persist_evidence",
            }
        )
    counterexamples = [
        _replay_policy_counterexample(
            item,
            sequence=index + 1,
            owner=owner,
        )
        for index, item in enumerate(violations[:16])
    ]
    return {
        "evidence_runtime_policy_active": True,
        "evidence_runtime_policy_passed": not counterexamples,
        "evidence_runtime_policy_mode": policy_mode,
        "evidence_runtime_policy_authority": policy_authority,
        "evidence_runtime_policy_authoritative_passed": (
            not counterexamples or policy_authority == "advisory"
        ),
        "evidence_runtime_policy_advisory_violation_count": (
            len(counterexamples) if policy_authority == "advisory" else 0
        ),
        "evidence_runtime_policy_violation_count": len(counterexamples),
        "evidence_runtime_policy_phase": str(
            state.get("phase") or "collecting"
        ),
        "evidence_runtime_policy_tool_call_attempt_count": int(
            state.get("tool_call_attempt_count") or 0
        ),
        "evidence_runtime_policy_artifact_file_count": artifact_file_count,
        "evidence_runtime_policy_artifact_bytes": artifact_bytes,
        "evidence_runtime_policy_consecutive_failed_action_count": int(
            state.get("consecutive_failed_action_count") or 0
        ),
        "evidence_runtime_policy_max_consecutive_failed_actions": (
            _positive_metric_int(
                state.get("max_consecutive_failed_actions"),
                default=(
                    AWorldCliReplayExecutor._DEFAULT_MAX_CONSECUTIVE_FAILED_ACTIONS
                ),
            )
        ),
        "evidence_runtime_policy_allowed_loopback_endpoint_count": int(
            state.get("allowed_loopback_endpoint_count") or 0
        ),
        "evidence_runtime_policy_allowed_control_action_count": int(
            state.get("allowed_control_action_count") or 0
        ),
        "replay_counterexamples": counterexamples,
    }


def _read_replay_evidence_policy_violations(
    artifact_dir: Path,
) -> list[dict[str, Any]]:
    path = artifact_dir / "framework_evidence_policy.jsonl"
    try:
        raw = path.read_bytes()[:131_072].decode("utf-8", errors="replace")
    except OSError:
        return []
    violations: list[dict[str, Any]] = []
    for line in raw.splitlines()[:32]:
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if (
            isinstance(item, Mapping)
            and item.get("schema_version")
            == _REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION
            and item.get("code")
        ):
            violations.append(dict(item))
    return violations


def _replay_agent_artifact_inventory(artifact_dir: Path) -> tuple[int, int]:
    count = 0
    total_bytes = 0
    for current_root, directories, filenames in os.walk(
        artifact_dir,
        followlinks=False,
    ):
        directories[:] = [
            name
            for name in directories[:64]
            if name != "logs" and not (Path(current_root) / name).is_symlink()
        ]
        for name in filenames[:256]:
            if name in _REPLAY_POLICY_CONTROL_FILES:
                continue
            path = Path(current_root) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            count += 1
            total_bytes += max(0, size)
            if count >= 256:
                return count, total_bytes
    return count, total_bytes


def _positive_metric_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value) if int(value) > 0 else default


def _replay_policy_counterexample(
    violation: Mapping[str, Any],
    *,
    sequence: int,
    owner: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": REPLAY_COUNTEREXAMPLE_SCHEMA_VERSION,
        "sequence": sequence,
        **({"owner": owner} if owner else {}),
        "failure_code": str(
            violation.get("code") or "replay_evidence_policy_violation"
        )[:96],
        "stage": "task_rollout",
        "state_before": str(violation.get("phase") or "collecting")[:64],
        "trigger": "tool_call",
        "tool_name": str(violation.get("tool_name") or "unknown")[:128],
        "action_name": str(violation.get("action_name") or "unknown")[:128],
        "manifest_entry_count": max(
            0, int(violation.get("manifest_entry_count") or 0)
        ),
        "artifact_file_count": max(
            0, int(violation.get("artifact_file_count") or 0)
        ),
        "artifact_bytes": max(0, int(violation.get("artifact_bytes") or 0)),
        **{
            key: max(0, int(violation.get(key) or 0))
            for key in (
                "artifact_file_limit",
                "artifact_byte_limit",
                "tool_call_attempt_count",
            )
            if violation.get(key) is not None
        },
        "required_transition": str(
            violation.get("required_transition")
            or "finalize_or_reduce_collection"
        )[:128],
        **(
            {
                "action_fingerprint": str(
                    violation.get("action_fingerprint")
                )[:80],
            }
            if violation.get("action_fingerprint")
            else {}
        ),
        **(
            {
                key: max(0, int(violation.get(key) or 0))
                for key in (
                    "consecutive_failure_count",
                    "observed_endpoint_count",
                    "undeclared_endpoint_count",
                    "control_action_count",
                )
                if violation.get(key) is not None
            }
        ),
    }


_FINAL_ANSWER_ARTIFACT_REFERENCE = re.compile(
    r"`([^`\n]{1,512})`|\[[^\]\n]{0,256}\]\(([^)\n]{1,512})\)"
)
_CANONICAL_EVIDENCE_CONTROL_FILES = frozenset(
    {"evidence_manifest.jsonl", "evidence_bundle.json"}
)


def _final_answer_artifact_reference_metrics(
    *,
    trajectory: list[Mapping[str, Any]],
    artifact_dir: Path | None,
) -> dict[str, Any]:
    """Compare bounded final-answer file references with canonical evidence.

    Only counts and content-addressed identities leave the replay boundary. Raw
    filenames and task text remain private, while the repair loop receives a
    deterministic, actionable signal instead of relying solely on judge prose.
    """

    if artifact_dir is None:
        return {}
    final_answer = _replay_final_answer(trajectory)
    if not final_answer:
        return {}
    references = _bounded_final_answer_artifact_references(final_answer)
    if not references:
        return {
            "evidence_artifact_reference_count": 0,
            "evidence_unmanifested_artifact_reference_count": 0,
        }
    manifested_paths = _canonical_bundle_artifact_paths(artifact_dir)
    manifested_names = {path.name for path in manifested_paths}
    unresolved: list[str] = []
    manifested_count = 0
    assessed_count = 0
    artifact_root = artifact_dir.resolve(strict=False)
    for reference in references:
        reference_path = Path(reference).expanduser()
        reference_name = reference_path.name
        canonical_control = reference_name in _CANONICAL_EVIDENCE_CONTROL_FILES
        referenced_path = (
            reference_path
            if reference_path.is_absolute()
            else artifact_dir / reference_path
        )
        matched = canonical_control or any(
            referenced_path.resolve(strict=False) == path
            for path in manifested_paths
        )
        if not matched and reference_name in manifested_names:
            matched = True
        artifact_local = referenced_path.resolve(strict=False).is_relative_to(
            artifact_root
        ) and referenced_path.exists()
        if not matched and not canonical_control and not artifact_local:
            # Backticked source/package paths are common in normal task output.
            # Only artifact-local or canonically manifested references belong to
            # the evidence-integrity contract.
            continue
        assessed_count += 1
        if matched:
            manifested_count += 1
        else:
            unresolved.append(reference)
    metrics: dict[str, Any] = {
        "evidence_artifact_reference_count": assessed_count,
        "evidence_manifested_artifact_reference_count": manifested_count,
        "evidence_unmanifested_artifact_reference_count": len(unresolved),
    }
    if unresolved:
        metrics["evidence_unmanifested_artifact_reference_identity_digests"] = [
            "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
            for value in unresolved[:32]
        ]
    return metrics


def _trajectory_external_tool_call_count(
    trajectory: Sequence[Mapping[str, Any]],
) -> int:
    """Count authoritative task-plane tool calls without retaining arguments.

    ``state.messages`` is a diagnostic conversation snapshot rather than the
    executed action ledger.  Some providers preserve a rejected or truncated
    tool-call proposal there even when the gateway discards it and emits a
    normal terminal text action.  Treating those proposals as executed calls
    makes a valid response-only replay impossible to attest because no tool
    evidence can exist for an action that never crossed the tool boundary.

    The signed trajectory's per-step ``action.tool_calls`` field is the
    authoritative execution intent used by the replay gateway, so only count
    calls from that field.  Repeated snapshots remain deduplicated by call id.
    """

    tool_call_ids: set[str] = set()
    anonymous_count = 0
    for step in trajectory[:256]:
        action = step.get("action")
        if not isinstance(action, Mapping):
            continue
        raw_calls = action.get("tool_calls")
        if not isinstance(raw_calls, (list, tuple)):
            continue
        for raw_call in raw_calls[:256]:
            if not isinstance(raw_call, Mapping):
                continue
            call_id = raw_call.get("id")
            if isinstance(call_id, str) and call_id:
                tool_call_ids.add(call_id)
            else:
                anonymous_count += 1
    return len(tool_call_ids) + anonymous_count


def _trajectory_task_completion_established(
    trajectory: list[Mapping[str, Any]],
    *,
    capture_mode: str,
) -> bool:
    if capture_mode != "task_response":
        return False
    last_meaningful_action: Mapping[str, Any] | None = None
    for step in trajectory:
        action = step.get("action") if isinstance(step, Mapping) else None
        if not isinstance(action, Mapping):
            continue
        content = action.get("content")
        meaningful_content = bool(
            isinstance(content, str)
            and content.strip()
            and content.strip().casefold() not in {"none", "null"}
        )
        tool_calls = action.get("tool_calls")
        has_tool_calls = bool(isinstance(tool_calls, (list, tuple)) and tool_calls)
        if meaningful_content or has_tool_calls:
            last_meaningful_action = action
    if last_meaningful_action is None:
        return False
    finished = last_meaningful_action.get("is_agent_finished")
    terminal = finished is True or (
        isinstance(finished, str)
        and finished.strip().casefold() == "true"
    )
    terminal_tool_calls = last_meaningful_action.get("tool_calls")
    return bool(
        terminal
        and not (
            isinstance(terminal_tool_calls, (list, tuple))
            and terminal_tool_calls
        )
    )


def _replay_final_answer(trajectory: list[Mapping[str, Any]]) -> str:
    fallback = ""
    terminal_answer = ""
    for step in trajectory:
        action = step.get("action") if isinstance(step, Mapping) else None
        if not isinstance(action, Mapping):
            continue
        content = action.get("content")
        if (
            not isinstance(content, str)
            or not content.strip()
            or content.strip().casefold() in {"none", "null"}
        ):
            continue
        fallback = content
        finished = action.get("is_agent_finished")
        tool_calls = action.get("tool_calls")
        if (
            finished is True
            or (
                isinstance(finished, str)
                and finished.strip().casefold() == "true"
            )
        ) and not (
            isinstance(tool_calls, (list, tuple)) and tool_calls
        ):
            terminal_answer = content
        else:
            terminal_answer = ""
    return terminal_answer or fallback


def _bounded_final_answer_artifact_references(final_answer: str) -> tuple[str, ...]:
    references: list[str] = []
    for match in _FINAL_ANSWER_ARTIFACT_REFERENCE.finditer(final_answer[:64_000]):
        raw = next((value for value in match.groups() if value), "").strip()
        if not raw or "://" in raw or raw.startswith(("#", "@")):
            continue
        path = Path(raw)
        if not path.suffix or any(char in raw for char in ("<", ">", "\0")):
            continue
        normalized = str(path)
        if normalized not in references:
            references.append(normalized)
        if len(references) >= 64:
            break
    return tuple(references)


def _canonical_bundle_artifact_paths(artifact_dir: Path) -> tuple[Path, ...]:
    bundle_path = artifact_dir / "evidence_bundle.json"
    try:
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ()
    entries = payload.get("entries") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        return ()
    paths: list[Path] = []
    for entry in entries[:256]:
        artifact_path = (
            entry.get("artifact_path")
            if isinstance(entry, Mapping)
            else None
        )
        if not isinstance(artifact_path, str) or not artifact_path.strip():
            continue
        path = Path(artifact_path).expanduser()
        if not path.is_absolute():
            path = artifact_dir / path
        paths.append(path.resolve(strict=False))
    return tuple(paths)


def _compacted_argument_replay_failure(
    metrics: Mapping[str, Any],
) -> dict[str, Any] | None:
    if metrics.get("replay_compacted_argument_blocked") is not True:
        return None
    return {
        "reason": REPLAY_COMPACTED_ARGUMENT_FAILURE,
        "detail": "replay stopped before executing compacted tool arguments",
    }


def _evidence_manifest_metrics(
    *,
    artifact_dir: Path | None,
    evidence_manifest: Path | None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    if evidence_manifest is None:
        return {}
    manifest_present = evidence_manifest.exists()
    metrics: dict[str, Any] = {
        "evidence_manifest_path": str(evidence_manifest),
        "evidence_manifest_present": manifest_present,
        "evidence_manifest_readable": False,
        "evidence_manifest_valid": False,
        "evidence_manifest_entry_count": 0,
    }
    if not manifest_present:
        return metrics
    entries: list[Mapping[str, Any]] = []
    invalid_reasons: list[str] = []
    archived_entry_count = 0
    manifest_metadata: dict[str, Any] = {
        "path": str(evidence_manifest),
        "present": True,
        "readable": False,
        "valid": False,
        "entry_count": 0,
        "invalid_entry_count": 0,
    }
    try:
        manifest_size = evidence_manifest.stat().st_size
    except OSError as exc:
        invalid_reasons.append(f"manifest is not readable: {exc.__class__.__name__}")
        return _finalize_evidence_manifest_metrics(
            metrics=metrics,
            artifact_dir=artifact_dir,
            evidence_manifest=evidence_manifest,
            entries=entries,
            invalid_reasons=invalid_reasons,
            manifest_metadata=manifest_metadata,
        )
    metrics["evidence_manifest_readable"] = True
    metrics["evidence_manifest_size_bytes"] = manifest_size
    manifest_metadata["readable"] = True
    manifest_metadata["size_bytes"] = manifest_size
    if manifest_size > _MAX_EVIDENCE_MANIFEST_BYTES:
        invalid_reasons.append(
            "manifest exceeds "
            f"{_MAX_EVIDENCE_MANIFEST_BYTES} byte limit"
        )
        return _finalize_evidence_manifest_metrics(
            metrics=metrics,
            artifact_dir=artifact_dir,
            evidence_manifest=evidence_manifest,
            entries=entries,
            invalid_reasons=invalid_reasons,
            manifest_metadata=manifest_metadata,
        )
    try:
        with evidence_manifest.open("rb") as stream:
            manifest_bytes = stream.read(_MAX_EVIDENCE_MANIFEST_BYTES + 1)
    except OSError as exc:
        metrics["evidence_manifest_readable"] = False
        manifest_metadata["readable"] = False
        invalid_reasons.append(f"manifest is not readable: {exc.__class__.__name__}")
        return _finalize_evidence_manifest_metrics(
            metrics=metrics,
            artifact_dir=artifact_dir,
            evidence_manifest=evidence_manifest,
            entries=entries,
            invalid_reasons=invalid_reasons,
            manifest_metadata=manifest_metadata,
        )
    if len(manifest_bytes) > _MAX_EVIDENCE_MANIFEST_BYTES:
        metrics["evidence_manifest_size_bytes"] = len(manifest_bytes)
        manifest_metadata["size_bytes"] = len(manifest_bytes)
        invalid_reasons.append(
            "manifest exceeds "
            f"{_MAX_EVIDENCE_MANIFEST_BYTES} byte limit"
        )
        return _finalize_evidence_manifest_metrics(
            metrics=metrics,
            artifact_dir=artifact_dir,
            evidence_manifest=evidence_manifest,
            entries=entries,
            invalid_reasons=invalid_reasons,
            manifest_metadata=manifest_metadata,
        )
    metrics["evidence_manifest_size_bytes"] = len(manifest_bytes)
    manifest_metadata["size_bytes"] = len(manifest_bytes)
    manifest_fingerprint = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
    metrics["evidence_manifest_fingerprint"] = manifest_fingerprint
    manifest_metadata["fingerprint"] = manifest_fingerprint
    manifest_text = manifest_bytes.decode("utf-8", errors="replace")
    decoded_entry_count = 0
    for line_number, entry, decode_error in _decode_evidence_manifest_stream(
        manifest_text
    ):
        if decoded_entry_count >= _MAX_EVIDENCE_MANIFEST_ENTRIES:
            invalid_reasons.append(
                "manifest exceeds "
                f"{_MAX_EVIDENCE_MANIFEST_ENTRIES} entry limit"
            )
            break
        decoded_entry_count += 1
        if decode_error is not None:
            invalid_reasons.append(f"line {line_number}: {decode_error}")
            continue
        if not isinstance(entry, Mapping):
            invalid_reasons.append(f"line {line_number}: entry is not an object")
            continue
        archived_entry = _archive_workspace_manifest_artifact(
            entry,
            artifact_dir=artifact_dir,
            workspace_root=workspace_root,
        )
        if archived_entry is not entry:
            archived_entry_count += 1
            entry = archived_entry
        reason = _invalid_evidence_manifest_entry_reason(
            entry,
            artifact_dir=artifact_dir,
        )
        if reason is not None:
            invalid_reasons.append(f"line {line_number}: {reason}")
            continue
        entries.append(_canonical_evidence_entry(entry, artifact_dir=artifact_dir))
    if archived_entry_count:
        metrics["evidence_manifest_archived_entry_count"] = archived_entry_count
    return _finalize_evidence_manifest_metrics(
        metrics=metrics,
        artifact_dir=artifact_dir,
        evidence_manifest=evidence_manifest,
        entries=entries,
        invalid_reasons=invalid_reasons,
        manifest_metadata=manifest_metadata,
    )


def _finalize_evidence_manifest_metrics(
    *,
    metrics: dict[str, Any],
    artifact_dir: Path | None,
    evidence_manifest: Path,
    entries: list[Mapping[str, Any]],
    invalid_reasons: list[str],
    manifest_metadata: dict[str, Any],
) -> dict[str, Any]:
    manifest_valid = bool(entries) and not invalid_reasons
    metrics["evidence_manifest_entry_count"] = len(entries)
    metrics["evidence_manifest_valid"] = manifest_valid
    manifest_metadata["valid"] = manifest_valid
    manifest_metadata["entry_count"] = len(entries)
    manifest_metadata["invalid_entry_count"] = len(invalid_reasons)
    if invalid_reasons:
        metrics["evidence_manifest_invalid_entry_count"] = len(invalid_reasons)
        metrics["evidence_manifest_invalid_reasons"] = invalid_reasons
    bundle_metrics = _write_evidence_bundle(
        artifact_dir=artifact_dir,
        evidence_manifest=evidence_manifest,
        entries=entries,
        invalid_reasons=invalid_reasons,
        manifest_metadata=manifest_metadata,
    )
    metrics.update(bundle_metrics)
    return metrics


def _decode_evidence_manifest_stream(
    manifest_text: str,
) -> list[tuple[int, Any, str | None]]:
    """Decode JSONL plus whitespace-separated pretty-printed JSON objects.

    The replay contract asks agents to append one compact object per line, but
    shell heredocs and ``json.dumps(..., indent=2)`` commonly produce a stream
    of complete multi-line objects. Treat object boundaries from the JSON
    grammar as authoritative while preserving line-local diagnostics for
    malformed content. Every decoded value still passes the same schema,
    artifact-boundary, and bounded-evidence checks below.
    """

    decoder = json.JSONDecoder()
    decoded: list[tuple[int, Any, str | None]] = []
    cursor = 0
    text_length = len(manifest_text)
    while cursor < text_length:
        while cursor < text_length and manifest_text[cursor].isspace():
            cursor += 1
        if cursor >= text_length:
            break
        line_number = manifest_text.count("\n", 0, cursor) + 1
        try:
            value, end = decoder.raw_decode(manifest_text, cursor)
        except json.JSONDecodeError as exc:
            decoded.append(
                (
                    line_number,
                    None,
                    exc.msg,
                )
            )
            newline = manifest_text.find("\n", cursor)
            cursor = text_length if newline < 0 else newline + 1
            continue
        decoded.append((line_number, value, None))
        cursor = end
    return decoded


def _write_evidence_bundle(
    *,
    artifact_dir: Path | None,
    evidence_manifest: Path,
    entries: list[Mapping[str, Any]],
    invalid_reasons: list[str],
    manifest_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if artifact_dir is None:
        return {}
    bundle_path = artifact_dir / "evidence_bundle.json"
    bundle = {
        "format": "aworld.self_evolve.evidence_bundle",
        "version": 1,
        "manifest_path": str(evidence_manifest),
        "manifest": dict(manifest_metadata),
        "valid": bool(entries) and not invalid_reasons,
        "entries": entries,
    }
    if invalid_reasons:
        bundle["invalid_reasons"] = invalid_reasons
    bundle_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "evidence_bundle_path": str(bundle_path),
        "evidence_bundle_present": True,
        "evidence_bundle_valid": bundle["valid"],
        "evidence_bundle_entry_count": len(entries),
    }


def _canonical_evidence_entry(
    entry: Mapping[str, Any],
    *,
    artifact_dir: Path | None,
) -> dict[str, Any]:
    bounded_evidence = _bounded_evidence_payload(entry)
    evidence_type = _manifest_evidence_type(entry)
    artifact_path = None
    if evidence_type == "artifact":
        artifact_path = _manifest_artifact_path(entry, artifact_dir=artifact_dir)
    if not bounded_evidence and artifact_path is not None:
        synthetic_excerpt = _synthetic_bounded_artifact_excerpt(artifact_path)
        if synthetic_excerpt:
            bounded_evidence["bounded_excerpt"] = synthetic_excerpt["text"]
            bounded_evidence["source"] = "artifact_preview"
            bounded_evidence["truncated"] = synthetic_excerpt["truncated"]
    fields_used = entry.get("fields_used")
    if fields_used and "fields_used" not in bounded_evidence:
        bounded_evidence["fields_used"] = fields_used
    canonical = {
        "source_id": str(entry.get("source_id") or ""),
        "extraction_method": str(entry.get("extraction_method") or ""),
        "bounded_evidence": bounded_evidence,
    }
    if evidence_type == "metadata":
        canonical["evidence_type"] = "metadata"
        canonical["metadata"] = _metadata_evidence_payload(entry)
    elif artifact_path is not None:
        canonical["artifact_path"] = str(artifact_path)
    return canonical


def _bounded_evidence_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _MANIFEST_EVIDENCE_PAYLOAD_KEYS:
        if key in entry:
            payload[key] = entry[key]
    for alias, canonical_key in _MANIFEST_EVIDENCE_PAYLOAD_ALIASES.items():
        if canonical_key not in payload and alias in entry:
            payload[canonical_key] = entry[alias]
    return payload


def _metadata_evidence_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded structured payload for non-file evidence.

    The preferred manifest shape nests operation data under ``metadata``.
    Agents also commonly emit the same bounded data through one of the
    manifest's established evidence payload fields (for example,
    ``bounded_excerpt``).  Canonicalize that equivalent shape instead of
    rejecting otherwise verifiable evidence; both forms remain subject to the
    same JSON-serialization and size checks.
    """

    metadata = entry.get("metadata")
    if isinstance(metadata, Mapping) and metadata:
        return dict(metadata)
    return _bounded_evidence_payload(entry)


def _invalid_evidence_manifest_entry_reason(
    entry: Mapping[str, Any],
    *,
    artifact_dir: Path | None,
) -> str | None:
    for key in ("source_id", "extraction_method"):
        if not str(entry.get(key) or "").strip():
            return f"missing {key}"
    evidence_type = _manifest_evidence_type(entry)
    if evidence_type == "metadata":
        metadata = _metadata_evidence_payload(entry)
        if not metadata:
            return "missing metadata"
        try:
            serialized_metadata = json.dumps(
                metadata,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            return "metadata is not JSON serializable"
        if len(serialized_metadata) > _MAX_METADATA_EVIDENCE_CHARS:
            return "metadata exceeds bounded evidence limit"
        return None
    if evidence_type != "artifact":
        return f"unsupported evidence_type: {evidence_type}"
    if not str(entry.get("artifact_path") or "").strip():
        return "missing artifact_path"
    artifact_path = _manifest_artifact_path(entry, artifact_dir=artifact_dir)
    has_inline_bounded_evidence = _has_inline_bounded_evidence_payload(entry)
    if not artifact_path.exists():
        return "artifact_path does not exist"
    if artifact_dir is not None:
        try:
            artifact_path.resolve().relative_to(artifact_dir.resolve())
        except ValueError:
            if not has_inline_bounded_evidence:
                return "artifact_path is outside trusted replay/workspace directories"
    if not _has_manifest_evidence_payload(entry) and not _synthetic_bounded_artifact_excerpt(
        artifact_path
    ):
        return "missing bounded evidence payload"
    return None


def _manifest_evidence_type(entry: Mapping[str, Any]) -> str:
    explicit = str(entry.get("evidence_type") or "").strip().lower()
    if explicit == "file":
        return "artifact"
    if explicit:
        return explicit
    if not str(entry.get("artifact_path") or "").strip() and isinstance(
        entry.get("metadata"), Mapping
    ):
        return "metadata"
    return "artifact"


def _manifest_artifact_path(entry: Mapping[str, Any], *, artifact_dir: Path | None) -> Path:
    artifact_path = Path(str(entry.get("artifact_path")))
    if not artifact_path.is_absolute() and artifact_dir is not None:
        artifact_path = artifact_dir / artifact_path
    return artifact_path


def _archive_workspace_manifest_artifact(
    entry: Mapping[str, Any],
    *,
    artifact_dir: Path | None,
    workspace_root: Path | None,
) -> Mapping[str, Any]:
    if artifact_dir is None or workspace_root is None:
        return entry
    if _manifest_evidence_type(entry) != "artifact":
        return entry
    artifact_path = _manifest_artifact_path(entry, artifact_dir=artifact_dir)
    try:
        resolved_artifact = artifact_path.resolve()
    except OSError:
        return entry
    try:
        resolved_artifact.relative_to(artifact_dir.resolve())
        return entry
    except ValueError:
        pass

    if not artifact_path.exists() or not artifact_path.is_file():
        return entry

    try:
        workspace_relative = resolved_artifact.relative_to(workspace_root.resolve())
    except ValueError:
        return entry

    archive_dir = artifact_dir / "workspace_evidence"
    archive_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(resolved_artifact).encode("utf-8")).hexdigest()[:12]
    safe_name = "__".join(_safe_artifact_path_part(part) for part in workspace_relative.parts)
    archived_path = archive_dir / f"{digest}__{safe_name}"
    if not archived_path.exists():
        shutil.copy2(resolved_artifact, archived_path)

    normalized = dict(entry)
    normalized["artifact_path"] = str(archived_path)
    return normalized


def _safe_artifact_path_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return safe or "artifact"


def _synthetic_bounded_artifact_excerpt(artifact_path: Path) -> dict[str, Any] | None:
    try:
        with artifact_path.open("r", encoding="utf-8", errors="replace") as stream:
            raw = stream.read(_SYNTHETIC_EVIDENCE_EXCERPT_CHARS + 1)
    except OSError:
        return None
    text = raw.strip()
    if not text:
        return None
    truncated = len(text) > _SYNTHETIC_EVIDENCE_EXCERPT_CHARS
    if truncated:
        text = text[:_SYNTHETIC_EVIDENCE_EXCERPT_CHARS]
    return {"text": text, "truncated": truncated}


_MANIFEST_EVIDENCE_PAYLOAD_KEYS = (
    "excerpt",
    "excerpts",
    "bounded_excerpt",
    "bounded_excerpts",
    "field_list",
    "fields",
    "fields_extracted",
    "key_fields",
    "selected_fields",
    "claims_supported",
    "claims_supported_by",
    "summary",
    "structured_summary",
)


# Generated skills sometimes describe a list of bounded excerpts as the fields
# selected from an artifact.  Normalize that structurally equivalent spelling
# into the canonical bundle schema so downstream judges consume the explicit
# evidence instead of falling back to a truncated artifact preview.
_MANIFEST_EVIDENCE_PAYLOAD_ALIASES = {
    "bounded_excerpt_fields": "bounded_excerpts",
}


_MANIFEST_INLINE_BOUNDED_EVIDENCE_KEYS = (
    "excerpt",
    "excerpts",
    "bounded_excerpt",
    "bounded_excerpts",
    "claims_supported",
    "claims_supported_by",
    "summary",
    "structured_summary",
    *_MANIFEST_EVIDENCE_PAYLOAD_ALIASES,
)


def _has_manifest_evidence_payload(entry: Mapping[str, Any]) -> bool:
    return _has_any_manifest_payload(
        entry,
        keys=(*_MANIFEST_EVIDENCE_PAYLOAD_KEYS, *_MANIFEST_EVIDENCE_PAYLOAD_ALIASES),
    )


def _has_inline_bounded_evidence_payload(entry: Mapping[str, Any]) -> bool:
    return _has_any_manifest_payload(entry, keys=_MANIFEST_INLINE_BOUNDED_EVIDENCE_KEYS)


def _has_any_manifest_payload(entry: Mapping[str, Any], *, keys: Sequence[str]) -> bool:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, Mapping) and value:
            return True
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and value:
            return True
    return False


def _evidence_quality_failure(
    metrics: Mapping[str, Any],
    *,
    variant_id: str | None = None,
    variant_role: str | None = None,
) -> dict[str, Any] | None:
    policy_violation_count = metrics.get(
        "evidence_runtime_policy_violation_count"
    )
    policy_authority = str(
        metrics.get("evidence_runtime_policy_authority") or "authoritative"
    ).casefold()
    if (
        isinstance(policy_violation_count, (int, float))
        and not isinstance(policy_violation_count, bool)
        and policy_violation_count > 0
        and policy_authority != "advisory"
    ):
        raw_counterexamples = metrics.get("replay_counterexamples")
        counterexamples = [
            dict(item)
            for item in raw_counterexamples[:16]
            if isinstance(item, Mapping)
        ] if isinstance(raw_counterexamples, list) else []
        baseline = variant_role == "baseline" or variant_id == "baseline"
        return {
            "code": "replay_evidence_runtime_policy_violation",
            "type": "ReplayEvidencePolicyViolation",
            "outcome": "task_failure" if baseline else "candidate_failure",
            "failure_class": (
                "baseline_task_behavior"
                if baseline
                else "candidate_task_behavior"
            ),
            "failure_stage": "task_rollout",
            "repairable": not baseline,
            "category": "replay_runtime_policy",
            "reason": "replay violated a framework-enforced runtime contract",
            "diagnostics": {
                "policy_violation_count": int(policy_violation_count),
                "replay_counterexamples": counterexamples,
            },
        }
    compacted = metrics.get("evidence_compacted") is True
    strategy_failed = metrics.get("evidence_strategy_passed") is False
    invalid_manifest_count = metrics.get("evidence_manifest_invalid_entry_count")
    manifest_invalid = (
        isinstance(invalid_manifest_count, (int, float))
        and invalid_manifest_count > 0
    )
    if not strategy_failed:
        return None
    signals = metrics.get("evidence_compaction_signals")
    if not isinstance(signals, list):
        signals = []
    return {
        "code": "evidence_quality_failed",
        "outcome": "task_failure",
        "failure_stage": "evaluation",
        "reason": "evidence_quality_failed",
        "detail": "replay produced compacted, truncated, or otherwise unusable evidence",
        "evidence_compacted": compacted,
        "evidence_strategy_passed": not strategy_failed,
        "evidence_manifest_invalid_entry_count": invalid_manifest_count if manifest_invalid else 0,
        "evidence_compaction_signals": [str(signal) for signal in signals],
    }


def _has_valid_artifact_backed_timeout_evidence(metrics: Mapping[str, Any]) -> bool:
    invalid_manifest_count = metrics.get("evidence_manifest_invalid_entry_count")
    manifest_invalid = (
        isinstance(invalid_manifest_count, (int, float))
        and invalid_manifest_count > 0
    )
    return (
        metrics.get("evidence_manifest_valid") is True
        and metrics.get("evidence_bundle_valid") is True
        and not manifest_invalid
    )


def _timeout_evidence_counterexample(
    metrics: Mapping[str, Any],
    *,
    finalization_deadline: bool = False,
) -> dict[str, Any]:
    """Describe the physical timeout without assigning treatment blame."""

    return _task_completion_counterexample(
        metrics,
        failure_code=(
            "replay_evidence_finalization_timeout"
            if finalization_deadline
            else "replay_task_timeout_with_recoverable_evidence"
        ),
        trigger=(
            "evidence_finalization_timeout"
            if finalization_deadline
            else "task_timeout"
        ),
        required_transition=(
            "emit_task_response_within_frozen_finalization_deadline"
            if finalization_deadline
            else "finalize_task_response_before_timeout"
        ),
        owner="task",
    )


def _task_completion_counterexample(
    metrics: Mapping[str, Any],
    *,
    failure_code: str,
    trigger: str,
    required_transition: str,
    owner: str,
) -> dict[str, Any]:
    """Describe missing completion without copying task text or payloads."""

    return {
        "schema_version": REPLAY_COUNTEREXAMPLE_SCHEMA_VERSION,
        "sequence": 1,
        "owner": owner,
        "failure_code": failure_code,
        "stage": "task_rollout",
        "state_before": (
            "evidence_ready"
            if metrics.get("evidence_manifest_valid") is True
            else "collecting"
        ),
        "trigger": trigger,
        "tool_name": "replay_runtime",
        "action_name": "finalize_task_response",
        "manifest_entry_count": max(
            0, int(metrics.get("evidence_manifest_entry_count") or 0)
        ),
        "artifact_file_count": max(
            0,
            int(
                metrics.get("evidence_runtime_policy_artifact_file_count")
                or 0
            ),
        ),
        "artifact_bytes": max(
            0,
            int(metrics.get("evidence_runtime_policy_artifact_bytes") or 0),
        ),
        "required_transition": required_transition,
    }


def _task_completion_not_established_result(
    *,
    request: ReplayExecutionRequest,
    trajectory: list[Mapping[str, Any]],
    stdout: str,
    stderr: str,
    metrics: Mapping[str, Any],
    trigger: str,
    required_transition: str,
) -> ReplayExecutionResult:
    variant_role = _replay_execution_variant_role(request)
    owner = "task" if variant_role == "baseline" else "candidate"
    counterexample = _task_completion_counterexample(
        metrics,
        failure_code="replay_task_completion_not_established",
        trigger=trigger,
        required_transition=required_transition,
        owner=owner,
    )
    result_metrics = {
        **metrics,
        "task_completion_established": False,
        "replay_counterexamples": [counterexample],
    }
    return ReplayExecutionResult(
        status="failed",
        trajectory=trajectory,
        stdout=stdout,
        stderr=stderr,
        metrics=result_metrics,
        failure={
            "code": "replay_task_completion_not_established",
            "outcome": (
                "task_failure"
                if variant_role == "baseline"
                else "candidate_failure"
            ),
            "failure_class": (
                "baseline_task_incomplete"
                if variant_role == "baseline"
                else "candidate_task_behavior"
            ),
            "failure_stage": "task_rollout",
            "repairable": variant_role != "baseline",
            "category": "task_completion",
            "reason": (
                "replay process exited without establishing a terminal "
                "task action through TaskResponse.trajectory"
            ),
            "diagnostics": {
                "trajectory_capture_mode": metrics.get(
                    "trajectory_capture_mode"
                ),
                "task_completion_established": False,
                "replay_counterexamples": [counterexample],
            },
        },
    )


def _is_evidence_quality_failure(result: ReplayVariantResult) -> bool:
    failure = result.failure
    return (
        isinstance(failure, ReplayFailureEvent)
        and failure.code == "evidence_quality_failed"
    )


def _is_retryable_framework_capture_failure(
    result: ReplayVariantResult,
) -> bool:
    failure = result.failure
    return (
        isinstance(failure, ReplayFailureEvent)
        and failure.owner is FailureOwner.FRAMEWORK
        and failure.stage is FailureStage.EVALUATION
        and failure.code == "trajectory_capture_unavailable"
        and failure.repairable
    )


def _is_retryable_replay_service_startup_failure(
    result: ReplayVariantResult,
) -> bool:
    failure = result.failure
    return (
        isinstance(failure, ReplayFailureEvent)
        and failure.owner is FailureOwner.INFRASTRUCTURE
        and failure.stage is FailureStage.CAPABILITY_PREFLIGHT
        and failure.scope is FailureScope.SHARED_RUN
        and failure.code in {
            "replay_service_startup_timeout",
            "replay_service_startup_process_exited",
        }
        and failure.repairable
    )


def _merge_replay_attempt_metrics(
    result: ReplayVariantResult,
    *,
    attempts: list[ReplayVariantResult],
    canonical_variant_id: str,
) -> ReplayVariantResult:
    if len(attempts) == 1:
        return result
    retry_failures = [
        attempt.failure.compatibility_dict()
        for attempt in attempts[:-1]
        if attempt.failure is not None
    ]
    signals: list[str] = []
    for attempt in attempts:
        raw_signals = attempt.metrics.get("evidence_compaction_signals")
        if not isinstance(raw_signals, list):
            continue
        for item in raw_signals:
            signal = str(item).strip()
            if signal and signal not in signals:
                signals.append(signal)
    metrics = {
        **dict(result.metrics),
        "replay_attempt_count": len(attempts),
        "evidence_retry_count": sum(
            _is_evidence_quality_failure(attempt)
            for attempt in attempts[:-1]
        ),
        "framework_capture_retry_count": sum(
            _is_retryable_framework_capture_failure(attempt)
            for attempt in attempts[:-1]
        ),
        "service_startup_retry_count": sum(
            _is_retryable_replay_service_startup_failure(attempt)
            for attempt in attempts[:-1]
        ),
    }
    if retry_failures:
        metrics["retry_failures"] = retry_failures
    if signals:
        metrics["evidence_compaction_signals"] = signals
    return ReplayVariantResult(
        variant_id=canonical_variant_id,
        status=result.status,
        trajectory=result.trajectory,
        metrics=metrics,
        stdout_path=result.stdout_path,
        stderr_path=result.stderr_path,
        failure=result.failure,
        repetition_results=result.repetition_results,
    )


def _text_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_json_dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _persist_variant_lifecycle(
    artifact_dir: Path,
    result: ReplayVariantResult,
) -> None:
    """Persist typed lifecycle plus the legacy inspection files additively."""

    artifact_dir.mkdir(parents=True, exist_ok=True)
    for source_path, filename in (
        (result.stdout_path, "stdout.txt"),
        (result.stderr_path, "stderr.txt"),
    ):
        destination = artifact_dir / filename
        if not result.executed:
            if destination.exists():
                destination.unlink()
            continue
        if source_path is None:
            continue
        source = Path(source_path)
        try:
            if source.exists() and source.resolve() != destination.resolve():
                shutil.copyfile(source, destination)
        except OSError:
            pass
    _write_json(artifact_dir / "trajectory.json", result.trajectory)
    _write_json(artifact_dir / "metrics.json", result.metrics)
    failure_path = artifact_dir / "failure.json"
    if result.failure is not None:
        _write_json(failure_path, result.failure.compatibility_dict())
    elif failure_path.exists():
        failure_path.unlink()
    _write_json(
        artifact_dir / "lifecycle.json",
        {
            "schema_version": _REPLAY_LIFECYCLE_SCHEMA_V3,
            "repetition_semantics": _PER_MEMBER_REPETITION_SEMANTICS,
            "variant_id": result.variant_id,
            "status": result.status,
            "failure": result.failure.to_dict() if result.failure is not None else None,
            "blocked_by": [event.to_dict() for event in result.blocked_by],
        },
    )


def _measurement_artifact_reference(path: Path) -> dict[str, object]:
    """Return a bounded content address without loading a large artifact."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("measurement result artifact is missing or unsafe")
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
    return {
        "name": path.name,
        "content_digest": "sha256:" + digest.hexdigest(),
        "byte_count": byte_count,
    }


def _measurement_failure_projection(
    failure: ReplayFailureEvent | None,
) -> Mapping[str, Any] | None:
    if failure is None:
        return None
    payload = failure.to_dict()
    payload["diagnostics"] = {
        "full_failure_fingerprint": stable_control_fingerprint(payload),
    }
    payload["artifact_refs"] = []
    return payload


def _measurement_result_projection(
    result: ReplayVariantResult,
    *,
    artifact_dir: Path,
    include_artifact_references: bool = True,
) -> dict[str, Any]:
    """Project replay state into a bounded scheduler/control-plane message.

    Trajectory, stdout, stderr, and detailed metrics remain immutable replay
    artifacts.  Only their content addresses cross the scheduler boundary.
    """

    artifact_references: list[dict[str, object]] = []
    if include_artifact_references:
        for filename in (
            "lifecycle.json",
            "trajectory.json",
            "metrics.json",
            "failure.json",
        ):
            path = artifact_dir / filename
            if path.exists():
                artifact_references.append(_measurement_artifact_reference(path))
    return {
        "schema_version": _MEASUREMENT_RESULT_PROJECTION_SCHEMA,
        "variant_id": result.variant_id,
        "status": result.status.value,
        "executed": result.executed,
        "succeeded": result.succeeded,
        "failure": _measurement_failure_projection(result.failure),
        "blocked_by": [
            _measurement_failure_projection(event) for event in result.blocked_by[:8]
        ],
        "artifact_references": artifact_references,
    }


def _persist_measurement_result_projection(
    artifact_dir: Path,
    *,
    result: ReplayVariantResult,
) -> "ResolvedControl[Mapping[str, Any]]":
    # Local import preserves replay's existing lazy measurement-control import
    # boundary and avoids pulling scheduler/store into ordinary replay startup.
    from aworld.self_evolve.measurement_scheduler import ResolvedControl

    projection = _measurement_result_projection(result, artifact_dir=artifact_dir)
    resolved = ResolvedControl.from_value(projection)
    _write_json(artifact_dir / _MEASUREMENT_RESULT_PROJECTION_FILE, projection)
    reloaded = _load_measurement_result_projection(artifact_dir)
    if reloaded.result_fingerprint != resolved.result_fingerprint:
        raise ValueError("measurement result projection did not round trip")
    return reloaded


def _load_measurement_result_projection(
    artifact_dir: Path,
) -> "ResolvedControl[Mapping[str, Any]]":
    from aworld.self_evolve.measurement_scheduler import ResolvedControl

    payload = _load_json_object(
        artifact_dir / _MEASUREMENT_RESULT_PROJECTION_FILE
    )
    if payload.get("schema_version") != _MEASUREMENT_RESULT_PROJECTION_SCHEMA:
        raise ValueError("unsupported measurement result projection schema")
    raw_references = payload.get("artifact_references")
    if not isinstance(raw_references, list):
        raise ValueError("measurement result projection has no artifact references")
    for item in raw_references:
        if not isinstance(item, Mapping):
            raise ValueError("measurement result artifact reference is invalid")
        name = item.get("name")
        if not isinstance(name, str) or name != Path(name).name:
            raise ValueError("measurement result artifact name is unsafe")
        observed = _measurement_artifact_reference(artifact_dir / name)
        if observed != dict(item):
            raise ValueError("measurement result artifact content drifted")
    return ResolvedControl.from_value(dict(payload))


def _persist_measurement_execution_error(
    artifact_dir: Path,
    *,
    exc: BaseException,
    work_unit_id: str,
    arm: MeasurementArm,
) -> None:
    try:
        _write_json(
            artifact_dir / "measurement_execution_error.json",
            {
                "schema_version": "aworld.self_evolve.measurement_execution_error.v1",
                "work_unit_id": work_unit_id,
                "arm": arm.value,
                "error_type": type(exc).__name__,
                "error": sanitize_text(str(exc), max_chars=2_000),
                "recorded_at": _utc_now(),
            },
        )
    except OSError:
        logger.exception(
            "self_evolve.measurement.execution_error_persistence_failed "
            f"work_unit_id={work_unit_id}"
        )


def _safe_path(value: str) -> str:
    safe = "".join(
        character
        for character in value
        if character.isalnum() or character in {"-", "_", "."}
    ).strip(".")
    return safe or "default"


def _safe_artifact_namespace(value: str) -> tuple[str, ...]:
    parts = tuple(part for part in value.replace("\\", "/").split("/") if part)
    if not parts or any(part in {".", ".."} or _safe_path(part) != part for part in parts):
        raise ValueError(f"invalid replay artifact namespace: {value!r}")
    return parts


def _member_artifact_name(case_id: str) -> str:
    prefix = _safe_path(case_id)[:80]
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _aggregate_variant_results(
    *,
    base_variant_id: str,
    results: list[ReplayVariantResult],
    artifact_dir: Path,
    persist: bool = True,
) -> ReplayVariantResult:
    if not results:
        raise ValueError("cannot aggregate empty replay results")
    successful = [result for result in results if result.succeeded]
    failed = [
        result for result in results if result.status is ReplayExecutionStatus.FAILED
    ]
    blocked = [
        result for result in results if result.status is ReplayExecutionStatus.BLOCKED
    ]
    not_run = [
        result for result in results if result.status is ReplayExecutionStatus.NOT_RUN
    ]
    if successful:
        status = ReplayExecutionStatus.SUCCEEDED
    elif failed:
        status = ReplayExecutionStatus.FAILED
    elif blocked:
        status = ReplayExecutionStatus.BLOCKED
    else:
        status = ReplayExecutionStatus.NOT_RUN
    numeric_metrics: dict[str, list[float]] = {}
    evidence_compaction_signals: list[str] = []
    evidence_compacted_values: list[bool] = []
    latest_evidence_bundle_path: str | None = None
    replay_counterexamples: list[dict[str, Any]] = []
    provenance_values: dict[str, list[str]] = {
        key: [] for key in _REPLAY_PROVENANCE_METRIC_KEYS
    }
    for result in results:
        for key, value in result.metrics.items():
            if key in provenance_values and isinstance(value, str):
                provenance_values[key].append(value)
            elif key == "evidence_compacted" and isinstance(value, bool):
                evidence_compacted_values.append(value)
            elif key in _EVIDENCE_COVERAGE_BOOL_METRIC_KEYS:
                continue
            elif key in _EVIDENCE_COVERAGE_NUMERIC_METRIC_KEYS:
                continue
            elif key == "evidence_bundle_path" and isinstance(value, str) and value.strip():
                latest_evidence_bundle_path = value
            elif key == "evidence_compaction_signals" and isinstance(value, list):
                for item in value:
                    signal = str(item).strip()
                    if signal and signal not in evidence_compaction_signals:
                        evidence_compaction_signals.append(signal)
            elif key == "replay_counterexamples" and isinstance(value, list):
                for item in value[:16]:
                    if isinstance(item, Mapping) and dict(item) not in replay_counterexamples:
                        replay_counterexamples.append(dict(item))
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric_metrics.setdefault(str(key), []).append(float(value))
    metrics: dict[str, Any] = {
        "repetition_count": len(results),
        "successful_repetition_count": len(successful),
        "failed_repetition_count": len(failed),
        "blocked_repetition_count": len(blocked),
        "not_run_repetition_count": len(not_run),
    }
    repetition_failures = [
        result.failure.compatibility_dict()
        for result in failed
        if result.failure is not None
    ]
    if repetition_failures:
        metrics["repetition_failures"] = repetition_failures
    if replay_counterexamples:
        metrics["replay_counterexamples"] = replay_counterexamples[:16]
    if evidence_compacted_values:
        metrics["evidence_compacted"] = any(evidence_compacted_values)
    for key in _EVIDENCE_COVERAGE_BOOL_METRIC_KEYS:
        coverage_count = sum(
            isinstance(result.metrics.get(key), bool)
            for result in results
        )
        if not coverage_count:
            continue
        values = [result.metrics.get(key) is True for result in results]
        metrics[key] = (
            any(values)
            if key == "timeout_evidence_recovered"
            else all(values)
        )
        metrics[f"{key}_values"] = values
        metrics[f"{key}_coverage_count"] = coverage_count
        metrics[f"{key}_coverage"] = coverage_count / len(results)
    for key in _EVIDENCE_COVERAGE_NUMERIC_METRIC_KEYS:
        coverage_count = sum(
            isinstance(result.metrics.get(key), (int, float))
            and not isinstance(result.metrics.get(key), bool)
            for result in results
        )
        if not coverage_count:
            continue
        values = [
            (
                float(result.metrics[key])
                if isinstance(result.metrics.get(key), (int, float))
                and not isinstance(result.metrics.get(key), bool)
                else 0.0
            )
            for result in results
        ]
        metrics[key] = (
            max(values)
            if key
            in {
                "evidence_manifest_invalid_entry_count",
                "evidence_unmanifested_artifact_reference_count",
                "evidence_runtime_policy_violation_count",
                "evidence_runtime_policy_tool_call_attempt_count",
                "evidence_runtime_policy_artifact_file_count",
                "evidence_runtime_policy_artifact_bytes",
                "evidence_runtime_policy_consecutive_failed_action_count",
            }
            else sum(values) / len(values)
        )
        metrics[f"{key}_values"] = values
        metrics[f"{key}_min"] = min(values)
        metrics[f"{key}_max"] = max(values)
        metrics[f"{key}_coverage_count"] = coverage_count
        metrics[f"{key}_coverage"] = coverage_count / len(results)
    if latest_evidence_bundle_path:
        metrics["evidence_bundle_path"] = latest_evidence_bundle_path
    if evidence_compaction_signals:
        metrics["evidence_compaction_signals"] = evidence_compaction_signals
    for key, values in provenance_values.items():
        if len(values) == len(results) and len(set(values)) == 1:
            metrics[key] = values[0]
        elif values:
            metrics[f"{key}_values"] = values
    for key, values in numeric_metrics.items():
        if values:
            if key in {
                "repetition_count",
                "successful_repetition_count",
                "failed_repetition_count",
            }:
                metrics[key] = sum(values)
                metrics[f"{key}_values"] = values
                continue
            metrics[key] = sum(values) / len(values)
            metrics[f"{key}_values"] = values

    if status is ReplayExecutionStatus.SUCCEEDED:
        selected = successful[-1]
    elif status is ReplayExecutionStatus.FAILED:
        selected = failed[-1]
    else:
        selected = results[-1]
    failure: ReplayFailureEvent | None = None
    blocked_by: tuple[ReplayFailureEvent, ...] = ()
    if status is ReplayExecutionStatus.FAILED:
        legacy_failure = {
            "reason": "one or more replay repetitions failed",
            "failures": [
                result.failure.compatibility_dict()
                for result in results
                if result.failure is not None
            ],
        }
        causal = causal_failure_events(
            tuple(result.failure for result in failed if result.failure is not None)
        )
        exemplar = causal[0]
        if len(causal) == 1:
            failure = exemplar
        else:
            failure = ReplayFailureEvent(
                code="replay_repetition_failure",
                owner=exemplar.owner,
                stage=exemplar.stage,
                scope=exemplar.scope,
                repairable=any(event.repairable for event in causal),
                category="replay_repetition",
                summary="one or more replay repetitions failed",
                causes=tuple(event.event_id for event in causal),
                _compatibility=legacy_failure,
            )
    elif status is ReplayExecutionStatus.BLOCKED:
        blocked_by = causal_failure_events(
            tuple(event for result in blocked for event in result.blocked_by)
        )
    if persist:
        _write_json(artifact_dir / "aggregate_metrics.json", metrics)
    aggregate_executed = status in {
        ReplayExecutionStatus.SUCCEEDED,
        ReplayExecutionStatus.FAILED,
    }
    aggregated = ReplayVariantResult(
        variant_id=base_variant_id,
        status=status,
        trajectory=selected.trajectory if aggregate_executed else [],
        metrics=metrics,
        stdout_path=selected.stdout_path if aggregate_executed else None,
        stderr_path=selected.stderr_path if aggregate_executed else None,
        failure=failure,
        blocked_by=blocked_by,
        repetition_results=tuple(results) if aggregate_executed else (),
    )
    if persist:
        _persist_variant_lifecycle(artifact_dir, aggregated)
    return aggregated


def _aggregate_member_variant_results(
    *,
    base_variant_id: str,
    members: Sequence[CandidateReplayMemberResult],
    select: Callable[[CandidateReplayMemberResult], ReplayVariantResult],
    artifact_dir: Path,
    persist: bool = True,
) -> ReplayVariantResult:
    member_variants = [select(member) for member in members]
    if not member_variants:
        raise ValueError("cannot aggregate an empty replay member set")
    member_variant_pairs = tuple(zip(members, member_variants))
    repetition_results = [
        repetition
        for variant in member_variants
        for repetition in (
            variant.repetition_results if variant.repetition_results else (variant,)
        )
    ]
    failed_members = [
        {
            "case_id": member.case_id,
            "failure": (
                variant.failure.compatibility_dict()
                if variant.failure is not None
                else None
            ),
        }
        for member, variant in member_variant_pairs
        if variant.status is ReplayExecutionStatus.FAILED
    ]
    blocked_members = [
        (member, variant)
        for member, variant in member_variant_pairs
        if variant.status is ReplayExecutionStatus.BLOCKED
    ]
    not_run_members = [
        (member, variant)
        for member, variant in member_variant_pairs
        if variant.status is ReplayExecutionStatus.NOT_RUN
    ]
    successful_members = [
        (member, variant)
        for member, variant in member_variant_pairs
        if variant.succeeded
    ]
    generated_metric_keys = {
        "member_count",
        "successful_member_count",
        "failed_member_count",
        "blocked_member_count",
        "not_run_member_count",
        "repetition_count",
        "successful_repetition_count",
        "failed_repetition_count",
        "member_failures",
    }
    common_metric_keys = set(member_variants[0].metrics)
    for variant in member_variants[1:]:
        common_metric_keys.intersection_update(variant.metrics)
    common_metrics: dict[str, Any] = {}
    for key in common_metric_keys - generated_metric_keys:
        values = [variant.metrics[key] for variant in member_variants]
        if all(value == values[0] for value in values[1:]):
            common_metrics[key] = values[0]
    metrics = {
        **common_metrics,
        "member_count": len(members),
        "successful_member_count": len(successful_members),
        "failed_member_count": len(failed_members),
        "blocked_member_count": len(blocked_members),
        "not_run_member_count": len(not_run_members),
        "repetition_count": sum(
            int(variant.metrics.get("repetition_count", 0))
            for _, variant in member_variant_pairs
            if variant.executed
        ),
        "successful_repetition_count": sum(
            int(variant.metrics.get("successful_repetition_count", 0))
            for _, variant in member_variant_pairs
            if variant.executed
        ),
        "failed_repetition_count": sum(
            int(variant.metrics.get("failed_repetition_count", 0))
            for _, variant in member_variant_pairs
            if variant.executed
        ),
    }
    if failed_members:
        metrics["member_failures"] = failed_members
    failure: ReplayFailureEvent | None = None
    blocked_by: tuple[ReplayFailureEvent, ...] = ()
    if failed_members:
        causal = causal_failure_events(
            tuple(
                variant.failure
                for _, variant in member_variant_pairs
                if variant.failure is not None
            )
        )
        exemplar = causal[0]
        if len(causal) == 1:
            failure = exemplar
        else:
            compatibility = {
                **exemplar.compatibility_dict(),
                "reason": "one or more trajectory-set members failed replay",
                "members": failed_members,
            }
            failure = ReplayFailureEvent(
                code="replay_member_failure",
                owner=exemplar.owner,
                stage=exemplar.stage,
                scope=exemplar.scope,
                repairable=any(event.repairable for event in causal),
                category="replay_member_aggregate",
                summary="one or more trajectory-set members failed replay",
                causes=tuple(event.event_id for event in causal),
                _compatibility=compatibility,
            )
        status = ReplayExecutionStatus.FAILED
    elif blocked_members:
        blocked_by = causal_failure_events(
            tuple(
                event
                for _, variant in blocked_members
                for event in variant.blocked_by
            )
        )
        status = ReplayExecutionStatus.BLOCKED
    elif not_run_members:
        status = ReplayExecutionStatus.NOT_RUN
    else:
        status = ReplayExecutionStatus.SUCCEEDED
    if status is ReplayExecutionStatus.SUCCEEDED:
        selected_variant = successful_members[-1][1]
    elif status is ReplayExecutionStatus.FAILED:
        selected_variant = next(
            variant
            for _, variant in reversed(member_variant_pairs)
            if variant.status is ReplayExecutionStatus.FAILED
        )
    else:
        selected_variant = member_variants[-1]
    aggregate_executed = status in {
        ReplayExecutionStatus.SUCCEEDED,
        ReplayExecutionStatus.FAILED,
    }
    aggregated = ReplayVariantResult(
        variant_id=base_variant_id,
        status=status,
        trajectory=selected_variant.trajectory if aggregate_executed else [],
        metrics=metrics,
        failure=failure,
        blocked_by=blocked_by,
        stdout_path=selected_variant.stdout_path if aggregate_executed else None,
        stderr_path=selected_variant.stderr_path if aggregate_executed else None,
        repetition_results=(tuple(repetition_results) if aggregate_executed else ()),
    )
    if persist:
        _write_json(artifact_dir / "aggregate_metrics.json", metrics)
        _persist_variant_lifecycle(artifact_dir, aggregated)
    return aggregated


def _candidate_replay_request_from_mapping(payload: Mapping[str, Any]) -> CandidateReplayRequest:
    target_payload = payload.get("target")
    if not isinstance(target_payload, Mapping):
        raise ValueError("stored replay request is missing target")
    measurement_plan_payload = payload.get("measurement_plan")
    measurement_decision_payload = payload.get("measurement_isolation_decision")
    measurement_profile_payload = payload.get("measurement_evidence_policy_profile")
    measurement_attestations_payload = payload.get("measurement_lane_attestations")
    measurement_plan: MeasurementPlanV2 | None = None
    measurement_decision: IsolationDecision | None = None
    measurement_profile: EvidencePolicyProfileV2 | None = None
    measurement_attestations: dict[str, LaneMaterializationAttestationV1] = {}
    if (
        any(
            item is not None
            for item in (
                measurement_plan_payload,
                measurement_decision_payload,
                measurement_profile_payload,
            )
        )
        or bool(measurement_attestations_payload)
    ):
        if not all(
            isinstance(item, Mapping)
            for item in (
                measurement_plan_payload,
                measurement_decision_payload,
                measurement_profile_payload,
                measurement_attestations_payload,
            )
        ):
            raise ValueError("stored measurement replay contracts are incomplete")
        measurement_decision = IsolationDecision.from_dict(measurement_decision_payload)
        measurement_profile = EvidencePolicyProfileV2.from_dict(measurement_profile_payload)
        measurement_plan = MeasurementPlanV2.from_dict(
            measurement_plan_payload,
            isolation_decision=measurement_decision,
            evidence_policy_profile=measurement_profile,
        )
        measurement_attestations = {
            str(unit_id): LaneMaterializationAttestationV1.from_dict(
                value if isinstance(value, Mapping) else {}
            )
            for unit_id, value in measurement_attestations_payload.items()
        }
    return CandidateReplayRequest(
        run_id=str(payload.get("run_id") or ""),
        task_id=str(payload.get("task_id") or ""),
        workspace_root=str(payload.get("workspace_root") or ""),
        target=SelfEvolveTargetRef(
            target_type=str(target_payload.get("target_type") or ""),
            target_id=str(target_payload.get("target_id") or ""),
            path=(
                str(target_payload.get("path"))
                if target_payload.get("path") is not None
                else None
            ),
        ),
        candidate_id=str(payload.get("candidate_id") or ""),
        overlay_skill_root=str(payload.get("overlay_skill_root") or ""),
        task_input=payload.get("task_input"),
        baseline_skill_root=(
            str(payload.get("baseline_skill_root"))
            if payload.get("baseline_skill_root") is not None
            else None
        ),
        baseline_replay_dir=(
            str(payload.get("baseline_replay_dir"))
            if payload.get("baseline_replay_dir") is not None
            else None
        ),
        resume_replay_dir=(
            str(payload.get("resume_replay_dir"))
            if payload.get("resume_replay_dir") is not None
            else None
        ),
        agent=str(payload.get("agent")) if payload.get("agent") is not None else None,
        evidence_policy_mode=str(
            payload.get("evidence_policy_mode") or "legacy"
        ),
        measurement_plan=measurement_plan,
        measurement_isolation_decision=measurement_decision,
        measurement_evidence_policy_profile=measurement_profile,
        measurement_lane_attestations=measurement_attestations,
        timeout_seconds=_optional_float(payload.get("timeout_seconds")),
        max_steps=_optional_int(payload.get("max_steps")),
        max_tool_calls=_optional_int(payload.get("max_tool_calls")),
        max_tokens=_optional_int(payload.get("max_tokens")),
        max_cost_usd=_optional_float(payload.get("max_cost_usd")),
        baseline_repetitions=_positive_int(payload.get("baseline_repetitions"), default=1),
        candidate_repetitions=_positive_int(payload.get("candidate_repetitions"), default=1),
        invalid_control_patience=_positive_int(
            payload.get("invalid_control_patience"),
            default=2,
        ),
        measurement_early_stop_enabled=(
            payload.get("measurement_early_stop_enabled") is True
        ),
        stop_on_incomparable_member=(
            payload.get("stop_on_incomparable_member") is True
        ),
        repetition_policy=str(
            payload.get("repetition_policy") or "configured"
        ),
        repetition_semantics=(
            str(payload.get("repetition_semantics"))
            if payload.get("repetition_semantics") is not None
            else _MIGRATED_DISTRIBUTED_REPETITION_SEMANTICS
        ),
        dataset_fingerprint=(
            str(payload.get("dataset_fingerprint"))
            if payload.get("dataset_fingerprint") is not None
            else None
        ),
        baseline_skill_fingerprint=(
            str(payload.get("baseline_skill_fingerprint"))
            if payload.get("baseline_skill_fingerprint") is not None
            else None
        ),
        adaptation_fingerprint=(
            str(payload.get("adaptation_fingerprint"))
            if payload.get("adaptation_fingerprint") is not None
            else None
        ),
        support_fingerprint=(
            str(payload.get("support_fingerprint"))
            if payload.get("support_fingerprint") is not None
            else None
        ),
        timeout_envelope_fingerprint=(
            str(payload.get("timeout_envelope_fingerprint"))
            if payload.get("timeout_envelope_fingerprint") is not None
            else None
        ),
        workspace_seed_fingerprint=(
            str(payload.get("workspace_seed_fingerprint"))
            if payload.get("workspace_seed_fingerprint") is not None
            else None
        ),
        task_input_fingerprint=(
            str(payload.get("task_input_fingerprint"))
            if payload.get("task_input_fingerprint") is not None
            else None
        ),
        verified_candidate_package_fingerprint=(
            str(payload.get("verified_candidate_package_fingerprint"))
            if payload.get("verified_candidate_package_fingerprint") is not None
            else None
        ),
        artifact_namespace=(
            str(payload.get("artifact_namespace"))
            if payload.get("artifact_namespace") is not None
            else None
        ),
        replay_adaptation=_replay_adaptation_from_mapping(
            payload.get("replay_adaptation")
        ),
    )


def _replay_adaptation_from_mapping(value: Any) -> ReplayAdaptationBundle | None:
    if not isinstance(value, Mapping):
        return None
    raw_cases = value.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("stored replay adaptation is missing cases")
    cases: list[ReplayCaseAdaptation] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping):
            raise ValueError("stored replay adaptation case must be an object")
        dependencies = tuple(
            ReplayDependency(
                kind=str(item.get("kind") or ""),
                identifier=str(item.get("identifier") or ""),
                status=str(item.get("status") or ""),
                deterministic=item.get("deterministic") is True,
                adapter_id=(
                    str(item.get("adapter_id"))
                    if item.get("adapter_id") is not None
                    else None
                ),
                detail=(
                    str(item.get("detail"))
                    if item.get("detail") is not None
                    else None
                ),
            )
            for item in raw_case.get("dependencies", ())
            if isinstance(item, Mapping)
        )
        bindings = tuple(
            validate_replay_binding_concurrency(
                ReplayAdapterBinding(
                    adapter_id=str(item.get("adapter_id") or ""),
                    dependency_id=str(item.get("dependency_id") or ""),
                    deterministic=item.get("deterministic") is True,
                    environment=(
                        {
                            str(key): str(entry)
                            for key, entry in item.get("environment", {}).items()
                        }
                        if isinstance(item.get("environment"), Mapping)
                        else {}
                    ),
                    fixture_paths=tuple(
                        str(path)
                        for path in item.get("fixture_paths", ())
                        if isinstance(path, str)
                    ),
                    concurrency_mode=str(
                        item.get("concurrency_mode") or "exclusive"
                    ),
                    resource_key=(
                        str(item.get("resource_key"))
                        if item.get("resource_key") is not None
                        else None
                    ),
                    binding_fingerprint=(
                        str(item.get("binding_fingerprint"))
                        if item.get("binding_fingerprint") is not None
                        else None
                    ),
                )
            )
            for item in raw_case.get("bindings", ())
            if isinstance(item, Mapping)
        )
        cases.append(
            ReplayCaseAdaptation(
                case_id=str(raw_case.get("case_id") or ""),
                adapted_task_input=raw_case.get("adapted_task_input"),
                task_input_fingerprint=str(
                    raw_case.get("task_input_fingerprint") or ""
                ),
                dependencies=dependencies,
                bindings=bindings,
                tool_names=tuple(
                    str(item)
                    for item in raw_case.get("tool_names", ())
                    if isinstance(item, str)
                ),
                readiness=str(raw_case.get("readiness") or "unresolved"),
                diagnostics=tuple(
                    str(item)
                    for item in raw_case.get("diagnostics", ())
                    if isinstance(item, str)
                ),
            )
        )
    return ReplayAdaptationBundle(
        schema_version=str(value.get("schema_version") or ""),
        source_workspace_root=str(value.get("source_workspace_root") or ""),
        workspace_seed=str(value.get("workspace_seed") or ""),
        workspace_seed_fingerprint=str(
            value.get("workspace_seed_fingerprint") or ""
        ),
        manifest_path=str(value.get("manifest_path") or ""),
        environment_snapshot_path=str(
            value.get("environment_snapshot_path") or ""
        ),
        environment_fingerprint=str(value.get("environment_fingerprint") or ""),
        cases=tuple(cases),
        adaptation_fingerprint=str(value.get("adaptation_fingerprint") or ""),
        ready=value.get("ready") is True,
        replay_capability=_frozen_replay_capability_from_mapping(
            value.get("replay_capability")
        ),
    )


def _frozen_replay_capability_from_mapping(
    value: Any,
) -> FrozenReplayCapability | None:
    if not isinstance(value, Mapping):
        return None
    services: list[ReplayServiceSpec] = []
    for raw_service in value.get("services", ()):
        if not isinstance(raw_service, Mapping):
            continue
        raw_readiness = raw_service.get("readiness")
        if not isinstance(raw_readiness, Mapping):
            raise ValueError("stored replay service is missing readiness")
        services.append(
            ReplayServiceSpec(
                service_id=str(raw_service.get("service_id") or ""),
                requirement_id=str(raw_service.get("requirement_id") or ""),
                transport=str(raw_service.get("transport") or ""),
                response_fixture=str(
                    raw_service.get("response_fixture") or ""
                ),
                runtime_entrypoint=(
                    str(raw_service.get("runtime_entrypoint"))
                    if raw_service.get("runtime_entrypoint") is not None
                    else None
                ),
                task_entry_path=(
                    str(raw_service.get("task_entry_path"))
                    if raw_service.get("task_entry_path") is not None
                    else None
                ),
                readiness=ReplayReadinessProbe(
                    kind=str(raw_readiness.get("kind") or ""),
                    timeout_seconds=float(
                        raw_readiness.get("timeout_seconds") or 0.0
                    ),
                    path=str(raw_readiness.get("path") or "/"),
                ),
                protocol_probes=tuple(
                    ReplayProtocolProbe(
                        kind=str(raw_probe.get("kind") or ""),
                        timeout_seconds=float(
                            raw_probe.get("timeout_seconds") or 0.0
                        ),
                        path=str(raw_probe.get("path") or "/"),
                        validate_advertised_websockets=(
                            raw_probe.get("validate_advertised_websockets") is True
                        ),
                        request_text=(
                            str(raw_probe.get("request_text"))
                            if raw_probe.get("request_text") is not None
                            else None
                        ),
                        response_contains=(
                            str(raw_probe.get("response_contains"))
                            if raw_probe.get("response_contains") is not None
                            else None
                        ),
                        response_record_id=(
                            str(raw_probe.get("response_record_id"))
                            if raw_probe.get("response_record_id") is not None
                            else None
                        ),
                    )
                    for raw_probe in raw_service.get("protocol_probes", ())
                    if isinstance(raw_probe, Mapping)
                ),
            )
        )

    def files(key: str) -> tuple[FrozenReplayFile, ...]:
        return tuple(
            FrozenReplayFile(
                path=str(item.get("path") or ""),
                sha256=str(item.get("sha256") or ""),
                size=int(item.get("size") or 0),
            )
            for item in value.get(key, ())
            if isinstance(item, Mapping)
        )

    raw_evidence = value.get("evidence_refs")
    evidence_refs = (
        {
            str(key): tuple(
                str(item) for item in entries if isinstance(item, str)
            )
            for key, entries in raw_evidence.items()
            if isinstance(entries, (list, tuple))
        }
        if isinstance(raw_evidence, Mapping)
        else {}
    )
    raw_fixture_evidence = value.get("fixture_evidence_refs")
    fixture_evidence_refs = (
        {
            str(key): tuple(
                str(item) for item in entries if isinstance(item, str)
            )
            for key, entries in raw_fixture_evidence.items()
            if isinstance(entries, (list, tuple))
        }
        if isinstance(raw_fixture_evidence, Mapping)
        else {}
    )
    raw_replacements = value.get("endpoint_replacements")
    replacements = (
        {
            str(key): str(item)
            for key, item in raw_replacements.items()
            if isinstance(key, str) and isinstance(item, str)
        }
        if isinstance(raw_replacements, Mapping)
        else {}
    )
    return FrozenReplayCapability(
        capability_id=str(value.get("capability_id") or ""),
        capability_package_fingerprint=str(
            value.get("capability_package_fingerprint") or ""
        ),
        request_fingerprint=str(value.get("request_fingerprint") or ""),
        frozen_root=str(value.get("frozen_root") or ""),
        handled_requirements=tuple(
            str(item)
            for item in value.get("handled_requirements", ())
            if isinstance(item, str)
        ),
        unhandled_requirements=tuple(
            str(item)
            for item in value.get("unhandled_requirements", ())
            if isinstance(item, str)
        ),
        evidence_refs=evidence_refs,
        fixture_evidence_refs=fixture_evidence_refs,
        fixtures=files("fixtures"),
        runtime_files=files("runtime_files"),
        endpoint_replacements=replacements,
        services=tuple(services),
        deterministic=value.get("deterministic") is True,
        fingerprint=str(value.get("fingerprint") or ""),
        ready=value.get("ready") is True,
        concurrency_mode=str(value.get("concurrency_mode") or "exclusive"),
        resource_key=(
            str(value.get("resource_key"))
            if value.get("resource_key") is not None
            else None
        ),
        binding_fingerprint=(
            str(value.get("binding_fingerprint"))
            if value.get("binding_fingerprint") is not None
            else None
        ),
    )


def _load_variant_result_from_dir(
    variant_dir: Path,
    *,
    base_variant_id: str,
) -> ReplayVariantResult:
    if not variant_dir.exists():
        raise FileNotFoundError(f"stored replay variant not found: {variant_dir}")
    lifecycle_path = variant_dir / "lifecycle.json"
    if lifecycle_path.exists():
        return _load_lifecycle_variant_result(
            variant_dir,
            base_variant_id=base_variant_id,
        )
    repetition_dirs = _stored_repetition_dirs(variant_dir)
    if not repetition_dirs:
        return _load_single_variant_result(
            _effective_repetition_dir(variant_dir),
            variant_id=base_variant_id,
        )

    results = [
        _load_single_variant_result(
            _effective_repetition_dir(path),
            variant_id=(
                base_variant_id
                if len(repetition_dirs) == 1
                else f"{base_variant_id}-{index}"
            ),
        )
        for index, path in enumerate(repetition_dirs, start=1)
    ]
    aggregate_metrics = _load_optional_json_object(variant_dir / "aggregate_metrics.json")
    successful = [result for result in results if result.succeeded]
    selected = successful[-1] if successful else results[-1]
    status = "succeeded" if successful else "failed"
    failure = _load_optional_json_object(variant_dir / "failure.json")
    if status != "succeeded" and failure is None:
        failure = {
            "reason": "one or more replay repetitions failed",
            "failures": [
                result.failure.compatibility_dict()
                for result in results
                if result.failure is not None
            ],
        }
    metrics = dict(aggregate_metrics or {})
    metrics.setdefault("repetition_count", len(results))
    metrics.setdefault("successful_repetition_count", len(successful))
    metrics.setdefault("failed_repetition_count", len(results) - len(successful))
    return ReplayVariantResult(
        variant_id=base_variant_id,
        status=status,
        trajectory=selected.trajectory,
        metrics=metrics,
        stdout_path=selected.stdout_path,
        stderr_path=selected.stderr_path,
        failure=failure,
        repetition_results=tuple(results),
    )


def _stored_repetition_dirs(variant_dir: Path) -> list[Path]:
    dirs = [
        path
        for path in variant_dir.iterdir()
        if path.is_dir() and path.name.isdigit()
    ]
    return sorted(dirs, key=lambda path: int(path.name))


def _effective_repetition_dir(repetition_dir: Path) -> Path:
    retry_dirs = [
        path
        for path in repetition_dir.iterdir()
        if path.is_dir() and path.name.startswith("evidence_retry_")
    ]
    for path in sorted(retry_dirs, key=lambda item: item.name, reverse=True):
        if (path / "trajectory.json").exists() and not (path / "failure.json").exists():
            return path
    return repetition_dir


def _load_single_variant_result(variant_dir: Path, *, variant_id: str) -> ReplayVariantResult:
    if (variant_dir / "lifecycle.json").exists():
        return _load_lifecycle_variant_result(
            variant_dir,
            base_variant_id=variant_id,
        )
    trajectory_payload = _load_json_value(variant_dir / "trajectory.json")
    if not isinstance(trajectory_payload, list):
        raise ValueError(f"stored replay trajectory must be a list: {variant_dir}")
    trajectory = [item for item in trajectory_payload if isinstance(item, Mapping)]
    metrics = _load_optional_json_object(variant_dir / "metrics.json") or {}
    failure = _load_optional_json_object(variant_dir / "failure.json")
    status = "failed" if failure is not None else "succeeded"
    if not trajectory:
        status = "failed"
        failure = failure or {
            "reason": "trajectory_capture_unavailable",
            "detail": "stored replay trajectory is empty",
        }
    metrics.setdefault("repetition_count", 1)
    metrics.setdefault("successful_repetition_count", 1 if status == "succeeded" else 0)
    metrics.setdefault("failed_repetition_count", 0 if status == "succeeded" else 1)
    stdout_path = variant_dir / "stdout.txt"
    stderr_path = variant_dir / "stderr.txt"
    return ReplayVariantResult(
        variant_id=variant_id,
        status=status,
        trajectory=trajectory,
        metrics=metrics,
        stdout_path=str(stdout_path) if stdout_path.exists() else None,
        stderr_path=str(stderr_path) if stderr_path.exists() else None,
        failure=failure,
    )


def _load_lifecycle_variant_result(
    variant_dir: Path,
    *,
    base_variant_id: str,
) -> ReplayVariantResult:
    lifecycle = _load_json_object(variant_dir / "lifecycle.json")
    lifecycle_schema = lifecycle.get("schema_version")
    if lifecycle_schema not in {
        _REPLAY_LIFECYCLE_SCHEMA_V2,
        _REPLAY_LIFECYCLE_SCHEMA_V3,
    }:
        raise ValueError("unsupported stored replay lifecycle schema")
    if (
        lifecycle_schema == _REPLAY_LIFECYCLE_SCHEMA_V3
        and lifecycle.get("repetition_semantics")
        != _PER_MEMBER_REPETITION_SEMANTICS
    ):
        raise ValueError(
            "stored v3 replay lifecycle is missing per-member repetition semantics"
        )
    raw_failure = lifecycle.get("failure")
    failure = (
        ReplayFailureEvent.from_dict(raw_failure)
        if isinstance(raw_failure, Mapping)
        else None
    )
    raw_blocked_by = lifecycle.get("blocked_by")
    blocked_by = tuple(
        ReplayFailureEvent.from_dict(item)
        for item in raw_blocked_by
        if isinstance(item, Mapping)
    ) if isinstance(raw_blocked_by, list) else ()
    trajectory_payload = (
        _load_json_value(variant_dir / "trajectory.json")
        if (variant_dir / "trajectory.json").exists()
        else []
    )
    if not isinstance(trajectory_payload, list):
        raise ValueError(f"stored replay trajectory must be a list: {variant_dir}")
    trajectory = [item for item in trajectory_payload if isinstance(item, Mapping)]
    metrics = _load_optional_json_object(variant_dir / "metrics.json") or {}
    aggregate_metrics = _load_optional_json_object(
        variant_dir / "aggregate_metrics.json"
    )
    if aggregate_metrics is not None:
        metrics = {**dict(metrics), **dict(aggregate_metrics)}
    stdout_path = variant_dir / "stdout.txt"
    stderr_path = variant_dir / "stderr.txt"
    repetition_dirs = _stored_repetition_dirs(variant_dir)
    repetition_results = tuple(
        _load_single_variant_result(
            _effective_repetition_dir(path),
            variant_id=(
                base_variant_id
                if len(repetition_dirs) == 1
                else f"{base_variant_id}-{index}"
            ),
        )
        for index, path in enumerate(repetition_dirs, start=1)
    )
    return ReplayVariantResult(
        variant_id=str(lifecycle.get("variant_id") or base_variant_id),
        status=str(lifecycle.get("status") or ""),
        trajectory=trajectory,
        metrics=metrics,
        stdout_path=str(stdout_path) if stdout_path.exists() else None,
        stderr_path=str(stderr_path) if stderr_path.exists() else None,
        failure=failure,
        blocked_by=blocked_by,
        repetition_results=repetition_results,
    )


def _load_json_object(path: Path) -> Mapping[str, Any]:
    payload = _load_json_value(path)
    if not isinstance(payload, Mapping):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _load_optional_json_object(path: Path) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    return _load_json_object(path)


def _load_json_value(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _positive_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("stored replay repetition counts must be positive")
    return parsed


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def build_paired_replay_dataset(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    candidate: CandidateVariant,
    normalized: NormalizedReplayMembers | None = None,
) -> SelfEvolveDataset:
    normalized = normalized or normalize_replay_members(
        dataset=dataset,
        replay_result=replay_result,
    )
    if not normalized.valid:
        raise ValueError("candidate replay member result contract is invalid")
    if not normalized.members or any(
        not member.candidate.succeeded for member in normalized.members
    ):
        raise ValueError("candidate replay did not succeed")
    if not candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=replay_result,
        normalized=normalized,
    ):
        raise ValueError("candidate replay did not produce comparable paired outcomes")
    member_results = {member.case_id: member for member in normalized.members}
    cases: list[EvalCase] = []
    source_to_replay_case_ids: dict[str, list[str]] = {}
    for case in dataset.cases:
        member_result = member_results.get(case.case_id)
        if member_result is None:
            continue
        baseline_variant = member_result.baseline
        candidate_variant = member_result.candidate
        replay_request = member_result.request
        baseline_trajectory, baseline_trajectory_source = (
            _baseline_comparison_trajectory(case, baseline_variant)
        )
        baseline_outcome = (
            "success"
            if baseline_variant.succeeded
            else (
                "task_failure"
                if _is_task_rollout_capability_failure(
                    baseline_variant.failure
                )
                else _replay_failure_outcome(baseline_variant.failure)
            )
        )
        if not baseline_variant.succeeded:
            baseline_variant = replace(
                baseline_variant,
                trajectory=baseline_trajectory,
                metrics={
                    **dict(baseline_variant.metrics),
                    "replay_outcome": baseline_outcome,
                    "trajectory_source": baseline_trajectory_source,
                },
            )
        baseline_results = _evaluation_repetition_results(baseline_variant)
        candidate_results = _evaluation_repetition_results(candidate_variant)
        replay_case_count = max(len(baseline_results), len(candidate_results))
        for index in range(replay_case_count):
            baseline_result = baseline_results[index % len(baseline_results)]
            candidate_result = candidate_results[index % len(candidate_results)]
            metadata = dict(case.metadata)
            metadata["variant_trajectories"] = {
                "baseline": baseline_result.trajectory,
                candidate.candidate_id: candidate_result.trajectory,
            }
            metadata["replay"] = {
                "source_case_id": case.case_id,
                "independence_unit_id": case.case_id,
                "request": {
                    "run_id": replay_request.run_id,
                    "task_id": replay_request.task_id,
                    "candidate_id": replay_request.candidate_id,
                    "overlay_skill_root": replay_request.overlay_skill_root,
                },
                "baseline": {
                    "status": baseline_variant.status,
                    "outcome": baseline_outcome,
                    "trajectory_source": baseline_trajectory_source,
                    "metrics": _evaluation_replay_metrics(
                        aggregate_metrics=baseline_variant.metrics,
                        repetition_metrics=baseline_result.metrics,
                    ),
                    "aggregate_metrics": dict(baseline_variant.metrics),
                    "failure": (
                        baseline_variant.failure.compatibility_dict()
                        if baseline_variant.failure is not None
                        else None
                    ),
                    "failure_event": (
                        baseline_variant.failure.to_dict()
                        if baseline_variant.failure is not None
                        else None
                    ),
                    "variant_id": baseline_result.variant_id,
                },
                "candidate": {
                    "status": candidate_variant.status,
                    "outcome": "success",
                    "metrics": _evaluation_replay_metrics(
                        aggregate_metrics=candidate_variant.metrics,
                        repetition_metrics=candidate_result.metrics,
                    ),
                    "aggregate_metrics": dict(candidate_variant.metrics),
                    "failure": (
                        candidate_variant.failure.compatibility_dict()
                        if candidate_variant.failure is not None
                        else None
                    ),
                    "failure_event": (
                        candidate_variant.failure.to_dict()
                        if candidate_variant.failure is not None
                        else None
                    ),
                    "variant_id": candidate_result.variant_id,
                },
                "repetition_index": index + 1,
                "replay_case_count": replay_case_count,
            }
            case_id = (
                case.case_id
                if replay_case_count == 1
                else f"{case.case_id}__replay_{index + 1}"
            )
            source_to_replay_case_ids.setdefault(case.case_id, []).append(case_id)
            cases.append(
                EvalCase(
                    case_id=case_id,
                    input=case.input,
                    expected_output=case.expected_output,
                    verification_command=case.verification_command,
                    metadata=metadata,
                    trace_pack=case.trace_pack,
                    source=case.source,
                )
            )

    case_ids = [case.case_id for case in cases]
    split_case_ids = {
        split_name: [
            replay_case_id
            for source_case_id in source_case_ids
            for replay_case_id in source_to_replay_case_ids.get(source_case_id, ())
        ]
        for split_name, source_case_ids in dataset.recipe.splits.items()
    }
    source_trainable_case_ids = (
        dataset.recipe.trainable_case_ids
        or tuple(dataset.recipe.splits.get("train", ()))
    )
    source_held_out_case_ids = (
        dataset.recipe.held_out_case_ids
        or tuple(dataset.recipe.splits.get("held_out", ()))
    )
    trainable_case_ids = tuple(
        replay_case_id
        for source_case_id in source_trainable_case_ids
        for replay_case_id in source_to_replay_case_ids.get(source_case_id, ())
    )
    held_out_case_ids = tuple(
        replay_case_id
        for source_case_id in source_held_out_case_ids
        for replay_case_id in source_to_replay_case_ids.get(source_case_id, ())
    )
    held_out_member_count = sum(
        1 for case_id in source_held_out_case_ids if case_id in source_to_replay_case_ids
    )

    return SelfEvolveDataset(
        cases=tuple(cases),
        recipe=DatasetRecipe(
            source={
                **dict(dataset.recipe.source),
                "paired_replay": True,
                "paired_replay_dataset_schema": (
                    "aworld.self_evolve.paired_replay_dataset.v1"
                ),
                "candidate_id": candidate.candidate_id,
                "original_case_count": len(dataset.cases),
                "replay_case_count": len(cases),
                "member_replay_count": len(member_results) or 1,
                "held_out_member_count": held_out_member_count,
            },
            split_seed=dataset.recipe.split_seed,
            splits=(
                split_case_ids
                if any(split_case_ids.values())
                else {"train": case_ids, "validation": [], "held_out": []}
            ),
            synthetic_generation_policy=dataset.recipe.synthetic_generation_policy,
            trainable_case_ids=(
                trainable_case_ids
                if source_trainable_case_ids or source_held_out_case_ids
                else tuple(case_ids)
            ),
            held_out_case_ids=held_out_case_ids,
        ),
    )


def _evaluation_replay_metrics(
    *,
    aggregate_metrics: Mapping[str, Any],
    repetition_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    metrics = dict(aggregate_metrics)
    for key in (
        "evidence_bundle_path",
        "evidence_bundle_present",
        "evidence_bundle_valid",
        "evidence_bundle_entry_count",
    ):
        if key in repetition_metrics:
            metrics[key] = repetition_metrics[key]
    return metrics


def _evaluation_repetition_results(
    result: ReplayVariantResult,
) -> tuple[ReplayVariantResult, ...]:
    successful = tuple(item for item in result.repetition_results if item.succeeded)
    if successful:
        return successful
    return (result,)
