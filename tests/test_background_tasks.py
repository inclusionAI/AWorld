import os
import sys
import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "aworld-cli" / "src"))

from aworld.core.common import TaskStatusValue
from aworld_cli.commands.tasks import TasksCommand
from aworld_cli.core.background_task_manager import BackgroundTaskManager
from aworld_cli.models.task_metadata import TaskMetadata


class _StubTaskManager:
    def __init__(self, task: TaskMetadata):
        self._task = task

    def get_task(self, task_id: str):
        if self._task.task_id == task_id:
            return self._task
        return None


class TestBackgroundTaskManager:
    def test_normalize_final_status(self):
        manager = BackgroundTaskManager(session_id="test-session")

        assert manager._normalize_final_status(None) == "failed"
        assert manager._normalize_final_status(SimpleNamespace(status=TaskStatusValue.SUCCESS, success=True)) == "completed"
        assert manager._normalize_final_status(SimpleNamespace(status=TaskStatusValue.FAILED, success=False)) == "failed"
        assert manager._normalize_final_status(SimpleNamespace(status=TaskStatusValue.CANCELLED, success=False)) == "cancelled"
        assert manager._normalize_final_status(SimpleNamespace(status=TaskStatusValue.INTERRUPTED, success=False)) == "interrupted"
        assert manager._normalize_final_status(SimpleNamespace(status=TaskStatusValue.TIMEOUT, success=False)) == "timeout"
        assert manager._normalize_final_status(SimpleNamespace(status=TaskStatusValue.RUNNING, success=True)) == "running"
        assert manager._normalize_final_status(SimpleNamespace(status="unknown", success=False)) == "failed"

    @pytest.mark.anyio
    async def test_definitive_task_response_prefers_run_result(self):
        manager = BackgroundTaskManager(session_id="test-session")
        expected = SimpleNamespace(status=TaskStatusValue.SUCCESS, success=True, answer="ok")

        async def fake_run():
            await asyncio.sleep(0)
            return {"aw-task-1": expected}

        class DummyStreamingOutputs:
            def __init__(self):
                self._run_impl_task = asyncio.create_task(fake_run())
            def response(self):
                return None

        actual = await manager._get_definitive_task_response("aw-task-1", DummyStreamingOutputs())
        assert actual is expected

    @pytest.mark.anyio
    async def test_persist_tasks_uses_background_thread(self, monkeypatch, tmp_path):
        manager = BackgroundTaskManager(session_id="test-session", output_dir=str(tmp_path))
        manager.tasks["task-001"] = TaskMetadata(
            task_id="task-001",
            agent_name="Aworld",
            task_content="persist me",
            status="pending",
            submitted_at=datetime.now(),
        )

        captured = {}

        async def fake_to_thread(func, *args, **kwargs):
            captured["func"] = func
            captured["args"] = args
            captured["kwargs"] = kwargs
            return func(*args, **kwargs)

        monkeypatch.setattr("aworld_cli.core.background_task_manager.asyncio.to_thread", fake_to_thread)

        await manager._persist_tasks()

        assert captured["func"] == manager._write_persisted_tasks
        assert manager.metadata_file.exists()


class TestTasksCommandRendering:
    @pytest.mark.anyio
    async def test_show_status_has_no_ansi_escape_codes(self):
        cmd = TasksCommand()
        task = TaskMetadata(
            task_id="task-001",
            agent_name="Aworld",
            task_content="test task",
            status="failed",
            submitted_at=datetime.now(),
            started_at=datetime.now(),
            completed_at=datetime.now(),
            result="partial result",
            error="boom",
            output_file=".aworld/tasks/task-001.log",
        )

        result = await cmd._show_status("task-001", _StubTaskManager(task))

        assert "Task ID:" in result
        assert "Status:" in result
        assert "\x1b[" not in result

    @pytest.mark.anyio
    async def test_list_tasks_has_no_ansi_escape_codes(self):
        cmd = TasksCommand()
        task = TaskMetadata(
            task_id="task-001",
            agent_name="Aworld",
            task_content="test task",
            status="timeout",
            submitted_at=datetime.now(),
            output_file=".aworld/tasks/task-001.log",
        )

        class _ListTaskManager:
            def list_tasks(self):
                return [task]

            def get_stats(self):
                return {
                    "total": 1,
                    "running": 0,
                    "completed": 0,
                    "failed": 0,
                    "cancelled": 0,
                    "interrupted": 0,
                    "timeout": 1,
                    "pending": 0,
                }

        result = await cmd._list_tasks(_ListTaskManager())

        assert "Background Tasks" in result
        assert "task-001" in result
        assert "\x1b[" not in result
