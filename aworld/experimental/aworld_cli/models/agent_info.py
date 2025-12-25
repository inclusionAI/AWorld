"""
Agent information implementations.
"""
from typing import Optional, Union, Dict, Any
from typing import Protocol

class IAgentInfo(Protocol):
    """Protocol for agent information."""
    name: str
    desc: str


class AgentInfo:
    """
    Agent information implementation.
    Supports both local team objects and remote API responses.
    
    Example:
        # From local team
        >>> team = AgentTeamRegistry.get_team("MyAgent")
        >>> agent_info = AgentInfo.from_team(team)
        
        # From remote API
        >>> data = {"name": "MyAgent", "desc": "Description"}
        >>> agent_info = AgentInfo.from_dict(data)
    """
    
    def __init__(
        self, 
        name: str, 
        desc: str = "No description", 
        metadata: Optional[Dict[str, Any]] = None, 
        source: Optional[Any] = None,
        source_type: Optional[str] = None,
        source_location: Optional[str] = None
    ):
        """
        Initialize agent info.
        
        Args:
            name: Agent name
            desc: Agent description
            metadata: Optional metadata dictionary
            source: Optional source object (team for local, dict for remote)
            source_type: Optional source type (LOCAL, REMOTE, etc.)
            source_location: Optional source location (directory path or URL)
        """
        self.name = name
        self.desc = desc or "No description"
        self.metadata = metadata or {}
        self.source = source  # Keep reference to original source if needed
        self.source_type = source_type or "UNKNOWN"
        self.source_location = source_location or ""
    
    @classmethod
    def from_team(cls, team, source_location: Optional[str] = None) -> "AgentInfo":
        """
        Create AgentInfo from local team object.
        
        Args:
            team: Agent team instance from registry (can be AgentTeam or LocalAgent)
            source_location: Optional source location
            
        Returns:
            AgentInfo instance
        """
        return cls(
            name=team.name,
            desc=team.desc or "No description",
            metadata=getattr(team, 'metadata', {}),
            source=team,
            source_type="LOCAL",
            source_location=source_location or ""
        )
    
    @classmethod
    def from_local_agent(cls, agent, source_location: Optional[str] = None) -> "AgentInfo":
        """
        Create AgentInfo from LocalAgent object.
        
        Args:
            agent: LocalAgent instance from registry
            source_location: Optional source location (directory path)
            
        Returns:
            AgentInfo instance
        """
        return cls(
            name=agent.name,
            desc=agent.desc or "No description",
            metadata=getattr(agent, 'metadata', {}),
            source=agent,
            source_type="LOCAL",
            source_location=source_location or ""
        )
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], source_location: Optional[str] = None) -> "AgentInfo":
        """
        Create AgentInfo from dictionary (e.g., API response).
        
        Args:
            data: Dictionary containing agent data
            source_location: Optional source location (URL)
            
        Returns:
            AgentInfo instance
        """
        return cls(
            name=data.get("name", ""),
            desc=data.get("desc") or "No description",
            metadata=data.get("metadata", {}),
            source=data,
            source_type="REMOTE",
            source_location=source_location or ""
        )
    
    @classmethod
    def from_source(cls, source: Union[Any, Dict[str, Any]], source_location: Optional[str] = None) -> "AgentInfo":
        """
        Create AgentInfo from any source (auto-detect type).
        
        Args:
            source: Team object, LocalAgent object, or dictionary
            source_location: Optional source location
            
        Returns:
            AgentInfo instance
        """
        if isinstance(source, dict):
            return cls.from_dict(source, source_location=source_location)
        else:
            # Check if it's a LocalAgent (from aworld_cli.core.agent_registry)
            try:
                from aworld_cli.core.agent_registry import LocalAgent
                if isinstance(source, LocalAgent):
                    return cls.from_local_agent(source, source_location=source_location)
            except ImportError:
                pass
            # Fallback to from_team for backward compatibility
            return cls.from_team(source, source_location=source_location)

# Backward compatibility aliases
LocalAgentInfo = AgentInfo
RemoteAgentInfo = AgentInfo
AgentInfoWrapper = AgentInfo  # For backward compatibility

__all__ = ["AgentInfo", "LocalAgentInfo", "RemoteAgentInfo", "AgentInfoWrapper"]

