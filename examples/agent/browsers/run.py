# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.conf import ModelConfig
from aworld.core.task import Task
from aworld.runner import Runners
from examples.agent.browsers.agent import BrowserAgent
from examples.agent.browsers.config import BrowserAgentConfig
from examples.tools.common import Agents, Tools
from examples.tools.conf import BrowserToolConfig

if __name__ == '__main__':
    llm_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_temperature=0.3,

    )
    browser_tool_config = BrowserToolConfig(width=1280,
                                            height=720,
                                            headless=False,
                                            keep_browser_open=True,
                                            use_async=True,
                                            llm_config=llm_config)
    agent_config = BrowserAgentConfig(
        name=Agents.BROWSER.value,
        tool_calling_method="raw",
        llm_config=llm_config,
        max_actions_per_step=10,
        max_input_tokens=128000,
        working_dir="",
        # llm model not supported vision, need to set `False`
        # use_vision=False
    )

    task_config = {
        'max_steps': 100,
        'max_actions_per_step': 100
    }

    task = Task(
        input="""step1: first go to https://www.dangdang.com/ and search for 'the little prince' and rank by sales from high to low, get the first 5 results and put the products info in memory.
        step 2: write each product's title, price, discount, and publisher information to a fully structured HTML document with write_to_file, ensuring that the data is presented in a table with visible grid lines.
        step3: open the html file in browser by go_to_url""",
        agent=BrowserAgent(conf=agent_config, name=Agents.BROWSER.value, tool_names=[Tools.BROWSER.name]),
        tools_conf={Tools.BROWSER.value: browser_tool_config},
        conf=task_config
    )
    Runners.sync_run_task(task)
