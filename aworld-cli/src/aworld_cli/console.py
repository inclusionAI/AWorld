import asyncio
import sys
from pathlib import Path
from typing import List, Callable, Any, Union, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.formatted_text import HTML
from rich import box
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

from .models import AgentInfo
from ._globals import console
from .core.skill_registry import get_skill_registry
        
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
            source_type: Source type (LOCAL, REMOTE, etc.) - used as fallback if agent doesn't have source_type
            source_location: Source location (directory path or URL) - used as fallback if agent doesn't have source_location
        """
        if not agents:
            self.console.print(f"[red]No agents available ({source_type}: {source_location}).[/red]")
            return
            
        table = Table(title="Available Agents", box=box.ROUNDED, width=None)
        table.add_column("Name", style="magenta")
        table.add_column("Description", style="green")
        table.add_column("Address", style="blue")

        for agent in agents:
            desc = getattr(agent, "desc", "No description") or "No description"
            # Always use agent's own source_location if it exists and is valid
            # Fallback to provided parameters only if agent doesn't have this attribute
            agent_source_location = getattr(agent, "source_location", None)
            
            # Use agent's source_location if it exists and is valid, otherwise use fallback
            if agent_source_location and agent_source_location.strip() != "":
                # Use agent's own source_location
                pass
            else:
                # Use fallback
                agent_source_location = source_location
            
            table.add_row(agent.name, desc, agent_source_location)

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
        
        table = Table(title="Available Agents", box=box.ROUNDED, width=None)
        table.add_column("No.", style="cyan", justify="right")
        table.add_column("Name", style="magenta")
        table.add_column("Description", style="green")
        table.add_column("SourceType", style="cyan")
        table.add_column("Address", style="blue")

        for idx, agent in enumerate(agents, 1):
            desc = getattr(agent, "desc", "No description") or "No description"
            # Always use agent's own source_type and source_location if they exist and are valid
            # Fallback to provided parameters only if agent doesn't have these attributes
            agent_source_type = getattr(agent, "source_type", None)
            agent_source_location = getattr(agent, "source_location", None)
            
            # Use agent's source_type if it exists and is valid, otherwise use fallback
            if agent_source_type and agent_source_type != "UNKNOWN" and agent_source_type.strip() != "":
                # Use agent's own source_type
                pass
            else:
                # Use fallback
                agent_source_type = source_type
            
            # Use agent's source_location if it exists and is valid, otherwise use fallback
            if agent_source_location and agent_source_location.strip() != "":
                # Use agent's own source_location
                pass
            else:
                # Use fallback
                agent_source_location = source_location
            
            table.add_row(str(idx), agent.name, desc, agent_source_type, agent_source_location)

        self.console.print(table)
        self.console.print("[dim]Type 'exit' to cancel selection.[/dim]")

        # Check if we're in a real terminal
        is_terminal = sys.stdin.isatty()
        
        while True:
            if is_terminal:
                choice = Prompt.ask("Select an agent number", default="1")
            else:
                # Fallback for non-terminal environments
                self.console.print("Select an agent number [default: 1]: ", end="")
                choice = input().strip() or "1"
            
            # Check for exit command
            if choice.lower() in ("exit", "quit", "q"):
                self.console.print("[yellow]Selection cancelled.[/yellow]")
                return None
            
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(agents):
                    selected_agent = agents[idx]
                    self.console.print(f"[green]Selected agent: [bold]{selected_agent.name}[/bold][/green]")
                    return selected_agent
                else:
                    self.console.print("[red]Invalid selection. Please try again.[/red]")
            except ValueError:
                self.console.print("[red]Please enter a valid number or 'exit' to cancel.[/red]")
    
    def select_team(self, teams: List[AgentInfo], source_type: str = "LOCAL", source_location: str = "") -> Optional[AgentInfo]:
        """
        Alias for select_agent for backward compatibility.
        Use select_agent instead.
        """
        return self.select_agent(teams, source_type, source_location)

    async def run_chat_session(self, agent_name: str, executor: Callable[[str], Any], available_agents: List[AgentInfo] = None, executor_instance: Any = None) -> Union[bool, str]:
        """
        Run an interactive chat session with an agent.
        
        Args:
            agent_name: Name of the agent to chat with
            executor: Async function that executes chat messages
            available_agents: List of available agents for switching
            executor_instance: Optional executor instance (for session management)
            
        Returns:
            False if user wants to exit, True if wants to switch (show list), or agent name string to switch to
        """
        # Get current session_id if available
        session_id_info = ""
        if executor_instance and hasattr(executor_instance, 'session_id'):
            session_id_info = f"\nCurrent session: [dim]{executor_instance.session_id}[/dim]"
        
        # Get agent configuration info (PTC, MCP servers, and Skills)
        config_info = ""
        if available_agents:
            # Find current agent
            current_agent = next((a for a in available_agents if a.name == agent_name), None)
            if current_agent and current_agent.metadata:
                metadata = current_agent.metadata
                
                # Check PTC status
                ptc_tools = metadata.get("ptc_tools", [])
                ptc_status = "✅ Enabled" if ptc_tools else "❌ Disabled"
                ptc_count = len(ptc_tools) if isinstance(ptc_tools, list) else 0
                ptc_status_text = ptc_status + (f" ({ptc_count} tools)" if ptc_count > 0 else "")
                ptc_info = f"PTC: [dim]{ptc_status_text}[/dim]"
                
                # Get MCP servers list
                mcp_servers = metadata.get("mcp_servers", [])
                if isinstance(mcp_servers, list) and mcp_servers:
                    mcp_list = ", ".join(mcp_servers)
                    mcp_info = f"MCP Servers: [dim]{mcp_list}[/dim]"
                else:
                    mcp_info = f"MCP Servers: [dim]None[/dim]"
                
                config_info = f"\n{ptc_info}\n{mcp_info}"
        
        # Get skill status from executor if available
        skill_info = ""
        if executor_instance and hasattr(executor_instance, 'get_skill_status'):
            try:
                skill_status = executor_instance.get_skill_status()
                total = skill_status.get('total', 0)
                active = skill_status.get('active', 0)
                inactive = skill_status.get('inactive', 0)
                active_names = skill_status.get('active_names', [])
                
                if total > 0:
                    if active > 0 and active_names:
                        # Show active skill names
                        active_names_str = ", ".join(active_names)
                        skill_info = f"\nSkills: [dim]Total: {total}, Active: {active} ({active_names_str}), Inactive: {inactive}[/dim]"
                    else:
                        skill_info = f"\nSkills: [dim]Total: {total}, Active: {active}, Inactive: {inactive}[/dim]"
                else:
                    skill_info = f"\nSkills: [dim]None[/dim]"
            except Exception:
                # If getting skill status fails, don't show skill info
                pass
        
        help_text = (
            f"Starting chat session with [bold]{agent_name}[/bold].{session_id_info}{config_info}{skill_info}\n"
            f"Type 'exit' to quit.\n"
            f"Type '/switch [agent_name]' to switch agent.\n"
            f"Type '/new' to create a new session.\n"
            f"Type '/restore' or '/latest' to restore to the latest session.\n"
            f"Type '/skills' to list all available skills.\n"
            f"Type '/agents' to list all available agents.\n"
            f"Use @filename to include images or text files (e.g., @photo.jpg or @document.txt)."
        )
        self.console.print(Panel(help_text, style="blue"))

        # # Get agent-dir from environment variables
        # agent_dirs = []
        # local_agents_dir = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
        # if local_agents_dir:
        #     agent_dirs = [d.strip() for d in local_agents_dir.split(";") if d.strip()]
        
        # # Get skill-path from skill registry
        # skill_paths = []
        # try:
        #     registry = get_skill_registry()
        #     skill_paths = registry.list_sources()
        # except Exception:
        #     pass
        
        # # Display agent-dir and skill-path
        # if agent_dirs or skill_paths:
        #     info_lines = []
        #     if agent_dirs:
        #         agent_dirs_str = ", ".join(agent_dirs)
        #         info_lines.append(f"Agent Dirs: [dim]{agent_dirs_str}[/dim]")
        #     if skill_paths:
        #         skill_paths_str = ", ".join(skill_paths)
        #         info_lines.append(f"Skill Paths: [dim]{skill_paths_str}[/dim]")
            
        #     if info_lines:
        #         self.console.print("\n".join(info_lines))
        
        self.console.print()
        
        # Check if we're in a real terminal (not IDE debugger or redirected input)
        is_terminal = sys.stdin.isatty()
        
        # Setup completer and session only if in terminal
        agent_names = [a.name for a in available_agents] if available_agents else []
        session = None
        
        if is_terminal:
            completer = NestedCompleter.from_nested_dict({
                '/switch': {name: None for name in agent_names},
                '/new': None,
                '/restore': None,
                '/latest': None,
                '/skills': None,
                '/agents': None,
                '/exit': None,
                '/quit': None,
                'exit': None,
                'quit': None
                
            })
            session = PromptSession(completer=completer)

        while True:
            try:
                # Use prompt_toolkit in terminal, plain input() in non-terminal (e.g., IDE debugger)
                if is_terminal and session:
                    # Use prompt_toolkit for input with completion
                    # We use HTML for basic coloring of the prompt
                    user_input = await asyncio.to_thread(session.prompt, HTML("<b><cyan>You</cyan></b>: "))
                else:
                    # Fallback to plain input() for non-terminal environments
                    self.console.print("[cyan]You[/cyan]: ", end="")
                    user_input = await asyncio.to_thread(input)
                
                user_input = user_input.strip()
                
                # Skip empty input (user just pressed Enter)
                if not user_input:
                    continue
                
                # Handle explicit exit commands
                if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
                    if is_terminal:
                        return False
                    else:
                        # In non-terminal, just exit without confirmation
                        return False
                    continue

                # Handle new session command
                if user_input.lower() in ("/new", "new"):
                    if executor_instance and hasattr(executor_instance, 'new_session'):
                        executor_instance.new_session()
                        # Update session_id_info display
                        if hasattr(executor_instance, 'session_id'):
                            self.console.print(f"[dim]Current session: {executor_instance.session_id}[/dim]")
                    else:
                        self.console.print("[yellow]⚠️ Session management not available for this executor.[/yellow]")
                    continue
                
                # Handle restore session command
                if user_input.lower() in ("/restore", "restore", "/latest", "latest"):
                    if executor_instance and hasattr(executor_instance, 'restore_session'):
                        restored_id = executor_instance.restore_session()
                        # Update session_id_info display
                        self.console.print(f"[dim]Current session: {restored_id}[/dim]")
                    else:
                        self.console.print("[yellow]⚠️ Session restore not available for this executor.[/yellow]")
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
                
                # Handle skills command
                if user_input.lower() in ("/skills", "skills"):
                    try:
                        # First, load skills from plugin directories
                        from .runtime.cli import CliRuntime
                        from pathlib import Path

                        runtime = CliRuntime()
                        runtime.cli = self  # Set cli reference for console output
                        loaded_skills = await runtime._load_skills()
                        
                        # Display loading results from plugins
                        if loaded_skills:
                            total_loaded = sum(loaded_skills.values())
                            if total_loaded > 0:
                                self.console.print(f"[green]✅ Loaded {total_loaded} skill(s) from {len([k for k, v in loaded_skills.items() if v > 0])} plugin(s)[/green]")
                            else:
                                self.console.print("[dim]No new skills loaded from plugins.[/dim]")
                        
                        # Get all skills from registry (including newly loaded ones)
                        registry = get_skill_registry()
                        all_skills = registry.get_all_skills()
                        
                        if not all_skills:
                            self.console.print("[yellow]No skills available.[/yellow]")
                            continue
                        
                        # Separate skills into plugin and user skills
                        plugin_skills = {}
                        user_skills = {}
                        
                        for skill_name, skill_data in all_skills.items():
                            skill_path = skill_data.get("skill_path", "")
                            # Determine if skill is from plugin or user
                            # Plugin skills: from inner_plugins or .aworld directories
                            if skill_path and ("inner_plugins" in skill_path or ".aworld" in skill_path):
                                plugin_skills[skill_name] = skill_data
                            else:
                                user_skills[skill_name] = skill_data
                        
                        # Helper function to create and display a skills table
                        def display_skills_table(skills_dict, title):
                            if not skills_dict:
                                return
                            
                            table = Table(title=title, box=box.ROUNDED, width=None)
                            table.add_column("Name", style="magenta", width=30)
                            table.add_column("Description", style="green")
                            
                            for skill_name, skill_data in sorted(skills_dict.items()):
                                desc = skill_data.get("description") or skill_data.get("desc") or "No description"
                                # Truncate description if too long
                                if len(desc) > 60:
                                    desc = desc[:57] + "..."
                                
                                table.add_row(skill_name, desc)
                            
                            self.console.print(table)
                            self.console.print(f"[dim]Total: {len(skills_dict)} skill(s)[/dim]")
                        
                        # Display User skills first
                        if user_skills:
                            display_skills_table(user_skills, "User Skills")
                            self.console.print()  # Add spacing between tables
                        
                        # Display Plugin skills
                        if plugin_skills:
                            display_skills_table(plugin_skills, "Plugin Skills")
                        
                        # Display overall total
                        if plugin_skills and user_skills:
                            self.console.print(f"[dim]Overall Total: {len(all_skills)} skill(s)[/dim]")
                    except Exception as e:
                        self.console.print(f"[red]Error loading skills: {e}[/red]")
                    continue
                
                # Handle agents command
                if user_input.lower() in ("/agents", "agents"):
                    try:
                        from .runtime.cli import CliRuntime
                        from .runtime.loaders import PluginLoader
                        from pathlib import Path
                        from aworld.experimental.registry_workspace.agent_version_control_registry import _default_agent_registry
                        import os

                        built_in_agents = []
                        user_agents = []
                        base_path = os.environ.get('AGENT_REGISTRY_STORAGE_PATH', './data/agent_registry')
                        
                        # Load Built-in agents from plugins using PluginLoader
                        try:
                            # Get built-in plugin directories
                            runtime = CliRuntime()
                            plugin_dirs = runtime.plugin_dirs
                            
                            # Load agents from each plugin using PluginLoader
                            for plugin_dir in plugin_dirs:
                                try:
                                    loader = PluginLoader(plugin_dir, console=self.console)
                                    # Load agents from plugin (this also loads skills internally)
                                    plugin_agents = await loader.load_agents()
                                    # Mark as Built-in agents
                                    for agent in plugin_agents:
                                        if not hasattr(agent, 'source_type') or not agent.source_type:
                                            agent.source_type = "BUILT-IN"
                                    built_in_agents.extend(plugin_agents)
                                except Exception as e:
                                    self.console.print(f"[yellow]⚠️ Failed to load Built-in agents from plugin {plugin_dir.name}: {e}[/yellow]")
                        except Exception as e:
                            self.console.print(f"[yellow]⚠️ Failed to load Built-in agents from plugins: {e}[/yellow]")
                        
                        # Load User agents from AgentVersionControlRegistry default instance
                        try:
                            agent_list = await _default_agent_registry.list_desc()
                            for name, desc in agent_list:
                                agent_info = AgentInfo(
                                    name=name,
                                    desc=desc,
                                    source_type="USER",
                                    source_location=base_path
                                )
                                user_agents.append(agent_info)
                        except Exception as e:
                            self.console.print(f"[yellow]⚠️ Failed to load User agents from registry: {e}[/yellow]")
                        
                        # Display Built-in agents in a separate table
                        if built_in_agents:
                            self.display_agents(built_in_agents, source_type="BUILT-IN")
                        else:
                            self.console.print("[dim]No Built-in agents available.[/dim]")
                        
                        # Display User agents in a separate table
                        if user_agents:
                            self.display_agents(user_agents, source_type="USER", source_location=base_path)
                        else:
                            self.console.print("[dim]No User agents available.[/dim]")
                        
                        if not built_in_agents and not user_agents:
                            self.console.print("[yellow]No agents available.[/yellow]")
                    except Exception as e:
                        self.console.print(f"[red]Error loading agents: {e}[/red]")
                    continue

                # Print agent name before response
                self.console.print(f"[bold green]{agent_name}[/bold green]:")
                
                try:
                    # File parsing is now handled by FileParseHook automatically
                    # Just pass the user input as-is, the hook will process @filename references
                    # Execute the task/chat (FileParseHook will handle file parsing)
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

