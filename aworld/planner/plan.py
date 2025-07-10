import json
from dataclasses import Field
from typing import Dict, List, Union

from pydantic import BaseModel


class StepInfo(BaseModel):
    input: str = Field(..., description="step description")
    id: str = Field(..., description="tool or agent id")


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
    # Parse JSON plan
    plan_data = json.loads(plan_text)
    return Plan(**plan_data)
