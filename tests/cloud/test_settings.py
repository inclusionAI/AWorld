from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta
from pathlib import Path, PurePosixPath

import pytest

from aworld.cloud.errors import CloudError, CloudErrorCode
from aworld.cloud.settings import (
    CloudSettings,
    NetworkPolicy,
    ReferenceRepository,
    ResourceLimits,
    WorkspaceProfile,
)

IMAGE = "registry.example/codex@sha256:abc"


def _profile() -> WorkspaceProfile:
    return WorkspaceProfile(
        name="aworld-development",
        writable_repo_root=Path("/srv/aworld/workspaces"),
        runtime_image=IMAGE,
        references=(
            ReferenceRepository(
                name="gateway",
                host_path=Path("/srv/references/gateway"),
                container_path=PurePosixPath("/workspace/reference/gateway"),
            ),
        ),
        resources=ResourceLimits(
            cpus=2,
            memory_bytes=4 * 1024 * 1024 * 1024,
            pids=128,
            wall_clock_timeout=timedelta(minutes=30),
        ),
        network=NetworkPolicy(mode="bridge"),
    )


def _settings(tmp_path: Path) -> CloudSettings:
    profile = _profile()
    return CloudSettings(
        enabled=True,
        data_root=tmp_path,
        worker_id="cloud-worker-1",
        concurrency=2,
        lease_duration=timedelta(seconds=45),
        heartbeat_interval=timedelta(seconds=10),
        poll_interval=timedelta(milliseconds=500),
        allowed_profiles={profile.name: profile},
        allowed_images=frozenset({IMAGE}),
    )


def test_settings_cover_admin_owned_runtime_policy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    profile = settings.profile("aworld-development")

    assert settings.enabled is True
    assert settings.database_path == tmp_path / "cloud.sqlite3"
    assert settings.worker_id == "cloud-worker-1"
    assert settings.concurrency == 2
    assert settings.lease_duration == timedelta(seconds=45)
    assert profile.runtime_image in settings.allowed_images
    assert profile.resources.cpus == 2
    assert profile.network.mode == "bridge"
    assert profile.references[0].container_path == PurePosixPath(
        "/workspace/reference/gateway"
    )


def test_settings_and_profile_mapping_are_immutable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(FrozenInstanceError):
        settings.enabled = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        settings.allowed_profiles["other"] = _profile()  # type: ignore[index]


def test_profile_resolution_uses_a_stable_error_code(tmp_path: Path) -> None:
    with pytest.raises(CloudError) as raised:
        _settings(tmp_path).profile("client-supplied-profile")

    assert raised.value.code is CloudErrorCode.PROFILE_NOT_FOUND
    assert "client-supplied-profile" not in raised.value.message


def test_profile_image_must_be_on_admin_allow_list(tmp_path: Path) -> None:
    profile = _profile()

    with pytest.raises(ValueError, match="allowed_images"):
        CloudSettings(
            enabled=True,
            data_root=tmp_path,
            worker_id="cloud-worker-1",
            allowed_profiles={profile.name: profile},
            allowed_images=frozenset({"registry.example/other@sha256:def"}),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"concurrency": 0},
        {"lease_duration": timedelta(0)},
        {
            "lease_duration": timedelta(seconds=10),
            "heartbeat_interval": timedelta(seconds=10),
        },
    ],
)
def test_worker_timing_and_capacity_are_validated(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    profile = _profile()
    values = {
        "enabled": True,
        "data_root": tmp_path,
        "worker_id": "cloud-worker-1",
        "allowed_profiles": {profile.name: profile},
        "allowed_images": frozenset({IMAGE}),
        **overrides,
    }

    with pytest.raises(ValueError):
        CloudSettings(**values)  # type: ignore[arg-type]


def test_reference_paths_must_be_administrator_absolute_paths() -> None:
    with pytest.raises(ValueError, match="host_path"):
        ReferenceRepository(
            name="gateway",
            host_path=Path("relative/gateway"),
            container_path=PurePosixPath("/workspace/reference/gateway"),
        )
