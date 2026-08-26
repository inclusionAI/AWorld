"""Environment-backed composition helpers for the Cloud MVP processes."""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path, PurePosixPath

from aworld.cloud.settings import (
    CloudSettings,
    NetworkPolicy,
    ResourceLimits,
    WorkspaceProfile,
)


def _boolean(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _positive_float(value: str, *, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def cloud_settings_from_env(
    environment: Mapping[str, str] | None = None,
) -> CloudSettings:
    """Create the validated SQLite/local-Docker MVP settings."""

    values = os.environ if environment is None else environment
    data_root = Path(values.get("AWORLD_CLOUD_DATA_DIR", "/var/lib/aworld-cloud"))
    profile_name = values.get("AWORLD_CLOUD_PROFILE", "local-docker")
    runtime_image = values.get("AWORLD_CLOUD_RUNTIME_IMAGE", "aworld-cloud-mvp:local")
    lease_seconds = _positive_float(
        values.get("AWORLD_CLOUD_LEASE_SECONDS", "30"),
        name="AWORLD_CLOUD_LEASE_SECONDS",
    )
    heartbeat_seconds = _positive_float(
        values.get("AWORLD_CLOUD_HEARTBEAT_SECONDS", "10"),
        name="AWORLD_CLOUD_HEARTBEAT_SECONDS",
    )
    profile = WorkspaceProfile(
        name=profile_name,
        writable_repo_root=data_root / "workspace-repos",
        runtime_image=runtime_image,
        workdir=PurePosixPath(
            values.get("AWORLD_CLOUD_WORKDIR", "/workspace/aworld")
        ),
        runtime_user=values.get("AWORLD_CLOUD_RUNTIME_USER", "root"),
        resources=ResourceLimits(
            cpus=_positive_float(
                values.get("AWORLD_CLOUD_RUN_CPUS", "2"),
                name="AWORLD_CLOUD_RUN_CPUS",
            ),
            memory_bytes=_positive_int(
                values.get("AWORLD_CLOUD_RUN_MEMORY_BYTES", str(4 * 1024**3)),
                name="AWORLD_CLOUD_RUN_MEMORY_BYTES",
            ),
            pids=_positive_int(
                values.get("AWORLD_CLOUD_RUN_PIDS", "512"),
                name="AWORLD_CLOUD_RUN_PIDS",
            ),
            wall_clock_timeout=timedelta(
                seconds=_positive_float(
                    values.get("AWORLD_CLOUD_RUN_TIMEOUT_SECONDS", "1800"),
                    name="AWORLD_CLOUD_RUN_TIMEOUT_SECONDS",
                )
            ),
        ),
        network=NetworkPolicy(
            mode=values.get("AWORLD_CLOUD_RUN_NETWORK", "bridge")
        ),
    )
    return CloudSettings(
        enabled=_boolean(
            values.get("AWORLD_CLOUD_ENABLED", "true"),
            name="AWORLD_CLOUD_ENABLED",
        ),
        data_root=data_root,
        database_path=data_root / "cloud.sqlite3",
        worker_id=values.get(
            "AWORLD_CLOUD_WORKER_ID",
            f"{socket.gethostname()}-{os.getpid()}",
        ),
        concurrency=_positive_int(
            values.get("AWORLD_CLOUD_CONCURRENCY", "1"),
            name="AWORLD_CLOUD_CONCURRENCY",
        ),
        lease_duration=timedelta(seconds=lease_seconds),
        heartbeat_interval=timedelta(seconds=heartbeat_seconds),
        poll_interval=timedelta(
            seconds=_positive_float(
                values.get("AWORLD_CLOUD_POLL_SECONDS", "1"),
                name="AWORLD_CLOUD_POLL_SECONDS",
            )
        ),
        allowed_profiles={profile.name: profile},
        allowed_images=frozenset({runtime_image}),
    )
