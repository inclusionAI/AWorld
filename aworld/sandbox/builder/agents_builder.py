"""Builder for agents collection."""
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aworld.sandbox.builder.sandbox_builder import SandboxBuilder

# Import here to avoid circular import
from aworld.sandbox.builder.agent_builder import AgentBuilder


class AgentsBuilder:
    """Builder for agents collection."""
    
    def __init__(self, parent: 'SandboxBuilder'):
        self.parent = parent
        self.current_agent: Optional[AgentBuilder] = None
    
    def __getattr__(self, name: str) -> AgentBuilder:
        """Create a new agent builder for the given agent name.
        Supports both attribute access (agent_1) and method call (agent_1()).
        Automatically commits previous agent if exists.
        """
        # Auto-commit previous agent if exists
        if self.current_agent is not None:
            self.current_agent._auto_commit()
        
        # Create new agent builder
        self.current_agent = AgentBuilder(name, self.parent, self)
        return self.current_agent
