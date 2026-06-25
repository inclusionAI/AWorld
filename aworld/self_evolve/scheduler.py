from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from aworld.config.conf import SelfEvolveConfig


WriteJobCallable = Callable[[Path, Mapping[str, Any]], None]
RunJobCallable = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class SelfEvolveRunContext:
    agent_id: str
    task_id: str
    workspace_root: str
    trajectory: tuple[Mapping[str, Any], ...]
    self_evolve_config: SelfEvolveConfig
    source_hints: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SelfEvolveEnqueueResult:
    accepted: bool
    reason: str
    job_id: str | None = None
    job_path: Path | None = None


@dataclass(frozen=True)
class SelfEvolveSchedulerPolicy:
    cooldown_seconds: int = 0
    max_enqueue_retries: int = 0
    enqueue_timeout_seconds: float | None = None
    allow_duplicate_pending: bool = False


class SelfEvolveScheduler:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        write_job: WriteJobCallable | None = None,
        policy: SelfEvolveSchedulerPolicy | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.write_job = write_job or _write_job
        self.policy = policy or SelfEvolveSchedulerPolicy()
        self.now = now or time.time

    def enqueue(self, context: SelfEvolveRunContext) -> SelfEvolveEnqueueResult:
        if context.self_evolve_config.mode not in {"shadow", "online"}:
            return SelfEvolveEnqueueResult(
                accepted=False,
                reason="self-evolve mode is not eligible for post-run enqueue",
            )
        if not context.trajectory:
            return SelfEvolveEnqueueResult(
                accepted=False,
                reason="current trajectory is required for post-run enqueue",
            )
        if not self.policy.allow_duplicate_pending and _has_pending_job(self._jobs_dir()):
            return SelfEvolveEnqueueResult(
                accepted=False,
                reason="duplicate pending self-evolve job exists",
            )
        cooldown_remaining = _cooldown_remaining_seconds(
            self._jobs_dir(),
            cooldown_seconds=self.policy.cooldown_seconds,
            now=self.now(),
        )
        if cooldown_remaining > 0:
            return SelfEvolveEnqueueResult(
                accepted=False,
                reason="self-evolve target is in cooldown",
            )

        job_id = _job_id(context)
        job_path = self._jobs_dir() / f"{job_id}.json"
        payload = {
            "job_id": job_id,
            "status": "pending",
            "created_at": self.now(),
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "workspace_root": context.workspace_root,
            "trajectory": list(context.trajectory),
            "self_evolve_config": context.self_evolve_config.model_dump(),
            "source_hints": dict(context.source_hints),
        }
        attempts = 0
        last_error: Exception | None = None
        while attempts <= self.policy.max_enqueue_retries:
            attempts += 1
            try:
                self.write_job(job_path, payload)
                return SelfEvolveEnqueueResult(
                    accepted=True,
                    reason="pending self-evolve job persisted",
                    job_id=job_id,
                    job_path=job_path,
                )
            except Exception as exc:
                last_error = exc
        return SelfEvolveEnqueueResult(
            accepted=False,
            reason=f"enqueue failed: {last_error}",
        )

    def _jobs_dir(self) -> Path:
        return self.workspace_root / ".aworld" / "self_evolve" / "jobs"


class SelfEvolveJobWorker:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        run_job: RunJobCallable | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.run_job = run_job or _run_framework_job

    def drain_pending_jobs(self) -> int:
        drained = 0
        jobs_dir = self.workspace_root / ".aworld" / "self_evolve" / "jobs"
        for job_path in sorted(jobs_dir.glob("*.json")):
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            if payload.get("status") != "pending":
                continue
            drained += 1
            payload["status"] = "running"
            _write_job(job_path, payload)
            try:
                self.run_job(payload)
            except Exception as exc:
                payload["status"] = "failed"
                payload["failure"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            else:
                payload["status"] = "succeeded"
            _write_job(job_path, payload)
        return drained


def drain_pending_self_evolve_jobs(*, workspace_root: str | Path) -> int:
    return SelfEvolveJobWorker(workspace_root=workspace_root).drain_pending_jobs()


def _run_framework_job(payload: Mapping[str, Any]) -> None:
    from aworld.self_evolve.runner import optimize_from_cli_request

    raw_config = payload.get("self_evolve_config")
    config = (
        SelfEvolveConfig.model_validate(raw_config)
        if isinstance(raw_config, Mapping)
        else SelfEvolveConfig()
    )
    trajectory = payload.get("trajectory")
    if not isinstance(trajectory, list):
        raise ValueError("self-evolve job payload requires trajectory list")
    optimize_from_cli_request(
        workspace_root=str(payload.get("workspace_root") or "."),
        task=str(payload.get("task_id") or "self-evolve-job"),
        current_trajectory=tuple(
            item for item in trajectory if isinstance(item, Mapping)
        ),
        apply_policy=config.apply_policy,
        infer_target=True,
        min_eval_cases=config.min_eval_cases,
        judge_repetitions=config.judge_repetitions,
        max_run_tokens=config.max_run_tokens,
        min_score_delta=config.min_improvement,
        auto_apply_target_types=config.auto_apply_target_types,
        judge_config=config.judge_config,
        replay_enabled=config.replay_enabled,
        replay_timeout_seconds=config.replay_timeout_seconds,
        replay_max_steps=config.replay_max_steps,
        replay_candidate_limit=config.replay_candidate_limit,
        baseline_replay_repetitions=config.baseline_replay_repetitions,
        candidate_replay_repetitions=config.candidate_replay_repetitions,
        replay_stability_margin=config.replay_stability_margin,
    )


def _write_job(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _job_id(context: SelfEvolveRunContext) -> str:
    digest = hashlib.sha256(
        f"{context.agent_id}:{context.task_id}:{context.self_evolve_config.mode}".encode("utf-8")
    ).hexdigest()[:16]
    return f"self-evolve-{digest}"


def _has_pending_job(jobs_dir: Path) -> bool:
    if not jobs_dir.exists():
        return False
    for job_path in jobs_dir.glob("*.json"):
        try:
            payload = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("status") == "pending":
            return True
    return False


def _cooldown_remaining_seconds(
    jobs_dir: Path,
    *,
    cooldown_seconds: int,
    now: float,
) -> int:
    if cooldown_seconds <= 0 or not jobs_dir.exists():
        return 0
    latest_created_at = None
    for job_path in jobs_dir.glob("*.json"):
        try:
            payload = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        created_at = payload.get("created_at")
        if isinstance(created_at, (int, float)):
            latest_created_at = max(latest_created_at or created_at, created_at)
    if latest_created_at is None:
        return 0
    remaining = int(cooldown_seconds - max(0.0, now - latest_created_at))
    return max(0, remaining)
