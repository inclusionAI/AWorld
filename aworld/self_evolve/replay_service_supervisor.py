"""Parent-bound supervisor for untrusted replay service runtimes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


def supervise(command: Sequence[str], *, parent_pid: int | None = None) -> int:
    """Run ``command`` and tear it down when this supervisor loses its parent."""

    if not command:
        raise ValueError("replay service supervisor requires a command")
    expected_parent_pid = os.getppid() if parent_pid is None else parent_pid
    if expected_parent_pid <= 1:
        raise ValueError("replay service supervisor requires a live parent pid")
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, request_stop)
    process = subprocess.Popen(
        list(command),
        shell=False,
        stdin=subprocess.DEVNULL,
        start_new_session=os.name == "posix",
    )
    try:
        while True:
            return_code = process.poll()
            if return_code is not None:
                return int(return_code)
            if stopping or os.getppid() != expected_parent_pid:
                _terminate_process_group(process)
                return 128 + int(signal.SIGTERM)
            time.sleep(0.25)
    finally:
        _terminate_process_group(process)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parent_pid: int | None = None
    if arguments[:1] == ["--parent-pid"]:
        if len(arguments) < 2:
            raise ValueError("--parent-pid requires a value")
        parent_pid = int(arguments[1])
        arguments = arguments[2:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    return supervise(arguments, parent_pid=parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
