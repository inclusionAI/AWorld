"""Runtime HUD snapshot store."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


DEFAULT_BUCKETS = (
    "workspace",
    "session",
    "task",
    "activity",
    "usage",
    "notifications",
    "vcs",
    "plugins",
)


@dataclass
class HudSnapshotStore:
    _snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock)

    def update(self, **sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
        with self._lock:
            for name, payload in sections.items():
                if not payload:
                    continue
                bucket = self._snapshot.setdefault(name, {})
                bucket.update(payload)
            return deepcopy(self._snapshot)

    def settle(self, task_status: str = "idle") -> dict[str, dict[str, Any]]:
        with self._lock:
            task_bucket = self._snapshot.setdefault("task", {})
            activity_bucket = self._snapshot.setdefault("activity", {})
            task_bucket["status"] = task_status
            activity_bucket["current_tool"] = None
            return deepcopy(self._snapshot)

    def reset_for_session(self, session_id: str | None = None) -> dict[str, dict[str, Any]]:
        with self._lock:
            snapshot: dict[str, dict[str, Any]] = {
                "task": {"status": "idle"},
                "activity": {"current_tool": None, "recent_tools": [], "tool_calls_count": 0},
                "usage": {},
            }
            if session_id:
                snapshot["session"] = {"session_id": session_id}
            self._snapshot = snapshot
            return deepcopy(self._snapshot)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return deepcopy(self._snapshot)
