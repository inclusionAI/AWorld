# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Tests for cron notification system.

Tests notification center, scheduler integration, and notification publishing.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime
from io import StringIO
from datetime import timedelta
import pytz
from rich.console import Console

from aworld_cli.runtime.cron_notifications import CronNotification, CronNotificationCenter
from aworld_cli.console import AWorldCLI
from aworld.core.scheduler.scheduler import CronScheduler
from aworld.core.scheduler.store import FileBasedCronStore
from aworld.core.scheduler.executor import CronExecutor
from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload, CronJobState
from aworld.core.task import TaskResponse


@pytest.mark.asyncio
async def test_notification_center_publish_and_drain():
    """Test FIFO queue behavior of notification center."""
    center = CronNotificationCenter(max_size=10)

    # Publish 3 notifications
    await center.publish({
        'job_id': 'job1',
        'job_name': 'Job 1',
        'status': 'ok',
        'summary': 'Job 1 completed',
        'created_at': datetime.now(pytz.UTC).isoformat()
    })

    await center.publish({
        'job_id': 'job2',
        'job_name': 'Job 2',
        'status': 'error',
        'summary': 'Job 2 failed',
        'created_at': datetime.now(pytz.UTC).isoformat()
    })

    await center.publish({
        'job_id': 'job3',
        'job_name': 'Job 3',
        'status': 'timeout',
        'summary': 'Job 3 timed out',
        'created_at': datetime.now(pytz.UTC).isoformat()
    })

    # Drain and verify FIFO order
    notifications = await center.drain()

    assert len(notifications) == 3
    assert notifications[0].job_id == 'job1'
    assert notifications[1].job_id == 'job2'
    assert notifications[2].job_id == 'job3'

    # Verify queue is empty after drain
    notifications2 = await center.drain()
    assert len(notifications2) == 0


@pytest.mark.asyncio
async def test_notification_center_bounded_size():
    """Test that notification center enforces max size."""
    center = CronNotificationCenter(max_size=3)

    # Publish 5 notifications (exceeds max)
    for i in range(5):
        await center.publish({
            'job_id': f'job{i}',
            'job_name': f'Job {i}',
            'status': 'ok',
            'summary': f'Job {i} completed',
            'created_at': datetime.now(pytz.UTC).isoformat()
        })

    # Drain and verify only last 3 are kept (oldest dropped)
    notifications = await center.drain()

    # Due to FIFO dropping, we should have job2, job3, job4
    assert len(notifications) == 3
    # First two (job0, job1) should have been dropped
    assert notifications[0].job_id == 'job2'
    assert notifications[1].job_id == 'job3'
    assert notifications[2].job_id == 'job4'


@pytest.mark.asyncio
async def test_notification_center_tracks_unread_count_until_drain():
    """Unread count should increase on publish and reset after drain."""
    center = CronNotificationCenter(max_size=10)

    assert center.get_unread_count() == 0

    await center.publish({
        'job_id': 'job1',
        'job_name': 'Job 1',
        'status': 'ok',
        'summary': 'Job 1 completed',
        'created_at': datetime.now(pytz.UTC).isoformat()
    })
    await center.publish({
        'job_id': 'job2',
        'job_name': 'Job 2',
        'status': 'ok',
        'summary': 'Job 2 completed',
        'created_at': datetime.now(pytz.UTC).isoformat()
    })

    assert center.get_unread_count() == 2

    drained = await center.drain()
    assert len(drained) == 2
    assert center.get_unread_count() == 0


@pytest.mark.asyncio
async def test_notification_center_skips_non_user_visible_notifications():
    """Silent internal notifications should not surface in the user-facing queue."""
    center = CronNotificationCenter(max_size=10)

    await center.publish({
        'job_id': 'job1',
        'job_name': 'sample monitor',
        'status': 'ok',
        'summary': 'silent cleanup',
        'created_at': datetime.now(pytz.UTC).isoformat(),
        'user_visible': False,
    })

    notifications = await center.drain()

    assert notifications == []
    assert center.get_unread_count() == 0


