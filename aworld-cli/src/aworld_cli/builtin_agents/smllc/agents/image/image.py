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
        
        This agent handles image generation and image editing:
        1. Generating images from text descriptions.
        2. Editing images when input images are provided.
        3. Supporting various image sizes and formats.
        4. Handling negative prompts and other image generation parameters.
        """
        return await super().async_policy(observation, info, message, **kwargs)


@agent(
    name="image_generator",
    desc="""An intelligent assistant specially designed for image generation and editing. Use when:
- Generating images from text descriptions or prompts.
- Editing an existing image according to a text instruction.
- Creating visual content based on textual input.
- Producing images with specific styles, sizes, or formats.
- Modifying an image that already appeared earlier in the conversation context, tool results, or message headers.

Cannot process (do NOT delegate to this agent): Video generation, audio generation, document reading/analysis (.pdf, .docx), database queries, web scraping, or general code debugging not related to image generation.

Important routing rule:
- If the user says things like "这只猫", "这张图", "上面那张", "刚才生成的图片", "把它改成..." or otherwise refers to an existing image in context, the caller should treat that as image editing.
- In that case, the caller should pass the existing contextual image through `info.image_urls` / `info.image_url` instead of converting the request into a brand new text-to-image prompt.
- Only use pure text-to-image when there is no existing image to edit.

**Invocation format (MUST follow when calling):**
- `content`: Required. The text prompt describing the image to generate.
- `info`: Optional JSON string. Use when passing image params, e.g.:
  {"size": "1024x1024", "output_format": "png", "negative_prompt": "blurry, low quality", "seed": 42, "output_path": "./output/image.png"}
  Editing example:
  {"image_urls": ["https://example.com/input.png"], "size": "1328x1328", "guidance_scale": 4.5, "num_inference_steps": 30, "watermark": false, "output_path": "./output/edit.png"}
  Context-follow edit example:
  When the user says "把这只猫改成黄色", reuse the image already present in context and call:
  {"image_urls": ["<image-from-context>"], "output_path": "./output/edit.png"}
  Supported keys:
  - size: Image size (e.g., "1024x1024", "1024x768", "768x1024")
  - output_format: Output format (png, jpeg, webp), default: "png"
  - response_format: `url` or `b64_json`. Default behavior prefers `url` so the caller can receive a remote download link in the tool result.
  - negative_prompt: Negative prompt to exclude from generation
  - seed: Random seed for reproducible generation
  - image_urls / image_url / input_image / input_images / reference_images: Input image(s) for edit models. Remote HTTP/HTTPS URLs will be sent as the `url` field. Local file paths will be read and base64-encoded into the `image` field. `data:image/...` inputs will be normalized to base64 and sent as the `image` field.
  - guidance_scale / num_inference_steps / strength / watermark / prompt_extend / n: Extra parameters forwarded to compatible edit models when accepted by the backend.
  - output_path: Output file path (optional, auto-generated if not provided)

Expected tool result fields:
- `output_path`: requested local output path
- `local_path`: downloaded local file path when `response_format=url`
- `image_url`: remote image download link when the backend returns a URL
- `image_format`, `image_size_bytes`, `usage`

Current behavior:
- Default backend (`llm_provider=image`, env `TEXT_TO_IMAGE_PROVIDER=image`): text-to-image uses JSON `POST /v1/images/generations`; single-image edits use multipart `POST /v1/images/edits`.
- Kling backend (`llm_provider=kling_image`, env `TEXT_TO_IMAGE_PROVIDER=kling_image`): async task API — `POST /v1/images/generations` (text or one reference image) or `POST /v1/images/multi-image2image` (two or more reference images), then poll until images are ready.
- The agent prefers `response_format=url` by default so upstream callers such as `Aworld` can receive the remote image link.
- For the default Qwen-style backend, edits use `url`/`image` as above; Kling accepts URLs or raw base64 for reference images.
"""
)
def build_image_swarm():
    """Build and configure the image generation agent swarm."""
    # APP_EVALUATOR_SKILLS_DIR: override skill read directory (plugin root with skills/ subdir)
    plugin_base_dir = Path(__file__).resolve().parents[2]  # smllc bundle root
    env_skills_dir = Path(os.path.expanduser(os.environ.get("SKILLS_PATH"))).resolve()
    skill_configs = collect_plugin_and_user_skills(plugin_base_dir, user_dir=env_skills_dir)

    # Create Agent configuration (TEXT_TO_IMAGE_* as base image config)
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("TEXT_TO_IMAGE_MODEL_NAME", os.environ.get("IMAGE_MODEL_NAME", "")),
            llm_provider=os.environ.get("TEXT_TO_IMAGE_PROVIDER", os.environ.get("IMAGE_PROVIDER", "image")),
            llm_api_key=os.environ.get("TEXT_TO_IMAGE_API_KEY", os.environ.get("IMAGE_API_KEY")),
            llm_base_url=os.environ.get("TEXT_TO_IMAGE_BASE_URL", os.environ.get("IMAGE_BASE_URL")),
            llm_temperature=float(os.environ.get("TEXT_TO_IMAGE_TEMPERATURE", os.environ.get("IMAGE_TEMPERATURE", "0.1"))),
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
        name="image_generator",
        desc="An intelligent assistant for generating images from text prompts.",
        conf=agent_config,
        default_response_format=os.environ.get("IMAGE_RESPONSE_FORMAT", "url"),
        system_prompt=_system_prompt,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        sandbox=sandbox,
    )

    # Return the Swarm containing this Agent
    return Swarm(image_agent)
