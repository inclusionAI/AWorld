from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from aworld.config.conf import SelfEvolveConfig


WriteJobCallable = Callable[[Path, Mapping[str, Any]], None]


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


class SelfEvolveScheduler:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        write_job: WriteJobCallable | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.write_job = write_job or _write_job

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

        job_id = _job_id(context)
        job_path = self.workspace_root / ".aworld" / "self_evolve" / "jobs" / f"{job_id}.json"
        payload = {
            "job_id": job_id,
            "status": "pending",
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "workspace_root": context.workspace_root,
            "trajectory": list(context.trajectory),
            "self_evolve_config": context.self_evolve_config.model_dump(),
            "source_hints": dict(context.source_hints),
        }
        try:
            self.write_job(job_path, payload)
        except Exception as exc:
            return SelfEvolveEnqueueResult(
                accepted=False,
                reason=f"enqueue failed: {exc}",
            )
        return SelfEvolveEnqueueResult(
            accepted=True,
            reason="pending self-evolve job persisted",
            job_id=job_id,
            job_path=job_path,
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
