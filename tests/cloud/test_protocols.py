from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

from aworld.cloud.executor import CloudExecutor
from aworld.cloud.repository import (
    EventRepository,
    RunFileRepository,
    RunRepository,
    WorkspaceRepository,
)


class _WorkspaceStore:
    async def create_workspace(self, workspace, **kwargs):
        return workspace

    async def get_workspace(self, workspace_id):
        return None

    async def list_workspaces(self, **kwargs):
        return None

    async def update_workspace(self, workspace, **kwargs):
        return workspace

    async def begin_workspace_release(self, workspace, **kwargs):
        return workspace


class _RunStore:
    async def create_run(self, run, **kwargs):
        return run

    async def get_run(self, run_id):
        return None

    async def list_runs(self, **kwargs):
        return None

    async def claim_run(self, **kwargs):
        return None

    async def update_run(self, run, **kwargs):
        return run

    async def heartbeat_run(self, run_id, **kwargs):
        return None

    async def request_run_cancellation(self, run_id, **kwargs):
        return None

    async def create_retry_run(self, run, **kwargs):
        return run

    async def list_expired_runs(self, **kwargs):
        return ()


class _EventStore:
    async def append_event(self, run_id, **kwargs):
        return None

    async def list_events(self, run_id, **kwargs):
        return None


class _FileStore:
    async def register_run_file(self, run_file):
        return run_file

    async def get_run_file(self, file_id):
        return None

    async def list_run_files(self, run_id):
        return ()


class _Executor:
    async def start(self, request):
        return None

    async def wait(self, handle, *, on_event):
        return None

    async def inspect(self, executor_id):
        return None

    async def cancel(self, executor_id, *, grace_period: timedelta):
        return None


def test_protocols_support_structural_implementations() -> None:
    assert isinstance(_WorkspaceStore(), WorkspaceRepository)
    assert isinstance(_RunStore(), RunRepository)
    assert isinstance(_EventStore(), EventRepository)
    assert isinstance(_FileStore(), RunFileRepository)
    assert isinstance(_Executor(), CloudExecutor)


def test_foundation_has_no_forbidden_runtime_imports() -> None:
    package_root = Path(__file__).parents[2] / "aworld" / "cloud"
    forbidden = {"sqlite3", "docker", "fastapi", "aworld_gateway", "asap"}
    foundation_modules = (
        "__init__.py",
        "errors.py",
        "executor.py",
        "fake_executor.py",
        "models.py",
        "paths.py",
        "repository.py",
        "service.py",
        "settings.py",
        "worker.py",
    )

    imported_roots: set[str] = set()
    for module_name in foundation_modules:
        module_path = package_root / module_name
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots.isdisjoint(forbidden)
