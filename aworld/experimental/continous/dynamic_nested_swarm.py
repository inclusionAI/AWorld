from typing import Optional, Union, List

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.logs.util import logger

GUARDRAILS_AGENT = Union[Swarm, Agent, List[Swarm], List[Agent]]
"""
Dynamic Nested Swarm

This module provides functionality to wrap a Swarm (e.g., DeepResearch Swarm) 
with guard agents that can execute synchronously (blocking) or asynchronously (non-blocking).

Example:
    >>> deep_research_swarm = get_deepresearch_swarm(user_input)
    >>> blocking_guard = Agent(name="safety_checker", ...)
    >>> non_blocking_guard = Agent(name="monitor", ...)
    >>> wrapped_swarm = build_dns_swarm(deep_research_swarm, blocking_guard, non_blocking_guard)
"""


def build_dns_swarm(
    origin: Swarm,
    blocking_guard: Optional[GUARDRAILS_AGENT] = None,
    non_blocking_guard: Optional[GUARDRAILS_AGENT] = None
) -> Swarm:
    """
    Build a Dynamic Nested Swarm that wraps the origin Swarm with guard agents.
    
    The origin Swarm (e.g., DeepResearch Swarm) will be wrapped as a node in a new Swarm.
    Guard agents can be added to monitor or validate the execution:
    - blocking_guard: Executes synchronously before the origin, blocking the flow
    - non_blocking_guard: Not implemented yet (reserved for future use)
    
    Args:
        origin: The original Swarm to be wrapped (e.g., DeepResearch Swarm).
        blocking_guard: Guard agent(s)/swarm(s) that execute synchronously before origin, blocking the flow.
                        Can be a single Agent/Swarm or a list of Agents/Swarms.
        non_blocking_guard: Guard agent(s)/swarm(s) that execute asynchronously in parallel with origin, non-blocking.
                           Can be a single Agent/Swarm or a list of Agents/Swarms.
                           Note: Not implemented yet, will be ignored if provided.
    
    Returns:
        A new Swarm that wraps the origin with guard agents.
    
    Example:
        >>> # Simple case: only blocking guard
        >>> wrapped = build_dns_swarm(deep_research_swarm, blocking_guard=safety_agent)
        >>> # Topology: safety_agent -> deep_research_swarm
        
        >>> # With list guards
        >>> wrapped = build_dns_swarm(deep_research_swarm, blocking_guard=[guard1, guard2])
        >>> # Topology: guard1 -> guard2 -> deep_research_swarm
    """
    if origin is None:
        raise ValueError("origin Swarm cannot be None")
    
    # Warn if non_blocking_guard is provided (not implemented yet)
    if non_blocking_guard:
        logger.warning("⚠️ non_blocking_guard is not implemented yet and will be ignored")
    
    # No guards, just return origin
    if not blocking_guard:
        logger.info("📦 Building DNS Swarm without guards (returning origin)")
        return origin
    
    # Normalize blocking_guard to list for easier handling
    blocking_list = blocking_guard if isinstance(blocking_guard, list) else [blocking_guard]
    
    # Only blocking guard: blocking_guard(s) -> origin
    logger.info("🔒 Building DNS Swarm with blocking guard")
    return Swarm(*blocking_list, origin)