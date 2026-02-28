"""
UI helper components for aworld-cli.

This package centralizes Rich-based console UI primitives. Plugin-specific
UI is registered via UIRegistry and loaded with get_ui_components() or
get_plugin_ui_components().
"""

from .components import (
    select_menu_option,
    confirm_action,
    print_info_panel,
    print_error_panel,
)
from .registry import (
    UIRegistry,
    get_ui_registry,
    register_ui_plugin,
    get_ui_components,
)


def get_plugin_ui_components(plugin_name: str):
    """
    Load UI components provided by a plugin (registry first, then import fallback).

    Args:
        plugin_name: Plugin identifier, e.g. "memory".

    Returns:
        The registered provider (e.g. memory module with get_memory_path, ...),
        or None if not found.

    Example:
        >>> mem = get_plugin_ui_components("memory")
        >>> if mem:
        ...     path = mem.get_memory_path("project")
    """
    provider = get_ui_components(plugin_name)
    if provider is not None:
        return provider
    try:
        from importlib import import_module
        return import_module(f"aworld_cli.inner_plugins.{plugin_name}")
    except ImportError:
        return None


__all__ = [
    "select_menu_option",
    "confirm_action",
    "print_info_panel",
    "print_error_panel",
    "UIRegistry",
    "get_ui_registry",
    "register_ui_plugin",
    "get_ui_components",
    "get_plugin_ui_components",
]

