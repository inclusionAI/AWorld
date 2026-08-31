"""Typed control-plane contracts for trajectory construction.

The models in this module describe how a trajectory projection was built and
delivered.  They intentionally do not contain SAR items, provider messages, or
Tool output; those remain in their existing data/truth planes.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar


class TrajectoryFidelity(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    PLACEHOLDER = "placeholder"
    UNAVAILABLE = "unavailable"
    BUILD_FAILED = "build_failed"
    LEGACY = "legacy"


class TrajectoryBuildStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    EMPTY = "empty"
    FAILED = "failed"


class TrajectorySourceKind(str, Enum):
    EVENT_STATE = "event_state"
    LEGACY_LOG = "legacy_log"


class TrajectoryReasonCode(str, Enum):
    EXECUTION_NOT_STARTED = "execution_not_started"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_LOAD_FAILED = "agent_load_failed"
    EXECUTOR_CREATION_FAILED = "executor_creation_failed"
    ENVIRONMENT_INCOMPATIBLE = "environment_incompatible"
    TIMEZONE_DATA_MISSING = "timezone_data_missing"
    NATIVE_DEPENDENCY_INCOMPATIBLE = "native_dependency_incompatible"
    EXECUTION_LOG_MISSING = "execution_log_missing"
    SOURCE_NOT_FINALIZED = "source_not_finalized"
    TRAJECTORY_UPDATE_TIMEOUT = "trajectory_update_timeout"
    TRAJECTORY_BUILD_FAILED = "trajectory_build_failed"
    TRAJECTORY_STORAGE_EMPTY = "trajectory_storage_empty"
    TASKRESPONSE_BINDING_MISSING = "taskresponse_binding_missing"
    RUNTIME_ARTIFACT_UPLOAD_FAILED = "runtime_artifact_upload_failed"
    SCHEDULER_ARTIFACT_MISSING = "scheduler_artifact_missing"
    EXIT_STATUS_LOST = "exit_status_lost"
    CHECKSUM_MISMATCH = "checksum_mismatch"


class TrajectoryDeliveryState(str, Enum):
    PERSISTED = "persisted"
    FAILED = "failed"
    NOT_REQUESTED = "not_requested"


@dataclass(frozen=True, slots=True)
class TrajectoryDeliveryTargetReceipt:
    """Sanitized outcome for one trajectory delivery sink."""

    status: TrajectoryDeliveryState
    record_checksum: str | None = None
    reason_code: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", TrajectoryDeliveryState(self.status))
        _validate_checksum("record_checksum", self.record_checksum)
        if self.status is TrajectoryDeliveryState.FAILED and not self.error_code:
            raise ValueError("failed delivery receipt requires error_code")
        if self.status is not TrajectoryDeliveryState.FAILED and self.error_code is not None:
            raise ValueError("error_code is only valid for failed delivery receipts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "record_checksum": self.record_checksum,
            "reason_code": self.reason_code,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class TrajectoryDeliveryReceipt:
    """Immutable control-plane receipt without artifact paths or record bodies."""

    requested_format: str
    legacy: TrajectoryDeliveryTargetReceipt
    v2: TrajectoryDeliveryTargetReceipt

    def __post_init__(self) -> None:
        if self.requested_format not in {"legacy", "dual", "jsonl_v2", "invalid"}:
            raise ValueError("requested_format must be a supported format or invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_format": self.requested_format,
            "legacy": self.legacy.to_dict(),
            "v2": self.v2.to_dict(),
        }


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON does not support non-finite floats")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical datetime must be timezone-aware")
        return _format_datetime(value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON mappings require string keys")
        return {key: _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _canonical_json_value(getattr(value, item.name))
            for item in fields(value)
        }
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="json")
        except TypeError:
            dumped = value.model_dump()
        return _canonical_json_value(dumped)
    if hasattr(value, "to_dict"):
        return _canonical_json_value(value.to_dict())
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_trajectory_bytes(trajectory: Sequence[Any]) -> bytes:
    """Return the stable UTF-8 JSON representation used for trajectory hashes.

    The representation is a JSON array with lexicographically sorted object
    keys, compact separators, preserved Unicode, and no non-finite numbers.
    Logger prefixes, TaskResponse fields, and build metadata are deliberately
    excluded so every delivery boundary can compare the same projection hash.
    """

    if isinstance(trajectory, (str, bytes, bytearray)) or not isinstance(trajectory, Sequence):
        raise TypeError("trajectory must be a sequence of trajectory items")
    normalized = _canonical_json_value(list(trajectory))
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def compute_trajectory_checksum(trajectory: Sequence[Any]) -> str:
    """Compute the canonical, algorithm-qualified trajectory checksum."""

    digest = hashlib.sha256(canonical_trajectory_bytes(trajectory)).hexdigest()
    return f"sha256:{digest}"


def _format_datetime(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError("created_at must be an ISO-8601 string or datetime")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _validate_checksum(name: str, value: str | None) -> None:
    if value is None:
        return
    prefix = "sha256:"
    digest = value[len(prefix):] if value.startswith(prefix) else ""
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{name} must be a lowercase sha256:<hex> checksum")


@dataclass(frozen=True, slots=True)
class TrajectoryBuildResult:
    """Immutable control-plane outcome for one trajectory build.

    Counts and hashes describe the existing event/SAR projection.  Semantic
    trajectory content is never copied into this object.
    """

    SCHEMA_VERSION: ClassVar[str] = "aworld.trajectory.build.v1"

    task_id: str
    session_id: str | None
    trace_id: str | None
    task_epoch: int | None
    status: TrajectoryBuildStatus
    fidelity: TrajectoryFidelity
    reason_code: TrajectoryReasonCode | str | None
    source_kind: TrajectorySourceKind
    source_high_watermark: str | int | None
    scheduled_updates: int
    completed_updates: int
    failed_updates: int
    pending_updates: int
    source_agent_messages: int
    llm_call_count: int
    tool_call_count: int
    persisted_items: int
    trajectory_ref: str | None
    source_checksum: str | None
    trajectory_checksum: str | None
    builder_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", TrajectoryBuildStatus(self.status))
        object.__setattr__(self, "fidelity", TrajectoryFidelity(self.fidelity))
        object.__setattr__(self, "source_kind", TrajectorySourceKind(self.source_kind))
        if self.reason_code is not None and not isinstance(self.reason_code, TrajectoryReasonCode):
            try:
                object.__setattr__(self, "reason_code", TrajectoryReasonCode(self.reason_code))
            except ValueError:
                if not isinstance(self.reason_code, str) or not self.reason_code.strip():
                    raise ValueError("reason_code must be a non-empty string when provided")

        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("task_id must be a non-empty string")
        if not isinstance(self.builder_version, str) or not self.builder_version:
            raise ValueError("builder_version must be a non-empty string")
        if self.task_epoch is not None and (isinstance(self.task_epoch, bool) or self.task_epoch < 0):
            raise ValueError("task_epoch must be a non-negative integer when provided")
        if isinstance(self.source_high_watermark, bool) or not isinstance(
            self.source_high_watermark, (str, int, type(None))
        ):
            raise ValueError("source_high_watermark must be a string, integer, or None")

        count_names = (
            "scheduled_updates",
            "completed_updates",
            "failed_updates",
            "pending_updates",
            "source_agent_messages",
            "llm_call_count",
            "tool_call_count",
            "persisted_items",
        )
        for name in count_names:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

        reconciled_updates = self.completed_updates + self.failed_updates + self.pending_updates
        if self.scheduled_updates != reconciled_updates:
            raise ValueError(
                "scheduled_updates must equal completed_updates + failed_updates + pending_updates"
            )

        allowed_fidelity = {
            TrajectoryBuildStatus.COMPLETE: {TrajectoryFidelity.COMPLETE, TrajectoryFidelity.LEGACY},
            TrajectoryBuildStatus.PARTIAL: {
                TrajectoryFidelity.PARTIAL,
                TrajectoryFidelity.PLACEHOLDER,
                TrajectoryFidelity.LEGACY,
            },
            TrajectoryBuildStatus.EMPTY: {
                TrajectoryFidelity.UNAVAILABLE,
                TrajectoryFidelity.PLACEHOLDER,
                TrajectoryFidelity.LEGACY,
            },
            TrajectoryBuildStatus.FAILED: {TrajectoryFidelity.BUILD_FAILED},
        }
        if self.fidelity not in allowed_fidelity[self.status]:
            raise ValueError(f"fidelity {self.fidelity.value} is invalid for status {self.status.value}")

        if self.status is TrajectoryBuildStatus.COMPLETE:
            if self.failed_updates or self.pending_updates:
                raise ValueError("complete trajectory cannot have failed or pending updates")
            if self.persisted_items == 0:
                raise ValueError("complete trajectory must have persisted_items")
            if self.trajectory_checksum is None:
                raise ValueError("complete trajectory must have trajectory_checksum")
        elif self.reason_code is None:
            raise ValueError("reason_code is required for partial, empty, and failed results")

        if self.status is TrajectoryBuildStatus.EMPTY and self.persisted_items != 0:
            raise ValueError("empty trajectory must have persisted_items equal to zero")
        if self.trajectory_ref is not None and self.trajectory_checksum is None:
            raise ValueError("trajectory_ref requires trajectory_checksum")

        _validate_checksum("source_checksum", self.source_checksum)
        _validate_checksum("trajectory_checksum", self.trajectory_checksum)
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "task_epoch": self.task_epoch,
            "status": self.status.value,
            "fidelity": self.fidelity.value,
            "reason_code": _enum_value(self.reason_code),
            "source_kind": self.source_kind.value,
            "source_high_watermark": self.source_high_watermark,
            "scheduled_updates": self.scheduled_updates,
            "completed_updates": self.completed_updates,
            "failed_updates": self.failed_updates,
            "pending_updates": self.pending_updates,
            "source_agent_messages": self.source_agent_messages,
            "llm_call_count": self.llm_call_count,
            "tool_call_count": self.tool_call_count,
            "persisted_items": self.persisted_items,
            "trajectory_ref": self.trajectory_ref,
            "source_checksum": self.source_checksum,
            "trajectory_checksum": self.trajectory_checksum,
            "builder_version": self.builder_version,
            "created_at": _format_datetime(self.created_at),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrajectoryBuildResult":
        values = dict(payload)
        schema_version = values.pop("schema_version", cls.SCHEMA_VERSION)
        if schema_version != cls.SCHEMA_VERSION:
            raise ValueError(f"unsupported trajectory build schema: {schema_version}")
        values["created_at"] = _parse_datetime(values["created_at"])
        return cls(**values)
