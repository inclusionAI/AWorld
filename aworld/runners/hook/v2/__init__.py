"""AWorld Hooks V2 - 配置驱动的 Hook 系统

This module provides a configuration-driven hook system that supports:
- Shell command hooks (CommandHookWrapper)
- Python callback hooks (CallbackHookWrapper)
- JSON-based output protocol with flow control
- Permission integration (allow/deny/ask)
- Input/output parameter modification
"""

from .protocol import HookJSONOutput
from .wrappers import CommandHookWrapper, CallbackHookWrapper

__all__ = [
    'HookJSONOutput',
    'CommandHookWrapper',
    'CallbackHookWrapper',
]
