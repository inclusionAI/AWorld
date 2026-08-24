"""Typed admission contracts for resumable replay execution.

Authoritative measurement requires the exact frozen plan, candidate package,
and runtime dependencies.  Pre-authority progressive paired replay has a
separate contract that can reuse completed pairs without claiming measurement
authority.  Screening artifacts cannot produce either contract because they
live outside the canonical ``replay/`` namespace.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aworld.self_evolve.measurement_control import MeasurementWorkUnitState


MEASUREMENT_RESUME_CHECKPOINT_SCHEMA_VERSION = (
    "aworld.self_evolve.measurement_resume_checkpoint.v1"
)
PAIRED_REPLAY_RESUME_CHECKPOINT_SCHEMA_VERSION = (
    "aworld.self_evolve.paired_replay_resume_checkpoint.v1"
)
_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}")
_MAX_RETRY_ATTEMPTS = 2


@dataclass(frozen=True)
class MeasurementResumeCheckpointV1:
    """Immutable pointer to one resumable authoritative measurement graph."""

    source_run_id: str
    candidate_id: str
    candidate_fingerprint: str
    replay_dir: str
    request_path: str
    measurement_plan_fingerprint: str
    experiment_id: str
    protected_paths: tuple[str, ...]
    checkpoint_fingerprint: str
    schema_version: str = MEASUREMENT_RESUME_CHECKPOINT_SCHEMA_VERSION
    stage: str = "authoritative_replay"

    def __post_init__(self) -> None:
        if self.schema_version != MEASUREMENT_RESUME_CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("unsupported measurement resume checkpoint schema")
        if self.stage != "authoritative_replay":
            raise ValueError("measurement resume checkpoint must be authoritative")
        for value, name in (
            (self.source_run_id, "source_run_id"),
            (self.candidate_id, "candidate_id"),
        ):
            if _ID.fullmatch(value) is None:
                raise ValueError(f"invalid measurement checkpoint {name}")
        for value, name in (
            (self.candidate_fingerprint, "candidate_fingerprint"),
            (self.measurement_plan_fingerprint, "measurement_plan_fingerprint"),
            (self.checkpoint_fingerprint, "checkpoint_fingerprint"),
        ):
            if _FINGERPRINT.fullmatch(value) is None:
                raise ValueError(f"invalid measurement checkpoint {name}")
        if not re.fullmatch(r"experiment-[0-9a-f]{32}", self.experiment_id):
            raise ValueError("invalid measurement checkpoint experiment_id")
        expected_replay = f"replay/{self.candidate_id}"
        if self.replay_dir != expected_replay:
            raise ValueError("measurement checkpoint is outside authoritative replay")
        if self.request_path != f"{expected_replay}/request.json":
            raise ValueError("measurement checkpoint request path is not canonical")
        normalized_paths = tuple(sorted(dict.fromkeys(self.protected_paths)))
        if normalized_paths != self.protected_paths or not normalized_paths:
            raise ValueError("measurement checkpoint protected paths are not canonical")
        for value in normalized_paths:
            _validate_relative_path(value)
        if self.replay_dir not in normalized_paths:
            raise ValueError("measurement checkpoint does not protect its replay")
        if self.request_path not in normalized_paths:
            raise ValueError("measurement checkpoint does not protect its request")
        if self.checkpoint_fingerprint != _fingerprint(self._identity_payload()):
            raise ValueError("measurement checkpoint fingerprint mismatch")

    @classmethod
    def create(
        cls,
        *,
        source_run_id: str,
        candidate_id: str,
        candidate_fingerprint: str,
        measurement_plan_fingerprint: str,
        experiment_id: str,
        protected_paths: tuple[str, ...],
    ) -> "MeasurementResumeCheckpointV1":
        replay_dir = f"replay/{candidate_id}"
        payload = {
            "schema_version": MEASUREMENT_RESUME_CHECKPOINT_SCHEMA_VERSION,
            "stage": "authoritative_replay",
            "source_run_id": source_run_id,
            "candidate_id": candidate_id,
            "candidate_fingerprint": candidate_fingerprint,
            "replay_dir": replay_dir,
            "request_path": f"{replay_dir}/request.json",
            "measurement_plan_fingerprint": measurement_plan_fingerprint,
            "experiment_id": experiment_id,
            "protected_paths": tuple(sorted(dict.fromkeys(protected_paths))),
        }
        return cls(
            **payload,
            checkpoint_fingerprint=_fingerprint(payload),
        )

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "MeasurementResumeCheckpointV1":
        raw_paths = value.get("protected_paths")
        if not isinstance(raw_paths, list) or not all(
            isinstance(item, str) and item for item in raw_paths
        ):
            raise ValueError("measurement checkpoint protected_paths must be strings")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            stage=str(value.get("stage") or ""),
            source_run_id=str(value.get("source_run_id") or ""),
            candidate_id=str(value.get("candidate_id") or ""),
            candidate_fingerprint=str(value.get("candidate_fingerprint") or ""),
            replay_dir=str(value.get("replay_dir") or ""),
            request_path=str(value.get("request_path") or ""),
            measurement_plan_fingerprint=str(
                value.get("measurement_plan_fingerprint") or ""
            ),
            experiment_id=str(value.get("experiment_id") or ""),
            protected_paths=tuple(raw_paths),
            checkpoint_fingerprint=str(value.get("checkpoint_fingerprint") or ""),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity_payload(),
            "checkpoint_fingerprint": self.checkpoint_fingerprint,
        }

    def _identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "source_run_id": self.source_run_id,
            "candidate_id": self.candidate_id,
            "candidate_fingerprint": self.candidate_fingerprint,
            "replay_dir": self.replay_dir,
            "request_path": self.request_path,
            "measurement_plan_fingerprint": self.measurement_plan_fingerprint,
            "experiment_id": self.experiment_id,
            "protected_paths": list(self.protected_paths),
        }


@dataclass(frozen=True)
class PairedReplayResumeCheckpointV1:
    """Immutable pointer to one resumable pre-authority paired replay."""

    source_run_id: str
    candidate_id: str
    candidate_fingerprint: str
    verified_candidate_package_fingerprint: str
    replay_dir: str
    request_path: str
    progress_checkpoint_path: str
    pending_case_ids: tuple[str, ...]
    completed_pair_case_ids: tuple[str, ...]
    resumed_pair_case_ids: tuple[str, ...]
    protected_paths: tuple[str, ...]
    checkpoint_fingerprint: str
    schema_version: str = PAIRED_REPLAY_RESUME_CHECKPOINT_SCHEMA_VERSION
    stage: str = "paired_replay"

    def __post_init__(self) -> None:
        if self.schema_version != PAIRED_REPLAY_RESUME_CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("unsupported paired replay resume checkpoint schema")
        if self.stage != "paired_replay":
            raise ValueError("paired replay checkpoint has an invalid stage")
        for value, name in (
            (self.source_run_id, "source_run_id"),
            (self.candidate_id, "candidate_id"),
        ):
            if _ID.fullmatch(value) is None:
                raise ValueError(f"invalid paired replay checkpoint {name}")
        for value, name in (
            (self.candidate_fingerprint, "candidate_fingerprint"),
            (
                self.verified_candidate_package_fingerprint,
                "verified_candidate_package_fingerprint",
            ),
            (self.checkpoint_fingerprint, "checkpoint_fingerprint"),
        ):
            if _FINGERPRINT.fullmatch(value) is None:
                raise ValueError(f"invalid paired replay checkpoint {name}")
        expected_replay = f"replay/{self.candidate_id}"
        if self.replay_dir != expected_replay:
            raise ValueError("paired replay checkpoint is outside canonical replay")
        if self.request_path != f"{expected_replay}/request.json":
            raise ValueError("paired replay request path is not canonical")
        if self.progress_checkpoint_path != (
            f"{expected_replay}/members/paired_replay_checkpoint.json"
        ):
            raise ValueError("paired replay progress path is not canonical")
        if not self.pending_case_ids:
            raise ValueError("paired replay checkpoint has no pending work")
        for values, name in (
            (self.pending_case_ids, "pending_case_ids"),
            (self.completed_pair_case_ids, "completed_pair_case_ids"),
            (self.resumed_pair_case_ids, "resumed_pair_case_ids"),
        ):
            if tuple(sorted(dict.fromkeys(values))) != values:
                raise ValueError(f"paired replay checkpoint {name} is not canonical")
            if any(_ID.fullmatch(value) is None for value in values):
                raise ValueError(f"paired replay checkpoint {name} is invalid")
        if set(self.pending_case_ids) & set(self.completed_pair_case_ids):
            raise ValueError("paired replay pending and completed cases overlap")
        if not set(self.resumed_pair_case_ids).issubset(
            self.completed_pair_case_ids
        ):
            raise ValueError("paired replay resumed cases are not completed")
        normalized_paths = tuple(sorted(dict.fromkeys(self.protected_paths)))
        if normalized_paths != self.protected_paths or not normalized_paths:
            raise ValueError("paired replay protected paths are not canonical")
        for value in normalized_paths:
            _validate_relative_path(value)
        for required in (
            self.replay_dir,
            self.request_path,
            self.progress_checkpoint_path,
        ):
            if required not in normalized_paths:
                raise ValueError("paired replay checkpoint misses a dependency")
        if self.checkpoint_fingerprint != _fingerprint(self._identity_payload()):
            raise ValueError("paired replay checkpoint fingerprint mismatch")

    @classmethod
    def create(
        cls,
        *,
        source_run_id: str,
        candidate_id: str,
        candidate_fingerprint: str,
        verified_candidate_package_fingerprint: str,
        pending_case_ids: tuple[str, ...],
        completed_pair_case_ids: tuple[str, ...],
        resumed_pair_case_ids: tuple[str, ...],
        protected_paths: tuple[str, ...],
    ) -> "PairedReplayResumeCheckpointV1":
        replay_dir = f"replay/{candidate_id}"
        payload = {
            "schema_version": PAIRED_REPLAY_RESUME_CHECKPOINT_SCHEMA_VERSION,
            "stage": "paired_replay",
            "source_run_id": source_run_id,
            "candidate_id": candidate_id,
            "candidate_fingerprint": candidate_fingerprint,
            "verified_candidate_package_fingerprint": (
                verified_candidate_package_fingerprint
            ),
            "replay_dir": replay_dir,
            "request_path": f"{replay_dir}/request.json",
            "progress_checkpoint_path": (
                f"{replay_dir}/members/paired_replay_checkpoint.json"
            ),
            "pending_case_ids": tuple(sorted(dict.fromkeys(pending_case_ids))),
            "completed_pair_case_ids": tuple(
                sorted(dict.fromkeys(completed_pair_case_ids))
            ),
            "resumed_pair_case_ids": tuple(
                sorted(dict.fromkeys(resumed_pair_case_ids))
            ),
            "protected_paths": tuple(sorted(dict.fromkeys(protected_paths))),
        }
        return cls(**payload, checkpoint_fingerprint=_fingerprint(payload))

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "PairedReplayResumeCheckpointV1":
        raw_paths = value.get("protected_paths")
        raw_pending = value.get("pending_case_ids")
        raw_completed = value.get("completed_pair_case_ids")
        raw_resumed = value.get("resumed_pair_case_ids")
        for raw, name in (
            (raw_paths, "protected_paths"),
            (raw_pending, "pending_case_ids"),
            (raw_completed, "completed_pair_case_ids"),
            (raw_resumed, "resumed_pair_case_ids"),
        ):
            if not isinstance(raw, list) or not all(
                isinstance(item, str) and item for item in raw
            ):
                raise ValueError(f"paired replay checkpoint {name} must be strings")
        assert isinstance(raw_paths, list)
        assert isinstance(raw_pending, list)
        assert isinstance(raw_completed, list)
        assert isinstance(raw_resumed, list)
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            stage=str(value.get("stage") or ""),
            source_run_id=str(value.get("source_run_id") or ""),
            candidate_id=str(value.get("candidate_id") or ""),
            candidate_fingerprint=str(value.get("candidate_fingerprint") or ""),
            verified_candidate_package_fingerprint=str(
                value.get("verified_candidate_package_fingerprint") or ""
            ),
            replay_dir=str(value.get("replay_dir") or ""),
            request_path=str(value.get("request_path") or ""),
            progress_checkpoint_path=str(
                value.get("progress_checkpoint_path") or ""
            ),
            pending_case_ids=tuple(raw_pending),
            completed_pair_case_ids=tuple(raw_completed),
            resumed_pair_case_ids=tuple(raw_resumed),
            protected_paths=tuple(raw_paths),
            checkpoint_fingerprint=str(value.get("checkpoint_fingerprint") or ""),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity_payload(),
            "checkpoint_fingerprint": self.checkpoint_fingerprint,
        }

    def _identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "source_run_id": self.source_run_id,
            "candidate_id": self.candidate_id,
            "candidate_fingerprint": self.candidate_fingerprint,
            "verified_candidate_package_fingerprint": (
                self.verified_candidate_package_fingerprint
            ),
            "replay_dir": self.replay_dir,
            "request_path": self.request_path,
            "progress_checkpoint_path": self.progress_checkpoint_path,
            "pending_case_ids": list(self.pending_case_ids),
            "completed_pair_case_ids": list(self.completed_pair_case_ids),
            "resumed_pair_case_ids": list(self.resumed_pair_case_ids),
            "protected_paths": list(self.protected_paths),
        }


def discover_measurement_resume_checkpoint(
    store: Any,
    *,
    run_id: str,
    candidate_id: str,
    candidate_fingerprint: str,
) -> MeasurementResumeCheckpointV1 | None:
    """Return a checkpoint only for a complete, resumable v2 authority graph."""

    run_path = Path(store.run_path(run_id))
    replay_dir = run_path / "replay" / candidate_id
    request_path = replay_dir / "request.json"
    candidate_path = run_path / "candidates" / f"{candidate_id}.json"
    if not _regular_file(request_path) or not _regular_file(candidate_path):
        return None
    try:
        request = _read_json_mapping(request_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if request.get("run_id") != run_id or request.get("candidate_id") != candidate_id:
        return None
    raw_plan = request.get("measurement_plan")
    if not isinstance(raw_plan, Mapping):
        return None
    plan_fingerprint = raw_plan.get("measurement_plan_fingerprint")
    experiment_id = raw_plan.get("experiment_id")
    if (
        not isinstance(plan_fingerprint, str)
        or _FINGERPRINT.fullmatch(plan_fingerprint) is None
        or not isinstance(experiment_id, str)
        or not re.fullmatch(r"experiment-[0-9a-f]{32}", experiment_id)
        or raw_plan.get("candidate_fingerprint") != candidate_fingerprint
    ):
        return None
    try:
        plan = store.read_measurement_control_plan(run_id, plan_fingerprint)
        index = store.read_measurement_control_index(run_id, plan_fingerprint)
        experiment = store.read_measurement_experiment(run_id, experiment_id)
        isolation_decision, evidence_policy_profile = (
            store.read_measurement_control_contracts(run_id, plan_fingerprint)
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        plan.measurement_plan_fingerprint != plan_fingerprint
        or plan.experiment_id != experiment_id
        or plan.candidate_fingerprint != candidate_fingerprint
        or experiment.experiment_id != experiment_id
        or dict(raw_plan) != plan.to_dict()
        or request.get("measurement_isolation_decision")
        != isolation_decision.to_dict()
        or request.get("measurement_evidence_policy_profile")
        != evidence_policy_profile.to_dict()
        or not _index_has_resumable_work(index.work_units)
    ):
        return None

    try:
        protected: set[str] = {
            _relative_existing_path(run_path, replay_dir),
            _relative_existing_path(run_path, request_path),
            _relative_existing_path(run_path, candidate_path),
            _relative_existing_path(
                run_path,
                Path(store.measurement_control_plan_path(run_id, plan_fingerprint)),
            ),
            _relative_existing_path(
                run_path,
                Path(store.measurement_experiment_path(run_id, experiment_id)),
            ),
        }
    except ValueError:
        return None
    for optional in (
        run_path / "candidates" / candidate_id,
        run_path / "candidates" / f"{candidate_id}.diff",
        run_path / "candidates" / f"{candidate_id}.md",
    ):
        if optional.exists() and not optional.is_symlink():
            protected.add(_relative_existing_path(run_path, optional))

    overlay_root = request.get("overlay_skill_root")
    if not isinstance(overlay_root, str) or not overlay_root:
        return None
    overlay_path = Path(overlay_root)
    # The request points at ``.../overlays/<candidate>/skills``.  Protect the
    # complete candidate overlay, not only its skills child.
    overlay_owner = overlay_path.parent
    try:
        _relative_existing_path(run_path, overlay_path)
        protected.add(_relative_existing_path(run_path, overlay_owner))
    except ValueError:
        return None

    adaptation = request.get("replay_adaptation")
    if not isinstance(adaptation, Mapping):
        return None
    runtime_paths: list[Path] = []
    for key in ("workspace_seed", "manifest_path", "environment_snapshot_path"):
        value = adaptation.get(key)
        if not isinstance(value, str) or not value:
            return None
        runtime_paths.append(Path(value))
    try:
        for runtime_path in runtime_paths:
            _relative_existing_path(run_path, runtime_path)
        # These artifacts share one immutable replay-adaptation capability root.
        capability_root = runtime_paths[0].parent
        protected.add(_relative_existing_path(run_path, capability_root))
    except ValueError:
        return None

    try:
        return MeasurementResumeCheckpointV1.create(
            source_run_id=run_id,
            candidate_id=candidate_id,
            candidate_fingerprint=candidate_fingerprint,
            measurement_plan_fingerprint=plan_fingerprint,
            experiment_id=experiment_id,
            protected_paths=tuple(sorted(protected)),
        )
    except (TypeError, ValueError):
        return None


def load_measurement_resume_checkpoint(
    store: Any,
    *,
    run_id: str,
    report: Mapping[str, object],
) -> MeasurementResumeCheckpointV1 | None:
    """Load and revalidate the report checkpoint against current artifacts."""

    raw = report.get("measurement_resume_checkpoint")
    if not isinstance(raw, Mapping):
        return None
    try:
        recorded = MeasurementResumeCheckpointV1.from_dict(raw)
    except (TypeError, ValueError):
        return None
    if recorded.source_run_id != run_id:
        return None
    current = discover_measurement_resume_checkpoint(
        store,
        run_id=run_id,
        candidate_id=recorded.candidate_id,
        candidate_fingerprint=recorded.candidate_fingerprint,
    )
    if current is None or current != recorded:
        return None
    return current


def discover_paired_replay_resume_checkpoint(
    store: Any,
    *,
    run_id: str,
    candidate_id: str,
    verified_candidate_package_fingerprint: str,
) -> PairedReplayResumeCheckpointV1 | None:
    """Return a checkpoint for an incomplete, safe pre-authority replay."""

    run_path = Path(store.run_path(run_id))
    replay_dir = run_path / "replay" / candidate_id
    request_path = replay_dir / "request.json"
    progress_path = replay_dir / "members" / "paired_replay_checkpoint.json"
    candidate_path = run_path / "candidates" / f"{candidate_id}.json"
    if not all(
        _regular_file(path)
        for path in (request_path, progress_path, candidate_path)
    ):
        return None
    try:
        request = _read_json_mapping(request_path)
        progress = _read_json_mapping(progress_path)
        candidate = _read_json_mapping(candidate_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        request.get("run_id") != run_id
        or request.get("candidate_id") != candidate_id
        or request.get("verified_candidate_package_fingerprint")
        != verified_candidate_package_fingerprint
        or request.get("measurement_plan") is not None
        or request.get("repetition_semantics") != "per_member_v3"
        or progress.get("schema_version")
        != "aworld.self_evolve.paired_replay_checkpoint.v1"
        or progress.get("schedule") != "progressive_paired"
        or progress.get("resume_safe") is not True
    ):
        return None
    raw_pending = progress.get("pending_case_ids")
    raw_completed = progress.get("comparable_pair_case_ids")
    raw_resumed = progress.get("resumed_pair_case_ids", [])
    if not all(
        isinstance(raw, list)
        and all(isinstance(item, str) and item for item in raw)
        for raw in (raw_pending, raw_completed, raw_resumed)
    ):
        return None
    assert isinstance(raw_pending, list)
    assert isinstance(raw_completed, list)
    assert isinstance(raw_resumed, list)
    pending = tuple(sorted(dict.fromkeys(raw_pending)))
    completed = tuple(sorted(dict.fromkeys(raw_completed)))
    resumed = tuple(sorted(dict.fromkeys(raw_resumed)))
    if (
        not pending
        or not completed
        or not set(completed) - set(resumed)
        or len(pending) != len(raw_pending)
        or len(completed) != len(raw_completed)
        or len(resumed) != len(raw_resumed)
    ):
        return None
    adaptation = request.get("replay_adaptation")
    raw_cases = adaptation.get("cases") if isinstance(adaptation, Mapping) else None
    if not isinstance(raw_cases, list):
        return None
    request_case_ids = {
        item.get("case_id")
        for item in raw_cases
        if isinstance(item, Mapping)
        and isinstance(item.get("case_id"), str)
        and item.get("case_id")
    }
    if not set((*pending, *completed)).issubset(request_case_ids):
        return None
    candidate_fingerprint = _candidate_package_fingerprint(candidate)
    if _FINGERPRINT.fullmatch(candidate_fingerprint) is None:
        return None
    try:
        protected = {
            _relative_existing_path(run_path, replay_dir),
            _relative_existing_path(run_path, request_path),
            _relative_existing_path(run_path, progress_path),
            _relative_existing_path(run_path, candidate_path),
        }
        return PairedReplayResumeCheckpointV1.create(
            source_run_id=run_id,
            candidate_id=candidate_id,
            candidate_fingerprint=candidate_fingerprint,
            verified_candidate_package_fingerprint=(
                verified_candidate_package_fingerprint
            ),
            pending_case_ids=pending,
            completed_pair_case_ids=completed,
            resumed_pair_case_ids=resumed,
            protected_paths=tuple(sorted(protected)),
        )
    except (TypeError, ValueError):
        return None


def load_paired_replay_resume_checkpoint(
    store: Any,
    *,
    run_id: str,
    report: Mapping[str, object],
) -> PairedReplayResumeCheckpointV1 | None:
    """Load and revalidate a recorded paired replay continuation pointer."""

    raw = report.get("paired_replay_resume_checkpoint")
    if not isinstance(raw, Mapping):
        return None
    try:
        recorded = PairedReplayResumeCheckpointV1.from_dict(raw)
    except (TypeError, ValueError):
        return None
    if recorded.source_run_id != run_id:
        return None
    current = discover_paired_replay_resume_checkpoint(
        store,
        run_id=run_id,
        candidate_id=recorded.candidate_id,
        verified_candidate_package_fingerprint=(
            recorded.verified_candidate_package_fingerprint
        ),
    )
    if current is None or current != recorded:
        return None
    return current


def checkpoint_protects_path(
    checkpoint: MeasurementResumeCheckpointV1,
    *,
    run_path: Path,
    path: Path,
) -> bool:
    """Return whether ``path`` is equal to or contains a protected artifact."""

    try:
        relative = path.relative_to(run_path).as_posix()
    except ValueError:
        return False
    return any(
        relative == protected
        or protected.startswith(relative.rstrip("/") + "/")
        or relative.startswith(protected.rstrip("/") + "/")
        for protected in checkpoint.protected_paths
    )


def _index_has_resumable_work(entries: object) -> bool:
    if not isinstance(entries, tuple):
        try:
            entries = tuple(entries)  # type: ignore[arg-type]
        except TypeError:
            return False
    for entry in entries:
        state = getattr(entry, "state", None)
        attempt_count = getattr(entry, "attempt_count", None)
        if isinstance(state, MeasurementWorkUnitState):
            if not state.terminal:
                return True
            if (
                state
                in {
                    MeasurementWorkUnitState.MEMBER_TIMED_OUT,
                    MeasurementWorkUnitState.EVIDENCE_INVALID,
                }
                and isinstance(attempt_count, int)
                and not isinstance(attempt_count, bool)
                and attempt_count < _MAX_RETRY_ATTEMPTS
            ):
                return True
    return False


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _read_json_mapping(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("JSON artifact must be a mapping")
    return value


def _candidate_package_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Recompute the immutable package identity from its persisted JSON."""

    target = candidate.get("target")
    if not isinstance(target, Mapping):
        return ""
    raw_files = candidate.get("files", ())
    if not isinstance(raw_files, (list, tuple)) or not all(
        isinstance(item, Mapping) for item in raw_files
    ):
        return ""
    payload: dict[str, object] = {
        "target": {
            "target_type": target.get("target_type"),
            "target_id": target.get("target_id"),
            "path": target.get("path"),
        },
        "content": candidate.get("content"),
        "files": [
            {
                "path": item.get("path"),
                "operation": item.get("operation"),
                "content": item.get("content"),
                "executable": item.get("executable"),
            }
            for item in raw_files
        ],
    }
    structural_edit_intent = candidate.get("structural_edit_intent")
    if structural_edit_intent is not None:
        payload["structural_edit_intent"] = structural_edit_intent
    return _fingerprint(payload)


def _relative_existing_path(run_path: Path, path: Path) -> str:
    if path.is_symlink() or not path.exists():
        raise ValueError("measurement checkpoint dependency is missing or unsafe")
    root = run_path.resolve(strict=True)
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("measurement checkpoint dependency escaped its run") from exc
    _validate_relative_path(relative)
    return relative


def _validate_relative_path(value: str) -> None:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError("measurement checkpoint path must be canonical and relative")


def _fingerprint(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
