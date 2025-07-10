
import json
import os
from pathlib import Path
import random
import sys
import unittest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from aworld.core.agent.swarm import TeamSwarm
from aworld.runner import Runners
from examples.tools.common import Tools


from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ModelConfig

from aworld.agents.plan_agent import PlanAgent
from aworld.planner.built_in_planner import BuiltInPlanner
from aworld.core.context.base import Context
from aworld.models.llm import LLMModel
from aworld.planner.built_in_output_parser import BuiltInPlannerOutputParser
from examples.deepresearch.prompts import *

# os.environ["LLM_MODEL_NAME"] = "qwen/qwen3-1.7b"
# os.environ["LLM_BASE_URL"] = "https://agi.alipay.com/api"
os.environ["LLM_MODEL_NAME"] = "DeepSeek-V3"
os.environ["LLM_BASE_URL"] = "https://agi.alipay.com/api"
os.environ["LLM_API_KEY"] = "123"

def test_deepresearch():
    user_input = "7天北京旅游计划"

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
        use_planner=True
    )

    web_search_agent = Agent(
        name="web_search_agent",
        desc="web_search_agent",
        conf=agent_config,
        system_prompt=search_sys_prompt,
        mcp_servers=["aworldsearch_server"],
        mcp_config={
            "mcpServers": {
                "aworldsearch_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.aworldsearch_server"
                    ],
                    "env": {
                        "AWORLD_SEARCH_URL": "https://antragflowInside.alipay.com/v1/rpc/ragLlmSearch",
                        "AWORLD_SEARCH_TOTAL_NUM": "10",
                        "AWORLD_SEARCH_SLICE_NUM": "3",
                        "AWORLD_SEARCH_DOMAIN": "google",
                        "AWORLD_SEARCH_SEARCHMODE": "RAG_LLM",
                        "AWORLD_SEARCH_SOURCE": "lingxi_agent",
                        "AWORLD_SEARCH_UID": "2088802724428205"
                    }
                }
            }
        }
    )
    
    reporting_agent = Agent(
        name="reporting_agent",
        desc="reporting_agent",
        conf=agent_config,
        system_prompt=reporting_sys_prompt,
    )

    swarm = TeamSwarm(plan_agent, web_search_agent, reporting_agent, max_steps=1)
    result = Runners.sync_run(
        input=user_input,
        swarm=swarm
    )
    print(result)

if __name__ == "__main__":
    test_deepresearch()
