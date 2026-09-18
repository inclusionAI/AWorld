"""Pluggable format-based input groups, including SQLite's committed WAL view."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Protocol

from .store_io import StoreConflictError, StoreIntegrityError, capture, identity


class InputGroupStrategy(Protocol):
    name: str

    def matches(self, path: Path) -> bool: ...
    def members(self, path: Path) -> list[Path]: ...
    def finalize(self, path: Path, entries: dict, blobs: Path, limit: int) -> dict: ...


@dataclass(frozen=True)
class RegularFileStrategy:
    name: str = "regular-file/v1"

    def matches(self, path: Path) -> bool:
        return True

    def members(self, path: Path) -> list[Path]:
        return [path]

    def finalize(self, path: Path, entries: dict, blobs: Path, limit: int) -> dict:
        return {"strategy": self.name, "anchor": str(path), "members": list(entries)}


@dataclass(frozen=True)
class SQLiteGroupStrategy:
    """Stable raw group + SQLite backup on a private copy, never on source.

    Writers changing any member or adding/removing a sidecar during capture
    cause refusal. SQLite recovery/backup validates the committed view of the
    captured bytes. This does not promise a live-database transaction snapshot;
    a source writer must be quiescent for the bounded capture operation.
    """
    name: str = "sqlite-stable-group/v1"

    def matches(self, path: Path) -> bool:
        identity(path)
        with path.open("rb") as stream:
            return stream.read(16) == b"SQLite format 3\0"

    def members(self, path: Path) -> list[Path]:
        return [path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm")
                        if Path(str(path) + suffix).exists()
                        or Path(str(path) + suffix).is_symlink())]

    def finalize(self, path: Path, entries: dict, blobs: Path, limit: int) -> dict:
        with tempfile.TemporaryDirectory(prefix="sqlite-input-", dir=blobs.parent) as tmp:
            root = Path(tmp)
            copied = root / "captured.sqlite"
            for original, entry in entries.items():
                suffix = original[len(str(path)):]
                destination = Path(str(copied) + suffix)
                shutil.copyfile(blobs / entry["sha256"], destination)
            normalized = root / "committed.sqlite"
            try:
                with closing(sqlite3.connect(copied, timeout=0)) as source:
                    if source.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise StoreIntegrityError("captured SQLite input failed integrity validation")
                    with closing(sqlite3.connect(normalized)) as target:
                        source.backup(target)
                        if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                            raise StoreIntegrityError("SQLite committed snapshot is invalid")
            except sqlite3.Error as exc:
                raise StoreIntegrityError("SQLite input group is incomplete or corrupt") from exc
            recovered = capture(normalized, blobs, limit)
        return {"strategy": self.name, "anchor": str(path), "members": list(entries),
                "working_copy": recovered, "protocol": "stable-raw-group/private-sqlite-backup",
                "derived_from": [{"path": p, "sha256": e["sha256"], "start": 0,
                                  "end": e["size"]} for p, e in entries.items()]}


def capture_group(path: Path, strategy: InputGroupStrategy, blobs: Path, limit: int):
    members = strategy.members(path)
    before = {str(p): identity(p) for p in members}
    entries = {}
    remaining = limit
    for member in members:
        entry = capture(member, blobs, remaining)
        entries[str(member)] = entry
        remaining -= entry["size"]
    if strategy.members(path) != members or any(identity(Path(p)) != v for p, v in before.items()):
        raise StoreConflictError("input group changed during capture; stop its writer and retry")
    group = strategy.finalize(path, entries, blobs, limit)
    if strategy.members(path) != members or any(identity(Path(p)) != v for p, v in before.items()):
        raise StoreConflictError("input group changed during validation; stop its writer and retry")
    return entries, group
