# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Dict, Any

from aworld.agents.llm_agent import Agent


async def agent_to_dict(agent: Agent, override: Dict[str, Any] = None):
    """Agent attribute dict."""
    attr_dict = {
        "name": agent.name(),
        "conf": agent.conf,
        "desc": agent.desc(),
        "agent_id": agent.id(),
        "task": agent.task,
        "tool_names": agent.tool_names,
        "agent_names": agent.handoffs,
        "mcp_servers": agent.mcp_servers,
        "mcp_config": agent.mcp_config,
        "feedback_tool_result": agent.feedback_tool_result,
        "wait_tool_result": agent.wait_tool_result,
        "sandbox": agent.sandbox,
        "system_prompt": agent.system_prompt,
        "need_reset": agent.need_reset,
        "step_reset": agent.step_reset,
        "use_tools_in_prompt": agent.use_tools_in_prompt,
        "black_tool_actions": agent.black_tool_actions,
        "model_output_parser": agent.model_output_parser,
        "tool_aggregate_func": agent.tools_aggregate_func,
        "event_handler_name": agent.event_handler_name,
        "event_driven": agent.event_driven,
        "skill_configs": agent.skill_configs
    }
    if override:
        attr_dict.update(override)
    return attr_dict
