from __future__ import annotations

import json
from pathlib import Path


def _account_file(root: Path, account_id: str) -> Path:
    return root / f"{account_id}.json"


def save_account(
    root: Path,
    *,
    account_id: str,
    token: str,
    base_url: str,
    user_id: str = "",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": token,
        "base_url": base_url,
        "user_id": user_id,
    }
    path = _account_file(root, account_id)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_account(root: Path, account_id: str) -> dict[str, str] | None:
    path = _account_file(root, account_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "token": str(payload.get("token") or ""),
        "base_url": str(payload.get("base_url") or ""),
        "user_id": str(payload.get("user_id") or ""),
    }
