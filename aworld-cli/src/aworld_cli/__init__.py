"""
AWorld CLI package.
"""
from __future__ import annotations

import importlib
import os
import sys

from ._path_bootstrap import bootstrap_aworld_repo_path

os.environ.setdefault('AWORLD_DISABLE_CONSOLE_LOG', 'true')
bootstrap_aworld_repo_path(sys.path, __file__)

__all__ = [
    "AWorldCLI",
    "CliRuntime",
    "BaseCliRuntime",
    "AgentInfo",
    "TeamInfo",
    "AgentExecutor",
    "CLIHumanHandler",
]

_LAZY_IMPORTS = {
    "AWorldCLI": (".console", "AWorldCLI"),
    "CliRuntime": (".runtime", "CliRuntime"),
    "BaseCliRuntime": (".runtime", "BaseCliRuntime"),
    "AgentInfo": (".models", "AgentInfo"),
    "TeamInfo": (".models", "TeamInfo"),
    "AgentExecutor": (".executors", "AgentExecutor"),
    "CLIHumanHandler": (".handlers", "CLIHumanHandler"),
}


def __getattr__(name: str):
    if name not in _LAZY_IMPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _LAZY_IMPORTS[name]
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
