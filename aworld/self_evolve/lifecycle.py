from __future__ import annotations

import json
import math
import os
import shutil
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SelfEvolveArtifactRetentionPolicy:
    keep_latest_runs: int = 2
    raw_artifact_retention_days: int = 0
    stale_run_retention_hours: int = 24
    unreferenced_ingestion_retention_days: int = 7
    prune_unselected_candidate_materializations: bool = True


_TERMINAL_STATUSES = {"succeeded", "failed", "rejected"}
_INTERRUPTED_APPLY_STATUSES = {"backup_written", "applying"}
_RAW_RUN_DIRS = {
    "evidence",
    "repair_conformance",
}
_TEMP_RUN_DIRS = {
    "archived_workspace",
    "archived_worktree",
    "temp_workspace",
    "temporary_workspace",
    "tmp_workspace",
    "workspace_copy",
    "workspace_tmp",
    "worktree_copy",
    "worktree_tmp",
}
_DUPLICATE_OUTPUT_NAMES = {
    "stderr",
    "stderr.log",
    "stderr.txt",
    "stdout",
    "stdout.log",
    "stdout.txt",
}
_ACTIVE_RUN_LEASE = ".active.json"
_CLEANUP_QUARANTINE_DIR = ".artifact-retention-trash"
_EVALUATOR_RAW_DIRS = {
    "extracted",
    "logs",
    "temp",
    "tmp",
    "workspace",
}
_EVALUATOR_RAW_FILE_NAMES = {
    "trajectory.log",
}
_DURABLE_CANDIDATE_REFERENCE_KEYS = {
    "applied_candidate_id",
    "best_candidate_id",
    "selected_candidate_id",
    "source_candidate_id",
}
_RUN_REFERENCE_KEYS = {
    "from_run",
    "from_run_id",
    "latest_run_id",
    "parent_run",
    "parent_run_id",
    "prior_run",
    "prior_run_id",
    "source_request_run_id",
    "source_run",
    "source_run_id",
}
_RUN_REFERENCE_SEQUENCE_KEYS = {
    "campaign_prior_run_ids",
    "campaign_run_ids",
    "parent_run_ids",
    "prior_run_ids",
    "source_run_ids",
}
_NON_LINEAGE_SUBTREES = {"artifact_retention"}


