"""Plugin framework primitives."""

from .hud import HudLine, collect_hud_lines
from .hooks import PluginHookResult, load_plugin_hooks
from .manifest import load_plugin_manifest
from .models import PluginEntrypoint, PluginManifest
from .resources import PluginResourceResolver

__all__ = [
    "HudLine",
    "PluginHookResult",
    "PluginEntrypoint",
    "PluginManifest",
    "PluginResourceResolver",
    "collect_hud_lines",
    "load_plugin_hooks",
    "load_plugin_manifest",
]
