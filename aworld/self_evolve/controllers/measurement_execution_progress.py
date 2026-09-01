"""Progress, heartbeat, and checkpoint projection for paired replay."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping

from aworld.self_evolve.controllers.screening_execution import (
    _replay_request_artifact_path,
)
from aworld.self_evolve.replay import CandidateReplayRequest


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
