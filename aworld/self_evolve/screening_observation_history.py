"""Screening control identity and lifecycle observation history."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Mapping

from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import FailureOwner, ReplayExecutionStatus
from aworld.self_evolve.feedback_history import _report_matches_screening_harness
from aworld.self_evolve.history_support import (
    _load_json_mapping,
    _non_negative_int,
    _non_negative_screening_float,
)
from aworld.self_evolve.replay import (
    CandidateReplayRequest,
    _candidate_replay_request_from_mapping,
    _is_replayable_user_task_case,
    replay_dataset_fingerprint,
    replay_support_fingerprint,
    replay_timeout_envelope_fingerprint,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationBundle,
    replay_adaptation_semantic_fingerprint,
)
from aworld.self_evolve.replay_capability import replay_capability_semantic_fingerprint
from aworld.self_evolve.run_history import _report_matches_target
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import SelfEvolveTargetRef

_SCREENING_CONTROL_HARNESS_ID = "aworld.self_evolve.screening_harness.v2"
_MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS = 300


def _control_qualification_identity(
    *,
    case_id: str,
    baseline_skill_fingerprint: str,
    replay_adaptation: ReplayAdaptationBundle,
    timeout_seconds: float,
    max_steps: int | None,
    max_tool_calls: int | None,
    capability_fingerprint: Callable[[object], str] = (
        replay_capability_semantic_fingerprint
    ),
    adaptation_fingerprint: Callable[[object], str] = (
        replay_adaptation_semantic_fingerprint
    ),
    support_fingerprint: Callable[[object], str] = replay_support_fingerprint,
) -> dict[str, object]:
    """Freeze the exact support and envelope used to qualify one control."""

    capability = replay_adaptation.replay_capability
    capability_package_fingerprint = (
        capability.capability_package_fingerprint
        if capability is not None
        else "framework-only"
    )
    resolved_replay_capability_fingerprint = (
        capability_fingerprint(capability)
        if capability is not None
        else "framework-only"
    )
    resolved_support_fingerprint = support_fingerprint(replay_adaptation)
    assert resolved_support_fingerprint is not None
    timeout_fingerprint = replay_timeout_envelope_fingerprint(
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
    )
    identity: dict[str, object] = {
        "schema_version": "aworld.self_evolve.control_qualification_identity.v1",
        "case_id": case_id,
        "baseline_skill_fingerprint": baseline_skill_fingerprint,
        "capability_package_fingerprint": capability_package_fingerprint,
        "replay_capability_fingerprint": (resolved_replay_capability_fingerprint),
        "adaptation_fingerprint": adaptation_fingerprint(replay_adaptation),
        "support_fingerprint": resolved_support_fingerprint,
        "timeout_envelope_fingerprint": timeout_fingerprint,
        "timeout_seconds": float(timeout_seconds),
        "max_steps": max_steps,
        "max_tool_calls": max_tool_calls,
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    identity["control_identity_fingerprint"] = (
        "sha256:" + hashlib.sha256(encoded).hexdigest()
    )
    return identity


def _control_qualification_identity_from_request(
    request: CandidateReplayRequest,
) -> dict[str, object] | None:
    adaptation = request.replay_adaptation
    if (
        adaptation is None
        or request.baseline_skill_fingerprint is None
        or request.timeout_seconds is None
        or request.support_fingerprint is None
        or request.timeout_envelope_fingerprint is None
    ):
        return None
    identity = _control_qualification_identity(
        case_id=request.task_id,
        baseline_skill_fingerprint=request.baseline_skill_fingerprint,
        replay_adaptation=adaptation,
        timeout_seconds=request.timeout_seconds,
        max_steps=request.max_steps,
        max_tool_calls=request.max_tool_calls,
    )
    compatible_support_fingerprints = {identity["support_fingerprint"]}
    legacy_support_fingerprint = _legacy_path_sensitive_support_fingerprint(adaptation)
    if legacy_support_fingerprint is not None:
        compatible_support_fingerprints.add(legacy_support_fingerprint)
    if (
        request.support_fingerprint not in compatible_support_fingerprints
        or identity["timeout_envelope_fingerprint"]
        != request.timeout_envelope_fingerprint
    ):
        return None
    return identity


def _legacy_path_sensitive_support_fingerprint(
    replay_adaptation: ReplayAdaptationBundle,
) -> str | None:
    """Recognize persisted v1 requests whose support identity included paths."""

    capability = replay_adaptation.replay_capability
    payload = {
        "schema_version": "aworld.self_evolve.replay_support_identity.v1",
        "capability_package_fingerprint": (
            capability.capability_package_fingerprint
            if capability is not None
            else "framework-only"
        ),
        "replay_capability_fingerprint": (
            capability.fingerprint if capability is not None else "framework-only"
        ),
        "adaptation_fingerprint": replay_adaptation.adaptation_fingerprint,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _record_support_specific_control_observation(
    observations: dict[str, dict[str, object]],
    *,
    identity: Mapping[str, object],
    attempt: Mapping[str, object],
) -> None:
    fingerprint = identity.get("control_identity_fingerprint")
    required_fields = (
        "case_id",
        "baseline_skill_fingerprint",
        "capability_package_fingerprint",
        "replay_capability_fingerprint",
        "adaptation_fingerprint",
        "support_fingerprint",
        "timeout_envelope_fingerprint",
    )
    if (
        not isinstance(fingerprint, str)
        or not fingerprint
        or any(not isinstance(identity.get(key), str) for key in required_fields)
    ):
        return
    current = observations.setdefault(
        fingerprint,
        {
            "identity": dict(identity),
            "attempt_count": 0,
            "baseline_attempt_count": 0,
            "baseline_success_count": 0,
            "baseline_timeout_count": 0,
            "passed_count": 0,
            "total_wall_seconds": 0.0,
        },
    )
    if current.get("identity") != dict(identity):
        return
    details = attempt.get("details")
    baseline_status = (
        str(details.get("baseline_status") or "")
        if isinstance(details, Mapping)
        else ""
    )
    baseline_failure = (
        details.get("baseline_failure") if isinstance(details, Mapping) else None
    )
    current["attempt_count"] = _non_negative_int(current.get("attempt_count")) + 1
    current["passed_count"] = _non_negative_int(current.get("passed_count")) + int(
        attempt.get("passed") is True
    )
    current["total_wall_seconds"] = _non_negative_screening_float(
        current.get("total_wall_seconds")
    ) + _non_negative_screening_float(attempt.get("wall_seconds"))
    if baseline_status and baseline_status not in {"blocked", "not_run"}:
        current["baseline_attempt_count"] = (
            _non_negative_int(current.get("baseline_attempt_count")) + 1
        )
        current["baseline_success_count"] = _non_negative_int(
            current.get("baseline_success_count")
        ) + int(baseline_status == ReplayExecutionStatus.SUCCEEDED.value)
        current["baseline_timeout_count"] = _non_negative_int(
            current.get("baseline_timeout_count")
        ) + int(_framework_phase_timeout(baseline_failure))


def _framework_phase_timeout(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("code") == "replay_member_phase_timeout"
        and (
            (value.get("failure_owner") or value.get("owner"))
            == FailureOwner.FRAMEWORK.value
            or value.get("outcome") == "framework_failure"
        )
    )


def _screening_control_harness_fingerprint() -> str:
    return (
        "sha256:"
        + hashlib.sha256(_SCREENING_CONTROL_HARNESS_ID.encode("utf-8")).hexdigest()
    )


def _screening_observation_scope_fingerprint(
    *,
    dataset: SelfEvolveDataset,
    target: SelfEvolveTarget,
) -> str:
    payload = {
        "dataset_fingerprint": replay_dataset_fingerprint(dataset),
        "target_type": target.identity.target_type,
        "target_id": target.identity.target_id,
        "target_path": target.identity.path,
        "baseline_skill_fingerprint": target.fingerprint_current_content(),
        "harness_fingerprint": _screening_control_harness_fingerprint(),
    }
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )


def _restore_campaign_screening_case_observations(
    observations: dict[str, dict[str, float | int]],
    *,
    store: FilesystemSelfEvolveStore,
    prior_run_ids: tuple[str, ...],
    loaded_run_ids: set[str],
    control_observations: dict[str, dict[str, object]] | None = None,
    harness_fingerprint: str | None = None,
) -> None:
    """Restore payload-free control health across Campaign cycles/restarts."""

    for prior_run_id in prior_run_ids:
        if prior_run_id in loaded_run_ids:
            continue
        try:
            report = store.read_report(prior_run_id)
        except (FileNotFoundError, OSError, TypeError, ValueError):
            continue
        if not _report_matches_screening_harness(
            report,
            harness_fingerprint,
        ):
            continue
        verification_funnel = report.get("verification_funnel")
        authoritative_observations = (
            verification_funnel.get("authoritative_case_observations")
            if isinstance(verification_funnel, Mapping)
            else None
        )
        if isinstance(authoritative_observations, Mapping):
            for case_id, raw_observation in authoritative_observations.items():
                if not isinstance(case_id, str) or not isinstance(
                    raw_observation, Mapping
                ):
                    continue
                current = observations.setdefault(case_id, {})
                for field_name in (
                    "attempt_count",
                    "invalid_control_count",
                    "passed_count",
                    "authoritative_failure_count",
                ):
                    count = _non_negative_int(raw_observation.get(field_name))
                    if count <= 0:
                        continue
                    current[field_name] = (
                        _non_negative_int(current.get(field_name)) + count
                    )
                if not current:
                    observations.pop(case_id, None)
        population = report.get("population")
        screening = (
            population.get("screening") if isinstance(population, Mapping) else None
        )
        attempts = screening.get("attempts") if isinstance(screening, Mapping) else None
        if isinstance(attempts, (list, tuple)):
            for attempt in attempts:
                if not isinstance(attempt, Mapping):
                    continue
                raw_control_attempts = attempt.get("control_case_attempts")
                if not isinstance(raw_control_attempts, (list, tuple)):
                    continue
                for control_attempt in raw_control_attempts:
                    if not isinstance(control_attempt, Mapping):
                        continue
                    raw_case_ids = control_attempt.get("case_ids")
                    if control_attempt.get("invalid_control") is True:
                        raw_invalid_case_ids = control_attempt.get(
                            "invalid_control_case_ids"
                        )
                        if isinstance(raw_invalid_case_ids, (list, tuple)):
                            raw_case_ids = raw_invalid_case_ids
                    case_ids = tuple(
                        str(case_id)
                        for case_id in (
                            raw_case_ids
                            if isinstance(raw_case_ids, (list, tuple))
                            else ()
                        )
                        if isinstance(case_id, str) and case_id
                    )
                    if not case_ids:
                        continue
                    raw_identities = control_attempt.get("control_identities")
                    identities = (
                        tuple(
                            item for item in raw_identities if isinstance(item, Mapping)
                        )
                        if isinstance(raw_identities, (list, tuple))
                        else (
                            (control_attempt["control_identity"],)
                            if isinstance(
                                control_attempt.get("control_identity"),
                                Mapping,
                            )
                            else ()
                        )
                    )
                    if control_observations is not None:
                        for identity in identities:
                            _record_support_specific_control_observation(
                                control_observations,
                                identity=identity,
                                attempt={
                                    "passed": (control_attempt.get("passed") is True),
                                    "wall_seconds": (
                                        _non_negative_screening_float(
                                            control_attempt.get("wall_seconds")
                                        )
                                        / max(1, len(identities))
                                    ),
                                    "details": {
                                        "baseline_status": control_attempt.get(
                                            "baseline_status"
                                        ),
                                        "candidate_status": control_attempt.get(
                                            "candidate_status"
                                        ),
                                        "baseline_failure": control_attempt.get(
                                            "baseline_failure"
                                        ),
                                        "candidate_failure": control_attempt.get(
                                            "candidate_failure"
                                        ),
                                    },
                                },
                            )
                    for case_id in case_ids:
                        current = observations.setdefault(case_id, {})
                        current["attempt_count"] = (
                            _non_negative_int(current.get("attempt_count")) + 1
                        )
                        current["invalid_control_count"] = _non_negative_int(
                            current.get("invalid_control_count")
                        ) + int(control_attempt.get("invalid_control") is True)
                        current["passed_count"] = _non_negative_int(
                            current.get("passed_count")
                        ) + int(control_attempt.get("passed") is True)
                        wall_seconds = _non_negative_screening_float(
                            control_attempt.get("wall_seconds")
                        ) / max(1, len(case_ids))
                        current["total_wall_seconds"] = (
                            _non_negative_screening_float(
                                current.get("total_wall_seconds")
                            )
                            + wall_seconds
                        )
        _restore_authoritative_member_lifecycle_observations(
            observations,
            control_observations=control_observations,
            run_dir=store.run_path(prior_run_id),
        )
        loaded_run_ids.add(prior_run_id)


def _restore_historical_screening_lifecycle_observations(
    observations: dict[str, dict[str, float | int]],
    *,
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    dataset: SelfEvolveDataset,
    current_run_id: str,
    control_observations: dict[str, dict[str, object]] | None = None,
    loaded_run_ids: set[str] | None = None,
    harness_fingerprint: str | None = None,
) -> None:
    """Build a candidate-independent control profile from prior lifecycles."""

    eligible_case_ids = {case.case_id for case in dataset.cases}
    report_paths = sorted(
        store.artifact_root.glob("*/report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:128]
    for report_path in report_paths:
        run_dir = report_path.parent
        if run_dir.name == current_run_id or run_dir.is_symlink():
            continue
        if loaded_run_ids is not None and run_dir.name in loaded_run_ids:
            continue
        try:
            report = _load_json_mapping(report_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not _report_matches_target(report, target):
            continue
        if not _report_matches_screening_harness(
            report,
            harness_fingerprint,
        ):
            continue
        restored_authoritative = _restore_authoritative_member_lifecycle_observations(
            observations,
            control_observations=control_observations,
            run_dir=run_dir,
            eligible_case_ids=eligible_case_ids,
        )
        if control_observations is not None:
            health = report.get("support_specific_control_health")
            raw_health_observations = (
                health.get("observations") if isinstance(health, Mapping) else None
            )
            if not restored_authoritative and isinstance(
                raw_health_observations, (list, tuple)
            ):
                for raw_observation in raw_health_observations[:128]:
                    if not isinstance(raw_observation, Mapping):
                        continue
                    identity = raw_observation.get("identity")
                    fingerprint = (
                        identity.get("control_identity_fingerprint")
                        if isinstance(identity, Mapping)
                        else None
                    )
                    if isinstance(fingerprint, str) and fingerprint:
                        control_observations.setdefault(
                            fingerprint,
                            dict(raw_observation),
                        )
        screening_root = run_dir / "screening"
        if not screening_root.is_dir() or screening_root.is_symlink():
            continue
        for case_dir in screening_root.iterdir():
            if not case_dir.is_dir() or case_dir.is_symlink():
                continue
            replay_root = case_dir / "replay"
            if not replay_root.is_dir() or replay_root.is_symlink():
                continue
            for replay_dir in replay_root.iterdir():
                if not replay_dir.is_dir() or replay_dir.is_symlink():
                    continue
                try:
                    stored_request = _candidate_replay_request_from_mapping(
                        _load_json_mapping(replay_dir / "request.json")
                    )
                except (
                    FileNotFoundError,
                    OSError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    stored_request = None
                case_id = (
                    stored_request.task_id
                    if stored_request is not None
                    and stored_request.task_id in eligible_case_ids
                    else case_dir.name
                    if case_dir.name in eligible_case_ids
                    else None
                )
                if case_id is None:
                    continue
                current = observations.setdefault(case_id, {})
                _merge_screening_variant_lifecycle_observation(
                    current,
                    variant_dir=replay_dir / "baseline",
                    phase="baseline",
                )
                candidate_dir = replay_dir / replay_dir.name
                _merge_screening_variant_lifecycle_observation(
                    current,
                    variant_dir=candidate_dir,
                    phase="candidate",
                )
                if control_observations is not None:
                    identity = (
                        _control_qualification_identity_from_request(stored_request)
                        if stored_request is not None
                        else None
                    )
                    if identity is not None:
                        fingerprint = identity.get("control_identity_fingerprint")
                        if fingerprint not in control_observations:
                            _merge_support_specific_lifecycle_observation(
                                control_observations,
                                identity=identity,
                                variant_dir=replay_dir / "baseline",
                            )
                if not current:
                    observations.pop(case_id, None)


def _restore_authoritative_member_lifecycle_observations(
    observations: dict[str, dict[str, float | int]],
    *,
    control_observations: dict[str, dict[str, object]] | None,
    run_dir: Path,
    eligible_case_ids: set[str] | None = None,
) -> bool:
    """Recover completed member controls even when a run timed out pre-report."""

    replay_root = run_dir / "replay"
    if not replay_root.is_dir() or replay_root.is_symlink():
        return False
    restored = False
    candidate_dirs = sorted(replay_root.iterdir(), key=lambda path: path.name)[:32]
    for candidate_dir in candidate_dirs:
        members_root = candidate_dir / "members"
        if (
            not candidate_dir.is_dir()
            or candidate_dir.is_symlink()
            or not members_root.is_dir()
            or members_root.is_symlink()
        ):
            continue
        member_dirs = sorted(members_root.iterdir(), key=lambda path: path.name)[:256]
        for member_dir in member_dirs:
            if not member_dir.is_dir() or member_dir.is_symlink():
                continue
            try:
                stored_request = _candidate_replay_request_from_mapping(
                    _load_json_mapping(member_dir / "request.json")
                )
            except (
                FileNotFoundError,
                OSError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                continue
            case_id = stored_request.task_id
            if eligible_case_ids is not None and case_id not in eligible_case_ids:
                continue
            identity = _control_qualification_identity_from_request(stored_request)
            if identity is None:
                continue
            baseline_dir = member_dir / "baseline"
            lifecycle_path = baseline_dir / "lifecycle.json"
            if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
                continue
            restored = True
            current = observations.setdefault(case_id, {})
            _merge_screening_variant_lifecycle_observation(
                current,
                variant_dir=baseline_dir,
                phase="baseline",
            )
            if control_observations is not None:
                _merge_support_specific_lifecycle_observation(
                    control_observations,
                    identity=identity,
                    variant_dir=baseline_dir,
                )
            if not current:
                observations.pop(case_id, None)
    return restored


def _merge_screening_variant_lifecycle_observation(
    observation: dict[str, float | int],
    *,
    variant_dir: Path,
    phase: str,
) -> None:
    lifecycle_path = variant_dir / "lifecycle.json"
    if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
        return
    try:
        lifecycle = _load_json_mapping(lifecycle_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return
    status = str(lifecycle.get("status") or "")
    if status in {"", "blocked", "not_run"}:
        return
    attempt_key = f"{phase}_attempt_count"
    success_key = f"{phase}_success_count"
    timeout_key = f"{phase}_timeout_count"
    wall_key = f"{phase}_total_wall_seconds"
    observation[attempt_key] = _non_negative_int(observation.get(attempt_key)) + 1
    observation[success_key] = _non_negative_int(observation.get(success_key)) + int(
        status == ReplayExecutionStatus.SUCCEEDED.value
    )
    failure = lifecycle.get("failure")
    phase_timeout = bool(
        isinstance(failure, Mapping)
        and failure.get("code") == "replay_member_phase_timeout"
    )
    observation[timeout_key] = _non_negative_int(observation.get(timeout_key)) + int(
        phase_timeout
    )
    metrics_path = variant_dir / "aggregate_metrics.json"
    try:
        metrics = _load_json_mapping(metrics_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        metrics = {}
    latency_ms = metrics.get("latency_ms")
    wall_seconds = (
        float(latency_ms) / 1000.0
        if isinstance(latency_ms, (int, float))
        and not isinstance(latency_ms, bool)
        and math.isfinite(float(latency_ms))
        and float(latency_ms) >= 0
        else 0.0
    )
    if wall_seconds <= 0 and phase_timeout and isinstance(failure, Mapping):
        diagnostics = failure.get("diagnostics")
        timeout = (
            diagnostics.get("timeout_seconds")
            if isinstance(diagnostics, Mapping)
            else None
        )
        if (
            isinstance(timeout, (int, float))
            and not isinstance(timeout, bool)
            and math.isfinite(float(timeout))
            and float(timeout) > 0
        ):
            wall_seconds = float(timeout)
    if phase_timeout and isinstance(failure, Mapping):
        diagnostics = failure.get("diagnostics")
        timeout = (
            diagnostics.get("timeout_seconds")
            if isinstance(diagnostics, Mapping)
            else None
        )
        if (
            isinstance(timeout, (int, float))
            and not isinstance(timeout, bool)
            and math.isfinite(float(timeout))
            and float(timeout) > 0
        ):
            observation[f"{phase}_timeout_max_seconds"] = max(
                _non_negative_screening_float(
                    observation.get(f"{phase}_timeout_max_seconds")
                ),
                float(timeout),
            )
    observation[wall_key] = (
        _non_negative_screening_float(observation.get(wall_key)) + wall_seconds
    )
    if status == ReplayExecutionStatus.SUCCEEDED.value:
        success_wall_key = f"{phase}_success_wall_seconds"
        observation[success_wall_key] = (
            _non_negative_screening_float(observation.get(success_wall_key))
            + wall_seconds
        )


def _merge_support_specific_lifecycle_observation(
    observations: dict[str, dict[str, object]],
    *,
    identity: Mapping[str, object],
    variant_dir: Path,
) -> None:
    lifecycle_path = variant_dir / "lifecycle.json"
    if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
        return
    try:
        lifecycle = _load_json_mapping(lifecycle_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return
    status = str(lifecycle.get("status") or "")
    if status in {"", "blocked", "not_run"}:
        return
    failure = lifecycle.get("failure")
    metrics_path = variant_dir / "aggregate_metrics.json"
    try:
        metrics = _load_json_mapping(metrics_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        metrics = {}
    latency_ms = metrics.get("latency_ms")
    wall_seconds = (
        float(latency_ms) / 1000.0
        if isinstance(latency_ms, (int, float))
        and not isinstance(latency_ms, bool)
        and math.isfinite(float(latency_ms))
        and float(latency_ms) >= 0
        else 0.0
    )
    _record_support_specific_control_observation(
        observations,
        identity=identity,
        attempt={
            "passed": status == ReplayExecutionStatus.SUCCEEDED.value,
            "wall_seconds": wall_seconds,
            "details": {
                "baseline_status": status,
                "baseline_failure": failure,
            },
        },
    )


def _screening_control_preflight(
    dataset: SelfEvolveDataset,
    *,
    observations: Mapping[str, Mapping[str, float | int]],
    timeout_ceiling_seconds: float = _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
    harness_fingerprint: str | None = None,
) -> dict[str, object]:
    """Classify baseline feasibility before any candidate generation call."""

    case_ids = tuple(
        case.case_id for case in dataset.cases if _is_replayable_user_task_case(case)
    )
    feasible: list[str] = []
    infeasible: list[str] = []
    unknown: list[str] = []
    for case_id in case_ids:
        observation = observations.get(case_id, {})
        attempts = _non_negative_int(observation.get("baseline_attempt_count"))
        successes = _non_negative_int(observation.get("baseline_success_count"))
        timeouts = _non_negative_int(observation.get("baseline_timeout_count"))
        if successes > 0:
            feasible.append(case_id)
        elif (
            attempts > 0
            and timeouts >= attempts
            and _non_negative_screening_float(
                observation.get("baseline_timeout_max_seconds")
            )
            >= timeout_ceiling_seconds
        ):
            infeasible.append(case_id)
        else:
            unknown.append(case_id)
    status = (
        "feasible"
        if feasible
        else "infeasible"
        if case_ids and not unknown and len(infeasible) == len(case_ids)
        else "unknown"
    )
    generation_allowed = status != "infeasible"
    return {
        "schema_version": "aworld.self_evolve.screening_control_preflight.v1",
        "status": status,
        "case_count": len(case_ids),
        "feasible_case_ids": feasible,
        "infeasible_case_ids": infeasible,
        "unknown_case_ids": unknown,
        "candidate_generation_allowed": generation_allowed,
        "advisory_only": generation_allowed,
        "advisory_role": ("candidate_control_ordering" if generation_allowed else None),
        "failure_class": None if generation_allowed else "framework",
        "failure_owner": None if generation_allowed else "framework",
        "failure_scope": None if generation_allowed else "shared_run",
        "repairable": not generation_allowed,
        "code": None if generation_allowed else "baseline_controls_infeasible",
        "next_action": (
            None if generation_allowed else "repair_or_build_shared_replay_harness"
        ),
        "support_specific_qualification_required": True,
        "source": "same_harness_historical_baseline_lifecycle",
        "harness_fingerprint": (
            harness_fingerprint or _screening_control_harness_fingerprint()
        ),
        "timeout_ceiling_seconds": timeout_ceiling_seconds,
        "case_observations": {
            case_id: dict(observations.get(case_id, {}))
            for case_id in case_ids
            if observations.get(case_id)
        },
    }
