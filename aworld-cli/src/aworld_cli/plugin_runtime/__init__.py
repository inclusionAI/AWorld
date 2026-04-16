"""CLI-specific plugin runtime surfaces."""

from .commands import PluginPromptCommand, register_plugin_commands, sync_plugin_commands
from .context import CONTEXT_PHASES, PluginContextAdapter, load_plugin_contexts, run_context_phase
from .hooks import PluginHookResult, load_plugin_hooks
from .hud import HudLine, collect_hud_lines
from .state import PluginStateStore

__all__ = [
    "CONTEXT_PHASES",
    "HudLine",
    "PluginContextAdapter",
    "PluginHookResult",
    "PluginPromptCommand",
    "PluginStateStore",
    "collect_hud_lines",
    "load_plugin_contexts",
    "load_plugin_hooks",
    "register_plugin_commands",
    "run_context_phase",
    "sync_plugin_commands",
]
