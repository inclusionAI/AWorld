from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

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


@dataclass(frozen=True)
class ReplayVariantResult:
    variant_id: str
    status: str
    trajectory: list[Mapping[str, Any]]
    metrics: Mapping[str, Any] = field(default_factory=dict)
    stdout_path: str | None = None
    stderr_path: str | None = None
    failure: Mapping[str, Any] | None = None

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

        baseline = await self._run_variant(
            request,
            variant_id="baseline",
            skill_root=request.baseline_skill_root or _infer_baseline_skill_root(request),
            artifact_dir=replay_dir / "baseline",
        )
        candidate_result = await self._run_variant(
            request,
            variant_id=candidate.candidate_id,
            skill_root=request.overlay_skill_root,
            artifact_dir=replay_dir / _safe_path(candidate.candidate_id),
        )
        return CandidateReplayResult(
            request=request,
            baseline=baseline,
            candidate=candidate_result,
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
            )
        except subprocess.TimeoutExpired as exc:
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                failure={"type": "TimeoutExpired", "reason": "replay timed out"},
            )

        trajectory = _extract_trajectory_from_stdout(completed.stdout)
        if completed.returncode != 0:
            return ReplayExecutionResult(
                status="failed",
                trajectory=trajectory,
                stdout=completed.stdout,
                stderr=completed.stderr,
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
            stdout=completed.stdout,
            stderr=completed.stderr,
            metrics={"returncode": completed.returncode},
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
    return str(path.parent.parent)


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
            return [item for item in trajectory if isinstance(item, Mapping)]
    return []


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
        metadata = dict(case.metadata)
        metadata["variant_trajectories"] = {
            "baseline": replay_result.baseline.trajectory,
            candidate.candidate_id: replay_result.candidate.trajectory,
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
            },
            "candidate": {
                "status": replay_result.candidate.status,
                "metrics": dict(replay_result.candidate.metrics),
                "failure": replay_result.candidate.failure,
            },
        }
        cases.append(
            EvalCase(
                case_id=case.case_id,
                input=case.input,
                expected_output=case.expected_output,
                verification_command=case.verification_command,
                metadata=metadata,
                trace_pack=case.trace_pack,
                source=case.source,
            )
        )

    return SelfEvolveDataset(
        cases=tuple(cases),
        recipe=DatasetRecipe(
            source={
                **dict(dataset.recipe.source),
                "paired_replay": True,
                "candidate_id": candidate.candidate_id,
            },
            split_seed=dataset.recipe.split_seed,
            splits=dataset.recipe.splits,
            synthetic_generation_policy=dataset.recipe.synthetic_generation_policy,
            trainable_case_ids=dataset.recipe.trainable_case_ids,
            held_out_case_ids=dataset.recipe.held_out_case_ids,
        ),
    )
