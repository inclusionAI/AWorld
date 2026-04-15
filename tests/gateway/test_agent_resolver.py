from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.agent_resolver import AgentResolver


def test_resolve_prefers_explicit_agent_id():
    resolver = AgentResolver(global_default_agent_id="global-default")

    resolved = resolver.resolve(
        explicit_agent_id="explicit",
        session_agent_id="session",
        channel_default_agent_id="channel",
        matched_route_agent_id="route",
    )

    assert resolved == "explicit"


def test_resolve_falls_back_by_priority_order():
    resolver = AgentResolver(global_default_agent_id="global-default")

    assert resolver.resolve(
        explicit_agent_id=None,
        session_agent_id="session",
        channel_default_agent_id="channel",
        matched_route_agent_id="route",
    ) == "session"

    assert resolver.resolve(
        explicit_agent_id=None,
        session_agent_id=None,
        channel_default_agent_id="channel",
        matched_route_agent_id="route",
    ) == "channel"

    assert resolver.resolve(
        explicit_agent_id=None,
        session_agent_id=None,
        channel_default_agent_id=None,
        matched_route_agent_id="route",
    ) == "route"

    assert resolver.resolve(
        explicit_agent_id=None,
        session_agent_id=None,
        channel_default_agent_id=None,
        matched_route_agent_id=None,
    ) == "global-default"
