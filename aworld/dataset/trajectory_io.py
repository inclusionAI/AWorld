"""Versioned trajectory JSONL codec, sink, and compatibility reader.

This module owns only the persisted projection envelope.  Provider requests
remain authoritative in ``llm_calls`` and actions/results remain authoritative
in runtime events.  No answer or synthetic trajectory step is inferred here.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import threading
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from aworld.core.trajectory import (
    TrajectoryBuildResult,
    TrajectoryBuildStatus,
    TrajectoryFidelity,
    canonical_trajectory_bytes,
    compute_trajectory_checksum,
)

try:  # POSIX cross-process append lock; thread lock remains the portable fallback.
    import fcntl
except ImportError:  # pragma: no cover - exercised on non-POSIX platforms
    fcntl = None


SCHEMA_VERSION = "aworld.trajectory.v2"
RECORD_TYPE = "trajectory_snapshot"
CANONICALIZATION_VERSION = "aworld.canonical-json.v1"
DEFAULT_MAX_RECORD_BYTES = 64 * 1024 * 1024
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_V2_MARKER = '"schema_version"'
_LEGACY_MARKERS = ("'task_id'", '"task_id"')
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class TrajectoryFormat(str, Enum):
    LEGACY = "legacy"
    DUAL = "dual"
    JSONL_V2 = "jsonl_v2"


class TrajectoryIOError(ValueError):
    """Base error for malformed or inconsistent trajectory artifacts."""


class TrajectoryChecksumMismatchError(TrajectoryIOError):
    """A v2 record does not match its declared checksum."""


class TrajectoryRevisionConflictError(TrajectoryIOError):
    """Two different v2 records claim the same task revision."""


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def canonical_record_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return canonical JSON bytes for one record-shaped mapping."""

    if not isinstance(payload, Mapping):
        raise TypeError("trajectory record must be a mapping")
    # Reuse the same canonical JSON implementation as trajectory checksums,
    # removing only the temporary sequence wrapper around the record object.
    wrapped = canonical_trajectory_bytes([dict(payload)])
    return wrapped[1:-1]


def compute_record_checksum(payload: Mapping[str, Any]) -> str:
    """Compute an algorithm-qualified checksum for canonical record bytes."""

    return _sha256(canonical_record_bytes(payload))


def _json_value(value: Any) -> Any:
    """Normalize one supported value through the shared canonical encoder."""

    return json.loads(canonical_trajectory_bytes([value]).decode("utf-8"))[0]


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _validate_projection_consistency(
    build_result: TrajectoryBuildResult,
    trajectory: Sequence[Any] | None,
    *,
    label: str = "",
    require_nonempty_checksum: bool,
) -> None:
    """Reject contradictions between one build result and its delivered projection."""

    prefix = f"{label} " if label else ""
    has_inline = trajectory is not None
    inline_count = len(trajectory) if has_inline else 0
    has_ref = build_result.trajectory_ref is not None
    if has_inline and has_ref:
        raise TrajectoryIOError(
            f"{prefix}inline trajectory and trajectory_ref are mutually exclusive"
        )
    if build_result.status is TrajectoryBuildStatus.COMPLETE and not (has_inline or has_ref):
        raise TrajectoryIOError(
            f"complete {prefix}trajectory requires inline data or trajectory_ref"
        )
    if build_result.status in {
        TrajectoryBuildStatus.EMPTY,
        TrajectoryBuildStatus.FAILED,
    } and (inline_count or has_ref):
        raise TrajectoryIOError(
            f"{build_result.status.value} {prefix}trajectory cannot contain "
            "inline data or trajectory_ref"
        )
    if has_inline and build_result.persisted_items != inline_count:
        raise TrajectoryIOError(
            f"{prefix}inline trajectory length does not match build_result.persisted_items"
        )
    if not has_inline and not has_ref and build_result.persisted_items != 0:
        raise TrajectoryIOError(
            f"{prefix}trajectory without inline data or trajectory_ref cannot claim persisted items"
        )
    if inline_count and require_nonempty_checksum and build_result.trajectory_checksum is None:
        raise TrajectoryIOError(
            f"non-empty {prefix}inline trajectory requires trajectory_checksum"
        )
    if has_inline and build_result.trajectory_checksum is not None:
        actual = compute_trajectory_checksum(trajectory)
        if actual != build_result.trajectory_checksum:
            raise TrajectoryChecksumMismatchError(
                f"{prefix}inline trajectory does not match "
                "build_result.trajectory_checksum"
            )


