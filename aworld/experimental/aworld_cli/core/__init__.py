"""
Core modules for aworld-cli.
"""
from .registry import LocalAgent, LocalAgentRegistry, agent
from .loader import init_agents

__all__ = ["LocalAgent", "LocalAgentRegistry", "agent", "init_agents"]

