from typing import Optional, OrderedDict

from pydantic import BaseModel

class SandboxConfig:
    pass

class WorkingState(BaseModel):
    """
    [Runtime]Working memory state container for runtime

    Stores temporary data and intermediate results during task execution.
    This class serves as a placeholder for working memory implementation.
    """
    sandbox: SandboxConfig
    swarm: Optional["Swarm"]
    trajectories: OrderedDict
