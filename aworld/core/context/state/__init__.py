from typing import Optional, List, Dict, Any

from aworld.core.context.base import ContextUsage
from aworld.core.context.state.agent_state import AgentState
from aworld.core.context.state.session_state import SessionState
from aworld.core.context.state.trace_state import TraceState
from aworld.core.context.state.working_state import WorkingState
from aworld.memory.models import UserProfile, Fact
from pydantic import BaseModel, Field

from aworld.output import Artifact, Outputs

class BaseContextState(BaseModel):

    session_state: Optional['Task'] = Field(default=None, description="Task object reference")

    # Agent context isolation
    agent_states: Optional[Dict[str, AgentState]] = Field(default_factory=dict, description="Agent context isolation")


class ContextState(BaseModel):

    ################Seleted context[Shareable]  ################

    # Task-related attributes
    task: Optional['Task'] = Field(default=None, description="Task object reference")

    # Cur Session management: Conversation history
    session: SessionState = Field(description="Session management: Conversation history, actions, and reasoning steps")

    # long-term User profile information and preferences
    user_profile: Optional[UserProfile] = Field(default=None, description="User profile information and preferences")

    # long-term Facts,
    facts: Optional[list[Fact]] = Field(description="Relation Facts")

    # Knowledge base: retrieved information, and references
    custom_information: Optional[Dict[str, Any]] = Field(description="retrieved information, and references")

    ################ task processing ################

    # Working memory: Current task state and intermediate results
    working_state: WorkingState = Field(description="Working memory: Current task state and intermediate results")

    # Agent context isolation
    agent_states: Optional[Dict[str, AgentState]] = Field(default_factory=dict, description="Agent context isolation")

    ################ task output ################

    # cur task output buffer: Synthesized results and conclusions
    outputs: Outputs = Field(description="Output buffer: Synthesized results and conclusions")

    # cur task gen artifacts
    artifacts: List[Artifact] = Field(description="aigc artifacts")

    ################ monitor ##################
    # context_usage
    context_usage: ContextUsage = Field(default=None, description="ContextUsage")

    # Trace and token tracking
    trace_state: TraceState = Field(default=None, description="trace State")


class AworldContextState(BaseModel):

    ################Seleted context[Shareable]  ################

    # Task-related attributes
    task: Optional['Task'] = Field(default=None, description="Task object reference")

    # Cur Session management: Conversation history
    session: SessionState = Field(description="Session management: Conversation history, actions, and reasoning steps")

    # long-term User profile information and preferences
    user_profile: Optional[UserProfile] = Field(default=None, description="User profile information and preferences")

    # long-term Facts,
    facts: Optional[list[Fact]] = Field(description="Relation Facts")

    # Knowledge base: retrieved information, and references
    custom_information: Optional[Dict[str, Any]] = Field(description="retrieved information, and references")

    ################ task processing ################

    # Working memory: Current task state and intermediate results
    working_state: WorkingState = Field(description="Working memory: Current task state and intermediate results")

    # Agent context isolation
    agent_states: Optional[Dict[str, AgentState]] = Field(default_factory=dict, description="Agent context isolation")

    ################ task output ################

    # cur task output buffer: Synthesized results and conclusions
    outputs: Outputs = Field(description="Output buffer: Synthesized results and conclusions")

    # cur task gen artifacts
    artifacts: List[Artifact] = Field(description="aigc artifacts")

    ################ monitor ##################
    # context_usage
    context_usage: ContextUsage = Field(default=None, description="ContextUsage")

    # Trace and token tracking
    trace_state: TraceState = Field(default=None, description="trace State")




