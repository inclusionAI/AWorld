# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.models.model_response import ModelResponse

from aworld.core.agent.base import Agent
from examples.travel.conf import agent_config
from examples.travel.prompts import plan_sys_prompt, plan_prompt


# def resp_parse(llm_resp: ModelResponse):
#     # custom parse LLM response
#     pass


plan = Agent(
    conf=agent_config,
    name="example_plan_agent",
    system_prompt=plan_sys_prompt,
    agent_prompt=plan_prompt,
    # resp_parse_func=resp_parse,
)
