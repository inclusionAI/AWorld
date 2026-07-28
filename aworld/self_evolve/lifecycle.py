from __future__ import annotations

import errno
import json
import math
import os
import re
import stat
import socket
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


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
_RETENTION_TRANSACTION_DIR = "artifact_retention_transactions"
_RETENTION_TRANSACTION_SCHEMA = "aworld.self_evolve.artifact_retention_transaction.v1"
_SAFE_RUN_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}")
_FD_CLEANUP_SUPPORTED = (
    all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW"))
    and all(
        operation in os.supports_dir_fd
        for operation in (
            os.open,
            os.mkdir,
            os.rename,
            os.rmdir,
            os.stat,
            os.unlink,
        )
    )
    and os.listdir in os.supports_fd
    and os.stat in os.supports_follow_symlinks
)
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


@dataclass
class _RetentionTransaction:
    transaction_id: str
    run_id: str
    directory_fd: int
    payload: dict[str, Any]

    @property
    def filename(self) -> str:
        return f"{self.transaction_id}.json"

    def persist(self) -> None:
        _write_json_at(self.directory_fd, self.filename, self.payload)

    def record_intent(self, path: str) -> None:
        self.payload["pending_path"] = path
        self.payload["updated_at"] = time.time()
        self.persist()

    def clear_intent(self) -> None:
        self.payload["pending_path"] = None
        self.payload["updated_at"] = time.time()
        self.persist()

    def record_removed(self, path: str) -> None:
        result = self.payload["result"]
        removed_paths = result["removed_paths"]
        if path not in removed_paths:
            removed_paths.append(path)
        result["removed_path_count"] = len(removed_paths)
        self.payload["pending_path"] = None
        self.payload["updated_at"] = time.time()
        self.persist()

    def complete(self, result: Mapping[str, Any]) -> None:
        self.payload["status"] = "completed"
        self.payload["pending_path"] = None
        self.payload["updated_at"] = time.time()
        self.payload["result"] = {"status": "completed", **dict(result)}
        self.persist()

    def fail(self, error: str) -> None:
        self.payload["status"] = "failed"
        self.payload["updated_at"] = time.time()
        result = self.payload["result"]
        result["status"] = "failed"
        result["error"] = error
        pending_path = self.payload.get("pending_path")
        if isinstance(pending_path, str) and pending_path:
            result["uncertain_removed_paths"] = [pending_path]
        self.persist()

    def close(self) -> None:
        os.close(self.directory_fd)


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

    with _bound_artifact_root(
        workspace_root,
        artifact_root=artifact_root,
    ) as bound_root:
        if bound_root is None:
            return _empty_cleanup(retention)
        root, root_fd = bound_root
        return _cleanup_bound_artifact_root(
            root,
            root_fd=root_fd,
            retention=retention,
            current_run_id=current_run_id,
            now=now,
        )


def _cleanup_bound_artifact_root(
    root: Path,
    *,
    root_fd: int,
    retention: SelfEvolveArtifactRetentionPolicy,
    current_run_id: str | None,
    now: float | None,
) -> dict[str, Any]:
    cleanup_time = now if now is not None else time.time()
    transaction = _begin_retention_transaction(
        root,
        root_fd=root_fd,
        current_run_id=current_run_id,
        retention=retention,
        started_at=cleanup_time,
    )
    try:
        result = _perform_bound_artifact_cleanup(
            root,
            root_fd=root_fd,
            retention=retention,
            current_run_id=current_run_id,
            cleanup_time=cleanup_time,
            transaction=transaction,
        )
        if transaction is not None:
            transaction.complete(result)
        return result
    except BaseException as exc:
        if transaction is not None:
            try:
                transaction.fail(str(exc))
            except Exception:
                # Preserve the cleanup failure. The previously fsynced
                # prepared transaction still carries the last durable intent.
                pass
        raise
    finally:
        if transaction is not None:
            transaction.close()


