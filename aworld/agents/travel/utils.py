# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json

from aworld.core.common import ActionModel


def parse_result(data):
    data = json.loads(data.replace("```json", "").replace("```", ""))
    actions = data.get("action", [])
    parsed_results = []

    for action in actions:
        for key, value in action.items():
            data = key.split("__")
            parsed_results.append(ActionModel(tool_name=data[0],
                                              action_name=data[1] if len(data) > 1 else None,
                                              params=value))
    return parsed_results
