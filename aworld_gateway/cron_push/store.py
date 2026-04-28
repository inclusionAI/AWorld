from __future__ import annotations

import json
import threading
from pathlib import Path

from aworld_gateway.cron_push.types import (
    CronPushBinding,
    normalize_cron_push_binding,
)


class CronPushBindingStore:
    def __init__(self, file_path: Path | str) -> None:
        self._file_path = Path(file_path)
        self._lock = threading.Lock()

    def upsert(self, job_id: str, binding: CronPushBinding) -> None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return

        payload = normalize_cron_push_binding(binding, job_id=normalized_job_id)
        if payload is None:
            return

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
            return normalize_cron_push_binding(binding, job_id=normalized_job_id)

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

        data: dict[str, CronPushBinding] = {}
        for job_id, value in raw.items():
            normalized_job_id = str(job_id or "").strip()
            if not normalized_job_id:
                continue

            binding = normalize_cron_push_binding(value, job_id=normalized_job_id)
            if binding is None:
                continue

            data[normalized_job_id] = binding

        return data

    def _write_unlocked(self, data: dict[str, CronPushBinding]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._file_path.with_name(f"{self._file_path.name}.tmp")
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self._file_path)
