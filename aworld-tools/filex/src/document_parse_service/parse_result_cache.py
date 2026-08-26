"""Container-local result cache and single-flight coordination for FileX parsing."""

from __future__ import annotations

import asyncio
import copy
import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


CACHE_KEY_VERSION = "1"
DEFAULT_TTL_SECONDS = 3600
DEFAULT_MAX_ENTRIES = 128
_SECRET_MARKERS = ("secret", "token", "password", "api_key", "access_key")


@dataclass
class _CacheEntry:
    result: dict[str, Any]
    created_at: float


class ParseResultCache:
    """Bounded disk LRU cache with per-key cross-process single-flight execution."""

    def __init__(self, cache_root: Path) -> None:
        self._cache_root = cache_root
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_compute(
        self,
        *,
        key: str,
        task_id: str | None,
        compute: Callable[[], Awaitable[dict[str, Any]]],
        ttl_seconds: int,
        max_entries: int,
        force_refresh: bool,
    ) -> dict[str, Any]:
        lookup_started_at = time.perf_counter()
        if not force_refresh:
            cached = self._get(key, ttl_seconds)
            if cached is not None:
                return self._hit_result(cached, task_id, lookup_started_at, 0)

        wait_started_at = time.perf_counter()
        process_lock = self._locks.setdefault(key, asyncio.Lock())
        async with process_lock:
            lock_file = await self._acquire_file_lock(key)
            try:
                wait_duration_ms = int((time.perf_counter() - wait_started_at) * 1000)
                if not force_refresh:
                    cached = self._get(key, ttl_seconds)
                    if cached is not None:
                        return self._hit_result(
                            cached,
                            task_id,
                            lookup_started_at,
                            wait_duration_ms,
                        )

                lookup_duration_ms = int((time.perf_counter() - lookup_started_at) * 1000)
                result = await compute()
                cache_metrics = {
                    "status": "miss",
                    "forced_refresh": bool(force_refresh),
                    "key_version": CACHE_KEY_VERSION,
                    "lookup_duration_ms": lookup_duration_ms,
                    "single_flight_wait_duration_ms": wait_duration_ms,
                    "age_ms": 0,
                    "saved_duration_ms": 0,
                }
                self._set_cache_metrics(result, cache_metrics)
                if result.get("success"):
                    self._put(key, result, max_entries)
                return result
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()

    def _get(self, key: str, ttl_seconds: int) -> _CacheEntry | None:
        entry_path = self._entry_path(key)
        try:
            payload = json.loads(entry_path.read_text(encoding="utf-8"))
            entry = _CacheEntry(
                result=payload["result"],
                created_at=float(payload["created_at"]),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if time.time() - entry.created_at > ttl_seconds:
            entry_path.unlink(missing_ok=True)
            return None
        os.utime(entry_path, None)
        return entry

    def _put(self, key: str, result: dict[str, Any], max_entries: int) -> None:
        self._cache_root.mkdir(parents=True, exist_ok=True)
        entry_path = self._entry_path(key)
        temporary_path = entry_path.with_suffix(f".{os.getpid()}.tmp")
        payload = {"created_at": time.time(), "result": result}
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        temporary_path.replace(entry_path)
        entries = sorted(
            self._cache_root.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
        )
        for stale_path in entries[: -max(1, max_entries)]:
            stale_path.unlink(missing_ok=True)

    async def _acquire_file_lock(self, key: str):
        self._cache_root.mkdir(parents=True, exist_ok=True)
        lock_file = self._lock_path(key).open("a+")
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock_file
            except BlockingIOError:
                await asyncio.sleep(0.05)

    def _entry_path(self, key: str) -> Path:
        return self._cache_root / f"{key}.json"

    def _lock_path(self, key: str) -> Path:
        return self._cache_root / f"{key}.lock"

    def _hit_result(
        self,
        entry: _CacheEntry,
        task_id: str | None,
        lookup_started_at: float,
        wait_duration_ms: int,
    ) -> dict[str, Any]:
        result = copy.deepcopy(entry.result)
        source_task_id = result.get("task_id")
        if task_id:
            result["task_id"] = task_id
        age_ms = int((time.time() - entry.created_at) * 1000)
        metrics = result.setdefault("metrics", {})
        timings = metrics.get("timings_ms") or {}
        model = metrics.get("model") or {}
        original_total_ms = int(timings.get("total") or 0)
        lookup_duration_ms = int((time.perf_counter() - lookup_started_at) * 1000)
        self._set_cache_metrics(
            result,
            {
                "status": "hit",
                "forced_refresh": False,
                "key_version": CACHE_KEY_VERSION,
                "lookup_duration_ms": lookup_duration_ms,
                "single_flight_wait_duration_ms": wait_duration_ms,
                "age_ms": age_ms,
                "saved_duration_ms": original_total_ms,
                "source_task_id": source_task_id or "",
                "source_timings_ms": copy.deepcopy(timings),
                "source_model": copy.deepcopy(model),
            },
        )
        metrics["timings_ms"] = {
            key: lookup_duration_ms if key == "total" else 0
            for key in timings
        }
        metrics["timings_ms"].setdefault("total", lookup_duration_ms)
        if model:
            metrics["model"] = {
                **model,
                "call_count": 0,
                "retry_count": 0,
                "peak_concurrency": 0,
                "timeout_count": 0,
            }
        result["message"] = "Document parse cache hit"
        return result

    @staticmethod
    def _set_cache_metrics(result: dict[str, Any], cache_metrics: dict[str, Any]) -> None:
        metrics = result.setdefault("metrics", {})
        metrics["cache"] = cache_metrics


def build_parse_cache_key(
    *,
    file_id: str,
    workspace_path: str,
    file_type: str | None,
    asset_reference_mode: str,
    env_content: dict[str, Any],
) -> str:
    source: dict[str, Any]
    if file_id:
        source = {"file_id": file_id}
    else:
        path = Path(workspace_path)
        stat = path.stat()
        source = {
            "workspace_path": str(path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    payload = {
        "key_version": CACHE_KEY_VERSION,
        "source": source,
        "file_type": (file_type or "").lower(),
        "asset_reference_mode": asset_reference_mode,
        "env_content": _redact_secrets(env_content),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if _is_secret_key(key) else _redact_secrets(child)
            for key, child in sorted(value.items())
            if key
            not in {
                "filex_cache_enabled",
                "filex_cache_ttl_seconds",
                "filex_cache_max_entries",
            }
        }
    if isinstance(value, list):
        return [_redact_secrets(child) for child in value]
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in _SECRET_MARKERS)
