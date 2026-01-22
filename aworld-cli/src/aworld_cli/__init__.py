"""
AWorld CLI package.
"""
import os
os.environ['AWORLD_DISABLE_CONSOLE_LOG'] = 'true'

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

