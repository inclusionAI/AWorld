from __future__ import annotations

from pathlib import Path
from typing import Any

from aworld_cli.core.plugin_manager import PluginManager, get_builtin_agent_bundle_roots


def bootstrap_acp_plugins(base_dir: Path) -> dict[str, Any]:
    warnings: list[str] = []
    runtime_plugin_roots: list[Path] = []

    try:
        manager = PluginManager()
        runtime_plugin_roots = manager.get_runtime_plugin_roots()
    except Exception as exc:
        warnings.append(f"ACP plugin bootstrap degraded: {exc}")

    # Include builtin agent bundles (e.g., smllc) which contain the actual agents
    agent_bundle_roots = get_builtin_agent_bundle_roots()
    runtime_plugin_roots.extend(agent_bundle_roots)

    return {
        "plugin_roots": runtime_plugin_roots,
        "warnings": warnings,
        "command_sync_enabled": False,
        "interactive_refresh_enabled": False,
        "base_dir": Path(base_dir),
    }
