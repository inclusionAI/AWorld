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

LLM_BASE_URL = "http://localhost:1234/v1"
LLM_API_KEY = "123"
LLM_MODEL_NAME = "qwen/qwen3-1.7b"
#"DeepSeek-V3-Function-Call"
# "qwen/qwen3-8b" 
# "claude-3-7-sonnet-20250219"
# "QwQ-32B-Function-Call"
# LLM_API_KEY = "sk-5d0c421b87724cdd883cfa8e883998da"
os.environ["LLM_API_KEY"] = LLM_API_KEY
os.environ["LLM_BASE_URL"] = LLM_BASE_URL
os.environ["LLM_MODEL_NAME"] = LLM_MODEL_NAME
os.environ['GOOGLE_API_KEY'] = "AIzaSyDl7Axs2CyS0nvBJ47QL30t84N2-azuFNQ"
os.environ['TAVILY_API_KEY'] = "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"

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ContextRuleConfig, LlmCompressionConfig, ModelConfig, OptimizationConfig
from aworld.core.agent.swarm import Swarm, TeamSwarm
from aworld.runner import Runners
from examples.tools.common import Tools

plan_sys_prompt = """You are a strategic planning agent specialized in creating structured research plans. 
"""
plan_prompt = """Requirements:
1. **IMPORTANT**: You need to create a plan using tool_calls, try your best to use tools to complete tasks instead of doing it yourself.
2. The name in tool_calls must strictly use the name specified in tools
3. The content parameter in tool_calls is a json list like: ["something"], if you want to use a tool multiple times, use ["a", "b"] format
4. **IMPORTANT: Goal Achievement Check**: If trajectories already contain the goal, don't return tool_call, and if you plan to end, output the summary report from trajectories

tools:
{tool_list}

trajectories:
{trajectories}

Research topic:
调研地平线公司和Momenta公司的未来发展计划"""

search_sys_prompt = """You are a helpful search agent.
"""

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

summary_sys_prompt = """You are a helpful general summary agent.
"""
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
        max_model_len=8000,
        # need to set llm_api_key for use LLM
    )

    summary = Agent(
        conf=agent_config,
        name="summary_agent",
        desc="summary_agent",
        system_prompt=summary_sys_prompt,
        agent_prompt=summary_prompt,
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
        context_rule=ContextRuleConfig(
            optimization_config=OptimizationConfig(
                enabled=True,
                max_token_budget_ratio=1
            ),
            llm_compression_config=LlmCompressionConfig(
                enabled=True,
                trigger_compress_token_length=20000,
                compress_model=ModelConfig(
                    llm_model_name=LLM_MODEL_NAME,
                    llm_base_url=LLM_BASE_URL,
                    llm_api_key=LLM_API_KEY,
                )
            )
        )
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

