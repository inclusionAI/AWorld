"""
Registry for UI components provided by plugins.

Plugins register their UI exports (module or dict of callables) so that
console or other code can load them by plugin name without hardcoding imports.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

_global_registry: Optional["UIRegistry"] = None


class UIRegistry:
    """
    Registry mapping plugin names to their UI component provider.

    The provider is typically the plugin module itself (so callers can
    use get_memory_path, ensure_memory_file, etc.) or a dict of name -> callable.
    """

    def __init__(self) -> None:
        self._plugins: Dict[str, Any] = {}

    def register(self, plugin_name: str, provider: Any) -> None:
        """
        Register a plugin's UI components.

        Args:
            plugin_name: Plugin identifier, e.g. "memory".
            provider: The plugin module or a dict of component name -> callable.
        """
        self._plugins[plugin_name] = provider

    def get(self, plugin_name: str) -> Optional[Any]:
        """
        Get the UI component provider for a plugin.

        Args:
            plugin_name: Plugin identifier.

        Returns:
            The registered provider, or None if not found.
        """
        return self._plugins.get(plugin_name)

    def all_plugins(self) -> Dict[str, Any]:
        """Return all registered plugin providers (name -> provider)."""
        return dict(self._plugins)


def get_ui_registry() -> UIRegistry:
    """Return the singleton UI component registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = UIRegistry()
    return _global_registry


def register_ui_plugin(plugin_name: str, provider: Any) -> None:
    """
    Register a plugin's UI components with the global registry.

    Args:
        plugin_name: Plugin identifier, e.g. "memory".
        provider: The plugin module or dict of exports.

    Example:
        # In inner_plugins.memory.__init__.py:
        from aworld_cli.ui.registry import register_ui_plugin
        register_ui_plugin("memory", sys.modules[__name__])
    """
    get_ui_registry().register(plugin_name, provider)


def get_ui_components(plugin_name: str) -> Optional[Any]:
    """
    Get the UI component provider for a plugin from the registry.

    Args:
        plugin_name: Plugin identifier.

    Returns:
        The registered provider, or None if not found.
    """
    return get_ui_registry().get(plugin_name)
