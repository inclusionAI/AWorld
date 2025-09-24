from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from aworld.utils.serialized_util import to_serializable


class ExpMeta(BaseModel):
    task_id: str
    task_name: Optional[str] = None
    agent_id: Optional[str] = None
    step: Optional[int] = None
    execute_time: Optional[float] = None
    pre_agent: Optional[str] = None

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "agent_id": self.agent_id,
            "step": self.step,
            "execute_time": self.execute_time,
            "pre_agent": self.pre_agent
        }


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
