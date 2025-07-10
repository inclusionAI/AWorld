import json
import logging
from typing import Any, Dict, List, Union, Optional
from pydantic import BaseModel, Field, ValidationError
from typing import Dict, List, Union
logger = logging.getLogger(__name__)


class StepInfo(BaseModel):
    """单个步骤的详细信息"""
    
    # input for agent
    input: Optional[str] = Field(..., description="步骤的描述")
    # parameters for tools
    parameters: Optional[Dict[str, Any]] = Field(..., description="工具或代理的参数")
    # id of tool or agent
    id: str = Field(..., description="执行该步骤的工具或代理ID")


class Plan(BaseModel):
    """Defined plan structure, including steps and their sequence.

    Example:
        {
            "steps": {
                "agent_step_1": {"input": "analysis input", "id": "requirement_analyzer"},
                "agent_step_2": {"input": "generate code", "id": "code_generator"}
            },
            "dag": [["agent_step_1"], "agent_step_2"]
        }
    """

    steps: Dict[str, StepInfo] = Field(
        default_factory=dict,
        description="step id with it info"
    )

    dag: List[Union[str, List[str]]] = Field(
        default_factory=list,
        description="dag"
    )


def parse_plan(plan_text: str) -> Plan:
    """解析JSON格式的计划文本为Plan对象
    
    Args:
        plan_text: JSON格式的计划文本
        
    Returns:
        Plan对象
        
    Raises:
        ValueError: 当JSON格式错误或缺少必要字段时
        ValidationError: 当数据结构不符合Plan模型定义时
    
    Example:
        plan_text = '''
        {
            "steps": {
                "agent_step_1": {
                    "input": "Initialize agent2 with content='Apple Inc. development history'",
                    "id": "agent2---uuid5bf563uuid",
                    "parameters": {}
                }
            },
            "dag": [["agent_step_1"]]
        }
        '''
        plan = parse_plan(plan_text)
    """
    try:
        # 解析JSON
        plan_data = json.loads(plan_text)
        
        # 验证必要字段
        if "steps" not in plan_data:
            raise ValueError("计划数据缺少'steps'字段")
        if "dag" not in plan_data:
            raise ValueError("计划数据缺少'dag'字段")
            
        # 处理步骤信息
        processed_steps: Dict[str, StepInfo] = {}
        for step_id, step_info in plan_data["steps"].items():
            # 确保必要字段存在
            if "input" not in step_info:
                raise ValueError(f"步骤 '{step_id}' 缺少'input'字段")
            if "id" not in step_info:
                raise ValueError(f"步骤 '{step_id}' 缺少'id'字段")
                
            # 添加默认的parameters字段如果不存在
            if "parameters" not in step_info:
                step_info["parameters"] = {}
                
            # 创建StepInfo对象
            processed_steps[step_id] = StepInfo(
                input=step_info["input"],
                id=step_info["id"],
                parameters=step_info["parameters"]
            )
            
        # 验证DAG结构
        dag = plan_data["dag"]
        if not isinstance(dag, list):
            raise ValueError("'dag'字段必须是列表类型")
            
        # 验证DAG中的步骤ID是否都存在于steps中
        all_step_ids = set()
        def collect_step_ids(items: List[Union[str, List[str]]]):
            for item in items:
                if isinstance(item, list):
                    collect_step_ids(item)
                else:
                    all_step_ids.add(item)
                    
        collect_step_ids(dag)
        unknown_steps = all_step_ids - set(processed_steps.keys())
        if unknown_steps:
            raise ValueError(f"DAG中包含未定义的步骤ID: {unknown_steps}")
            
        # 创建Plan对象
        return Plan(
            steps=processed_steps,
            dag=dag
        )
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析错误: {e}")
        raise ValueError(f"无效的JSON格式: {e}")
    except ValidationError as e:
        logger.error(f"数据验证错误: {e}")
        raise
    except Exception as e:
        logger.error(f"解析计划时发生错误: {e}")
        raise ValueError(f"解析计划失败: {e}")
