"""Plugin framework primitives."""

from .context import CONTEXT_PHASES, PluginContextAdapter, load_plugin_contexts, run_context_phase
from .hud import HudLine, collect_hud_lines
from .hooks import PluginHookResult, load_plugin_hooks
from .manifest import load_plugin_manifest
from .models import PluginEntrypoint, PluginManifest
from .registry import PluginCapabilityRegistry, RegisteredEntrypoint
from .resources import PluginResourceResolver

__all__ = [
    "CONTEXT_PHASES",
    "HudLine",
    "PluginCapabilityRegistry",
    "PluginContextAdapter",
    "PluginHookResult",
    "PluginEntrypoint",
    "PluginManifest",
    "PluginResourceResolver",
    "RegisteredEntrypoint",
    "collect_hud_lines",
    "load_plugin_contexts",
    "load_plugin_hooks",
    "load_plugin_manifest",
    "run_context_phase",
]
