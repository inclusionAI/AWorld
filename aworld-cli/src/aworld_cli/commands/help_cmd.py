"""
/help command - Show available commands

This is a Tool Command (direct execution, no agent involvement).
"""
from aworld_cli.core.command_system import Command, CommandContext, register_command


@register_command
class HelpCommand(Command):
    """
    Show all available slash commands.

    Type: Tool Command (direct execution)
    Flow: Command → Result (no agent)
    """

    @property
    def name(self) -> str:
        return "help"

    @property
    def description(self) -> str:
        return "Show all available commands"

    @property
    def command_type(self) -> str:
        return "tool"  # Direct execution, no agent

    async def execute(self, context: CommandContext) -> str:
        """Display all available commands"""
        from aworld_cli.core.command_system import CommandRegistry

        help_text = CommandRegistry.help_text()

        # Add usage instructions
        full_help = f"""{help_text}

Usage:
  /<command> [args]

Examples:
  /help           Show this help message
  /commit         Create a git commit with intelligent analysis
  /review         Review current changes for code quality
  /diff [ref]     Show and summarize changes (default: HEAD)

Type any command followed by arguments if needed.
For natural language requests, just type without the / prefix.
"""
        return full_help
