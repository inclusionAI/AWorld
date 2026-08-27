"""Transactional standard-library SQLite repository for AWorld Cloud.

SQLite is an implementation detail of this module.  Service and worker code depend
on the storage-neutral protocols in :mod:`aworld.cloud.repository`.
"""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from aworld.cloud.errors import (
    CloudError,
    CloudErrorCode,
    InvalidTransitionError,
    WorkspaceBusyError,
)
from aworld.cloud.models import (
    ACTIVE_RUN_STATES,
    RUN_REQUEST_SCHEMA_VERSION,
    TERMINAL_RUN_STATES,
    TERMINAL_WORKSPACE_STATES,
    Batch,
    BatchId,
    BenchmarkMetadata,
    BenchmarkOutcome,
    EventId,
    ExecutorId,
    FileId,
    MountAccessMode,
    Run,
    RunEvent,
    RunFile,
    RunFileKind,
    RunId,
    RunMode,
    RunState,
    TrajectoryFormat,
    TrajectoryManifest,
    TrajectoryRole,
    Workspace,
    WorkspaceId,
    WorkspaceMount,
    WorkspaceState,
    aggregate_batch,
    can_transition_run,
    can_transition_workspace,
    format_utc_timestamp,
    parse_utc_timestamp,
    utc_now,
)
from aworld.cloud.repository import Page

SCHEMA_VERSION = 3
DEFAULT_BUSY_TIMEOUT = timedelta(seconds=5)
_MAX_PAGE_SIZE = 1000
_ACTIVE_STATE_VALUES = tuple(state.value for state in ACTIVE_RUN_STATES)