@pytest.mark.asyncio
async def test_scheduler_publishes_on_success():
    """Successful non-reminder jobs should publish and persist their result summary."""
    import tempfile
    from pathlib import Path

    # Create temp store
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        # Mock executor
        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=True, msg="当前结果 68000 units")
        )

        # Mock notification sink
        notification_sink = AsyncMock()

        # Create scheduler with notification sink
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        # Create and add a job
        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="test-job",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="test"),
            state=CronJobState(
                running=True,  # Simulate claimed job
                last_run_at=now.isoformat(),
                next_run_at=None
            )
        )

        await store.add_job(job)

        # Execute the claimed job
        await scheduler._execute_claimed_job(job)

        # Verify notification was published
        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args['job_id'] == job.id
        assert call_args['job_name'] == 'test-job'
        assert call_args['status'] == 'ok'
        assert 'completed' in call_args['summary']
        assert call_args['detail'] == "当前结果 68000 units"

        persisted_job = await store.get_job(job.id)
        assert persisted_job is not None
        assert persisted_job.state.last_result_summary == "当前结果 68000 units"


@pytest.mark.asyncio
async def test_scheduler_publishes_reminder_detail_on_success():
    """Reminder-style jobs should publish explicit reminder content for CLI rendering."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=True, msg="Success")
        )

        notification_sink = AsyncMock()
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="喝水提醒",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="提醒我喝水"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None
            )
        )

        await store.add_job(job)
        await scheduler._execute_claimed_job(job)

        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args['status'] == 'ok'
        assert call_args['detail'] == "提醒我喝水"


@pytest.mark.asyncio
async def test_scheduler_success_can_stay_silent_without_user_visible_notification():
    """Recurring jobs may complete successfully without surfacing a user-visible notification."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(
                success=True,
                msg="本次检查未产生命中事件",
                user_visible=False,
            )
        )

        notification_sink = AsyncMock()
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="silent-recurring-job",
            schedule=CronSchedule(kind="every", every_seconds=10),
            payload=CronPayload(message="run periodic task"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=(now + timedelta(seconds=10)).isoformat(),
            ),
        )

        await store.add_job(job)
        await scheduler._execute_claimed_job(job)

        notification_sink.assert_not_called()
        persisted_job = await store.get_job(job.id)
        assert persisted_job is not None
        assert persisted_job.enabled is True
        assert persisted_job.state.next_run_at is not None


@pytest.mark.asyncio
async def test_scheduler_recurring_job_can_publish_event_notification_without_stopping():
    """Recurring jobs may surface a user-visible event and continue scheduling later runs."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(
                success=True,
                msg="条件命中告警",
                answer="条件命中告警\n明细：发生了用户可见事件",
                user_visible=True,
            )
        )

        notification_sink = AsyncMock()
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="eventful-recurring-job",
            schedule=CronSchedule(kind="every", every_seconds=10),
            payload=CronPayload(message="run periodic task"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=(now + timedelta(seconds=10)).isoformat(),
            ),
        )

        await store.add_job(job)
        await scheduler._execute_claimed_job(job)

        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args["status"] == "ok"
        assert call_args["user_visible"] is True
        assert "completed" in call_args["summary"]
        assert "用户可见事件" in call_args["detail"]

        persisted_job = await store.get_job(job.id)
        assert persisted_job is not None
        assert persisted_job.enabled is True
        assert persisted_job.state.next_run_at is not None


@pytest.mark.asyncio
async def test_scheduler_publishes_full_result_detail_for_deleted_one_time_job():
    """One-time jobs should still publish full result detail even after deletion."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(
                success=True,
                msg="Task completed",
                answer="已保存 200 条内容\n输出文件：twitter_for_you_posts_200.md\n日志：twitter_scraper_200.log",
            )
        )

        notification_sink = AsyncMock()
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="twitter_scraper_200_posts",
            delete_after_run=True,
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="run scraper", tool_names=["bash"]),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None,
            ),
        )

        await store.add_job(job)
        await scheduler._execute_claimed_job(job)

        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args["status"] == "ok"
        assert "最终回答：" in call_args["detail"]
        assert "twitter_for_you_posts_200.md" in call_args["detail"]
        assert await store.get_job(job.id) is None


