"""History backends for the session kernel; the default needs only stdlib."""

from __future__ import annotations

import json
from copy import deepcopy

from aworld.core.context.simple import ContextEntry as SessionEntry


class InMemoryContextStorage:
    def __init__(self) -> None:
        self._entries: dict[str, list[SessionEntry]] = {}

    def append(self, session_id: str, entry: SessionEntry) -> None:
        self._entries.setdefault(session_id, []).append(deepcopy(entry))

    def read(self, session_id: str) -> tuple[SessionEntry, ...]:
        return deepcopy(tuple(self._entries.get(session_id, ())))


class MemoryStoreAdapter:
    """Reuse an existing MemoryStore without its global MemoryFactory.

    Core history is isolated with a metadata namespace. Payloads must be JSON
    values; live runs and event subscriptions remain owned by Session.
    """

    def __init__(self, memory_store, *, item_factory) -> None:
        self._store = memory_store
        self._item_factory = item_factory

    def append(self, session_id: str, entry: SessionEntry) -> None:
        content = json.dumps(
            {"run_id": entry.run_id, "kind": entry.kind, "data": entry.data},
            ensure_ascii=False, allow_nan=False,
        )
        self._store.add(self._item_factory(
            content=content, memory_type="message",
            metadata={"session_id": session_id, "aworld_history": "1"},
        ))

    def read(self, session_id: str) -> tuple[SessionEntry, ...]:
        entries = []
        for item in self._store.get_all(filters={"session_id": session_id, "memory_type": "message"}):
            if item.metadata.get("aworld_history") != "1":
                continue
            data = json.loads(item.content)
            entries.append(SessionEntry(data["run_id"], data["kind"], data["data"]))
        return tuple(entries)
