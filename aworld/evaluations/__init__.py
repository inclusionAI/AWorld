# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import importlib
import pkgutil
from aworld.logs.util import logger


def _auto_discover_scorers(current_dir, package_name: str = None):
    """Auto-discover and import all scorer modules in the current directory."""

    if not package_name:
        package_name = f"{__name__}.{os.path.basename(current_dir)}"
    for _, module_name, _ in pkgutil.iter_modules([current_dir]):
        try:
            importlib.import_module(f'.{module_name}', package=package_name)
        except Exception as e:
            logger.error(f"Failed to import scorer module {module_name}: {e}")


_auto_discover_scorers(current_dir=os.path.join(os.path.dirname(__file__), 'scorers'))
