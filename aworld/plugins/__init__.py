"""Shared plugin framework primitives."""

from .discovery import DiscoveredPlugin, discover_plugins
from .manifest import load_plugin_manifest
from .models import PluginEntrypoint, PluginManifest
from .registry import PluginCapabilityRegistry, RegisteredEntrypoint
from .resources import PluginResourceResolver

__all__ = [
    "DiscoveredPlugin",
    "PluginCapabilityRegistry",
    "PluginEntrypoint",
    "PluginManifest",
    "PluginResourceResolver",
    "RegisteredEntrypoint",
    "discover_plugins",
    "load_plugin_manifest",
]
