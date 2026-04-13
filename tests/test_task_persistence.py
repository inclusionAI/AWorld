import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "aworld-cli" / "src"))

from aworld_cli.core.background_task_manager import BackgroundTaskManager
from aworld_cli.models.task_metadata import TaskMetadata


def test_persist_and_reload_tasks(tmp_path):
    manager = BackgroundTaskManager(session_id="session-a", output_dir=str(tmp_path))
    task = TaskMetadata(
        task_id="task-003",
        agent_name="Aworld",
        task_content="persist me",
        status="completed",
        submitted_at=datetime.now(),
        started_at=datetime.now(),
        completed_at=datetime.now(),
        result="done",
        output_file=str(tmp_path / "task-003.log"),
    )
    manager.tasks[task.task_id] = task
    manager._task_counter = 4
    manager._persist_tasks_sync()

    restored = BackgroundTaskManager(session_id="session-b", output_dir=str(tmp_path))

    loaded = restored.get_task("task-003")
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.result == "done"
    assert restored._task_counter >= 4


def test_restore_marks_running_tasks_interrupted(tmp_path):
    log_path = tmp_path / "task-000.log"
    log_path.write_text("[12:00:00] hello\n", encoding="utf-8")

    manager = BackgroundTaskManager(session_id="session-a", output_dir=str(tmp_path))
    task = TaskMetadata(
        task_id="task-000",
        agent_name="Aworld",
        task_content="was running",
        status="running",
        submitted_at=datetime.now(),
        started_at=datetime.now(),
        output_file=str(log_path),
    )
    manager.tasks = {task.task_id: task}
    manager._task_counter = 1
    manager._persist_tasks_sync()

    restored = BackgroundTaskManager(session_id="session-b", output_dir=str(tmp_path))
    loaded = restored.get_task("task-000")

    assert loaded is not None
    assert loaded.status == "interrupted"
    assert "restarted" in (loaded.error or "")
    assert len(loaded.output_buffer) == 1


def test_restore_only_keeps_last_1000_log_lines(tmp_path):
    log_path = tmp_path / "task-100.log"
    with open(log_path, "w", encoding="utf-8") as f:
        for i in range(1205):
            f.write(f"[12:00:{i % 60:02d}] line-{i}\n")

    manager = BackgroundTaskManager(session_id="session-a", output_dir=str(tmp_path))
    task = TaskMetadata(
        task_id="task-100",
        agent_name="Aworld",
        task_content="large log",
        status="completed",
        submitted_at=datetime.now(),
        output_file=str(log_path),
    )

    manager._load_output_buffer_from_log(task)

    assert len(task.output_buffer) == 1000
    assert task.output_buffer[0][1] == "line-205"
    assert task.output_buffer[-1][1] == "line-1204"
