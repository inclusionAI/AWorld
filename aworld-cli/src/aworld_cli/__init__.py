"""
AWorld CLI package.
"""
import os
import sys

from ._path_bootstrap import bootstrap_aworld_repo_path

os.environ.setdefault('AWORLD_DISABLE_CONSOLE_LOG', 'true')
bootstrap_aworld_repo_path(sys.path, __file__)

from .console import AWorldCLI
from .runtime import CliRuntime, BaseCliRuntime
from .models import AgentInfo, TeamInfo
from .executors import AgentExecutor

# Import handlers to ensure they are registered
from .handlers import CLIHumanHandler

__all__ = [
    "AWorldCLI",
    "CliRuntime",
    "BaseCliRuntime",
    "AgentInfo",
    "TeamInfo",
    "AgentExecutor",
    "CLIHumanHandler",
]
