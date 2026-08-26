"""Stable domain errors exposed by AWorld Cloud boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import Any


class CloudErrorCode(str, Enum):
    """Machine-readable error codes that remain stable across transports."""

    WORKSPACE_NOT_FOUND = "workspace_not_found"
    WORKSPACE_BUSY = "workspace_busy"
    RUN_NOT_FOUND = "run_not_found"
    FILE_NOT_FOUND = "file_not_found"
    INVALID_TRANSITION = "invalid_transition"
    REVISION_CONFLICT = "revision_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    PROFILE_NOT_FOUND = "profile_not_found"
    IMAGE_NOT_ALLOWED = "image_not_allowed"
    UNSAFE_MOUNT = "unsafe_mount"
    INVALID_REQUEST = "invalid_request"
    REPOSITORY_UNAVAILABLE = "repository_unavailable"
    EXECUTOR_UNAVAILABLE = "executor_unavailable"
    EXECUTOR_FAILED = "executor_failed"
    WORKER_LEASE_EXPIRED = "worker_lease_expired"
    WORKSPACE_PROVISION_FAILED = "workspace_provision_failed"


class CloudError(Exception):
    """A typed boundary failure with a stable code and non-secret details."""

    def __init__(
        self,
        code: CloudErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = MappingProxyType(dict(details or {}))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code.value!r}, message={self.message!r})"
        )


class InvalidTransitionError(CloudError):
    """Raised when a state-machine transition is not legal."""

    def __init__(self, resource: str, current: str, target: str) -> None:
        super().__init__(
            CloudErrorCode.INVALID_TRANSITION,
            f"cannot transition {resource} from {current} to {target}",
            details={
                "resource": resource,
                "current_state": current,
                "target_state": target,
            },
        )


class WorkspaceBusyError(CloudError):
    """Raised when another run already owns a workspace mutation slot."""

    def __init__(self, workspace_id: str) -> None:
        super().__init__(
            CloudErrorCode.WORKSPACE_BUSY,
            "workspace already has an active run",
            details={"workspace_id": workspace_id},
        )
