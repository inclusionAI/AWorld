# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.core.envs.tool import ToolFactory
from aworld.core.agent.swarm import Swarm

from aworld.core.client import Client
from aworld.config.common import Agents, Tools
from aworld.core.task import Task
from aworld.agents.browser.agent import BrowserAgent
from aworld.agents.browser.config import BrowserAgentConfig
from aworld.virtual_environments.conf import BrowserToolConfig
from aworld.config.conf import ModelConfig

if __name__ == '__main__':
    client = Client()
    llm_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_temperature=0.3,
    )
    browser_tool_config = BrowserToolConfig(width=1280,
                                            height=720,
                                            headless=False,
                                            keep_browser_open=True,
                                            llm_config=llm_config)
    agent_config = BrowserAgentConfig(
        tool_calling_method="raw",
        name=Agents.BROWSER.value,
        llm_config=llm_config,
        max_actions_per_step=10,
        max_input_tokens=128000,
        working_dir=".",
        # llm model not supported vision, need to set `False`
        # use_vision=False
    )

    task_config = {
        'max_steps': 100,
        'max_actions_per_step': 100
    }

    Task(input="""step1: first go to https://www.dangdang.com/ and search for 'the little prince' and rank by sales from high to low, get the first 5 results and put the products info in memory.
        step 2: write each product's title, price, discount, and publisher information to a fully structured HTML document with write_to_file, ensuring that the data is presented in a table with visible grid lines.
        step3: open the html file in browser by go_to_url""",
         swarm=Swarm(BrowserAgent(conf=agent_config)),
         tools=[ToolFactory(Tools.BROWSER.value, conf=browser_tool_config)],
         task_config=task_config).run()
