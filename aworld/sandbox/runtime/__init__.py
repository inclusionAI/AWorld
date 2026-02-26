"""
Runtime support utilities for sandbox execution.

This module exposes two key abstractions:

- SandboxLoopPool: manages a small pool of dedicated asyncio event loops,
  each running in its own thread, intended for sandbox-bound work.
- SandboxManager: maps sandbox IDs to a specific loop in the pool and
  provides helpers to run coroutines on the correct loop and to perform
  cleanup.

The initial integration focuses on MCP-related operations while keeping
the public Sandbox API unchanged.
"""

from .loop_pool import SandboxLoopPool  # noqa: F401
from .manager import SandboxManager  # noqa: F401

