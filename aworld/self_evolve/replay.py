from __future__ import annotations

import asyncio
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
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from aworld.logs.util import logger
from aworld.memory.tool_call_compaction import REPLAY_COMPACTED_ARGUMENT_FAILURE
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    is_framework_meta_trace_pack,
)
from aworld.self_evolve.replay_adaptation import (
    REPLAY_ARTIFACT_PLACEHOLDER,
    REPLAY_WORKSPACE_PLACEHOLDER,
    ReplayAdaptationBundle,
    ReplayAdapterBinding,
    ReplayCaseAdaptation,
    ReplayDependency,
    materialize_replay_workspace,
)
from aworld.self_evolve.replay_capability import (
    FrozenReplayCapability,
    build_replay_sandboxed_command,
    FrozenReplayFile,
    ReplayReadinessProbe,
    ReplayServiceSpec,
    replay_process_memory_bytes,
    replay_process_resource_limiter,
    verify_frozen_replay_capability,
)
from aworld.self_evolve.types import CandidateVariant, DatasetRecipe, SelfEvolveTargetRef, to_json_dict

_EVIDENCE_RETRY_LIMIT = 1
_SYNTHETIC_EVIDENCE_EXCERPT_CHARS = 4000
_MAX_METADATA_EVIDENCE_CHARS = 16_384
_COMPARABLE_TASK_FAILURE_TYPES = {"TaskFailure", "TimeoutExpired"}
_COMPARABLE_TASK_FAILURE_REASONS = {"evidence_quality_failed"}
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


@dataclass(frozen=True)
class ReplayVariantResult:
    variant_id: str
    status: str
    trajectory: list[Mapping[str, Any]]
    metrics: Mapping[str, Any] = field(default_factory=dict)
    stdout_path: str | None = None
    stderr_path: str | None = None
    failure: Mapping[str, Any] | None = None
    repetition_results: tuple["ReplayVariantResult", ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


@dataclass(frozen=True)
class CandidateReplayResult:
    request: CandidateReplayRequest
    baseline: ReplayVariantResult
    candidate: ReplayVariantResult
    member_results: tuple["CandidateReplayMemberResult", ...] = ()

    @property
    def succeeded(self) -> bool:
        if self.member_results:
            return all(member.succeeded for member in self.member_results)
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


def candidate_replay_is_comparable(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    require_adapted: bool = False,
) -> bool:
    if not _candidate_replay_provenance_is_comparable(
        replay_result,
        require_adapted=require_adapted,
    ):
        return False
    coverage = candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=replay_result,
    )
    return (
        coverage["member_count"] > 0
        and coverage["comparable_pair_count"] == coverage["member_count"]
    )


def _candidate_replay_provenance_is_comparable(
    replay_result: CandidateReplayResult,
    *,
    require_adapted: bool,
) -> bool:
    if replay_result.request.adaptation_fingerprint is None:
        return not require_adapted
    if (
        replay_result.request.replay_adaptation is not None
        and not replay_result.request.replay_adaptation.ready
    ):
        return False
    if replay_result.member_results:
        pairs = tuple(
            (member.request, member.baseline, member.candidate)
            for member in replay_result.member_results
        )
    else:
        pairs = (
            (
                replay_result.request,
                replay_result.baseline,
                replay_result.candidate,
            ),
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
) -> dict[str, int]:
    replayable_cases = tuple(
        case for case in dataset.cases if _is_replayable_user_task_case(case)
    )
    cases_by_id = {case.case_id: case for case in replayable_cases}
    pairs: list[tuple[EvalCase | None, ReplayVariantResult, ReplayVariantResult]]
    if replay_result.member_results:
        pairs = [
            (
                cases_by_id.get(member.case_id),
                member.baseline,
                member.candidate,
            )
            for member in replay_result.member_results
        ]
        missing_member_count = max(0, len(cases_by_id) - len(pairs))
    else:
        case = cases_by_id.get(replay_result.request.task_id)
        if case is None and len(replayable_cases) == 1:
            case = replayable_cases[0]
        pairs = [(case, replay_result.baseline, replay_result.candidate)]
        missing_member_count = 0

    strict_pair_count = 0
    task_failure_pair_count = 0
    infrastructure_failure_count = 0
    candidate_failure_count = 0
    incomparable_pair_count = missing_member_count
    for case, baseline, candidate in pairs:
        if case is None or not candidate.succeeded:
            incomparable_pair_count += 1
            if not candidate.succeeded:
                candidate_failure_count += 1
            continue
        if baseline.succeeded:
            strict_pair_count += 1
            continue
        if _replay_failure_outcome(baseline.failure) == "task_failure":
            trajectory, _ = _baseline_comparison_trajectory(case, baseline)
            if trajectory:
                task_failure_pair_count += 1
                continue
        else:
            infrastructure_failure_count += 1
        incomparable_pair_count += 1

    member_count = len(pairs) + missing_member_count
    comparable_pair_count = strict_pair_count + task_failure_pair_count
    return {
        "member_count": member_count,
        "strict_pair_count": strict_pair_count,
        "task_failure_pair_count": task_failure_pair_count,
        "comparable_pair_count": comparable_pair_count,
        "incomparable_pair_count": incomparable_pair_count,
        "infrastructure_failure_count": infrastructure_failure_count,
        "candidate_failure_count": candidate_failure_count,
    }


