"""Immutable AWorld Cloud records, identifiers, timestamps, and state policy."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, NewType

from aworld.cloud.errors import InvalidTransitionError, WorkspaceBusyError

WorkspaceId = NewType("WorkspaceId", str)
RunId = NewType("RunId", str)
EventId = NewType("EventId", str)
FileId = NewType("FileId", str)
ExecutorId = NewType("ExecutorId", str)

RUN_REQUEST_SCHEMA_VERSION = "aworld.cloud.run-request.v1"
ATIF_SCHEMA_VERSION = "ATIF-v1.7"


def utc_now() -> datetime:
    """Return an aware timestamp in UTC."""

    return datetime.now(timezone.utc)


def as_utc(value: datetime, *, field_name: str = "timestamp") -> datetime:
    """Validate an aware timestamp and normalize it to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(timezone.utc)


def format_utc_timestamp(value: datetime) -> str:
    """Serialize an aware timestamp as RFC 3339 with an explicit UTC offset."""

    return as_utc(value).isoformat()


def parse_utc_timestamp(value: str) -> datetime:
    """Parse an RFC 3339 timestamp and normalize it to UTC."""

    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp must be RFC 3339") from exc
    return as_utc(parsed)


def _optional_utc(value: datetime | None, *, field_name: str) -> datetime | None:
    return None if value is None else as_utc(value, field_name=field_name)


def _require_text(value: object, field_name: str) -> None:
    if not str(value).strip():
        raise ValueError(f"{field_name} must not be empty")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


class WorkspaceState(str, Enum):
    CREATING = "creating"
    READY = "ready"
    BUSY = "busy"
    RELEASING = "releasing"
    RELEASED = "released"
    FAILED = "failed"


