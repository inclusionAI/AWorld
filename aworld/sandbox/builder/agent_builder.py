"""Builder for a single agent configuration."""
from typing import Dict, List, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aworld.sandbox.builder.sandbox_builder import SandboxBuilder
    from aworld.sandbox.builder.agents_builder import AgentsBuilder


class AgentBuilder:
    """Builder for a single agent configuration."""
    
    def __init__(self, name: str, parent: 'SandboxBuilder', agents_builder: 'AgentsBuilder'):
        self.name = name
        self.parent = parent
        self.agents_builder = agents_builder
        self.config: Dict[str, Any] = {}
        self._committed = False
    
    def __call__(self) -> 'AgentBuilder':
        """Make AgentBuilder callable, returns self for fluent API."""
        return self
    
    def _auto_commit(self):
        """Automatically commit agent configuration if not already committed."""
        if not self._committed:
            self.parent._add_agent(self.name, self.config)
            self._committed = True
            self.agents_builder.current_agent = None
    
    def __getattr__(self, name: str):
        """Forward unknown method calls to parent SandboxBuilder after auto-committing.
        This allows seamless chaining: agent_1().run_mode("local").agents().agent_2()...
        """
        # Auto-commit current agent before forwarding to parent
        self._auto_commit()
        # Forward to parent SandboxBuilder
        return getattr(self.parent, name)
    
    def run_mode(self, mode: Optional[str] = None) -> 'AgentBuilder':
        """Set agent run mode: 'local' or 'remote'.
        
        Usage:
            # Direct value
            builder.run_mode("local")
            
            # Or use convenience methods
            builder.local()
            builder.remote()
        """
        if mode is not None:
            self.config["run_mode"] = mode.lower()
        return self
    
    def local(self) -> 'AgentBuilder':
        """Set agent run mode to 'local' (convenience method)."""
        self.config["run_mode"] = "local"
        return self
    
    def remote(self) -> 'AgentBuilder':
        """Set agent run mode to 'remote' (convenience method)."""
        self.config["run_mode"] = "remote"
        return self
    
    def location(self, location: str) -> 'AgentBuilder':
        """Set agent location (path or URL)."""
        self.config["location"] = location
        return self
    
    def headers(self, headers: Dict[str, Any]) -> 'AgentBuilder':
        """Set agent headers."""
        self.config["headers"] = headers
        return self
    
    def env(self, env: Dict[str, str]) -> 'AgentBuilder':
        """Set agent environment variables."""
        self.config["env"] = env
        return self
    
    def args(self, args: List[str]) -> 'AgentBuilder':
        """Set agent command arguments."""
        self.config["args"] = args
        return self
    
    def extra(self, extra: Dict[str, Any]) -> 'AgentBuilder':
        """Set extra configuration (merged into config)."""
        self.config.update(extra)
        return self
    
    def build(self) -> 'SandboxBuilder':
        """Finish building this agent and return to parent builder.
        Note: This method is optional - agent will be auto-committed when starting next agent or building sandbox.
        """
        self._auto_commit()
        return self.parent

