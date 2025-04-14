# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from aworld.agents import BrowserAgent
from aworld.agents.browser.config import BrowserAgentConfig
from aworld.config import ModelConfig
from aworld.config.common import Tools

from aworld.core.agent.swarm import Swarm
from aworld.core.task import Task
from aworld.virtual_environments.conf import BrowserToolConfig
from examples.travel.search_agent import search
from examples.travel.write_agent import write
from examples.travel.plan_agent import plan

# TODO: social mode
if __name__ == '__main__':
    llm_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_temperature=1,
        # need to set llm_api_key for use LLM
        llm_api_key=""
    )
    agent_config = BrowserAgentConfig(
        llm_config=llm_config,
        # use_vision=False
    )

    goal = """
    I need a 7-day Japan itinerary from April 2 to April 8 2025, departing from Hangzhou, We want to see beautiful cherry blossoms and experience traditional Japanese culture (kendo, tea ceremonies, Zen meditation). We would like to taste matcha in Uji and enjoy the hot springs in Kobe. I am planning to propose during this trip, so I need a special location recommendation. Please provide a detailed itinerary and create a simple HTML travel handbook that includes a 7-day Japan itinerary, an updated cherry blossom table, attraction descriptions, essential Japanese phrases, and travel tips for us to reference throughout our journey.
    you need search and extract different info 3 times, and then write, at last use browser agent goto the html url and then, complete the task.
    """

    # os.environ['GOOGLE_API_KEY'] = ""
    # os.environ['GOOGLE_ENGINE_ID'] = ""

    browser_agent = BrowserAgent(name='browser_agent', conf=agent_config, tool_names=["browser"])
    browser_tool = BrowserToolConfig(width=800, height=720, keep_browser_open=True, llm_config=llm_config)

    swarm = Swarm(plan, search, plan, browser_agent, plan, write)
    task = Task(swarm=swarm, input=goal,
                tools_conf={Tools.BROWSER.value: browser_tool})
    task.run()
