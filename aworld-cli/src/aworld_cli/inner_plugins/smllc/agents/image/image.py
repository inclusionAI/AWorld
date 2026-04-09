import os
from pathlib import Path
from typing import Dict, Any, List

from aworld.agents.image_agent import ImageAgent
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


@HookFactory.register(name="pre_image_hook")
class PreImageCreatorHook(PreLLMCallHook):
    """Hook triggered before LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""

    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('image'):
            # Logging and monitoring only - do not modify content
            pass
        return message


@HookFactory.register(name="post_image_hook")
class PostImageCreatorHook(PostLLMCallHook):
    """Hook triggered after LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""

    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('image'):
            # Logging and monitoring only - do not modify content
            pass
        return message


class ImageCreatorAgent(ImageAgent):
    """An agent specializing in generating images from text prompts."""

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """
        Execute the agent's policy for image generation tasks.
        
        This agent handles image generation:
        1. Generating images from text descriptions.
        2. Supporting various image sizes and formats.
        3. Handling negative prompts and other image generation parameters.
        """
        return await super().async_policy(observation, info, message, **kwargs)


@agent(
    name="image_generator",
    desc="""An intelligent assistant specially designed for text-to-image generation. Use when:
- Generating images from text descriptions or prompts.
- Creating visual content based on textual input.
- Producing images with specific styles, sizes, or formats.

Cannot process (do NOT delegate to this agent): Video generation, audio generation, document reading/analysis (.pdf, .docx), database queries, web scraping, or general code debugging not related to image generation.

**Invocation format (MUST follow when calling):**
- `content`: Required. The text prompt describing the image to generate.
- `info`: Optional JSON string. Use when passing image params, e.g.:
  {"size": "1024x1024", "output_format": "png", "negative_prompt": "blurry, low quality", "seed": 42, "output_path": "./output/image.png"}
  Supported keys:
  - size: Image size (e.g., "1024x1024", "1024x768", "768x1024")
  - output_format: Output format (png, jpeg, webp), default: "png"
  - negative_prompt: Negative prompt to exclude from generation
  - seed: Random seed for reproducible generation
  - output_path: Output file path (optional, auto-generated if not provided)
"""
)
def build_image_swarm():
    """Build and configure the image generation agent swarm."""
    # APP_EVALUATOR_SKILLS_DIR: override skill read directory (plugin root with skills/ subdir)
    plugin_base_dir = Path(__file__).resolve().parents[2]  # smllc plugin root
    env_skills_dir = Path(os.path.expanduser(os.environ.get("SKILLS_PATH"))).resolve()
    skill_configs = collect_plugin_and_user_skills(plugin_base_dir, user_dir=env_skills_dir)

    # Create Agent configuration (IMAGE_* from models.image or fallback to LLM_*)
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("IMAGE_MODEL_NAME", ""),
            llm_provider=os.environ.get("IMAGE_PROVIDER", "image"),
            llm_api_key=os.environ.get("IMAGE_API_KEY"),
            llm_base_url=os.environ.get("IMAGE_BASE_URL"),
            llm_temperature=float(os.environ.get("IMAGE_TEMPERATURE", "0.1")),
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

    # Create ImageCreatorAgent instance
    image_agent = ImageCreatorAgent(
        name="image",
        desc="An intelligent assistant for generating images from text prompts.",
        conf=agent_config,
        system_prompt=_system_prompt,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        sandbox=sandbox,
    )

    # Return the Swarm containing this Agent
    return Swarm(image_agent)