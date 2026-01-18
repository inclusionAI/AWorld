from typing import Dict, List, Any, Optional

from aworld.sandbox.base import Sandbox
from aworld.sandbox.common import BaseSandbox
from aworld.sandbox.models import SandboxEnvType
from aworld.sandbox.implementations import LocalSandbox, KubernetesSandbox, SuperSandbox


# For backward compatibility, use LocalSandbox as the default Sandbox implementation
DefaultSandbox = LocalSandbox


# Mapping of environment types to sandbox classes
SANDBOX_CLASS_MAP = {
    SandboxEnvType.LOCAL: LocalSandbox,
    SandboxEnvType.K8S: KubernetesSandbox,
    SandboxEnvType.SUPERCOMPUTER: SuperSandbox,
}

# Override Sandbox class constructor to create the appropriate sandbox based on env_type
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
    reuse: bool = False,
    **kwargs
) -> Sandbox:
    """
    Factory function to create a sandbox instance based on the environment type.
    
    Args:
        env_type: The environment type. Defaults to LOCAL if None.
        sandbox_id: Unique identifier for the sandbox. If None, one will be generated.
        metadata: Additional metadata for the sandbox.
        timeout: Timeout for sandbox operations.
        mcp_servers: List of MCP servers to use.
        mcp_config: Configuration for MCP servers.
        black_tool_actions: Black list of tool actions.
        skill_configs: Skill configurations.
        custom_env_tools: Custom environment tools. Optional parameter.
        reuse: Whether to reuse MCP server connections. Default is False.
        **kwargs: Additional parameters for specific sandbox types.
        
    Returns:
        Sandbox: An instance of a sandbox implementation.
        
    Raises:
        ValueError: If an invalid environment type is provided.
    """
    env_type = env_type or SandboxEnvType.LOCAL
    
    if env_type == SandboxEnvType.LOCAL:
        return LocalSandbox(
            sandbox_id=sandbox_id,
            metadata=metadata,
            timeout=timeout,
            mcp_servers=mcp_servers,
            mcp_config=mcp_config,
            black_tool_actions=black_tool_actions,
            skill_configs=skill_configs,
            custom_env_tools=custom_env_tools,
            reuse=reuse,
            **kwargs
        )
    elif env_type == SandboxEnvType.K8S:
        return KubernetesSandbox(
            sandbox_id=sandbox_id,
            metadata=metadata,
            timeout=timeout,
            mcp_servers=mcp_servers,
            mcp_config=mcp_config,
            black_tool_actions=black_tool_actions,
            skill_configs=skill_configs,
            custom_env_tools=custom_env_tools,
            **kwargs
        )
    elif env_type == SandboxEnvType.SUPERCOMPUTER:
        return SuperSandbox(
            sandbox_id=sandbox_id,
            metadata=metadata,
            timeout=timeout,
            mcp_servers=mcp_servers,
            mcp_config=mcp_config,
            black_tool_actions=black_tool_actions,
            skill_configs=skill_configs,
            custom_env_tools=custom_env_tools,
            **kwargs
        )
    else:
        raise ValueError(f"Invalid environment type: {env_type}")


# Monkey patch the Sandbox class to make direct instantiation work
old_init = Sandbox.__init__

def _sandbox_init(self, *args, **kwargs):
    if type(self) is Sandbox:
        # This should never be called directly, as __new__ will return a different type
        pass
    else:
        # Pass through to the original __init__ for actual implementations
        old_init(self, *args, **kwargs)

# Store the original __new__ method
original_new = object.__new__

# Create a new __new__ method that intercepts Sandbox instantiation
def _sandbox_new(cls, *args, **kwargs):
    if cls is Sandbox:
        # If trying to instantiate Sandbox directly, determine target class and 
        # return an uninitialized instance. Python will then call __init__ on it ONCE.
        env_type = kwargs.get('env_type') or SandboxEnvType.LOCAL
        target_cls = SANDBOX_CLASS_MAP.get(env_type)
        
        if not target_cls:
            raise ValueError(f"Invalid environment type: {env_type}")
            
        return original_new(target_cls)
    else:
        # For subclasses, use the original __new__
        return original_new(cls)

# Apply the monkey patches
Sandbox.__init__ = _sandbox_init
Sandbox.__new__ = _sandbox_new


# Expose key classes and functions
__all__ = [
    'Sandbox',
    'BaseSandbox',
    'LocalSandbox',
    'KubernetesSandbox',
    'SuperSandbox',
    'DefaultSandbox',
    'SandboxEnvType',
    'create_sandbox'
]
