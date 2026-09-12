from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest

from aworld.self_evolve import fixture_service


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="parent SIGKILL lifecycle requires POSIX process semantics",
)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_fixture(read_fixture: Callable[[], bytes]) -> bytes:
    deadline = time.monotonic() + 5.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return read_fixture()
        except (ConnectionError, OSError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise AssertionError("fixture service did not become ready") from last_error


def _wait_for_exit(pid: int) -> None:
    deadline = time.monotonic() + 5.0
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_exists(pid)


def _launch_parent_bound_fixture(
    tmp_path: Path,
    *,
    transport: str,
    port: int,
    fixture_path: Path,
) -> tuple[subprocess.Popen[str], int]:
    launcher_path = tmp_path / f"{transport}_launcher.py"
    launcher_path.write_text(
        "import os, subprocess, sys, time\n"
        "child = subprocess.Popen(["
        "sys.executable, '-I', sys.argv[1], "
        "'--port', sys.argv[2], "
        "'--transport', sys.argv[3], "
        "'--fixture', sys.argv[4], "
        "'--parent-pid', str(os.getpid())])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    launcher = subprocess.Popen(
        [
            sys.executable,
            str(launcher_path),
            str(Path(fixture_service.__file__).resolve()),
            str(port),
            transport,
            str(fixture_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert launcher.stdout is not None
    fixture_pid = int(launcher.stdout.readline().strip())
    return launcher, fixture_pid


def _http_get(port: int) -> bytes:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/fixture",
        timeout=0.25,
    ) as response:
        assert response.status == 200
        return response.read()


def _tcp_get(port: int) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=0.25) as client:
        client.sendall(b"fixture request")
        return client.recv(1024 * 1024)


@pytest.mark.parametrize(
    ("transport", "reader"),
    (
        ("http_fixture", _http_get),
        ("tcp_fixture", _tcp_get),
    ),
)
def test_fixture_serves_and_exits_when_parent_is_sigkilled(
    tmp_path: Path,
    transport: str,
    reader: Callable[[int], bytes],
) -> None:
    payload = f"payload from {transport}".encode()
    fixture_path = tmp_path / f"{transport}.fixture"
    fixture_path.write_bytes(payload)
    port = _reserve_loopback_port()
    launcher, fixture_pid = _launch_parent_bound_fixture(
        tmp_path,
        transport=transport,
        port=port,
        fixture_path=fixture_path,
    )
    try:
        assert _wait_for_fixture(lambda: reader(port)) == payload

        os.kill(launcher.pid, signal.SIGKILL)
        launcher.wait(timeout=5.0)
        _wait_for_exit(fixture_pid)
    finally:
        if launcher.poll() is None:
            launcher.kill()
            launcher.wait(timeout=5.0)
        if _pid_exists(fixture_pid):
            os.kill(fixture_pid, signal.SIGKILL)
