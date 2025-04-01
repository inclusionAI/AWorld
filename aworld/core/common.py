# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from enum import Enum

from pydantic import BaseModel
from typing import Dict, Any, Union, List


class ActionResult(BaseModel):
    """Result of executing an action by use tool."""
    is_done: bool = False
    success: bool = None
    content: str = None
    error: str = None
    keep: bool = False


class Tools(Enum):
    """Tool list supported in the framework."""
    BROWSER = "browser"
    ANDROID = "android"
    GYM = "openai_gym"
    SEARCH_API = "search_api"
    SHELL = "shell"
    PYTHON_EXECUTE = "python_execute"
    CODE_EXECUTE = "code_execute"
    FILE = "file"
    IMAGE_ANALYSIS = "image_analysis"
    DOCUMENT_ANALYSIS = "document_analysis"


class Agents(Enum):
    """Tool list supported in the framework."""
    BROWSER = "browser_agent"
    ANDROID = "android_agent"
    SEARCH = "search_agent"
    CODE_EXECUTE = "code_execute_agent"
    FILE = "file_agent"
    IMAGE_ANALYSIS = "image_analysis_agent"
    SHELL = "shell_agent"
    DOCUMENT = "document_agent"
    GYM = "gym_agent"
    PLAN = "plan_agent"
    EXECUTE = "execute_agent"
    SUMMARY = "summary_agent"


class Observation(BaseModel):
    """Observation information obtained from the tools.

    It can be an agent(as a tool) in the swarm or a tool in the virtual environment.
    """
    # default is None, means the main virtual environment or swarm
    container_id: str = None
    # default is None for compatible, means an agent name or a tool name
    observer: str = None
    # default is None for compatible, means with its action/ability name of an agent or a tool
    # NOTE: The only ability of an agent as a tool is handoffs
    ability: str = None
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
    """The unified model of BaseAgent response can be provided to the agent, or tool actions in environmental."""
    tool_name: str = None
    # agent name
    agent_name: str = None
    # action_name is a tool action name by agent policy.
    action_name: str = None
    params: Dict[str, Any] = {}
    policy_info: Any = None
