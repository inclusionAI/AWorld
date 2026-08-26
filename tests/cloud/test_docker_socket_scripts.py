from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
HELPER = ROOT / "scripts" / "aworld-cloud-docker.sh"
INIT = ROOT / "scripts" / "aworld-cloud-init.sh"


def _fake_docker(tmp_path: Path, script: str) -> Path:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    docker = bin_directory / "docker"
    docker.write_text(script, encoding="utf-8")
    docker.chmod(0o755)
    return bin_directory


def test_explicit_docker_socket_takes_precedence(tmp_path: Path) -> None:
    socket_path = "/daemon/explicit/docker.sock"
    bin_directory = _fake_docker(
        tmp_path,
        """#!/bin/sh
if [ "$1" = "run" ] && printf '%s' "$*" | grep -q "source=$TEST_DOCKER_SOCKET,target="; then
  exit 0
fi
exit 1
""",
    )
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "AWORLD_CLOUD_DOCKER_SOCKET=/does/not/exist.sock\n",
        encoding="utf-8",
    )
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
        env={
            **os.environ,
            "PATH": f"{bin_directory}:{os.environ['PATH']}",
            "AWORLD_CLOUD_DOCKER_SOCKET": socket_path,
            "TEST_DOCKER_SOCKET": socket_path,
        },
    )

    assert result.stdout.strip() == socket_path


def test_socket_is_discovered_from_active_docker_context(tmp_path: Path) -> None:
    socket_path = "/daemon/context/docker.sock"
    bin_directory = _fake_docker(
        tmp_path,
        """#!/bin/sh
if [ "$1 $2" = "context inspect" ]; then
  printf 'unix://%s\n' "$TEST_DOCKER_SOCKET"
  exit 0
fi
if [ "$1" = "run" ] && printf '%s' "$*" | grep -q "source=$TEST_DOCKER_SOCKET,target="; then
  exit 0
fi
exit 1
""",
    )
    environment_file = tmp_path / ".env"
    environment_file.write_text("AWORLD_CLOUD_DATA_DIR=/tmp/data\n")
    environment = {
        **os.environ,
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "TEST_DOCKER_SOCKET": socket_path,
    }
    environment.pop("AWORLD_CLOUD_DOCKER_SOCKET", None)
    environment.pop("AWORLD_CLOUD_DATA_DIR", None)
    environment.pop("DOCKER_HOST", None)
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

    assert result.stdout.strip() == socket_path


def test_socket_falls_back_to_docker_desktop_home_path(tmp_path: Path) -> None:
    socket_path = tmp_path / "home" / ".docker" / "run" / "docker.sock"
    bin_directory = _fake_docker(
        tmp_path,
        """#!/bin/sh
if [ "$1" = "run" ] && printf '%s' "$*" | grep -q "source=$TEST_DOCKER_SOCKET,target="; then
  exit 0
fi
exit 1
""",
    )
    environment_file = tmp_path / ".env"
    environment_file.write_text("AWORLD_CLOUD_DATA_DIR=/tmp/data\n")
    environment = {
        **os.environ,
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "TEST_DOCKER_SOCKET": str(socket_path),
    }
    environment.pop("AWORLD_CLOUD_DOCKER_SOCKET", None)
    environment.pop("AWORLD_CLOUD_DATA_DIR", None)
    environment.pop("DOCKER_HOST", None)
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

    assert result.stdout.strip() == str(socket_path)