class RunState(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunMode(str, Enum):
    QUERY = "query"
    BENCHMARK = "benchmark"


class MountAccessMode(str, Enum):
    READ_ONLY = "ro"
    READ_WRITE = "rw"


class RunFileKind(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"
    EVENTS = "events"
    RESULT = "result"
    ARTIFACT = "artifact"
    TRAJECTORY = "trajectory"


class TrajectoryFormat(str, Enum):
    ATIF = "atif"
    PROVIDER_NATIVE = "provider_native"


class TrajectoryRole(str, Enum):
    CANONICAL = "canonical"
    PROVIDER_RAW = "provider_raw"


ACTIVE_RUN_STATES = frozenset(
    {RunState.STARTING, RunState.RUNNING, RunState.CANCELLING}
)
TERMINAL_RUN_STATES = frozenset(
    {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}
)
TERMINAL_WORKSPACE_STATES = frozenset({WorkspaceState.RELEASED, WorkspaceState.FAILED})

_WORKSPACE_TRANSITIONS: Mapping[WorkspaceState, frozenset[WorkspaceState]] = (
    MappingProxyType(
        {
            WorkspaceState.CREATING: frozenset(
                {WorkspaceState.READY, WorkspaceState.FAILED}
            ),
            WorkspaceState.READY: frozenset(
                {WorkspaceState.BUSY, WorkspaceState.RELEASING, WorkspaceState.FAILED}
            ),
            WorkspaceState.BUSY: frozenset(
                {WorkspaceState.READY, WorkspaceState.FAILED}
            ),
            WorkspaceState.RELEASING: frozenset(
                {WorkspaceState.RELEASED, WorkspaceState.FAILED}
            ),
            WorkspaceState.RELEASED: frozenset(),
            WorkspaceState.FAILED: frozenset(),
        }
    )
)

_RUN_TRANSITIONS: Mapping[RunState, frozenset[RunState]] = MappingProxyType(
    {
        RunState.QUEUED: frozenset({RunState.STARTING, RunState.CANCELLED}),
        RunState.STARTING: frozenset(
            {RunState.RUNNING, RunState.CANCELLING, RunState.FAILED}
        ),
        RunState.RUNNING: frozenset(
            {RunState.CANCELLING, RunState.SUCCEEDED, RunState.FAILED}
        ),
        RunState.CANCELLING: frozenset({RunState.CANCELLED, RunState.FAILED}),
        RunState.SUCCEEDED: frozenset(),
        RunState.FAILED: frozenset(),
        RunState.CANCELLED: frozenset(),
    }
)


@dataclass(frozen=True)
class WorkspaceMount:
    """One administrator-selected host mount in a workspace."""

    host_path: Path
    container_path: PurePosixPath
    access_mode: MountAccessMode

    def __post_init__(self) -> None:
        object.__setattr__(self, "host_path", Path(self.host_path))
        object.__setattr__(self, "container_path", PurePosixPath(self.container_path))
        if not self.container_path.is_absolute():
            raise ValueError("container_path must be absolute")


@dataclass(frozen=True)
class Workspace:
    """Durable immutable snapshot of a cloud workspace."""

    id: WorkspaceId
    name: str
    profile_name: str
    state: WorkspaceState
    revision: int
    runtime_image: str
    writable_repo_path: Path
    codex_home_path: Path
    workdir: PurePosixPath
    created_at: datetime
    updated_at: datetime
    mounts: tuple[WorkspaceMount, ...] = ()
    released_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "name", "profile_name", "runtime_image"):
            _require_text(getattr(self, field_name), field_name)
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        object.__setattr__(self, "writable_repo_path", Path(self.writable_repo_path))
        object.__setattr__(self, "codex_home_path", Path(self.codex_home_path))
        object.__setattr__(self, "workdir", PurePosixPath(self.workdir))
        object.__setattr__(self, "mounts", tuple(self.mounts))
        object.__setattr__(
            self, "created_at", as_utc(self.created_at, field_name="created_at")
        )
        object.__setattr__(
            self, "updated_at", as_utc(self.updated_at, field_name="updated_at")
        )
        object.__setattr__(
            self,
            "released_at",
            _optional_utc(self.released_at, field_name="released_at"),
        )
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.state is WorkspaceState.RELEASED and self.released_at is None:
            raise ValueError("a released workspace requires released_at")
        if self.released_at is not None and self.state is not WorkspaceState.RELEASED:
            raise ValueError("released_at is only valid for a released workspace")


@dataclass(frozen=True)
class BenchmarkMetadata:
    """Optional benchmark identity interpreted by a configured adapter."""

    dataset: str
    task_id: str
    harness: str | None = None
    verifier: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.dataset, "benchmark.dataset")
        _require_text(self.task_id, "benchmark.task_id")
        for field_name in ("harness", "verifier"):
            value = getattr(self, field_name)
            if value is not None:
                _require_text(value, f"benchmark.{field_name}")


