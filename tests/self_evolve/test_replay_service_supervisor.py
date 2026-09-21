from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from aworld.self_evolve import replay_service_supervisor


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_replay_service_supervisor_stops_runtime_when_parent_dies(
    tmp_path: Path,
) -> None:
    runtime_pid_path = tmp_path / "runtime.pid"
    runtime_path = tmp_path / "runtime.py"
    runtime_path.write_text(
        "import os, pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    launcher_path = tmp_path / "launcher.py"
    launcher_path.write_text(
        "import os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-I', sys.argv[1], "
        "'--parent-pid', str(os.getpid()), '--', "
        "sys.executable, sys.argv[2], sys.argv[3]])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    launcher = subprocess.Popen(
        [
            sys.executable,
            str(launcher_path),
            str(Path(replay_service_supervisor.__file__).resolve()),
            str(runtime_path),
            str(runtime_pid_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert launcher.stdout is not None
    supervisor_pid = int(launcher.stdout.readline().strip())
    deadline = time.monotonic() + 5.0
    while not runtime_pid_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runtime_pid_path.is_file()
    runtime_pid = int(runtime_pid_path.read_text(encoding="utf-8"))

    os.kill(launcher.pid, signal.SIGKILL)
    launcher.wait(timeout=5.0)
    deadline = time.monotonic() + 5.0
    while (
        (_pid_exists(supervisor_pid) or _pid_exists(runtime_pid))
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)

    assert not _pid_exists(runtime_pid)
    assert not _pid_exists(supervisor_pid)
