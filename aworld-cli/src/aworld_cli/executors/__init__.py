"""
Agent executors for executing chat interactions.
"""
from .base import AgentExecutor
from .base_executor import BaseAgentExecutor

__all__ = ["AgentExecutor", "BaseAgentExecutor"]

# Import executors for convenience (using relative imports to avoid circular dependencies)
from .local import LocalAgentExecutor
from .remote import RemoteAgentExecutor
from .continuous import ContinuousExecutor
from .hooks import (
    ExecutorHookPoint,
    ExecutorHook,
    PreInputParseHook,
    PostInputParseHook,
    PreBuildContextHook,
    PostBuildContextHook,
    PreBuildTaskHook,
    PostBuildTaskHook,
    PreRunTaskHook,
    PostRunTaskHook,
    OnTaskErrorHook
)

# Import FileParseHook to ensure it's registered with HookFactory
# This is a default hook that is automatically enabled
from .file_parse_hook import FileParseHook  # noqa: F401

__all__.extend([
    "LocalAgentExecutor", 
    "RemoteAgentExecutor", 
    "ContinuousExecutor",
    "ExecutorHookPoint",
    "ExecutorHook",
    "PreInputParseHook",
    "PostInputParseHook",
    "PreBuildContextHook",
    "PostBuildContextHook",
    "PreBuildTaskHook",
    "PostBuildTaskHook",
    "PreRunTaskHook",
    "PostRunTaskHook",
    "OnTaskErrorHook",
    "FileParseHook"
])