@dataclass(frozen=True)
class BenchmarkOutcome:
    """Executor- or adapter-produced terminal benchmark output."""

    reward: float | None = None
    result: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.reward is not None:
            if isinstance(self.reward, bool) or not isinstance(
                self.reward, (int, float)
            ):
                raise ValueError("benchmark reward must be numeric")
            if not math.isfinite(self.reward):
                raise ValueError("benchmark reward must be finite")
            object.__setattr__(self, "reward", float(self.reward))
        if not isinstance(self.result, Mapping):
            raise TypeError("benchmark result must be a JSON object")
        try:
            json.dumps(self.result, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("benchmark result must be JSON-compatible") from exc
        object.__setattr__(self, "result", _freeze_json(self.result))


@dataclass(frozen=True)
class Run:
    """Durable immutable snapshot of one Codex execution attempt."""

    id: RunId
    workspace_id: WorkspaceId
    state: RunState
    revision: int
    attempt: int
    task: str
    created_at: datetime
    request_schema_version: str = RUN_REQUEST_SCHEMA_VERSION
    mode: RunMode = RunMode.QUERY
    benchmark: BenchmarkMetadata | None = None
    benchmark_outcome: BenchmarkOutcome | None = None
    model: str | None = None
    retry_of_run_id: RunId | None = None
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    executor_id: ExecutorId | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.workspace_id, "workspace_id")
        _require_text(self.task, "task")
        if self.request_schema_version != RUN_REQUEST_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported run request schema: {self.request_schema_version}"
            )
        if self.mode is RunMode.QUERY and self.benchmark is not None:
            raise ValueError("benchmark metadata is not valid for query runs")
        if self.mode is RunMode.BENCHMARK and self.benchmark is None:
            raise ValueError("benchmark metadata is required for benchmark runs")
        if self.mode is RunMode.QUERY and self.benchmark_outcome is not None:
            raise ValueError("benchmark outcome is not valid for query runs")
        if self.benchmark_outcome is not None and self.state not in TERMINAL_RUN_STATES:
            raise ValueError("benchmark outcome is only valid for terminal runs")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if self.attempt == 1 and self.retry_of_run_id is not None:
            raise ValueError("a first attempt cannot reference a retry source")
        if self.attempt > 1 and self.retry_of_run_id is None:
            raise ValueError("a retry attempt requires retry_of_run_id")
        object.__setattr__(
            self, "created_at", as_utc(self.created_at, field_name="created_at")
        )
        for field_name in ("lease_expires_at", "started_at", "finished_at"):
            object.__setattr__(
                self,
                field_name,
                _optional_utc(getattr(self, field_name), field_name=field_name),
            )
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must not precede created_at")
        if self.finished_at is not None:
            start = self.started_at or self.created_at
            if self.finished_at < start:
                raise ValueError("finished_at must not precede the run start")
        if self.state in TERMINAL_RUN_STATES and self.finished_at is None:
            raise ValueError("a terminal run requires finished_at")
        if self.state not in TERMINAL_RUN_STATES and self.finished_at is not None:
            raise ValueError("finished_at is only valid for a terminal run")


@dataclass(frozen=True)
class RunEvent:
    """A durable, monotonically sequenced event for one run."""

    id: EventId
    run_id: RunId
    sequence: int
    event_type: str
    payload: Mapping[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.run_id, "run_id")
        _require_text(self.event_type, "event_type")
        if self.sequence < 1:
            raise ValueError("sequence must be positive")
        object.__setattr__(self, "payload", _freeze_json(self.payload))
        object.__setattr__(
            self, "created_at", as_utc(self.created_at, field_name="created_at")
        )


@dataclass(frozen=True)
class TrajectoryManifest:
    """Typed semantics for an executor-produced trajectory file."""

    format: TrajectoryFormat
    schema_version: str
    role: TrajectoryRole

    def __post_init__(self) -> None:
        _require_text(self.schema_version, "trajectory.schema_version")
        if (
            self.role is TrajectoryRole.CANONICAL
            and self.format is not TrajectoryFormat.ATIF
        ):
            raise ValueError("the canonical trajectory must use ATIF")


@dataclass(frozen=True)
class RunFile:
    """Manifest metadata for a retrievable run-owned file."""

    id: FileId
    run_id: RunId
    kind: RunFileKind
    relative_path: PurePosixPath
    size_bytes: int
    sha256: str
    created_at: datetime
    trajectory: TrajectoryManifest | None = None

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.run_id, "run_id")
        _require_text(self.sha256, "sha256")
        object.__setattr__(self, "relative_path", PurePosixPath(self.relative_path))
        if self.relative_path.is_absolute() or ".." in self.relative_path.parts:
            raise ValueError("relative_path must be a contained relative path")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if self.kind is RunFileKind.TRAJECTORY and self.trajectory is None:
            raise ValueError("trajectory files require trajectory manifest metadata")
        if self.kind is not RunFileKind.TRAJECTORY and self.trajectory is not None:
            raise ValueError("trajectory metadata is only valid for trajectory files")
        object.__setattr__(
            self, "created_at", as_utc(self.created_at, field_name="created_at")
        )


