from __future__ import annotations

import json
from pathlib import Path


class ContextTokenStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, str] = {}

    def _path(self, account_id: str) -> Path:
        return self._root / f"{account_id}.context-tokens.json"

    @staticmethod
    def _key(account_id: str, peer_id: str) -> str:
        return f"{account_id}:{peer_id}"

    def restore(self, account_id: str) -> None:
        path = self._path(account_id)
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        for peer_id, token in payload.items():
            if isinstance(token, str) and token:
                self._cache[self._key(account_id, str(peer_id))] = token

    def get(self, account_id: str, peer_id: str) -> str | None:
        return self._cache.get(self._key(account_id, peer_id))

    def set(self, account_id: str, peer_id: str, token: str) -> None:
        self._cache[self._key(account_id, peer_id)] = token
        self._persist(account_id)

    def _persist(self, account_id: str) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        prefix = f"{account_id}:"
        payload = {
            key[len(prefix) :]: value
            for key, value in self._cache.items()
            if key.startswith(prefix)
        }
        self._path(account_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
