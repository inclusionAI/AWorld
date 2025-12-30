"""
Agent information models.
"""
from typing import Protocol
from .agent_info import AgentInfo

class IAgentInfo(Protocol):
    """Protocol for agent information."""
    name: str
    desc: str

# Type alias for backward compatibility and cleaner usage
TeamInfo = AgentInfo

__all__ = ["IAgentInfo", "AgentInfo", "TeamInfo"]

