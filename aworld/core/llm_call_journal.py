"""Crash-tolerant mutation journal for in-flight ``Context.llm_calls``.

The journal is runtime evidence, not a trajectory projection.  It exists so an
external supervisor can distinguish a provider request that never returned from
a completed run whose final trajectory persistence failed.  Finalized
``trajectory.log``/``trajectory.jsonl`` and runtime events remain authoritative
for Raw trajectory content.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # POSIX cross-process append lock; thread lock is the portable fallback.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None


SCHEMA_VERSION = "aworld.llm-call-journal.v1"
RECORD_TYPE = "llm_call_journal_record"
_COMPATIBLE_RECORD_TYPES = {RECORD_TYPE, "llm_calls_snapshot"}
JOURNAL_PATH_ENV = "AWORLD_LLM_CALL_JOURNAL_PATH"
DEFAULT_MAX_RECORD_BYTES = 64 * 1024 * 1024
DEFAULT_COMPRESSION_THRESHOLD_BYTES = 1024 * 1024
DEFAULT_MAX_DECOMPRESSED_RECORD_BYTES = 512 * 1024 * 1024
COMPRESSED_ENCODING = "zlib+base64"
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())


def _json_value(value: Any) -> Any:
    """Return a stable JSON value without requiring call sites to pre-normalize."""
    return json.loads(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
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


def _compressed_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    encoded = _canonical_bytes(payload)
    compressed = zlib.compress(encoded)
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "stream_id": payload.get("stream_id"),
        "encoding": COMPRESSED_ENCODING,
        "uncompressed_bytes": len(encoded),
        "uncompressed_sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "compressed_payload": base64.b64encode(compressed).decode("ascii"),
    }


def _decompress_record(
    wrapper: Mapping[str, Any],
    *,
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_RECORD_BYTES,
) -> dict[str, Any]:
    if wrapper.get("encoding") != COMPRESSED_ENCODING:
        raise ValueError("journal record encoding is unsupported")
    declared_size = wrapper.get("uncompressed_bytes")
    if (
        not isinstance(declared_size, int)
        or declared_size < 0
        or declared_size > max_decompressed_bytes
    ):
        raise ValueError("journal compressed record size is invalid")
    compressed_payload = wrapper.get("compressed_payload")
    if not isinstance(compressed_payload, str):
        raise ValueError("journal compressed payload is invalid")
    try:
        compressed = base64.b64decode(compressed_payload, validate=True)
        decompressor = zlib.decompressobj()
        encoded = decompressor.decompress(compressed, max_decompressed_bytes + 1)
        if len(encoded) > max_decompressed_bytes or decompressor.unconsumed_tail:
            raise ValueError("journal compressed record exceeds decompression limit")
        encoded += decompressor.flush(max_decompressed_bytes + 1 - len(encoded))
    except (ValueError, zlib.error) as exc:
        raise ValueError("journal compressed payload cannot be decoded") from exc
    if len(encoded) != declared_size:
        raise ValueError("journal compressed record size mismatch")
    expected_sha = "sha256:" + hashlib.sha256(encoded).hexdigest()
    if wrapper.get("uncompressed_sha256") != expected_sha:
        raise ValueError("journal compressed record digest mismatch")
    try:
        record = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("journal decompressed payload is invalid") from exc
    if not isinstance(record, dict):
        raise ValueError("journal decompressed record must be an object")
    if record.get("stream_id") != wrapper.get("stream_id"):
        raise ValueError("journal compressed record stream mismatch")
    return record


def _mapping_patch(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return unambiguous path operations; lists remain atomic values."""
    operations: list[dict[str, Any]] = []

    def walk(old: Any, new: Any, path: list[str]) -> None:
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            for key in sorted(set(old) | set(new)):
                next_path = [*path, str(key)]
                if key not in new:
                    operations.append({"op": "remove", "path": next_path})
                elif key not in old:
                    operations.append(
                        {
                            "op": "replace",
                            "path": next_path,
                            "value": _json_value(new[key]),
                        }
                    )
                elif old[key] != new[key]:
                    walk(old[key], new[key], next_path)
            return
        operations.append({"op": "replace", "path": path, "value": _json_value(new)})

    walk(previous, current, [])
    return operations


