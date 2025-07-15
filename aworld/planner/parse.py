# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json

from aworld.logs.util import logger
from aworld.planner.models import Plan, StepInfos


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
