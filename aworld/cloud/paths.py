"""Contained filesystem path construction for cloud service-owned data."""

from __future__ import annotations

import shutil
from pathlib import Path

from aworld.cloud.errors import CloudError, CloudErrorCode
from aworld.cloud.models import RunId, WorkspaceId
from aworld.cloud.settings import CloudSettings, WorkspaceProfile


def _safe_component(value: object, *, field_name: str) -> str:
    component = str(value)
    if (
        not component
        or component in {".", ".."}
        or Path(component).name != component
        or "/" in component
        or "\\" in component
    ):
        raise CloudError(
            CloudErrorCode.UNSAFE_MOUNT,
            f"{field_name} is not safe for path construction",
        )
    return component


def contained_child(root: Path, component: object, *, field_name: str) -> Path:
    """Resolve one child while rejecting traversal and symlink escapes."""

    safe_component = _safe_component(component, field_name=field_name)
    resolved_root = Path(root).resolve(strict=False)
    candidate = (resolved_root / safe_component).resolve(strict=False)
    if candidate.parent != resolved_root:
        raise CloudError(
            CloudErrorCode.UNSAFE_MOUNT,
            f"{field_name} escapes its administrator-configured root",
        )
    return candidate


class CloudPaths:
    """Service-owned paths derived only from settings and generated identifiers."""

    def __init__(self, settings: CloudSettings) -> None:
        self._data_root = settings.data_root.resolve(strict=False)

    def writable_workspace(
        self,
        profile: WorkspaceProfile,
        workspace_id: WorkspaceId,
    ) -> Path:
        return contained_child(
            profile.writable_repo_root,
            workspace_id,
            field_name="workspace_id",
        )

    def codex_home(self, workspace_id: WorkspaceId) -> Path:
        workspace_root = contained_child(
            self._data_root / "workspaces",
            workspace_id,
            field_name="workspace_id",
        )
        return workspace_root / "codex-home"

    def run_output(self, run_id: RunId) -> Path:
        return contained_child(
            self._data_root / "runs",
            run_id,
            field_name="run_id",
        )

    def provision_workspace(self, writable_repo: Path, codex_home: Path) -> None:
        writable_repo.mkdir(parents=True, exist_ok=True)
        codex_home.mkdir(parents=True, exist_ok=True)

    def provision_run_output(self, run_id: RunId) -> Path:
        output = self.run_output(run_id)
        output.mkdir(parents=True, exist_ok=True)
        return output

    @staticmethod
    def release_writable_workspace(
        profile: WorkspaceProfile,
        workspace_id: WorkspaceId,
        writable_repo: Path,
    ) -> None:
        expected = contained_child(
            profile.writable_repo_root,
            workspace_id,
            field_name="workspace_id",
        )
        if writable_repo.resolve(strict=False) != expected:
            raise CloudError(
                CloudErrorCode.UNSAFE_MOUNT,
                "workspace path does not match its administrator profile",
            )
        if expected.exists():
            shutil.rmtree(expected)
