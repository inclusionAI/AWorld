from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import inspect
import json
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, fields as dataclass_fields, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from aworld.core.context.amni.local import LocalIsolatedApplicationContext
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.memory.tool_call_compaction import REPLAY_COMPACTED_ARGUMENT_FAILURE
from aworld.runners.batch import (
    DeterministicTaskBatchExecutor,
    TaskBatchItem,
    TaskResourceClaim,
)
from aworld.self_evolve.concurrency import SelfEvolveConcurrencyPolicy
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    is_framework_meta_trace_pack,
)
from aworld.self_evolve.failure_events import (
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
    causal_failure_events,
)
from aworld.self_evolve.replay_adaptation import (
    REPLAY_ARTIFACT_PLACEHOLDER,
    REPLAY_WORKSPACE_PLACEHOLDER,
    ReplayAdaptationBundle,
    ReplayAdapterBinding,
    ReplayCaseAdaptation,
    ReplayDependency,
    validate_replay_binding_concurrency,
    materialize_replay_workspace,
)
from aworld.self_evolve.replay_capability import (
    FrozenReplayCapability,
    build_replay_sandboxed_command,
    FrozenReplayFile,
    ReplayProtocolProbe,
    ReplayReadinessProbe,
    ReplayServiceSpec,
    replay_payload_contains_expected_value,
    replay_process_memory_bytes,
    replay_process_resource_limiter,
    verify_frozen_replay_capability,
)
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.types import CandidateVariant, DatasetRecipe, SelfEvolveTargetRef, to_json_dict