@dataclass(frozen=True, slots=True)
class TrajectoryEnvelope:
    """One immutable ``aworld.trajectory.v2`` JSONL record."""

    build_result: TrajectoryBuildResult
    revision: int
    trajectory: Sequence[Any] | None = None
    llm_calls: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    token_id_trajectory: Any = None
    is_sub_task: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        if self.trajectory is not None and not _is_sequence(self.trajectory):
            raise TypeError("trajectory must be a sequence or None")
        if not _is_sequence(self.llm_calls):
            raise TypeError("llm_calls must be a sequence")
        if any(not isinstance(call, Mapping) for call in self.llm_calls):
            raise TypeError("llm_calls entries must be mappings")

        _validate_projection_consistency(
            self.build_result,
            self.trajectory,
            require_nonempty_checksum=True,
        )

    def _payload_without_record_checksum(self) -> dict[str, Any]:
        build = self.build_result.to_dict()
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": RECORD_TYPE,
            "task_id": self.build_result.task_id,
            "session_id": self.build_result.session_id,
            "trace_id": self.build_result.trace_id,
            "task_epoch": self.build_result.task_epoch,
            "is_sub_task": self.is_sub_task,
            "revision": self.revision,
            "created_at": build["created_at"],
            "builder_version": self.build_result.builder_version,
            "build_result": build,
            "trajectory": _json_value(list(self.trajectory)) if self.trajectory is not None else None,
            "trajectory_ref": self.build_result.trajectory_ref,
            "llm_calls": _json_value([dict(call) for call in self.llm_calls]),
            "token_id_trajectory": _json_value(self.token_id_trajectory),
            "integrity": {
                "algorithm": "sha256",
                "canonicalization": CANONICALIZATION_VERSION,
                "trajectory_checksum": self.build_result.trajectory_checksum,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload_without_record_checksum()
        payload["integrity"]["record_checksum"] = compute_record_checksum(payload)
        return payload

    def to_json_line(self) -> bytes:
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrajectoryEnvelope":
        values = dict(payload)
        if values.get("schema_version") != SCHEMA_VERSION:
            raise TrajectoryIOError(f"unsupported trajectory schema: {values.get('schema_version')}")
        if values.get("record_type") != RECORD_TYPE:
            raise TrajectoryIOError(f"unsupported trajectory record type: {values.get('record_type')}")

        integrity = values.get("integrity")
        if not isinstance(integrity, Mapping):
            raise TrajectoryIOError("v2 record is missing integrity metadata")
        if integrity.get("algorithm") != "sha256":
            raise TrajectoryIOError("v2 record uses an unsupported checksum algorithm")
        if integrity.get("canonicalization") != CANONICALIZATION_VERSION:
            raise TrajectoryIOError("v2 record uses an unsupported canonicalization version")
        declared_record_checksum = integrity.get("record_checksum")
        if not isinstance(declared_record_checksum, str):
            raise TrajectoryIOError("v2 record is missing record_checksum")
        checksum_payload = dict(values)
        checksum_integrity = dict(integrity)
        checksum_integrity.pop("record_checksum", None)
        checksum_payload["integrity"] = checksum_integrity
        actual_record_checksum = compute_record_checksum(checksum_payload)
        if actual_record_checksum != declared_record_checksum:
            raise TrajectoryChecksumMismatchError(
                f"record checksum mismatch: expected {declared_record_checksum}, got {actual_record_checksum}"
            )

        build_payload = values.get("build_result")
        if not isinstance(build_payload, Mapping):
            raise TrajectoryIOError("v2 record is missing build_result")
        build_result = TrajectoryBuildResult.from_dict(build_payload)
        identity_pairs = {
            "task_id": build_result.task_id,
            "session_id": build_result.session_id,
            "trace_id": build_result.trace_id,
            "task_epoch": build_result.task_epoch,
            "builder_version": build_result.builder_version,
            "created_at": build_result.to_dict()["created_at"],
        }
        for name, expected in identity_pairs.items():
            if values.get(name) != expected:
                raise TrajectoryIOError(f"v2 record {name} does not match build_result")
        if values.get("trajectory_ref") != build_result.trajectory_ref:
            raise TrajectoryIOError("v2 record trajectory_ref does not match build_result")
        if integrity.get("trajectory_checksum") != build_result.trajectory_checksum:
            raise TrajectoryIOError("v2 integrity checksum does not match build_result")

        trajectory = values.get("trajectory")
        if trajectory is not None and not isinstance(trajectory, list):
            raise TrajectoryIOError("v2 trajectory must be a list or null")
        llm_calls = values.get("llm_calls", [])
        if not isinstance(llm_calls, list) or any(not isinstance(call, Mapping) for call in llm_calls):
            raise TrajectoryIOError("v2 llm_calls must be a list of objects")
        if not isinstance(values.get("is_sub_task", False), bool):
            raise TrajectoryIOError("v2 is_sub_task must be a boolean")
        return cls(
            build_result=build_result,
            revision=values.get("revision"),
            trajectory=trajectory,
            llm_calls=llm_calls,
            token_id_trajectory=values.get("token_id_trajectory"),
            is_sub_task=bool(values.get("is_sub_task", False)),
        )


@dataclass(frozen=True, slots=True)
class TrajectorySinkConfig:
    format: TrajectoryFormat = TrajectoryFormat.LEGACY
    path: str | Path | None = None
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(self, "format", TrajectoryFormat(self.format))
        if isinstance(self.max_record_bytes, bool) or self.max_record_bytes <= 0:
            raise ValueError("max_record_bytes must be positive")

    @property
    def jsonl_path(self) -> Path:
        if self.path is not None:
            return Path(self.path).expanduser()
        log_dir = Path(os.environ.get("AWORLD_LOG_PATH", Path.cwd() / "logs")).expanduser()
        return log_dir / "trajectory.jsonl"

    @property
    def writes_legacy(self) -> bool:
        return self.format in {TrajectoryFormat.LEGACY, TrajectoryFormat.DUAL}

    @property
    def writes_v2(self) -> bool:
        return self.format in {TrajectoryFormat.DUAL, TrajectoryFormat.JSONL_V2}

    @classmethod
    def from_env(cls) -> "TrajectorySinkConfig":
        return cls(
            format=os.environ.get("AWORLD_TRAJECTORY_FORMAT", TrajectoryFormat.LEGACY.value),
            path=os.environ.get("AWORLD_TRAJECTORY_V2_PATH") or None,
        )

    @classmethod
    def from_sources(cls, config: Mapping[str, Any] | None = None) -> "TrajectorySinkConfig":
        """Resolve task-local settings over process defaults.

        Keeping this resolution next to the sink prevents the runner from
        developing a second interpretation of trajectory output modes.
        """

        env = cls.from_env()
        values = config or {}
        return cls(
            format=values.get("trajectory_format") or env.format,
            path=values.get("trajectory_v2_path") or env.path,
            max_record_bytes=(
                values.get("trajectory_max_record_bytes") or env.max_record_bytes
            ),
        )


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())


