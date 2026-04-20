from typing import Dict, List, Protocol

from .cli import run_batch_command


class CliCommandHandler(Protocol):
    """
    Protocol for CLI command handlers.

    A CLI command handler is a callable that receives the remaining
    command-line arguments (excluding program name and command name)
    and returns a process exit code.
    """

    def __call__(self, argv: List[str]) -> int:
        """
        Execute CLI command.

        Args:
            argv: Remaining arguments, excluding program name and command name.

        Returns:
            Process exit code. Zero indicates success, non-zero indicates failure.
        """


def get_commands() -> Dict[str, CliCommandHandler]:
    """
    Get CLI commands provided by the batch inner plugin.

    Returns:
        Mapping from command name to handler. Includes both the primary
        'batch-job' command and a 'batch' alias for backward compatibility.
    """
    return {
        "batch-job": run_batch_command,
        "batch": run_batch_command,
    }

