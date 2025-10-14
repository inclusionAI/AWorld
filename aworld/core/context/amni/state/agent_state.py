import uuid
from typing import Optional

from pydantic import BaseModel, Field

from .common import ContextUsage, WorkingState
from aworld.config import AgentConfig, ContextRuleConfig
from aworld.core.memory import AgentMemoryConfig
from aworld.memory.models import AgentExperience


class AgentWorkingState(WorkingState):
    pass


class BaseAgentState(BaseModel):

    agent_id: str = Field(default=str(uuid.uuid4()))

    # context rule
    context_rule: ContextRuleConfig = Field(default=None)

    # cur agent call llm tokens
    context_usage: ContextUsage = Field(default=ContextUsage(), description="ContextUsage")

    working_state: AgentWorkingState = Field(default=AgentWorkingState(), description="working state")


class ApplicationAgentState(BaseAgentState):

    agent_config: Optional[AgentConfig] = Field(default=None)

    # automatic few short long-term
    experiences: Optional[list[AgentExperience]] = Field(default=None)

    # should be in  context rule
    memory_config: AgentMemoryConfig = Field(default=None)