def cleanup_self_evolve_artifacts(
    workspace_root: str | Path,
    *,
    artifact_root: str | Path | None = None,
    policy: SelfEvolveArtifactRetentionPolicy | None = None,
    current_run_id: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    retention = policy or SelfEvolveArtifactRetentionPolicy()
    if retention.keep_latest_runs < 0:
        raise ValueError("keep_latest_runs must be non-negative")
    if retention.raw_artifact_retention_days < 0:
        raise ValueError("raw_artifact_retention_days must be non-negative")
    if retention.stale_run_retention_hours < 0:
        raise ValueError("stale_run_retention_hours must be non-negative")
    if retention.unreferenced_ingestion_retention_days < 0:
        raise ValueError("unreferenced_ingestion_retention_days must be non-negative")

    root = _validated_artifact_root(
        workspace_root,
        artifact_root=artifact_root,
    )
    if not root.exists():
        return _empty_cleanup(retention)
    if not root.is_dir():
        raise ValueError("self-evolve artifact root must be a directory")

    cleanup_time = now if now is not None else time.time()
    cutoff = cleanup_time - (
        retention.raw_artifact_retention_days * 24 * 60 * 60
    )
    stale_run_cutoff = cleanup_time - (
        retention.stale_run_retention_hours * 60 * 60
    )
    ingestion_cutoff = cleanup_time - (
        retention.unreferenced_ingestion_retention_days * 24 * 60 * 60
    )
    removed_paths = _recover_cleanup_quarantine(
        root,
        stale_cutoff=stale_run_cutoff,
    )
    run_dirs = _run_dirs(root)
    run_ids = {path.name for path in run_dirs}
    referenced_run_ids = _referenced_run_ids(run_dirs, run_ids=run_ids)
    referenced_run_ids.update(
        _campaign_referenced_run_ids(root, run_ids=run_ids)
    )
    recent_run_ids = {
        path.name
        for path in sorted(run_dirs, key=_path_mtime, reverse=True)[
            : retention.keep_latest_runs
        ]
    }
    if current_run_id:
        recent_run_ids.add(current_run_id)

    removed_run_ids: set[str] = set()
    archived_run_ids: set[str] = set()
    skipped_runs: list[dict[str, str]] = []

    for run_dir in sorted(run_dirs, key=lambda path: path.name):
        skip_reason = _cleanup_skip_reason(
            run_dir,
            stale_run_cutoff=stale_run_cutoff,
        )
        if skip_reason is not None:
            skipped_runs.append({"run_id": run_dir.name, "reason": skip_reason})
            continue

        if _is_stale_dead_run(run_dir, stale_run_cutoff=stale_run_cutoff):
            _archive_stale_run(run_dir, archived_at=cleanup_time)
            archived_run_ids.add(run_dir.name)
        run_removed = False
        for path in _terminal_cleanup_candidates(
            root,
            run_dir,
            prune_unselected_candidate_materializations=(
                retention.prune_unselected_candidate_materializations
            ),
        ):
            if _is_age_gated_raw_path(path, run_dir=run_dir, root=root) and _path_mtime(path) > cutoff:
                continue
            if not path.exists() and not path.is_symlink():
                continue
            if _remove_path(path, cleanup_root=root):
                removed_paths.append(str(path))
                run_removed = True
        if run_removed:
            removed_run_ids.add(run_dir.name)

    protected_ingestion_ids = _referenced_ingestion_ids(root, run_dirs=run_dirs)
    removed_ingestion_ids: list[str] = []
    ingestion_root = root / "ingestions"
    if ingestion_root.is_dir() and not ingestion_root.is_symlink():
        for ingestion_dir in sorted(ingestion_root.iterdir(), key=lambda path: path.name):
            if (
                not ingestion_dir.is_dir()
                or ingestion_dir.is_symlink()
                or ingestion_dir.name in protected_ingestion_ids
                or _path_mtime(ingestion_dir) > ingestion_cutoff
            ):
                continue
            if _remove_path(ingestion_dir, cleanup_root=root):
                removed_paths.append(str(ingestion_dir))
                removed_ingestion_ids.append(ingestion_dir.name)

    return {
        "policy": asdict(retention),
        "removed_run_count": len(removed_run_ids),
        "removed_run_ids": sorted(removed_run_ids),
        "archived_run_ids": sorted(archived_run_ids),
        "removed_path_count": len(removed_paths),
        "removed_paths": removed_paths,
        "skipped_runs": skipped_runs,
        "protected_run_ids": sorted(recent_run_ids | referenced_run_ids),
        "removed_ingestion_ids": removed_ingestion_ids,
        "protected_ingestion_ids": sorted(protected_ingestion_ids),
    }


def _empty_cleanup(policy: SelfEvolveArtifactRetentionPolicy) -> dict[str, Any]:
    return {
        "policy": asdict(policy),
        "removed_run_count": 0,
        "removed_run_ids": [],
        "archived_run_ids": [],
        "removed_path_count": 0,
        "removed_paths": [],
        "skipped_runs": [],
        "protected_run_ids": [],
        "removed_ingestion_ids": [],
        "protected_ingestion_ids": [],
    }


def _run_dirs(root: Path) -> list[Path]:
    return [
        path
        for path in root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and path.name != "evaluator"
        and ((path / "run.json").exists() or (path / "report.json").exists())
    ]


def _campaign_referenced_run_ids(root: Path, *, run_ids: set[str]) -> set[str]:
    campaign_root = root / "campaigns"
    if not campaign_root.is_dir() or campaign_root.is_symlink():
        return set()
    referenced: set[str] = set()
    for path in campaign_root.glob("*/campaign.json"):
        if path.is_symlink() or path.parent.is_symlink():
            continue
        payload = _read_json_object(path)
        if payload is None or payload.get("schema_version") != (
            "aworld.self_evolve.campaign.v1"
        ):
            continue
        if payload.get("status") not in {"active", "paused"}:
            continue
        raw_run_ids = payload.get("run_ids")
        if not isinstance(raw_run_ids, list):
            continue
        referenced.update(
            str(run_id)
            for run_id in raw_run_ids
            if isinstance(run_id, str) and run_id in run_ids
        )
    return referenced


def _referenced_ingestion_ids(
    root: Path,
    *,
    run_dirs: Iterable[Path],
) -> set[str]:
    referenced: set[str] = set()
    for run_dir in run_dirs:
        payload = _read_json_object(run_dir / "ingestion_ref.json")
        ingestion_id = payload.get("ingestion_id") if payload else None
        if isinstance(ingestion_id, str) and ingestion_id:
            referenced.add(ingestion_id)

    campaign_root = root / "campaigns"
    if not campaign_root.is_dir() or campaign_root.is_symlink():
        return referenced
    for path in campaign_root.glob("*/campaign.json"):
        if path.is_symlink() or path.parent.is_symlink():
            continue
        payload = _read_json_object(path)
        if (
            payload is None
            or payload.get("schema_version") != "aworld.self_evolve.campaign.v1"
            or payload.get("status") not in {"active", "paused"}
        ):
            continue
        source_snapshot = payload.get("source_snapshot")
        if not isinstance(source_snapshot, Mapping):
            continue
        ingestion_id = source_snapshot.get("ingestion_id")
        if isinstance(ingestion_id, str) and ingestion_id:
            referenced.add(ingestion_id)
    return referenced


def _cleanup_skip_reason(
    run_dir: Path,
    *,
    stale_run_cutoff: float,
) -> str | None:
    if _has_interrupted_apply(run_dir):
        return "apply_interrupted"
    if _has_live_run_lease(run_dir):
        return "run_active"
    if _run_status(run_dir) in _TERMINAL_STATUSES:
        return None
    if not _is_stale_dead_run(
        run_dir,
        stale_run_cutoff=stale_run_cutoff,
    ):
        return "run_not_terminal"
    return None


def _run_status(run_dir: Path) -> str | None:
    for name in ("run.json", "report.json"):
        payload = _read_json_object(run_dir / name)
        status = payload.get("status") if payload else None
        if isinstance(status, str) and status:
            return status
    return None


def _has_interrupted_apply(run_dir: Path) -> bool:
    apply_dir = run_dir / "apply"
    if not apply_dir.exists():
        return False
    for journal_path in apply_dir.glob("*.journal.json"):
        payload = _read_json_object(journal_path)
        if payload and payload.get("status") in _INTERRUPTED_APPLY_STATUSES:
            return True
    return False


def _terminal_cleanup_candidates(
    root: Path,
    run_dir: Path,
    *,
    prune_unselected_candidate_materializations: bool,
) -> Iterable[Path]:
    for name in sorted(_RAW_RUN_DIRS | _TEMP_RUN_DIRS):
        yield run_dir / name
    yield from _replay_workspace_paths(run_dir)
    yield from _replay_adaptation_workspace_seed_paths(run_dir)
    yield run_dir / "overlays"
    if prune_unselected_candidate_materializations:
        yield from _candidate_materialization_paths(run_dir)
    for child in sorted(run_dir.iterdir() if run_dir.exists() else ()):
        if child.name in _DUPLICATE_OUTPUT_NAMES or child.suffix in {".stdout", ".stderr"}:
            yield child
    yield from _evaluator_raw_paths(root, run_dir)
    # Release dead/terminal ownership proof only after every other cleanup
    # candidate has been atomically detached.
    yield run_dir / _ACTIVE_RUN_LEASE


def _replay_workspace_paths(run_dir: Path) -> Iterable[Path]:
    replay_dir = run_dir / "replay"
    if not replay_dir.is_dir() or replay_dir.is_symlink():
        return
    for request_path in replay_dir.rglob("execution_request.json"):
        if request_path.is_symlink() or not request_path.is_file():
            continue
        workspace = request_path.parent / "workspace"
        if workspace.exists() or workspace.is_symlink():
            yield workspace


def _replay_adaptation_workspace_seed_paths(run_dir: Path) -> Iterable[Path]:
    adaptation_dir = run_dir / "replay_adaptation"
    if not adaptation_dir.is_dir() or adaptation_dir.is_symlink():
        return
    for dataset_dir in sorted(adaptation_dir.iterdir(), key=lambda path: path.name):
        if not dataset_dir.is_dir() or dataset_dir.is_symlink():
            continue
        for capability_dir in sorted(dataset_dir.iterdir(), key=lambda path: path.name):
            if not capability_dir.is_dir() or capability_dir.is_symlink():
                continue
            seed = capability_dir / "workspace_seed"
            if seed.exists() or seed.is_symlink():
                yield seed


def _evaluator_raw_paths(root: Path, run_dir: Path) -> Iterable[Path]:
    evaluator_root = root / "evaluator"
    evaluator_run = evaluator_root / run_dir.name
    if evaluator_root.is_symlink() or evaluator_run.is_symlink():
        raise ValueError("evaluator artifact path cannot traverse a symlink")
    if not evaluator_run.is_dir():
        return
    for path in evaluator_run.rglob("*"):
        if path.name in _EVALUATOR_RAW_DIRS and (
            path.is_dir() or path.is_symlink()
        ):
            yield path
            continue
        if path.name in _EVALUATOR_RAW_FILE_NAMES or (
            path.is_file()
            and (
                path.name in _DUPLICATE_OUTPUT_NAMES
                or path.suffix in {".stdout", ".stderr"}
            )
        ):
            yield path


def _candidate_materialization_paths(run_dir: Path) -> Iterable[Path]:
    candidate_dir = run_dir / "candidates"
    if not candidate_dir.is_dir() or candidate_dir.is_symlink():
        return
    protected_ids = _durable_candidate_ids(run_dir)
    for path in sorted(candidate_dir.iterdir()):
        candidate_id: str | None = None
        if path.is_dir() and not path.is_symlink():
            candidate_id = path.name
        elif path.is_file() and path.suffix in {".diff", ".md"}:
            candidate_id = path.stem
        if candidate_id is not None and candidate_id not in protected_ids:
            yield path


def _durable_candidate_ids(run_dir: Path) -> set[str]:
    candidate_ids: set[str] = set()
    for name in ("run.json", "report.json"):
        payload = _read_json_object(run_dir / name)
        if payload is not None:
            candidate_ids.update(_candidate_reference_values(payload))
    for journal_path in (run_dir / "apply").glob("*.journal.json"):
        payload = _read_json_object(journal_path)
        candidate_id = payload.get("candidate_id") if payload else None
        if isinstance(candidate_id, str) and candidate_id:
            candidate_ids.add(candidate_id)
    lineage_dir = run_dir / "optimizer_lineage"
    lineage_paths = () if lineage_dir.is_symlink() else lineage_dir.glob("*.json")
    for lineage_path in lineage_paths:
        payload = _read_json_object(lineage_path)
        parent_ids = payload.get("parent_candidate_ids") if payload else None
        if isinstance(parent_ids, list):
            candidate_ids.update(
                value for value in parent_ids if isinstance(value, str) and value
            )
    return candidate_ids


def _candidate_reference_values(value: Any, *, key: str | None = None) -> Iterable[str]:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            yield from _candidate_reference_values(child_value, key=str(child_key))
        return
    if isinstance(value, list):
        for child in value:
            yield from _candidate_reference_values(child, key=key)
        return
    if (
        isinstance(value, str)
        and key in _DURABLE_CANDIDATE_REFERENCE_KEYS
        and value
    ):
        yield value


def _has_live_run_lease(run_dir: Path) -> bool:
    return _run_lease_state(run_dir) in {"foreign", "invalid", "live"}


def _run_lease_state(run_dir: Path) -> str:
    lease_path = run_dir / _ACTIVE_RUN_LEASE
    if not lease_path.exists() and not lease_path.is_symlink():
        return "absent"
    if lease_path.is_symlink():
        return "invalid"
    payload = _read_json_object(lease_path)
    if payload is None:
        return "invalid"
    hostname = payload.get("hostname")
    if not isinstance(hostname, str) or not hostname:
        return "invalid"
    if hostname != socket.gethostname():
        # A foreign host cannot be probed safely. Prefer retaining its run.
        return "foreign"
    pid = payload.get("pid")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
    ):
        return "invalid"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except (OSError, PermissionError):
        return "live"
    return "live"


