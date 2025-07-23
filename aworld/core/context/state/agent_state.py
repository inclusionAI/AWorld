import uuid
from email.policy import default
from typing import Union, Optional, List

from pydantic import BaseModel
from pydantic.dataclasses import dataclass
from sympy.polys.domains.field import Field

from aworld.config import AgentConfig, ContextRuleConfig
from aworld.core.context.state.common import ContextUsage
from aworld.core.memory import AgentMemoryConfig
from aworld.memory.models import MemoryMessage, MemorySummary, AgentExperience
from aworld.output import Artifact


class AgentState(BaseModel):

    agent_id: str = Field(default = str(uuid.uuid4()))

    agent_config: AgentConfig = Field(default=None)

    # context rule
    context_rule: ContextRuleConfig = Field(default=None)

    # should be in  context rule, 先不破坏历史结构
    memory_config: AgentMemoryConfig = Field(default=None)

    # short-term memory: summary
    conversation_history: list[Union[MemoryMessage, MemorySummary]] = Field(default=None)

    # automatic few short long-term
    experiences: Optional[list[AgentExperience]] = Field(default=None)

    # cur task gen artifacts(temp-files)
    artifacts: List[Artifact] = Field(description="aigc artifacts")

    # cur agent call llm
    context_usage: ContextUsage = Field(default=None, description="ContextUsage")
