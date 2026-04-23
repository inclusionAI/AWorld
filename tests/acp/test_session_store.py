from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.session_store import AcpSessionStore


def test_new_session_creates_stable_mapping(tmp_path: Path) -> None:
    store = AcpSessionStore()

    record = store.create_session(cwd=str(tmp_path), requested_mcp_servers=[])

    assert record.acp_session_id
    assert record.aworld_session_id
    assert record.cwd == str(tmp_path.resolve())
    assert store.get(record.acp_session_id) is record


def test_missing_session_returns_none() -> None:
    store = AcpSessionStore()

    assert store.get("missing") is None


def test_invalid_cwd_is_rejected(tmp_path: Path) -> None:
    store = AcpSessionStore()

    with pytest.raises(ValueError, match="AWORLD_ACP_INVALID_CWD"):
        store.create_session(
            cwd=str(tmp_path / "missing"),
            requested_mcp_servers=[],
        )


def test_invalid_requested_mcp_servers_are_rejected(tmp_path: Path) -> None:
    store = AcpSessionStore()

    with pytest.raises(ValueError, match="AWORLD_ACP_UNSUPPORTED_MCP_SERVERS"):
        store.create_session(
            cwd=str(tmp_path),
            requested_mcp_servers="bad-shape",
        )