_SCHEMA_V1 = (
    """
    CREATE TABLE workspaces (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        profile_name TEXT NOT NULL,
        state TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        runtime_image TEXT NOT NULL,
        writable_repo_path TEXT NOT NULL,
        codex_home_path TEXT NOT NULL,
        workdir TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        released_at TEXT
    )
    """,
    """
    CREATE TABLE workspace_mounts (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        container_path TEXT NOT NULL,
        host_path TEXT NOT NULL,
        access_mode TEXT NOT NULL,
        PRIMARY KEY (workspace_id, ordinal),
        UNIQUE (workspace_id, container_path)
    )
    """,
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        state TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        retry_of_run_id TEXT REFERENCES runs(id),
        task TEXT NOT NULL,
        model TEXT,
        worker_id TEXT,
        lease_expires_at TEXT,
        executor_id TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        exit_code INTEGER,
        error_code TEXT,
        error_message TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX one_active_run_per_workspace
    ON runs(workspace_id)
    WHERE state IN ('starting', 'running', 'cancelling')
    """,
    """
    CREATE INDEX runs_queue_order
    ON runs(state, created_at, id)
    """,
    """
    CREATE INDEX runs_expired_lease
    ON runs(state, lease_expires_at, id)
    """,
    """
    CREATE TABLE run_events (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (run_id, sequence)
    )
    """,
    """
    CREATE TABLE run_files (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (run_id, relative_path)
    )
    """,
    """
    CREATE TABLE idempotency_keys (
        scope TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (scope, idempotency_key)
    )
    """,
)

_SCHEMA_V2 = (
    (
        "ALTER TABLE runs ADD COLUMN request_schema_version TEXT NOT NULL "
        f"DEFAULT '{RUN_REQUEST_SCHEMA_VERSION}'"
    ),
    "ALTER TABLE runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'query'",
    "ALTER TABLE runs ADD COLUMN benchmark_json TEXT",
    "ALTER TABLE runs ADD COLUMN benchmark_reward REAL",
    "ALTER TABLE runs ADD COLUMN benchmark_result_json TEXT",
    "ALTER TABLE run_files ADD COLUMN trajectory_format TEXT",
    "ALTER TABLE run_files ADD COLUMN trajectory_schema_version TEXT",
    "ALTER TABLE run_files ADD COLUMN trajectory_role TEXT",
)

_SCHEMA_V3 = (
    """
    CREATE TABLE batches (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        name TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "ALTER TABLE runs ADD COLUMN batch_id TEXT REFERENCES batches(id)",
    "CREATE INDEX batches_order ON batches(created_at, id)",
    "CREATE INDEX batches_workspace_order ON batches(workspace_id, created_at, id)",
    "CREATE INDEX runs_batch_order ON runs(batch_id, created_at, id)",
)


def _optional_timestamp(value: datetime | None) -> str | None:
    return None if value is None else format_utc_timestamp(value)


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    return None if value is None else parse_utc_timestamp(value)


def _validate_page_size(limit: int) -> None:
    if limit < 1 or limit > _MAX_PAGE_SIZE:
        raise CloudError(
            CloudErrorCode.INVALID_REQUEST,
            f"page size must be between 1 and {_MAX_PAGE_SIZE}",
        )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported event payload value: {type(value).__name__}")


def _encode_cursor(
    kind: str,
    cursor: tuple[str, str],
    filters: Mapping[str, str | None],
) -> str:
    payload = json.dumps(
        {"cursor": cursor, "filters": dict(filters), "kind": kind, "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    token: str | None,
    *,
    kind: str,
    filters: Mapping[str, str | None],
) -> tuple[str, str] | None:
    if token is None:
        return None
    if not token or len(token) > 4096:
        raise CloudError(CloudErrorCode.INVALID_REQUEST, "invalid page token")
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.b64decode(
            token + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudError(
            CloudErrorCode.INVALID_REQUEST,
            "invalid page token",
        ) from exc
    if not isinstance(payload, dict):
        raise CloudError(CloudErrorCode.INVALID_REQUEST, "invalid page token")
    cursor = payload.get("cursor")
    if (
        payload.get("version") != 1
        or payload.get("kind") != kind
        or payload.get("filters") != dict(filters)
        or not isinstance(cursor, list)
        or len(cursor) != 2
        or not all(isinstance(value, str) for value in cursor)
    ):
        raise CloudError(CloudErrorCode.INVALID_REQUEST, "invalid page token")
    return cursor[0], cursor[1]


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        try:
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


class SQLiteCloudRepository:
    """Single-node SQLite implementation of the cloud repository protocols."""

    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout: timedelta = DEFAULT_BUSY_TIMEOUT,
    ) -> None:
        self._database_path = Path(database_path)
        if busy_timeout <= timedelta(0):
            raise ValueError("busy_timeout must be positive")
        self._busy_timeout_ms = max(1, int(busy_timeout.total_seconds() * 1000))
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    async def initialize(self) -> None:
        with self._lock:
            if self._connection is None:
                self._database_path.parent.mkdir(parents=True, exist_ok=True)
                connection: sqlite3.Connection | None = None
                try:
                    connection = sqlite3.connect(
                        self._database_path,
                        timeout=self._busy_timeout_ms / 1000,
                        isolation_level=None,
                        check_same_thread=False,
                    )
                    connection.row_factory = sqlite3.Row
                    connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
                    connection.execute("PRAGMA foreign_keys = ON")
                    connection.execute("PRAGMA journal_mode = WAL")
                    self._connection = connection
                except sqlite3.Error as exc:
                    if connection is not None:
                        connection.close()
                    raise self._repository_error(exc) from exc
            try:
                self._migrate(self._require_connection())
            except sqlite3.Error as exc:
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
                raise self._repository_error(exc) from exc
            except BaseException:
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
                raise

    async def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _migrate(self, connection: sqlite3.Connection) -> None:
        with _transaction(connection):
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            current_version = int(row["version"])
            if current_version > SCHEMA_VERSION:
                raise CloudError(
                    CloudErrorCode.REPOSITORY_UNAVAILABLE,
                    "database schema is newer than this AWorld Cloud version",
                )
            if current_version < 1:
                for statement in _SCHEMA_V1:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, format_utc_timestamp(utc_now())),
                )
            if current_version < 2:
                for statement in _SCHEMA_V2:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, format_utc_timestamp(utc_now())),
                )
            if current_version < 3:
                for statement in _SCHEMA_V3:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (3, format_utc_timestamp(utc_now())),
                )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise CloudError(
                CloudErrorCode.REPOSITORY_UNAVAILABLE,
                "cloud repository is not initialized",
            )
        return self._connection

    @staticmethod
    def _repository_error(exc: sqlite3.Error) -> CloudError:
        details: dict[str, Any] = {}
        error_name = getattr(exc, "sqlite_errorname", None)
        if error_name:
            details["database_error"] = error_name
        return CloudError(
            CloudErrorCode.REPOSITORY_UNAVAILABLE,
            "cloud metadata repository is temporarily unavailable",
            details=details,
        )

    @staticmethod
    def _integrity_error(exc: sqlite3.IntegrityError) -> CloudError:
        if "runs.workspace_id" in str(exc):
            return WorkspaceBusyError("unknown")
        return CloudError(
            CloudErrorCode.INVALID_REQUEST,
            "cloud metadata violates a persistence constraint",
        )

    @staticmethod
    def _validate_idempotency(key: str, fingerprint: str) -> None:
        if not key.strip() or not fingerprint.strip():
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "idempotency key and request fingerprint are required",
            )

    def _lookup_idempotency(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        key: str,
        fingerprint: str,
        resource_type: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT request_fingerprint, resource_type, resource_id
            FROM idempotency_keys
            WHERE scope = ? AND idempotency_key = ?
            """,
            (scope, key),
        ).fetchone()
        if row is None:
            return None
        if (
            row["request_fingerprint"] != fingerprint
            or row["resource_type"] != resource_type
        ):
            raise CloudError(
                CloudErrorCode.IDEMPOTENCY_CONFLICT,
                "idempotency key was already used for a different request",
            )
        return str(row["resource_id"])

    @staticmethod
    def _store_idempotency(
        connection: sqlite3.Connection,
        *,
        scope: str,
        key: str,
        fingerprint: str,
        resource_type: str,
        resource_id: str,
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_keys(
                scope, idempotency_key, request_fingerprint,
                resource_type, resource_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                scope,
                key,
                fingerprint,
                resource_type,
                resource_id,
                format_utc_timestamp(created_at),
            ),
        )

    @staticmethod
    def _workspace_values(workspace: Workspace) -> tuple[Any, ...]:
        return (
            str(workspace.id),
            workspace.name,
            workspace.profile_name,
            workspace.state.value,
            workspace.revision,
            workspace.runtime_image,
            str(workspace.writable_repo_path),
            str(workspace.codex_home_path),
            str(workspace.workdir),
            format_utc_timestamp(workspace.created_at),
            format_utc_timestamp(workspace.updated_at),
            _optional_timestamp(workspace.released_at),
        )

    @staticmethod
    def _run_values(run: Run) -> tuple[Any, ...]:
        benchmark_json = None
        if run.benchmark is not None:
            benchmark_json = json.dumps(
                {
                    "dataset": run.benchmark.dataset,
                    "task_id": run.benchmark.task_id,
                    "harness": run.benchmark.harness,
                    "verifier": run.benchmark.verifier,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        benchmark_result_json = None
        if run.benchmark_outcome is not None:
            benchmark_result_json = json.dumps(
                _json_compatible(run.benchmark_outcome.result),
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        return (
            str(run.id),
            str(run.workspace_id),
            str(run.batch_id) if run.batch_id is not None else None,
            run.state.value,
            run.revision,
            run.attempt,
            str(run.retry_of_run_id) if run.retry_of_run_id is not None else None,
            run.task,
            run.model,
            run.request_schema_version,
            run.mode.value,
            benchmark_json,
            (
                run.benchmark_outcome.reward
                if run.benchmark_outcome is not None
                else None
            ),
            benchmark_result_json,
            run.worker_id,
            _optional_timestamp(run.lease_expires_at),
            str(run.executor_id) if run.executor_id is not None else None,
            format_utc_timestamp(run.created_at),
            _optional_timestamp(run.started_at),
            _optional_timestamp(run.finished_at),
            run.exit_code,
            run.error_code,
            run.error_message,
        )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> Run:
        retry_id = row["retry_of_run_id"]
        executor_id = row["executor_id"]
        benchmark_payload = (
            json.loads(row["benchmark_json"])
            if row["benchmark_json"] is not None
            else None
        )
        benchmark_result = (
            json.loads(row["benchmark_result_json"])
            if row["benchmark_result_json"] is not None
            else None
        )
        return Run(
            id=RunId(row["id"]),
            workspace_id=WorkspaceId(row["workspace_id"]),
            batch_id=(
                BatchId(row["batch_id"]) if row["batch_id"] is not None else None
            ),
            state=RunState(row["state"]),
            revision=row["revision"],
            attempt=row["attempt"],
            retry_of_run_id=RunId(retry_id) if retry_id is not None else None,
            task=row["task"],
            model=row["model"],
            request_schema_version=row["request_schema_version"],
            mode=RunMode(row["mode"]),
            benchmark=(
                BenchmarkMetadata(**benchmark_payload)
                if benchmark_payload is not None
                else None
            ),
            benchmark_outcome=(
                BenchmarkOutcome(
                    reward=row["benchmark_reward"],
                    result=benchmark_result or {},
                )
                if row["benchmark_reward"] is not None or benchmark_result is not None
                else None
            ),
            worker_id=row["worker_id"],
            lease_expires_at=_parse_optional_timestamp(row["lease_expires_at"]),
            executor_id=ExecutorId(executor_id) if executor_id is not None else None,
            created_at=parse_utc_timestamp(row["created_at"]),
            started_at=_parse_optional_timestamp(row["started_at"]),
            finished_at=_parse_optional_timestamp(row["finished_at"]),
            exit_code=row["exit_code"],
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    def _row_to_workspace(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> Workspace:
        mount_rows = connection.execute(
            """
            SELECT host_path, container_path, access_mode
            FROM workspace_mounts
            WHERE workspace_id = ?
            ORDER BY ordinal
            """,
            (row["id"],),
        ).fetchall()
        mounts = tuple(
            WorkspaceMount(
                host_path=Path(mount["host_path"]),
                container_path=PurePosixPath(mount["container_path"]),
                access_mode=MountAccessMode(mount["access_mode"]),
            )
            for mount in mount_rows
        )
        return Workspace(
            id=WorkspaceId(row["id"]),
            name=row["name"],
            profile_name=row["profile_name"],
            state=WorkspaceState(row["state"]),
            revision=row["revision"],
            runtime_image=row["runtime_image"],
            writable_repo_path=Path(row["writable_repo_path"]),
            codex_home_path=Path(row["codex_home_path"]),
            workdir=PurePosixPath(row["workdir"]),
            created_at=parse_utc_timestamp(row["created_at"]),
            updated_at=parse_utc_timestamp(row["updated_at"]),
            mounts=mounts,
            released_at=_parse_optional_timestamp(row["released_at"]),
        )

    def _get_workspace(
        self,
        connection: sqlite3.Connection,
        workspace_id: WorkspaceId | str,
    ) -> Workspace | None:
        row = connection.execute(
            "SELECT * FROM workspaces WHERE id = ?",
            (str(workspace_id),),
        ).fetchone()
        return None if row is None else self._row_to_workspace(connection, row)

    def _get_run(
        self,
        connection: sqlite3.Connection,
        run_id: RunId | str,
    ) -> Run | None:
        row = connection.execute(
            "SELECT * FROM runs WHERE id = ?",
            (str(run_id),),
        ).fetchone()
        return None if row is None else self._row_to_run(row)

    def _row_to_batch(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> Batch:
        run_rows = connection.execute(
            "SELECT * FROM runs WHERE batch_id = ? ORDER BY created_at, id",
            (row["id"],),
        ).fetchall()
        return aggregate_batch(
            batch_id=BatchId(row["id"]),
            workspace_id=WorkspaceId(row["workspace_id"]),
            name=row["name"],
            created_at=parse_utc_timestamp(row["created_at"]),
            runs=(self._row_to_run(run_row) for run_row in run_rows),
        )

    def _get_batch(
        self,
        connection: sqlite3.Connection,
        batch_id: BatchId | str,
    ) -> Batch | None:
        row = connection.execute(
            "SELECT * FROM batches WHERE id = ?",
            (str(batch_id),),
        ).fetchone()
        return None if row is None else self._row_to_batch(connection, row)

    async def create_workspace(
        self,
        workspace: Workspace,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Workspace:
        self._validate_idempotency(idempotency_key, request_fingerprint)
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    existing_id = self._lookup_idempotency(
                        connection,
                        scope="workspace:create",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="workspace",
                    )
                    if existing_id is not None:
                        existing = self._get_workspace(connection, existing_id)
                        if existing is None:
                            raise CloudError(
                                CloudErrorCode.REPOSITORY_UNAVAILABLE,
                                "idempotent workspace record is missing",
                            )
                        return existing
                    connection.execute(
                        """
                        INSERT INTO workspaces(
                            id, name, profile_name, state, revision, runtime_image,
                            writable_repo_path, codex_home_path, workdir,
                            created_at, updated_at, released_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        self._workspace_values(workspace),
                    )
                    for ordinal, mount in enumerate(workspace.mounts):
                        connection.execute(
                            """
                            INSERT INTO workspace_mounts(
                                workspace_id, ordinal, container_path,
                                host_path, access_mode
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                str(workspace.id),
                                ordinal,
                                str(mount.container_path),
                                str(mount.host_path),
                                mount.access_mode.value,
                            ),
                        )
                    self._store_idempotency(
                        connection,
                        scope="workspace:create",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="workspace",
                        resource_id=str(workspace.id),
                        created_at=workspace.created_at,
                    )
                    return workspace
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def get_workspace(self, workspace_id: WorkspaceId) -> Workspace | None:
        with self._lock:
            try:
                return self._get_workspace(self._require_connection(), workspace_id)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def list_workspaces(
        self,
        *,
        limit: int,
        page_token: str | None = None,
    ) -> Page[Workspace]:
        _validate_page_size(limit)
        cursor = _decode_cursor(
            page_token,
            kind="workspaces",
            filters={},
        )
        with self._lock:
            connection = self._require_connection()
            parameters: list[Any] = []
            where = ""
            if cursor is not None:
                where = "WHERE (created_at > ? OR (created_at = ? AND id > ?))"
                parameters.extend((cursor[0], cursor[0], cursor[1]))
            parameters.append(limit + 1)
            try:
                rows = connection.execute(
                    f"""
                    SELECT * FROM workspaces
                    {where}
                    ORDER BY created_at, id
                    LIMIT ?
                    """,
                    parameters,
                ).fetchall()
                visible = rows[:limit]
                items = tuple(
                    self._row_to_workspace(connection, row) for row in visible
                )
                next_token = None
                if len(rows) > limit:
                    last = visible[-1]
                    next_token = _encode_cursor(
                        "workspaces",
                        (last["created_at"], last["id"]),
                        {},
                    )
                return Page(items=items, next_page_token=next_token)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def update_workspace(
        self,
        workspace: Workspace,
        *,
        expected_revision: int,
        expected_state: WorkspaceState,
    ) -> Workspace:
        if workspace.revision != expected_revision + 1:
            raise CloudError(
                CloudErrorCode.REVISION_CONFLICT,
                "workspace revision is not the expected successor",
            )
        if expected_state in TERMINAL_WORKSPACE_STATES:
            raise InvalidTransitionError(
                "workspace", expected_state.value, workspace.state.value
            )
        if workspace.state is not expected_state and not can_transition_workspace(
            expected_state, workspace.state
        ):
            raise InvalidTransitionError(
                "workspace", expected_state.value, workspace.state.value
            )
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    cursor = connection.execute(
                        """
                        UPDATE workspaces SET
                            name = ?, profile_name = ?, state = ?, revision = ?,
                            runtime_image = ?, writable_repo_path = ?,
                            codex_home_path = ?, workdir = ?, created_at = ?,
                            updated_at = ?, released_at = ?
                        WHERE id = ? AND revision = ? AND state = ?
                        """,
                        (
                            workspace.name,
                            workspace.profile_name,
                            workspace.state.value,
                            workspace.revision,
                            workspace.runtime_image,
                            str(workspace.writable_repo_path),
                            str(workspace.codex_home_path),
                            str(workspace.workdir),
                            format_utc_timestamp(workspace.created_at),
                            format_utc_timestamp(workspace.updated_at),
                            _optional_timestamp(workspace.released_at),
                            str(workspace.id),
                            expected_revision,
                            expected_state.value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        self._raise_workspace_cas_failure(
                            connection,
                            workspace.id,
                            expected_revision,
                            expected_state,
                        )
                    stored = self._get_workspace(connection, workspace.id)
                    assert stored is not None
                    return stored
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def begin_workspace_release(
        self,
        workspace: Workspace,
        *,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Workspace:
        self._validate_idempotency(idempotency_key, request_fingerprint)
        if (
            workspace.state is not WorkspaceState.RELEASING
            or workspace.revision != expected_revision + 1
        ):
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "workspace release must enter releasing at the next revision",
            )
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    existing_id = self._lookup_idempotency(
                        connection,
                        scope="workspace:release",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="workspace",
                    )
                    if existing_id is not None:
                        if existing_id != str(workspace.id):
                            raise CloudError(
                                CloudErrorCode.IDEMPOTENCY_CONFLICT,
                                "idempotency key belongs to another workspace",
                            )
                        existing = self._get_workspace(connection, existing_id)
                        if existing is None:
                            raise CloudError(
                                CloudErrorCode.REPOSITORY_UNAVAILABLE,
                                "idempotent workspace record is missing",
                            )
                        return existing
                    cursor = connection.execute(
                        """
                        UPDATE workspaces SET
                            name = ?, profile_name = ?, state = ?, revision = ?,
                            runtime_image = ?, writable_repo_path = ?,
                            codex_home_path = ?, workdir = ?, created_at = ?,
                            updated_at = ?, released_at = ?
                        WHERE id = ? AND revision = ? AND state = ?
                        """,
                        (
                            workspace.name,
                            workspace.profile_name,
                            workspace.state.value,
                            workspace.revision,
                            workspace.runtime_image,
                            str(workspace.writable_repo_path),
                            str(workspace.codex_home_path),
                            str(workspace.workdir),
                            format_utc_timestamp(workspace.created_at),
                            format_utc_timestamp(workspace.updated_at),
                            _optional_timestamp(workspace.released_at),
                            str(workspace.id),
                            expected_revision,
                            WorkspaceState.READY.value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        self._raise_workspace_cas_failure(
                            connection,
                            workspace.id,
                            expected_revision,
                            WorkspaceState.READY,
                        )
                    self._store_idempotency(
                        connection,
                        scope="workspace:release",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="workspace",
                        resource_id=str(workspace.id),
                        created_at=workspace.updated_at,
                    )
                    stored = self._get_workspace(connection, workspace.id)
                    assert stored is not None
                    return stored
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    @staticmethod
    def _raise_workspace_cas_failure(
        connection: sqlite3.Connection,
        workspace_id: WorkspaceId,
        expected_revision: int,
        expected_state: WorkspaceState,
    ) -> None:
        row = connection.execute(
            "SELECT revision, state FROM workspaces WHERE id = ?",
            (str(workspace_id),),
        ).fetchone()
        if row is None:
            raise CloudError(
                CloudErrorCode.WORKSPACE_NOT_FOUND,
                "workspace does not exist",
            )
        raise CloudError(
            CloudErrorCode.REVISION_CONFLICT,
            "workspace changed before the update was applied",
            details={
                "expected_revision": expected_revision,
                "expected_state": expected_state.value,
                "actual_revision": row["revision"],
                "actual_state": row["state"],
            },
        )

    def _ensure_workspace_available(
        self,
        connection: sqlite3.Connection,
        workspace_id: WorkspaceId,
    ) -> None:
        row = connection.execute(
            "SELECT state FROM workspaces WHERE id = ?",
            (str(workspace_id),),
        ).fetchone()
        if row is None:
            raise CloudError(
                CloudErrorCode.WORKSPACE_NOT_FOUND,
                "workspace does not exist",
            )
        active = connection.execute(
            """
            SELECT 1 FROM runs
            WHERE workspace_id = ? AND state IN (?, ?, ?)
            LIMIT 1
            """,
            (str(workspace_id), *_ACTIVE_STATE_VALUES),
        ).fetchone()
        if row["state"] == WorkspaceState.BUSY.value or active is not None:
            raise WorkspaceBusyError(str(workspace_id))
        if row["state"] != WorkspaceState.READY.value:
            raise InvalidTransitionError(
                "workspace", row["state"], WorkspaceState.BUSY.value
            )

    def _insert_run(self, connection: sqlite3.Connection, run: Run) -> None:
        connection.execute(
            """
            INSERT INTO runs(
                id, workspace_id, batch_id, state, revision, attempt, retry_of_run_id,
                task, model, request_schema_version, mode, benchmark_json,
                benchmark_reward, benchmark_result_json,
                worker_id, lease_expires_at, executor_id,
                created_at, started_at, finished_at, exit_code,
                error_code, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._run_values(run),
        )

    @staticmethod
    def _insert_run_event(
        connection: sqlite3.Connection,
        run_id: RunId,
        *,
        event_type: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> None:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence "
            "FROM run_events WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO run_events(
                id, run_id, sequence, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"event-{uuid.uuid4().hex}",
                str(run_id),
                int(row["sequence"]),
                event_type,
                json.dumps(
                    _json_compatible(payload),
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                format_utc_timestamp(created_at),
            ),
        )

    async def create_batch(
        self,
        batch: Batch,
        runs: tuple[Run, ...],
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Batch:
        self._validate_idempotency(idempotency_key, request_fingerprint)
        if not runs or any(
            run.batch_id != batch.id
            or run.workspace_id != batch.workspace_id
            or run.state is not RunState.QUEUED
            or run.revision != 0
            or run.attempt != 1
            for run in runs
        ):
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "a batch requires new queued first-attempt runs",
            )
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    existing_id = self._lookup_idempotency(
                        connection,
                        scope="batch:create",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="batch",
                    )
                    if existing_id is not None:
                        existing = self._get_batch(connection, existing_id)
                        if existing is None:
                            raise CloudError(
                                CloudErrorCode.REPOSITORY_UNAVAILABLE,
                                "idempotent batch record is missing",
                            )
                        return existing
                    self._ensure_workspace_available(connection, batch.workspace_id)
                    connection.execute(
                        "INSERT INTO batches(id, workspace_id, name, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            str(batch.id),
                            str(batch.workspace_id),
                            batch.name,
                            format_utc_timestamp(batch.created_at),
                        ),
                    )
                    for run in runs:
                        self._insert_run(connection, run)
                        self._insert_run_event(
                            connection,
                            run.id,
                            event_type="run.queued",
                            payload={
                                "batch_id": str(batch.id),
                                "mode": run.mode.value,
                                "state": run.state.value,
                            },
                            created_at=run.created_at,
                        )
                    self._store_idempotency(
                        connection,
                        scope="batch:create",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="batch",
                        resource_id=str(batch.id),
                        created_at=batch.created_at,
                    )
                    stored = self._get_batch(connection, batch.id)
                    assert stored is not None
                    return stored
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def get_batch(self, batch_id: BatchId) -> Batch | None:
        with self._lock:
            try:
                return self._get_batch(self._require_connection(), batch_id)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def list_batches(
        self,
        *,
        limit: int,
        page_token: str | None = None,
        workspace_id: WorkspaceId | None = None,
    ) -> Page[Batch]:
        _validate_page_size(limit)
        filters = {
            "workspace_id": str(workspace_id) if workspace_id is not None else None
        }
        cursor = _decode_cursor(page_token, kind="batches", filters=filters)
        clauses: list[str] = []
        parameters: list[Any] = []
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            parameters.append(str(workspace_id))
        if cursor is not None:
            clauses.append("(created_at > ? OR (created_at = ? AND id > ?))")
            parameters.extend((cursor[0], cursor[0], cursor[1]))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit + 1)
        with self._lock:
            connection = self._require_connection()
            try:
                rows = connection.execute(
                    f"SELECT * FROM batches {where} ORDER BY created_at, id LIMIT ?",
                    parameters,
                ).fetchall()
                visible = rows[:limit]
                items = tuple(self._row_to_batch(connection, row) for row in visible)
                next_token = None
                if len(rows) > limit:
                    last = visible[-1]
                    next_token = _encode_cursor(
                        "batches", (last["created_at"], last["id"]), filters
                    )
                return Page(items=items, next_page_token=next_token)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def list_batch_runs(
        self,
        batch_id: BatchId,
        *,
        limit: int,
        page_token: str | None = None,
    ) -> Page[Run]:
        if await self.get_batch(batch_id) is None:
            raise CloudError(CloudErrorCode.BATCH_NOT_FOUND, "batch does not exist")
        return await self.list_runs(
            limit=limit,
            page_token=page_token,
            batch_id=batch_id,
        )

    async def cancel_batch(
        self,
        batch_id: BatchId,
        *,
        requested_at: datetime,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Batch:
        self._validate_idempotency(idempotency_key, request_fingerprint)
        requested = format_utc_timestamp(requested_at)
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    existing_id = self._lookup_idempotency(
                        connection,
                        scope="batch:cancel",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="batch",
                    )
                    if existing_id is not None:
                        if existing_id != str(batch_id):
                            raise CloudError(
                                CloudErrorCode.IDEMPOTENCY_CONFLICT,
                                "idempotency key belongs to another batch",
                            )
                        existing = self._get_batch(connection, batch_id)
                        if existing is None:
                            raise CloudError(
                                CloudErrorCode.REPOSITORY_UNAVAILABLE,
                                "idempotent batch record is missing",
                            )
                        return existing
                    if self._get_batch(connection, batch_id) is None:
                        raise CloudError(
                            CloudErrorCode.BATCH_NOT_FOUND, "batch does not exist"
                        )
                    rows = connection.execute(
                        "SELECT * FROM runs WHERE batch_id = ? ORDER BY created_at, id",
                        (str(batch_id),),
                    ).fetchall()
                    for row in rows:
                        run = self._row_to_run(row)
                        target: RunState | None = None
                        if run.state is RunState.QUEUED:
                            target = RunState.CANCELLED
                            connection.execute(
                                "UPDATE runs SET state = ?, revision = revision + 1, "
                                "finished_at = ? WHERE id = ? AND revision = ?",
                                (target.value, requested, str(run.id), run.revision),
                            )
                        elif run.state in {RunState.STARTING, RunState.RUNNING}:
                            target = RunState.CANCELLING
                            connection.execute(
                                "UPDATE runs SET state = ?, revision = revision + 1 "
                                "WHERE id = ? AND revision = ?",
                                (target.value, str(run.id), run.revision),
                            )
                        if target is not None:
                            self._insert_run_event(
                                connection,
                                run.id,
                                event_type=f"run.{target.value}",
                                payload={
                                    "batch_id": str(batch_id),
                                    "state": target.value,
                                },
                                created_at=requested_at,
                            )
                    self._store_idempotency(
                        connection,
                        scope="batch:cancel",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="batch",
                        resource_id=str(batch_id),
                        created_at=requested_at,
                    )
                    stored = self._get_batch(connection, batch_id)
                    assert stored is not None
                    return stored
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def create_run(
        self,
        run: Run,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Run:
        self._validate_idempotency(idempotency_key, request_fingerprint)
        if run.state is not RunState.QUEUED or run.revision != 0 or run.attempt != 1:
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "a submitted run must be a new first attempt in queued state",
            )
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    existing_id = self._lookup_idempotency(
                        connection,
                        scope="run:create",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="run",
                    )
                    if existing_id is not None:
                        existing = self._get_run(connection, existing_id)
                        if existing is None:
                            raise CloudError(
                                CloudErrorCode.REPOSITORY_UNAVAILABLE,
                                "idempotent run record is missing",
                            )
                        return existing
                    self._ensure_workspace_available(connection, run.workspace_id)
                    self._insert_run(connection, run)
                    self._store_idempotency(
                        connection,
                        scope="run:create",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="run",
                        resource_id=str(run.id),
                        created_at=run.created_at,
                    )
                    return run
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def get_run(self, run_id: RunId) -> Run | None:
        with self._lock:
            try:
                return self._get_run(self._require_connection(), run_id)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def list_runs(
        self,
        *,
        limit: int,
        page_token: str | None = None,
        workspace_id: WorkspaceId | None = None,
        state: RunState | None = None,
        batch_id: BatchId | None = None,
    ) -> Page[Run]:
        _validate_page_size(limit)
        filters = {
            "state": state.value if state is not None else None,
            "workspace_id": str(workspace_id) if workspace_id is not None else None,
            "batch_id": str(batch_id) if batch_id is not None else None,
        }
        cursor = _decode_cursor(
            page_token,
            kind="runs",
            filters=filters,
        )
        clauses: list[str] = []
        parameters: list[Any] = []
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            parameters.append(str(workspace_id))
        if state is not None:
            clauses.append("state = ?")
            parameters.append(state.value)
        if batch_id is not None:
            clauses.append("batch_id = ?")
            parameters.append(str(batch_id))
        if cursor is not None:
            clauses.append("(created_at > ? OR (created_at = ? AND id > ?))")
            parameters.extend((cursor[0], cursor[0], cursor[1]))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit + 1)
        with self._lock:
            try:
                rows = (
                    self._require_connection()
                    .execute(
                        f"""
                    SELECT * FROM runs
                    {where}
                    ORDER BY created_at, id
                    LIMIT ?
                    """,
                        parameters,
                    )
                    .fetchall()
                )
                visible = rows[:limit]
                items = tuple(self._row_to_run(row) for row in visible)
                next_token = None
                if len(rows) > limit:
                    last = visible[-1]
                    next_token = _encode_cursor(
                        "runs",
                        (last["created_at"], last["id"]),
                        filters,
                    )
                return Page(items=items, next_page_token=next_token)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def claim_run(
        self,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> Run | None:
        if not worker_id.strip():
            raise CloudError(CloudErrorCode.INVALID_REQUEST, "worker_id is required")
        lease = format_utc_timestamp(lease_expires_at)
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    row = connection.execute(
                        """
                        SELECT queued.id, queued.revision
                        FROM runs AS queued
                        WHERE queued.state = ?
                          AND NOT EXISTS (
                              SELECT 1 FROM runs AS active
                              WHERE active.workspace_id = queued.workspace_id
                                AND active.state IN (?, ?, ?)
                          )
                        ORDER BY queued.created_at, queued.id
                        LIMIT 1
                        """,
                        (RunState.QUEUED.value, *_ACTIVE_STATE_VALUES),
                    ).fetchone()
                    if row is None:
                        return None
                    cursor = connection.execute(
                        """
                        UPDATE runs SET
                            state = ?, revision = revision + 1,
                            worker_id = ?, lease_expires_at = ?
                        WHERE id = ? AND revision = ? AND state = ?
                        """,
                        (
                            RunState.STARTING.value,
                            worker_id,
                            lease,
                            row["id"],
                            row["revision"],
                            RunState.QUEUED.value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        return None
                    claimed = self._get_run(connection, row["id"])
                    assert claimed is not None
                    return claimed
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def update_run(
        self,
        run: Run,
        *,
        expected_revision: int,
        expected_state: RunState,
    ) -> Run:
        if run.revision != expected_revision + 1:
            raise CloudError(
                CloudErrorCode.REVISION_CONFLICT,
                "run revision is not the expected successor",
            )
        if expected_state in TERMINAL_RUN_STATES:
            raise InvalidTransitionError("run", expected_state.value, run.state.value)
        if run.state is not expected_state and not can_transition_run(
            expected_state, run.state
        ):
            raise InvalidTransitionError("run", expected_state.value, run.state.value)
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    values = self._run_values(run)
                    cursor = connection.execute(
                        """
                        UPDATE runs SET
                            workspace_id = ?, batch_id = ?, state = ?, revision = ?, attempt = ?,
                            retry_of_run_id = ?, task = ?, model = ?,
                            request_schema_version = ?, mode = ?, benchmark_json = ?,
                            benchmark_reward = ?, benchmark_result_json = ?,
                            worker_id = ?, lease_expires_at = ?, executor_id = ?,
                            created_at = ?, started_at = ?, finished_at = ?,
                            exit_code = ?, error_code = ?, error_message = ?
                        WHERE id = ? AND revision = ? AND state = ?
                        """,
                        (
                            *values[1:],
                            str(run.id),
                            expected_revision,
                            expected_state.value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        self._raise_run_cas_failure(
                            connection,
                            run.id,
                            expected_revision,
                            expected_state,
                        )
                    stored = self._get_run(connection, run.id)
                    assert stored is not None
                    return stored
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    @staticmethod
    def _raise_run_cas_failure(
        connection: sqlite3.Connection,
        run_id: RunId,
        expected_revision: int,
        expected_state: RunState,
    ) -> None:
        row = connection.execute(
            "SELECT revision, state FROM runs WHERE id = ?",
            (str(run_id),),
        ).fetchone()
        if row is None:
            raise CloudError(CloudErrorCode.RUN_NOT_FOUND, "run does not exist")
        raise CloudError(
            CloudErrorCode.REVISION_CONFLICT,
            "run changed before the update was applied",
            details={
                "expected_revision": expected_revision,
                "expected_state": expected_state.value,
                "actual_revision": row["revision"],
                "actual_state": row["state"],
            },
        )

    async def heartbeat_run(
        self,
        run_id: RunId,
        *,
        worker_id: str,
        expected_revision: int,
        lease_expires_at: datetime,
    ) -> Run:
        lease = format_utc_timestamp(lease_expires_at)
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    cursor = connection.execute(
                        """
                        UPDATE runs SET
                            revision = revision + 1,
                            lease_expires_at = ?
                        WHERE id = ? AND worker_id = ? AND revision = ?
                          AND state IN (?, ?, ?)
                        """,
                        (
                            lease,
                            str(run_id),
                            worker_id,
                            expected_revision,
                            *_ACTIVE_STATE_VALUES,
                        ),
                    )
                    if cursor.rowcount != 1:
                        row = connection.execute(
                            "SELECT state FROM runs WHERE id = ?", (str(run_id),)
                        ).fetchone()
                        if row is None:
                            raise CloudError(
                                CloudErrorCode.RUN_NOT_FOUND,
                                "run does not exist",
                            )
                        raise CloudError(
                            CloudErrorCode.REVISION_CONFLICT,
                            "run heartbeat ownership or revision changed",
                        )
                    stored = self._get_run(connection, run_id)
                    assert stored is not None
                    return stored
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def request_run_cancellation(
        self,
        run_id: RunId,
        *,
        requested_at: datetime,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Run:
        self._validate_idempotency(idempotency_key, request_fingerprint)
        requested = format_utc_timestamp(requested_at)
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    existing_id = self._lookup_idempotency(
                        connection,
                        scope="run:cancel",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="run",
                    )
                    if existing_id is not None:
                        if existing_id != str(run_id):
                            raise CloudError(
                                CloudErrorCode.IDEMPOTENCY_CONFLICT,
                                "idempotency key belongs to another run",
                            )
                        existing = self._get_run(connection, run_id)
                        if existing is None:
                            raise CloudError(
                                CloudErrorCode.REPOSITORY_UNAVAILABLE,
                                "idempotent run record is missing",
                            )
                        return existing
                    run = self._get_run(connection, run_id)
                    if run is None:
                        raise CloudError(
                            CloudErrorCode.RUN_NOT_FOUND,
                            "run does not exist",
                        )
                    if run.state is RunState.QUEUED:
                        connection.execute(
                            """
                            UPDATE runs SET
                                state = ?, revision = revision + 1, finished_at = ?
                            WHERE id = ? AND revision = ? AND state = ?
                            """,
                            (
                                RunState.CANCELLED.value,
                                requested,
                                str(run.id),
                                run.revision,
                                RunState.QUEUED.value,
                            ),
                        )
                    elif run.state in {RunState.STARTING, RunState.RUNNING}:
                        connection.execute(
                            """
                            UPDATE runs SET state = ?, revision = revision + 1
                            WHERE id = ? AND revision = ? AND state = ?
                            """,
                            (
                                RunState.CANCELLING.value,
                                str(run.id),
                                run.revision,
                                run.state.value,
                            ),
                        )
                    elif run.state not in {RunState.CANCELLING, RunState.CANCELLED}:
                        raise InvalidTransitionError(
                            "run", run.state.value, RunState.CANCELLING.value
                        )
                    self._store_idempotency(
                        connection,
                        scope="run:cancel",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="run",
                        resource_id=str(run.id),
                        created_at=requested_at,
                    )
                    stored = self._get_run(connection, run.id)
                    assert stored is not None
                    return stored
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def create_retry_run(
        self,
        run: Run,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Run:
        self._validate_idempotency(idempotency_key, request_fingerprint)
        if (
            run.state is not RunState.QUEUED
            or run.revision != 0
            or run.retry_of_run_id is None
        ):
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "a retry must be a new queued run with lineage",
            )
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    existing_id = self._lookup_idempotency(
                        connection,
                        scope="run:retry",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="run",
                    )
                    if existing_id is not None:
                        existing = self._get_run(connection, existing_id)
                        if existing is None:
                            raise CloudError(
                                CloudErrorCode.REPOSITORY_UNAVAILABLE,
                                "idempotent retry record is missing",
                            )
                        return existing
                    source = self._get_run(connection, run.retry_of_run_id)
                    if source is None:
                        raise CloudError(
                            CloudErrorCode.RUN_NOT_FOUND,
                            "retry source does not exist",
                        )
                    if source.state is not RunState.FAILED:
                        raise InvalidTransitionError(
                            "run retry", source.state.value, RunState.QUEUED.value
                        )
                    if (
                        run.workspace_id != source.workspace_id
                        or run.attempt != source.attempt + 1
                        or run.task != source.task
                        or run.model != source.model
                    ):
                        raise CloudError(
                            CloudErrorCode.INVALID_REQUEST,
                            "retry lineage does not match its source",
                        )
                    self._ensure_workspace_available(connection, run.workspace_id)
                    self._insert_run(connection, run)
                    self._store_idempotency(
                        connection,
                        scope="run:retry",
                        key=idempotency_key,
                        fingerprint=request_fingerprint,
                        resource_type="run",
                        resource_id=str(run.id),
                        created_at=run.created_at,
                    )
                    return run
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def list_expired_runs(
        self,
        *,
        expired_before: datetime,
        limit: int,
    ) -> tuple[Run, ...]:
        _validate_page_size(limit)
        cutoff = format_utc_timestamp(expired_before)
        with self._lock:
            try:
                rows = (
                    self._require_connection()
                    .execute(
                        """
                    SELECT * FROM runs
                    WHERE state IN (?, ?, ?)
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at <= ?
                    ORDER BY lease_expires_at, id
                    LIMIT ?
                    """,
                        (*_ACTIVE_STATE_VALUES, cutoff, limit),
                    )
                    .fetchall()
                )
                return tuple(self._row_to_run(row) for row in rows)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def append_event(
        self,
        run_id: RunId,
        *,
        event_type: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> RunEvent:
        if not event_type.strip():
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "event_type is required",
            )
        try:
            payload_json = json.dumps(
                _json_compatible(payload),
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "event payload must contain JSON-compatible values",
            ) from exc
        event_id = EventId(f"event-{uuid.uuid4().hex}")
        timestamp = format_utc_timestamp(created_at)
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    if self._get_run(connection, run_id) is None:
                        raise CloudError(
                            CloudErrorCode.RUN_NOT_FOUND,
                            "run does not exist",
                        )
                    row = connection.execute(
                        """
                        SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence
                        FROM run_events WHERE run_id = ?
                        """,
                        (str(run_id),),
                    ).fetchone()
                    sequence = int(row["sequence"])
                    connection.execute(
                        """
                        INSERT INTO run_events(
                            id, run_id, sequence, event_type,
                            payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(event_id),
                            str(run_id),
                            sequence,
                            event_type,
                            payload_json,
                            timestamp,
                        ),
                    )
                    return RunEvent(
                        id=event_id,
                        run_id=run_id,
                        sequence=sequence,
                        event_type=event_type,
                        payload=json.loads(payload_json),
                        created_at=parse_utc_timestamp(timestamp),
                    )
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def list_events(
        self,
        run_id: RunId,
        *,
        after_sequence: int = 0,
        limit: int,
    ) -> Page[RunEvent]:
        _validate_page_size(limit)
        if after_sequence < 0:
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "after_sequence must be non-negative",
            )
        with self._lock:
            connection = self._require_connection()
            try:
                if self._get_run(connection, run_id) is None:
                    raise CloudError(
                        CloudErrorCode.RUN_NOT_FOUND,
                        "run does not exist",
                    )
                rows = connection.execute(
                    """
                    SELECT * FROM run_events
                    WHERE run_id = ? AND sequence > ?
                    ORDER BY sequence
                    LIMIT ?
                    """,
                    (str(run_id), after_sequence, limit + 1),
                ).fetchall()
                visible = rows[:limit]
                events = tuple(
                    RunEvent(
                        id=EventId(row["id"]),
                        run_id=RunId(row["run_id"]),
                        sequence=row["sequence"],
                        event_type=row["event_type"],
                        payload=json.loads(row["payload_json"]),
                        created_at=parse_utc_timestamp(row["created_at"]),
                    )
                    for row in visible
                )
                next_token = None
                if len(rows) > limit:
                    last = visible[-1]
                    next_token = _encode_cursor(
                        "events",
                        (str(run_id), str(last["sequence"])),
                        {},
                    )
                return Page(items=events, next_page_token=next_token)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def register_run_file(self, run_file: RunFile) -> RunFile:
        with self._lock:
            connection = self._require_connection()
            try:
                with _transaction(connection):
                    existing = self._get_run_file(connection, run_file.id)
                    if existing is not None:
                        if existing == run_file:
                            return existing
                        raise CloudError(
                            CloudErrorCode.INVALID_REQUEST,
                            "run file identity is already registered differently",
                        )
                    if self._get_run(connection, run_file.run_id) is None:
                        raise CloudError(
                            CloudErrorCode.RUN_NOT_FOUND,
                            "run does not exist",
                        )
                    connection.execute(
                        """
                        INSERT INTO run_files(
                            id, run_id, kind, relative_path,
                            size_bytes, sha256, created_at, trajectory_format,
                            trajectory_schema_version, trajectory_role
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(run_file.id),
                            str(run_file.run_id),
                            run_file.kind.value,
                            str(run_file.relative_path),
                            run_file.size_bytes,
                            run_file.sha256,
                            format_utc_timestamp(run_file.created_at),
                            (
                                run_file.trajectory.format.value
                                if run_file.trajectory is not None
                                else None
                            ),
                            (
                                run_file.trajectory.schema_version
                                if run_file.trajectory is not None
                                else None
                            ),
                            (
                                run_file.trajectory.role.value
                                if run_file.trajectory is not None
                                else None
                            ),
                        ),
                    )
                    return run_file
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from exc
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    @staticmethod
    def _row_to_run_file(row: sqlite3.Row) -> RunFile:
        trajectory = None
        if row["trajectory_format"] is not None:
            trajectory = TrajectoryManifest(
                format=TrajectoryFormat(row["trajectory_format"]),
                schema_version=row["trajectory_schema_version"],
                role=TrajectoryRole(row["trajectory_role"]),
            )
        return RunFile(
            id=FileId(row["id"]),
            run_id=RunId(row["run_id"]),
            kind=RunFileKind(row["kind"]),
            relative_path=PurePosixPath(row["relative_path"]),
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            created_at=parse_utc_timestamp(row["created_at"]),
            trajectory=trajectory,
        )

    def _get_run_file(
        self,
        connection: sqlite3.Connection,
        file_id: FileId | str,
    ) -> RunFile | None:
        row = connection.execute(
            "SELECT * FROM run_files WHERE id = ?",
            (str(file_id),),
        ).fetchone()
        return None if row is None else self._row_to_run_file(row)

    async def get_run_file(self, file_id: FileId) -> RunFile | None:
        with self._lock:
            try:
                return self._get_run_file(self._require_connection(), file_id)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc

    async def list_run_files(self, run_id: RunId) -> tuple[RunFile, ...]:
        with self._lock:
            connection = self._require_connection()
            try:
                if self._get_run(connection, run_id) is None:
                    raise CloudError(
                        CloudErrorCode.RUN_NOT_FOUND,
                        "run does not exist",
                    )
                rows = connection.execute(
                    """
                    SELECT * FROM run_files
                    WHERE run_id = ?
                    ORDER BY relative_path, id
                    """,
                    (str(run_id),),
                ).fetchall()
                return tuple(self._row_to_run_file(row) for row in rows)
            except sqlite3.Error as exc:
                raise self._repository_error(exc) from exc
