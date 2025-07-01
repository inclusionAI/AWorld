# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from pathlib import Path
import os, sys
import json
import logging

logger = logging.getLogger(__name__)

from aworld.agents.plan_agent import PlanAgent
# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# LLM_BASE_URL = "https://agi.alipay.com/api"
LLM_BASE_URL = "http://localhost:1234/v1"
LLM_API_KEY = "sk-9329256ff1394003b6761615361a8f0f"
# LLM_MODEL_NAME = "QwQ-32B-Function-Call" # "shangshu.claude-3.7-sonnet"
LLM_MODEL_NAME = "qwen/qwen3-1.7b"
os.environ["LLM_API_KEY"] = LLM_API_KEY
os.environ["LLM_BASE_URL"] = LLM_BASE_URL
os.environ["LLM_MODEL_NAME"] = LLM_MODEL_NAME
os.environ['GOOGLE_API_KEY'] = "AIzaSyDl7Axs2CyS0nvBJ47QL30t84N2-azuFNQ"
os.environ['TAVILY_API_KEY'] = "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"

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.agent.swarm import Swarm, TeamSwarm
from aworld.runner import Runners
from examples.tools.common import Tools

plan_sys_prompt = "You are a helpful plan agent."
plan_prompt = """搜索，tool_calls的content参数是包含以下内容的一个json列表，必须符合json格式规范
["地平线公司的未来发展计划", "Momenta公司的未来发展计划", "地平线公司和Momenta公司的未来发展计划"]
"""

search_sys_prompt = "You are a helpful search agent."
# search_prompt = """
#     Please act as a search agent, constructing appropriate keywords and searach terms, using search toolkit to collect relevant information, including urls, webpage snapshots, etc.

#     Here are the question: {task}

#     pleas only use one action complete this task, at least results 6 pages.
#     """
search_prompt = """Conduct targeted aworld_search tools to gather the most recent, credible information on "{research_topic}" and synthesize it into a verifiable text artifact.

Instructions:
- Query should ensure that the most current information is gathered. The current date is {current_date}.
- Conduct multiple, diverse searches to gather comprehensive information.
- Consolidate key findings while meticulously tracking the source(s) for each specific piece of information.
- The output should be a well-written summary or report based on your search findings. 
- Only include the information found in the search results, don't make up any information.
- 输出中文结果
- 搜索工具的入参数量为1，结果数也为1
Research Topic:
{task}
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

    search1 = Agent(
        conf=agent_config,
        name="search_agent1",
        desc="search_agent1",
        system_prompt=search_sys_prompt,
        agent_prompt=search_prompt,
        # tool_names=[Tools.SEARCH_API.value],
    )

    # search2 = Agent(
    #     conf=agent_config,
    #     name="search_agent2",
    #     desc="search_agent2",
    #     system_prompt=search_sys_prompt,
    #     agent_prompt=search_prompt,
    #     # tool_names=[Tools.SEARCH_API.value],
    # )

    """创建解析函数的工厂函数"""
    def parse_multiple_contents(llm_resp):
        """解析包含多个内容的工具调用响应"""
        from aworld.core.agent.base import AgentResult
        from aworld.core.common import ActionModel
        
        if llm_resp.tool_calls is None or len(llm_resp.tool_calls) == 0:
            # 如果没有工具调用，返回空的AgentResult
            return AgentResult(actions=[], current_state=None)
        
        func_content = llm_resp.tool_calls[0].function
        try:
            arguments = json.loads(func_content.arguments)
            contents = arguments.get('content', [])
        except Exception as e:
            logger.error(f"Failed to parse tool call arguments: {llm_resp.tool_calls}, error: {e}")
            # 返回空的AgentResult
            return AgentResult(actions=[], current_state=None)
        print(f'contents: {contents}')
        
        actions = []
        for content in contents:
            # 为每个content创建一个独立的ActionModel
            new_arguments = {'content': content}
            actions.append(ActionModel(
                tool_name=func_content.name,
                tool_id=f"{llm_resp.tool_calls[0].id}" if len(contents) > 1 else llm_resp.tool_calls[0].id,
                agent_name="planer_agent",  # 使用字符串避免循环引用
                params=new_arguments,
                policy_info=llm_resp.content or ""
            ))
        print(f'actions: {actions}')
        return AgentResult(actions=actions, current_state=None)
        
    planer = PlanAgent(
        conf=agent_config,
        name="planer_agent",
        desc="planer_agent",
        system_prompt=plan_sys_prompt,
        agent_prompt=plan_prompt,
        agent_names=[search1.id(), summary.id()],
        resp_parse_func=parse_multiple_contents
    )

    # default is workflow swarm
    # swarm = TeamSwarm(planer, search1, search2, summary, max_steps=1)
    swarm = Swarm(planer, search1, summary, max_steps=1)

    prefix = ""
    # can special search google, wiki, duck go, or baidu. such as:
    # prefix = "search wiki: "
    res = Runners.sync_run(
        input=prefix + plan_prompt,
        swarm=swarm
    )
    print(res.answer)
