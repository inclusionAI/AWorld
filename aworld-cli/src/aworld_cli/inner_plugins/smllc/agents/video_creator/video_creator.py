import os
from pathlib import Path
from typing import Dict, Any, List

from aworld.agents.llm_agent import Agent
from aworld.agents.video_agent import VideoAgent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation, ActionModel
from aworld.core.context.amni.config import get_default_config, AgentContextConfig, ContextEnvConfig
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PreLLMCallHook, PostLLMCallHook
from aworld.sandbox import Sandbox
from aworld_cli.core import agent
from aworld_cli.core.skill_registry import collect_plugin_and_user_skills
from .mcp_config import mcp_config


@HookFactory.register(name="pre_video_creator_hook")
class PreMultiTaskVideoCreatorHook(PreLLMCallHook):
    """Hook triggered before LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('video_creator'):
            # Logging and monitoring only - do not modify content
            pass
        return message


@HookFactory.register(name="post_video_creator_hook")
class PostMultiTaskVideoCreatorHook(PostLLMCallHook):
    """Hook triggered after LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('video_creator'):
            # Logging and monitoring only - do not modify content
            pass
        return message


class MultiTaskVideoCreatorAgent(VideoAgent):
    """An agent specializing in creating, editing, and generating video content."""

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """
        Execute the agent's policy for video creation tasks.
        
        This agent handles video creation and editing:
        1. Creating new videos from images, audio clips, or text.
        2. Editing existing videos (e.g., trimming, concatenating, adding effects).
        3. Adding or replacing audio tracks in videos.
        4. Programmatically generating animations or visual effects.
        """
        return await super().async_policy(observation, info, message, **kwargs)


@agent(
    name="video_creator",
    desc="""An intelligent assistant specially designed for creating, editing, and generating video content. Use when:
- Creating new videos from images, audio clips, or text.
- Editing existing videos (e.g., trimming, concatenating, adding effects or overlays).
- Adding or replacing audio tracks in videos.
- Programmatically generating animations or visual effects.

Cannot process (do NOT delegate to this agent): Document reading/analysis (.pdf, .docx), database queries, web scraping, or general code debugging not related to video creation.

**Invocation format (MUST follow when calling):**
- `content`: Required. The video generation prompt (text description of what to create).
- `info`: Required JSON string. Use when passing image/video params, e.g.:
  {"image_url": "<data_path_or_base64_string>", "reference_images": ["<path1>", "<path2>"], "resolution": "720p", "duration": 5, "fps": 24, "output_dir": "./output", "sound": "on"}
  Supported keys: image_url, reference_images (list of paths/URLs/base64), resolution, duration (must be ≤ 5 seconds), fps, poll, poll_interval, poll_timeout, download_video, output_dir.
"""
)
def build_video_creator_swarm():
    """Build and configure the multi-task video_creator agent swarm."""
    # APP_EVALUATOR_SKILLS_DIR: override skill read directory (plugin root with skills/ subdir)
    plugin_base_dir = Path(__file__).resolve().parents[2]  # smllc plugin root
    env_skills_dir = Path(os.path.expanduser(os.environ.get("SKILLS_PATH"))).resolve()
    skill_configs = collect_plugin_and_user_skills(plugin_base_dir, user_dir=env_skills_dir)

    # Create Agent configuration (DIFFUSION_* from models.diffusion or fallback to MEDIA_LLM_*/LLM_*)
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("DIFFUSION_MODEL_NAME", "claude-3-5-sonnet-20241022"),
            llm_provider=os.environ.get("DIFFUSION_PROVIDER", "openai"),
            llm_api_key=os.environ.get("DIFFUSION_API_KEY"),
            llm_base_url=os.environ.get("DIFFUSION_BASE_URL"),
            llm_temperature=float(os.environ.get("DIFFUSION_TEMPERATURE", "0.1")),
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

    # Create MultiTaskVideoCreatorAgent instance
    video_creator = MultiTaskVideoCreatorAgent(
        name="video_creator",
        desc="An intelligent assistant for creating, editing, and generating video content.",
        conf=agent_config,
        system_prompt=_system_prompt,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        sandbox=sandbox,
        # tool_names = ["CAST_SEARCH"]
    )

    # Return the Swarm containing this Agent
    return Swarm(video_creator)