def _is_stale_dead_run(run_dir: Path, *, stale_run_cutoff: float) -> bool:
    return (
        _run_status(run_dir) not in _TERMINAL_STATUSES
        and _run_lease_state(run_dir) == "dead"
        and _run_activity_mtime(run_dir) <= stale_run_cutoff
    )


def _run_activity_mtime(run_dir: Path) -> float:
    timestamps = [
        _path_mtime(run_dir),
        _path_mtime(run_dir / "run.json"),
        _path_mtime(run_dir / "report.json"),
        _path_mtime(run_dir / _ACTIVE_RUN_LEASE),
    ]
    lease = _read_json_object(run_dir / _ACTIVE_RUN_LEASE)
    started_at = lease.get("started_at") if lease else None
    if (
        isinstance(started_at, (int, float))
        and not isinstance(started_at, bool)
        and math.isfinite(float(started_at))
    ):
        timestamps.append(float(started_at))
    return max(timestamps)


def _archive_stale_run(run_dir: Path, *, archived_at: float) -> None:
    archive_path = run_dir / "artifact_retention_archive.json"
    if archive_path.exists() or archive_path.is_symlink():
        if archive_path.is_symlink() or not archive_path.is_file():
            raise ValueError("stale run archive path is unsafe")
        existing = _read_json_object(archive_path)
        if (
            existing is None
            or existing.get("schema_version")
            != "aworld.self_evolve.stale_run_archive.v1"
            or existing.get("run_id") != run_dir.name
            or existing.get("reason") != "stale_dead_lease"
        ):
            raise ValueError("stale run archive record is invalid")
        return
    lease = _read_json_object(run_dir / _ACTIVE_RUN_LEASE) or {}
    _write_json_atomic(
        archive_path,
        {
            "schema_version": "aworld.self_evolve.stale_run_archive.v1",
            "run_id": run_dir.name,
            "reason": "stale_dead_lease",
            "prior_status": _run_status(run_dir),
            "archived_at": archived_at,
            "lease": lease,
        },
    )


