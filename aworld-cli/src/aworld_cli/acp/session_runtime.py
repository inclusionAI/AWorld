from __future__ import annotations

import copy
from typing import Any, Callable

from .errors import AWORLD_ACP_UNSUPPORTED_MCP_SERVERS


def normalize_requested_mcp_servers(
    requested_mcp_servers: Any,
) -> tuple[list[str], dict[str, Any]]:
    if not requested_mcp_servers:
        return [], {"mcpServers": {}}

    if isinstance(requested_mcp_servers, dict):
        servers = requested_mcp_servers.get("mcpServers")
        if not isinstance(servers, dict):
            raise ValueError(AWORLD_ACP_UNSUPPORTED_MCP_SERVERS)
        return list(servers.keys()), {"mcpServers": copy.deepcopy(servers)}

    if not isinstance(requested_mcp_servers, list):
        raise ValueError(AWORLD_ACP_UNSUPPORTED_MCP_SERVERS)

    normalized: dict[str, Any] = {}
    for item in requested_mcp_servers:
        if not isinstance(item, dict):
            raise ValueError(AWORLD_ACP_UNSUPPORTED_MCP_SERVERS)

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(AWORLD_ACP_UNSUPPORTED_MCP_SERVERS)

        raw_config = item.get("config")
        if raw_config is None:
            raw_config = {key: value for key, value in item.items() if key != "name"}

        if not isinstance(raw_config, dict) or not raw_config:
            raise ValueError(AWORLD_ACP_UNSUPPORTED_MCP_SERVERS)

        normalized[name.strip()] = copy.deepcopy(raw_config)

    return list(normalized.keys()), {"mcpServers": normalized}


def apply_requested_mcp_servers(
    swarm: Any,
    requested_mcp_servers: Any,
) -> Callable[[], None]:
    requested_names, requested_config = normalize_requested_mcp_servers(requested_mcp_servers)
    if not requested_names:
        return lambda: None

    snapshots: list[tuple[Any, dict[str, Any], list[str]]] = []
    for agent in extract_swarm_agents(swarm):
        sandbox = getattr(agent, "sandbox", None)
        if sandbox is None:
            continue

        original_config = copy.deepcopy(getattr(sandbox, "mcp_config", None) or {})
        original_config.setdefault("mcpServers", {})
        original_servers = list(getattr(sandbox, "mcp_servers", None) or [])

        merged_config = copy.deepcopy(original_config)
        merged_config.setdefault("mcpServers", {})
        for server_name, server_config in requested_config["mcpServers"].items():
            merged_config["mcpServers"][server_name] = copy.deepcopy(server_config)

        merged_servers = list(dict.fromkeys([*original_servers, *requested_names]))

        sandbox.mcp_config = merged_config
        sandbox.mcp_servers = merged_servers
        snapshots.append((sandbox, original_config, original_servers))

    if not snapshots:
        raise ValueError(AWORLD_ACP_UNSUPPORTED_MCP_SERVERS)

    def restore() -> None:
        for sandbox, original_config, original_servers in reversed(snapshots):
            sandbox.mcp_config = original_config
            sandbox.mcp_servers = original_servers

    return restore


def extract_swarm_agents(swarm: Any) -> list[Any]:
    agents: list[Any] = []

    agent_graph = getattr(swarm, "agent_graph", None)
    graph_agents = getattr(agent_graph, "agents", None)
    if isinstance(graph_agents, dict) and graph_agents:
        agents.extend(graph_agents.values())
    elif isinstance(graph_agents, (list, tuple)) and graph_agents:
        agents.extend(graph_agents)

    swarm_agents = getattr(swarm, "agents", None)
    if not agents and isinstance(swarm_agents, dict) and swarm_agents:
        agents.extend(swarm_agents.values())
    elif not agents and isinstance(swarm_agents, (list, tuple)) and swarm_agents:
        agents.extend(swarm_agents)
    elif not agents and swarm_agents is not None:
        agents.append(swarm_agents)

    topology = getattr(swarm, "topology", None)
    if not agents and isinstance(topology, (list, tuple)):
        for item in topology:
            if isinstance(item, (list, tuple)):
                agents.extend(part for part in item if part is not None)
            elif item is not None:
                agents.append(item)

    communicate_agent = getattr(swarm, "communicate_agent", None)
    if not agents and isinstance(communicate_agent, (list, tuple)):
        agents.extend(part for part in communicate_agent if part is not None)
    elif not agents and communicate_agent is not None:
        agents.append(communicate_agent)

    unique_agents: list[Any] = []
    seen: set[str] = set()
    for agent in agents:
        agent_id = None
        identifier = getattr(agent, "id", None)
        if callable(identifier):
            try:
                agent_id = str(identifier())
            except Exception:
                agent_id = None
        if agent_id is None:
            agent_id = str(id(agent))
        if agent_id in seen:
            continue
        seen.add(agent_id)
        unique_agents.append(agent)

    return unique_agents
