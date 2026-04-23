from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp import executor as executor_module
from aworld_cli.acp.executor import AcpLocalExecutor
from aworld_cli.executors.local import LocalAgentExecutor


@pytest.mark.asyncio
async def test_acp_local_executor_create_workspace_does_not_mutate_global_workspace_env(
    monkeypatch, tmp_path: Path
) -> None:
    created: dict[str, str] = {}

    class FakeWorkSpace:
        @staticmethod
        def from_local_storages(*, session_id: str, storage_path: str):
            created["session_id"] = session_id
            created["storage_path"] = storage_path
            return {"session_id": session_id, "storage_path": storage_path}

    monkeypatch.setattr(executor_module, "WorkSpace", FakeWorkSpace, raising=False)
    monkeypatch.setenv("WORKSPACE_PATH", "/tmp/original-workspace")

    executor = AcpLocalExecutor.__new__(AcpLocalExecutor)
    executor._working_directory = str(tmp_path)

    workspace = await AcpLocalExecutor._create_workspace(executor, "session-1")

    assert workspace == {
        "session_id": "session-1",
        "storage_path": str(tmp_path / ".aworld" / "workspaces" / "session-1"),
    }
    assert created["storage_path"] == str(tmp_path / ".aworld" / "workspaces" / "session-1")
    assert os.environ["WORKSPACE_PATH"] == "/tmp/original-workspace"


@pytest.mark.asyncio
async def test_acp_local_executor_build_task_sets_context_working_dir_base_path(
    monkeypatch, tmp_path: Path
) -> None:
    seen: dict[str, str] = {}

    async def fake_parent_build_task(self, task_content: str, session_id=None, task_id=None, image_urls=None):
        seen["task_content"] = task_content
        seen["working_dir_base_path"] = self.context_config.env_config.working_dir_base_path
        return {"task_content": task_content, "session_id": session_id}

    monkeypatch.setattr(LocalAgentExecutor, "_build_task", fake_parent_build_task)

    executor = AcpLocalExecutor.__new__(AcpLocalExecutor)
    executor._working_directory = str(tmp_path)
    executor.context_config = None

    payload = await AcpLocalExecutor._build_task(
        executor,
        "hello",
        session_id="session-1",
    )

    assert payload == {"task_content": "hello", "session_id": "session-1"}
    assert seen["task_content"] == "hello"
    assert seen["working_dir_base_path"] == str(tmp_path / ".aworld" / "workspaces")
