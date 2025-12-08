import asyncio
from typing import List, Callable, Any, Union, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.formatted_text import HTML
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

from .models import AgentInfo

console = Console()

# ... existing imports ...

from rich.text import Text
from rich.color import Color

# ... existing imports ...

class AWorldCLI:
    def __init__(self):
        self.console = console

    def _get_gradient_text(self, text: str, start_color: str, end_color: str) -> Text:
        """Create a Text object with a horizontal gradient."""
        result = Text()
        lines = text.strip().split("\n")
        max_width = max(len(line) for line in lines)
        
        c1 = Color.parse(start_color).get_truecolor()
        c2 = Color.parse(end_color).get_truecolor()
        
        for line in lines:
            for i, char in enumerate(line):
                if char.isspace():
                    result.append(char)
                    continue
                    
                ratio = i / max_width if max_width > 0 else 0
                r = int(c1[0] + (c2[0] - c1[0]) * ratio)
                g = int(c1[1] + (c2[1] - c1[1]) * ratio)
                b = int(c1[2] + (c2[2] - c1[2]) * ratio)
                color_hex = f"#{r:02x}{g:02x}{b:02x}"
                result.append(char, style=f"bold {color_hex}")
            result.append("\n")
        return result

    def display_welcome(self):
        # "ANSI Shadow" font style for AWORLD - cleaner and modern
        ascii_art = """
 █████╗ ██╗    ██╗ ██████╗ ██████╗ ██╗     ██████╗ 
██╔══██╗██║    ██║██╔═══██╗██╔══██╗██║     ██╔══██╗
███████║██║ █╗ ██║██║   ██║██████╔╝██║     ██║  ██║
██╔══██║██║███╗██║██║   ██║██╔══██╗██║     ██║  ██║
██║  ██║╚███╔███╔╝╚██████╔╝██║  ██║███████╗██████╔╝
╚═╝  ╚═╝ ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═════╝ 
"""
        # Single color Blue (#4285F4)
        banner = Text(ascii_art, style="bold #4285F4")
        
        # Display the Logo standalone (without a box), left aligned
        self.console.print(banner)
        
        # Subtitle / Version
        subtitle = Text("\n🤖 Interact with your agents directly from the terminal", style="italic #875fff")
        version = Text("\n v0.1.0", style="dim white")
        
        self.console.print(Text.assemble(subtitle, version))
        self.console.print() # Padding

    def display_agents(self, agents: List[AgentInfo], source_type: str = "LOCAL", source_location: str = ""):
        """
        Display available agents in a table.
        
        Args:
            agents: List of agents to display
            source_type: Source type (LOCAL, REMOTE, etc.)
            source_location: Source location (directory path or URL)
        """
        if not agents:
            self.console.print(f"[red]No agents available ({source_type}: {source_location}).[/red]")
            return
            
        table = Table(title="Available Agents", box=box.ROUNDED)
        table.add_column("Name", style="magenta")
        table.add_column("Description", style="green")
        table.add_column("SourceType", style="cyan")
        table.add_column("Address", style="blue")

        for agent in agents:
            desc = getattr(agent, "desc", "No description") or "No description"
            table.add_row(agent.name, desc, source_type, source_location)

        self.console.print(table)

    def select_agent(self, agents: List[AgentInfo], source_type: str = "LOCAL", source_location: str = "") -> Optional[AgentInfo]:
        """
        Prompt user to select an agent from the list.
        
        Args:
            agents: List of available agents
            source_type: Source type (LOCAL, REMOTE, etc.)
            source_location: Source location (directory path or URL)
            
        Returns:
            Selected agent or None if selection cancelled
        """
        if not agents:
            self.console.print(f"[red]No agents available ({source_type}: {source_location}).[/red]")
            return None
        
        table = Table(title="Available Agents", box=box.ROUNDED)
        table.add_column("No.", style="cyan", justify="right")
        table.add_column("Name", style="magenta")
        table.add_column("Description", style="green")
        table.add_column("SourceType", style="cyan")
        table.add_column("Address", style="blue")

        for idx, agent in enumerate(agents, 1):
            desc = getattr(agent, "desc", "No description") or "No description"
            table.add_row(str(idx), agent.name, desc, source_type, source_location)

        self.console.print(table)

        while True:
            choice = Prompt.ask("Select an agent number", default="1")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(agents):
                    selected_agent = agents[idx]
                    self.console.print(f"[green]Selected agent: [bold]{selected_agent.name}[/bold][/green]")
                    return selected_agent
                else:
                    self.console.print("[red]Invalid selection. Please try again.[/red]")
            except ValueError:
                self.console.print("[red]Please enter a valid number.[/red]")
    
    def select_team(self, teams: List[AgentInfo], source_type: str = "LOCAL", source_location: str = "") -> Optional[AgentInfo]:
        """
        Alias for select_agent for backward compatibility.
        Use select_agent instead.
        """
        return self.select_agent(teams, source_type, source_location)

    async def run_chat_session(self, agent_name: str, executor: Callable[[str], Any], available_agents: List[AgentInfo] = None) -> Union[bool, str]:
        """
        Run an interactive chat session with an agent.
        
        Args:
            agent_name: Name of the agent to chat with
            executor: Async function that executes chat messages
            available_agents: List of available agents for switching
            
        Returns:
            False if user wants to exit, True if wants to switch (show list), or agent name string to switch to
        """
        self.console.print(Panel(f"Starting chat session with [bold]{agent_name}[/bold].\nType 'exit' to quit.\nType '/switch [agent_name]' to switch agent.", style="blue"))
        
        # Setup completer
        agent_names = [a.name for a in available_agents] if available_agents else []
        
        completer = NestedCompleter.from_nested_dict({
            '/switch': {name: None for name in agent_names},
            '/exit': None,
            '/quit': None,
            'exit': None,
            'quit': None
        })
        
        session = PromptSession(completer=completer)

        while True:
            try:
                # Use prompt_toolkit for input with completion
                # We use HTML for basic coloring of the prompt
                user_input = await asyncio.to_thread(session.prompt, HTML("<b><cyan>You</cyan></b>: "))
                
                user_input = user_input.strip()
                
                # Skip empty input (user just pressed Enter)
                if not user_input:
                    continue
                
                # Handle explicit exit commands
                if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
                    if Confirm.ask("Are you sure you want to exit?"):
                        return False # Return False to stop the loop (Exit App)
                    continue

                # Handle switch command
                if user_input.lower().startswith(("/switch", "switch")):
                    parts = user_input.split(maxsplit=1)
                    if len(parts) > 1:
                         target_agent = parts[1]
                         # Validate agent existence
                         if target_agent in agent_names:
                             return target_agent
                         else:
                             self.console.print(f"[red]Agent '{target_agent}' not found.[/red]")
                             continue
                    else:
                        return True # Return True to switch agent (show list)

                # Print agent name before response
                self.console.print(f"[bold green]{agent_name}[/bold green]:")
                
                try:
                    # Execute the task/chat
                    # The executor handles all printing (both streaming and non-streaming)
                    response = await executor(user_input)
                    # Response is returned for potential future use, but content is already printed by executor
                except Exception as e:
                    self.console.print(f"[bold red]Error executing task:[/bold red] {e}")
                    continue

            except KeyboardInterrupt:
                self.console.print("\n[yellow]Session interrupted.[/yellow]")
                break
            except Exception as e:
                self.console.print(f"[red]An unexpected error occurred:[/red] {e}")

        return False

