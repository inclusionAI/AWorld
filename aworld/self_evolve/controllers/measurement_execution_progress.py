"""Progress, heartbeat, and checkpoint projection for paired replay."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from aworld.self_evolve.controllers.screening_execution import (
    _replay_request_artifact_path,
)
from aworld.self_evolve.replay import CandidateReplayRequest


def _remaining_replay_phase_count(
    payload: Mapping[str, object],
) -> int | None:
    case_count = payload.get("case_count")
    case_index = payload.get("case_index")
    if (
        isinstance(case_count, bool)
        or not isinstance(case_count, int)
        or case_count <= 0
        or isinstance(case_index, bool)
        or not isinstance(case_index, int)
        or case_index <= 0
        or case_index > case_count
    ):
        return None
    phase = str(payload.get("phase") or "replay")
    return (case_count - case_index) * 2 + (
        2 if phase == "baseline" else 1
    )


def _replay_total_budget_admission(
    *,
    payload: Mapping[str, object],
    replay_started_at: float,
    now: float,
    total_timeout_seconds: float | None,
    completed_phase_durations: Sequence[float],
) -> dict[str, object] | None:
    """Stop before a new member phase cannot fit the remaining replay budget.

    The estimate is deliberately based only on fully completed member phases.
    A hard timeout remains authoritative when there is not yet enough runtime
    evidence to make a useful admission decision.
    """

    if payload.get("event") != "member_phase_started":
        return None
    if (
        total_timeout_seconds is None
        or isinstance(total_timeout_seconds, bool)
        or not math.isfinite(float(total_timeout_seconds))
        or float(total_timeout_seconds) <= 0
        or len(completed_phase_durations) < 2
    ):
        return None
    remaining_phase_count = _remaining_replay_phase_count(payload)
    if remaining_phase_count is None:
        return None
    case_count = int(payload["case_count"])
    case_index = int(payload["case_index"])
    bounded_durations = [
        float(duration)
        for duration in completed_phase_durations
        if not isinstance(duration, bool)
        and isinstance(duration, (int, float))
        and math.isfinite(float(duration))
        and float(duration) >= 0
    ]
    if len(bounded_durations) < 2:
        return None
    phase = str(payload.get("phase") or "replay")
    elapsed_seconds = max(0.0, float(now) - float(replay_started_at))
    remaining_budget_seconds = max(
        0.0,
        float(total_timeout_seconds) - elapsed_seconds,
    )
    mean_phase_seconds = sum(bounded_durations) / len(bounded_durations)
    estimated_required_seconds = mean_phase_seconds * remaining_phase_count
    if estimated_required_seconds < remaining_budget_seconds:
        return None
    return {
        "trigger": "insufficient_remaining_total_budget",
        "case_id": str(payload.get("case_id") or "unknown-case"),
        "case_index": case_index,
        "case_count": case_count,
        "phase": phase,
        "completed_phase_count": len(bounded_durations),
        "remaining_phase_count": remaining_phase_count,
        "mean_completed_phase_seconds": round(mean_phase_seconds, 3),
        "estimated_required_seconds": round(estimated_required_seconds, 3),
        "remaining_budget_seconds": round(remaining_budget_seconds, 3),
        "resume_safe": True,
    }


def _replay_member_progress_message(
    payload: Mapping[str, object],
) -> str:
    event = str(payload.get("event") or "member_progress")
    candidate_id = str(payload.get("candidate_id") or "candidate")
    case_id = str(payload.get("case_id") or "unknown-case")
    case_index = payload.get("case_index")
    case_count = payload.get("case_count")
    phase = str(payload.get("phase") or "replay")
    prefix = (
        f"Replay candidate {candidate_id}; case {case_index}/{case_count} "
        f"({case_id}); phase {phase}"
    )
    if event == "member_phase_started":
        cache = payload.get("baseline_cache_offered")
        cache_text = (
            f"; baseline cache {'offered' if cache is True else 'miss'}"
            if phase == "baseline"
            else ""
        )
        return (
            f"{prefix} started; repetitions "
            f"{payload.get('repetition_count')}{cache_text}"
        )
    if event == "member_phase_completed":
        cache_status = payload.get("baseline_cache_status")
        cache_text = (
            f"; baseline cache {cache_status}"
            if phase == "baseline" and cache_status is not None
            else ""
        )
        return (
            f"{prefix} completed with status {payload.get('status')}"
            f"{cache_text}"
        )
    if event == "checkpoint_pairs_reused":
        return (
            f"Replay candidate {candidate_id}; reused "
            f"{payload.get('reused_case_count')} completed case pair(s) from "
            "the prior measurement checkpoint; pending cases "
            f"{payload.get('pending_case_count')}"
        )
    if event in {"replay_attempt_started", "replay_attempt_completed"}:
        status = (
            f"; status {payload.get('status')}"
            if event == "replay_attempt_completed"
            else ""
        )
        return (
            f"{prefix}; attempt {payload.get('attempt_index')}/"
            f"{payload.get('attempt_limit')} "
            f"{'completed' if event == 'replay_attempt_completed' else 'started'}"
            f"; attempt timeout {payload.get('attempt_timeout_seconds')}s"
            f"{status}"
        )
    if event == "measurement_stop_triggered":
        return (
            f"{prefix}; trusted measurement stopped replay after "
            f"{payload.get('patience')} invalid control member(s); "
            f"unused cases {payload.get('unused_case_count')}; resume safe "
            f"{payload.get('resume_safe')}"
        )
    if event == "authoritative_stop_triggered":
        return (
            f"{prefix}; stopped remaining authoritative members after "
            f"{payload.get('trigger')}; unused cases "
            f"{payload.get('unused_case_count')}; resume safe "
            f"{payload.get('resume_safe')}"
        )
    return prefix


def _replay_member_hard_deadline_seconds(
    request: CandidateReplayRequest,
    payload: Mapping[str, object],
) -> float | None:
    """Resolve the authoritative member deadline for heartbeat telemetry."""

    value: object
    if request.measurement_plan is not None:
        value = request.measurement_plan.deadlines.member_hard_deadline_seconds
    else:
        value = payload.get("phase_timeout_seconds")
        if value is None:
            value = request.timeout_seconds
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        return None
    return float(value)


def _replay_timeout_checkpoint_details(
    request: CandidateReplayRequest,
) -> dict[str, object]:
    replay_dir = Path(_replay_request_artifact_path(request))
    members_root = replay_dir / "members"
    checkpoint_path = members_root / "paired_replay_checkpoint.json"
    baseline_manifest_path = members_root / "baseline_cache_manifest.json"
    diagnostic_paths = (
        replay_dir / "request.json",
        checkpoint_path,
        baseline_manifest_path,
        (
            Path(request.workspace_root)
            / ".aworld"
            / "self_evolve"
            / request.run_id
            / "candidates"
            / f"{request.candidate_id}.json"
        ),
    )
    details: dict[str, object] = {
        "resume_safe": True,
        "resume_candidate_id": request.candidate_id,
        "resume_candidate_package_fingerprint": (
            request.verified_candidate_package_fingerprint
        ),
        "diagnostic_refs": [
            str(path) for path in diagnostic_paths if path.is_file()
        ],
    }
    if not checkpoint_path.is_file() or checkpoint_path.is_symlink():
        return details
    try:
        raw_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return details
    if not isinstance(raw_checkpoint, Mapping):
        return details
    bounded_checkpoint: dict[str, object] = {}
    for key in (
        "schema_version",
        "schedule",
        "active_case_id",
        "active_phase",
        "resume_safe",
        "baseline_cache_manifest",
        "resumed_from_replay_dir",
    ):
        value = raw_checkpoint.get(key)
        if isinstance(value, (str, bool)) or value is None:
            bounded_checkpoint[key] = value
    for key in (
        "baseline_phase_completed_case_ids",
        "candidate_phase_completed_case_ids",
        "comparable_pair_case_ids",
        "reusable_baseline_case_ids",
        "pending_case_ids",
        "resumed_pair_case_ids",
    ):
        raw = raw_checkpoint.get(key)
        if isinstance(raw, list):
            bounded_checkpoint[key] = [
                str(item)[:160] for item in raw[:64]
            ]
    details["replay_checkpoint"] = bounded_checkpoint
    details["completed_baseline_case_count"] = len(
        bounded_checkpoint.get("baseline_phase_completed_case_ids", [])
    )
    details["completed_candidate_case_count"] = len(
        bounded_checkpoint.get("candidate_phase_completed_case_ids", [])
    )
    details["completed_comparable_pair_count"] = len(
        bounded_checkpoint.get("comparable_pair_case_ids", [])
    )
    details["pending_case_count"] = len(
        bounded_checkpoint.get("pending_case_ids", [])
    )
    return details
