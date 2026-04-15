"""Backward-compatible aliases for the legacy built-in plugin namespace."""

from importlib import import_module
from pathlib import Path
import sys


def _alias_builtin_plugins() -> None:
    plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
    if not plugins_dir.exists():
        return

    for plugin_dir in plugins_dir.iterdir():
        if not plugin_dir.is_dir():
            continue
        module_name = plugin_dir.name
        legacy_name = f"{__name__}.{module_name}"
        canonical_name = f"aworld_cli.plugins.{module_name}"
        if legacy_name in sys.modules:
            continue
        sys.modules[legacy_name] = import_module(canonical_name)


_alias_builtin_plugins()

