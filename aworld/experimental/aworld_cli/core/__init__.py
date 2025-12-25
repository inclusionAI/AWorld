"""
Core modules for aworld-cli.
"""
from .agent_registry import LocalAgent, LocalAgentRegistry, agent
from .loader import init_agents
from .skill_registry import get_skill_registry, register_skill_source, reset_skill_registry

__all__ = [
    "LocalAgent",
    "LocalAgentRegistry",
    "agent",
    "init_agents",
    "get_skill_registry",
    "register_skill_source",
    "reset_skill_registry",
]

