import uuid
from typing import Union, Optional, List, Dict, Any

from pydantic import BaseModel
from sympy.polys.domains.field import Field

from aworld.config import AgentConfig, ContextRuleConfig
from aworld.core.context.state.common import ContextUsage
from aworld.core.memory import AgentMemoryConfig
from aworld.memory.models import MemoryMessage, MemorySummary, AgentExperience
from aworld.output import Artifact


class BaseAgentState(BaseModel):

    # short-term memory: summary
    memory_messages: list[Union[MemoryMessage, MemorySummary]] = Field(default=None)

    # cur task gen artifacts(temp-files)
    artifacts: List[Artifact] = Field(description="aigc artifacts")

    # context rule
    context_rule: ContextRuleConfig = Field(default=None)

    # cur agent call llm tokens
    context_usage: ContextUsage = Field(default=None, description="ContextUsage")

    kv_store: Dict[str, Any] = Field(default={}, description="custom_info")


class AgentState(BaseAgentState):

    agent_id: str = Field(default = str(uuid.uuid4()))

    agent_config: AgentConfig = Field(default=None)


    # automatic few short long-term
    experiences: Optional[list[AgentExperience]] = Field(default=None)


    # should be in  context rule
    memory_config: AgentMemoryConfig = Field(default=None)