_EVIDENCE_RETRY_LIMIT = 1
_SYNTHETIC_EVIDENCE_EXCERPT_CHARS = 4000
_MAX_METADATA_EVIDENCE_CHARS = 16_384
_REPLAY_SERVICE_PROTOCOL_TRACE_NAME = "protocol_trace.jsonl"
_MAX_REPLAY_SERVICE_PROTOCOL_TRACE_BYTES = 64 * 1024
_MAX_REPLAY_SERVICE_PROTOCOL_TRACE_EXCERPT_CHARS = 4_000
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
    agent: str | None = None
    timeout_seconds: float | None = None
    max_steps: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    baseline_repetitions: int = 1
    candidate_repetitions: int = 1
    replay_adaptation: ReplayAdaptationBundle | None = None
    dataset_fingerprint: str | None = None
    baseline_skill_fingerprint: str | None = None
    adaptation_fingerprint: str | None = None
    workspace_seed_fingerprint: str | None = None
    task_input_fingerprint: str | None = None
    verified_candidate_package_fingerprint: str | None = None
    repetition_semantics: str = _PER_MEMBER_REPETITION_SEMANTICS


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
            failure = ReplayFailureEvent.from_legacy_mapping(failure)
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

    def __post_init__(self) -> None:
        if any(
            not isinstance(event, ReplayFailureEvent)
            for event in self.artifact_failure_events
        ):
            raise ValueError(
                "artifact_failure_events must contain replay failure events"
            )

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
                "service_logical_ids": json.dumps(
                    sorted(service.service_id for service in replay_capability.services),
                    separators=(",", ":"),
                ),
                "service_startup_status": "ready",
                "service_cleanup_status": "stopped",
            }
            for variant in (baseline, candidate):
                if any(
                    variant.metrics.get(key) != value
                    for key, value in capability_expected.items()
                ):
                    return False
            if replay_capability.services:
                baseline_endpoints = _service_endpoint_values(baseline)
                candidate_endpoints = _service_endpoint_values(candidate)
                if (
                    not baseline_endpoints
                    or not candidate_endpoints
                    or baseline_endpoints & candidate_endpoints
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
            if variant.status is ReplayExecutionStatus.FAILED and variant.failure is not None:
                owner_counts[variant.failure.owner] += 1
            if variant.status is ReplayExecutionStatus.BLOCKED:
                blocked_variant_count += 1
                member_blocked = True
            elif variant.status is ReplayExecutionStatus.NOT_RUN:
                not_run_variant_count += 1
        if member_blocked:
            blocked_member_count += 1
        if baseline.status is ReplayExecutionStatus.FAILED:
            baseline_execution_failure_count += 1
            if baseline.failure is not None and baseline.failure.owner in {
                FailureOwner.INFRASTRUCTURE,
                FailureOwner.FRAMEWORK,
            }:
                infrastructure_failure_count += 1
        if candidate.status is ReplayExecutionStatus.FAILED:
            candidate_execution_failure_count += 1
            candidate_failure_count += 1
        if not candidate.succeeded:
            continue
        if baseline.succeeded:
            strict_pair_count += 1
            continue
        if baseline.failure is not None and (
            baseline.failure.owner is FailureOwner.TASK
            or (
                baseline.failure.owner is FailureOwner.CANDIDATE
                and baseline.failure.stage is FailureStage.TASK_ROLLOUT
            )
        ):
            trajectory, _ = _baseline_comparison_trajectory(case, baseline)
            if trajectory:
                task_failure_pair_count += 1
                continue
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
        else ReplayFailureEvent.from_legacy_mapping(failure)
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
    raw_stage = str(payload.get("failure_stage") or "")
    failure_type = str(payload.get("type") or "")
    if service_preflight:
        stage = FailureStage.CAPABILITY_PREFLIGHT
    elif raw_stage == FailureStage.TASK_ROLLOUT.value:
        stage = FailureStage.TASK_ROLLOUT
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
            if stage is FailureStage.TASK_ROLLOUT
            else FailureScope.CANDIDATE
        )
    elif owner is FailureOwner.TASK:
        scope = FailureScope.MEMBER
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
    return ReplayFailureEvent(
        event_id=f"replay-event-{uuid.uuid4().hex}",
        code=code,
        owner=owner,
        stage=stage,
        scope=scope,
        repairable=legacy.repairable,
        category=legacy.category,
        summary=legacy.summary,
        diagnostics=legacy.diagnostics,
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


class CandidateReplayBackend(Protocol):
    async def replay_candidate(
        self,
        request: CandidateReplayRequest,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayResult:
        """Replay baseline/candidate variants and return their trajectories."""


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
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    adaptation_fingerprint: str | None = None
    workspace_seed_fingerprint: str | None = None
    task_input_fingerprint: str | None = None
    dataset_fingerprint: str | None = None
    baseline_skill_fingerprint: str | None = None
    adapter_determinism: str | None = None
    isolated_workspace_path: str | None = None
    replay_capability_id: str | None = None
    capability_package_fingerprint: str | None = None
    frozen_capability_fingerprint: str | None = None
    service_runtime_fingerprint: str | None = None
    service_logical_ids: str | None = None
    service_endpoint: str | None = None
    service_startup_status: str | None = None


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

    async def execute(self) -> ReplayVariantResult:
        return await self.backend._run_variant_with_evidence_retries(
            self.request,
            variant_id=self.variant_id,
            skill_root=self.skill_root,
            artifact_dir=self.artifact_dir,
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
        required = {"direction", "sequence", "kind", "fields", "correlation"}
        missing = sorted(required.difference(record))
        if missing:
            raise ReplayServiceProtocolError(
                "skill runtime protocol_trace.jsonl record is missing required "
                f"summary fields: {', '.join(missing)}"
            )
        if not isinstance(record.get("fields"), list) or not isinstance(
            record.get("correlation"), Mapping
        ):
            raise ReplayServiceProtocolError(
                "skill runtime protocol_trace.jsonl fields must be a list and "
                "correlation must be an object"
            )
        direction = str(record.get("direction") or "").strip().lower()
        if direction in {"in", "inbound", "received", "receive", "recv"}:
            directions.add("in")
        elif direction in {"out", "outbound", "emitted", "emit", "send", "sent"}:
            directions.add("out")
        else:
            raise ReplayServiceProtocolError(
                "skill runtime protocol_trace.jsonl direction must describe a "
                "received or emitted interaction"
            )
        record_count += 1
    if record_count == 0:
        raise ReplayServiceProtocolError(
            "skill runtime wrote an empty protocol_trace.jsonl"
        )
    if directions != {"in", "out"}:
        raise ReplayServiceProtocolError(
            "skill runtime protocol_trace.jsonl must record both received and "
            "emitted interactions"
        )


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
        except OSError:
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
    if result.failure is None:
        return result
    traces = _replay_service_protocol_diagnostics(artifact_dir)
    if not traces:
        return result
    failure = dict(result.failure)
    current_diagnostics = failure.get("diagnostics")
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
    """Attribute a post-response timeout to reusable candidate behavior."""

    failure = result.failure
    if (
        variant_id == "baseline"
        or not isinstance(failure, Mapping)
        or failure.get("type") != "TimeoutExpired"
        or failure.get("outcome") is not None
    ):
        return result
    completed_operations = _completed_replay_data_plane_operations(failure)
    if not completed_operations:
        return result
    classified = {
        **dict(failure),
        "outcome": "candidate_failure",
        "failure_class": "candidate_task_behavior",
        "failure_stage": "task_rollout",
        "repairable": True,
        "completed_data_plane_operations": list(completed_operations),
    }
    return replace(result, failure=classified)


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


class AWorldCliCandidateReplayBackend:
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
    ) -> CandidateReplayResult:
        if not _has_authoritative_per_member_repetitions(request):
            raise ValueError(
                "candidate replay execution requires explicit per-member "
                "repetition semantics"
            )
        replay_dir = (
            Path(request.workspace_root)
            / ".aworld"
            / "self_evolve"
            / _safe_path(request.run_id)
            / "replay"
            / _safe_path(candidate.candidate_id)
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
        for case in replay_cases:
            adapted_task_input = _adapted_task_input(request, case)
            member_request = replace(
                request,
                task_id=case.case_id,
                task_input=adapted_task_input,
                task_input_fingerprint=_adapted_task_input_fingerprint(request, case),
                baseline_replay_dir=_member_baseline_replay_dir(
                    request.baseline_replay_dir,
                    case.case_id,
                ),
                baseline_repetitions=member_baseline_repetitions,
                candidate_repetitions=member_candidate_repetitions,
            )
            member_dir = members_root / _member_artifact_name(case.case_id)
            member_dir.mkdir(parents=True, exist_ok=True)
            _write_json(member_dir / "request.json", member_request)
            prepared_members.append((case, member_request, member_dir))

        baselines: list[ReplayVariantResult] = []
        for _, member_request, member_dir in prepared_members:
            if candidate_blocking_event is not None:
                baseline = _blocked_variant_result(
                    "baseline", blocked_by=candidate_blocking_event
                )
                _persist_variant_lifecycle(member_dir / "baseline", baseline)
            else:
                baseline = await self._load_or_run_baseline(
                    member_request,
                    candidate=candidate,
                    replay_dir=member_dir,
                )
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
            baselines.append(baseline)

        for (case, member_request, member_dir), baseline in zip(
            prepared_members, baselines, strict=True
        ):
            blocking_event = candidate_blocking_event
            if (
                baseline.status is ReplayExecutionStatus.FAILED
                and _baseline_failure_blocks_candidate(baseline.failure)
            ):
                assert baseline.failure is not None
                blocking_event = baseline.failure
            if blocking_event is not None:
                candidate_result = _blocked_variant_result(
                    candidate.candidate_id, blocked_by=blocking_event
                )
                _persist_variant_lifecycle(
                    member_dir / _safe_path(candidate.candidate_id), candidate_result
                )
            else:
                candidate_result = await self._run_repetitions(
                    member_request,
                    base_variant_id=candidate.candidate_id,
                    skill_root=member_request.overlay_skill_root,
                    artifact_dir=member_dir / _safe_path(candidate.candidate_id),
                    repetitions=member_request.candidate_repetitions,
                )
                if (
                    candidate_result.status is ReplayExecutionStatus.FAILED
                    and candidate_result.failure is not None
                    and candidate_result.failure.scope in {
                        FailureScope.CANDIDATE,
                        FailureScope.SHARED_RUN,
                    }
                    and candidate_result.failure.stage
                    is not FailureStage.TASK_ROLLOUT
                ):
                    candidate_blocking_event = candidate_result.failure
            member_items.append(
                CandidateReplayMemberResult(
                    case_id=case.case_id,
                    request=member_request,
                    baseline=baseline,
                    candidate=candidate_result,
                )
            )
        member_results = tuple(member_items)
        _write_json(
            members_root / "manifest.json",
            {
                "schema_version": _MEMBER_REPLAY_SCHEMA_V3,
                "repetition_semantics": _PER_MEMBER_REPETITION_SEMANTICS,
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

    async def _load_or_run_baseline(
        self,
        request: CandidateReplayRequest,
        *,
        candidate: CandidateVariant,
        replay_dir: Path,
    ) -> ReplayVariantResult:
        if request.baseline_replay_dir and _stored_baseline_matches_request(request):
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
            )
        return baseline
    async def _run_repetitions(
        self,
        request: CandidateReplayRequest,
        *,
        base_variant_id: str,
        skill_root: str | None,
        artifact_dir: Path,
        repetitions: int,
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
    ) -> ReplayVariantResult:
        attempts: list[ReplayVariantResult] = []
        for attempt_index in range(1, _EVIDENCE_RETRY_LIMIT + 2):
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
            result = await self._run_variant(
                request,
                variant_id=attempt_variant_id,
                skill_root=skill_root,
                artifact_dir=attempt_dir,
            )
            attempts.append(result)
            if not _is_evidence_quality_failure(result):
                return _merge_replay_attempt_metrics(
                    result,
                    attempts=attempts,
                    canonical_variant_id=variant_id,
                )
            if attempt_index <= _EVIDENCE_RETRY_LIMIT:
                logger.info(
                    "self_evolve.replay.evidence_retry "
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
        service_session: _ReplayServiceSession | None = None
        service_failure: Mapping[str, Any] | None = None
        service_cleanup_status = "not_required"
        service_cleanup_failure: Mapping[str, Any] | None = None
        replay_capability = (
            request.replay_adaptation.replay_capability
            if request.replay_adaptation is not None
            else None
        )
        if replay_capability is not None:
            try:
                service_session = await _start_replay_services(
                    replay_capability,
                    artifact_dir=artifact_dir,
                )
                endpoint_urls = {
                    source: service_session.endpoints[service_id]
                    for source, service_id in replay_capability.endpoint_replacements.items()
                }
                task_input = _replace_replay_endpoints(task_input, endpoint_urls)
                environment.update(service_session.environment)
            except Exception as exc:
                has_candidate_runtime = any(
                    service.transport == "skill_runtime"
                    for service in replay_capability.services
                )
                service_failure_details: dict[str, Any] = {
                    "type": type(exc).__name__,
                    "reason": str(exc),
                    "outcome": (
                        "candidate_failure"
                        if has_candidate_runtime
                        and isinstance(
                            exc,
                            (
                                ReplayServiceProtocolError,
                                RuntimeError,
                                TimeoutError,
                                ValueError,
                            ),
                        )
                        else "infrastructure_failure"
                    ),
                }
                fixture_summaries = _replay_capability_fixture_summaries(
                    replay_capability
                )
                if fixture_summaries:
                    service_failure_details["diagnostics"] = {
                        "replay_fixture_summaries": fixture_summaries,
                    }
                service_failure = service_failure_details
        execution_request = ReplayExecutionRequest(
            variant_id=variant_id,
            task_id=request.task_id,
            candidate_id=request.candidate_id,
            workspace_root=workspace_root,
            task_input=task_input,
            task_text=_task_text(task_input),
            skill_root=skill_root,
            artifact_dir=str(artifact_dir),
            skill_names=(
                (request.target.target_id,)
                if skill_root and request.target.target_id
                else ()
            ),
            agent=request.agent,
            timeout_seconds=request.timeout_seconds,
            max_steps=request.max_steps,
            max_tokens=request.max_tokens,
            max_cost_usd=request.max_cost_usd,
            environment=environment,
            adaptation_fingerprint=adaptation_fingerprint,
            workspace_seed_fingerprint=workspace_seed_fingerprint,
            task_input_fingerprint=task_input_fingerprint,
            dataset_fingerprint=request.dataset_fingerprint,
            baseline_skill_fingerprint=request.baseline_skill_fingerprint,
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
            service_startup_status=(
                "ready"
                if service_session is not None
                else "failed"
                if replay_capability is not None
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
                "reason": "trajectory_capture_unavailable",
                "detail": "replay executor succeeded but did not return trajectory evidence",
            }
        evidence_failure = _evidence_quality_failure(metrics)
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
        "adaptation_fingerprint",
        "workspace_seed_fingerprint",
        "task_input_fingerprint",
    )
    if any(getattr(request, key) is None for key in provenance_keys):
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
    return (
        baseline.succeeded
        and _successful_repetition_count(baseline)
        == request.baseline_repetitions
    )


def _successful_repetition_count(result: ReplayVariantResult) -> int:
    count = result.metrics.get("successful_repetition_count")
    if isinstance(count, (int, float)):
        return int(count)
    if result.repetition_results:
        return sum(1 for repetition in result.repetition_results if repetition.succeeded)
    return 1 if result.succeeded else 0


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
) -> subprocess.CompletedProcess[str]:
    """Run a replay CLI process while supervising terminal task diagnostics."""

    if not capture_output:
        raise ValueError("replay CLI supervision requires captured output")
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        text=text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=start_new_session,
        env=dict(env),
    )
    deadline = time.monotonic() + max(float(timeout), 0.0)
    while True:
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
            return subprocess.CompletedProcess(
                list(command),
                process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        except subprocess.TimeoutExpired as exc:
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


class AWorldCliReplayExecutor:
    _DEFAULT_TOOL_CALL_LIMIT = 24
    _DEFAULT_RESERVED_OUTPUT_TOKENS = 4096

    async def __call__(self, request: ReplayExecutionRequest) -> ReplayExecutionResult:
        artifact_dir = Path(request.artifact_dir)
        evidence_manifest = artifact_dir / "evidence_manifest.jsonl"
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
                artifact_dir=artifact_dir,
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
                "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR": str(artifact_dir),
                "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST": str(evidence_manifest),
                "AWORLD_LOG_PATH": str(artifact_dir / "logs"),
                "AWORLD_TRAJECTORY_LOG_DISABLED": "1",
                "AWORLD_TOOL_CALL_LIMIT": str(self._DEFAULT_TOOL_CALL_LIMIT),
                "AWORLD_PROMPT_BUDGET_RESERVED_OUTPUT_TOKENS": str(
                    self._DEFAULT_RESERVED_OUTPUT_TOKENS
                ),
                "AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES": "AWORLD_REPLAY_",
            }
        )
        execution_started_at = time.time()
        try:
            completed = await asyncio.to_thread(
                _run_replay_cli,
                command,
                cwd=request.workspace_root,
                text=True,
                capture_output=True,
                timeout=request.timeout_seconds,
                start_new_session=True,
                env=execution_environment,
                artifact_dir=artifact_dir,
                execution_started_at=execution_started_at,
                replay_environment=request.environment,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _text_output(exc.stdout)
            stderr = _text_output(exc.stderr)
            evidence_metrics = _replay_evidence_metrics(
                stdout=stdout,
                stderr=stderr,
                trajectory=[],
                artifact_dir=artifact_dir,
                evidence_manifest=evidence_manifest,
                workspace_root=Path(request.workspace_root),
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
            process_diagnostics = _bounded_process_output_diagnostics(
                stdout=stdout,
                stderr=stderr,
                workspace_root=Path(request.workspace_root),
                artifact_dir=artifact_dir,
                since=execution_started_at,
            )
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
                        **process_diagnostics,
                    },
                    metrics=evidence_metrics,
                )
            if _has_valid_artifact_backed_timeout_evidence(evidence_metrics):
                metrics = {
                    "trajectory_capture_mode": "artifact_manifest",
                    "timeout_recovered_with_artifact_evidence": True,
                    **evidence_metrics,
                }
                return ReplayExecutionResult(
                    status="succeeded",
                    trajectory=_artifact_manifest_trajectory(
                        request,
                        metrics=metrics,
                    ),
                    stdout=stdout,
                    stderr=stderr,
                    metrics=metrics,
                )
            failure: dict[str, Any] = {
                "type": "TimeoutExpired",
                "reason": "replay timed out",
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
            )
        finally:
            shutil.rmtree(runtime_root, ignore_errors=True)

        stdout = _text_output(completed.stdout)
        stderr = _text_output(completed.stderr)
        trajectory_payload = _extract_trajectory_payload_from_stdout(stdout)
        trajectory = trajectory_payload["trajectory"]
        capture_mode = trajectory_payload["trajectory_capture_mode"]
        evidence_metrics = _replay_evidence_metrics(
            stdout=stdout,
            stderr=stderr,
            trajectory=trajectory,
            artifact_dir=artifact_dir,
            evidence_manifest=evidence_manifest,
            workspace_root=Path(request.workspace_root),
        )
        metrics = {
            "returncode": completed.returncode,
            "trajectory_capture_mode": capture_mode,
            **evidence_metrics,
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
        if completed.returncode == 0 and trajectory and capture_mode != "task_response":
            return ReplayExecutionResult(
                status="failed",
                trajectory=trajectory,
                stdout=stdout,
                stderr=stderr,
                metrics=metrics,
                failure={
                    "reason": "trajectory_capture_mode_unsupported",
                    "detail": "self-evolve replay requires TaskResponse.trajectory evidence",
                    "trajectory_capture_mode": capture_mode,
                },
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
        evidence_failure = _evidence_quality_failure(metrics)
        if evidence_failure is not None:
            return ReplayExecutionResult(
                status="failed",
                trajectory=trajectory,
                stdout=stdout,
                stderr=stderr,
                metrics=metrics,
                failure=evidence_failure,
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
    max_tokens: int | None = None,
    max_cost_usd: float | None = None,
    baseline_repetitions: int = 1,
    candidate_repetitions: int = 1,
    baseline_replay_dir: str | Path | None = None,
    replay_adaptation: ReplayAdaptationBundle | None = None,
    verified_candidate_package_fingerprint: str | None = None,
) -> CandidateReplayRequest:
    if not dataset.cases:
        raise ValueError("candidate replay requires at least one eval case")
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
        task_input=task_input,
        agent=agent,
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
        baseline_repetitions=baseline_repetitions,
        candidate_repetitions=candidate_repetitions,
        replay_adaptation=replay_adaptation,
        dataset_fingerprint=replay_dataset_fingerprint(dataset),
        baseline_skill_fingerprint=candidate.target_fingerprint,
        adaptation_fingerprint=adaptation_fingerprint,
        workspace_seed_fingerprint=workspace_seed_fingerprint,
        task_input_fingerprint=task_input_fingerprint,
        verified_candidate_package_fingerprint=(
            verified_candidate_package_fingerprint
        ),
    )


def _adapted_task_input(request: CandidateReplayRequest, case: EvalCase) -> Any:
    if request.replay_adaptation is None:
        return case.input
    return request.replay_adaptation.case(case.case_id).adapted_task_input


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
    # Read the operation-indexed response evidence for every preflight.  Strict
    # task-plane probes use the values as acceptance requirements; ordinary
    # probes use them only to classify compiler/runtime selector drift without
    # exposing recorded payloads in diagnostics.
    recorded_response_values = _replay_capability_recorded_response_values(
        capability
    )
    fixture_service = Path(__file__).with_name("fixture_service.py").resolve()
    try:
        for service in capability.services:
            port = _reserve_loopback_port()
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
            service_environment["AWORLD_REPLAY_FIXTURE_PATH"] = str(fixture_path)
            service_dir = service_logs / _safe_path(service.service_id)
            service_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = service_dir / "stdout.txt"
            stderr_path = service_dir / "stderr.txt"
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
                    preexec_fn=replay_process_resource_limiter(
                        max_file_bytes=8 * 1024 * 1024,
                        max_memory_bytes=512 * 1024 * 1024,
                        cpu_seconds=600,
                    ),
                )
            except Exception:
                stdout_handle.close()
                stderr_handle.close()
                raise
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
                    timeout_seconds=service.readiness.timeout_seconds,
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
                    diagnostic_recorded_response_values = tuple(
                        value
                        for values in fixture_operation_values.values()
                        for value in values
                    )
                    required_recorded_response_values: tuple[str, ...] = ()
                    if require_recorded_response:
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
                    _validate_replay_service_protocol_trace(
                        service_scratch / _REPLAY_SERVICE_PROTOCOL_TRACE_NAME
                    )
            except Exception as exc:
                raise _replay_service_failure_with_stderr(
                    exc,
                    stderr_path=stderr_path,
                ) from exc
            endpoints[service.service_id] = endpoint
            environment[
                "AWORLD_REPLAY_ENDPOINT_"
                + re.sub(r"[^A-Za-z0-9]+", "_", service.service_id).strip("_").upper()
            ] = endpoint
    except Exception:
        await session.stop()
        raise
    session.endpoints = endpoints
    session.environment = environment
    return session


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
    session = await _start_replay_services(
        capability,
        artifact_dir=resolved_artifact_dir,
        required_nonempty_probe_operations=required_nonempty_probe_operations,
        required_recorded_probe_operations=required_recorded_probe_operations,
        integrity_capability=integrity_capability,
    )
    try:
        return dict(session.endpoints)
    finally:
        await session.stop()


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
        return ReplayServiceProtocolError(message)
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


async def _wait_for_replay_service(
    process: subprocess.Popen[Any],
    *,
    host: str,
    port: int,
    kind: str,
    path: str,
    timeout_seconds: float,
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
            raise RuntimeError(
                f"replay service exited before readiness (exit={process.returncode})"
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
                raise
            await asyncio.sleep(0.02)
    raise TimeoutError(
        f"replay service readiness timed out after {timeout_seconds}s: {last_error}"
    )


class ReplayServiceProtocolError(OSError):
    pass


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
                raise OSError("HTTP readiness probe returned an invalid response")
            status_line = response.split(b"\r\n", 1)[0]
            if b" 2" not in status_line:
                raise OSError("HTTP readiness probe returned a non-success status")
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
    if required_matches and sum(
        1
        for value in recorded_values
        if replay_payload_contains_expected_value(value, match_payload)
    ) < required_matches:
        raise ReplayServiceProtocolError(
            "HTTP data-plane probe must return surrounding recorded response context"
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
            "advertised WebSocket handshake requires HTTP/1.1"
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
    if required_matches and sum(
        1
        for value in recorded_values
        if _protocol_result_contains(result, value)
    ) < required_matches:
        raise ReplayServiceProtocolError(
            "task-plane probe must return surrounding recorded response context"
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

    local_baseline = member_root / "baseline"
    if _stored_replay_variant_succeeded(local_baseline):
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
    if _stored_replay_variant_succeeded(baseline_dir):
        return str(baseline_dir)
    return None


def _stored_replay_variant_succeeded(variant_dir: Path) -> bool:
    if not variant_dir.is_dir():
        return False
    try:
        result = _load_variant_result_from_dir(
            variant_dir,
            base_variant_id="baseline",
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return False
    return result.succeeded


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
    marker = (".aworld", "self_evolve", "drafts", "skills")
    return any(
        normalized_parts[index : index + len(marker)] == marker
        for index in range(0, len(normalized_parts) - len(marker) + 1)
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
- Preserve the original user task above, but execute it with artifact-first evidence handling.
- Do not stream large raw tool outputs, full pages, full documents, large JSON, or long logs directly into the conversation.
- Persist large or unknown-size source material under this exact artifact directory before inspecting or summarizing it: {artifact_dir}
- Redirect large or unknown-size responses directly to an artifact file. A line-count limit such as `head -N` is not a byte bound because JSON, HTML, and logs may contain one very large line; inspect only an explicit byte-bounded excerpt or selected structured fields from the saved artifact.
- Also export or use AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR={artifact_dir} when invoking tools that can receive environment variables.
- Append one JSON object per evidence source to this exact replay evidence_manifest.jsonl file: {evidence_manifest}
- Serialize each manifest object compactly on one physical line (for example with json.dumps and no indentation), then parse that line once before appending it; do not append pretty-printed multi-line JSON.
- Also export or use AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST={evidence_manifest} when invoking tools that can receive environment variables.
- Each manifest object must include source_id, extraction_method, and the bounded excerpt or field list used for the final answer.
- File-backed evidence must include artifact_path. Non-file operation evidence must instead use evidence_type="metadata" with a structured metadata object; never place job IDs, status text, or multiple values into artifact_path.
- Emit only bounded structured summaries with source identifiers, locations, and short excerpts.
- If a tool result is compacted, truncated, schema-invalid, or too large to inspect and no valid artifact-backed evidence bundle, manifest entry, or bounded extract exists, treat that result as unusable evidence and retry with a narrower extraction strategy before answering.
- Keep a concise evidence ledger mapping important final-answer claims to non-compacted extracts or artifact references.
- For bounded replay validation, prefer the smallest representative evidence path that can verify the task contract; cap slow external collection once at least one valid artifact-backed sample exists, and report the actual collected counts rather than padding them.
- After the first successful structured extraction, immediately persist replay artifacts and a manifest entry before any further collection attempts.
- Once the requested output artifact and a valid evidence manifest exist, stop evidence collection and return the final answer with only the artifact path, counts, and evidence ledger requested by the task.
- Before finalizing, perform a claim-by-claim check and omit claims that are not supported by non-compacted evidence captured in the trajectory.
""".strip()


_REPLAY_RUNTIME_POLICY = """
Self-evolve replay runtime contract:
- Preserve the original task's authorization boundary. Task-plane operations required by the original task are allowed, including task-requested reads, writes, navigation, submissions, and artifact creation.
- Treat prerequisites not created by this replay as externally managed and attach-only by default.
- Do not terminate, restart, reconfigure, or replace externally managed prerequisites solely to make replay succeed.
- Do not copy or substitute credentials, sessions, profiles, or private state from the host solely to repair a missing prerequisite.
- Do not override the supplied HOME, TMPDIR, XDG_*, or framework runtime-root environment variables. Keep subprocess state in those isolated roots or the replay artifact directory.
- Only endpoints supplied through AWORLD_REPLAY_ENDPOINT_* are inside the replay dependency boundary. Do not enumerate or connect to any other loopback port, original endpoint, host service, or externally running process as a fallback.
- If a supplied replay endpoint does not implement the required protocol, report a replay capability mismatch; do not bypass it by discovering another host endpoint.
- On the first terminal protocol signal from a supplied endpoint (for example a CDP/WebSocket protocol error, closed response channel, or required-field mismatch), persist one bounded diagnostic artifact and immediately return a replay capability mismatch. Do not retry alternate URL forms or inspect host ports.
- If the original task explicitly authorizes a control-plane operation, that operation is allowed within the stated scope. Resources created by this replay may also be managed and cleaned up by this replay.
- Probe prerequisite compatibility using bounded, non-mutating checks. If the required prerequisite remains unavailable, fail the replay with a prerequisite-unavailable reason instead of repairing the host environment.
- Keep all replay-created files inside the workspace or replay artifact directory unless the original task explicitly names another output location.
- Stop retrying a failed execution path when it cannot produce new evidence; switch once to a materially different bounded strategy or fail with the observed reason.
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
    for line in reversed(stdout.splitlines()):
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
    manifest_valid = manifest_metrics.get("evidence_manifest_valid") is True
    manifest_invalid_count = manifest_metrics.get("evidence_manifest_invalid_entry_count")
    manifest_fully_valid = manifest_valid and not (
        isinstance(manifest_invalid_count, (int, float)) and manifest_invalid_count > 0
    )
    metrics = {
        "evidence_compacted": compacted,
        "evidence_strategy_passed": (not compacted) or manifest_fully_valid,
        "evidence_compaction_signals": signals,
        **manifest_metrics,
    }
    if replay_compacted_argument_blocked:
        metrics["replay_compacted_argument_blocked"] = True
    return metrics


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
    metrics: dict[str, Any] = {
        "evidence_manifest_path": str(evidence_manifest),
        "evidence_manifest_present": evidence_manifest.exists(),
        "evidence_manifest_valid": False,
        "evidence_manifest_entry_count": 0,
    }
    if not evidence_manifest.exists():
        return metrics
    entries: list[Mapping[str, Any]] = []
    invalid_reasons: list[str] = []
    archived_entry_count = 0
    manifest_text = evidence_manifest.read_text(
        encoding="utf-8",
        errors="replace",
    )
    for line_number, entry, decode_error in _decode_evidence_manifest_stream(
        manifest_text
    ):
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
    metrics["evidence_manifest_entry_count"] = len(entries)
    metrics["evidence_manifest_valid"] = bool(entries)
    if archived_entry_count:
        metrics["evidence_manifest_archived_entry_count"] = archived_entry_count
    if invalid_reasons:
        metrics["evidence_manifest_invalid_entry_count"] = len(invalid_reasons)
    if invalid_reasons:
        metrics["evidence_manifest_invalid_reasons"] = invalid_reasons
    bundle_metrics = _write_evidence_bundle(
        artifact_dir=artifact_dir,
        evidence_manifest=evidence_manifest,
        entries=entries,
        invalid_reasons=invalid_reasons,
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
) -> dict[str, Any]:
    if artifact_dir is None:
        return {}
    bundle_path = artifact_dir / "evidence_bundle.json"
    bundle = {
        "format": "aworld.self_evolve.evidence_bundle",
        "version": 1,
        "manifest_path": str(evidence_manifest),
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
        raw = artifact_path.read_text(encoding="utf-8", errors="replace")
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


def _evidence_quality_failure(metrics: Mapping[str, Any]) -> dict[str, Any] | None:
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


def _artifact_manifest_trajectory(
    request: ReplayExecutionRequest,
    *,
    metrics: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    meta = {
        "trajectory_capture_mode": "artifact_manifest",
    }
    manifest_path = metrics.get("evidence_manifest_path")
    if isinstance(manifest_path, str) and manifest_path:
        meta["evidence_manifest_path"] = manifest_path
    bundle_path = metrics.get("evidence_bundle_path")
    if isinstance(bundle_path, str) and bundle_path:
        meta["evidence_bundle_path"] = bundle_path
    return [
        {
            "state": {"input": request.task_input},
            "action": {
                "content": "Replay completed from artifact-backed evidence manifest.",
                "is_agent_finished": "True",
            },
            "reward": {"status": "ok"},
            "meta": meta,
        }
    ]


def _is_evidence_quality_failure(result: ReplayVariantResult) -> bool:
    failure = result.failure
    return (
        isinstance(failure, ReplayFailureEvent)
        and failure.code == "evidence_quality_failed"
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
        "evidence_retry_count": len(attempts) - 1,
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


def _safe_path(value: str) -> str:
    safe = "".join(
        character
        for character in value
        if character.isalnum() or character in {"-", "_", "."}
    ).strip(".")
    return safe or "default"


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
    evidence_strategy_passed_values: list[bool] = []
    evidence_bundle_valid_values: list[bool] = []
    latest_evidence_bundle_path: str | None = None
    provenance_values: dict[str, list[str]] = {
        key: [] for key in _REPLAY_PROVENANCE_METRIC_KEYS
    }
    for result in results:
        for key, value in result.metrics.items():
            if key in provenance_values and isinstance(value, str):
                provenance_values[key].append(value)
            elif key == "evidence_compacted" and isinstance(value, bool):
                evidence_compacted_values.append(value)
            elif key == "evidence_strategy_passed" and isinstance(value, bool):
                evidence_strategy_passed_values.append(value)
            elif key == "evidence_bundle_valid" and isinstance(value, bool):
                evidence_bundle_valid_values.append(value)
            elif key == "evidence_bundle_path" and isinstance(value, str) and value.strip():
                latest_evidence_bundle_path = value
            elif key == "evidence_compaction_signals" and isinstance(value, list):
                for item in value:
                    signal = str(item).strip()
                    if signal and signal not in evidence_compaction_signals:
                        evidence_compaction_signals.append(signal)
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
    if evidence_compacted_values:
        metrics["evidence_compacted"] = any(evidence_compacted_values)
    if evidence_strategy_passed_values:
        metrics["evidence_strategy_passed"] = all(evidence_strategy_passed_values)
    if evidence_bundle_valid_values:
        metrics["evidence_bundle_valid"] = all(evidence_bundle_valid_values)
        metrics["evidence_bundle_valid_values"] = evidence_bundle_valid_values
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
        agent=str(payload.get("agent")) if payload.get("agent") is not None else None,
        timeout_seconds=_optional_float(payload.get("timeout_seconds")),
        max_steps=_optional_int(payload.get("max_steps")),
        max_tokens=_optional_int(payload.get("max_tokens")),
        max_cost_usd=_optional_float(payload.get("max_cost_usd")),
        baseline_repetitions=_positive_int(payload.get("baseline_repetitions"), default=1),
        candidate_repetitions=_positive_int(payload.get("candidate_repetitions"), default=1),
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
    if not replay_result.candidate.succeeded:
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
