from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from aworld.logs.util import logger
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.types import CandidateVariant, DatasetRecipe, SelfEvolveTargetRef, to_json_dict


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
        logger.info(
            "self_evolve.replay.start "
            f"run_id={request.run_id} task_id={request.task_id} "
            f"candidate_id={candidate.candidate_id} "
            f"baseline_repetitions={request.baseline_repetitions} "
            f"candidate_repetitions={request.candidate_repetitions}"
        )

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
                await self._run_variant(
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
        command = [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "run",
            "--task",
            request.task_text,
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
        metrics = {
            "returncode": completed.returncode,
            "trajectory_capture_mode": capture_mode,
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
) -> CandidateReplayRequest:
    if not dataset.cases:
        raise ValueError("candidate replay requires at least one eval case")
    case = dataset.cases[0]
    return CandidateReplayRequest(
        run_id=run_id,
        task_id=case.case_id,
        workspace_root=str(Path(workspace_root)),
        target=target,
        candidate_id=candidate.candidate_id,
        overlay_skill_root=str(Path(overlay_skill_root)),
        baseline_skill_root=_infer_baseline_skill_root_from_target(target),
        task_input=case.input,
        agent=agent,
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
        baseline_repetitions=baseline_repetitions,
        candidate_repetitions=candidate_repetitions,
    )


def _infer_baseline_skill_root(request: CandidateReplayRequest) -> str | None:
    if request.baseline_skill_root:
        return request.baseline_skill_root
    return _infer_baseline_skill_root_from_target(request.target)


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


def _aggregate_variant_results(
    *,
    base_variant_id: str,
    results: list[ReplayVariantResult],
    artifact_dir: Path,
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
    for result in results:
        for key, value in result.metrics.items():
            if isinstance(value, (int, float)):
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
        _write_json(artifact_dir / "failure.json", failure)
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

    cases: list[EvalCase] = []
    for case in dataset.cases:
        baseline_results = _evaluation_repetition_results(replay_result.baseline)
        candidate_results = _evaluation_repetition_results(replay_result.candidate)
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
                    "run_id": replay_result.request.run_id,
                    "task_id": replay_result.request.task_id,
                    "candidate_id": replay_result.request.candidate_id,
                    "overlay_skill_root": replay_result.request.overlay_skill_root,
                },
                "baseline": {
                    "status": replay_result.baseline.status,
                    "metrics": dict(replay_result.baseline.metrics),
                    "failure": replay_result.baseline.failure,
                    "variant_id": baseline_result.variant_id,
                },
                "candidate": {
                    "status": replay_result.candidate.status,
                    "metrics": dict(replay_result.candidate.metrics),
                    "failure": replay_result.candidate.failure,
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

    return SelfEvolveDataset(
        cases=tuple(cases),
        recipe=DatasetRecipe(
            source={
                **dict(dataset.recipe.source),
                "paired_replay": True,
                "candidate_id": candidate.candidate_id,
                "original_case_count": len(dataset.cases),
                "replay_case_count": len(cases),
            },
            split_seed=dataset.recipe.split_seed,
            splits={"train": case_ids, "validation": [], "held_out": []},
            synthetic_generation_policy=dataset.recipe.synthetic_generation_policy,
            trainable_case_ids=tuple(case_ids),
            held_out_case_ids=dataset.recipe.held_out_case_ids,
        ),
    )


def _evaluation_repetition_results(
    result: ReplayVariantResult,
) -> tuple[ReplayVariantResult, ...]:
    successful = tuple(item for item in result.repetition_results if item.succeeded)
    if successful:
        return successful
    return (result,)
