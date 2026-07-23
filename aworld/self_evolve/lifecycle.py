from __future__ import annotations

import json
import os
import shutil
import socket
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SelfEvolveArtifactRetentionPolicy:
    keep_latest_runs: int = 2
    raw_artifact_retention_days: int = 0
    stale_run_retention_hours: int = 24
    prune_unselected_candidate_materializations: bool = True


_TERMINAL_STATUSES = {"succeeded", "failed", "rejected"}
_INTERRUPTED_APPLY_STATUSES = {"backup_written", "applying"}
_RAW_RUN_DIRS = {
    "evidence",
    "repair_conformance",
    "replay",
    "replay_adaptation",
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
_DURABLE_CANDIDATE_REFERENCE_KEYS = {
    "applied_candidate_id",
    "best_candidate_id",
    "selected_candidate_id",
    "source_candidate_id",
}


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

    root = (
        Path(artifact_root)
        if artifact_root is not None
        else Path(workspace_root) / ".aworld" / "self_evolve"
    )
    if not root.exists():
        return _empty_cleanup(retention)

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

    removed_paths: list[str] = []
    removed_run_ids: set[str] = set()
    skipped_runs: list[dict[str, str]] = []
    cutoff = (now if now is not None else time.time()) - (
        retention.raw_artifact_retention_days * 24 * 60 * 60
    )
    stale_run_cutoff = (now if now is not None else time.time()) - (
        retention.stale_run_retention_hours * 60 * 60
    )

    for run_dir in sorted(run_dirs, key=lambda path: path.name):
        skip_reason = _cleanup_skip_reason(
            run_dir,
            recent_run_ids=recent_run_ids,
            referenced_run_ids=referenced_run_ids,
            stale_run_cutoff=stale_run_cutoff,
        )
        if skip_reason is not None:
            skipped_runs.append({"run_id": run_dir.name, "reason": skip_reason})
            continue

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
            if not path.exists():
                continue
            _remove_path(path)
            removed_paths.append(str(path))
            run_removed = True
        if run_removed:
            removed_run_ids.add(run_dir.name)

    return {
        "policy": asdict(retention),
        "removed_run_count": len(removed_run_ids),
        "removed_run_ids": sorted(removed_run_ids),
        "removed_path_count": len(removed_paths),
        "removed_paths": removed_paths,
        "skipped_runs": skipped_runs,
        "protected_run_ids": sorted(recent_run_ids | referenced_run_ids),
    }


def _empty_cleanup(policy: SelfEvolveArtifactRetentionPolicy) -> dict[str, Any]:
    return {
        "policy": asdict(policy),
        "removed_run_count": 0,
        "removed_run_ids": [],
        "removed_path_count": 0,
        "removed_paths": [],
        "skipped_runs": [],
        "protected_run_ids": [],
    }


def _run_dirs(root: Path) -> list[Path]:
    return [
        path
        for path in root.iterdir()
        if path.is_dir()
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


def _cleanup_skip_reason(
    run_dir: Path,
    *,
    recent_run_ids: set[str],
    referenced_run_ids: set[str],
    stale_run_cutoff: float,
) -> str | None:
    if run_dir.name in recent_run_ids:
        return "recent_run"
    if run_dir.name in referenced_run_ids:
        return "referenced_by_lineage"
    if _has_interrupted_apply(run_dir):
        return "apply_interrupted"
    if _run_status(run_dir) not in _TERMINAL_STATUSES:
        if _has_live_run_lease(run_dir):
            return "run_active"
        if _path_mtime(run_dir) > stale_run_cutoff:
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
    yield run_dir / "overlays"
    yield run_dir / _ACTIVE_RUN_LEASE
    if prune_unselected_candidate_materializations:
        yield from _candidate_materialization_paths(run_dir)
    for child in sorted(run_dir.iterdir() if run_dir.exists() else ()):
        if child.name in _DUPLICATE_OUTPUT_NAMES or child.suffix in {".stdout", ".stderr"}:
            yield child
    evaluator_dir = root / "evaluator" / run_dir.name
    yield evaluator_dir


def _candidate_materialization_paths(run_dir: Path) -> Iterable[Path]:
    candidate_dir = run_dir / "candidates"
    if not candidate_dir.is_dir():
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
    for lineage_path in (run_dir / "optimizer_lineage").glob("*.json"):
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
    payload = _read_json_object(run_dir / _ACTIVE_RUN_LEASE)
    if payload is None:
        return False
    hostname = payload.get("hostname")
    if isinstance(hostname, str) and hostname and hostname != socket.gethostname():
        # A foreign host cannot be probed safely. Prefer retaining its run.
        return True
    pid = payload.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, PermissionError):
        return True
    return True


def _is_age_gated_raw_path(path: Path, *, run_dir: Path, root: Path) -> bool:
    if path == run_dir / "overlays":
        return False
    if path.name in _TEMP_RUN_DIRS or path.name in _DUPLICATE_OUTPUT_NAMES:
        return False
    if path.suffix in {".stdout", ".stderr"}:
        return False
    return path in {run_dir / name for name in _RAW_RUN_DIRS} or path == root / "evaluator" / run_dir.name


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
        if parent.exists():
            yield from parent.glob("*.json")


def _iter_reference_values(value: Any, *, key: str | None = None) -> Iterable[str]:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            yield from _iter_reference_values(child_value, key=str(child_key))
        return
    if isinstance(value, list):
        for child in value:
            yield from _iter_reference_values(child, key=key)
        return
    if (
        isinstance(value, str)
        and key is not None
        and key != "run_id"
        and ("run_id" in key or key in {"from_run", "source_run", "parent_run"})
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


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)
