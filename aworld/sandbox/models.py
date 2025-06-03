from dataclasses import dataclass
from enum import IntEnum, StrEnum

from pydantic import BaseModel
from typing_extensions import Optional, Dict,Any


class SandboxStatus(StrEnum):
    """Sandbox status enumeration."""
    INIT = 'Pending'       # Initialization state
    RUNNING = 'Running'    # Running
    STOPPED = 'Stopped'    # Stopped
    ERROR = 'Failed'      # Error state
    REMOVED = 'Removed'    # Removed
    UNKNOWN = 'Unknown'  # Removed

class SandboxEnvType(IntEnum):
    """Sandbox env type enumeration."""
    K8S = 1
    SUPERCOMPUTER = 2


@dataclass
class SandboxCreateResponse:
    sandbox_id: str

@dataclass
class SandboxK8sResponse(SandboxCreateResponse):
    pod_name: Optional[str] = None
    service_name: Optional[str] = None
    status: Optional[str] = None
    cluster_ip: Optional[str] = None
    host: Optional[str] = None
    mcp_config: Optional[Any] = None,

@dataclass
class SandboxInfo:
    """Information about a sandbox."""

    sandbox_id: str
    """Sandbox ID."""
    status: str
    """sandbox status"""
    metadata: Dict[str, str]
    """Saved sandbox metadata."""



class EnvConfig(BaseModel):
    """Data structure contained in the environment"""
    name: str = "default"
    version: str = "1.0.0"
    dockerfile: Optional[str] = None    #Dockerfile of the image required when creating the environment