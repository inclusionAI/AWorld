# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from aworld.config.conf import AgentConfig, ModelConfig

from aworld.config.common import Tools
from aworld.core.agent.base import BaseAgent
from aworld.core.agent.swarm import Swarm
from aworld.core.task import Task

search_sys_prompt = "You are a helpful search agent."
search_prompt = """
    Please act as a search agent, constructing appropriate keywords and searach terms, using search toolkit to collect relevant information, including urls, webpage snapshots, etc.

    Here are the question: {task}

    pleas only use one action complete this task, at least results 6 pages.
    """

summary_sys_prompt = "You are a helpful general summary agent."

summary_prompt = """
Summarize the following text in one clear and concise paragraph, capturing the key ideas without missing critical points. 
Ensure the summary is easy to understand and avoids excessive detail.

Here are the content: 
{task}
"""

if __name__ == "__main__":
    # need to set GOOGLE_API_KEY and GOOGLE_ENGINE_ID to use Google search.
    os.environ['GOOGLE_API_KEY'] = ""
    os.environ['GOOGLE_ENGINE_ID'] = ""

    model_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_temperature=1,
        # need to set llm_api_key for use LLM
        llm_api_key=""
    )
    agent_config = AgentConfig(
        llm_config=model_config,
    )

    search = BaseAgent(
        conf=agent_config,
        name="search_agent",
        system_prompt=search_sys_prompt,
        agent_prompt=search_prompt,
        tool_names=[Tools.SEARCH_API.value]
    )

    summary = BaseAgent(
        conf=agent_config,
        name="summary_agent",
        system_prompt=summary_sys_prompt,
        agent_prompt=summary_prompt
    )
    # sequence swarm mode
    swarm = Swarm(search, summary)

    res = Task(
        input="""search baidu: Best places in Japan for kendo, tea ceremony, and Zen meditation near Kyoto, Nara, or Kobe""",
        swarm=swarm,
    ).run()
    print(res['answer'])