def allowed_workspace_transitions(state: WorkspaceState) -> frozenset[WorkspaceState]:
    return _WORKSPACE_TRANSITIONS[state]


def allowed_run_transitions(state: RunState) -> frozenset[RunState]:
    return _RUN_TRANSITIONS[state]


def can_transition_workspace(current: WorkspaceState, target: WorkspaceState) -> bool:
    return target in allowed_workspace_transitions(current)


def can_transition_run(current: RunState, target: RunState) -> bool:
    return target in allowed_run_transitions(current)


def transition_workspace(
    workspace: Workspace,
    target: WorkspaceState,
    *,
    at: datetime | None = None,
) -> Workspace:
    """Return the next revision after enforcing the workspace state machine."""

    if not can_transition_workspace(workspace.state, target):
        raise InvalidTransitionError("workspace", workspace.state.value, target.value)
    changed_at = as_utc(at or utc_now())
    return replace(
        workspace,
        state=target,
        revision=workspace.revision + 1,
        updated_at=changed_at,
        released_at=changed_at if target is WorkspaceState.RELEASED else None,
    )


def transition_run(
    run: Run,
    target: RunState,
    *,
    at: datetime | None = None,
    exit_code: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    benchmark_outcome: BenchmarkOutcome | None = None,
) -> Run:
    """Return the next revision after enforcing the run state machine."""

    if not can_transition_run(run.state, target):
        raise InvalidTransitionError("run", run.state.value, target.value)
    changed_at = as_utc(at or utc_now())
    started_at = run.started_at
    if target is RunState.RUNNING and started_at is None:
        started_at = changed_at
    return replace(
        run,
        state=target,
        revision=run.revision + 1,
        started_at=started_at,
        finished_at=changed_at if target in TERMINAL_RUN_STATES else None,
        exit_code=exit_code if target in TERMINAL_RUN_STATES else run.exit_code,
        error_code=error_code if target in TERMINAL_RUN_STATES else run.error_code,
        error_message=error_message
        if target in TERMINAL_RUN_STATES
        else run.error_message,
        benchmark_outcome=(
            benchmark_outcome
            if target in TERMINAL_RUN_STATES
            else run.benchmark_outcome
        ),
    )


def create_retry_run(
    source: Run,
    *,
    run_id: RunId,
    created_at: datetime | None = None,
) -> Run:
    """Create a queued retry whose direct parent is an immutable failed run."""

    if source.state is not RunState.FAILED:
        raise InvalidTransitionError(
            "run retry", source.state.value, RunState.QUEUED.value
        )
    return Run(
        id=run_id,
        workspace_id=source.workspace_id,
        state=RunState.QUEUED,
        revision=0,
        attempt=source.attempt + 1,
        retry_of_run_id=source.id,
        task=source.task,
        request_schema_version=source.request_schema_version,
        mode=source.mode,
        benchmark=source.benchmark,
        model=source.model,
        created_at=as_utc(created_at or utc_now()),
    )


def active_run_for_workspace(
    workspace_id: WorkspaceId,
    runs: Iterable[Run],
) -> Run | None:
    """Return the active run and reject a pre-existing invariant violation."""

    active = [
        run
        for run in runs
        if run.workspace_id == workspace_id and run.state in ACTIVE_RUN_STATES
    ]
    if len(active) > 1:
        raise WorkspaceBusyError(str(workspace_id))
    return active[0] if active else None


def ensure_workspace_accepts_run(workspace: Workspace, runs: Iterable[Run]) -> None:
    """Enforce that a ready workspace has no executing mutation owner."""

    if workspace.state is WorkspaceState.BUSY or active_run_for_workspace(
        workspace.id, runs
    ):
        raise WorkspaceBusyError(str(workspace.id))
    if workspace.state is not WorkspaceState.READY:
        raise InvalidTransitionError(
            "workspace", workspace.state.value, WorkspaceState.BUSY.value
        )
