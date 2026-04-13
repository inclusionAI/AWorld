"""
AWorld CLI slash commands.

This module contains all slash command implementations.
Commands are automatically registered when imported.

Available Commands:
- /help: Show all available commands (tool command)
- /commit: Create intelligent git commits (prompt command)
- /review: Perform code review on changes (prompt command)
- /diff: Summarize git changes (prompt command)
- /history: View tool call history (tool command)
- /cron: Manage scheduled tasks (tool command)
- /dispatch: Submit task to background execution (tool command)
- /tasks: Manage background tasks (tool command)

Usage:
    # Import to register all commands
    from aworld_cli import commands

    # Or import specific commands
    from aworld_cli.commands.help import HelpCommand
    from aworld_cli.commands.commit import CommitCommand
"""

# Import all command modules to trigger @register_command
from . import help_cmd
from . import commit
from . import review
from . import diff
from . import history
from . import cron_cmd
from . import dispatch
from . import tasks

__all__ = [
    "help_cmd",
    "commit",
    "review",
    "diff",
    "history",
    "cron_cmd",
    "dispatch",
    "tasks",
]
