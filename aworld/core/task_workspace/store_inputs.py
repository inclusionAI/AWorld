"""Pluggable format-based input groups, including SQLite's committed WAL view."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import struct
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
        return [path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")
                        if Path(str(path) + suffix).exists()
                        or Path(str(path) + suffix).is_symlink())]

    def finalize(self, path: Path, entries: dict, blobs: Path, limit: int) -> dict:
        wal = entries.get(str(path) + "-wal")
        wal_validation = _validate_wal(blobs / wal["sha256"]) if wal else None
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
                "wal_validation": wal_validation,
                "derived_from": [{"path": p, "sha256": e["sha256"], "start": 0,
                                  "end": e["size"]} for p, e in entries.items()]}


def _validate_wal(path: Path) -> dict:
    """Do not silently accept SQLite discarding checksum-corrupted WAL pages."""
    def checksum(data, endian, value):
        words = struct.unpack(endian + str(len(data) // 4) + "I", data)
        first, second = value
        for index in range(0, len(words), 2):
            first = (first + words[index] + second) & 0xffffffff
            second = (second + words[index + 1] + first) & 0xffffffff
        return first, second
    with path.open("rb") as stream:
        header = stream.read(32)
        if not header:
            return {"frames": 0, "commits": 0}
        if len(header) != 32:
            raise StoreIntegrityError("truncated SQLite WAL header")
        magic, version, page_size, _, salt1, salt2, check1, check2 = struct.unpack(">8I", header)
        if (magic not in (0x377f0682, 0x377f0683) or version != 3007000
                or not 512 <= page_size <= 65536 or page_size & (page_size - 1)):
            raise StoreIntegrityError("unsupported/corrupt SQLite WAL header")
        endian = ">" if magic & 1 else "<"
        value = checksum(header[:24], endian, (0, 0))
        if value != (check1, check2):
            raise StoreIntegrityError("SQLite WAL header checksum failed")
        frames = commits = 0
        while frame := stream.read(24):
            if len(frame) != 24:
                raise StoreIntegrityError("truncated SQLite WAL frame")
            page, commit, first_salt, second_salt, check1, check2 = struct.unpack(">6I", frame)
            if (first_salt, second_salt) != (salt1, salt2):
                # A reset WAL may retain old-epoch tail allocation; SQLite
                # ignores those frames. Current-epoch checksum failures reject.
                break
            data = stream.read(page_size)
            if len(data) != page_size or not page:
                raise StoreIntegrityError("incomplete SQLite WAL page")
            value = checksum(data, endian, checksum(frame[:8], endian, value))
            if value != (check1, check2):
                raise StoreIntegrityError("SQLite WAL frame checksum failed")
            frames += 1
            commits += int(commit > 0)
    return {"frames": frames, "commits": commits}


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