def _is_age_gated_raw_path(path: Path, *, run_dir: Path, root: Path) -> bool:
    if path == run_dir / "overlays":
        return False
    if path.name in _TEMP_RUN_DIRS or path.name in _DUPLICATE_OUTPUT_NAMES:
        return False
    if path.suffix in {".stdout", ".stderr"}:
        return False
    return (
        path in {run_dir / name for name in _RAW_RUN_DIRS}
        or _is_controlled_descendant(
            path,
            root / "evaluator" / run_dir.name,
        )
        or _is_controlled_descendant(path, run_dir / "replay")
        or _is_controlled_descendant(path, run_dir / "replay_adaptation")
    )


def _is_controlled_descendant(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return bool(relative.parts)


def _referenced_run_ids(run_dirs: list[Path], *, run_ids: set[str]) -> set[str]:
    referenced: set[str] = set()
    for owner in run_dirs:
        for json_path in _lineage_reference_files(owner):
            payload = _read_json_object(json_path)
            if payload is None:
                continue
            for value in _iter_reference_values(payload):
                if value in run_ids and value != owner.name:
                    referenced.add(value)
    return referenced


def _lineage_reference_files(run_dir: Path) -> Iterable[Path]:
    for name in ("report.json", "run.json"):
        path = run_dir / name
        if path.exists():
            yield path
    for parent in (run_dir / "optimizer_lineage", run_dir / "lineage"):
        if parent.exists() and not parent.is_symlink():
            yield from parent.glob("*.json")


def _iter_reference_values(
    value: Any,
    *,
    key: str | None = None,
    path: tuple[str, ...] = (),
) -> Iterable[str]:
    if any(part in _NON_LINEAGE_SUBTREES for part in path):
        return
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            normalized_key = str(child_key)
            yield from _iter_reference_values(
                child_value,
                key=normalized_key,
                path=(*path, normalized_key),
            )
        return
    if isinstance(value, list):
        for child in value:
            yield from _iter_reference_values(child, key=key, path=path)
        return
    if (
        isinstance(value, str)
        and key in (_RUN_REFERENCE_KEYS | _RUN_REFERENCE_SEQUENCE_KEYS)
        and value
    ):
        yield value


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _remove_path(path: Path, *, cleanup_root: Path) -> bool:
    _assert_controlled_cleanup_path(path, cleanup_root=cleanup_root)
    if not path.exists() and not path.is_symlink():
        return False
    quarantine_root = cleanup_root / _CLEANUP_QUARANTINE_DIR
    if quarantine_root.is_symlink() or (
        quarantine_root.exists() and not quarantine_root.is_dir()
    ):
        raise ValueError("cleanup quarantine path is unsafe")
    quarantine_root.mkdir(exist_ok=True)
    operation = quarantine_root / uuid.uuid4().hex
    operation.mkdir()
    _write_json_atomic(
        operation / "owner.json",
        {
            "schema_version": "aworld.self_evolve.cleanup_quarantine.v1",
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": time.time(),
            "source_path": str(path),
        },
    )
    quarantined = operation / "artifact"
    try:
        os.replace(path, quarantined)
    except FileNotFoundError:
        shutil.rmtree(operation)
        try:
            quarantine_root.rmdir()
        except OSError:
            pass
        return False
    try:
        shutil.rmtree(operation)
    finally:
        try:
            quarantine_root.rmdir()
        except OSError:
            pass
    return True


def _recover_cleanup_quarantine(
    cleanup_root: Path,
    *,
    stale_cutoff: float,
) -> list[str]:
    quarantine_root = cleanup_root / _CLEANUP_QUARANTINE_DIR
    if not quarantine_root.exists() and not quarantine_root.is_symlink():
        return []
    if quarantine_root.is_symlink() or not quarantine_root.is_dir():
        raise ValueError("cleanup quarantine path is unsafe")
    removed: list[str] = []
    for operation in sorted(quarantine_root.iterdir(), key=lambda path: path.name):
        if operation.is_symlink() or not operation.is_dir():
            raise ValueError("cleanup quarantine operation is unsafe")
        owner = _read_json_object(operation / "owner.json")
        if owner is None and not (operation / "artifact").exists():
            if _path_mtime(operation) <= stale_cutoff:
                shutil.rmtree(operation)
                removed.append(str(operation))
            continue
        if not _is_recoverable_quarantine_owner(
            owner,
            stale_cutoff=stale_cutoff,
        ):
            continue
        shutil.rmtree(operation)
        removed.append(str(operation))
    try:
        quarantine_root.rmdir()
    except OSError:
        pass
    return removed


def _is_recoverable_quarantine_owner(
    owner: Mapping[str, Any] | None,
    *,
    stale_cutoff: float,
) -> bool:
    if (
        owner is None
        or owner.get("schema_version")
        != "aworld.self_evolve.cleanup_quarantine.v1"
    ):
        raise ValueError("cleanup quarantine owner record is invalid")
    hostname = owner.get("hostname")
    pid = owner.get("pid")
    started_at = owner.get("started_at")
    if (
        not isinstance(hostname, str)
        or not hostname
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(started_at, (int, float))
        or isinstance(started_at, bool)
        or not math.isfinite(float(started_at))
    ):
        raise ValueError("cleanup quarantine owner record is invalid")
    if hostname != socket.gethostname() or float(started_at) > stale_cutoff:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, PermissionError):
        return False
    return False


