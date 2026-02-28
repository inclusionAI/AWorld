"""
Command registry and developer-facing registration helpers for aworld-cli.

This module provides:

- CommandRegistry: Internal registry for CLI commands.
- get_command_registry: Singleton accessor for the registry.
- register_command: Thin helper to register commands in Python code.
- cli_command: Decorator for zero-boilerplate command registration.

The goal is to:

- Keep CommandRegistry as an internal implementation detail.
- Give command authors a simple Python API (or decorator) to add commands.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, TypedDict


CommandHandler = Callable[[List[str]], int]


class CommandMeta(TypedDict, total=False):
    """
    Metadata for a CLI command.

    Attributes:
        name: Command name as seen from CLI (e.g. "review").
        handler: Callable invoked with remaining argv.
        help: Short help text for usage and examples.
        origin: Logical origin of the command (core, plugin, file, etc.).
        examples: Example invocations for documentation and --examples output.
    """

    name: str
    handler: CommandHandler
    help: str
    origin: str
    examples: List[str]


class CommandRegistry:
    """
    Registry for aworld-cli commands.

    This is the single source of truth for mapping command names to handlers
    and associated metadata. CLI entrypoints should only depend on this
    abstraction when resolving commands.
    """

    def __init__(self) -> None:
        """Initialize an empty command registry."""
        self._commands: Dict[str, CommandMeta] = {}

    def register(
        self,
        name: str,
        handler: CommandHandler,
        help: Optional[str] = None,
        origin: str = "core",
        examples: Optional[List[str]] = None,
        override: bool = False,
    ) -> None:
        """
        Register a command in the registry.

        Args:
            name: Command name as seen in CLI (e.g. "review").
            handler: Callable that takes remaining argv and returns exit code.
            help: Optional short help text for this command.
            origin: Logical origin identifier (e.g. "core", "plugin:batch").
            examples: Optional list of example invocations.
            override: Whether to override an existing command with the same name.

        Raises:
            ValueError: If the command already exists and override is False.
        """
        if name in self._commands and not override:
            existing = self._commands[name]
            existing_origin = existing.get("origin", "unknown")
            raise ValueError(
                f"Command '{name}' already registered from origin '{existing_origin}'"
            )

        meta: CommandMeta = {
            "name": name,
            "handler": handler,
            "origin": origin,
        }
        if help:
            meta["help"] = help
        if examples:
            meta["examples"] = examples

        self._commands[name] = meta

    def get(self, name: str) -> Optional[CommandMeta]:
        """
        Get command metadata by name.

        Args:
            name: Command name.

        Returns:
            CommandMeta dictionary if found, otherwise None.
        """
        return self._commands.get(name)

    def all(self) -> Dict[str, CommandMeta]:
        """
        Return a copy of all registered commands.

        Returns:
            Dictionary mapping command names to CommandMeta objects.
        """
        return dict(self._commands)


_global_registry: Optional[CommandRegistry] = None


def get_command_registry() -> CommandRegistry:
    """
    Get the singleton command registry instance.

    Returns:
        CommandRegistry singleton.
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = CommandRegistry()
    return _global_registry


def register_command(
    name: str,
    handler: CommandHandler,
    help: Optional[str] = None,
    origin: str = "core",
    examples: Optional[List[str]] = None,
    override: bool = False,
) -> None:
    """
    Register a command in the global command registry.

    This is a developer-facing helper that hides the registry implementation.

    Args:
        name: Command name as seen in CLI.
        handler: Callable that implements the command.
        help: Optional help text.
        origin: Command origin (e.g. "core", "plugin:batch").
        examples: Optional example invocations.
        override: Whether to override an existing command.
    """
    registry = get_command_registry()
    registry.register(
        name=name,
        handler=handler,
        help=help,
        origin=origin,
        examples=examples,
        override=override,
    )


def cli_command(
    name: Optional[str] = None,
    help: Optional[str] = None,
    origin: str = "core",
    examples: Optional[List[str]] = None,
    override: bool = False,
) -> Callable[[CommandHandler], CommandHandler]:
    """
    Decorator to register a CLI command without exposing the registry.

    Usage example:

        @cli_command(
            name="review",
            help="Review code changes or paths",
            examples=[
                "aworld-cli review --git-diff",
                "aworld-cli review --path src/",
            ],
        )
        def handle_review(argv: List[str]) -> int:
            ...
            return 0

    Args:
        name: Command name. Defaults to decorated function name.
        help: Optional help text.
        origin: Command origin (default: "core").
        examples: Optional example invocations.
        override: Whether to override an existing command.

    Returns:
        The original function, after it has been registered.
    """

    def decorator(func: CommandHandler) -> CommandHandler:
        command_name = name or func.__name__
        register_command(
            name=command_name,
            handler=func,
            help=help,
            origin=origin,
            examples=examples,
            override=override,
        )
        return func

    return decorator


__all__ = [
    "CommandHandler",
    "CommandMeta",
    "CommandRegistry",
    "get_command_registry",
    "register_command",
    "cli_command",
]

