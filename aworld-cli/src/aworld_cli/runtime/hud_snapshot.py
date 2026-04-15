"""Runtime HUD snapshot store."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
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

    def update(self, **sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
        for name, payload in sections.items():
            if not payload:
                continue
            bucket = self._snapshot.setdefault(name, {})
            bucket.update(payload)
        return self.snapshot()

    def settle(self, task_status: str = "idle") -> dict[str, dict[str, Any]]:
        task_bucket = self._snapshot.setdefault("task", {})
        activity_bucket = self._snapshot.setdefault("activity", {})
        task_bucket["status"] = task_status
        activity_bucket["current_tool"] = None
        return self.snapshot()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._snapshot)
