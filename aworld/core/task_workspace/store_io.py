"""Bounded, durable filesystem primitives for the scoped task workspace store."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


class StoreError(RuntimeError):
    pass


class StoreIntegrityError(StoreError):
    pass


class StoreConflictError(StoreError):
    pass


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".store-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path: Path, value) -> None:
    atomic_bytes(path, canonical(value))


def read_json(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
        raise StoreIntegrityError("store record must be a bounded regular file")
    with path.open("rb") as stream:
        return json.loads(stream.read(16 * 1024 * 1024 + 1))


def identity(path: Path):
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(value.st_mode):
        raise StoreIntegrityError(f"only regular files are supported: {path}")
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def capture(path: Path, blobs: Path, limit: int) -> dict:
    """Capture one stable regular file; never follow a source symlink."""
    before = identity(path)
    if before is None:
        raise FileNotFoundError(path)
    if before[2] > limit:
        raise StoreError(f"file exceeds snapshot byte allowance: {path}")
    source = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    fd, name = tempfile.mkstemp(prefix=".blob-", dir=blobs)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(source, "rb") as incoming, os.fdopen(fd, "wb") as outgoing:
            info = os.fstat(incoming.fileno())
            if (info.st_dev, info.st_ino) != before[:2]:
                raise StoreConflictError("source changed before snapshot")
            while chunk := incoming.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise StoreError("snapshot byte allowance exceeded")
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if identity(path) != before or size != before[2]:
            raise StoreConflictError(f"source changed during snapshot: {path}")
        entry = {"sha256": digest.hexdigest(), "size": size,
                 "mode": stat.S_IMODE(info.st_mode)}
        destination = blobs / entry["sha256"]
        if destination.exists():
            verify_blob(destination, entry)
        else:
            os.chmod(name, 0o400)
            os.replace(name, destination)
            sync_directory(blobs)
        return entry
    finally:
        Path(name).unlink(missing_ok=True)


def verify_blob(path: Path, expected: dict) -> None:
    actual = identity(path)
    if actual is None or actual[2] != expected["size"]:
        raise StoreIntegrityError("snapshot size/availability changed")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected["sha256"] or identity(path) != actual:
        raise StoreIntegrityError("snapshot content changed")


@contextmanager
def locked(path: Path):
    try:
        import fcntl
    except ImportError as exc:
        raise StoreError("transactional store currently requires POSIX file locking") from exc
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
