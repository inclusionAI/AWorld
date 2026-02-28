"""
Registry for session (slash) commands in interactive chat.

Session commands are in-chat commands like /memory, /skills, etc. Plugins
register handlers here; console.run_chat_session loads plugins and dispatches
user input to the matching handler. When dispatching, the current executor's
context is passed as the second argument (may be None if no executor).

Usage (in a plugin module, on import):

    from aworld_cli.core.session_commands import register_session_command

    async def handle_memory(cli, context):
        ...

    register_session_command("/memory", handle_memory, "Edit project or global MEMORY.md")
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Tuple

# Handler: async (cli: AWorldCLI, context: Any) -> None. context is executor.context or None.
SessionCommandHandler = Callable[..., Awaitable[None]]

_registry: Dict[str, Tuple[SessionCommandHandler, str]] = {}


def register_session_command(
    name: str,
    handler: SessionCommandHandler,
    description: str = "",
) -> None:
    """
    Register a session (slash) command for interactive chat.

    Args:
        name: Command name including slash, e.g. "/memory".
        handler: Async function (cli, context) -> None. cli is AWorldCLI; context is
            current executor.context (or None when no executor).
        description: Short description for help and completion meta.
    """
    _registry[name] = (handler, description or name)


def get_all_session_commands() -> Dict[str, Tuple[SessionCommandHandler, str]]:
    """
    Return all registered session commands.

    Returns:
        Dict mapping command name to (handler, description).
    """
    return dict(_registry)


def load_session_command_plugins() -> None:
    """
    Load plugin modules that register session commands.

    Importing these modules causes them to call register_session_command.
    Add new plugins here as they are introduced.
    """
    try:
        from aworld_cli.inner_plugins.memory import commands  # noqa: F401
    except ImportError:
        pass
    try:
        from aworld_cli.inner_plugins.skills import commands  # noqa: F401
    except ImportError:
        pass