def _apply_mapping_patch(
    previous: Mapping[str, Any], patch: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    updated = _json_value(previous)
    for operation in patch:
        op = operation.get("op")
        path = operation.get("path")
        if op not in {"remove", "replace"} or not isinstance(path, list) or not path:
            raise ValueError("journal patch operation is invalid")
        parent = updated
        for key in path[:-1]:
            if not isinstance(key, str) or not isinstance(parent.get(key), dict):
                raise ValueError("journal patch path is invalid")
            parent = parent[key]
        key = path[-1]
        if not isinstance(key, str):
            raise ValueError("journal patch path is invalid")
        if op == "remove":
            if key not in parent:
                raise ValueError("journal patch remove target is missing")
            parent.pop(key)
        else:
            if "value" not in operation:
                raise ValueError("journal patch replacement value is missing")
            parent[key] = _json_value(operation["value"])
    return updated


def configured_journal_path() -> Path | None:
    value = os.environ.get(JOURNAL_PATH_ENV)
    return Path(value).expanduser().resolve() if value else None


def _context_stream_id(context: Any) -> str:
    value = getattr(context, "_llm_call_journal_stream_id", None)
    if not isinstance(value, str) or not value:
        value = uuid.uuid4().hex
        setattr(context, "_llm_call_journal_stream_id", value)
    return value


def append_llm_call_snapshot(
    *,
    context: Any,
    event_type: str,
    llm_calls: Sequence[Mapping[str, Any]],
    call_recorded_at_epoch_ns: Sequence[int] | None = None,
    path: Path | None = None,
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
) -> Path | None:
    """Append and fsync one immutable snapshot when journaling is configured."""
    destination = path or configured_journal_path()
    if destination is None:
        return None
    if not event_type:
        raise ValueError("LLM call journal event_type must not be empty")
    identities: dict[str, Any] = {}
    for name in ("task_id", "session_id", "trace_id"):
        try:
            value = getattr(context, name, None)
        except Exception:
            value = None
        if value is not None:
            identities[name] = str(value)
    recorded_at = time.time_ns()
    call_times = (
        [recorded_at] * len(llm_calls)
        if call_recorded_at_epoch_ns is None
        else list(call_recorded_at_epoch_ns)
    )
    if len(call_times) != len(llm_calls) or not all(
        isinstance(value, int) and value >= 0 for value in call_times
    ):
        raise ValueError("journal snapshot call timestamps are invalid")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "event_type": event_type,
        "recorded_at_epoch_ns": recorded_at,
        "context": identities,
        "stream_id": _context_stream_id(context),
        "operation": "snapshot",
        "llm_calls": _json_value(list(llm_calls)),
        "call_recorded_at_epoch_ns": call_times,
    }
    return _append_payload(
        destination=destination,
        payload=payload,
        max_record_bytes=max_record_bytes,
        context=context,
    )


def append_llm_call_mutation(
    *,
    context: Any,
    event_type: str,
    index: int,
    current: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
    recorded_at_epoch_ns: int | None = None,
    path: Path | None = None,
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
) -> Path | None:
    """Persist one append or recursive replace mutation for a single call."""
    destination = path or configured_journal_path()
    if destination is None:
        return None
    identities: dict[str, Any] = {}
    for name in ("task_id", "session_id", "trace_id"):
        try:
            value = getattr(context, name, None)
        except Exception:
            value = None
        if value is not None:
            identities[name] = str(value)
    recorded_at = (
        time.time_ns() if recorded_at_epoch_ns is None else recorded_at_epoch_ns
    )
    if not isinstance(recorded_at, int) or recorded_at < 0:
        raise ValueError("journal mutation timestamp is invalid")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "event_type": event_type,
        "recorded_at_epoch_ns": recorded_at,
        "context": identities,
        "stream_id": _context_stream_id(context),
        "operation": "append" if previous is None else "replace",
        "index": index,
    }
    if previous is None:
        payload["llm_call"] = _json_value(current)
    else:
        payload["patch"] = _mapping_patch(previous, current)
    return _append_payload(
        destination=destination,
        payload=payload,
        max_record_bytes=max_record_bytes,
        context=context,
    )