@pytest.mark.asyncio
async def test_scheduler_publishes_live_progress_logs():
    """Scheduler should publish live execution progress for `/cron show` follow mode."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        executor = AsyncMock(spec=CronExecutor)

        async def fake_execute_with_retry(job, progress_callback=None, **kwargs):
            if progress_callback:
                await progress_callback("info", "子步骤：准备执行")
            return TaskResponse(success=True, msg="执行完成")

        executor.execute_with_retry = AsyncMock(side_effect=fake_execute_with_retry)

        progress_sink = AsyncMock()
        scheduler = CronScheduler(store, executor, progress_sink=progress_sink)

        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="live-job",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="test"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None,
            ),
        )

        await store.add_job(job)
        await scheduler._execute_claimed_job(job)

        assert progress_sink.await_count >= 3
        messages = [call.args[0]["message"] for call in progress_sink.await_args_list]
        assert any("任务开始执行" in message for message in messages)
        assert any("子步骤：准备执行" in message for message in messages)
        assert any("任务执行完成" in message for message in messages)


@pytest.mark.asyncio
async def test_notification_center_logs_warning_when_progress_store_fails(monkeypatch):
    """Progress-log buffering failures should be surfaced as warnings."""
    center = CronNotificationCenter()
    warnings = []

    monkeypatch.setattr(
        "aworld_cli.runtime.cron_notifications.logger.warning",
        lambda message: warnings.append(message),
    )
    monkeypatch.setattr(center, "_progress_logs", None)

    await center.publish_progress({
        "job_id": "job-1",
        "job_name": "test",
        "message": "step",
    })

    assert len(warnings) == 1
    assert "Failed to store cron progress log" in warnings[0]


@pytest.mark.asyncio
async def test_scheduler_short_circuits_instructional_reminders():
    """Reminder payloads should publish directly instead of re-entering the agent."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=True, msg="should not be used")
        )

        notification_sink = AsyncMock()
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="运动提醒",
            delete_after_run=True,
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="提醒用户运动", agent_name="default"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None,
            ),
        )

        await store.add_job(job)
        await scheduler._execute_claimed_job(job)

        executor.execute_with_retry.assert_not_called()
        assert await store.get_job(job.id) is None

        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args["status"] == "ok"
        assert call_args["detail"] == "提醒我运动"


@pytest.mark.asyncio
async def test_scheduler_publishes_on_error():
    """Test that scheduler publishes error notification with error message."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        # Mock executor that fails
        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=False, msg="Task failed with error")
        )

        # Mock notification sink
        notification_sink = AsyncMock()

        # Create scheduler with notification sink
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        # Create and add a job
        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="failing-job",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="test"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None
            )
        )

        await store.add_job(job)

        # Execute the claimed job
        await scheduler._execute_claimed_job(job)

        # Verify error notification was published
        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args['job_id'] == job.id
        assert call_args['status'] == 'error'
        assert 'failed' in call_args['summary']
        # Per Section 8.4, summary uses fixed template without error detail
        assert call_args['summary'] == 'Cron task "failing-job" failed'


@pytest.mark.asyncio
async def test_scheduler_publishes_on_timeout():
    """Test that scheduler publishes timeout notification."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        # Mock executor that times out
        async def timeout_executor(job, progress_callback=None):
            await asyncio.sleep(10)  # Will be cancelled by timeout
            return TaskResponse(success=True, msg="Should not reach here")

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(side_effect=timeout_executor)

        # Mock notification sink
        notification_sink = AsyncMock()

        # Create scheduler with notification sink
        scheduler = CronScheduler(store, executor, notification_sink=notification_sink)

        # Create and add a job with short timeout
        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="timeout-job",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="test", timeout_seconds=1),  # 1 second timeout
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None
            )
        )

        await store.add_job(job)

        # Execute the claimed job (will timeout)
        await scheduler._execute_claimed_job(job)

        # Verify timeout notification was published
        notification_sink.assert_called_once()
        call_args = notification_sink.call_args[0][0]
        assert call_args['job_id'] == job.id
        assert call_args['status'] == 'timeout'
        assert 'timed out' in call_args['summary']