class TrajectoryJsonlSink:
    """Append complete JSONL records without going through the generic logger."""

    def __init__(self, config: TrajectorySinkConfig | None = None):
        self.config = config or TrajectorySinkConfig.from_env()

    def append(self, envelope: TrajectoryEnvelope) -> Path | None:
        if not self.config.writes_v2:
            return None
        line = envelope.to_json_line()
        if len(line) > self.config.max_record_bytes:
            raise TrajectoryIOError(
                f"trajectory record exceeds {self.config.max_record_bytes} byte limit"
            )
        path = self.config.jsonl_path
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = _path_lock(path)
        with lock:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            process_locked = False
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    process_locked = True
                view = memoryview(line)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("failed to append trajectory JSONL record")
                    view = view[written:]
            finally:
                if process_locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        return path


@dataclass(frozen=True, slots=True)
class TrajectoryReadDiagnostic:
    source: str
    line_number: int | None
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class TrajectorySnapshot:
    schema_version: str
    task_id: str
    revision: int
    trajectory: list[Any] | None
    llm_calls: list[Mapping[str, Any]]
    token_id_trajectory: Any
    is_sub_task: bool
    fidelity: str
    build_result: Mapping[str, Any]
    trajectory_ref: str | None
    trajectory_checksum: str | None
    record_checksum: str | None
    source: str
    line_number: int


