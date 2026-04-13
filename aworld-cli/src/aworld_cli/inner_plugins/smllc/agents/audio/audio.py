import os
from pathlib import Path
from typing import Dict, Any, List

from aworld.agents.audio_agent import AudioAgent
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


@HookFactory.register(name="pre_audio_hook")
class PreMultiTaskVideoCreatorHook(PreLLMCallHook):
    """Hook triggered before LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""

    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('audio'):
            # Logging and monitoring only - do not modify content
            pass
        return message


@HookFactory.register(name="post_audio_hook")
class PostMultiTaskVideoCreatorHook(PostLLMCallHook):
    """Hook triggered after LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""

    async def exec(self, message: Message, context: Context = None) -> Message:
        if message.sender.startswith('audio'):
            # Logging and monitoring only - do not modify content
            pass
        return message


class AudioCreatorAgent(AudioAgent):
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
    name="audio_generator",
    desc="""An intelligent assistant specially designed for text-to-speech audio generation. Use when:
- Converting text to speech audio.
- Generating audio with different voices and styles.
- Creating audio files with customized speed and encoding.

Cannot process (do NOT delegate to this agent): Video generation, document reading/analysis (.pdf, .docx), database queries, web scraping, or general code debugging not related to audio generation.

**Invocation format (MUST follow when calling):**
- `content`: Required. The text to convert to speech.
- `info`: Optional JSON string. Use when passing audio params, e.g.:
  {"voice_type": "zh_male_M392_conversation_wvae_bigtts", "encoding": "mp3", "speed_ratio": 1.0, "output_path": "./output/audio.mp3", "uid": "user_123"}
  Supported keys:
  - voice_type: Voice type identifier (e.g., "zh_male_M392_conversation_wvae_bigtts")
  - encoding: Audio format (mp3, wav, pcm, ogg_opus), default: "mp3"
  - speed_ratio: Speech speed (0.5 to 2.0), default: 1.0
  - output_path: Output file path (optional, auto-generated if not provided)
  - uid: User ID for the request (optional)
"""
)
def build_audio_swarm():
    """Build and configure the multi-task audio agent swarm."""
    # APP_EVALUATOR_SKILLS_DIR: override skill read directory (plugin root with skills/ subdir)
    plugin_base_dir = Path(__file__).resolve().parents[2]  # smllc plugin root
    # Get user skills directory from environment (optional)
    env_skills_path = os.environ.get("SKILLS_PATH")
    env_skills_dir = Path(os.path.expanduser(env_skills_path)).resolve() if env_skills_path else None
    skill_configs = collect_plugin_and_user_skills(plugin_base_dir, user_dir=env_skills_dir)

    # Create Agent configuration (AUDIO_* from models.audio or fallback to MEDIA_LLM_*/LLM_*)
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("AUDIO_MODEL_NAME", os.environ.get("LLM_MODEL_NAME", "claude-3-5-sonnet-20241022")),
            llm_provider=os.environ.get("AUDIO_PROVIDER", os.environ.get("LLM_PROVIDER", "openai")),
            llm_api_key=os.environ.get("AUDIO_API_KEY", os.environ.get("LLM_API_KEY")),
            llm_base_url=os.environ.get("AUDIO_BASE_URL", os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")),
            llm_temperature=float(os.environ.get("AUDIO_TEMPERATURE", os.environ.get("LLM_TEMPERATURE", "0.1"))),
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
    audio = AudioCreatorAgent(
        name="audio_generator",
        desc="An intelligent assistant for creating, editing, and generating video content.",
        conf=agent_config,
        system_prompt=_system_prompt,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        sandbox=sandbox,
        # tool_names = ["CAST_SEARCH"]
    )

    # Return the Swarm containing this Agent
    return Swarm(audio)
