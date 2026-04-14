from pathlib import Path

import pytest

from aworld_cli.runtime.cron_notifications import CronNotificationCenter
from examples.cron_experience_demo.demo_setup import DemoPaths, ensure_demo_paths
from examples.cron_experience_demo.run_auto_demo import (
    DemoCronExecutor,
    _build_auto_demo_jobs,
    build_demo_runtime,
    default_demo_root,
)


def test_default_demo_root_uses_hidden_runtime_directory() -> None:
    root = default_demo_root()

    assert root.name == ".demo_runtime"
    assert root.parent.name == "cron_experience_demo"


@pytest.mark.asyncio
async def test_build_demo_runtime_creates_isolated_runtime(tmp_path: Path) -> None:
    runtime = await build_demo_runtime(tmp_path)

    expected_root = tmp_path.resolve()
    expected_store = expected_root / ".aworld" / "cron.json"

    assert runtime.paths.root == expected_root
    assert runtime.paths.cron_store == expected_store
    assert runtime.paths.aworld_dir == expected_root / ".aworld"
    assert runtime.scheduler.store.file_path == expected_store
    assert isinstance(runtime.notification_center, CronNotificationCenter)
    assert runtime.scheduler.notification_sink == runtime.notification_center.publish


@pytest.mark.asyncio
async def test_build_demo_runtime_rejects_existing_non_demo_directory(tmp_path: Path) -> None:
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep-me", encoding="utf-8")

    with pytest.raises(ValueError, match="empty directory or an initialized demo root"):
        await build_demo_runtime(tmp_path)

    assert sentinel.read_text(encoding="utf-8") == "keep-me"


@pytest.mark.asyncio
async def test_build_demo_runtime_resets_existing_initialized_demo_root(tmp_path: Path) -> None:
    paths = DemoPaths.from_root(tmp_path)
    ensure_demo_paths(paths)
    stale_file = paths.outputs_dir / "stale.txt"
    stale_file.write_text("stale", encoding="utf-8")

    runtime = await build_demo_runtime(tmp_path)

    assert runtime.paths.root == tmp_path.resolve()
    assert not stale_file.exists()


@pytest.mark.asyncio
async def test_demo_cron_executor_writes_showcase_outputs(tmp_path: Path) -> None:
    paths = DemoPaths.from_root(tmp_path / "demo-root")
    ensure_demo_paths(paths)
    reminder_job, heartbeat_job = _build_auto_demo_jobs(paths)
    executor = DemoCronExecutor()

    reminder_result = await executor.execute(reminder_job)
    heartbeat_result = await executor.execute(heartbeat_job)

    assert reminder_result.success is True
    assert heartbeat_result.success is True
    assert (paths.outputs_dir / "reminder.txt").is_file()
    assert (paths.outputs_dir / "heartbeat.log").is_file()