def _assert_controlled_cleanup_path(path: Path, *, cleanup_root: Path) -> None:
    root = Path(os.path.abspath(cleanup_root))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("cleanup path escapes its controlled root") from exc
    if not relative.parts:
        raise ValueError("cleanup cannot remove its controlled root")
    current = root
    if current.is_symlink():
        raise ValueError("cleanup root cannot be a symlink")
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("cleanup path cannot traverse a symlink")


def _validated_artifact_root(
    workspace_root: str | Path,
    *,
    artifact_root: str | Path | None,
) -> Path:
    workspace = Path(os.path.abspath(Path(workspace_root).expanduser()))
    if workspace.is_symlink():
        raise ValueError("workspace cleanup anchor cannot be a symlink")
    if not workspace.is_dir():
        raise ValueError("workspace cleanup anchor must be an existing directory")
    candidate = Path(
        os.path.abspath(
            Path(artifact_root).expanduser()
            if artifact_root is not None
            else workspace / ".aworld" / "self_evolve"
        )
    )
    try:
        relative = candidate.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(
            "self-evolve artifact root must be within the workspace cleanup anchor"
        ) from exc
    if not relative.parts:
        raise ValueError("self-evolve artifact root cannot be the workspace root")
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(
                "self-evolve artifact root cannot traverse a symlink"
            )
    workspace_resolved = workspace.resolve(strict=True)
    candidate_resolved = candidate.resolve(strict=False)
    try:
        resolved_relative = candidate_resolved.relative_to(workspace_resolved)
    except ValueError as exc:
        raise ValueError(
            "resolved self-evolve artifact root escapes the workspace cleanup anchor"
        ) from exc
    if not resolved_relative.parts:
        raise ValueError("self-evolve artifact root cannot resolve to the workspace root")
    return candidate


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
