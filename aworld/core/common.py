# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from enum import Enum

from pydantic import BaseModel
from typing import Dict, Any, Union, List


class ActionResult(BaseModel):
    """Result of executing an action by use tool."""
    is_done: bool = False
    success: bool = False
    content: str = None
    error: str = None
    keep: bool = False
    action_name: str = None
    tool_name: str = None


class Observation(BaseModel):
    """Observation information obtained from the tools.

    It can be an agent(as a tool) in the swarm or a tool in the virtual environment.
    """
    # default is None, means the main virtual environment or swarm
    container_id: str = None
    # Observer who obtains observation, default is None for compatible, means an agent name or a tool name
    observer: str = None
    # default is None for compatible, means with its action/ability name of an agent or a tool
    # NOTE: The only ability of an agent as a tool is handoffs
    ability: str = None
    # The agent wants the observation to be created, default is None for compatible.
    from_agent_name: str = None
    # To which agent should the observation be given, default is None for compatible.
    to_agent_name: str = None
    # general info for agent
    content: Any = None
    # dom_tree is a str or DomTree object
    dom_tree: Union[str, Any] = None
    image: str = None  # base64
    action_result: List[ActionResult] = []
    # for video or image list
    images: List[str] = []
    info: Dict[str, Any] = {}


class ParamInfo(BaseModel):
    name: str | None = None
    type: str = "str"
    required: bool = False
    desc: str = None
    default_value: Any = None


class ToolActionInfo(BaseModel):
    name: str
    input_params: Dict[str, ParamInfo] = {}
    desc: str = None


class ActionModel(BaseModel):
    tool_name: str = None
    # agent name
    agent_name: str = None
    # action_name is a tool action name by agent policy.
    action_name: str = None
    params: Dict[str, Any] = {}
    policy_info: Any = None


class AgentPolicy(BaseModel):
    """The unified model of Agent response can be provided to the agent, or tool actions in environmental."""

    # decision to agent policy
    actions: List[ActionModel]
    # which agent made the policy
    name: str
    # belongs to which swarm
    swarm_id: str = None
