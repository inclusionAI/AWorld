from __future__ import annotations

from pathlib import Path
from typing import Any

from aworld_cli.core.plugin_manager import PluginManager


def bootstrap_acp_plugins(base_dir: Path) -> dict[str, Any]:
    manager = PluginManager()
    runtime_plugin_roots = manager.get_runtime_plugin_roots()
    return {
        "plugin_roots": runtime_plugin_roots,
        "warnings": [],
        "command_sync_enabled": False,
        "interactive_refresh_enabled": False,
        "base_dir": Path(base_dir),
    }
