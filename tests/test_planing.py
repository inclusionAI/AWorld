# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from pathlib import Path
import os, sys
# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

LLM_BASE_URL = "https://agi.alipay.com/api"
LLM_API_KEY = "sk-9329256ff1394003b6761615361a8f0f"
LLM_MODEL_NAME = "QwQ-32B-Function-Call" # "shangshu.claude-3.7-sonnet"
os.environ["LLM_API_KEY"] = LLM_API_KEY
os.environ["LLM_BASE_URL"] = LLM_BASE_URL
os.environ["LLM_MODEL_NAME"] = LLM_MODEL_NAME
os.environ['GOOGLE_API_KEY'] = "AIzaSyDl7Axs2CyS0nvBJ47QL30t84N2-azuFNQ"
os.environ['TAVILY_API_KEY'] = "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"

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from examples.tools.common import Tools

search_sys_prompt = "You are a helpful search agent."
# search_prompt = """
#     Please act as a search agent, constructing appropriate keywords and searach terms, using search toolkit to collect relevant information, including urls, webpage snapshots, etc.

#     Here are the question: {task}

#     pleas only use one action complete this task, at least results 6 pages.
#     """
search_prompt = """
using summary_agent tools to summarize the following content:
1. 今天是周一
2. 周一是好天气
3. 好天气适合运动
"""

summary_sys_prompt = "You are a helpful general summary agent."

summary_prompt = """
Summarize the following text in one clear and concise paragraph, capturing the key ideas without missing critical points. 
Ensure the summary is easy to understand and avoids excessive detail.

Here are the content: 
{task}
"""

# search and summary
if __name__ == "__main__":
    # need to set GOOGLE_API_KEY and GOOGLE_ENGINE_ID to use Google search.
    # os.environ['GOOGLE_API_KEY'] = ""
    # os.environ['GOOGLE_ENGINE_ID'] = ""

    agent_config = AgentConfig(
        # llm_provider="openai",
        llm_model_name=LLM_MODEL_NAME,
        llm_temperature=1,
        llm_base_url=LLM_BASE_URL,
        llm_api_key=LLM_API_KEY,
        # need to set llm_api_key for use LLM
    )

    summary = Agent(
        conf=agent_config,
        name="summary_agent",
        desc="summary_agent",
        system_prompt=summary_sys_prompt,
        agent_prompt=summary_prompt
    )

    search = Agent(
        conf=agent_config,
        name="search_agent",
        system_prompt=search_sys_prompt,
        agent_prompt=search_prompt,
        # tool_names=[Tools.SEARCH_API.value],
        agent_names=[summary.id()]
    )

    # default is workflow swarm
    swarm = Swarm(search, summary, max_steps=1)

    prefix = ""
    # can special search google, wiki, duck go, or baidu. such as:
    # prefix = "search wiki: "
    res = Runners.sync_run(
        input=prefix + search_prompt,
        swarm=swarm
    )
    print(res.answer)
