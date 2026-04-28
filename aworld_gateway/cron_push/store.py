from __future__ import annotations

import json
import threading
from pathlib import Path

from aworld_gateway.cron_push.types import (
    CronPushBinding,
    copy_cron_push_binding,
    with_cron_push_job_id,
)


class CronPushBindingStore:
    def __init__(self, file_path: Path | str) -> None:
        self._file_path = Path(file_path)
        self._lock = threading.Lock()

    def upsert(self, job_id: str, binding: CronPushBinding) -> None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return

        payload = with_cron_push_job_id(normalized_job_id, binding)

        with self._lock:
            data = self._read_unlocked()
            data[normalized_job_id] = payload
            self._write_unlocked(data)

    def get(self, job_id: str) -> CronPushBinding | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None

        with self._lock:
            data = self._read_unlocked()
            binding = data.get(normalized_job_id)
            if not isinstance(binding, dict):
                return None
            return copy_cron_push_binding(binding)

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

    def _read_unlocked(self) -> dict[str, CronPushBinding]:
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

    def _write_unlocked(self, data: dict[str, CronPushBinding]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
