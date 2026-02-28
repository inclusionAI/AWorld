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

from typing import Any, Awaitable, Callable, Dict, List, Tuple

# Handler: async (cli: AWorldCLI, context: Any) -> None. context is executor.context or None.
SessionCommandHandler = Callable[..., Awaitable[None]]

# Dynamic provider: async (session_commands, cli, executor, agent_name) -> None; mutates session_commands.
SessionCommandDynamicProvider = Callable[..., Awaitable[None]]

_registry: Dict[str, Tuple[SessionCommandHandler, str]] = {}
_dynamic_providers: List[SessionCommandDynamicProvider] = []


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


def register_session_command_dynamic_provider(provider: SessionCommandDynamicProvider) -> None:
    """
    Register a dynamic provider that adds session commands when a chat session starts.

    The provider is invoked with (session_commands, cli, executor, agent_name) and should
    mutate session_commands in place (e.g. add /<skill_name> from loaded skills).
    Called automatically by merge_dynamic_session_commands() from console.run_chat_session.

    Example (in a plugin, on import):
        from aworld_cli.core.session_commands import register_session_command_dynamic_provider
        async def my_provider(session_commands, cli, executor, agent_name):
            session_commands["/myskill"] = (my_handler, "My skill")
        register_session_command_dynamic_provider(my_provider)
    """
    _dynamic_providers.append(provider)


async def merge_dynamic_session_commands(
    session_commands: Dict[str, Tuple[SessionCommandHandler, str]],
    cli: Any,
    executor: Any,
    agent_name: str,
) -> None:
    """
    Run all registered dynamic providers to merge per-session commands into session_commands.

    Call this after get_all_session_commands() when starting a chat session (cli has
    executor and agent_name). Each provider may add entries to session_commands (e.g.
    /<skill_name> from loaded skills). Mutates session_commands in place.
    """
    import asyncio

    for provider in _dynamic_providers:
        try:
            if asyncio.iscoroutinefunction(provider):
                await provider(session_commands, cli, executor, agent_name)
            else:
                provider(session_commands, cli, executor, agent_name)
        except Exception:
            pass


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
        from aworld_cli.inner_plugins.messages import commands  # noqa: F401
    except ImportError:
        pass
    try:
        from aworld_cli.inner_plugins.skills import commands  # noqa: F401
    except ImportError:
        pass
