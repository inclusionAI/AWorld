
from dataclasses import Field
import json
import logging
import re
from typing import Dict, List, Optional, Union
from aworld.core.tool.base import BaseTool
from aworld.models.model_response import ModelResponse

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.agent.base import AgentResult
from aworld.core.common import ActionModel, Observation
from aworld.core.event.base import Message
from aworld.models.model_response import ModelResponse
from aworld.planner.built_in_planner import PlanningOutputParser, BuiltInPlanner
from aworld.core.context.prompts.string_prompt_template import StringPromptTemplate

logger = logging.getLogger(__name__)




class StepInfo(BaseTool):
    """单个步骤的详细信息"""
    
    input: str = Field(..., description="步骤的描述")
    id: str = Field(..., description="执行该步骤的工具或代理ID")


class Plan(BaseModel):
    """计划结构，包含步骤信息和执行顺序
    
    Example:
        {
            "steps": {
                "agent_step_1": {"input": "分析用户需求", "id": "requirement_analyzer"},
                "agent_step_2": {"input": "生成代码", "id": "code_generator"}
            },
            "dag": [["agent_step_1"], "agent_step_2"]
        }
    """
    
    steps: Dict[str, StepInfo] = Field(
        default_factory=dict,
        description="步骤字典，key为步骤ID，value为步骤信息"
    )
    
    dag: List[Union[str, List[str]]] = Field(
        default_factory=list,
        description="步骤执行顺序的DAG结构"
    )



def parse_plan(plan_text: str) -> Plan:
    # Parse JSON plan
    plan_data = json.loads(plan_text)
    return Plan(**plan_data)
