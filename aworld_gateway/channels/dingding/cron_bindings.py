from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class DingdingCronBindingStore:
    def __init__(self, file_path: Path | str) -> None:
        self._file_path = Path(file_path)
        self._lock = threading.Lock()

    def upsert(self, job_id: str, binding: dict[str, Any]) -> None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return

        payload = dict(binding)
        payload["job_id"] = normalized_job_id

        with self._lock:
            data = self._read_unlocked()
            data[normalized_job_id] = payload
            self._write_unlocked(data)

    def get(self, job_id: str) -> dict[str, Any] | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None

        with self._lock:
            data = self._read_unlocked()
            binding = data.get(normalized_job_id)
            return dict(binding) if isinstance(binding, dict) else None

    def remove(self, job_id: str) -> None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return

        with self._lock:
            data = self._read_unlocked()
            if normalized_job_id not in data:
                return
            data.pop(normalized_job_id, None)
            self._write_unlocked(data)

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self._file_path.exists():
            return {}
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(job_id): value
            for job_id, value in raw.items()
            if isinstance(value, dict)
        }

    def _write_unlocked(self, data: dict[str, dict[str, Any]]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class DingdingCronNotifier:
    def __init__(self, connector, binding_store: DingdingCronBindingStore) -> None:
        self._connector = connector
        self._binding_store = binding_store

    async def publish(self, notification: dict[str, Any]) -> None:
        job_id = str(notification.get("job_id") or "").strip()
        if not job_id:
            return

        binding = self._binding_store.get(job_id)
        if not binding:
            return

        session_webhook = str(binding.get("session_webhook") or "").strip()
        if not session_webhook:
            return

        user_visible = notification.get("user_visible") is not False
        text = self._format_notification_text(notification)
        if user_visible and text:
            await self._connector.send_text(session_webhook=session_webhook, text=text)

        if not notification.get("next_run_at"):
            self._binding_store.remove(job_id)

    @staticmethod
    def _format_notification_text(notification: dict[str, Any]) -> str:
        summary = str(notification.get("summary") or "").strip()
        detail = str(notification.get("detail") or "").strip()
        next_run_at = str(notification.get("next_run_at") or "").strip()

        lines: list[str] = []
        if summary:
            lines.append(summary)
        if detail and detail != summary:
            lines.append(detail)
        if next_run_at:
            lines.append(f"下次执行：{next_run_at}")

        return "\n".join(lines).strip()
