import asyncio
import sys
from pathlib import Path
from typing import List, Callable, Any, Union, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich import box
from rich.color import Color
from rich.panel import Panel
from rich.prompt import Prompt
from rich.style import Style
from rich.table import Table
from rich.text import Text

from aworld.logs.util import logger
from ._globals import console
from .core.skill_registry import get_skill_registry
from .models import AgentInfo
from .user_input import UserInputHandler
from .ui import select_menu_option
from .core.session_commands import get_all_session_commands


# ... existing imports ...

class AWorldCLI:
    def __init__(self):
        self.console = console
        self.user_input = UserInputHandler(console)

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
        
        # Config source
        from .core.config import get_config
        source_type, source_path = get_config().get_config_source(".env")
        if source_type == "local":
            self.console.print(f"[dim]📁 Using local config: {source_path}[/dim]")
        else:
            self.console.print(f"[dim]🌐 Using global config: {source_path}[/dim]")

        # Subtitle / Version
        subtitle = Text("🤖 Interact with your agents directly from the terminal", style="italic #875fff")
        version = Text("\n v0.1.0", style="dim white")
        
        self.console.print(Text.assemble(subtitle, version))
        self.console.print() # Padding

    async def _interactive_config_editor(self):
        """Interactive configuration editor. First menu: choose config type (e.g. model)."""
        from .core.config import get_config
        from rich.panel import Panel

        config = get_config()
        current_config = config.load_config()
        
        self.console.print(Panel(
            "[bold cyan]Configuration Editor[/bold cyan]\n\n"
            f"Config file: [dim]{config.get_config_path()}[/dim]",
            title="Config",
            border_style="cyan"
        ))
        
        # First menu: select configuration type (Back/cancel uses largest number)
        config_types = [
            ("1", "Model configuration", self._edit_models_config),
            ("2", "Skills configuration", self._edit_skills_config),
        ]
        back_key = str(len(config_types) + 1)
        self.console.print("\n[bold]Select configuration type:[/bold]")
        for key, label, _ in config_types:
            self.console.print(f"  {key}. {label}")
        self.console.print(f"  {back_key}. Back (cancel)")
        
        choice = Prompt.ask("\nChoice", default="1")
        if choice == back_key:
            self.console.print("[dim]Cancelled.[/dim]")
            return
        
        handler = None
        for key, label, fn in config_types:
            if choice == key:
                handler = fn
                break
        if not handler:
            self.console.print("[red]Invalid selection.[/red]")
            return
        
        await handler(config, current_config)
    
    async def _edit_models_config(self, config, current_config: dict):
        """Edit models section of config (providers, api_key, model, base_url)."""
        from rich.table import Table
        
        if 'models' not in current_config:
            current_config['models'] = {}
        
        providers = ['openai', 'anthropic', 'gemini']
        self.console.print("\n[bold]Model provider:[/bold]")
        for i, provider in enumerate(providers, 1):
            self.console.print(f"  {i}. {provider}")
        
        provider_choice = Prompt.ask("\nSelect provider (1-3)", default="1")
        try:
            provider_idx = int(provider_choice) - 1
            if provider_idx < 0 or provider_idx >= len(providers):
                self.console.print("[red]Invalid selection[/red]")
                return
            selected_provider = providers[provider_idx]
        except ValueError:
            self.console.print("[red]Invalid selection[/red]")
            return
        
        if selected_provider not in current_config['models']:
            current_config['models'][selected_provider] = {}
        
        provider_config = current_config['models'][selected_provider]
        
        self.console.print(f"\n[bold]Configuring {selected_provider}[/bold]")
        current_api_key = provider_config.get('api_key', '')
        if current_api_key:
            masked_key = current_api_key[:8] + "..." if len(current_api_key) > 8 else "***"
            self.console.print(f"  [dim]Current API key: {masked_key}[/dim]")
        api_key = Prompt.ask(
            f"  {selected_provider.upper()}_API_KEY",
            default=current_api_key,
            password=True
        )
        if api_key:
            provider_config['api_key'] = api_key
        
        current_model = provider_config.get('model', '')
        if current_model:
            self.console.print(f"  [dim]Default: {current_model}[/dim]")
        else:
            self.console.print("  [dim]e.g. gpt-4, claude-3-opus · press Enter to leave empty[/dim]")
        model = Prompt.ask("  Model name", default=current_model)
        if model:
            provider_config['model'] = model
        
        current_base_url = provider_config.get('base_url', '')
        self.console.print("  [dim]Optional · press Enter to leave empty[/dim]")
        base_url = Prompt.ask("  Base URL", default=current_base_url)
        if base_url:
            provider_config['base_url'] = base_url
        
        config.save_config(current_config)
        self.console.print(f"\n[green]✅ Configuration saved to {config.get_config_path()}[/green]")
        
        table = Table(title=f"{selected_provider.upper()} Configuration", box=box.ROUNDED)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        for key, value in provider_config.items():
            if key == 'api_key':
                masked_value = value[:8] + "..." if len(str(value)) > 8 else "***"
                table.add_row(key, masked_value)
            else:
                table.add_row(key, str(value))
        self.console.print()
        self.console.print(table)

    async def _edit_skills_config(self, config, current_config: dict):
        """Edit skills section of config (global SKILLS_PATH and per-agent XXX_SKILLS_PATH)."""
        default_skills_path = str(Path.home() / ".aworld" / "skills")
        if 'skills' not in current_config:
            current_config['skills'] = {}

        skills_cfg = current_config['skills']
        self.console.print("\n[bold]Skills paths:[/bold]")
        self.console.print("  [dim]Paths are relative to home or absolute. Use semicolon (;) to separate multiple paths. Enter to keep, '-' to clear.[/dim]\n")

        # Global SKILLS_PATH
        current = skills_cfg.get('skills_path', '')
        val = Prompt.ask("  SKILLS_PATH (global)", default=current or default_skills_path)
        v = val.strip() if val else ''
        if v and v != '-':
            skills_cfg['skills_path'] = v
        elif v == '-' or (not v and current):
            skills_cfg.pop('skills_path', None)

        # Per-agent paths (same default as SKILLS_PATH)
        for label, key in [
            ("EVALUATOR_SKILLS_PATH (evaluator)", "evaluator_skills_path"),
            ("EXPLORER_SKILLS_PATH (explorer)", "explorer_skills_path"),
            ("AWORLD_SKILLS_PATH (aworld)", "aworld_skills_path"),
            ("DEVELOPER_SKILLS_PATH (developer)", "developer_skills_path"),
        ]:
            current = skills_cfg.get(key, '')
            val = Prompt.ask(f"  {label}", default=current or default_skills_path)
            v = val.strip() if val else ''
            if v and v != '-':
                skills_cfg[key] = v
            elif v == '-' or (not v and current):
                skills_cfg.pop(key, None)

        if not skills_cfg:
            current_config.pop('skills', None)
        else:
            current_config['skills'] = skills_cfg

        config.save_config(current_config)
        self.console.print(f"\n[green]✅ Configuration saved to {config.get_config_path()}[/green]")
        table = Table(title="Skills Configuration", box=box.ROUNDED)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        for k, v in (current_config.get('skills') or {}).items():
            table.add_row(k, str(v)[:60] + ("..." if len(str(v)) > 60 else ""))
        if current_config.get('skills'):
            self.console.print()
            self.console.print(table)

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

        from pathlib import Path
        table = Table(title="Available Agents", box=box.ROUNDED)
        table.add_column("Name", style="magenta")
        table.add_column("Description", style="green")
        _addr_max = 48
        table.add_column("Address", style="dim", no_wrap=False, max_width=_addr_max)

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

            if not agent_source_location or agent_source_location.strip() == "":
                addr_cell = Text("—", style="dim")
            else:
                address = agent_source_location.strip()
                p = Path(address)
                link_target = p.parent if p.suffix else p
                try:
                    link_url = link_target.resolve().as_uri()
                except (OSError, RuntimeError):
                    link_url = ""
                if link_url and len(address) > _addr_max:
                    addr_display = address[: _addr_max - 3] + "..."
                    addr_cell = Text(addr_display, style=Style(dim=True, link=link_url))
                elif link_url:
                    addr_cell = Text(address, style=Style(dim=True, link=link_url))
                else:
                    addr_display = address[: _addr_max - 3] + "..." if len(address) > _addr_max else address
                    addr_cell = Text(addr_display, style="dim")

            table.add_row(agent.name, desc, addr_cell)

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

        # Default: use first agent (Aworld) without prompting or showing the agents table
        selected_agent = agents[0]
        return selected_agent
    
    def select_team(self, teams: List[AgentInfo], source_type: str = "LOCAL", source_location: str = "") -> Optional[AgentInfo]:
        """
        Alias for select_agent for backward compatibility.
        Use select_agent instead.
        """
        return self.select_agent(teams, source_type, source_location)

    def _visualize_team(self, executor_instance: Any):
        """Visualize the structure of the current team in a full-width split-screen layout."""
        from rich.columns import Columns
        try:
            from rich.console import Group
        except ImportError:
            try:
                from rich import Group
            except ImportError:
                # Fallback for older Rich versions
                class Group:
                    """Fallback Group class for older Rich versions."""
                    def __init__(self, *renderables):
                        self.renderables = renderables
                    
                    def __rich_console__(self, console, options):
                        for renderable in self.renderables:
                            yield renderable
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.align import Align
        from rich import box

        # 1. Get swarm from executor
        swarm = getattr(executor_instance, "swarm", None)
        if not swarm:
            self.console.print("[yellow]Current agent does not support visualization (not a swarm).[/yellow]")
            return

        # 2. Get agent graph
        graph = getattr(swarm, "agent_graph", None)
        if not graph:
            self.console.print("[yellow]No agent graph found in swarm.[/yellow]")
            return

        # --- Gather Data ---

        # Goal
        goal_text = "Run task"
        if hasattr(executor_instance, "task"):
            task = executor_instance.task
            if hasattr(task, "input") and task.input:
                 goal_text = str(task.input)
            elif hasattr(task, "name") and task.name:
                 goal_text = task.name

        if goal_text == "Run task" and hasattr(swarm, "task") and swarm.task:
             goal_text = str(swarm.task)

        if len(goal_text) > 100:
            goal_text = goal_text[:97] + "..."

        # Active skills
        active_skill_names = set()
        if hasattr(executor_instance, 'get_skill_status'):
             try:
                 status = executor_instance.get_skill_status()
                 active_names = status.get('active_names', [])
                 if active_names:
                     active_skill_names = set(active_names)
             except:
                 pass

        # Build Agent Panels
        agent_panels = []
        if graph and graph.agents:
            for agent in graph.agents.values():
                agent_skills = set()
                if hasattr(agent, "skill_configs") and agent.skill_configs:
                     for skill_name in agent.skill_configs.keys():
                         agent_skills.add(skill_name)

                agent_tools = set()
                mcp_tools = set()
                if hasattr(agent, "tools") and agent.tools:
                    for tool in agent.tools:
                        if isinstance(tool, dict) and "function" in tool:
                            agent_tools.add(tool["function"].get("name", "unknown"))
                        elif hasattr(tool, "name"):
                             agent_tools.add(tool.name)

                if hasattr(agent, "mcp_servers") and agent.mcp_servers:
                    for s in agent.mcp_servers:
                         mcp_tools.add(s)

                content_parts = []
                if agent_skills:
                    skills_list = []
                    for s in list(agent_skills)[:5]:
                        if s in active_skill_names:
                             skills_list.append(f"[bold green]• {s}[/bold green]")
                        else:
                             skills_list.append(f"• {s}")
                    if len(agent_skills) > 5:
                        skills_list.append(f"[dim]...({len(agent_skills)-5})[/dim]")
                    content_parts.append(f"[bold cyan]Skills:[/bold cyan]\n" + "\n".join(skills_list))

                tools_list = []
                built_in_list = ["Read", "Write", "Bash", "Grep"]
                has_builtins = any(t in agent_tools for t in built_in_list)
                if has_builtins:
                     tools_list.append("Built-in")
                if mcp_tools:
                     tools_list.append(f"MCP: {len(mcp_tools)}")
                custom_tools = [t for t in agent_tools if t not in built_in_list]
                if custom_tools:
                     tools_list.append(f"Custom: {len(custom_tools)}")

                if tools_list:
                    content_parts.append(f"[bold yellow]Tools:[/bold yellow]\n" + ", ".join(tools_list))

                agent_content = "\n".join(content_parts) if content_parts else "[dim]-[/dim]"

                agent_panel = Panel(
                    agent_content,
                    title=f"[bold]{agent.name()}[/bold]",
                    box=box.ROUNDED,
                    border_style="blue",
                    padding=(0, 1),
                    expand=True
                )
                agent_panels.append(agent_panel)

        # --- Build Layout ---

        layout = Layout()
        layout.split_row(
            Layout(name="process", ratio=1),
            Layout(name="team", ratio=1)
        )

        # --- Process Column (Left) ---

        goal_panel = Panel(
            Align.center(f"[bold]GOAL[/bold]\n\"{goal_text}\""),
            box=box.ROUNDED,
            style="white",
            border_style="green"
        )

        loop_panel = Panel(
             Align.center("[bold]AGENT LOOP[/bold]\n[dim]observe → think → act → learn → repeat[/dim]"),
             box=box.ROUNDED,
             style="white"
        )

        hooks_panel = Panel(
             Align.center("[bold]HOOKS[/bold]\n[dim]guard rails, logging, human-in-the-loop[/dim]"),
             box=box.ROUNDED,
             style="white"
        )

        output_panel = Panel(
             Align.center("[bold]STRUCTURED OUTPUT[/bold]\n[dim]validated JSON matching your schema[/dim]"),
             box=box.ROUNDED,
             style="white",
             border_style="green"
        )

        arrow_down = Align.center("│\n▼")

        process_content = Group(
            goal_panel,
            arrow_down,
            loop_panel,
            arrow_down,
            hooks_panel,
            arrow_down,
            output_panel
        )

        layout["process"].update(Panel(process_content, title="Process Flow", box=box.ROUNDED))

        # --- Team Column (Right) ---

        swarm_label = Align.center("[bold]SWARM[/bold]")

        # Use Columns for agents if there are many, or Stack if few
        # Using Columns with expand=True to fill width
        if len(agent_panels) > 1:
             agents_display = Columns(agent_panels, expand=True, equal=True)
        else:
             agents_display = Group(*[Align.center(p) for p in agent_panels])

        team_content = Group(
            swarm_label,
            Align.center("│\n▼"),
            agents_display
        )

        layout["team"].update(Panel(team_content, title="Team Structure", box=box.ROUNDED))

        # Print layout full width
        self.console.print(layout)
    
    async def _esc_key_listener(self):
        """
        Background listener for Esc key to interrupt currently executing tasks.
        This function runs in the background, continuously listening for keyboard input.
        """
        try:
            from prompt_toolkit import Application
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import Layout
            from prompt_toolkit.layout.containers import Window
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.formatted_text import FormattedText
            
            # Create a hidden window to capture Esc key
            kb = KeyBindings()
            
            # Store reference to currently executing task
            if not hasattr(self, '_current_executor_task'):
                self._current_executor_task = None
            
            def handle_esc(event):
                """Handle Esc key press"""
                if hasattr(self, '_current_executor_task') and self._current_executor_task:
                    if not self._current_executor_task.done():
                        self._current_executor_task.cancel()
                        self.console.print("\n[yellow]⚠️ Task interrupted by Esc key[/yellow]")
            
            kb.add("escape")(handle_esc)
            
            # Create an invisible control
            control = FormattedTextControl(
                text=FormattedText([("", "")]),
                focusable=True
            )
            
            window = Window(content=control, height=0)
            layout = Layout(window)
            
            # Create a hidden application to listen for keyboard
            app = Application(
                layout=layout,
                key_bindings=kb,
                full_screen=False,
                mouse_support=False
            )
            
            # Run application in background
            await asyncio.to_thread(app.run)
        except Exception:
            # If prompt_toolkit is not available or error occurs, fail silently
            pass

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
            f"Type '/memory' to edit project or global MEMORY.md.\n"
            f"Type '/skills' to list all available skills.\n"
            f"Type '/agents' to list all available agents.\n"
            f"Use @filename to include images or text files (e.g., @photo.jpg or @document.txt)."
        )
        self.console.print(Panel(help_text, style="blue"))
        # Session commands already loaded in runtime.start(); merge per-session skill commands
        session_commands = get_all_session_commands()
        # Register each loaded skill as a slash command; selecting one calls context.active_skill
        try:
            from aworld_cli.inner_plugins.skills.commands import register_skill_commands_into
            await register_skill_commands_into(session_commands, self, executor_instance, agent_name)
        except Exception:
            pass

        # Check if we're in a real terminal (not IDE debugger or redirected input)
        is_terminal = sys.stdin.isatty()
        
        # Setup completer and session only if in terminal
        agent_names = [a.name for a in available_agents] if available_agents else []
        session = None
        
        if is_terminal:
            # 用整行前缀匹配：/ski → /skills、/age → /agents；meta_dict 在补全菜单中显示描述（命令左、描述右）
            slash_cmds = [
                "/agents", "/new", "/restore", "/latest",
                "/exit", "/quit", "/switch",
            ]
            slash_cmds.extend(session_commands.keys())
            switch_with_agents = [f"/switch {n}" for n in agent_names] if agent_names else []
            all_words = slash_cmds + switch_with_agents + ["exit", "quit"]
            meta_dict = {
                "/agents": "List available agents",
                "/new": "Create a new session",
                "/restore": "Restore to the latest session",
                "/latest": "Restore to the latest session",
                "/exit": "Exit chat",
                "/quit": "Exit chat",
                "/switch": "Switch to another agent",
                "exit": "Exit chat",
                "quit": "Exit chat",
            }
            for cmd_name, (_, desc) in session_commands.items():
                meta_dict[cmd_name] = desc
            # Aliases without leading slash for natural input
            for cmd_name in session_commands:
                alias = cmd_name.lstrip("/")
                if alias and alias not in meta_dict:
                    meta_dict[alias] = meta_dict.get(cmd_name, cmd_name)
            for n in agent_names:
                meta_dict[f"/switch {n}"] = f"Switch to agent: {n}"
            completer = WordCompleter(
                all_words,
                ignore_case=True,
                sentence=True,
                meta_dict=meta_dict,
            )
            # 历史记录文件：上/下方向键可浏览并加载已执行过的指令
            from pathlib import Path
            history_path = Path.home() / ".aworld" / "cli_history"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            session = PromptSession(
                completer=completer,
                complete_while_typing=True,  # 输入时即显示补全列表（带描述）
                history=FileHistory(str(history_path)),
            )

        while True:
            try:
                # Use prompt_toolkit in terminal, plain input() in non-terminal (e.g., IDE debugger)
                if is_terminal and session:
                    # Use prompt_toolkit for input with completion
                    # We use HTML for basic coloring of the prompt
                    prompt_text = "<b><cyan>You</cyan></b>: "
                    user_input = await asyncio.to_thread(session.prompt, HTML(prompt_text))
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
                
                # Dispatch to session command plugins (e.g. /memory, /skills); pass current context
                session_handled = False
                current_context = getattr(executor_instance, "context", None) if executor_instance else None
                for cmd_name, (handler, _) in session_commands.items():
                    alias = cmd_name.lstrip("/")
                    if user_input.lower() in (cmd_name.lower(), alias.lower()):
                        await handler(self, current_context)
                        session_handled = True
                        break
                if session_handled:
                    continue

                # Handle agents command
                if user_input.lower() in ("/agents", "agents"):
                    try:
                        from .runtime.cli import CliRuntime
                        from .runtime.loaders import PluginLoader
                        from aworld_cli.core.agent_scanner import global_agent_registry
                        from pathlib import Path
                        import os

                        built_in_agents = []
                        user_agents = []
                        base_path = os.path.expanduser(
                            os.environ.get('AGENTS_PATH', '~/.aworld/agents'))

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
                                    logger.info(f"Failed to load Built-in agents from plugin {plugin_dir.name}: {e}")
                                    import traceback
                                    logger.debug(traceback.format_exc())
                        except Exception as e:
                            logger.info(f"Failed to load Built-in agents from plugins: {e}")
                        
                        # Load User agents from AgentScanner default instance
                        try:
                            agent_list = await global_agent_registry.list_desc()
                            for item in agent_list:
                                # Handle both old format (4-tuple with version) and new format (3-tuple)
                                if len(item) == 4:
                                    name, desc, path, version = item
                                else:
                                    name, desc, path = item
                                    version = None
                                agent_info = AgentInfo(
                                    name=name,
                                    desc=desc,
                                    metadata={"version": version} if version else {},
                                    source_type="USER",
                                    source_location=base_path
                                )
                                user_agents.append(agent_info)
                        except Exception as e:
                            logger.info(f"Failed to load User agents from registry: {e}")
                        
                        # Log summary
                        total_agents = len(built_in_agents) + len(user_agents)
                        logger.info(f"Loaded {total_agents} agent(s): {len(built_in_agents)} from Built-in plugins, {len(user_agents)} from User registry ({base_path})")
                        
                        # Display Built-in agents in a separate table
                        if built_in_agents:
                            self.console.print("\n[bold cyan]Built-in Agents:[/bold cyan]")
                            self.display_agents(built_in_agents, source_type="BUILT-IN")
                        else:
                            self.console.print("[dim]No Built-in agents available.[/dim]")
                        
                        # Display User agents in a separate table
                        if user_agents:
                            self.console.print("\n[bold cyan]User Agents:[/bold cyan]")
                            self.display_agents(user_agents, source_type="USER", source_location=base_path)
                        else:
                            self.console.print("[dim]No User agents available.[/dim]")
                        
                        if not built_in_agents and not user_agents:
                            self.console.print("[yellow]⚠️  No agents available.[/yellow]")
                    except Exception as e:
                        logger.info(f"Error loading agents: {e}")
                        import traceback
                        logger.debug(traceback.format_exc())
                    continue

                # Handle visualize command
                if user_input.lower() in ("/visualize_trajectory", "visualize_trajectory"):
                    self._visualize_team(executor_instance)
                    continue

                # Handle sessions command
                if user_input.lower() in ("/sessions", "sessions"):
                    if executor_instance:
                        # Debug: Print session related attributes
                        session_attrs = {k: v for k, v in executor_instance.__dict__.items() if 'session' in k.lower()}
                        # Also check if context has session info
                        if hasattr(executor_instance, 'context') and executor_instance.context:
                            context_session_attrs = {k: v for k, v in executor_instance.context.__dict__.items() if
                                                     'session' in k.lower()}
                            session_attrs.update({f"context.{k}": v for k, v in context_session_attrs.items()})

                        if session_attrs:
                            self.console.print(f"[dim]Session Info: {session_attrs}[/dim]")
                    else:
                        self.console.print("[yellow]No executor instance available.[/yellow]")
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
                    import traceback
                    logger.error(f"Error executing task: {e} {traceback.format_exc()}")
                    self.console.print("[bold red]Error executing task:[/bold red]", end=" ")
                    continue

            except KeyboardInterrupt:
                self.console.print("\n[yellow]Session interrupted.[/yellow]")
                break
            except Exception as e:
                import traceback
                logger.error(f"Error executing task: {e} {traceback.format_exc()}")
                self.console.print("[red]An unexpected error occurred:[/red]", end=" ")

        return False

