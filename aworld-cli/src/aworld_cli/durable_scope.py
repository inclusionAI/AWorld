"""Bounded identities for reconciling durable trajectory evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


MAX_SCOPE_TEXT_CHARS = 256
MAX_TASK_EPOCH_TEXT_CHARS = 128
MAX_TASK_EPOCH_INT = 2**63 - 1


def _text(value: Any, *, limit: int = MAX_SCOPE_TEXT_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized and len(normalized) <= limit else None


def task_epoch(value: Any) -> str | int | None:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_TASK_EPOCH_INT
    ):
        return value
    return _text(value, limit=MAX_TASK_EPOCH_TEXT_CHARS)


def normalize_scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    scope: dict[str, Any] = {}
    for key in ("task_id", "session_id"):
        normalized = _text(value.get(key))
        if normalized is not None:
            scope[key] = normalized
    epoch = task_epoch(value.get("task_epoch"))
    if epoch is not None:
        scope["task_epoch"] = epoch
    boundary = _text(value.get("run_boundary_id") or value.get("trace_id"))
    if boundary is not None:
        scope["run_boundary_id"] = boundary
    return scope


def scope_from_context(context: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for source, target in (
        ("task_id", "task_id"),
        ("session_id", "session_id"),
        ("task_epoch", "task_epoch"),
        ("trace_id", "run_boundary_id"),
    ):
        try:
            values[target] = getattr(context, source, None)
        except Exception:
            values[target] = None
    return normalize_scope(values)


def scope_key(value: Any) -> tuple[Any, ...] | None:
    scope = normalize_scope(value)
    if not all(
        key in scope
        for key in ("task_id", "session_id", "task_epoch", "run_boundary_id")
    ):
        return None
    epoch = scope["task_epoch"]
    typed_epoch = (
        ("int", epoch) if isinstance(epoch, int) and not isinstance(epoch, bool) else ("str", epoch)
    )
    return (
        scope["task_id"],
        scope["session_id"],
        typed_epoch,
        scope["run_boundary_id"],
    )


def scopes_match(left: Any, right: Any) -> bool:
    left_key = scope_key(left)
    return left_key is not None and left_key == scope_key(right)


__all__ = [
    "MAX_SCOPE_TEXT_CHARS",
    "MAX_TASK_EPOCH_TEXT_CHARS",
    "MAX_TASK_EPOCH_INT",
    "normalize_scope",
    "scope_from_context",
    "scope_key",
    "scopes_match",
    "task_epoch",
]
