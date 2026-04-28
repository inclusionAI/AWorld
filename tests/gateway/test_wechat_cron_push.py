from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld.output.base import ToolResultOutput

from aworld_gateway.config import WechatChannelConfig
from aworld_gateway.types import OutboundEnvelope


class _CronRouter:
    def __init__(self, output_payload: dict[str, object]) -> None:
        self._output_payload = output_payload

    async def handle_inbound(self, inbound, *, channel_default_agent_id, on_output=None):
        if on_output is not None:
            await on_output(
                ToolResultOutput(
                    tool_name="cron",
                    action_name="cron_tool",
                    data=self._output_payload,
                )
            )
        return OutboundEnvelope(
            channel="wechat",
            account_id=inbound.account_id,
            conversation_id=inbound.conversation_id,
            reply_to_message_id=inbound.message_id,
            text="已创建提醒",
        )


class _FakeScheduler:
    def __init__(self) -> None:
        self.notification_sink = None


def _expected_binding(*, job_id: str, conversation_id: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "channel": "wechat",
        "account_id": "wx-account",
        "conversation_id": conversation_id,
        "sender_id": "user-1",
        "target": {"chat_id": conversation_id},
    }


async def _start_connector(*, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, router: _CronRouter):
    from aworld_gateway.channels.wechat.connector import WechatConnector

    scheduler = _FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: scheduler)
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_WECHAT_BASE_URL", "https://ilink.example.test")

    sent: list[tuple[str, str, dict | None]] = []

    async def fake_send_text(*, chat_id: str, text: str, metadata: dict | None = None):
        sent.append((chat_id, text, metadata))
        return {"chat_id": chat_id, "text": text}

    connector = WechatConnector(
        config=WechatChannelConfig(default_agent_id="aworld"),
        router=router,
        storage_root=tmp_path,
    )
    connector.send_text = fake_send_text  # type: ignore[method-assign]
    await connector.start()
    return connector, scheduler, sent


@pytest.mark.asyncio
async def test_connector_process_message_binds_shared_cron_push_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    router = _CronRouter(
        {
            "success": True,
            "job_id": "job-main",
            "advance_reminder": {"job_id": "job-advance"},
        }
    )
    connector, scheduler, sent = await _start_connector(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        router=router,
    )

    await connector._process_message(
        {
            "message_id": "m-1",
            "from_user_id": "user-1",
            "item_list": [{"type": 1, "text_item": {"text": "一分钟后提醒我喝水"}}],
        }
    )

    assert scheduler.notification_sink is not None
    assert connector._cron_push_bridge._binding_store.get("job-main") == _expected_binding(
        job_id="job-main",
        conversation_id="user-1",
    )
    assert connector._cron_push_bridge._binding_store.get("job-advance") == _expected_binding(
        job_id="job-advance",
        conversation_id="user-1",
    )
    assert sent == [("user-1", "已创建提醒", {})]

    await connector.stop()


@pytest.mark.asyncio
async def test_connector_scheduler_notification_sends_wechat_text_and_clears_terminal_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    router = _CronRouter(
        {
            "success": True,
            "job_id": "job-main",
            "advance_reminder": {"job_id": "job-advance"},
        }
    )
    connector, scheduler, sent = await _start_connector(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        router=router,
    )

    await connector._process_message(
        {
            "message_id": "m-1",
            "from_user_id": "user-1",
            "item_list": [{"type": 1, "text_item": {"text": "一分钟后提醒我喝水"}}],
        }
    )

    await scheduler.notification_sink(
        {
            "job_id": "job-main",
            "summary": 'Cron task "喝水提醒" completed',
            "detail": "提醒我喝水",
            "next_run_at": None,
        }
    )

    assert sent == [
        ("user-1", "已创建提醒", {}),
        ("user-1", 'Cron task "喝水提醒" completed\n提醒我喝水', None),
    ]
    assert connector._cron_push_bridge._binding_store.get("job-main") is None
    assert connector._cron_push_bridge._binding_store.get("job-advance") == _expected_binding(
        job_id="job-advance",
        conversation_id="user-1",
    )

    await connector.stop()


@pytest.mark.asyncio
async def test_connector_recurring_scheduler_notification_preserves_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    router = _CronRouter({"success": True, "job_id": "job-main"})
    connector, scheduler, sent = await _start_connector(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        router=router,
    )

    await connector._process_message(
        {
            "message_id": "m-1",
            "from_user_id": "user-1",
            "item_list": [{"type": 1, "text_item": {"text": "每天九点提醒我喝水"}}],
        }
    )

    await scheduler.notification_sink(
        {
            "job_id": "job-main",
            "summary": 'Cron task "喝水提醒" completed',
            "detail": "提醒我喝水",
            "next_run_at": "2026-04-28T09:00:00+08:00",
        }
    )

    assert sent == [
        ("user-1", "已创建提醒", {}),
        (
            "user-1",
            'Cron task "喝水提醒" completed\n提醒我喝水\n下次执行：2026-04-28T09:00:00+08:00',
            None,
        ),
    ]
    assert connector._cron_push_bridge._binding_store.get("job-main") == _expected_binding(
        job_id="job-main",
        conversation_id="user-1",
    )

    await connector.stop()


@pytest.mark.asyncio
async def test_connector_stop_restores_previous_scheduler_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    async def previous_sink(notification) -> None:
        return None

    scheduler = _FakeScheduler()
    scheduler.notification_sink = previous_sink
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: scheduler)
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_WECHAT_BASE_URL", "https://ilink.example.test")

    connector = WechatConnector(
        config=WechatChannelConfig(default_agent_id="aworld"),
        router=None,
        storage_root=tmp_path,
    )
    await connector.start()

    assert scheduler.notification_sink is not previous_sink

    await connector.stop()

    assert scheduler.notification_sink is previous_sink