def test_init_replaces_stale_persisted_socket_with_daemon_path(tmp_path: Path) -> None:
    stale_socket = "/Users/test/.colima/default/docker.sock"
    socket_path = "/var/run/docker.sock"
    bin_directory = _fake_docker(
        tmp_path,
        """#!/bin/sh
if [ "$1 $2" = "context inspect" ]; then
  printf 'unix://%s\n' "$TEST_STALE_DOCKER_SOCKET"
  exit 0
fi
if [ "$1" = "run" ]; then
  printf '%s' "$*" | grep -q "source=$TEST_DOCKER_SOCKET,target=" && exit 0
  exit 1
fi
if [ "$1 $2" = "compose --env-file" ]; then
  test "$AWORLD_CLOUD_DOCKER_SOCKET" = "$TEST_DOCKER_SOCKET"
  exit 0
fi
exit 1
""",
    )
    environment_file = tmp_path / "cloud.env"
    data_directory = tmp_path / "data"
    environment_file.write_text(
        f"AWORLD_CLOUD_DATA_DIR={data_directory}\n"
        f"AWORLD_CLOUD_DOCKER_SOCKET={stale_socket}\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "AWORLD_CLOUD_ENV_FILE": str(environment_file),
        "TEST_DOCKER_SOCKET": socket_path,
        "TEST_STALE_DOCKER_SOCKET": stale_socket,
    }
    environment.pop("AWORLD_CLOUD_DOCKER_SOCKET", None)
    environment.pop("AWORLD_CLOUD_DATA_DIR", None)
    environment.pop("DOCKER_HOST", None)
    result = subprocess.run(
        ["bash", str(INIT)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    persisted_environment = environment_file.read_text(encoding="utf-8")
    assert f"AWORLD_CLOUD_DOCKER_SOCKET={socket_path}" in persisted_environment
    assert stale_socket not in persisted_environment
    assert "Ignoring unusable persisted Docker socket" in result.stderr


def test_init_migrates_legacy_macos_data_directory_to_repo(tmp_path: Path) -> None:
    bin_directory = _fake_docker(
        tmp_path,
        """#!/bin/sh
if [ "$1" = "run" ]; then
  printf '%s' "$*" | grep -q 'source=/var/run/docker.sock,target=' && exit 0
  exit 1
fi
if [ "$1 $2" = "compose --env-file" ]; then
  exit 0
fi
exit 1
""",
    )
    uname = bin_directory / "uname"
    uname.write_text("#!/bin/sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
    uname.chmod(0o755)
    mkdir = bin_directory / "mkdir"
    mkdir.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mkdir.chmod(0o755)
    environment_file = tmp_path / "cloud.env"
    environment_file.write_text(
        "AWORLD_CLOUD_DATA_DIR=/var/tmp/aworld-cloud\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "AWORLD_CLOUD_ENV_FILE": str(environment_file),
    }
    environment.pop("AWORLD_CLOUD_DATA_DIR", None)
    environment.pop("AWORLD_CLOUD_DOCKER_SOCKET", None)
    environment.pop("DOCKER_HOST", None)

    result = subprocess.run(
        ["bash", str(INIT)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    expected = ROOT / ".aworld-cloud"
    persisted_environment = environment_file.read_text(encoding="utf-8")
    assert f"AWORLD_CLOUD_DATA_DIR={expected}" in persisted_environment
    assert "AWORLD_CLOUD_DATA_DIR=/var/tmp/aworld-cloud" not in persisted_environment
    assert "Migrated legacy macOS data directory" in result.stdout


@pytest.mark.parametrize(
    ("persisted_data_directory", "explicit_data_directory"),
    [
        ("/srv/aworld-cloud", None),
        ("/var/tmp/aworld-cloud", "/var/tmp/aworld-cloud"),
    ],
)
def test_init_preserves_explicit_data_directory_on_macos(
    tmp_path: Path,
    persisted_data_directory: str,
    explicit_data_directory: str | None,
) -> None:
    bin_directory = _fake_docker(
        tmp_path,
        """#!/bin/sh
if [ "$1" = "run" ]; then
  printf '%s' "$*" | grep -q 'source=/var/run/docker.sock,target=' && exit 0
  exit 1
fi
if [ "$1 $2" = "compose --env-file" ]; then
  exit 0
fi
exit 1
""",
    )
    uname = bin_directory / "uname"
    uname.write_text("#!/bin/sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
    uname.chmod(0o755)
    mkdir = bin_directory / "mkdir"
    mkdir.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mkdir.chmod(0o755)
    environment_file = tmp_path / "cloud.env"
    environment_file.write_text(
        f"AWORLD_CLOUD_DATA_DIR={persisted_data_directory}\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "AWORLD_CLOUD_ENV_FILE": str(environment_file),
    }
    if explicit_data_directory is not None:
        environment["AWORLD_CLOUD_DATA_DIR"] = explicit_data_directory
    else:
        environment.pop("AWORLD_CLOUD_DATA_DIR", None)
    environment.pop("AWORLD_CLOUD_DOCKER_SOCKET", None)
    environment.pop("DOCKER_HOST", None)

    result = subprocess.run(
        ["bash", str(INIT)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    expected = explicit_data_directory or persisted_data_directory
    assert f"AWORLD_CLOUD_DATA_DIR={expected}" in environment_file.read_text(
        encoding="utf-8"
    )
    assert "Migrated legacy macOS data directory" not in result.stdout
