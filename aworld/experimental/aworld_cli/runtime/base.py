"""
Base runtime for CLI protocols.
Provides common functionality for both local and remote runtimes.
"""
from typing import List, Optional
from ..console import AWorldCLI
from ..models import AgentInfo
from ..executors import AgentExecutor


class BaseAgentRuntime:
    """
    Base runtime for CLI protocols that interact with agents.
    Provides common functionality for agent selection and chat session management.
    
    To create a new runtime, inherit from this class and implement:
    - _load_agents(): Load available agents
    - _create_executor(): Create executor for selected agent
    - _get_source_type(): Return source type string
    - _get_source_location(): Return source location string
    
    Example:
        class CustomRuntime(BaseAgentRuntime):
            async def _load_agents(self) -> List[AgentInfo]:
                # Load agents from custom source
                pass
            
            async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
                # Create custom executor
                pass
            
            def _get_source_type(self) -> str:
                return "CUSTOM"
            
            def _get_source_location(self) -> str:
                return "custom://location"
    """
    
    def __init__(self, agent_name: Optional[str] = None):
        """
        Initialize base runtime.
        
        Args:
            agent_name: The name of the agent to interact with. If None, user will be prompted to select.
        """
        self.agent_name = agent_name
        self._running = False
        self.cli = AWorldCLI()
    
    async def start(self) -> None:
        """Start the CLI interaction loop."""
        self._running = True
        self.cli.display_welcome()
        
        # Load agents (implemented by subclasses)
        agents = await self._load_agents()
        
        if not agents:
            self.cli.console.print("[red]❌ No agents available.[/red]")
            return
        
        while self._running:
            # Select agent
            selected_agent = await self._select_agent(agents)
            if not selected_agent:
                return
            
            # Start chat session
            executor = await self._create_executor(selected_agent)
            if not executor:
                self.cli.console.print("[red]❌ Failed to create executor for agent.[/red]")
                continue
            
            result = await self.cli.run_chat_session(
                selected_agent.name, 
                executor.chat, 
                available_agents=agents,
                executor_instance=executor
            )
            
            # Handle session result
            if result is False:
                # User wants to exit app
                break
            elif result is True:
                # User wants to switch agent (show list)
                if len(agents) == 1:
                    self.cli.console.print("[yellow]ℹ️ Only one agent available. Cannot switch.[/yellow]")
                    # Continue with the same agent
                    continue
                # Multiple agents available, will show list in next iteration
                selected_agent = None
                continue
            elif isinstance(result, str):
                # User wants to switch to specific agent
                self.agent_name = result
                # Loop will handle selection
                continue
    
    async def stop(self) -> None:
        """Stop the CLI loop."""
        self._running = False
    
    async def _load_agents(self) -> List[AgentInfo]:
        """
        Load available agents.
        Must be implemented by subclasses.
        
        Returns:
            List of available agents
        """
        raise NotImplementedError("Subclasses must implement _load_agents")
    
    async def _select_agent(self, agents: List[AgentInfo]) -> Optional[AgentInfo]:
        """
        Select an agent from the list.
        
        Args:
            agents: List of available agents
            
        Returns:
            Selected agent or None if selection cancelled
        """
        selected_agent = None
        
        # If agent_name was provided, try to find it
        if self.agent_name:
            for agent in agents:
                if agent.name == self.agent_name:
                    selected_agent = agent
                    break
            if not selected_agent:
                self.cli.console.print(f"[red]❌ Agent '{self.agent_name}' not found.[/red]")
            # Clear it so next loop we select
            self.agent_name = None
        
        if not selected_agent:
            if len(agents) == 1:
                self.cli.display_agents(agents, source_type=self._get_source_type(), source_location=self._get_source_location())
                selected_agent = agents[0]
                self.cli.console.print(f"[green]🎯 Using default agent: [bold]{selected_agent.name}[/bold][/green]")
            else:
                selected_agent = self.cli.select_agent(agents, source_type=self._get_source_type(), source_location=self._get_source_location())
        
        return selected_agent
    
    async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
        """
        Create an executor for the selected agent.
        Must be implemented by subclasses.
        
        Args:
            agent: Selected agent
            
        Returns:
            Agent executor or None if creation failed
        """
        raise NotImplementedError("Subclasses must implement _create_executor")
    
    def _get_source_type(self) -> str:
        """
        Get the source type for display purposes.
        Must be implemented by subclasses.
        
        Returns:
            Source type string (e.g., "LOCAL", "REMOTE")
        """
        raise NotImplementedError("Subclasses must implement _get_source_type")
    
    def _get_source_location(self) -> str:
        """
        Get the source location for display purposes.
        Must be implemented by subclasses.
        
        Returns:
            Source location string (e.g., directory path or URL)
        """
        raise NotImplementedError("Subclasses must implement _get_source_location")

