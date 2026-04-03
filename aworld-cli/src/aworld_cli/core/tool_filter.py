"""
Tool filtering utilities for slash commands.

Implements tool whitelist filtering to restrict agent access to specific tools
during command execution.
"""
import fnmatch
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

from aworld.logs.util import logger


def matches_pattern(tool_name: str, pattern: str) -> bool:
    """
    Check if tool name matches a pattern.

    Supports:
    - Exact match: "git_status"
    - Wildcard: "git_*" matches "git_status", "git_diff", etc.
    - Nested wildcard: "terminal:git*" matches "terminal:git_status", etc.

    Args:
        tool_name: Tool name to check (e.g., "git_status", "terminal__mcp_execute_command")
        pattern: Pattern to match against

    Returns:
        True if tool name matches pattern

    Examples:
        >>> matches_pattern("git_status", "git_status")
        True
        >>> matches_pattern("git_status", "git_*")
        True
        >>> matches_pattern("terminal__mcp_execute_command", "terminal:*")
        True
        >>> matches_pattern("terminal__git_status", "terminal:git*")
        True
        >>> matches_pattern("filesystem__read_file", "git_*")
        False
    """
    # Handle terminal: prefix (map to terminal__ in tool names)
    if ":" in pattern:
        # Convert "terminal:git*" -> "terminal__git*"
        # Convert "filesystem:read*" -> "filesystem__read*"
        parts = pattern.split(":", 1)
        pattern = f"{parts[0]}__{parts[1]}"

    # Use fnmatch for wildcard matching
    return fnmatch.fnmatch(tool_name, pattern)


def filter_tools_by_whitelist(
    available_tools: List[str],
    allowed_patterns: List[str]
) -> List[str]:
    """
    Filter tools by whitelist patterns.

    Args:
        available_tools: List of available tool names
        allowed_patterns: List of allowed patterns (supports wildcards)

    Returns:
        Filtered list of tool names

    Examples:
        >>> tools = ["git_status", "git_diff", "filesystem__read_file", "bash"]
        >>> patterns = ["git_*", "bash"]
        >>> filter_tools_by_whitelist(tools, patterns)
        ['git_status', 'git_diff', 'bash']
    """
    if not allowed_patterns:
        # No whitelist = allow all tools
        return available_tools

    filtered = []
    for tool_name in available_tools:
        for pattern in allowed_patterns:
            if matches_pattern(tool_name, pattern):
                filtered.append(tool_name)
                break  # Tool matched, no need to check other patterns

    logger.debug(f"Tool filtering: {len(available_tools)} -> {len(filtered)} tools")
    logger.debug(f"Allowed patterns: {allowed_patterns}")
    logger.debug(f"Filtered tools: {filtered}")

    return filtered


@contextmanager
def temporary_tool_filter(swarm, allowed_tools: Optional[List[str]] = None):
    """
    Context manager to temporarily filter tools for a swarm and its agents.

    Automatically restores original tools when exiting context.

    Args:
        swarm: Swarm instance
        allowed_tools: List of allowed tool patterns (None = no filtering)

    Yields:
        The swarm instance

    Example:
        >>> with temporary_tool_filter(swarm, ["git_*", "bash"]):
        ...     # Execute command with filtered tools
        ...     result = await executor.chat(prompt)
        ... # Tools automatically restored here
    """
    # If no filter specified, just yield swarm as-is
    if not allowed_tools:
        logger.debug("No tool filtering applied")
        yield swarm
        return

    # Save original tool lists
    original_swarm_tools = swarm.tools.copy() if hasattr(swarm, 'tools') else []
    original_agent_tools: Dict[str, List[str]] = {}

    try:
        # Get all agents from swarm
        agents_to_filter = []

        # Method 1: Try agent_graph.agents
        if hasattr(swarm, 'agent_graph') and swarm.agent_graph:
            if hasattr(swarm.agent_graph, 'agents') and swarm.agent_graph.agents:
                if isinstance(swarm.agent_graph.agents, dict):
                    agents_to_filter.extend(swarm.agent_graph.agents.values())
                elif isinstance(swarm.agent_graph.agents, (list, tuple)):
                    agents_to_filter.extend(swarm.agent_graph.agents)

        # Method 2: Try swarm.agents
        if not agents_to_filter and hasattr(swarm, 'agents') and swarm.agents:
            if isinstance(swarm.agents, dict):
                agents_to_filter.extend(swarm.agents.values())
            elif isinstance(swarm.agents, (list, tuple)):
                agents_to_filter.extend(swarm.agents)

        # Method 3: Try topology
        if not agents_to_filter and hasattr(swarm, 'topology') and swarm.topology:
            for item in swarm.topology:
                if hasattr(item, 'tool_names'):
                    agents_to_filter.append(item)
                elif isinstance(item, (list, tuple)):
                    agents_to_filter.extend([a for a in item if hasattr(a, 'tool_names')])

        logger.debug(f"Filtering tools for {len(agents_to_filter)} agent(s)")

        # Filter swarm-level tools
        if original_swarm_tools:
            filtered_swarm_tools = filter_tools_by_whitelist(original_swarm_tools, allowed_tools)
            swarm.tools = filtered_swarm_tools
            logger.debug(f"Swarm tools: {len(original_swarm_tools)} -> {len(filtered_swarm_tools)}")

        # Filter agent-level tools
        for agent in agents_to_filter:
            if hasattr(agent, 'tool_names') and agent.tool_names:
                agent_id = getattr(agent, 'id', lambda: 'unknown')()
                original_agent_tools[agent_id] = agent.tool_names.copy()

                filtered_agent_tools = filter_tools_by_whitelist(agent.tool_names, allowed_tools)
                agent.tool_names = filtered_agent_tools

                logger.debug(f"Agent {agent_id} tools: {len(original_agent_tools[agent_id])} -> {len(filtered_agent_tools)}")

        # Yield filtered swarm
        yield swarm

    finally:
        # Restore original tools
        if hasattr(swarm, 'tools'):
            swarm.tools = original_swarm_tools
            logger.debug(f"Restored swarm tools: {len(original_swarm_tools)}")

        # Restore agent tools
        for agent in agents_to_filter:
            if hasattr(agent, 'tool_names'):
                agent_id = getattr(agent, 'id', lambda: 'unknown')()
                if agent_id in original_agent_tools:
                    agent.tool_names = original_agent_tools[agent_id]
                    logger.debug(f"Restored agent {agent_id} tools: {len(original_agent_tools[agent_id])}")
