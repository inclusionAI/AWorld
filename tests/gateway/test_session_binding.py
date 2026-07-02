from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.session_binding import SessionBinding


def test_build_returns_stable_gateway_session_id():
    session_id = SessionBinding().build(
        agent_id="agent-1",
        channel="telegram",
        account_id="acct-9",
        conversation_type="group",
        conversation_id="conv-42",
    )

    assert session_id == "gw:agent-1:telegram:acct-9:group:conv-42"
