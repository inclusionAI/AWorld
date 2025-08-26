

# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from aworld.agents.llm_agent import Agent
from aworld.config.conf import ModelConfig, AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.logs.util import color_log, Color
from aworld.runner import Runners
from aworld.tools.human.human import HUMAN

# os.environ["LLM_MODEL_NAME"] = "YOUR_LLM_MODEL_NAME"
# os.environ["LLM_BASE_URL"] = "YOUR_LLM_BASE_URL"
# os.environ["LLM_API_KEY"] = "YOUR_LLM_API_KEY"

if __name__ == '__main__':
    conf = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ["LLM_MODEL_NAME"],
            llm_base_url=os.environ["LLM_BASE_URL"],
            llm_api_key=os.environ["LLM_API_KEY"]
        )
    )
    agent = Agent(
        conf=conf,
        name='human_test',
        system_prompt="You are a helpful assistant.",
        tool_names=[HUMAN]
    )

    swarm = Swarm(agent, max_steps=1)
    result = Runners.sync_run(
        input="use human tool to ask a question, e.g. what is the weather in beijing?" \
              "please use HUMAN tool only once",
        swarm=swarm
    )
    color_log(f"agent result:{result.answer}", color=Color.pink)
