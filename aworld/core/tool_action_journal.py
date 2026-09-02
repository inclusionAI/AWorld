"""Crash-tolerant append-only journal for Tool action runtime evidence.

The journal is deliberately independent from ``TaskResponse`` and the final
trajectory projection.  It records only events that crossed an AWorld runtime
boundary, allowing an external timeout supervisor to recover real Tool actions
and sandbox receipts without parsing text logs or inventing missing results.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from aworld.utils.serialized_util import to_serializable

try:  # POSIX cross-process append lock; thread lock is the portable fallback.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None


SCHEMA_VERSION = "aworld.tool-action-journal.v1"
RECORD_TYPE = "tool_action_journal_record"
JOURNAL_PATH_ENV = "AWORLD_TOOL_ACTION_JOURNAL_PATH"
DEFAULT_MAX_RECORD_BYTES = 64 * 1024 * 1024
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())


def _json_value(value: Any) -> Any:
    """Return a detached JSON value while preserving structured model fields.

    ``default=str`` alone turns Pydantic ``ActionModel``/``ActionResult``
    instances into opaque strings.  That makes a checksum-valid journal
    impossible to replay as a trajectory.  Use AWorld's generic serializer
    first, then round-trip through JSON to detach mutable caller-owned values.
    """
    return json.loads(
        json.dumps(
            to_serializable(value),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _checksum(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def configured_journal_path() -> Path | None:
    value = os.environ.get(JOURNAL_PATH_ENV)
    return Path(value).expanduser().resolve() if value else None


def _action_value(action: Any, name: str) -> Any:
    if isinstance(action, Mapping):
        return action.get(name)
    return getattr(action, name, None)


def tool_action_batch_id(actions: Sequence[Any]) -> str:
    """Return a stable batch identity without inspecting task semantics."""
    call_ids = [
        str(value)
        for action in actions
        if isinstance((value := _action_value(action, "tool_call_id")), str)
        and value
    ]
    identity: Any = (
        {"tool_call_ids": call_ids}
        if len(call_ids) == len(actions) and call_ids
        else {"actions": _json_value(list(actions))}
    )
    return "sha256:" + hashlib.sha256(_canonical_bytes(identity)).hexdigest()


def _context_stream_id(context: Any) -> str:
    value = getattr(context, "_tool_action_journal_stream_id", None)
    if not isinstance(value, str) or not value:
        value = uuid.uuid4().hex
        setattr(context, "_tool_action_journal_stream_id", value)
    return value


def append_tool_action_event(
    *,
    context: Any,
    event_type: str,
    actions: Sequence[Any],
    status: str,
    results: Sequence[Any] | None = None,
    batch_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    path: Path | None = None,
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
) -> Path | None:
    """Append and fsync one immutable Tool-boundary event.

    Callers treat journaling as observability: an append failure must not alter
    Tool execution semantics.  This function validates aggressively so corrupt
    or ambiguous records cannot later be treated as trajectory truth.
    """
    destination = path or configured_journal_path()
    if destination is None:
        return None
    if context is None:
        raise ValueError("Tool action journal requires a Context")
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("Tool action journal event_type must be non-empty")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("Tool action journal status must be non-empty")
    action_values = list(actions)
    resolved_batch_id = batch_id or tool_action_batch_id(action_values)
    identities: dict[str, str] = {}
    for name in ("task_id", "session_id", "trace_id"):
        try:
            value = getattr(context, name, None)
        except Exception:
            value = None
        if value is not None:
            identities[name] = str(value)
    recorded_at = time.time_ns()
    stream_id = _context_stream_id(context)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "event_type": event_type,
        "status": status,
        "recorded_at_epoch_ns": recorded_at,
        "context": identities,
        "stream_id": stream_id,
        "batch_id": resolved_batch_id,
        "actions": _json_value(action_values),
    }
    if results is not None:
        payload["results"] = _json_value(list(results))
    if metadata is not None:
        payload["metadata"] = _json_value(dict(metadata))

    destination.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(destination):
        sequence = getattr(context, "_tool_action_journal_next_sequence", 0)
        previous_checksum = getattr(
            context, "_tool_action_journal_previous_checksum", None
        )
        if not isinstance(sequence, int) or sequence < 0:
            raise ValueError("Tool action journal stream sequence is invalid")
        if previous_checksum is not None and not isinstance(previous_checksum, str):
            raise ValueError("Tool action journal previous checksum is invalid")
        payload["stream_sequence"] = sequence
        payload["previous_record_checksum"] = previous_checksum
        record = dict(payload)
        record_checksum = _checksum(payload)
        record["record_checksum"] = record_checksum
        encoded = _canonical_bytes(record) + b"\n"
        if len(encoded) > max_record_bytes:
            raise ValueError("Tool action journal record exceeds configured byte limit")
        with destination.open("ab") as stream:
            if fcntl is not None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        context._tool_action_journal_next_sequence = sequence + 1
        context._tool_action_journal_previous_checksum = record_checksum
    return destination


@dataclass(frozen=True)
class ToolActionJournalRecovery:
    events: tuple[dict[str, Any], ...]
    valid_record_count: int
    invalid_record_count: int
    trailing_partial_ignored: bool
    stream_count: int

    @property
    def available(self) -> bool:
        return self.valid_record_count > 0

    def to_evidence(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "available" if self.available else "unavailable",
            "valid_record_count": self.valid_record_count,
            "invalid_record_count": self.invalid_record_count,
            "trailing_partial_ignored": self.trailing_partial_ignored,
            "event_count": len(self.events),
            "stream_count": self.stream_count,
        }


def read_tool_action_journal(path: Path) -> ToolActionJournalRecovery:
    """Recover checksum-valid events, isolating damaged concurrent streams."""
    if not path.exists():
        return ToolActionJournalRecovery((), 0, 0, False, 0)
    records: list[dict[str, Any]] = []
    next_sequence: dict[str, int] = {}
    previous_checksum: dict[str, str | None] = {}
    invalid_streams: set[str] = set()
    valid = invalid = 0
    trailing_partial = False
    lines = path.read_bytes().splitlines(keepends=True)
    for index, raw in enumerate(lines):
        if not raw.strip():
            continue
        parsed: Any = None
        is_last_partial = index == len(lines) - 1 and not raw.endswith(b"\n")
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("journal record must be an object")
            checksum = parsed.pop("record_checksum", None)
            if parsed.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("journal schema mismatch")
            if parsed.get("record_type") != RECORD_TYPE:
                raise ValueError("journal record type mismatch")
            if checksum != _checksum(parsed):
                raise ValueError("journal checksum mismatch")
            stream_id = parsed.get("stream_id")
            sequence = parsed.get("stream_sequence")
            previous = parsed.get("previous_record_checksum")
            if not isinstance(stream_id, str) or not stream_id:
                raise ValueError("journal stream id is invalid")
            if stream_id in invalid_streams:
                raise ValueError("journal stream was already invalidated")
            if sequence != next_sequence.get(stream_id, 0):
                raise ValueError("journal stream sequence is invalid")
            if previous != previous_checksum.get(stream_id):
                raise ValueError("journal stream hash chain is invalid")
            if not isinstance(parsed.get("recorded_at_epoch_ns"), int):
                raise ValueError("journal record time is invalid")
            if not isinstance(parsed.get("actions"), list):
                raise ValueError("journal actions must be a list")
            next_sequence[stream_id] = sequence + 1
            previous_checksum[stream_id] = checksum
            parsed["record_checksum"] = checksum
            records.append(parsed)
            valid += 1
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            invalid += 1
            trailing_partial = trailing_partial or is_last_partial
            if isinstance(parsed, dict) and isinstance(parsed.get("stream_id"), str):
                invalid_streams.add(parsed["stream_id"])
            continue
    records = [
        record for record in records if record.get("stream_id") not in invalid_streams
    ]
    records.sort(
        key=lambda record: (
            record.get("recorded_at_epoch_ns", 0),
            record.get("stream_id", ""),
            record.get("stream_sequence", 0),
        )
    )
    return ToolActionJournalRecovery(
        events=tuple(records),
        valid_record_count=len(records),
        invalid_record_count=invalid + (valid - len(records)),
        trailing_partial_ignored=trailing_partial,
        stream_count=len({record["stream_id"] for record in records}),
    )


__all__ = [
    "JOURNAL_PATH_ENV",
    "ToolActionJournalRecovery",
    "append_tool_action_event",
    "configured_journal_path",
    "read_tool_action_journal",
    "tool_action_batch_id",
]
