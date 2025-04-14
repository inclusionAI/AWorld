# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json

from aworld.config.conf import AgentConfig, ModelConfig

from aworld.core.agent.base import Agent, AgentResult, is_agent_by_name
from aworld.core.common import ActionModel
from aworld.logs.util import logger
from examples.travel.prompts import plan_sys_prompt, plan_prompt

model_config = ModelConfig(
    llm_provider="openai",
    llm_model_name="gpt-4o",
    llm_temperature=1,
    # need to set llm_api_key for use LLM
    llm_api_key=""
)
agent_config = AgentConfig(
    llm_config=model_config,
    # use_vision=False
)


def resp_parse(resp):
    results = []
    if not resp or not resp.choices:
        logger.warning("LLM no valid response!")
        return AgentResult(actions=[], current_state=None)

    is_call_tool = False
    content = resp.choices[0].message.content
    if content:
        content = content.replace("```json", "").replace("```", "")
    else:
        raise RuntimeError("no valid llm response.")
    tool_name = ''
    action_name = ''
    try:
        content = json.loads(content)
        actions = content.get('actions', [])
        if actions:
            action = actions[0]
            content = action.get('task', content.get('content'))
            tool_name = action.get('action', '')
    except:
        pass

    tool_action = tool_name.split('__')
    if len(tool_action) > 1:
        action_name = tool_action[1]
    # no tool call, agent name is itself.
    results.append(ActionModel(agent_name="example_plan_agent",
                               tool_name=tool_action[0],
                               action_name=action_name,
                               policy_info=content))
    return AgentResult(actions=results, current_state=None, is_call_tool=is_call_tool)


plan = Agent(
    conf=agent_config,
    name="example_plan_agent",
    system_prompt=plan_sys_prompt,
    agent_prompt=plan_prompt,
    resp_parse_func=resp_parse
)
