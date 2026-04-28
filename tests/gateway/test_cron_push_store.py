from __future__ import annotations

import json
from pathlib import Path

from aworld_gateway.cron_push import (
    CronNotificationFormatter,
    CronPushBindingStore,
)


def test_store_round_trips_binding_payload_and_injects_job_id(tmp_path: Path) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")

    store.upsert(
        "job-main",
        {
            "channel": "wechat",
            "account_id": "wx-account",
            "conversation_id": "chat-1",
            "sender_id": "user-1",
            "target": {"chat_id": "chat-1"},
            "meta": {"created_from": "cron_tool"},
        },
    )

    assert store.get("job-main") == {
        "job_id": "job-main",
        "channel": "wechat",
        "account_id": "wx-account",
        "conversation_id": "chat-1",
        "sender_id": "user-1",
        "target": {"chat_id": "chat-1"},
        "meta": {"created_from": "cron_tool"},
    }


def test_store_ignores_invalid_json_file(tmp_path: Path) -> None:
    path = tmp_path / "cron-push.json"
    path.write_text("{not-json", encoding="utf-8")

    store = CronPushBindingStore(path)

    assert store.get("missing") is None


def test_store_remove_deletes_binding(tmp_path: Path) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")
    store.upsert("job-main", {"channel": "dingtalk", "target": {"session_webhook": "https://callback"}})

    store.remove("job-main")

    assert store.get("job-main") is None


def test_store_blank_job_id_is_noop_for_upsert_and_remove(tmp_path: Path) -> None:
    path = tmp_path / "cron-push.json"
    store = CronPushBindingStore(path)

    store.upsert("", {"channel": "wechat", "target": {"chat_id": "chat-1"}})
    store.remove("   ")

    assert not path.exists()
    assert store.get("") is None


def test_store_ignores_malformed_binding_loaded_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "cron-push.json"
    path.write_text(
        json.dumps(
            {
                "job-good": {
                    "job_id": "job-good",
                    "channel": "wechat",
                    "target": {"chat_id": "chat-1"},
                },
                "job-bad-scalar": {
                    "job_id": "job-bad-scalar",
                    "channel": 123,
                },
                "job-bad-target": {
                    "job_id": "job-bad-target",
                    "channel": "wechat",
                    "target": "chat-1",
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    store = CronPushBindingStore(path)

    assert store.get("job-good") == {
        "job_id": "job-good",
        "channel": "wechat",
        "target": {"chat_id": "chat-1"},
    }
    assert store.get("job-bad-scalar") is None
    assert store.get("job-bad-target") is None


def test_store_upsert_uses_atomic_replace_and_round_trips(tmp_path: Path, monkeypatch) -> None:
    store_path = tmp_path / "cron-push.json"
    store = CronPushBindingStore(store_path)
    replace_calls: list[tuple[str, str]] = []
    original_replace = Path.replace

    def tracked_replace(self: Path, target: Path) -> Path:
        replace_calls.append((str(self), str(target)))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", tracked_replace)

    store.upsert(
        "job-main",
        {
            "channel": "wechat",
            "target": {"chat_id": "chat-1"},
            "meta": {"source": "test"},
        },
    )

    assert store.get("job-main") == {
        "job_id": "job-main",
        "channel": "wechat",
        "target": {"chat_id": "chat-1"},
        "meta": {"source": "test"},
    }
    assert replace_calls == [
        (str(tmp_path / "cron-push.json.tmp"), str(store_path)),
    ]


def test_store_upsert_ignores_invalid_in_memory_binding(tmp_path: Path) -> None:
    store = CronPushBindingStore(tmp_path / "cron-push.json")

    store.upsert("job-missing-channel", {"target": {"chat_id": "chat-1"}})
    store.upsert("job-bad-target", {"channel": "wechat", "target": "chat-1"})  # type: ignore[arg-type]

    assert store.get("job-missing-channel") is None
    assert store.get("job-bad-target") is None


def test_formatter_renders_summary_detail_and_next_run() -> None:
    text = CronNotificationFormatter.format(
        {
            "summary": 'Cron task "喝水提醒" completed',
            "detail": "提醒我喝水",
            "next_run_at": "2026-04-28T09:00:00+08:00",
        }
    )

    assert text == 'Cron task "喝水提醒" completed\n提醒我喝水\n下次执行：2026-04-28T09:00:00+08:00'


def test_formatter_skips_duplicate_detail() -> None:
    text = CronNotificationFormatter.format(
        {
            "summary": 'Cron task "喝水提醒" completed',
            "detail": 'Cron task "喝水提醒" completed',
        }
    )

    assert text == 'Cron task "喝水提醒" completed'
