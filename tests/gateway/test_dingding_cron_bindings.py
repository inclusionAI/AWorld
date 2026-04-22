# coding: utf-8
from pathlib import Path

from aworld_gateway.channels.dingding.cron_bindings import (
    DingdingCronBindingStore,
    DingdingCronNotifier,
)


def test_dingding_cron_notifier_cleans_up_silent_final_notification_without_sending_text(tmp_path: Path) -> None:
    """Silent terminal notifications should clear job bindings without producing user-visible DingTalk text."""

    class _FakeConnector:
        def __init__(self) -> None:
            self.sent = []

        async def send_text(self, *, session_webhook: str, text: str) -> None:
            self.sent.append((session_webhook, text))

    binding_store = DingdingCronBindingStore(tmp_path / "bindings.json")
    binding_store.upsert(
        "job-1",
        {
            "session_webhook": "https://callback",
            "conversation_id": "conv-1",
            "sender_id": "user-1",
        },
    )
    connector = _FakeConnector()
    notifier = DingdingCronNotifier(connector, binding_store)

    import asyncio

    asyncio.run(
        notifier.publish(
            {
                "job_id": "job-1",
                "summary": 'Cron task "silent recurring job" completed',
                "detail": "本次静默结束，仅用于清理绑定",
                "next_run_at": None,
                "user_visible": False,
            }
        )
    )

    assert connector.sent == []
    assert binding_store.get("job-1") is None
