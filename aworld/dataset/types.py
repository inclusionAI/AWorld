from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from aworld.utils.serialized_util import to_serializable


class ExpMeta(BaseModel):
    session_id: str
    task_id: str
    task_name: Optional[str] = None
    agent_id: Optional[str] = None
    step: Optional[int] = None
    execute_time: Optional[float] = None
    pre_agent: Optional[str] = None

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "agent_id": self.agent_id,
            "step": self.step,
            "execute_time": self.execute_time,
            "pre_agent": self.pre_agent
        }

class TrajectoryState(BaseModel):
    """
    S: Environment & Context
    """
    input: Any = Field(default=None, description="Agent input (query)")
    messages: List[Dict[str, Any]] = Field(default_factory=list, description="History messages")
    context: Dict[str, Any] = Field(default_factory=dict, description="Context variables")

class TrajectoryAction(BaseModel):
    """
    A: Decision & Execution
    """
    content: Optional[str] = Field(default=None, description="Assistant message content")
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list, description="Tool calls")
    is_agent_finished: bool = Field(default=False, description="Is agent finished")
    status: Optional[str] = Field(default=None, description="Execution status")
    msg: Optional[str] = Field(default=None, description="Execution error message")
    ext_info: Dict[str, Any] = Field(default_factory=dict, description="Extra information")

class TrajectoryReward(BaseModel):
    """
    R: Feedback & Result
    """
    tool_outputs: List[Dict[str, Any]] = Field(default_factory=list, description="Tool message output")
    status: Optional[str] = Field(default=None, description="Execution status")
    score: Optional[float] = Field(default=None, description="User feedback or score")


class Experience(BaseModel):
    state: Any
    actions: List[Any] = Field(default_factory=list)
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    ext_info: Dict[str, Any] = Field(default_factory=dict)
    # Optional fields to keep backward-compat for legacy pipelines
    reward_t: Optional[float] = None
    adv_t: Optional[Any] = None
    v_t: Optional[Any] = None

    def to_dict(self):
        return {
            "state": to_serializable(self.state),
            "actions": to_serializable(self.actions),
            "reward_t": self.reward_t,
            "adv_t": self.adv_t,
            "v_t": self.v_t,
            "messages": self.messages,
            "ext_info": to_serializable(self.ext_info)
        }


class DataRow(BaseModel):
    exp_meta: ExpMeta
    exp_data: Experience
    id: str

    def to_dict(self):
        return {
            "exp_meta": self.exp_meta.to_dict(),
            "exp_data": self.exp_data.to_dict(),
            "id": self.id
        }

class TrajectoryItem(BaseModel):
    """
    Standardized Trajectory Item with SAR structure
    """
    id: str
    meta: ExpMeta
    state: TrajectoryState
    action: TrajectoryAction
    reward: TrajectoryReward

    def to_dict(self):
        return {
            "id": self.id,
            "meta": self.meta.to_dict(),
            "state": self.state.model_dump(),
            "action": self.action.model_dump(),
            "reward": self.reward.model_dump()
        }