@dataclass(frozen=True, slots=True)
class TrajectoryReadResult:
    records: tuple[TrajectorySnapshot, ...]
    diagnostics: tuple[TrajectoryReadDiagnostic, ...]


def _decode_nested_json(value: Any, *, max_depth: int = 3) -> Any:
    decoded = value
    for _ in range(max_depth):
        if not isinstance(decoded, str):
            break
        try:
            decoded = json.loads(decoded)
        except json.JSONDecodeError:
            break
    return decoded


def _legacy_snapshot(
    payload: Mapping[str, Any], *, source: str, line_number: int
) -> TrajectorySnapshot:
    task_id = payload.get("task_id")
    if task_id is None:
        raise TrajectoryIOError("legacy trajectory record is missing task_id")
    embedded_build_payload = _decode_nested_json(payload.get("trajectory_build_result"))
    if embedded_build_payload is not None and not isinstance(embedded_build_payload, Mapping):
        raise TrajectoryIOError("legacy trajectory_build_result must decode to an object")

    embedded_build_result = None
    if embedded_build_payload is not None:
        try:
            embedded_build_result = TrajectoryBuildResult.from_dict(embedded_build_payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise TrajectoryIOError(f"invalid legacy trajectory_build_result: {exc}") from exc
        if str(task_id) != embedded_build_result.task_id:
            raise TrajectoryIOError(
                "legacy task_id does not match trajectory_build_result.task_id"
            )

    trajectory = _decode_nested_json(payload.get("trajectory"))
    if trajectory is None and embedded_build_result is not None:
        if (
            embedded_build_result.persisted_items == 0
            and embedded_build_result.status is not TrajectoryBuildStatus.COMPLETE
        ):
            trajectory = []
    if trajectory is not None and not isinstance(trajectory, list):
        raise TrajectoryIOError("legacy trajectory must decode to a list or null")
    if trajectory is None and embedded_build_result is None:
        raise TrajectoryIOError("legacy trajectory must decode to a list")
    llm_calls = _decode_nested_json(payload.get("llm_calls", []))
    if not isinstance(llm_calls, list):
        raise TrajectoryIOError("legacy llm_calls must decode to a list")
    token_ids = _decode_nested_json(payload.get("token_id_trajectory"))
    if embedded_build_result is None:
        build_result = {
            "status": "complete" if trajectory else "empty",
            "fidelity": TrajectoryFidelity.LEGACY.value,
            "source_kind": "legacy_log",
            "persisted_items": len(trajectory or []),
            "trajectory_checksum": None,
        }
        trajectory_ref = None
        trajectory_checksum = None
    else:
        trajectory_ref = embedded_build_result.trajectory_ref
        _validate_projection_consistency(
            embedded_build_result,
            trajectory,
            label="legacy",
            # Old logger transport did not promise a trajectory checksum. Preserve
            # checksum-less partial records, but verify every checksum it does carry.
            require_nonempty_checksum=False,
        )
        trajectory_checksum = embedded_build_result.trajectory_checksum
        build_result = embedded_build_result.to_dict()
        build_result["source_build_fidelity"] = build_result["fidelity"]
        build_result["source_build_kind"] = build_result["source_kind"]
        build_result["fidelity"] = TrajectoryFidelity.LEGACY.value
        build_result["source_kind"] = "legacy_log"
    return TrajectorySnapshot(
        schema_version="legacy",
        task_id=str(task_id),
        revision=0,
        trajectory=trajectory,
        llm_calls=llm_calls,
        token_id_trajectory=token_ids,
        is_sub_task=bool(payload.get("is_sub_task", False)),
        fidelity=TrajectoryFidelity.LEGACY.value,
        build_result=build_result,
        trajectory_ref=trajectory_ref,
        trajectory_checksum=trajectory_checksum,
        record_checksum=None,
        source=source,
        line_number=line_number,
    )


def _v2_snapshot(envelope: TrajectoryEnvelope, *, source: str, line_number: int) -> TrajectorySnapshot:
    payload = envelope.to_dict()
    return TrajectorySnapshot(
        schema_version=SCHEMA_VERSION,
        task_id=envelope.build_result.task_id,
        revision=envelope.revision,
        trajectory=list(envelope.trajectory) if envelope.trajectory is not None else None,
        llm_calls=[dict(call) for call in envelope.llm_calls],
        token_id_trajectory=envelope.token_id_trajectory,
        is_sub_task=envelope.is_sub_task,
        fidelity=envelope.build_result.fidelity.value,
        build_result=envelope.build_result.to_dict(),
        trajectory_ref=envelope.build_result.trajectory_ref,
        trajectory_checksum=envelope.build_result.trajectory_checksum,
        record_checksum=payload["integrity"]["record_checksum"],
        source=source,
        line_number=line_number,
    )


def _candidate_text(line: str) -> str | None:
    clean = _ANSI_ESCAPE_RE.sub("", line).strip()
    if not clean:
        return None
    candidates = [0] if clean.startswith("{") else []
    for marker in (_V2_MARKER, *_LEGACY_MARKERS):
        marker_index = clean.find(marker)
        if marker_index >= 0:
            brace_index = clean.rfind("{", 0, marker_index + 1)
            if brace_index >= 0:
                candidates.append(brace_index)
    for index in sorted(set(candidates)):
        candidate = clean[index:].strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, Mapping):
                return candidate
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            parsed = ast.literal_eval(candidate)
            if isinstance(parsed, Mapping):
                return candidate
        except (SyntaxError, ValueError):
            pass
    return clean if any(marker in clean for marker in (_V2_MARKER, *_LEGACY_MARKERS)) else None


