import logging
import os
from pathlib import Path
import sys

from aworld.planner.plan import DefaultPlanner

from aworld.core.agent.swarm import TeamSwarm
from aworld.runner import Runners
from examples.tools.common import Tools

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ModelConfig

from aworld.cmd import BaseAWorldAgent, ChatCompletionRequest
from aworld.config.conf import AgentConfig, ModelConfig, TaskConfig
from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.runner import Runners
from .prompts import *

logger = logging.getLogger(__name__)



# os.environ["LLM_MODEL_NAME"] = "qwen/qwen3-8b"
# os.environ["LLM_BASE_URL"] = "http://localhost:1234/v1"
os.environ["LLM_MODEL_NAME"] = "DeepSeek-V3"
os.environ["LLM_BASE_URL"] = "https://agi.alipay.com/api"
os.environ["LLM_API_KEY"] = "sk-5d0c421b87724cdd883cfa8e883998da"

def get_deepresearch_swarm(user_input):

    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.getenv("LLM_MODEL_NAME"),
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_api_key=os.getenv("LLM_API_KEY")
        ),
        use_vision=False
    )

    plan_agent = Agent(
        name="planner_agent",
        desc="planner_agent",
        conf=agent_config,
        use_planner=True,
        planner=DefaultPlanner(plan_sys_prompt, replan_sys_prompt),
        use_tools_in_prompt=True
    )

    web_search_agent = Agent(
        name="web_search_agent",
        desc="web_search_agent",
        conf=agent_config,
        system_prompt_template=search_sys_prompt,
        tool_names=[Tools.SEARCH_API.value]
        # mcp_servers=["aworldsearch_server"],
        # mcp_config={
        #     "mcpServers": {
        #         "aworldsearch_server": {
        #             "command": "python",
        #             "args": [
        #                 "-m",
        #                 "mcp_servers.aworldsearch_server"
        #             ],
        #             "env": {
        #                 "AWORLD_SEARCH_URL": "https://antragflowInside.alipay.com/v1/rpc/ragLlmSearch",
        #                 "AWORLD_SEARCH_TOTAL_NUM": "10",
        #                 "AWORLD_SEARCH_SLICE_NUM": "3",
        #                 "AWORLD_SEARCH_DOMAIN": "google",
        #                 "AWORLD_SEARCH_SEARCHMODE": "RAG_LLM",
        #                 "AWORLD_SEARCH_SOURCE": "lingxi_agent",
        #                 "AWORLD_SEARCH_UID": "2088802724428205"
        #             }
        #         }
        #     }
        # }
    )
    
    reporting_agent = Agent(
        name="reporting_agent",
        desc="reporting_agent",
        conf=agent_config,
        system_prompt_template=reporting_sys_prompt,
    )

    return TeamSwarm(plan_agent, web_search_agent, reporting_agent, max_steps=1)
    

class AWorldAgent(BaseAWorldAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def name(self):
        return "Test Deepresearch Agent"

    def description(self):
        return "Test Deepresearch Agent"

    async def run(self, prompt: str = None, request: ChatCompletionRequest = None):

        if prompt is None and request is not None:
            prompt = request.messages[-1].content
        
        swarm = get_deepresearch_swarm(prompt)

        task = Task(
            input=prompt,
            swarm=swarm,
            conf=TaskConfig(max_steps=20),
            session_id=request.session_id,
            endless_threshold=50,
        )

        async for output in Runners.streamed_run_task(task).stream_events():
            logger.info(f"Agent Ouput: {output}")
            yield output
