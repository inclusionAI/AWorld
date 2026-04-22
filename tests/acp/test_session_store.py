from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.session_store import AcpSessionStore


def test_new_session_creates_stable_mapping() -> None:
    store = AcpSessionStore()

    record = store.create_session(cwd="/tmp/demo", requested_mcp_servers=[])

    assert record.acp_session_id
    assert record.aworld_session_id
    assert store.get(record.acp_session_id) is record


def test_missing_session_returns_none() -> None:
    store = AcpSessionStore()

    assert store.get("missing") is None