def _parse_candidate(candidate: str, *, source: str, line_number: int) -> TrajectorySnapshot:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(candidate)
        except (SyntaxError, ValueError) as exc:
            raise TrajectoryIOError("malformed trajectory record") from exc
    if not isinstance(payload, Mapping):
        raise TrajectoryIOError("trajectory record must be an object")
    schema_version = payload.get("schema_version")
    if schema_version is not None:
        if schema_version != SCHEMA_VERSION:
            raise TrajectoryIOError(f"unsupported trajectory schema: {schema_version}")
        return _v2_snapshot(
            TrajectoryEnvelope.from_dict(payload), source=source, line_number=line_number
        )
    return _legacy_snapshot(payload, source=source, line_number=line_number)


def _related_paths(path: Path, include_rotations: bool) -> list[Path]:
    if not include_rotations:
        return [path] if path.exists() else []
    parent = path.parent
    candidates: set[Path] = {path} if path.exists() else set()
    if not parent.exists():
        return []
    if path.name in {"trajectory.log", "trajectory.jsonl"}:
        for candidate in parent.iterdir():
            name = candidate.name
            if name.startswith("trajectory") and (
                ".log" in name or ".jsonl" in name or name.endswith(".zip")
            ):
                candidates.add(candidate)
    else:
        for candidate in parent.glob(f"{path.stem}*{path.suffix}*"):
            candidates.add(candidate)
        for candidate in parent.glob(f"{path.name}*"):
            candidates.add(candidate)
    return sorted(
        (candidate for candidate in candidates if candidate.is_file()),
        key=lambda candidate: (candidate.stat().st_mtime_ns, candidate.name),
    )


def _bounded_lines(handle: Any, max_record_bytes: int) -> Iterable[tuple[int, bytes]]:
    line_number = 0
    while True:
        raw_line = handle.readline(max_record_bytes + 2)
        if not raw_line:
            return
        line_number += 1
        if len(raw_line) > max_record_bytes and not raw_line.endswith(b"\n"):
            while True:
                remainder = handle.readline(max_record_bytes + 2)
                if not remainder or remainder.endswith(b"\n"):
                    break
            yield line_number, raw_line
            continue
        yield line_number, raw_line


