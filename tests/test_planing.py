# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from pathlib import Path
import os, sys
import json
import logging

# logger = logging.getLogger(__name__)

from aworld.agents.plan_agent import PlanAgent
from aworld.core.context.prompts.dynamic_variables import create_simple_field_getter
from aworld.core.context.prompts.string_prompt_template import StringPromptTemplate
from aworld.models.model_response import ModelResponse

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# LLM_BASE_URL = "https://agi.alipay.com/api"
LLM_BASE_URL = "http://localhost:1234/v1"
LLM_API_KEY = "sk-9329256ff1394003b6761615361a8f0f"
# LLM_MODEL_NAME = "shangshu.claude-3.7-sonnet" #"DeepSeek-V3-Function-Call" # "QwQ-32B-Function-Call" # "shangshu.claude-3.7-sonnet"
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

plan_sys_prompt = """You are a strategic planning agent specialized in creating structured research plans. 

Your role is to:
1. Analyze research requirements and break them down into logical, sequential steps
2. Create comprehensive search strategies that gather information systematically
3. Ensure each search step builds upon previous findings for thorough analysis
4. Generate precise tool calls that follow the specified format requirements

Focus on creating plans that gather individual company information first, then synthesize comparisons."""
plan_prompt = """You need to create a plan using tool_calls.

Strategy:
1. use search_agent to search for "地平线公司的未来发展计划" and "Momenta公司的未来发展计划" to gather comprehensive information about future plans
2. use summary_agent to summary "地平线公司和Momenta公司的未来发展计划"

Requirements:
1. The name in tool_calls must strictly use the name specified in tools
2. The content parameter in tool_calls is a json list like: ["地平线公司的未来发展计划"], but summary_agent only accept one content
"""

search_sys_prompt = "You are a helpful search agent."
# search_prompt = """
#     Please act as a search agent, constructing appropriate keywords and searach terms, using search toolkit to collect relevant information, including urls, webpage snapshots, etc.

#     Here are the question: {task}

#     pleas only use one action complete this task, at least results 6 pages.
#     """
search_prompt = """Conduct targeted aworld_search tools to gather the most recent, credible information on "{task}" and synthesize it into a verifiable text artifact.

Instructions:
- Query should ensure that the most current information is gathered. The current date is {current_date}.
- Conduct multiple, diverse searches to gather comprehensive information.
- Consolidate key findings while meticulously tracking the source(s) for each specific piece of information.
- The output should be a well-written summary or report based on your search findings. 
- Only include the information found in the search results, don't make up any information.
- answer should be in English
- search tool's input number is 1, and result number is 1
Research Topic:
{task}
"""

summary_sys_prompt = "You are a helpful general summary agent."
summary_prompt = """You are an expert research assistant analyzing summaries about "{task}".

Instructions:
- Identify knowledge gaps or areas that need deeper exploration and generate a follow-up query. (1 or multiple).
- If provided summaries are sufficient to answer the user's question, don't generate a follow-up query.
- If there is a knowledge gap, generate a follow-up query that would help expand your understanding.
- Focus on technical details, implementation specifics, or emerging trends that weren't fully covered.
- answer should be in English
Requirements:
- Ensure the follow-up query is self-contained and includes necessary context for web search.

Output Format:
- Format your response as a JSON object with these exact keys:
   - "is_sufficient": true or false
   - "knowledge_gap": Describe what information is missing or needs clarification
   - "follow_up_queries": Write a specific question to address this gap

Example:
```
{{
    "is_sufficient": true, // or false
    "knowledge_gap": "The summary lacks information about performance metrics and benchmarks", // "" if is_sufficient is true
    "follow_up_queries": ["What are typical performance benchmarks and metrics used to evaluate [specific technology]?"] // [] if is_sufficient is true
}}
```

Reflect carefully on the Summaries to identify knowledge gaps and produce a follow-up query. Then, produce your output following this JSON format:

Summaries:
{trajectories}
"""

"""创建解析函数的工厂函数"""
def parse_multiple_contents(llm_resp: ModelResponse):
    """解析包含多个内容的工具调用响应"""
    from aworld.core.agent.base import AgentResult
    from aworld.core.common import ActionModel
    
    if llm_resp.tool_calls is None or len(llm_resp.tool_calls) == 0:
        # 如果没有工具调用，返回AgentResult: is_call_tool=False
        return AgentResult(actions=[ActionModel(policy_info=llm_resp.content)], current_state="done", is_call_tool=False)

    actions = []
    
    # 遍历所有的tool_calls，而不是只处理第一个
    for tool_call in llm_resp.tool_calls:
        func_content = tool_call.function
        try:
            arguments = json.loads(func_content.arguments)
            
            # 检查是否有content参数，且content是列表
            if 'content' in arguments and isinstance(arguments['content'], list):
                contents = arguments['content']
                # 为每个content创建一个独立的ActionModel
                for content in contents:
                    new_arguments = {'content': content}
                    print(f"new_arguments: {new_arguments}")
                    actions.append(ActionModel(
                        tool_name=func_content.name,
                        tool_id=f"{tool_call.id}_{content}" if len(contents) > 1 else tool_call.id,
                        agent_name="planer_agent",  # 使用字符串避免循环引用
                        params=new_arguments,
                        policy_info=llm_resp.content or ""
                    ))
            else:
                # 如果content不是列表或不存在，直接使用原始参数
                actions.append(ActionModel(
                    tool_name=func_content.name,
                    tool_id=tool_call.id,
                    agent_name="planer_agent",
                    params=arguments,
                    policy_info=llm_resp.content or ""
                ))
                
        except Exception as e:
            print(f"Failed to parse tool call arguments: {tool_call}, error: {e}")
            # 跳过解析失败的tool_call，继续处理下一个
            continue
    
    print(f'Total tool_calls processed: {len(llm_resp.tool_calls)}')
    print(f'Total actions created: {len(actions)}')
    print(f'Actions: {actions}')
    
    return AgentResult(actions=actions, current_state=None)
    

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
        # agent_prompt=summary_prompt,
        agent_prompt_template=StringPromptTemplate(template=summary_prompt,
                                                   partial_variables={"trajectories": create_simple_field_getter("trajectories")})
    )

    search = Agent(
        conf=agent_config,
        name="search_agent",
        desc="search_agent",
        system_prompt=search_sys_prompt,
        agent_prompt=search_prompt,
        tool_names=[Tools.SEARCH_API.value],
    )

    planer = PlanAgent(
        conf=agent_config,
        name="planer_agent",
        desc="planer_agent",
        system_prompt=plan_sys_prompt,
        agent_prompt=plan_prompt,
        agent_names=[search.id(), summary.id()],
        resp_parse_func=parse_multiple_contents
    )

    # default is workflow swarm
    # swarm = TeamSwarm(planer, search1, search2, summary, max_steps=1)
    swarm = Swarm(planer, search, summary, max_steps=1)

    prefix = ""
    # can special search google, wiki, duck go, or baidu. such as:
    # prefix = "search wiki: "
    res = Runners.sync_run(
        input=prefix + plan_prompt,
        swarm=swarm
    )
    print(res.answer)
