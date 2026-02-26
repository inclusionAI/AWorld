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
from aworld_cli.core.skill_registry import collect_plugin_and_user_skills

from .mcp_config import mcp_config


@HookFactory.register(name="pre_evaluator_hook")
class PreMultiTaskEvaluatorHook(PreLLMCallHook):
    """Hook triggered before LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('evaluator'):
            # Logging and monitoring only - do not modify content
            pass
        return message


@HookFactory.register(name="post_evaluator_hook")
class PostMultiTaskEvaluatorHook(PostLLMCallHook):
    """Hook triggered after LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('evaluator'):
            # Logging and monitoring only - do not modify content
            pass
        return message


class MultiTaskEvaluatorAgent(Agent):
    """A versatile agent specializing in evaluation and presenting professional suggestions for the apps/code/html/website improvement."""

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """
        Execute the agent's policy for multi-domain tasks.
        
        This agent handles two primary domains:
        1. Evaluation: Analyze the app/code/html/website's performance, user experience, and so on.
        2. Improvement: Present professional suggestions for the app/code/html/website improvement.
        """
        return await super().async_policy(observation, info, message, **kwargs)


@agent(
    name="evaluator",
    desc="""A versatile intelligent assistant for evaluation, when to use:
- Evaluation: Analyze the app/code/html/website's performance, user experience, and so on.
- Improvement: Present professional suggestions for the app/code/html/website improvement.
"""
)
def build_evaluator_swarm():
    """Build and configure the multi-task evaluator agent swarm."""
    # APP_EVALUATOR_SKILLS_DIR: override skill read directory (plugin root with skills/ subdir)
    plugin_base_dir = Path(__file__).resolve().parents[2]  # smllc plugin root
    env_skills_dir = Path(os.path.expanduser(os.environ.get("EVALUATOR_SKILLS_PATH"))).resolve()
    skill_configs = collect_plugin_and_user_skills(plugin_base_dir, user_dir=env_skills_dir)

    # Create Agent configuration with Claude Sonnet model
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "claude-3-5-sonnet-20241022"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.1")),
            params={"max_completion_tokens": 59000}
        ),
        skill_configs=skill_configs
    )

    # Extract all server keys from mcp_config
    mcp_servers = list(mcp_config.get("mcpServers", {}).keys())

    # Configure sandbox with MCP servers
    sandbox = Sandbox(
        mcp_config=mcp_config
    )
    sandbox.reuse = True

    _prompt_path = Path(__file__).resolve().parent / "prompt.txt"
    _system_prompt = _prompt_path.read_text(encoding="utf-8")

    # Create MultiTaskEvaluatorAgent instance
    evaluator = MultiTaskEvaluatorAgent(
        name="evaluator",
        desc="A versatile intelligent assistant that can evaluate the apps/code/html/website's performance, user experience, and so on, and present professional suggestions for the apps/code/html/website improvement.",
        conf=agent_config,
        system_prompt=_system_prompt,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        sandbox=sandbox,
        tool_names = ["CAST_SEARCH", "CAST_ANALYSIS", "human"]
    )

    # Return the Swarm containing this Agent
    return Swarm(evaluator)
