"""Transport-independent AWorld Cloud workspace and run use cases."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from aworld.cloud.errors import CloudError, CloudErrorCode, WorkspaceBusyError
from aworld.cloud.models import (
    ACTIVE_RUN_STATES,
    MountAccessMode,
    Run,
    RunId,
    RunState,
    Workspace,
    WorkspaceId,
    WorkspaceMount,
    WorkspaceState,
    create_retry_run,
    transition_workspace,
    utc_now,
)
from aworld.cloud.paths import CloudPaths
from aworld.cloud.repository import CloudRepository, Page
from aworld.cloud.settings import CloudSettings, WorkspaceProfile

IdFactory = Callable[[], str]
Clock = Callable[[], datetime]


def _default_id() -> str:
    return uuid.uuid4().hex


def _fingerprint(kind: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"kind": kind, "payload": payload, "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_text(value: str, *, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise CloudError(
            CloudErrorCode.INVALID_REQUEST,
            f"{field_name} must not be empty",
        )
    return stripped


@dataclass(frozen=True)
class WorkspaceInspection:
    workspace: Workspace
    active_run_id: RunId | None
    codex_config_present: bool
    codex_auth_present: bool


class CloudService:
    """Lifecycle boundary shared by future HTTP and CLI integrations."""

    def __init__(
        self,
        repository: CloudRepository,
        settings: CloudSettings,
        *,
        id_factory: IdFactory = _default_id,
        clock: Clock = utc_now,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._id_factory = id_factory
        self._clock = clock
        self._paths = CloudPaths(settings)

    def _ensure_enabled(self) -> None:
        if not self._settings.enabled:
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "AWorld Cloud is disabled",
            )

    def _profile(self, name: str) -> WorkspaceProfile:
        self._ensure_enabled()
        return self._settings.profile(name)

    @staticmethod
    def _workspace_mounts(
        profile: WorkspaceProfile,
        writable_repo_path: Path,
        codex_home_path: Path,
    ) -> tuple[WorkspaceMount, ...]:
        mounts = [
            WorkspaceMount(
                host_path=writable_repo_path,
                container_path=PurePosixPath("/workspace/aworld"),
                access_mode=MountAccessMode.READ_WRITE,
            )
        ]
        mounts.extend(
            WorkspaceMount(
                host_path=reference.host_path,
                container_path=reference.container_path,
                access_mode=MountAccessMode.READ_ONLY,
            )
            for reference in profile.references
        )
        mounts.append(
            WorkspaceMount(
                host_path=codex_home_path,
                container_path=PurePosixPath("/home/node/.codex"),
                access_mode=MountAccessMode.READ_WRITE,
            )
        )
        targets = [mount.container_path for mount in mounts]
        if len(targets) != len(set(targets)):
            raise CloudError(
                CloudErrorCode.UNSAFE_MOUNT,
                "workspace profile contains duplicate container mount targets",
            )
        return tuple(mounts)

    async def create_workspace(
        self,
        *,
        name: str,
        profile_name: str,
        idempotency_key: str,
    ) -> WorkspaceInspection:
        name = _require_text(name, field_name="name")
        idempotency_key = _require_text(
            idempotency_key,
            field_name="idempotency_key",
        )
        profile = self._profile(profile_name)
        workspace_id = WorkspaceId(f"workspace-{self._id_factory()}")
        writable_repo = self._paths.writable_workspace(profile, workspace_id)
        codex_home = self._paths.codex_home(workspace_id)
        created_at = self._clock()
        candidate = Workspace(
            id=workspace_id,
            name=name,
            profile_name=profile.name,
            state=WorkspaceState.CREATING,
            revision=0,
            runtime_image=profile.runtime_image,
            writable_repo_path=writable_repo,
            codex_home_path=codex_home,
            workdir=profile.workdir,
            created_at=created_at,
            updated_at=created_at,
            mounts=self._workspace_mounts(profile, writable_repo, codex_home),
        )
        fingerprint = _fingerprint(
            "workspace.create",
            {"name": name, "profile_name": profile.name},
        )
        workspace = await self._repository.create_workspace(
            candidate,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        if workspace.state is WorkspaceState.CREATING:
            try:
                self._paths.provision_workspace(
                    workspace.writable_repo_path,
                    workspace.codex_home_path,
                )
                ready = transition_workspace(
                    workspace,
                    WorkspaceState.READY,
                    at=self._clock(),
                )
                workspace = await self._repository.update_workspace(
                    ready,
                    expected_revision=workspace.revision,
                    expected_state=WorkspaceState.CREATING,
                )
            except CloudError as exc:
                if exc.code is CloudErrorCode.REVISION_CONFLICT:
                    current = await self._repository.get_workspace(workspace.id)
                    if current is not None and current.state is WorkspaceState.READY:
                        workspace = current
                    else:
                        raise
                else:
                    raise
            except OSError as exc:
                failed = transition_workspace(
                    workspace,
                    WorkspaceState.FAILED,
                    at=self._clock(),
                )
                await self._repository.update_workspace(
                    failed,
                    expected_revision=workspace.revision,
                    expected_state=WorkspaceState.CREATING,
                )
                raise CloudError(
                    CloudErrorCode.WORKSPACE_PROVISION_FAILED,
                    "workspace resources could not be provisioned",
                ) from exc
        return await self.inspect_workspace(workspace.id)

    async def _active_run_id(self, workspace_id: WorkspaceId) -> RunId | None:
        for state in ACTIVE_RUN_STATES:
            page = await self._repository.list_runs(
                limit=1,
                workspace_id=workspace_id,
                state=state,
            )
            if page.items:
                return page.items[0].id
        return None

    async def _inspect(self, workspace: Workspace) -> WorkspaceInspection:
        return WorkspaceInspection(
            workspace=workspace,
            active_run_id=await self._active_run_id(workspace.id),
            codex_config_present=(workspace.codex_home_path / "config.toml").is_file(),
            codex_auth_present=(workspace.codex_home_path / "auth.json").is_file(),
        )

    async def inspect_workspace(self, workspace_id: WorkspaceId) -> WorkspaceInspection:
        self._ensure_enabled()
        workspace = await self._repository.get_workspace(workspace_id)
        if workspace is None:
            raise CloudError(
                CloudErrorCode.WORKSPACE_NOT_FOUND,
                "workspace does not exist",
            )
        return await self._inspect(workspace)

    async def get_workspace(self, workspace_id: WorkspaceId) -> WorkspaceInspection:
        """Return the inspectable workspace view for one stable identifier."""

        return await self.inspect_workspace(workspace_id)

    async def list_workspaces(
        self,
        *,
        limit: int,
        page_token: str | None = None,
    ) -> Page[WorkspaceInspection]:
        self._ensure_enabled()
        page = await self._repository.list_workspaces(
            limit=limit,
            page_token=page_token,
        )
        inspections = tuple(
            [await self._inspect(workspace) for workspace in page.items]
        )
        return Page(items=inspections, next_page_token=page.next_page_token)

    async def release_workspace(
        self,
        workspace_id: WorkspaceId,
        *,
        idempotency_key: str,
    ) -> WorkspaceInspection:
        self._ensure_enabled()
        idempotency_key = _require_text(
            idempotency_key,
            field_name="idempotency_key",
        )
        inspection = await self.inspect_workspace(workspace_id)
        workspace = inspection.workspace
        if workspace.state is WorkspaceState.RELEASED:
            return inspection
        if (
            workspace.state is WorkspaceState.BUSY
            or inspection.active_run_id is not None
        ):
            raise WorkspaceBusyError(str(workspace_id))
        if workspace.state is not WorkspaceState.READY:
            raise CloudError(
                CloudErrorCode.INVALID_TRANSITION,
                "workspace cannot be released from its current state",
            )
        profile = self._profile(workspace.profile_name)
        releasing = transition_workspace(
            workspace,
            WorkspaceState.RELEASING,
            at=self._clock(),
        )
        releasing = await self._repository.begin_workspace_release(
            releasing,
            expected_revision=workspace.revision,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint(
                "workspace.release",
                {"workspace_id": str(workspace.id)},
            ),
        )
        try:
            self._paths.release_writable_workspace(
                profile,
                workspace.id,
                workspace.writable_repo_path,
            )
        except OSError as exc:
            failed = transition_workspace(
                releasing,
                WorkspaceState.FAILED,
                at=self._clock(),
            )
            await self._repository.update_workspace(
                failed,
                expected_revision=releasing.revision,
                expected_state=WorkspaceState.RELEASING,
            )
            raise CloudError(
                CloudErrorCode.WORKSPACE_PROVISION_FAILED,
                "workspace resources could not be released",
            ) from exc
        released = transition_workspace(
            releasing,
            WorkspaceState.RELEASED,
            at=self._clock(),
        )
        released = await self._repository.update_workspace(
            released,
            expected_revision=releasing.revision,
            expected_state=WorkspaceState.RELEASING,
        )
        return await self._inspect(released)

    async def submit_run(
        self,
        workspace_id: WorkspaceId,
        *,
        task: str,
        model: str | None,
        idempotency_key: str,
    ) -> Run:
        self._ensure_enabled()
        task = _require_text(task, field_name="task")
        idempotency_key = _require_text(
            idempotency_key,
            field_name="idempotency_key",
        )
        await self.inspect_workspace(workspace_id)
        run = Run(
            id=RunId(f"run-{self._id_factory()}"),
            workspace_id=workspace_id,
            state=RunState.QUEUED,
            revision=0,
            attempt=1,
            task=task,
            model=model,
            created_at=self._clock(),
        )
        stored = await self._repository.create_run(
            run,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint(
                "run.submit",
                {"model": model, "task": task, "workspace_id": str(workspace_id)},
            ),
        )
        if stored.id == run.id:
            await self._repository.append_event(
                stored.id,
                event_type="run.queued",
                payload={"state": stored.state.value},
                created_at=stored.created_at,
            )
        return stored

    async def get_run(self, run_id: RunId) -> Run:
        self._ensure_enabled()
        run = await self._repository.get_run(run_id)
        if run is None:
            raise CloudError(CloudErrorCode.RUN_NOT_FOUND, "run does not exist")
        return run

    async def list_runs(
        self,
        *,
        limit: int,
        page_token: str | None = None,
        workspace_id: WorkspaceId | None = None,
        state: RunState | None = None,
    ) -> Page[Run]:
        self._ensure_enabled()
        return await self._repository.list_runs(
            limit=limit,
            page_token=page_token,
            workspace_id=workspace_id,
            state=state,
        )

    async def cancel_run(
        self,
        run_id: RunId,
        *,
        idempotency_key: str,
    ) -> Run:
        self._ensure_enabled()
        idempotency_key = _require_text(
            idempotency_key,
            field_name="idempotency_key",
        )
        before = await self.get_run(run_id)
        cancelled = await self._repository.request_run_cancellation(
            run_id,
            requested_at=self._clock(),
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint(
                "run.cancel",
                {"run_id": str(run_id)},
            ),
        )
        if cancelled.revision != before.revision:
            await self._repository.append_event(
                cancelled.id,
                event_type=f"run.{cancelled.state.value}",
                payload={"state": cancelled.state.value},
                created_at=self._clock(),
            )
        return cancelled

    async def retry_run(
        self,
        run_id: RunId,
        *,
        idempotency_key: str,
    ) -> Run:
        self._ensure_enabled()
        idempotency_key = _require_text(
            idempotency_key,
            field_name="idempotency_key",
        )
        source = await self.get_run(run_id)
        retry = create_retry_run(
            source,
            run_id=RunId(f"run-{self._id_factory()}"),
            created_at=self._clock(),
        )
        stored = await self._repository.create_retry_run(
            retry,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint(
                "run.retry",
                {"run_id": str(run_id)},
            ),
        )
        if stored.id == retry.id:
            await self._repository.append_event(
                stored.id,
                event_type="run.queued",
                payload={
                    "retry_of_run_id": str(source.id),
                    "state": stored.state.value,
                },
                created_at=stored.created_at,
            )
        return stored
