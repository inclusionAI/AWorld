# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
from typing import Optional, Dict, Any, Union, List

from pydantic import BaseModel, Field

from aworld.logs.util import logger


class StepInfo(BaseModel):
    """Step information details"""

    # input for agent
    input: Optional[str] = Field(..., description="Step description")
    # parameters for tools
    parameters: Optional[Dict[str, Any]] = Field(..., description="Tool or agent parameters")
    # id of tool or agent
    id: str = Field(..., description="Tool or agent ID for execution")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format"""
        return {
            "input": self.input,
            "parameters": self.parameters or {},
            "id": self.id
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepInfo":
        """Create from dictionary"""
        return cls(
            input=data.get("input"),
            parameters=data.get("parameters", {}),
            id=data["id"]
        )

    class Config:
        json_encoders = {
            # Add custom encoders if needed
        }


class StepInfos(BaseModel):
    """Defined plan structure, including steps and their sequence."""

    steps: Dict[str, StepInfo] = Field(
        default_factory=dict,
        description="step id with it info"
    )

    dag: List[Union[str, List[str]]] = Field(
        default_factory=list,
        description="dag"
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format"""
        return {
            "steps": {
                step_id: step.to_dict()
                for step_id, step in self.steps.items()
            },
            "dag": self.dag
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepInfos":
        """Create from dictionary"""
        return cls(
            steps={
                step_id: StepInfo.from_dict(step_info)
                for step_id, step_info in data.get("steps", {}).items()
            },
            dag=data.get("dag", [])
        )

    class Config:
        json_encoders = {
            # Add custom encoders if needed
        }


class Plan(BaseModel):
    """Plan structure with step information and final answer"""

    step_infos: StepInfos = Field(
        default_factory=lambda: StepInfos(steps={}, dag=[]),
        description="Step information and execution sequence"
    )
    answer: str = Field(
        default="",
        description="Final answer or result"
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format"""
        return {
            "step_infos": self.step_infos.to_dict(),
            "answer": self.answer
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        """Create from dictionary"""
        return cls(
            step_infos=StepInfos.from_dict(data.get("step_infos", {"steps": {}, "dag": []})),
            answer=data.get("answer", "")
        )

    def json(self, **kwargs) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def parse_raw(cls, json_str: str) -> "Plan":
        """Create from JSON string"""
        try:
            data = json.loads(json_str)
            return cls.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to parse JSON: {e}")
            return cls()

    class Config:
        json_encoders = {
            # Add custom encoders if needed
        }


def parse_step_infos(step_infos: dict) -> StepInfos:
    """Parse step information dictionary into StepInfos object"""
    try:
        return StepInfos.from_dict(step_infos)
    except Exception as e:
        logger.error(f"Error parsing step infos: {e}")
        return StepInfos(steps={}, dag=[])


def parse_step_json(step_json: str) -> StepInfos:
    """Parse JSON string into StepInfos object"""
    try:
        data = json.loads(step_json)
        return parse_step_infos(data)
    except Exception as e:
        logger.error(f"Failed to parse step JSON: {e}")
        return StepInfos(steps={}, dag=[])


def parse_plan(plan_text: str) -> Plan:
    """Parse JSON string into Plan object"""
    return Plan.parse_raw(plan_text)
