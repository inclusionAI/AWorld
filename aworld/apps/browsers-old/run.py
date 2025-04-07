# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.core.envs.tool import ToolFactory
from aworld.core.swarm import Swarm

from aworld.core.client import Client
from aworld.core.common import Agents, Tools
from aworld.core.task import Task
from aworld.agents.browser.agent import BrowserAgent
from aworld.config.conf import AgentConfig
from aworld.virtual_environments.conf import BrowserToolConfig


def main():
    client = Client()
    browser_tool_config = BrowserToolConfig(width=1280, height=720, keep_browser_open=True)

    agent_config = AgentConfig(
        agent_name=Agents.BROWSER.value,
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_num_ctx=32000,
        llm_temperature=1,
    )

    task_config = {
        'max_steps': 100,
        'max_actions_per_step': 100
    }

    client.submit(
        Task(input="""step1: first go to https://www.dangdang.com/ and search for 'the little prince' and rank by sales from high to low, get the first 5 results and put the products info in memory.
    step 2: write each product's title, price, discount, and publisher information to a fully structured HTML document with write_to_file, ensuring that the data is presented in a table with visible grid lines.
    step3: open the html file in browser by go_to_url""",
                    swarm=Swarm(BrowserAgent(conf=agent_config)),
                    tools=[ToolFactory(Tools.BROWSER.value, conf=browser_tool_config)],
                    task_config=task_config))


if __name__ == '__main__':
    main()
