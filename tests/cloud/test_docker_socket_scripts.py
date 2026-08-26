from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
HELPER = ROOT / "scripts" / "aworld-cloud-docker.sh"
INIT = ROOT / "scripts" / "aworld-cloud-init.sh"


def _unix_socket(path: Path) -> socket.socket:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    return listener


def test_explicit_docker_socket_takes_precedence(tmp_path: Path) -> None:
    socket_path = tmp_path / "explicit.sock"
    listener = _unix_socket(socket_path)
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "AWORLD_CLOUD_DOCKER_SOCKET=/does/not/exist.sock\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; aworld_cloud_detect_docker_socket "$2"',
                "bash",
                str(HELPER),
                str(environment_file),
            ],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "AWORLD_CLOUD_DOCKER_SOCKET": str(socket_path)},
        )
    finally:
        listener.close()

    assert result.stdout.strip() == str(socket_path)


def test_socket_is_discovered_from_active_docker_context(tmp_path: Path) -> None:
    socket_path = tmp_path / "context.sock"
    listener = _unix_socket(socket_path)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    docker = bin_directory / "docker"
    docker.write_text(
        f"#!/bin/sh\nprintf '%s\\n' 'unix://{socket_path}'\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment_file = tmp_path / ".env"
    environment_file.write_text("AWORLD_CLOUD_DATA_DIR=/tmp/data\n")
    environment = {
        **os.environ,
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
    }
    environment.pop("AWORLD_CLOUD_DOCKER_SOCKET", None)
    environment.pop("DOCKER_HOST", None)
    try:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; aworld_cloud_detect_docker_socket "$2"',
                "bash",
                str(HELPER),
                str(environment_file),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    finally:
        listener.close()

    assert result.stdout.strip() == str(socket_path)


def test_socket_falls_back_to_docker_desktop_home_path(tmp_path: Path) -> None:
    socket_path = tmp_path / "home" / ".docker" / "run" / "docker.sock"
    socket_path.parent.mkdir(parents=True)
    listener = _unix_socket(socket_path)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    docker = bin_directory / "docker"
    docker.write_text(
        "#!/bin/sh\nexit 1\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment_file = tmp_path / ".env"
    environment_file.write_text("AWORLD_CLOUD_DATA_DIR=/tmp/data\n")
    environment = {
        **os.environ,
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
    }
    environment.pop("AWORLD_CLOUD_DOCKER_SOCKET", None)
    environment.pop("DOCKER_HOST", None)
    try:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; aworld_cloud_detect_docker_socket "$2"',
                "bash",
                str(HELPER),
                str(environment_file),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    finally:
        listener.close()

    assert result.stdout.strip() == str(socket_path)


def test_init_persists_detected_socket_for_direct_compose_use(tmp_path: Path) -> None:
    socket_path = tmp_path / "desktop.sock"
    listener = _unix_socket(socket_path)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    docker = bin_directory / "docker"
    docker.write_text(
        """#!/bin/sh
if [ "$1 $2" = "context inspect" ]; then
  printf '%s\n' "$TEST_DOCKER_SOCKET_ENDPOINT"
  exit 0
fi
if [ "$1 $2" = "compose --env-file" ]; then
  test "$AWORLD_CLOUD_DOCKER_SOCKET" = "$TEST_DOCKER_SOCKET"
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment_file = tmp_path / "cloud.env"
    data_directory = tmp_path / "data"
    environment_file.write_text(
        f"AWORLD_CLOUD_DATA_DIR={data_directory}\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "AWORLD_CLOUD_ENV_FILE": str(environment_file),
        "TEST_DOCKER_SOCKET": str(socket_path),
        "TEST_DOCKER_SOCKET_ENDPOINT": f"unix://{socket_path}",
    }
    environment.pop("AWORLD_CLOUD_DOCKER_SOCKET", None)
    environment.pop("DOCKER_HOST", None)
    try:
        subprocess.run(
            ["bash", str(INIT)],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    finally:
        listener.close()

    assert (
        f"AWORLD_CLOUD_DOCKER_SOCKET={socket_path}"
        in environment_file.read_text(encoding="utf-8")
    )
