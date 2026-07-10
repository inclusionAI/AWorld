from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from aworld.logs.util import logger
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    is_framework_meta_trace_pack,
)
from aworld.self_evolve.types import CandidateVariant, DatasetRecipe, SelfEvolveTargetRef, to_json_dict

_EVIDENCE_RETRY_LIMIT = 1
_SYNTHETIC_EVIDENCE_EXCERPT_CHARS = 4000


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
            for case in replay_cases:
                member_request = replace(
                    request,
                    task_id=case.case_id,
                    task_input=case.input,
                    baseline_replay_dir=_member_baseline_replay_dir(
                        request.baseline_replay_dir,
                        case.case_id,
                    ),
                )
                member_dir = members_root / _member_artifact_name(case.case_id)
                member_dir.mkdir(parents=True, exist_ok=True)
                _write_json(member_dir / "request.json", member_request)
                member_items.append(
                    await self._replay_member(
                        member_request,
                        candidate=candidate,
                        replay_dir=member_dir,
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
        if request.baseline_replay_dir:
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
            baseline = await self._run_repetitions(
                request,
                base_variant_id="baseline",
                skill_root=request.baseline_skill_root or _infer_baseline_skill_root(request),
                artifact_dir=replay_dir / "baseline",
                repetitions=request.baseline_repetitions,
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
        execution_request = ReplayExecutionRequest(
            variant_id=variant_id,
            task_id=request.task_id,
            candidate_id=request.candidate_id,
            workspace_root=request.workspace_root,
            task_input=request.task_input,
            task_text=_task_text(request.task_input),
            skill_root=skill_root,
            artifact_dir=str(artifact_dir),
            agent=request.agent,
            timeout_seconds=request.timeout_seconds,
            max_steps=request.max_steps,
            max_tokens=request.max_tokens,
            max_cost_usd=request.max_cost_usd,
        )
        _write_json(artifact_dir / "execution_request.json", execution_request)
        started_at = time.monotonic()
        try:
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
        if not isinstance(execution_result, ReplayExecutionResult):
            raise ValueError("replay executor must return ReplayExecutionResult")

        metrics = {
            "latency_ms": (time.monotonic() - started_at) * 1000,
            **dict(execution_result.metrics),
        }
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


class AWorldCliReplayExecutor:
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
                    "AWORLD_SELF_EVOLVE_AUTO_DRAIN": "0",
                    "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR": str(artifact_dir),
                    "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST": str(evidence_manifest),
                    "AWORLD_LOG_PATH": str(artifact_dir / "logs"),
                    "AWORLD_TRAJECTORY_LOG_DISABLED": "1",
                },
            )
        except subprocess.TimeoutExpired as exc:
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                stdout=_text_output(exc.stdout),
                stderr=_text_output(exc.stderr),
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
) -> CandidateReplayRequest:
    if not dataset.cases:
        raise ValueError("candidate replay requires at least one eval case")
    case = _select_replay_case(dataset)
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
        task_input=case.input,
        agent=agent,
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
        baseline_repetitions=baseline_repetitions,
        candidate_repetitions=candidate_repetitions,
    )


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
            return str(root / relative_path / "baseline")
    return None


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
- Each manifest object must include source_id, artifact_path, extraction_method, and the bounded excerpt or field list used for the final answer.
- Emit only bounded structured summaries with source identifiers, locations, and short excerpts.
- If any tool result is compacted, truncated, schema-invalid, or too large to inspect, treat that result as unusable evidence and retry with a narrower extraction strategy before answering.
- Keep a concise evidence ledger mapping important final-answer claims to non-compacted extracts or artifact references.
- Before finalizing, perform a claim-by-claim check and omit claims that are not supported by non-compacted evidence captured in the trajectory.
""".strip()


def _replay_task_text(
    task_text: str,
    *,
    artifact_dir: Path | None = None,
    evidence_manifest: Path | None = None,
) -> str:
    if "Self-evolve replay evidence requirements:" in task_text:
        return task_text
    artifact_dir_text = str(artifact_dir) if artifact_dir is not None else "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"
    evidence_manifest_text = (
        str(evidence_manifest)
        if evidence_manifest is not None
        else "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST"
    )
    return task_text.rstrip() + "\n\n" + _REPLAY_EVIDENCE_POLICY.format(
        artifact_dir=artifact_dir_text,
        evidence_manifest=evidence_manifest_text,
    )


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
    return {
        "evidence_compacted": compacted,
        "evidence_strategy_passed": (not compacted) or manifest_fully_valid,
        "evidence_compaction_signals": signals,
        **manifest_metrics,
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
    artifact_path = _manifest_artifact_path(entry, artifact_dir=artifact_dir)
    bounded_evidence = _bounded_evidence_payload(entry)
    if not bounded_evidence:
        synthetic_excerpt = _synthetic_bounded_artifact_excerpt(artifact_path)
        if synthetic_excerpt:
            bounded_evidence["bounded_excerpt"] = synthetic_excerpt["text"]
            bounded_evidence["source"] = "artifact_preview"
            bounded_evidence["truncated"] = synthetic_excerpt["truncated"]
    fields_used = entry.get("fields_used")
    if fields_used and "fields_used" not in bounded_evidence:
        bounded_evidence["fields_used"] = fields_used
    return {
        "source_id": str(entry.get("source_id") or ""),
        "artifact_path": str(artifact_path),
        "extraction_method": str(entry.get("extraction_method") or ""),
        "bounded_evidence": bounded_evidence,
    }


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
    for key in ("source_id", "artifact_path", "extraction_method"):
        if not str(entry.get(key) or "").strip():
            return f"missing {key}"
    artifact_path = _manifest_artifact_path(entry, artifact_dir=artifact_dir)
    if not artifact_path.exists():
        return "artifact_path does not exist"
    if artifact_dir is not None:
        try:
            artifact_path.resolve().relative_to(artifact_dir.resolve())
        except ValueError:
            return "artifact_path is outside trusted replay/workspace directories"
    if not _has_manifest_evidence_payload(entry) and not _synthetic_bounded_artifact_excerpt(
        artifact_path
    ):
        return "missing bounded evidence payload"
    return None


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


def _has_manifest_evidence_payload(entry: Mapping[str, Any]) -> bool:
    for key in _MANIFEST_EVIDENCE_PAYLOAD_KEYS:
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
    for result in results:
        for key, value in result.metrics.items():
            if key == "evidence_compacted" and isinstance(value, bool):
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
    for key, values in numeric_metrics.items():
        if values:
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
        return _load_single_variant_result(variant_dir, variant_id=base_variant_id)

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
    if not replay_result.baseline.succeeded:
        raise ValueError("baseline replay did not succeed")

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
