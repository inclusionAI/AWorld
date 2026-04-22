from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.self_test import build_summary


def test_build_summary_is_machine_checkable() -> None:
    summary = build_summary(
        cases=[
            {"id": "initialize_handshake", "ok": True},
            {"id": "new_session_usable", "ok": False, "detail": "boom"},
        ]
    )

    assert summary["ok"] is False
    assert summary["summary"]["passed"] == 1
    assert summary["summary"]["failed"] == 1
    assert summary["cases"][1]["id"] == "new_session_usable"
