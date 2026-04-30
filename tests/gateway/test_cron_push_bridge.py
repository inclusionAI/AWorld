from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld.output.base import ToolResultOutput

from aworld_gateway.cron_push import CronPushBindingStore, CronPushBridge


def test_bridge_extracts_primary_and_advance_job_ids() -> None:
    output = ToolResultOutput(
        tool_name="cron",
        action_name="cron_tool",
        data={
            "success": True,
            "job_id": "job-main",
            "advance_reminder": {"job_id": "job-advance"},
        },
    )

    assert CronPushBridge.extract_job_ids(output) == ["job-main", "job-advance"]


def test_bridge_deduplicates_repeated_job_ids() -> None:
    output = ToolResultOutput(
        tool_name="cron",
        action_name="cron_tool",
        data={
            "success": True,
            "job_id": "job-main",
            "advance_reminder": {"job_id": "job-main"},
        },
    )

    assert CronPushBridge.extract_job_ids(output) == ["job-main"]


def test_bridge_ignores_missing_or_unsuccessful_cron_payloads() -> None:
    missing_success = ToolResultOutput(
        tool_name="cron",
        action_name="cron_tool",
        data={"job_id": "job-main"},
    )
    failed = ToolResultOutput(
        tool_name="cron",
        action_name="cron_tool",
        data={"success": False, "job_id": "job-main"},
    )

    assert CronPushBridge.extract_job_ids(missing_success) == []
    assert CronPushBridge.extract_job_ids(failed) == []


def test_bridge_persists_bindings_from_cron_tool_result(tmp_path: Path) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")
    bridge = CronPushBridge(binding_store=store)
    output = ToolResultOutput(
        tool_name="cron",
        action_name="cron_tool",
        data={
            "success": True,
            "job_id": "job-main",
            "advance_reminder": {"job_id": "job-advance"},
        },
    )

    job_ids = bridge.bind_output(
        output,
        {
            "channel": "wechat",
            "account_id": "acct-1",
            "conversation_id": "conv-1",
            "sender_id": "user-1",
            "target": {"chat_id": "chat-1"},
            "meta": {"source": "cron"},
        },
    )

    assert job_ids == ["job-main", "job-advance"]
    assert store.get("job-main") == {
        "job_id": "job-main",
        "channel": "wechat",
        "account_id": "acct-1",
        "conversation_id": "conv-1",
        "sender_id": "user-1",
        "target": {"chat_id": "chat-1"},
        "meta": {"source": "cron"},
    }
    assert store.get("job-advance") == {
        "job_id": "job-advance",
        "channel": "wechat",
        "account_id": "acct-1",
        "conversation_id": "conv-1",
        "sender_id": "user-1",
        "target": {"chat_id": "chat-1"},
        "meta": {"source": "cron"},
    }


