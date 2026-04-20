"""Shared plugin framework primitives."""

from .discovery import DiscoveredPlugin, discover_plugins
from .manifest import load_plugin_manifest
from .models import PluginEntrypoint, PluginManifest
from .registry import PluginCapabilityRegistry, RegisteredEntrypoint
from .resolution import resolve_plugin_activation
from .resources import PluginResourceResolver
from .validation import get_plugin_manifest_schema_path, load_plugin_manifest_schema, validate_plugin_path

__all__ = [
    "DiscoveredPlugin",
    "PluginCapabilityRegistry",
    "PluginEntrypoint",
    "PluginManifest",
    "PluginResourceResolver",
    "RegisteredEntrypoint",
    "discover_plugins",
    "get_plugin_manifest_schema_path",
    "load_plugin_manifest",
    "load_plugin_manifest_schema",
    "resolve_plugin_activation",
    "validate_plugin_path",
]
