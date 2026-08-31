"""Dependency-light immutable JSON values and canonical hashing.

The compiler contracts use these values instead of placing mutable ``dict`` or
``list`` objects inside frozen dataclasses.  Mapping insertion order is retained
for lossless request snapshots, while canonical hashes sort object keys and
preserve array order.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAlias, Union


JSONScalar: TypeAlias = None | bool | int | float | str
FrozenJSON: TypeAlias = Union[JSONScalar, tuple["FrozenJSON", ...], "FrozenMap"]


@dataclass(frozen=True, slots=True)
class FrozenMap(Mapping[str, FrozenJSON]):
    """An immutable, insertion-ordered mapping of JSON values."""

    _items: tuple[tuple[str, FrozenJSON], ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for key, value in self._items:
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            if key in seen:
                raise ValueError(f"duplicate JSON object key: {key}")
            if not _is_frozen_json(value):
                raise TypeError("FrozenMap values must already be deeply frozen")
            seen.add(key)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FrozenMap":
        return cls(tuple((key, freeze_json(item)) for key, item in value.items()))

    def __getitem__(self, key: str) -> FrozenJSON:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


def _is_frozen_json(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, FrozenMap):
        return True
    if isinstance(value, tuple):
        return all(_is_frozen_json(item) for item in value)
    return False


def freeze_json(value: Any) -> FrozenJSON:
    """Return a detached, deeply immutable JSON representation.

    Tuples are accepted as JSON arrays for compatibility with already-frozen
    values.  Unsupported runtime objects are rejected instead of being coerced
    with ``str()``, which could make request hashes non-deterministic.
    """

    if isinstance(value, Enum):
        return freeze_json(value.value)
    if isinstance(value, FrozenMap):
        return value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON does not support non-finite floats")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return FrozenMap.from_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def thaw_json(value: FrozenJSON) -> Any:
    """Return a detached mutable JSON value while preserving stored order."""

    if isinstance(value, FrozenMap):
        return {key: thaw_json(item) for key, item in value._items}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Return stable compact UTF-8 JSON bytes for a JSON-compatible value."""

    frozen = freeze_json(value)
    return json.dumps(
        thaw_json(frozen),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_hash(value: Any) -> str:
    """Return an algorithm-qualified hash of canonical JSON bytes."""

    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def redacted_shape_preview(value: FrozenJSON) -> str:
    """Describe value shape without exposing keys, text, paths, or scalars."""

    if isinstance(value, FrozenMap):
        return f"<object fields={len(value)}>"
    if isinstance(value, tuple):
        return f"<array items={len(value)}>"
    if isinstance(value, str):
        return f"<string chars={len(value)}>"
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "<boolean>"
    if isinstance(value, int):
        return "<integer>"
    return "<number>"


__all__ = [
    "FrozenJSON",
    "FrozenMap",
    "canonical_json_bytes",
    "canonical_json_hash",
    "freeze_json",
    "redacted_shape_preview",
    "thaw_json",
]
