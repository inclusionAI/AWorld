import os
from datetime import datetime

from aworld.agents.llm_agent import Agent
from aworld.config import ModelConfig
from aworld.config.conf import AgentConfig

from ..deepresearch_prompt import *


def create_plan_agent(agent_config: dict):
    def _current_agent_name():
        return "plan_agent"

    custom_sys_prompt = None
    custom_llm = None
    if agent_config and agent_config.get('agents', None) and agent_config['agents']:
        for config in agent_config['agents']:
            if config.get("id") == _current_agent_name():
                custom_sys_prompt = config.get("prompt")
                custom_llm = config.get("model")
                break

    model_config = ModelConfig(
        llm_provider="openai",
        llm_model_name=custom_llm or os.getenv("LLM_MODEL_NAME"),
        llm_base_url=os.getenv("LLM_BASE_URL"),
        llm_api_key=os.getenv("LLM_API_KEY")
    )

    agent_config = AgentConfig(
        llm_config=model_config,
        use_vision=False
    )

    return Agent(
        name=_current_agent_name(),
        desc="plan_agent",
        conf=agent_config,
        system_prompt=custom_sys_prompt or plan_sys_prompt.format(system_cur_date =datetime.now().strftime("%Y年%m月%d日")),
        step_reset=False,
        event_driven=False
    )