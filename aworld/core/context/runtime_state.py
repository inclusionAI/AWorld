"""Task-scoped runtime state shared by Context transport copies.

Amni's ``WorkingState`` provides the typed shape used for task continuation, but
normal event transport deep-copies that state.  This small sidecar is the
in-process coordination layer for values that must fan in across those copies.
It is deliberately not serialized into checkpoints or prompts.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Callable


class TaskRuntimeStateRegistry:
    """Thread-safe, task/namespace-scoped runtime sidecar."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[tuple[str | None, str, str], Any] = {}
        self._claimed_tokens: set[tuple[str | None, str, str]] = set()

    @staticmethod
    def _key(
        task_id: str | None, namespace: str, key: str
    ) -> tuple[str | None, str, str]:
        return task_id, str(namespace), str(key)

    def read(self, task_id: str | None, namespace: str, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._values.get(self._key(task_id, namespace, key)))

    def write(
        self, task_id: str | None, namespace: str, key: str, value: Any
    ) -> None:
        with self._lock:
            self._values[self._key(task_id, namespace, key)] = copy.deepcopy(value)

    def update(
        self,
        task_id: str | None,
        namespace: str,
        key: str,
        updater: Callable[[Any], Any],
    ) -> Any:
        with self._lock:
            registry_key = self._key(task_id, namespace, key)
            current = copy.deepcopy(self._values.get(registry_key))
            updated = updater(current)
            self._values[registry_key] = copy.deepcopy(updated)
            return copy.deepcopy(updated)

    def claim_token(
        self, task_id: str | None, namespace: str, token: str
    ) -> bool:
        """Return true exactly once for a task-scoped continuation token."""
        claim = (task_id, str(namespace), str(token))
        with self._lock:
            if claim in self._claimed_tokens:
                return False
            self._claimed_tokens.add(claim)
            return True


__all__ = ["TaskRuntimeStateRegistry"]
