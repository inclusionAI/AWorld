"""Shared Sandbox construction for the bundled AWorld agents."""

import os
from typing import Any, Dict, Optional, Sequence

from aworld.sandbox import Sandbox


def resolve_agent_sandbox_reuse() -> bool:
    """Return the connection-reuse policy selected for bundled agents."""

    return os.environ.get("AWORLD_SANDBOX_REUSE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def create_agent_sandbox(
    builtin_tools: Sequence[str],
    *,
    mcp_config: Optional[Dict[str, Any]] = None,
) -> Sandbox:
    """Create a local sandbox backed by AWorld's packaged tool servers."""

    return Sandbox(
        mcp_config=mcp_config,
        builtin_tools=list(builtin_tools),
        workspaces=[os.getcwd()],
        reuse=resolve_agent_sandbox_reuse(),
    )