def _iter_source_lines(
    paths: Iterable[Path], max_record_bytes: int
) -> Iterable[tuple[str, int, bytes]]:
    for path in paths:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                for member in sorted(archive.namelist()):
                    if member.endswith("/"):
                        continue
                    with archive.open(member) as handle:
                        for line_number, line in _bounded_lines(handle, max_record_bytes):
                            yield f"{path}!{member}", line_number, line
            continue
        with path.open("rb") as handle:
            for line_number, line in _bounded_lines(handle, max_record_bytes):
                yield str(path), line_number, line


def _select_latest(records: Sequence[TrajectorySnapshot]) -> tuple[TrajectorySnapshot, ...]:
    task_order: list[str] = []
    grouped: dict[str, list[TrajectorySnapshot]] = {}
    for record in records:
        if record.task_id not in grouped:
            task_order.append(record.task_id)
            grouped[record.task_id] = []
        grouped[record.task_id].append(record)

    selected: list[TrajectorySnapshot] = []
    for task_id in task_order:
        task_records = grouped[task_id]
        v2_records = [record for record in task_records if record.schema_version == SCHEMA_VERSION]
        if not v2_records:
            selected.append(task_records[-1])
            continue
        by_revision: dict[int, list[TrajectorySnapshot]] = {}
        for record in v2_records:
            by_revision.setdefault(record.revision, []).append(record)
        for revision, revision_records in by_revision.items():
            checksums = {record.record_checksum for record in revision_records}
            if len(checksums) != 1:
                raise TrajectoryRevisionConflictError(
                    f"task {task_id} revision {revision} has conflicting record checksums"
                )
        highest_revision = max(by_revision)
        highest = by_revision[highest_revision]
        selected.append(highest[-1])
    return tuple(selected)


def read_trajectory_records(
    path: str | Path,
    *,
    include_rotations: bool = True,
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
) -> TrajectoryReadResult:
    """Read and select legacy/v2 trajectory snapshots from one artifact family."""

    if isinstance(max_record_bytes, bool) or max_record_bytes <= 0:
        raise ValueError("max_record_bytes must be positive")
    source_path = Path(path).expanduser()
    records: list[TrajectorySnapshot] = []
    diagnostics: list[TrajectoryReadDiagnostic] = []
    for source, line_number, raw_line in _iter_source_lines(
        _related_paths(source_path, include_rotations), max_record_bytes
    ):
        if len(raw_line) > max_record_bytes:
            diagnostics.append(
                TrajectoryReadDiagnostic(
                    source, line_number, "record_too_large", f"record exceeds {max_record_bytes} bytes"
                )
            )
            continue
        text = raw_line.decode("utf-8", errors="replace")
        candidate = _candidate_text(text)
        if candidate is None:
            if text.strip():
                diagnostics.append(
                    TrajectoryReadDiagnostic(source, line_number, "ignored_header", "non-record log line")
                )
            continue
        try:
            records.append(_parse_candidate(candidate, source=source, line_number=line_number))
        except (TrajectoryChecksumMismatchError, TrajectoryRevisionConflictError):
            raise
        except (TrajectoryIOError, TypeError, KeyError) as exc:
            diagnostics.append(
                TrajectoryReadDiagnostic(source, line_number, "malformed_record", str(exc))
            )
    return TrajectoryReadResult(records=_select_latest(records), diagnostics=tuple(diagnostics))


def looks_like_trajectory_log(path: str | Path) -> bool:
    """Return whether a file family contains a recognizable legacy or v2 record."""

    source_path = Path(path).expanduser()
    try:
        for _, _, raw_line in _iter_source_lines(
            _related_paths(source_path, True), DEFAULT_MAX_RECORD_BYTES
        ):
            if len(raw_line) > DEFAULT_MAX_RECORD_BYTES:
                continue
            clean = _ANSI_ESCAPE_RE.sub("", raw_line.decode("utf-8", errors="replace"))
            if SCHEMA_VERSION in clean:
                return True
            if any(marker in clean for marker in _LEGACY_MARKERS) and (
                "'trajectory'" in clean or '"trajectory"' in clean
            ):
                return True
    except (OSError, zipfile.BadZipFile):
        return False
    return False
