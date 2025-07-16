import os
import uuid

from aworld.config.conf import AgentConfig, ToolConfig
from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.config import ModelConfig, TaskConfig
from ..deepresearch_prompt import *
from aworld.runner import Runners

# loop agent
# reasoning_loop_agent = LoopableAgent(
#     name="reasoning_loop_agent",
#     desc="reasoning_loop_agent",
#     conf=agent_config,
#     max_run_times=5,
#     system_prompt=reasoning_loop_sys_prompt,
#     mcp_servers=[
#         # "ms-playwright", "google-search",
#         "tavily"
#     ],
#     mcp_config={
#         "mcpServers": {
#             "tavily": {
#                 "command": "npx",
#                 "args": ["-y", "tavily-mcp@0.2.2"],
#                 "env": {
#                     "TAVILY_API_KEY": os.environ["TAVILY_API_KEY"],
#                     "SESSION_REQUEST_CONNECT_TIMEOUT": "60"
#                 }
#             }
#         }
#     }
# )

def create_reasoning_loop_agent(agent_config: dict):
    def _current_agent_name():
        return "reasoning_loop_agent"

    custom_sys_prompt = None
    custom_llm = None
    custom_mcp_servers = None
    if agent_config and agent_config.get('agents', None) and agent_config['agents']:
        for config in agent_config['agents']:
            if config.get("id") == _current_agent_name():
                custom_sys_prompt = config.get("prompt")
                custom_llm = config.get("model")
                custom_mcp_servers = config.get("mcp")
                break

    model_config = ModelConfig(
        llm_provider="openai",
        llm_model_name=custom_llm or os.getenv("LLM_MODEL_NAME"),
        llm_base_url=os.getenv("LLM_BASE_URL"),
        llm_api_key=os.getenv("LLM_API_KEY")
    )

    agent_config = AgentConfig(
        llm_config=model_config,
        use_vision=False
    )

    return Agent(
        name=_current_agent_name(),
        desc="reasoning_loop_agent",
        conf=agent_config,
        system_prompt=custom_sys_prompt or reasoning_loop_sys_prompt,
        step_reset=False,
        event_driven=False,
        mcp_servers=[
            # "ms-playwright", "google-search",
            "tavily",
            #"amap-amap-sse",
            #"tongyi-wanxiang"
        ],
        mcp_config={
            "mcpServers": {
                "tavily": {
                    "command": "npx",
                    "args": ["-y", "tavily-mcp@0.2.2"],
                    "env": {
                        "TAVILY_API_KEY": os.environ["TAVILY_API_KEY"],
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "60"
                    }
                },
                # "tongyi-wanxiang": {
                #     "command": "npx",
                #     "args": [
                #         "-y",
                #         "tongyi-wanx-mcp-server@latest"
                #     ],
                #     "env": {
                #         "DASHSCOPE_API_KEY": os.environ["TONGYI_WANGXIANG_API_KEY"]
                #     }
                # }
            }
        }
    )

