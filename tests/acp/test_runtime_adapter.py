from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.runtime_adapter import normalize_tool_end


def test_tool_result_without_prior_start_gets_synthetic_turn_scoped_id() -> None:
    state: dict[str, object] = {}

    event = normalize_tool_end(
        state,
        native_id=None,
        tool_name="shell",
        status="completed",
        payload={"ok": True},
    )

    assert event["event_type"] == "tool_end"
    assert event["tool_call_id"].startswith("acp_tool_")


def test_tool_result_preserves_native_id_when_present() -> None:
    state: dict[str, object] = {}

    event = normalize_tool_end(
        state,
        native_id="native-tool-1",
        tool_name="shell",
        status="completed",
        payload={"ok": True},
    )

    assert event["tool_call_id"] == "native-tool-1"
    assert event["tool_name"] == "shell"
