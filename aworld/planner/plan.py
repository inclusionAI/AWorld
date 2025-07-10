
from dataclasses import Field
import json
import logging
from typing import Dict, List, Union
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class StepInfo(BaseModel):
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
