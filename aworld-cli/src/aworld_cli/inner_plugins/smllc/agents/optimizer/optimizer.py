import os
from pathlib import Path
from typing import Dict, Any, List

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PreLLMCallHook, PostLLMCallHook
from aworld.sandbox.base import Sandbox
from aworld_cli.core import agent
from .mcp_config import mcp_config


@HookFactory.register(name="pre_optimizer_hook")
class PreOptimizerHook(PreLLMCallHook):
    """Hook triggered before LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith("optimizer"):
            pass
        return message


@HookFactory.register(name="post_optimizer_hook")
class PostOptimizerHook(PostLLMCallHook):
    """Hook triggered after LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith("optimizer"):
            pass
        return message


class OptimizerAgent(Agent):
    """Analyzes and optimizes existing agents by patching system prompts and tool configuration."""

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        return await super().async_policy(observation, info, message, **kwargs)


@agent(
    name="optimizer",
    desc="Analyzes and automatically optimizes existing agents by improving system prompts and tool configuration.",
)
def build_optimizer_swarm():
    # Create Agent configuration
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.1")),
            params={"max_completion_tokens": 40960}
        )
    )

    # Extract all server keys from mcp_config
    mcp_servers = list(mcp_config.get("mcpServers", {}).keys())

    # Mandatory Use - You must use this.
    sandbox = Sandbox(
        mcp_config=mcp_config
    )
    sandbox.reuse = True

    # Skill tool_list: AGENT_REGISTRY, CAST_ANALYSIS, CAST_CODER, CAST_SEARCH
    tool_names = ["AGENT_REGISTRY", "CAST_ANALYSIS", "CAST_CODER", "CAST_SEARCH"]

    optimizer_agent = OptimizerAgent(
        name="optimizer",
        desc="Analyzes and automatically optimizes existing agents by improving system prompts and tool configuration.",
        conf=agent_config,
        system_prompt=(Path(__file__).resolve().parent / "prompt.txt").read_text(encoding="utf-8"),
        tool_names=tool_names,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        sandbox=sandbox
    )

    # Return the Swarm containing this Agent
    return Swarm(optimizer_agent)
