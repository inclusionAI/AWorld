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
from aworld.sandbox import Sandbox
from aworld_cli.core import agent
from aworld_cli.core.skill_registry import collect_plugin_and_user_skills

from .mcp_config import mcp_config


@HookFactory.register(name="pre_media_comprehension_hook")
class PreMultiTaskMediaComprehensionHook(PreLLMCallHook):
    """Hook triggered before LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('media_comprehension'):
            # Logging and monitoring only - do not modify content
            pass
        return message


@HookFactory.register(name="post_media_comprehension_hook")
class PostMultiTaskMediaComprehensionHook(PostLLMCallHook):
    """Hook triggered after LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('media_comprehension'):
            # Logging and monitoring only - do not modify content
            pass
        return message


class MultiTaskMediaComprehensionAgent(Agent):
    """An agent specializing in understanding and analyzing images, audio, and video files."""

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """
        Execute the agent's policy for media comprehension tasks.
        
        This agent handles understanding and analysis of:
        1. Images: Visual content recognition, description, and interpretation.
        2. Audio: Speech recognition, transcription, and audio content analysis.
        3. Video: Video content understanding, scene analysis, and multimodal comprehension.
        """
        return await super().async_policy(observation, info, message, **kwargs)


@agent(
    name="media_comprehension",
    desc="""An intelligent assistant for understanding images, audio, and video files. Use when:
- Images: Recognize, describe, and interpret visual content.
- Audio: Transcribe speech and analyze audio content.
- Video: Understand video content, analyze scenes, and perform multimodal comprehension.
"""
)
def build_media_comprehension_swarm():
    """Build and configure the multi-task media_comprehension agent swarm."""
    # APP_EVALUATOR_SKILLS_DIR: override skill read directory (plugin root with skills/ subdir)
    plugin_base_dir = Path(__file__).resolve().parents[2]  # smllc plugin root
    env_skills_dir = Path(os.path.expanduser(os.environ.get("SKILLS_PATH"))).resolve()
    skill_configs = collect_plugin_and_user_skills(plugin_base_dir, user_dir=env_skills_dir)

    # Create Agent configuration with Claude Sonnet model
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("MEDIA_LLM_MODEL_NAME", "claude-3-5-sonnet-20241022"),
            llm_provider=os.environ.get("MEDIA_LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("MEDIA_LLM_API_KEY"),
            llm_base_url=os.environ.get("MEDIA_LLM_BASE_URL"),
            llm_temperature=float(os.environ.get("MEDIA_LLM_TEMPERATURE", "0.1")),
            params={"max_completion_tokens": 59000},
            llm_stream_call=os.environ.get("STREAM", "0").lower() in ("1", "true", "yes")
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

    # Create MultiTaskMediaComprehensionAgent instance
    media_comprehension = MultiTaskMediaComprehensionAgent(
        name="media_comprehension",
        desc="An intelligent assistant for understanding and analyzing images, audio, and video files.",
        conf=agent_config,
        system_prompt=_system_prompt,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        sandbox=sandbox,
        tool_names = ["CAST_SEARCH"]
    )

    # Return the Swarm containing this Agent
    return Swarm(media_comprehension)