def _replay_failure_outcome(failure: Mapping[str, Any] | None) -> str:
    if not isinstance(failure, Mapping):
        return "infrastructure_failure"
    failure_type = failure.get("type")
    if failure_type in _COMPARABLE_TASK_FAILURE_TYPES:
        return "task_failure"
    reason = failure.get("reason")
    if reason in _COMPARABLE_TASK_FAILURE_REASONS:
        return "task_failure"
    nested = failure.get("failures")
    if isinstance(nested, list) and nested:
        outcomes = {
            _replay_failure_outcome(item)
            for item in nested
            if isinstance(item, Mapping)
        }
        if outcomes == {"task_failure"}:
            return "task_failure"
    return "infrastructure_failure"


def _baseline_preflight_skipped_candidate_result(
    candidate_id: str,
) -> ReplayVariantResult:
    return ReplayVariantResult(
        variant_id=candidate_id,
        status="failed",
        trajectory=[],
        failure={
            "reason": "baseline_preflight_failed",
            "detail": (
                "candidate replay skipped because baseline infrastructure replay failed"
            ),
        },
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
    return [], "unavailable"


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
    member_manifest_path = root / "members" / "manifest.json"
    if member_manifest_path.exists():
        member_manifest = _load_json_object(member_manifest_path)
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
            if relative_path != _member_artifact_name(case_id):
                raise ValueError("stored member replay path does not match case_id")
            member_root = root / "members" / relative_path
            member_request = _candidate_replay_request_from_mapping(
                _load_json_object(member_root / "request.json")
            )
            member_results.append(
                CandidateReplayMemberResult(
                    case_id=case_id,
                    request=member_request,
                    baseline=_load_variant_result_from_dir(
                        (
                            Path(member_request.baseline_replay_dir)
                            if member_request.baseline_replay_dir
                            else member_root / "baseline"
                        ),
                        base_variant_id="baseline",
                    ),
                    candidate=_load_variant_result_from_dir(
                        member_root / _safe_path(request.candidate_id),
                        base_variant_id=request.candidate_id,
                    ),
                )
            )
        members = tuple(member_results)
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
        return CandidateReplayResult(
            request=request,
            baseline=baseline,
            candidate=candidate,
            member_results=members,
        )
    baseline = _load_variant_result_from_dir(root / "baseline", base_variant_id="baseline")
    candidate = _load_variant_result_from_dir(
        root / _safe_path(request.candidate_id),
        base_variant_id=request.candidate_id,
    )
    return CandidateReplayResult(request=request, baseline=baseline, candidate=candidate)


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
        shutil.rmtree(self.private_root, ignore_errors=True)
        if self.disk_limit_error is not None:
            errors.append(self.disk_limit_error)
        if errors:
            raise RuntimeError("replay service cleanup failed: " + "; ".join(errors))


class AWorldCliCandidateReplayBackend:
    def __init__(
        self,
        *,
        executor: ReplayExecutor | None = None,
    ) -> None:
        self.executor = executor or AWorldCliReplayExecutor()

    async def replay_candidate(
        self,
        request: CandidateReplayRequest,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayResult:
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

        if len(replay_cases) == 1 and len(dataset.cases) == 1:
            member = await self._replay_member(
                request,
                candidate=candidate,
                replay_dir=replay_dir,
            )
            baseline = member.baseline
            candidate_result = member.candidate
            member_results: tuple[CandidateReplayMemberResult, ...] = ()
        else:
            members_root = replay_dir / "members"
            member_items: list[CandidateReplayMemberResult] = []
            prepared_members: list[
                tuple[CandidateReplayRequest, Path, ReplayVariantResult]
            ] = []
            member_baseline_repetitions = _distributed_member_repetitions(
                request.baseline_repetitions,
                member_count=len(replay_cases),
            )
            member_candidate_repetitions = _distributed_member_repetitions(
                request.candidate_repetitions,
                member_count=len(replay_cases),
            )
            for case in replay_cases:
                adapted_task_input = _adapted_task_input(request, case)
                member_request = replace(
                    request,
                    task_id=case.case_id,
                    task_input=adapted_task_input,
                    task_input_fingerprint=_adapted_task_input_fingerprint(
                        request,
                        case,
                    ),
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
                baseline = await self._load_or_run_baseline(
                    member_request,
                    candidate=candidate,
                    replay_dir=member_dir,
                )
                prepared_members.append((member_request, member_dir, baseline))

            baseline_preflight_failed = any(
                not baseline.succeeded
                and _replay_failure_outcome(baseline.failure)
                == "infrastructure_failure"
                for _, _, baseline in prepared_members
            )
            for member_request, member_dir, baseline in prepared_members:
                if baseline_preflight_failed:
                    candidate_result = _baseline_preflight_skipped_candidate_result(
                        candidate.candidate_id
                    )
                else:
                    candidate_result = await self._run_repetitions(
                        member_request,
                        base_variant_id=candidate.candidate_id,
                        skill_root=member_request.overlay_skill_root,
                        artifact_dir=member_dir / _safe_path(candidate.candidate_id),
                        repetitions=member_request.candidate_repetitions,
                    )
                member_items.append(
                    CandidateReplayMemberResult(
                        case_id=member_request.task_id,
                        request=member_request,
                        baseline=baseline,
                        candidate=candidate_result,
                    )
                )
            member_results = tuple(member_items)
            _write_json(
                members_root / "manifest.json",
                {
                    "schema_version": "aworld.self_evolve.member_replay.v1",
                    "members": [
                        {
                            "case_id": member.case_id,
                            "path": _member_artifact_name(member.case_id),
                            "succeeded": member.succeeded,
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

    async def _replay_member(
        self,
        request: CandidateReplayRequest,
        *,
        candidate: CandidateVariant,
        replay_dir: Path,
    ) -> CandidateReplayMemberResult:
        baseline = await self._load_or_run_baseline(
            request,
            candidate=candidate,
            replay_dir=replay_dir,
        )
        candidate_result = await self._run_repetitions(
            request,
            base_variant_id=candidate.candidate_id,
            skill_root=request.overlay_skill_root,
            artifact_dir=replay_dir / _safe_path(candidate.candidate_id),
            repetitions=request.candidate_repetitions,
        )
        return CandidateReplayMemberResult(
            case_id=request.task_id,
            request=request,
            baseline=baseline,
            candidate=candidate_result,
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
        results: list[ReplayVariantResult] = []
        logger.info(
            "self_evolve.replay.repetitions.start "
            f"run_id={request.run_id} task_id={request.task_id} "
            f"variant_id={base_variant_id} repetitions={repetitions}"
        )
        for index in range(1, repetitions + 1):
            variant_id = base_variant_id if repetitions == 1 else f"{base_variant_id}-{index}"
            repetition_dir = artifact_dir if repetitions == 1 else artifact_dir / str(index)
            logger.info(
                "self_evolve.replay.repetition.start "
                f"run_id={request.run_id} task_id={request.task_id} "
                f"variant_id={variant_id} index={index}/{repetitions}"
            )
            results.append(
                await self._run_variant_with_evidence_retries(
                    request,
                    variant_id=variant_id,
                    skill_root=skill_root,
                    artifact_dir=repetition_dir,
                )
            )
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
                service_failure = {
                    "type": type(exc).__name__,
                    "reason": str(exc),
                    "outcome": "infrastructure_failure",
                }
        execution_request = ReplayExecutionRequest(
            variant_id=variant_id,
            task_id=request.task_id,
            candidate_id=request.candidate_id,
            workspace_root=workspace_root,
            task_input=task_input,
            task_text=_task_text(task_input),
            skill_root=skill_root,
            artifact_dir=str(artifact_dir),
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
        if failure is not None:
            _write_json(artifact_dir / "failure.json", failure)

        return ReplayVariantResult(
            variant_id=variant_id,
            status=status,
            trajectory=execution_result.trajectory,
            metrics=metrics,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            failure=failure,
        )


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


class AWorldCliReplayExecutor:
    _DEFAULT_TOOL_CALL_LIMIT = 24

    async def __call__(self, request: ReplayExecutionRequest) -> ReplayExecutionResult:
        artifact_dir = Path(request.artifact_dir)
        evidence_manifest = artifact_dir / "evidence_manifest.jsonl"
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
        if request.max_steps is not None:
            command.extend(["--max-runs", str(request.max_steps)])
        if request.max_cost_usd is not None:
            command.extend(["--max-cost", str(request.max_cost_usd)])

        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                cwd=request.workspace_root,
                text=True,
                capture_output=True,
                timeout=request.timeout_seconds,
                env={
                    **os.environ,
                    **dict(request.environment),
                    "AWORLD_SELF_EVOLVE_AUTO_DRAIN": "0",
                    "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR": str(artifact_dir),
                    "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST": str(evidence_manifest),
                    "AWORLD_LOG_PATH": str(artifact_dir / "logs"),
                    "AWORLD_TRAJECTORY_LOG_DISABLED": "1",
                    "AWORLD_TOOL_CALL_LIMIT": str(self._DEFAULT_TOOL_CALL_LIMIT),
                },
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
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                stdout=stdout,
                stderr=stderr,
                failure={"type": "TimeoutExpired", "reason": "replay timed out"},
            )

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
) -> _ReplayServiceSession:
    if not capability.ready or not capability.deterministic:
        raise ValueError("skill-owned replay capability is not ready")
    verify_frozen_replay_capability(capability)
    source_frozen_root = Path(capability.frozen_root).expanduser().resolve()
    if not (source_frozen_root / "runtime").is_dir() or not (
        source_frozen_root / "fixtures"
    ).is_dir():
        raise ValueError("frozen replay capability directories are missing")
    private_root = Path(tempfile.mkdtemp(prefix="aworld-replay-service-"))
    frozen_root = private_root / "capability"
    shutil.copytree(source_frozen_root, frozen_root, symlinks=False)
    fixture_root = (frozen_root / "fixtures").resolve()
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
            command = build_replay_sandboxed_command(
                command,
                read_roots=(fixture_service, fixture_root),
                writable_roots=(scratch_root,),
                allow_loopback=True,
            )
            service_environment = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
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
            await _wait_for_replay_service(
                process,
                host="127.0.0.1",
                port=port,
                kind=service.readiness.kind,
                path=service.readiness.path,
                timeout_seconds=service.readiness.timeout_seconds,
            )
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
            )
            return
        except OSError as exc:
            last_error = exc
            await asyncio.sleep(0.02)
    raise TimeoutError(
        f"replay service readiness timed out after {timeout_seconds}s: {last_error}"
    )


def _probe_replay_service(host: str, port: int, kind: str, path: str) -> None:
    with socket.create_connection((host, port), timeout=0.25) as connection:
        if kind == "http":
            connection.sendall(
                f"GET {path} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode("ascii")
            )
            response = connection.recv(64)
            if not response.startswith(b"HTTP/"):
                raise OSError("HTTP readiness probe returned an invalid response")


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
    return max(1, (max(1, repetitions) + member_count - 1) // member_count)


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
- Also export or use AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR={artifact_dir} when invoking tools that can receive environment variables.
- Append one JSON object per evidence source to this exact replay evidence_manifest.jsonl file: {evidence_manifest}
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
    for line_number, line in enumerate(
        evidence_manifest.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError as exc:
            invalid_reasons.append(f"line {line_number}: {exc.msg}")
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
        canonical["metadata"] = dict(entry.get("metadata") or {})
    elif artifact_path is not None:
        canonical["artifact_path"] = str(artifact_path)
    return canonical


def _bounded_evidence_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _MANIFEST_EVIDENCE_PAYLOAD_KEYS:
        if key in entry:
            payload[key] = entry[key]
    return payload


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
        metadata = entry.get("metadata")
        if not isinstance(metadata, Mapping) or not metadata:
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


_MANIFEST_INLINE_BOUNDED_EVIDENCE_KEYS = (
    "excerpt",
    "excerpts",
    "bounded_excerpt",
    "bounded_excerpts",
    "claims_supported",
    "claims_supported_by",
    "summary",
    "structured_summary",
)


def _has_manifest_evidence_payload(entry: Mapping[str, Any]) -> bool:
    return _has_any_manifest_payload(entry, keys=_MANIFEST_EVIDENCE_PAYLOAD_KEYS)


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
    return isinstance(failure, Mapping) and failure.get("reason") == "evidence_quality_failed"


def _merge_replay_attempt_metrics(
    result: ReplayVariantResult,
    *,
    attempts: list[ReplayVariantResult],
    canonical_variant_id: str,
) -> ReplayVariantResult:
    if len(attempts) == 1:
        return result
    retry_failures = [
        attempt.failure
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
    if len(results) == 1:
        result = results[0]
        failures = [result.failure] if result.failure is not None else []
        metrics = {
            **dict(result.metrics),
            "repetition_count": 1,
            "successful_repetition_count": 1 if result.succeeded else 0,
            "failed_repetition_count": 0 if result.succeeded else 1,
        }
        if failures:
            metrics["repetition_failures"] = failures
        return ReplayVariantResult(
            variant_id=base_variant_id,
            status=result.status,
            trajectory=result.trajectory,
            metrics=metrics,
            stdout_path=result.stdout_path,
            stderr_path=result.stderr_path,
            failure=result.failure,
        )

    successful = [result for result in results if result.succeeded]
    failed = [result for result in results if not result.succeeded]
    status = "succeeded" if successful else "failed"
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
    }
    repetition_failures = [
        result.failure for result in failed if result.failure is not None
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

    selected = successful[-1] if successful else results[-1]
    failure = None
    if status != "succeeded":
        failure = {
            "reason": "one or more replay repetitions failed",
            "failures": [
                result.failure
                for result in results
                if result.failure is not None
            ],
        }
        if persist:
            _write_json(artifact_dir / "failure.json", failure)
    if persist:
        _write_json(artifact_dir / "aggregate_metrics.json", metrics)
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


def _aggregate_member_variant_results(
    *,
    base_variant_id: str,
    members: Sequence[CandidateReplayMemberResult],
    select: Callable[[CandidateReplayMemberResult], ReplayVariantResult],
    artifact_dir: Path,
    persist: bool = True,
) -> ReplayVariantResult:
    member_variants = [select(member) for member in members]
    repetition_results = [
        repetition
        for variant in member_variants
        for repetition in (
            variant.repetition_results if variant.repetition_results else (variant,)
        )
    ]
    aggregated = _aggregate_variant_results(
        base_variant_id=base_variant_id,
        results=repetition_results,
        artifact_dir=artifact_dir,
        persist=persist,
    )
    failed_members = [
        {
            "case_id": member.case_id,
            "failure": select(member).failure,
        }
        for member in members
        if not select(member).succeeded
    ]
    all_members_succeeded = not failed_members
    metrics = {
        **dict(aggregated.metrics),
        "member_count": len(members),
        "successful_member_count": len(members) - len(failed_members),
        "failed_member_count": len(failed_members),
    }
    if failed_members:
        metrics["member_failures"] = failed_members
    if persist:
        _write_json(artifact_dir / "aggregate_metrics.json", metrics)
    failure = None
    if not all_members_succeeded:
        failure = {
            "reason": "one or more trajectory-set members failed replay",
            "members": failed_members,
        }
        if persist:
            _write_json(artifact_dir / "failure.json", failure)
    return replace(
        aggregated,
        status="succeeded" if all_members_succeeded else "failed",
        metrics=metrics,
        failure=failure,
    )


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
                readiness=ReplayReadinessProbe(
                    kind=str(raw_readiness.get("kind") or ""),
                    timeout_seconds=float(
                        raw_readiness.get("timeout_seconds") or 0.0
                    ),
                    path=str(raw_readiness.get("path") or "/"),
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
    )


def _load_variant_result_from_dir(
    variant_dir: Path,
    *,
    base_variant_id: str,
) -> ReplayVariantResult:
    if not variant_dir.exists():
        raise FileNotFoundError(f"stored replay variant not found: {variant_dir}")
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
                result.failure for result in results if result.failure is not None
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
) -> SelfEvolveDataset:
    if not replay_result.candidate.succeeded:
        raise ValueError("candidate replay did not succeed")
    if not candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=replay_result,
    ):
        raise ValueError("candidate replay did not produce comparable paired outcomes")

    member_results = {
        member.case_id: member for member in replay_result.member_results
    }
    cases: list[EvalCase] = []
    source_to_replay_case_ids: dict[str, list[str]] = {}
    for case in dataset.cases:
        if member_results:
            member_result = member_results.get(case.case_id)
            if member_result is None:
                continue
            baseline_variant = member_result.baseline
            candidate_variant = member_result.candidate
            replay_request = member_result.request
        else:
            baseline_variant = replay_result.baseline
            candidate_variant = replay_result.candidate
            replay_request = replay_result.request
        baseline_trajectory, baseline_trajectory_source = (
            _baseline_comparison_trajectory(case, baseline_variant)
        )
        baseline_outcome = (
            "success"
            if baseline_variant.succeeded
            else _replay_failure_outcome(baseline_variant.failure)
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
                    "failure": baseline_variant.failure,
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
                    "failure": candidate_variant.failure,
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
