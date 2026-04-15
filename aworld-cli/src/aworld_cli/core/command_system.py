"""
Slash command framework for AWorld CLI.
Implements prompt-based commands following Claude Code's pattern.

Architecture:
- Tool Commands: Execute directly without agent involvement (e.g., /help, /config)
- Prompt Commands: Generate prompts for agent to execute (e.g., /commit, /review)

Command Flow:
    User types "/command args"
      ↓
    CLI detects "/" prefix
      ↓
    CommandRegistry routes to command
      ↓
    [Tool Command]              [Prompt Command]
         ↓                           ↓
    Command.execute()          Command.get_prompt()
         ↓                           ↓
    Direct result              Agent → Tools → Result
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class CommandContext:
    """Context passed to command handlers"""
    cwd: str
    user_args: str
    sandbox: Optional[Any] = None
    agent_config: Optional[Any] = None
    runtime: Optional[Any] = None

    def __post_init__(self):
        """Validate context"""
        if not self.cwd:
            import os
            self.cwd = os.getcwd()


class Command(ABC):
    """
    Base class for slash commands.

    Commands can be either:
    1. Tool Commands: Direct execution (implement execute())
       - Fast, deterministic, no LLM call
       - Examples: /help, /config, /list

    2. Prompt Commands: Agent-mediated (implement get_prompt())
       - Leverages agent intelligence
       - Examples: /commit, /review, /diff

    Usage:
        @register_command
        class MyCommand(Command):
            @property
            def name(self) -> str:
                return "mycommand"

            @property
            def description(self) -> str:
                return "Does something useful"

            async def execute(self, context: CommandContext) -> str:
                # For tool commands
                return "Result"

            # OR

            async def get_prompt(self, context: CommandContext) -> str:
                # For prompt commands
                return "Prompt for agent"
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Command name (without / prefix)"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Short description shown in help"""
        pass

    @property
    def command_type(self) -> str:
        """
        Command type: 'tool' or 'prompt'.
        - 'tool': Direct execution (no agent)
        - 'prompt': Agent-mediated execution (default)
        """
        return "prompt"  # Default to prompt commands

    @property
    def allowed_tools(self) -> List[str]:
        """
        Whitelist of tools this command can use (for prompt commands).

        Supports wildcards:
        - "terminal:git*" allows all git terminal commands
        - "git_*" allows all git_ tools

        Ignored for tool commands.

        Example:
            return ["terminal:git*", "git_status", "git_diff", "git_commit"]
        """
        return []

    @property
    def completion_items(self) -> Dict[str, str]:
        """
        Optional slash-completion entries exposed by the command.

        Returns:
            Mapping of completion phrase -> user-facing description.
            Example: {"/cron show": "查看单个任务详情"}
        """
        return {}

    async def execute(self, context: CommandContext) -> str:
        """
        Direct execution for tool commands.
        Override this for commands that don't need agent involvement.

        Flow: Command → Result (no agent)

        Example:
            async def execute(self, context: CommandContext) -> str:
                return "Command output"
        """
        raise NotImplementedError(
            f"Command '{self.name}' does not support direct execution. "
            f"Either implement execute() for tool commands or get_prompt() for prompt commands."
        )

    async def get_prompt(self, context: CommandContext) -> str:
        """
        Generate prompt for agent-mediated commands.
        Override this for commands that leverage agent intelligence.

        Flow: Command → Agent → Tools → Result

        Example:
            async def get_prompt(self, context: CommandContext) -> str:
                # Gather context
                status = subprocess.run(["git", "status"], ...).stdout

                # Generate prompt
                return f'''## Task

                Current status:
                {status}

                Please analyze and take action...
                '''
        """
        raise NotImplementedError(
            f"Command '{self.name}' does not support prompt generation. "
            f"Either implement get_prompt() for prompt commands or execute() for tool commands."
        )

    async def pre_execute(self, context: CommandContext) -> Optional[str]:
        """
        Optional hook before execution (both command types).
        Return error message if command cannot proceed.

        Example:
            async def pre_execute(self, context: CommandContext) -> Optional[str]:
                if not is_git_repo(context.cwd):
                    return "Not a git repository"
                return None
        """
        return None

    async def post_execute(self, context: CommandContext, result: Any) -> None:
        """Optional hook after execution (both command types)"""
        pass


class CommandRegistry:
    """
    Central registry for all slash commands.
    Commands are registered at module import time via @register_command decorator.

    Usage:
        # Register a command
        @register_command
        class MyCommand(Command):
            ...

        # Get a command
        cmd = CommandRegistry.get("mycommand")

        # List all commands
        commands = CommandRegistry.list_commands()

        # Get help text
        help_text = CommandRegistry.help_text()
    """

    _commands: Dict[str, Command] = {}

    @classmethod
    def register(cls, command: Command) -> None:
        """
        Register a command.

        Args:
            command: Command instance to register

        Raises:
            ValueError: If command name already registered
        """
        if command.name in cls._commands:
            raise ValueError(f"Command '{command.name}' already registered")
        cls._commands[command.name] = command

    @classmethod
    def get(cls, name: str) -> Optional[Command]:
        """
        Get command by name.

        Args:
            name: Command name (without / prefix)

        Returns:
            Command instance or None if not found
        """
        return cls._commands.get(name)

    @classmethod
    def unregister(cls, name: str) -> None:
        """Remove a registered command if present."""
        cls._commands.pop(name, None)

    @classmethod
    def list_commands(cls) -> List[Command]:
        """
        Get all registered commands.

        Returns:
            List of all registered Command instances
        """
        return list(cls._commands.values())

    @classmethod
    def snapshot(cls) -> Dict[str, Command]:
        """Return a shallow copy of the current registry (useful for tests)."""
        return dict(cls._commands)

    @classmethod
    def restore(cls, snapshot: Dict[str, Command]) -> None:
        """Replace registry contents with a previous snapshot."""
        cls._commands = dict(snapshot)

    @classmethod
    def help_text(cls) -> str:
        """
        Generate help text for all commands.

        Returns:
            Formatted help text listing all available commands
        """
        if not cls._commands:
            return "No commands available."

        lines = ["Available commands:"]
        for cmd in sorted(cls._commands.values(), key=lambda c: c.name):
            lines.append(f"  /{cmd.name:<15} {cmd.description}")
        return "\n".join(lines)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered commands (useful for testing)"""
        cls._commands.clear()


def register_command(cmd_class: type) -> type:
    """
    Decorator to register a command class.

    Usage:
        @register_command
        class MyCommand(Command):
            @property
            def name(self) -> str:
                return "mycommand"

            @property
            def description(self) -> str:
                return "Does something"

            async def execute(self, context: CommandContext) -> str:
                return "Result"

    Args:
        cmd_class: Command class to register

    Returns:
        The same command class (for chaining)
    """
    # Instantiate command and register
    command_instance = cmd_class()
    CommandRegistry.register(command_instance)
    return cmd_class
