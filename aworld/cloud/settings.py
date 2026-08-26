"""Administrator-owned, transport-independent AWorld Cloud settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from aworld.cloud.errors import CloudError, CloudErrorCode


@dataclass(frozen=True)
class ResourceLimits:
    """Per-run limits selected by an administrator, never by a run request."""

    cpus: float = 1.0
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    pids: int = 256
    wall_clock_timeout: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if self.cpus <= 0:
            raise ValueError("cpus must be positive")
        if self.memory_bytes <= 0:
            raise ValueError("memory_bytes must be positive")
        if self.pids <= 0:
            raise ValueError("pids must be positive")
        if self.wall_clock_timeout <= timedelta(0):
            raise ValueError("wall_clock_timeout must be positive")


@dataclass(frozen=True)
class NetworkPolicy:
    """Server-selected container network policy."""

    mode: str = "none"

    def __post_init__(self) -> None:
        if not self.mode.strip() or any(character.isspace() for character in self.mode):
            raise ValueError("network mode must be a non-empty token")


@dataclass(frozen=True)
class ReferenceRepository:
    """One administrator-configured read-only reference repository."""

    name: str
    host_path: Path
    container_path: PurePosixPath

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("reference repository name must not be empty")
        object.__setattr__(self, "host_path", Path(self.host_path))
        object.__setattr__(self, "container_path", PurePosixPath(self.container_path))
        if not self.host_path.is_absolute():
            raise ValueError("reference host_path must be absolute")
        if not self.container_path.is_absolute():
            raise ValueError("reference container_path must be absolute")


@dataclass(frozen=True)
class WorkspaceProfile:
    """Complete server-owned policy from which workspace mounts are derived."""

    name: str
    writable_repo_root: Path
    runtime_image: str
    references: tuple[ReferenceRepository, ...] = ()
    workdir: PurePosixPath = field(
        default_factory=lambda: PurePosixPath("/workspace/aworld")
    )
    runtime_user: str = "node"
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    network: NetworkPolicy = field(default_factory=NetworkPolicy)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("profile name must not be empty")
        if not self.runtime_image.strip():
            raise ValueError("runtime_image must not be empty")
        if not self.runtime_user.strip():
            raise ValueError("runtime_user must not be empty")
        object.__setattr__(self, "writable_repo_root", Path(self.writable_repo_root))
        object.__setattr__(self, "workdir", PurePosixPath(self.workdir))
        object.__setattr__(self, "references", tuple(self.references))
        if not self.writable_repo_root.is_absolute():
            raise ValueError("writable_repo_root must be absolute")
        if not self.workdir.is_absolute():
            raise ValueError("workdir must be absolute")
        targets = [reference.container_path for reference in self.references]
        if len(targets) != len(set(targets)):
            raise ValueError("reference container paths must be unique")


@dataclass(frozen=True)
class CloudSettings:
    """Validated settings injected into cloud services by an administrator."""

    enabled: bool
    data_root: Path
    worker_id: str
    allowed_profiles: Mapping[str, WorkspaceProfile]
    allowed_images: frozenset[str]
    database_path: Path | None = None
    concurrency: int = 1
    lease_duration: timedelta = timedelta(seconds=30)
    heartbeat_interval: timedelta = timedelta(seconds=10)
    poll_interval: timedelta = timedelta(seconds=1)

    def __post_init__(self) -> None:
        data_root = Path(self.data_root)
        if not data_root.is_absolute():
            raise ValueError("data_root must be absolute")
        database_path = (
            Path(self.database_path)
            if self.database_path
            else data_root / "cloud.sqlite3"
        )
        if not database_path.is_absolute():
            raise ValueError("database_path must be absolute")
        if not self.worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if self.concurrency < 1:
            raise ValueError("concurrency must be positive")
        if self.lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if self.heartbeat_interval <= timedelta(0):
            raise ValueError("heartbeat_interval must be positive")
        if self.poll_interval <= timedelta(0):
            raise ValueError("poll_interval must be positive")
        if self.heartbeat_interval >= self.lease_duration:
            raise ValueError("heartbeat_interval must be shorter than lease_duration")

        profiles = dict(self.allowed_profiles)
        images = frozenset(self.allowed_images)
        for name, profile in profiles.items():
            if name != profile.name:
                raise ValueError("allowed profile keys must match profile names")
            if profile.runtime_image not in images:
                raise ValueError(
                    f"profile {name!r} uses an image outside allowed_images"
                )

        object.__setattr__(self, "data_root", data_root)
        object.__setattr__(self, "database_path", database_path)
        object.__setattr__(self, "allowed_profiles", MappingProxyType(profiles))
        object.__setattr__(self, "allowed_images", images)

    def profile(self, name: str) -> WorkspaceProfile:
        """Resolve an administrator-defined profile by exact name."""

        try:
            return self.allowed_profiles[name]
        except KeyError as exc:
            raise CloudError(
                CloudErrorCode.PROFILE_NOT_FOUND,
                "workspace profile is not configured",
                details={"profile_name": name},
            ) from exc