def _append_payload(
    *,
    destination: Path,
    payload: Mapping[str, Any],
    max_record_bytes: int,
    context: Any | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(destination):
        record_payload = dict(payload)
        if context is not None:
            sequence = getattr(context, "_llm_call_journal_next_sequence", 0)
            previous_checksum = getattr(
                context, "_llm_call_journal_previous_checksum", None
            )
            if not isinstance(sequence, int) or sequence < 0:
                raise ValueError("LLM call journal stream sequence is invalid")
            if previous_checksum is not None and not isinstance(previous_checksum, str):
                raise ValueError("LLM call journal previous checksum is invalid")
            record_payload["stream_sequence"] = sequence
            record_payload["previous_record_checksum"] = previous_checksum
        stored_payload = record_payload
        if len(_canonical_bytes(record_payload)) >= DEFAULT_COMPRESSION_THRESHOLD_BYTES:
            stored_payload = _compressed_record(record_payload)
        record = dict(stored_payload)
        record_checksum = _checksum(stored_payload)
        record["record_checksum"] = record_checksum
        encoded = _canonical_bytes(record) + b"\n"
        if len(encoded) > max_record_bytes:
            raise ValueError("LLM call journal record exceeds configured byte limit")
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
        if context is not None:
            context._llm_call_journal_next_sequence = sequence + 1
            context._llm_call_journal_previous_checksum = record_checksum
    return destination


@dataclass(frozen=True)
class LLMCallJournalStreamRecovery:
    stream_id: str
    llm_calls: tuple[dict[str, Any], ...]
    latest_event_type: str | None
    latest_recorded_at_epoch_ns: int | None
    valid_record_count: int
    invalid_record_count: int
    call_recorded_at_epoch_ns: tuple[int, ...] = ()


@dataclass(frozen=True)
class LLMCallJournalRecovery:
    latest_llm_calls: tuple[dict[str, Any], ...]
    latest_event_type: str | None
    latest_recorded_at_epoch_ns: int | None
    valid_record_count: int
    invalid_record_count: int
    trailing_partial_ignored: bool
    latest_stream_id: str | None = None
    streams: tuple[LLMCallJournalStreamRecovery, ...] = ()

    @property
    def available(self) -> bool:
        return self.valid_record_count > 0

    @property
    def merged_llm_calls(self) -> tuple[dict[str, Any], ...]:
        """Merge isolated stream tips by stable provider-attempt identity."""
        merged: list[dict[str, Any]] = []
        request_positions: dict[str, int] = {}
        unresolved_call_positions: dict[str, int] = {}
        timed_calls = sorted(
            [
                (
                    timestamp,
                    stream.stream_id,
                    index,
                    call,
                )
                for stream in self.streams
                for index, (call, timestamp) in enumerate(
                    zip(stream.llm_calls, stream.call_recorded_at_epoch_ns)
                )
            ],
            key=lambda entry: entry[:3],
        )
        for _, _, _, call in timed_calls:
            request_id = call.get("request_id")
            call_id = call.get("call_id")
            request_id = (
                request_id if isinstance(request_id, str) and request_id else None
            )
            call_id = call_id if isinstance(call_id, str) and call_id else None
            if request_id is not None and request_id in request_positions:
                position = request_positions[request_id]
                merged[position] = _json_value(call)
            elif (
                request_id is not None
                and call_id is not None
                and call_id in unresolved_call_positions
            ):
                position = unresolved_call_positions.pop(call_id)
                merged[position] = _json_value(call)
                request_positions[request_id] = position
            elif request_id is not None:
                position = len(merged)
                merged.append(_json_value(call))
                request_positions[request_id] = position
            elif call_id is not None and call_id in unresolved_call_positions:
                position = unresolved_call_positions[call_id]
                merged[position] = _json_value(call)
            else:
                position = len(merged)
                merged.append(_json_value(call))
                if call_id is not None:
                    unresolved_call_positions[call_id] = position
        return tuple(merged)

    def to_evidence(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "available" if self.available else "unavailable",
            "latest_event_type": self.latest_event_type,
            "latest_recorded_at_epoch_ns": self.latest_recorded_at_epoch_ns,
            "valid_record_count": self.valid_record_count,
            "invalid_record_count": self.invalid_record_count,
            "trailing_partial_ignored": self.trailing_partial_ignored,
            "llm_call_count": len(self.latest_llm_calls),
            "merged_llm_call_count": len(self.merged_llm_calls),
            "stream_count": len(self.streams),
            "latest_stream_id": self.latest_stream_id,
        }


def read_llm_call_journal(path: Path) -> LLMCallJournalRecovery:
    """Recover the latest checksum-valid snapshot, ignoring a torn final write."""
    states: dict[str, list[dict[str, Any]]] = {}
    state_call_times: dict[str, list[int]] = {}
    stream_valid: dict[str, int] = {}
    stream_invalid: dict[str, int] = {}
    stream_chain_valid: dict[str, bool] = {}
    stream_chain_mode: dict[str, bool] = {}
    stream_next_sequence: dict[str, int] = {}
    stream_previous_checksum: dict[str, str | None] = {}
    stream_events: dict[str, str | None] = {}
    stream_times: dict[str, int | None] = {}
    latest_calls: tuple[dict[str, Any], ...] = ()
    latest_event_type = None
    latest_recorded_at = None
    latest_stream_id = None
    valid = invalid = 0
    trailing_partial = False
    if not path.exists():
        return LLMCallJournalRecovery((), None, None, 0, 0, False)
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            record = None
            is_last_partial = not raw.endswith(b"\n")
            try:
                stored_record = json.loads(raw)
                if not isinstance(stored_record, dict):
                    raise ValueError("journal record must be an object")
                record = stored_record
                checksum = stored_record.pop("record_checksum", None)
                if checksum != _checksum(stored_record):
                    raise ValueError("journal checksum mismatch")
                record = (
                    _decompress_record(stored_record)
                    if "encoding" in stored_record
                    else stored_record
                )
                if not isinstance(record, dict):
                    raise ValueError("journal record must be an object")
                if record.get("schema_version") != SCHEMA_VERSION:
                    raise ValueError("journal schema mismatch")
                if record.get("record_type") not in _COMPATIBLE_RECORD_TYPES:
                    raise ValueError("journal record type mismatch")
                stream_id = record.get("stream_id", "legacy")
                if not isinstance(stream_id, str) or not stream_id:
                    raise ValueError("journal stream id is invalid")
                has_sequence = "stream_sequence" in record
                has_previous_checksum = "previous_record_checksum" in record
                if has_sequence != has_previous_checksum:
                    raise ValueError("journal stream chain fields are incomplete")
                uses_chain = has_sequence and has_previous_checksum
                existing_chain_mode = stream_chain_mode.get(stream_id)
                if (
                    existing_chain_mode is not None
                    and existing_chain_mode != uses_chain
                ):
                    raise ValueError("journal stream chain mode changed")
                if uses_chain:
                    sequence = record.get("stream_sequence")
                    previous_checksum = record.get("previous_record_checksum")
                    expected_sequence = stream_next_sequence.get(stream_id, 0)
                    expected_previous = stream_previous_checksum.get(stream_id)
                    if (
                        not isinstance(sequence, int)
                        or sequence != expected_sequence
                        or previous_checksum != expected_previous
                    ):
                        raise ValueError("journal stream hash chain is invalid")
                state = states.setdefault(stream_id, [])
                call_times = state_call_times.setdefault(stream_id, [])
                chain_valid = stream_chain_valid.setdefault(stream_id, True)
                record_time = record.get("recorded_at_epoch_ns")
                if not isinstance(record_time, int):
                    raise ValueError("journal record time is invalid")
                operation = record.get("operation", "snapshot")
                if operation == "snapshot":
                    calls = record.get("llm_calls")
                    if not isinstance(calls, list) or not all(
                        isinstance(call, dict) for call in calls
                    ):
                        raise ValueError("journal llm_calls must be a list of objects")
                    next_state = _json_value(calls)
                    snapshot_call_times = record.get("call_recorded_at_epoch_ns")
                    if snapshot_call_times is None:
                        next_call_times = [record_time] * len(next_state)
                    elif (
                        not isinstance(snapshot_call_times, list)
                        or len(snapshot_call_times) != len(next_state)
                        or not all(
                            isinstance(value, int) and value >= 0
                            for value in snapshot_call_times
                        )
                    ):
                        raise ValueError("journal snapshot call timestamps are invalid")
                    else:
                        next_call_times = list(snapshot_call_times)
                elif operation == "append":
                    mutation_index = record.get("index")
                    call = record.get("llm_call")
                    if mutation_index != len(state) or not isinstance(call, dict):
                        raise ValueError("journal append mutation is not contiguous")
                    next_state = [*state, _json_value(call)]
                    next_call_times = [*call_times, record_time]
                elif operation == "replace":
                    mutation_index = record.get("index")
                    patch = record.get("patch")
                    if (
                        not isinstance(mutation_index, int)
                        or mutation_index < 0
                        or mutation_index >= len(state)
                        or not isinstance(patch, list)
                        or not all(isinstance(operation, dict) for operation in patch)
                    ):
                        raise ValueError("journal replace mutation is invalid")
                    next_state = list(state)
                    next_state[mutation_index] = _apply_mapping_patch(
                        state[mutation_index], patch
                    )
                    next_call_times = list(call_times)
                    next_call_times[mutation_index] = record_time
                else:
                    raise ValueError("journal operation is unsupported")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                invalid += 1
                trailing_partial = trailing_partial or is_last_partial
                failed_stream = (
                    record.get("stream_id", "legacy")
                    if isinstance(record, dict)
                    else "unparsed"
                )
                if isinstance(failed_stream, str):
                    stream_invalid[failed_stream] = (
                        stream_invalid.get(failed_stream, 0) + 1
                    )
                    stream_chain_valid[failed_stream] = False
                continue
            if not chain_valid:
                invalid += 1
                stream_invalid[stream_id] = stream_invalid.get(stream_id, 0) + 1
                continue
            valid += 1
            stream_chain_mode.setdefault(stream_id, uses_chain)
            if uses_chain:
                stream_next_sequence[stream_id] = sequence + 1
                stream_previous_checksum[stream_id] = checksum
            states[stream_id] = next_state
            state_call_times[stream_id] = next_call_times
            stream_valid[stream_id] = stream_valid.get(stream_id, 0) + 1
            stream_events[stream_id] = record.get("event_type")
            stream_times[stream_id] = record.get("recorded_at_epoch_ns")
            latest_calls = tuple(next_state)
            latest_event_type = record.get("event_type")
            latest_recorded_at = record.get("recorded_at_epoch_ns")
            latest_stream_id = stream_id
    stream_recoveries = tuple(
        LLMCallJournalStreamRecovery(
            stream_id=stream_id,
            llm_calls=tuple(state),
            latest_event_type=stream_events.get(stream_id),
            latest_recorded_at_epoch_ns=stream_times.get(stream_id),
            valid_record_count=stream_valid.get(stream_id, 0),
            invalid_record_count=stream_invalid.get(stream_id, 0),
            call_recorded_at_epoch_ns=tuple(state_call_times.get(stream_id, ())),
        )
        for stream_id, state in sorted(states.items())
        if stream_valid.get(stream_id, 0) > 0
    )
    return LLMCallJournalRecovery(
        latest_llm_calls=latest_calls,
        latest_event_type=latest_event_type,
        latest_recorded_at_epoch_ns=latest_recorded_at,
        valid_record_count=valid,
        invalid_record_count=invalid,
        trailing_partial_ignored=trailing_partial,
        latest_stream_id=latest_stream_id,
        streams=stream_recoveries,
    )