@pytest.mark.asyncio
async def test_scheduler_no_publish_without_sink():
    """Test that scheduler handles absence of notification sink gracefully."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        # Mock executor
        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=True, msg="Success")
        )

        # Create scheduler WITHOUT notification sink
        scheduler = CronScheduler(store, executor, notification_sink=None)

        # Create and add a job
        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="test-job",
            schedule=CronSchedule(kind="at", at=now.isoformat()),
            payload=CronPayload(message="test"),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=None
            )
        )

        await store.add_job(job)

        # Execute the claimed job (should not crash without sink)
        try:
            await scheduler._execute_claimed_job(job)

            # Should complete successfully without notification
            assert True
        except Exception as e:
            pytest.fail(f"Scheduler should handle missing sink gracefully, but raised: {e}")


@pytest.mark.asyncio
async def test_every_job_first_followup_advances_by_full_interval():
    """First execution of an every-job should schedule the next run one full interval later."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))
        scheduler = CronScheduler(store, AsyncMock(spec=CronExecutor))

        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="interval-job",
            schedule=CronSchedule(kind="every", every_seconds=180),
            payload=CronPayload(message="test"),
        )

        added_job = await scheduler.add_job(job)
        claim_time = datetime.fromisoformat(added_job.state.next_run_at.replace('Z', '+00:00'))
        claimed_job = await store.claim_due_job(
            added_job.id,
            claim_time.isoformat(),
            scheduler._calculate_claim_next_run(added_job, claim_time).isoformat(),
        )

        assert claimed_job is not None
        assert claimed_job.state.next_run_at == (claim_time + timedelta(seconds=180)).isoformat()


