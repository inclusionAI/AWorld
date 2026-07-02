from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.errors import AWORLD_ACP_UNSUPPORTED_MCP_SERVERS
from aworld_cli.acp.session_runtime import (
    apply_requested_mcp_servers,
    extract_swarm_agents,
    normalize_requested_mcp_servers,
)


def test_normalize_requested_mcp_servers_accepts_list_form() -> None:
    names, payload = normalize_requested_mcp_servers(
        [{"name": "demo", "command": "demo-server", "args": ["--help"]}]
    )

    assert names == ["demo"]
    assert payload == {
        "mcpServers": {
            "demo": {"command": "demo-server", "args": ["--help"]},
        }
    }


def test_normalize_requested_mcp_servers_accepts_mapping_form() -> None:
    names, payload = normalize_requested_mcp_servers(
        {
            "mcpServers": {
                "demo": {"command": "demo-server"},
                "search": {"command": "search-server"},
            }
        }
    )

    assert names == ["demo", "search"]
    assert payload == {
        "mcpServers": {
            "demo": {"command": "demo-server"},
            "search": {"command": "search-server"},
        }
    }


@pytest.mark.parametrize(
    "payload",
    [
        "bad",
        [{"name": "", "command": "demo-server"}],
        [{"name": "demo"}],
        {"mcpServers": []},
    ],
)
def test_normalize_requested_mcp_servers_rejects_unsupported_shapes(payload) -> None:
    with pytest.raises(ValueError, match=AWORLD_ACP_UNSUPPORTED_MCP_SERVERS):
        normalize_requested_mcp_servers(payload)


def test_apply_requested_mcp_servers_merges_and_restores_sandbox_state() -> None:
    class FakeSandbox:
        def __init__(self) -> None:
            self.mcp_config = {"mcpServers": {"base": {"command": "base"}}}
            self.mcp_servers = ["base"]

    class FakeAgent:
        def __init__(self, identifier: str) -> None:
            self._identifier = identifier
            self.sandbox = FakeSandbox()

        def id(self):
            return self._identifier

    class FakeSwarm:
        def __init__(self, agents):
            self.agent_graph = type("Graph", (), {"agents": {agent.id(): agent for agent in agents}})()

    first = FakeAgent("agent-1")
    second = FakeAgent("agent-2")

    restore = apply_requested_mcp_servers(
        FakeSwarm([first, second]),
        [{"name": "demo", "command": "demo-server"}],
    )

    assert first.sandbox.mcp_servers == ["base", "demo"]
    assert second.sandbox.mcp_servers == ["base", "demo"]
    assert first.sandbox.mcp_config["mcpServers"]["demo"] == {"command": "demo-server"}
    assert second.sandbox.mcp_config["mcpServers"]["demo"] == {"command": "demo-server"}

    restore()

    assert first.sandbox.mcp_servers == ["base"]
    assert second.sandbox.mcp_servers == ["base"]
    assert first.sandbox.mcp_config == {"mcpServers": {"base": {"command": "base"}}}
    assert second.sandbox.mcp_config == {"mcpServers": {"base": {"command": "base"}}}


def test_apply_requested_mcp_servers_rejects_when_swarm_has_no_sandbox_agents() -> None:
    class FakeAgent:
        def id(self):
            return "agent-1"

    class FakeSwarm:
        topology = [FakeAgent()]

    with pytest.raises(ValueError, match=AWORLD_ACP_UNSUPPORTED_MCP_SERVERS):
        apply_requested_mcp_servers(
            FakeSwarm(),
            [{"name": "demo", "command": "demo-server"}],
        )


def test_extract_swarm_agents_deduplicates_by_agent_id() -> None:
    class FakeAgent:
        def __init__(self, identifier: str) -> None:
            self._identifier = identifier

        def id(self):
            return self._identifier

    agent = FakeAgent("agent-1")
    swarm = type(
        "FakeSwarm",
        (),
        {"agents": [agent, agent], "communicate_agent": agent},
    )()

    agents = extract_swarm_agents(swarm)

    assert agents == [agent]
