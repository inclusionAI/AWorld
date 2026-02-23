from typing import Dict, List, Any, Optional

from aworld.sandbox.base import BaseSandbox
from aworld.sandbox.models import SandboxEnvType
from aworld.sandbox.implementations import Sandbox
from aworld.sandbox.builder import SandboxBuilder

DefaultSandbox = Sandbox

SANDBOX_CLASS_MAP = {
    SandboxEnvType.LOCAL: Sandbox,
}


def create_sandbox(
    env_type: Optional[int] = None,
    sandbox_id: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    mcp_servers: Optional[List[str]] = None,
    mcp_config: Optional[Any] = None,
    black_tool_actions: Optional[Dict[str, List[str]]] = None,
    skill_configs: Optional[Any] = None,
    custom_env_tools: Optional[Any] = None,
    reuse: bool = True,
    **kwargs,
):
    """Create a sandbox instance. Only LOCAL is supported."""
    env_type = env_type or SandboxEnvType.LOCAL
    if env_type != SandboxEnvType.LOCAL:
        raise ValueError(f"Invalid environment type: {env_type}. Only LOCAL is supported.")
    return Sandbox(
        sandbox_id=sandbox_id,
        metadata=metadata,
        timeout=timeout,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        black_tool_actions=black_tool_actions,
        skill_configs=skill_configs,
        custom_env_tools=custom_env_tools,
        reuse=reuse,
        **kwargs,
    )


__all__ = [
    "Sandbox",
    "BaseSandbox",
    "DefaultSandbox",
    "SandboxEnvType",
    "create_sandbox",
    "SandboxBuilder",
]