def test_bridge_logs_binding_and_terminal_cleanup(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")
    bridge = CronPushBridge(binding_store=store)
    output = ToolResultOutput(
        tool_name="cron",
        action_name="cron_tool",
        data={"success": True, "job_id": "job-main"},
    )
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    async def sender(binding, text: str, notification) -> None:
        return None

    bridge.register_sender("wechat", sender)

    bridge.bind_output(
        output,
        {
            "channel": "wechat",
            "account_id": "acct-1",
            "conversation_id": "conv-1",
            "sender_id": "user-1",
            "target": {"chat_id": "chat-1"},
        },
    )

    asyncio.run(
        bridge.publish_notification(
            {
                "job_id": "job-main",
                "summary": "done",
                "detail": "cron detail",
                "next_run_at": None,
            }
        )
    )

    assert "Cron push binding stored channel=wechat job_id=job-main" in caplog.text
    assert "Cron push notification publishing channel=wechat job_id=job-main" in caplog.text
    assert "Cron push binding removed job_id=job-main reason=terminal_notification" in caplog.text
    assert store.get("job-main") is None


def test_bridge_logs_missing_binding_and_sender(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")
    bridge = CronPushBridge(binding_store=store)
    store.upsert("job-main", {"channel": "wechat", "target": {"chat_id": "chat-1"}})
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    asyncio.run(bridge.publish_notification({"job_id": "missing-job", "summary": "done"}))
    asyncio.run(bridge.publish_notification({"job_id": "job-main", "summary": "done"}))

    assert "Cron push notification skipped job_id=missing-job reason=binding_missing" in caplog.text
    assert "Cron push notification skipped channel=wechat job_id=job-main reason=sender_missing" in caplog.text


def test_bridge_dispatches_notification_to_registered_sender(tmp_path: Path) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")
    bridge = CronPushBridge(binding_store=store)
    store.upsert(
        "job-main",
        {
            "channel": "wechat",
            "target": {"chat_id": "chat-1"},
        },
    )
    seen: list[tuple[str, str, dict]] = []
    skipped: list[tuple[str, str, dict]] = []

    async def wechat_sender(binding, text: str, notification) -> None:
        seen.append((binding["channel"], text, dict(notification)))

    async def telegram_sender(binding, text: str, notification) -> None:
        skipped.append((binding["channel"], text, dict(notification)))

    bridge.register_sender("wechat", wechat_sender)
    bridge.register_sender("telegram", telegram_sender)

    asyncio.run(
        bridge.publish_notification(
            {
                "job_id": "job-main",
                "summary": 'Cron task "喝水提醒" completed',
                "detail": "提醒我喝水",
                "next_run_at": "2026-04-28T09:00:00+08:00",
            }
        )
    )

    assert seen == [
        (
            "wechat",
            'Cron task "喝水提醒" completed\n提醒我喝水\n下次执行：2026-04-28T09:00:00+08:00',
            {
                "job_id": "job-main",
                "summary": 'Cron task "喝水提醒" completed',
                "detail": "提醒我喝水",
                "next_run_at": "2026-04-28T09:00:00+08:00",
            },
        )
    ]
    assert skipped == []


def test_bridge_install_scheduler_sink_chains_previous_sink_and_installs_once(
    tmp_path: Path,
) -> None:
    class _Scheduler:
        def __init__(self) -> None:
            self.notification_sink = None

    store = CronPushBindingStore(tmp_path / "cron-push.json")
    bridge = CronPushBridge(binding_store=store)
    store.upsert(
        "job-main",
        {
            "channel": "wechat",
            "target": {"chat_id": "chat-1"},
        },
    )
    previous_notifications: list[dict] = []
    sender_notifications: list[str] = []

    async def previous_sink(notification) -> None:
        previous_notifications.append(dict(notification))

    async def sender(binding, text: str, notification) -> None:
        sender_notifications.append(text)

    scheduler = _Scheduler()
    scheduler.notification_sink = previous_sink
    bridge.register_sender("wechat", sender)

    bridge.install_scheduler_sink(scheduler)
    installed_sink = scheduler.notification_sink
    bridge.install_scheduler_sink(scheduler)

    assert scheduler.notification_sink is installed_sink

    asyncio.run(
        scheduler.notification_sink(
            {
                "job_id": "job-main",
                "summary": 'Cron task "喝水提醒" completed',
                "detail": "提醒我喝水",
                "next_run_at": "2026-04-28T09:00:00+08:00",
            }
        )
    )

    assert previous_notifications == [
        {
            "job_id": "job-main",
            "summary": 'Cron task "喝水提醒" completed',
            "detail": "提醒我喝水",
            "next_run_at": "2026-04-28T09:00:00+08:00",
        }
    ]
    assert sender_notifications == [
        'Cron task "喝水提醒" completed\n提醒我喝水\n下次执行：2026-04-28T09:00:00+08:00'
    ]


def test_bridge_install_scheduler_sink_supports_multiple_scheduler_instances(
    tmp_path: Path,
) -> None:
    class _Scheduler:
        def __init__(self) -> None:
            self.notification_sink = None

    bridge = CronPushBridge(binding_store=CronPushBindingStore(tmp_path / "cron-push.json"))
    scheduler_one = _Scheduler()
    scheduler_two = _Scheduler()

    bridge.install_scheduler_sink(scheduler_one)
    bridge.install_scheduler_sink(scheduler_two)

    assert scheduler_one.notification_sink is not None
    assert scheduler_two.notification_sink is not None
    assert scheduler_one.notification_sink is not scheduler_two.notification_sink


def test_bridge_uninstall_scheduler_sink_restores_previous_sink(tmp_path: Path) -> None:
    class _Scheduler:
        def __init__(self) -> None:
            self.notification_sink = None

    async def previous_sink(notification) -> None:
        return None

    bridge = CronPushBridge(binding_store=CronPushBindingStore(tmp_path / "cron-push.json"))
    scheduler = _Scheduler()
    scheduler.notification_sink = previous_sink

    bridge.install_scheduler_sink(scheduler)
    assert scheduler.notification_sink is not previous_sink

    bridge.uninstall_scheduler_sink(scheduler)
    assert scheduler.notification_sink is previous_sink


def test_bridge_silent_terminal_notification_cleans_binding_without_sending(
    tmp_path: Path,
) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")
    bridge = CronPushBridge(binding_store=store)
    store.upsert(
        "job-main",
        {
            "channel": "wechat",
            "target": {"chat_id": "chat-1"},
        },
    )
    sent: list[str] = []

    async def sender(binding, text: str, notification) -> None:
        sent.append(text)

    bridge.register_sender("wechat", sender)

    asyncio.run(
        bridge.publish_notification(
            {
                "job_id": "job-main",
                "summary": 'Cron task "静默任务" completed',
                "detail": "仅清理绑定",
                "next_run_at": None,
                "user_visible": False,
            }
        )
    )

    assert sent == []
    assert store.get("job-main") is None


def test_bridge_terminal_notification_cleans_binding_even_when_sender_raises(
    tmp_path: Path,
) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")
    bridge = CronPushBridge(binding_store=store)
    store.upsert(
        "job-main",
        {
            "channel": "wechat",
            "target": {"chat_id": "chat-1"},
        },
    )

    async def sender(binding, text: str, notification) -> None:
        raise RuntimeError("send failed")

    bridge.register_sender("wechat", sender)

    asyncio.run(
        bridge.publish_notification(
            {
                "job_id": "job-main",
                "summary": 'Cron task "失败任务" completed',
                "detail": "发送失败也要清理绑定",
                "next_run_at": None,
            }
        )
    )

    assert store.get("job-main") is None


def test_bridge_recurring_notification_preserves_binding(tmp_path: Path) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")
    bridge = CronPushBridge(binding_store=store)
    store.upsert(
        "job-main",
        {
            "channel": "wechat",
            "target": {"chat_id": "chat-1"},
        },
    )
    sent: list[str] = []

    async def sender(binding, text: str, notification) -> None:
        sent.append(text)

    bridge.register_sender("wechat", sender)

    asyncio.run(
        bridge.publish_notification(
            {
                "job_id": "job-main",
                "summary": 'Cron task "喝水提醒" completed',
                "detail": "提醒我喝水",
                "next_run_at": "2026-04-28T09:00:00+08:00",
            }
        )
    )

    assert sent == [
        'Cron task "喝水提醒" completed\n提醒我喝水\n下次执行：2026-04-28T09:00:00+08:00'
    ]
    assert store.get("job-main") == {
        "job_id": "job-main",
        "channel": "wechat",
        "target": {"chat_id": "chat-1"},
    }