def _perform_bound_artifact_cleanup(
    root: Path,
    *,
    root_fd: int,
    retention: SelfEvolveArtifactRetentionPolicy,
    current_run_id: str | None,
    cleanup_time: float,
    transaction: _RetentionTransaction | None,
) -> dict[str, Any]:
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
        cleanup_root_fd=root_fd,
        stale_cutoff=stale_run_cutoff,
        transaction=transaction,
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
            _archive_stale_run(
                run_dir,
                archived_at=cleanup_time,
                cleanup_root=root,
                cleanup_root_fd=root_fd,
            )
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
            if _remove_path(
                path,
                cleanup_root=root,
                cleanup_root_fd=root_fd,
                transaction=transaction,
            ):
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
            if _remove_path(
                ingestion_dir,
                cleanup_root=root,
                cleanup_root_fd=root_fd,
                transaction=transaction,
            ):
                removed_paths.append(str(ingestion_dir))
                removed_ingestion_ids.append(ingestion_dir.name)

    transaction_ids = (
        [transaction.transaction_id]
        if transaction is not None
        else []
    )
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
        "transaction_ids": transaction_ids,
    }


def read_self_evolve_retention_transactions(
    workspace_root: str | Path,
    *,
    artifact_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    with _bound_artifact_root(
        workspace_root,
        artifact_root=artifact_root,
    ) as bound_root:
        if bound_root is None:
            return []
        root, root_fd = bound_root
        transactions: list[dict[str, Any]] = []
        for run_id in sorted(os.listdir(root_fd)):
            if _SAFE_RUN_ID.fullmatch(run_id) is None:
                continue
            entry = os.stat(run_id, dir_fd=root_fd, follow_symlinks=False)
            if not stat.S_ISDIR(entry.st_mode):
                continue
            run_fd = _open_directory_at(root_fd, run_id)
            try:
                try:
                    transaction_fd = _open_directory_at(
                        run_fd,
                        _RETENTION_TRANSACTION_DIR,
                    )
                except FileNotFoundError:
                    continue
                try:
                    for filename in sorted(os.listdir(transaction_fd)):
                        if re.fullmatch(r"[0-9a-f]{32}\.json", filename) is None:
                            continue
                        payload = _read_json_at(transaction_fd, filename)
                        transaction = _validated_retention_transaction(
                            payload,
                            root=root,
                            run_id=run_id,
                            transaction_id=filename.removesuffix(".json"),
                        )
                        if transaction is not None:
                            transactions.append(transaction)
                finally:
                    os.close(transaction_fd)
            finally:
                os.close(run_fd)
        return transactions


def acknowledge_self_evolve_retention_transactions(
    workspace_root: str | Path,
    *,
    artifact_root: str | Path | None = None,
    run_id: str,
    transaction_ids: Iterable[str],
) -> None:
    if _SAFE_RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run_id is unsafe for retention acknowledgement")
    normalized_ids = tuple(dict.fromkeys(str(value) for value in transaction_ids))
    if any(re.fullmatch(r"[0-9a-f]{32}", value) is None for value in normalized_ids):
        raise ValueError("retention transaction id is invalid")
    if not normalized_ids:
        return
    with _bound_artifact_root(
        workspace_root,
        artifact_root=artifact_root,
    ) as bound_root:
        if bound_root is None:
            return
        root, root_fd = bound_root
        try:
            run_fd = _open_directory_at(root_fd, run_id)
        except FileNotFoundError:
            return
        try:
            try:
                transaction_fd = _open_directory_at(
                    run_fd,
                    _RETENTION_TRANSACTION_DIR,
                )
            except FileNotFoundError:
                return
            try:
                for transaction_id in normalized_ids:
                    filename = f"{transaction_id}.json"
                    payload = _read_json_at(transaction_fd, filename)
                    if payload is None:
                        continue
                    transaction = _validated_retention_transaction(
                        payload,
                        root=root,
                        run_id=run_id,
                        transaction_id=transaction_id,
                    )
                    if transaction is None:
                        continue
                    os.unlink(filename, dir_fd=transaction_fd)
                    os.fsync(transaction_fd)
            finally:
                os.close(transaction_fd)
            _remove_empty_directory_at(run_fd, _RETENTION_TRANSACTION_DIR)
        finally:
            os.close(run_fd)


def _validated_retention_transaction(
    payload: Mapping[str, Any] | None,
    *,
    root: Path,
    run_id: str,
    transaction_id: str,
) -> dict[str, Any] | None:
    if (
        payload is None
        or payload.get("schema_version") != _RETENTION_TRANSACTION_SCHEMA
        or payload.get("run_id") != run_id
        or payload.get("transaction_id") != transaction_id
        or payload.get("artifact_root") != str(root)
        or payload.get("status") not in {"prepared", "completed", "failed"}
    ):
        raise ValueError("artifact retention transaction is invalid")
    raw_result = payload.get("result")
    if not isinstance(raw_result, Mapping):
        raise ValueError("artifact retention transaction result is invalid")
    hostname = payload.get("hostname")
    pid = payload.get("pid")
    if (
        not isinstance(hostname, str)
        or not hostname
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
    ):
        raise ValueError("artifact retention transaction owner is invalid")
    status = payload.get("status")
    if status == "prepared":
        if hostname != socket.gethostname():
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        except (OSError, PermissionError):
            return None
        else:
            return None
    result = dict(raw_result)
    transaction_ids = {
        value
        for value in result.get("transaction_ids", ())
        if isinstance(value, str) and value
    } if isinstance(result.get("transaction_ids"), (list, tuple, set)) else set()
    transaction_ids.add(transaction_id)
    result["transaction_ids"] = sorted(transaction_ids)
    if status == "completed":
        result["status"] = "completed"
    else:
        result["status"] = "failed"
        result.setdefault(
            "error",
            "artifact retention transaction did not complete",
        )
        pending_path = payload.get("pending_path")
        if isinstance(pending_path, str) and pending_path:
            uncertain = {
                value
                for value in result.get("uncertain_removed_paths", ())
                if isinstance(value, str) and value
            } if isinstance(
                result.get("uncertain_removed_paths"),
                (list, tuple, set),
            ) else set()
            uncertain.add(pending_path)
            result["uncertain_removed_paths"] = sorted(uncertain)
    return {
        "run_id": run_id,
        "transaction_id": transaction_id,
        "result": result,
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
        "transaction_ids": [],
    }


def _begin_retention_transaction(
    root: Path,
    *,
    root_fd: int,
    current_run_id: str | None,
    retention: SelfEvolveArtifactRetentionPolicy,
    started_at: float,
) -> _RetentionTransaction | None:
    if current_run_id is None:
        return None
    if _SAFE_RUN_ID.fullmatch(current_run_id) is None:
        raise ValueError("current_run_id is unsafe for retention transaction")
    run_fd = _open_or_create_directory_at(root_fd, current_run_id)
    try:
        transaction_directory_fd = _open_or_create_directory_at(
            run_fd,
            _RETENTION_TRANSACTION_DIR,
        )
    finally:
        os.close(run_fd)
    transaction_id = uuid.uuid4().hex
    initial_result = _empty_cleanup(retention)
    initial_result["transaction_ids"] = [transaction_id]
    transaction = _RetentionTransaction(
        transaction_id=transaction_id,
        run_id=current_run_id,
        directory_fd=transaction_directory_fd,
        payload={
            "schema_version": _RETENTION_TRANSACTION_SCHEMA,
            "transaction_id": transaction_id,
            "run_id": current_run_id,
            "status": "prepared",
            "started_at": started_at,
            "updated_at": started_at,
            "pending_path": None,
            "artifact_root": str(root),
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "result": initial_result,
        },
    )
    try:
        transaction.persist()
    except Exception:
        transaction.close()
        raise
    return transaction


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


def _archive_stale_run(
    run_dir: Path,
    *,
    archived_at: float,
    cleanup_root: Path,
    cleanup_root_fd: int,
) -> None:
    archive_path = run_dir / "artifact_retention_archive.json"
    parent_fd, leaf = _open_bound_parent(
        archive_path,
        cleanup_root=cleanup_root,
        cleanup_root_fd=cleanup_root_fd,
    )
    try:
        existing = _read_json_at(parent_fd, leaf)
        if existing is not None:
            entry = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(entry.st_mode):
                raise ValueError("stale run archive path is unsafe")
            if (
                existing.get("schema_version")
                != "aworld.self_evolve.stale_run_archive.v1"
                or existing.get("run_id") != run_dir.name
                or existing.get("reason") != "stale_dead_lease"
            ):
                raise ValueError("stale run archive record is invalid")
            return
        if _entry_exists_at(parent_fd, leaf):
            raise ValueError("stale run archive record is invalid")
        lease = _read_json_object(run_dir / _ACTIVE_RUN_LEASE) or {}
        _write_json_at(
            parent_fd,
            leaf,
            {
                "schema_version": "aworld.self_evolve.stale_run_archive.v1",
                "run_id": run_dir.name,
                "reason": "stale_dead_lease",
                "prior_status": _run_status(run_dir),
                "archived_at": archived_at,
                "lease": lease,
            },
        )
    finally:
        os.close(parent_fd)


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


def _remove_path(
    path: Path,
    *,
    cleanup_root: Path,
    cleanup_root_fd: int,
    transaction: _RetentionTransaction | None,
) -> bool:
    try:
        parent_fd, leaf = _open_bound_parent(
            path,
            cleanup_root=cleanup_root,
            cleanup_root_fd=cleanup_root_fd,
        )
    except FileNotFoundError:
        return False
    trash_fd = -1
    operation_fd = -1
    operation_name = uuid.uuid4().hex
    try:
        trash_fd = _open_or_create_directory_at(
            cleanup_root_fd,
            _CLEANUP_QUARANTINE_DIR,
        )
        os.mkdir(operation_name, mode=0o700, dir_fd=trash_fd)
        os.fsync(trash_fd)
        operation_fd = _open_directory_at(trash_fd, operation_name)
        _write_json_at(
            operation_fd,
            "owner.json",
            {
                "schema_version": "aworld.self_evolve.cleanup_quarantine.v1",
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "started_at": time.time(),
                "source_path": str(path),
            },
        )
        if transaction is not None:
            transaction.record_intent(str(path))
        try:
            os.rename(
                leaf,
                "artifact",
                src_dir_fd=parent_fd,
                dst_dir_fd=operation_fd,
            )
            os.fsync(parent_fd)
            os.fsync(operation_fd)
        except FileNotFoundError:
            if transaction is not None:
                transaction.clear_intent()
            os.close(operation_fd)
            operation_fd = -1
            _remove_tree_entry(trash_fd, operation_name)
            _remove_empty_directory_at(
                cleanup_root_fd,
                _CLEANUP_QUARANTINE_DIR,
            )
            return False
        os.close(operation_fd)
        operation_fd = -1
        _remove_tree_entry(trash_fd, operation_name)
        _remove_empty_directory_at(
            cleanup_root_fd,
            _CLEANUP_QUARANTINE_DIR,
        )
        if transaction is not None:
            transaction.record_removed(str(path))
        return True
    finally:
        if operation_fd >= 0:
            os.close(operation_fd)
        if trash_fd >= 0:
            os.close(trash_fd)
        os.close(parent_fd)


def _recover_cleanup_quarantine(
    cleanup_root: Path,
    *,
    cleanup_root_fd: int,
    stale_cutoff: float,
    transaction: _RetentionTransaction | None,
) -> list[str]:
    try:
        quarantine_fd = _open_directory_at(
            cleanup_root_fd,
            _CLEANUP_QUARANTINE_DIR,
        )
    except FileNotFoundError:
        return []
    removed: list[str] = []
    try:
        for operation_name in sorted(os.listdir(quarantine_fd)):
            operation_fd = _open_directory_at(quarantine_fd, operation_name)
            try:
                owner = _read_json_at(operation_fd, "owner.json")
                artifact_exists = _entry_exists_at(operation_fd, "artifact")
                if owner is None and not artifact_exists:
                    if os.fstat(operation_fd).st_mtime <= stale_cutoff:
                        operation_path = str(
                            cleanup_root
                            / _CLEANUP_QUARANTINE_DIR
                            / operation_name
                        )
                        if transaction is not None:
                            transaction.record_intent(operation_path)
                        os.close(operation_fd)
                        operation_fd = -1
                        _remove_tree_entry(quarantine_fd, operation_name)
                        removed.append(operation_path)
                        if transaction is not None:
                            transaction.record_removed(operation_path)
                    continue
                if not _is_recoverable_quarantine_owner(
                    owner,
                    stale_cutoff=stale_cutoff,
                ):
                    continue
                os.close(operation_fd)
                operation_fd = -1
                operation_path = str(
                    cleanup_root
                    / _CLEANUP_QUARANTINE_DIR
                    / operation_name
                )
                if transaction is not None:
                    transaction.record_intent(operation_path)
                _remove_tree_entry(quarantine_fd, operation_name)
                removed.append(operation_path)
                if transaction is not None:
                    transaction.record_removed(operation_path)
            finally:
                if operation_fd >= 0:
                    os.close(operation_fd)
        _remove_empty_directory_at(
            cleanup_root_fd,
            _CLEANUP_QUARANTINE_DIR,
        )
        return removed
    finally:
        os.close(quarantine_fd)


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


def _controlled_relative_path(path: Path, *, cleanup_root: Path) -> Path:
    root = Path(os.path.abspath(cleanup_root))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("cleanup path escapes its controlled root") from exc
    if not relative.parts:
        raise ValueError("cleanup cannot remove its controlled root")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("cleanup path contains an unsafe component")
    return relative


def _artifact_root_paths(
    workspace_root: str | Path,
    *,
    artifact_root: str | Path | None,
) -> tuple[Path, Path, tuple[int, int], Path, Path, tuple[int, int] | None, Path]:
    workspace_input = Path(os.path.abspath(Path(workspace_root).expanduser()))
    workspace_identity = _path_directory_identity(
        workspace_input,
        label="workspace cleanup anchor",
    )
    try:
        workspace = workspace_input.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise ValueError(
            "workspace cleanup anchor must be an existing directory"
        ) from exc
    if (
        _path_directory_identity(
            workspace,
            label="resolved workspace cleanup anchor",
        )
        != workspace_identity
    ):
        raise ValueError("workspace cleanup anchor changed during binding")

    candidate_input = Path(
        os.path.abspath(
            Path(artifact_root).expanduser()
            if artifact_root is not None
            else workspace_input / ".aworld" / "self_evolve"
        )
    )
    candidate_identity = _path_directory_identity(
        candidate_input,
        label="self-evolve artifact root",
        missing_ok=True,
    )
    try:
        candidate = candidate_input.resolve(strict=False)
    except RuntimeError as exc:
        raise ValueError(
            "self-evolve artifact root contains a symlink loop"
        ) from exc
    if candidate_identity is not None and (
        _path_directory_identity(
            candidate,
            label="resolved self-evolve artifact root",
        )
        != candidate_identity
    ):
        raise ValueError("self-evolve artifact root changed during binding")
    try:
        relative = candidate.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(
            "self-evolve artifact root must resolve within the workspace cleanup "
            "anchor; symlink or alias escapes are not allowed"
        ) from exc
    logical_candidate = Path(os.path.abspath(workspace_input / relative))
    physical_candidate = Path(os.path.abspath(workspace / relative))
    if candidate_input not in {logical_candidate, physical_candidate}:
        raise ValueError(
            "self-evolve artifact root cannot traverse a symlink or alias "
            "below the workspace cleanup anchor"
        )
    if not relative.parts:
        raise ValueError("self-evolve artifact root cannot be the workspace root")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("self-evolve artifact root contains an unsafe component")
    return (
        workspace_input,
        workspace,
        workspace_identity,
        candidate_input,
        candidate,
        candidate_identity,
        relative,
    )


@contextmanager
def _bound_artifact_root(
    workspace_root: str | Path,
    *,
    artifact_root: str | Path | None,
) -> Iterator[tuple[Path, int] | None]:
    (
        workspace_input,
        workspace,
        workspace_identity,
        candidate_input,
        candidate,
        candidate_identity,
        relative,
    ) = _artifact_root_paths(
        workspace_root,
        artifact_root=artifact_root,
    )
    try:
        workspace_fd = _open_absolute_directory(workspace)
    except FileNotFoundError as exc:
        raise ValueError(
            "workspace cleanup anchor must be an existing directory"
        ) from exc
    root_fd = -1
    try:
        _assert_bound_path_identity(
            workspace_input,
            workspace_fd,
            expected_identity=workspace_identity,
            label="workspace cleanup anchor",
        )
        try:
            root_fd = _walk_directory_fd(workspace_fd, relative.parts)
        except FileNotFoundError:
            if candidate_identity is not None:
                raise ValueError(
                    "self-evolve artifact root changed during binding"
                )
            try:
                current_candidate = candidate_input.resolve(strict=False)
            except RuntimeError as exc:
                raise ValueError(
                    "self-evolve artifact root changed during binding"
                ) from exc
            if current_candidate != candidate:
                raise ValueError(
                    "self-evolve artifact root changed during binding"
                )
            yield None
            return
        if candidate_identity is None:
            raise ValueError("self-evolve artifact root appeared during binding")
        _assert_bound_path_identity(
            candidate_input,
            root_fd,
            expected_identity=candidate_identity,
            label="self-evolve artifact root",
        )
        yield candidate, root_fd
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        os.close(workspace_fd)


def _path_directory_identity(
    path: Path,
    *,
    label: str,
    missing_ok: bool = False,
) -> tuple[int, int] | None:
    try:
        path_stat = os.stat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ValueError(f"{label} must be an existing directory") from None
    if not stat.S_ISDIR(path_stat.st_mode):
        raise ValueError(f"{label} must be an existing directory")
    return path_stat.st_dev, path_stat.st_ino


def _assert_bound_path_identity(
    path: Path,
    directory_fd: int,
    *,
    expected_identity: tuple[int, int],
    label: str,
) -> None:
    try:
        path_identity = _path_directory_identity(path, label=label)
        bound_stat = os.fstat(directory_fd)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ValueError(f"{label} changed during binding") from exc
    bound_identity = bound_stat.st_dev, bound_stat.st_ino
    if (
        not stat.S_ISDIR(bound_stat.st_mode)
        or path_identity != expected_identity
        or bound_identity != expected_identity
    ):
        raise ValueError(f"{label} changed during binding")


def _directory_open_flags() -> int:
    if not _FD_CLEANUP_SUPPORTED:
        raise RuntimeError("race-safe artifact cleanup is unsupported on this platform")
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute():
        raise ValueError("cleanup anchor must be absolute")
    current_fd = os.open("/", _directory_open_flags())
    try:
        for part in path.parts[1:]:
            next_fd = _open_directory_at(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _walk_directory_fd(parent_fd: int, parts: Iterable[str]) -> int:
    current_fd = os.dup(parent_fd)
    try:
        for part in parts:
            next_fd = _open_directory_at(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_directory_at(parent_fd: int, name: str) -> int:
    if not name or name in {".", ".."} or "/" in name:
        raise ValueError("unsafe directory component")
    try:
        return os.open(name, _directory_open_flags(), dir_fd=parent_fd)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise
        if exc.errno in {
            errno.ELOOP,
            errno.ENOTDIR,
        }:
            raise ValueError("cleanup path cannot traverse a symlink") from exc
        raise


def _open_or_create_directory_at(parent_fd: int, name: str) -> int:
    created = False
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    if created:
        os.fsync(parent_fd)
    return _open_directory_at(parent_fd, name)


def _open_bound_parent(
    path: Path,
    *,
    cleanup_root: Path,
    cleanup_root_fd: int,
) -> tuple[int, str]:
    relative = _controlled_relative_path(path, cleanup_root=cleanup_root)
    parent_fd = _walk_directory_fd(
        cleanup_root_fd,
        relative.parts[:-1],
    )
    return parent_fd, relative.parts[-1]


def _remove_tree_entry(parent_fd: int, name: str) -> None:
    entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(entry.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return
    child_fd = _open_directory_at(parent_fd, name)
    try:
        for child_name in sorted(os.listdir(child_fd)):
            _remove_tree_entry(child_fd, child_name)
        os.rmdir(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(child_fd)


def _remove_empty_directory_at(parent_fd: int, name: str) -> None:
    try:
        os.rmdir(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno in {
            errno.ENOTEMPTY,
            errno.EBUSY,
        }:
            return
        raise


def _entry_exists_at(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _read_json_at(parent_fd: int, name: str) -> dict[str, Any] | None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("JSON artifact cannot be a symlink") from exc
        raise
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as handle:
            try:
                payload = json.load(handle)
            except json.JSONDecodeError:
                return None
    finally:
        os.close(descriptor)
    return payload if isinstance(payload, dict) else None


def _write_json_at(
    parent_fd: int,
    name: str,
    payload: Mapping[str, Any],
) -> None:
    if not name or name in {".", ".."} or "/" in name:
        raise ValueError("unsafe JSON artifact name")
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            flags,
            0o600,
            dir_fd=parent_fd,
        )
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("atomic JSON write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
