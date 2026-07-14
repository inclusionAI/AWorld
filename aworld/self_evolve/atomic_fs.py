from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


class AtomicFilesystemError(RuntimeError):
    """Raised when the host cannot atomically exchange two filesystem paths."""


def atomic_exchange_paths(left: str | Path, right: str | Path) -> None:
    """Atomically exchange two existing paths on supported POSIX hosts."""

    left_path = Path(left).absolute()
    right_path = Path(right).absolute()
    if not left_path.exists() or not right_path.exists():
        raise AtomicFilesystemError("atomic exchange requires two existing paths")
    if left_path.stat().st_dev != right_path.stat().st_dev:
        raise AtomicFilesystemError("atomic exchange paths must share a filesystem")

    libc = ctypes.CDLL(None, use_errno=True)
    left_bytes = os.fsencode(left_path)
    right_bytes = os.fsencode(right_path)
    result: int
    if sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise AtomicFilesystemError("atomic directory exchange is unavailable")
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(left_bytes, right_bytes, 0x00000002)
    elif sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise AtomicFilesystemError("atomic directory exchange is unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, left_bytes, -100, right_bytes, 0x00000002)
    else:
        raise AtomicFilesystemError(
            f"atomic directory exchange is unsupported on {sys.platform}"
        )
    if result != 0:
        error_number = ctypes.get_errno()
        raise AtomicFilesystemError(
            f"atomic directory exchange failed: {os.strerror(error_number)}"
        )
