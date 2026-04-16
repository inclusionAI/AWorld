"""Plugin framework primitives."""

from aworld.plugins import (
    DiscoveredPlugin,
    PluginCapabilityRegistry,
    PluginEntrypoint,
    PluginManifest,
    PluginResourceResolver,
    RegisteredEntrypoint,
    discover_plugins,
    load_plugin_manifest,
)
from .context import CONTEXT_PHASES, PluginContextAdapter, load_plugin_contexts, run_context_phase
from .hud import HudLine, collect_hud_lines
from .hooks import PluginHookResult, load_plugin_hooks

__all__ = [
    "CONTEXT_PHASES",
    "DiscoveredPlugin",
    "HudLine",
    "PluginCapabilityRegistry",
    "PluginContextAdapter",
    "PluginHookResult",
    "PluginEntrypoint",
    "PluginManifest",
    "PluginResourceResolver",
    "RegisteredEntrypoint",
    "collect_hud_lines",
    "discover_plugins",
    "load_plugin_contexts",
    "load_plugin_hooks",
    "load_plugin_manifest",
    "run_context_phase",
]
