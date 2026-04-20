"""Host-owned plugin capability helpers and adapters."""

from .commands import PluginPromptCommand, register_plugin_commands, sync_plugin_commands
from .context import CONTEXT_PHASES, PluginContextAdapter, load_plugin_contexts, run_context_phase
from .hooks import PluginHookResult, load_plugin_hooks
from .hud import HudLine, collect_hud_lines
from .hud_helpers import format_hud_context_bar, format_hud_elapsed, format_hud_tokens
from .state import PluginStateHandle, PluginStateStore

__all__ = [
    "CONTEXT_PHASES",
    "HudLine",
    "PluginContextAdapter",
    "PluginHookResult",
    "PluginPromptCommand",
    "PluginStateHandle",
    "PluginStateStore",
    "collect_hud_lines",
    "format_hud_context_bar",
    "format_hud_elapsed",
    "format_hud_tokens",
    "load_plugin_contexts",
    "load_plugin_hooks",
    "register_plugin_commands",
    "run_context_phase",
    "sync_plugin_commands",
]