@pytest.mark.asyncio
async def test_scheduler_disables_job_after_max_runs():
    """Bounded recurring jobs should stop themselves after the configured number of runs."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "cron.json"
        store = FileBasedCronStore(str(store_path))

        executor = AsyncMock(spec=CronExecutor)
        executor.execute_with_retry = AsyncMock(
            return_value=TaskResponse(success=True, msg="Success")
        )
        scheduler = CronScheduler(store, executor)

        now = datetime.now(pytz.UTC)
        job = CronJob(
            name="bounded-job",
            schedule=CronSchedule(kind="every", every_seconds=180),
            payload=CronPayload(message="提醒我运动", max_runs=3),
            state=CronJobState(
                running=True,
                last_run_at=now.isoformat(),
                next_run_at=(now + timedelta(seconds=180)).isoformat(),
                run_count=2,
            )
        )

        await store.add_job(job)
        await scheduler._execute_claimed_job(job)

        persisted_job = await store.get_job(job.id)
        assert persisted_job is not None
        assert persisted_job.enabled is False
        assert persisted_job.state.next_run_at is None
        assert persisted_job.state.run_count == 3


def test_console_renders_reminder_detail():
    """CLI notification renderer should surface notification detail below status line."""
    cli = AWorldCLI()
    buffer = StringIO()
    cli.console = Console(file=buffer, force_terminal=False, color_system=None)

    cli.render_cron_notifications([
        CronNotification(
            job_id="job-1",
            job_name="喝水提醒",
            status="ok",
            summary='Cron task "喝水提醒" completed',
            detail="提醒我喝水",
        )
    ])

    output = buffer.getvalue()
    assert 'Cron task "喝水提醒" completed' in output
    assert '内容：提醒我喝水' in output


def test_console_formats_information_style_status_bar_with_unread_cron_notifications():
    """Unread cron reminders should appear in the general status bar."""
    cli = AWorldCLI()
    cli._toolbar_workspace_name = "aworld"
    cli._toolbar_git_branch = "feat/subagent-optimization-clean"

    class FakeRuntime:
        def __init__(self):
            self._notification_center = CronNotificationCenter()

    runtime = FakeRuntime()

    asyncio.run(runtime._notification_center.publish({
        'job_id': 'job-1',
        'job_name': '喝水提醒',
        'status': 'ok',
        'summary': 'Cron task "喝水提醒" completed',
        'detail': '提醒我喝水',
        'created_at': datetime.now(pytz.UTC).isoformat(),
    }))

    toolbar = cli._build_status_bar_text(runtime, agent_name="Aworld", mode="Chat")

    assert toolbar is not None
    assert "Agent: Aworld" in toolbar
    assert "Mode: Chat" in toolbar
    assert "Cron: 1 unread" in toolbar
    assert "Workspace: aworld" in toolbar
    assert "Branch: feat/subagent-optimization-clean" in toolbar
    assert "Hint: /cron inbox" not in toolbar


@pytest.mark.asyncio
async def test_notification_poller_keeps_unread_notifications_when_hud_is_active():
    center = CronNotificationCenter()
    await center.publish({
        "job_id": "job-hud",
        "job_name": "HUD Job",
        "status": "ok",
        "summary": "HUD Job completed",
        "created_at": datetime.now(pytz.UTC).isoformat(),
    })

    class FakeRuntime:
        def __init__(self):
            self._notification_center = center

        async def _drain_notifications(self):
            return await center.drain()

        def active_plugin_capabilities(self):
            return ("hud",)

    cli = AWorldCLI()
    buffer = StringIO()
    cli.console = Console(file=buffer, force_terminal=False, color_system=None)
    stop_event = asyncio.Event()

    task = asyncio.create_task(cli._notification_poller(FakeRuntime(), poll_interval=0.01, stop_event=stop_event))
    await asyncio.sleep(0.03)
    stop_event.set()
    await task

    assert center.get_unread_count() == 1
    assert buffer.getvalue() == ""


@pytest.mark.asyncio
async def test_notification_poller_drains_and_renders_without_hud():
    center = CronNotificationCenter()
    await center.publish({
        "job_id": "job-inline",
        "job_name": "Inline Job",
        "status": "ok",
        "summary": "Inline Job completed",
        "created_at": datetime.now(pytz.UTC).isoformat(),
    })

    class FakeRuntime:
        def __init__(self):
            self._notification_center = center

        async def _drain_notifications(self):
            return await center.drain()

        def active_plugin_capabilities(self):
            return ()

    cli = AWorldCLI()
    buffer = StringIO()
    cli.console = Console(file=buffer, force_terminal=False, color_system=None)
    stop_event = asyncio.Event()

    task = asyncio.create_task(cli._notification_poller(FakeRuntime(), poll_interval=0.01, stop_event=stop_event))
    await asyncio.sleep(0.03)
    stop_event.set()
    await task

    assert center.get_unread_count() == 0
    assert "Inline Job completed" in buffer.getvalue()


@pytest.mark.asyncio
async def test_notification_poller_uses_prompt_safe_rendering_without_hud_when_prompt_is_active(monkeypatch):
    center = CronNotificationCenter()
    await center.publish({
        "job_id": "job-inline-active",
        "job_name": "Inline Active Job",
        "status": "ok",
        "summary": "Inline Active Job completed",
        "created_at": datetime.now(pytz.UTC).isoformat(),
    })

    class FakeRuntime:
        def __init__(self):
            self._notification_center = center

        async def _drain_notifications(self):
            return await center.drain()

        def active_plugin_capabilities(self):
            return ()

    cli = AWorldCLI()
    buffer = StringIO()
    cli.console = Console(file=buffer, force_terminal=False, color_system=None)
    cli._active_prompt_session = MagicMock()
    stop_event = asyncio.Event()
    calls = []

    async def fake_run_in_terminal(func, render_cli_done=False, in_executor=False):
        calls.append((render_cli_done, in_executor))
        func()

    monkeypatch.setattr("aworld_cli.console.run_in_terminal", fake_run_in_terminal, raising=False)

    task = asyncio.create_task(cli._notification_poller(FakeRuntime(), poll_interval=0.01, stop_event=stop_event))
    await asyncio.sleep(0.03)
    stop_event.set()
    await task

    assert center.get_unread_count() == 0
    assert calls == [(False, False)]
    assert "Inline Active Job completed" in buffer.getvalue()


@pytest.mark.asyncio
async def test_ensure_notification_poller_starts_once_and_stops_cleanly():
    class FakeRuntime:
        def __init__(self):
            self._notification_center = object()

    cli = AWorldCLI()
    starts = []

    async def fake_poller(runtime, poll_interval=2.0, stop_event=None):
        starts.append((runtime, stop_event))
        await stop_event.wait()

    cli._notification_poller = fake_poller

    await cli._ensure_notification_poller(FakeRuntime())
    first_task = cli._notification_poll_task
    await asyncio.sleep(0)

    assert first_task is not None
    assert len(starts) == 1

    await cli._ensure_notification_poller(FakeRuntime())
    assert cli._notification_poll_task is first_task
    assert len(starts) == 1

    await cli._stop_notification_poller()
    assert cli._notification_poll_task is None
    assert cli._notification_stop_event is None


@pytest.mark.asyncio
async def test_notification_poller_invalidates_active_prompt_when_hud_is_active():
    center = CronNotificationCenter()
    await center.publish({
        "job_id": "job-hud-refresh",
        "job_name": "HUD Refresh Job",
        "status": "ok",
        "summary": "HUD Refresh Job completed",
        "created_at": datetime.now(pytz.UTC).isoformat(),
    })

    class FakeRuntime:
        def __init__(self):
            self._notification_center = center

        async def _drain_notifications(self):
            return await center.drain()

        def active_plugin_capabilities(self):
            return ("hud",)

    class FakeApp:
        def __init__(self):
            self.invalidate_calls = 0

        def invalidate(self):
            self.invalidate_calls += 1

    class FakeSession:
        def __init__(self):
            self.app = FakeApp()

    cli = AWorldCLI()
    cli._active_prompt_session = FakeSession()
    stop_event = asyncio.Event()

    task = asyncio.create_task(cli._notification_poller(FakeRuntime(), poll_interval=0.01, stop_event=stop_event))
    await asyncio.sleep(0.03)
    stop_event.set()
    await task

    assert center.get_unread_count() == 1
    assert cli._active_prompt_session.app.invalidate_calls > 0


def test_console_formats_information_style_status_bar_when_cron_queue_is_clear():
    """The status bar should remain stable even when there are no unread reminders."""
    cli = AWorldCLI()
    cli._toolbar_workspace_name = "aworld"
    cli._toolbar_git_branch = "main"

    class FakeRuntime:
        def __init__(self):
            self._notification_center = CronNotificationCenter()

    toolbar = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat")

    assert "Agent: Aworld" in toolbar
    assert "Mode: Chat" in toolbar
    assert "Cron: clear" in toolbar
    assert "Workspace: aworld" in toolbar
    assert "Branch: main" in toolbar
    assert "Hint: /cron inbox" not in toolbar


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
