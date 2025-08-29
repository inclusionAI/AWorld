from . import gaia_agent_loop  # Expose submodule for attribute access
from .gaia_agent_loop import GaiaAgentLoop  # Optional: direct class import for convenience

__all__ = [
    "gaia_agent_loop",
    "GaiaAgentLoop",
]

