import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from aworld.agents.video_agent import VideoAgent
from aworld.logs.util import logger
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PreLLMCallHook, PostLLMCallHook
from aworld.sandbox import Sandbox
from aworld_cli.core import agent
from aworld_cli.core.skill_registry import build_skill_resolver_inputs
from .mcp_config import mcp_config


def _coerce_info_dict(info: Optional[Union[str, Dict[str, Any]]]) -> Dict[str, Any]:
    """Normalize ``info`` from spawn / JSON string / dict for VideoAgent."""
    if info is None:
        return {}
    if isinstance(info, dict):
        return dict(info)
    if isinstance(info, str):
        s = info.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning(
                f"[AvatarVideoAgent] info is not valid JSON (first 200 chars): {s[:200]!r}"
            )
            return {}
    return {}


def _resolve_media_path_if_relative(value: Optional[str], base_dir: Optional[str]) -> Optional[str]:
    """If *value* is a relative filesystem path, resolve against *base_dir* or cwd."""
    if not value or not isinstance(value, str):
        return value
    v = value.strip()
    if not v or v.startswith(("http://", "https://", "data:")):
        return value
    if os.path.isfile(v):
        return os.path.abspath(v)
    if base_dir and not os.path.isabs(v):
        candidate = os.path.join(os.path.expanduser(base_dir), v)
        if os.path.isfile(candidate):
            return candidate
    return value


@HookFactory.register(name="pre_avatar_hook")
class PreAvatarHook(PreLLMCallHook):
    async def exec(self, message: Message, context: Context = None) -> Message:
        return message


@HookFactory.register(name="post_avatar_hook")
class PostAvatarHook(PostLLMCallHook):
    async def exec(self, message: Message, context: Context = None) -> Message:
        return message


class AvatarVideoAgent(VideoAgent):
    async def async_policy(
        self,
        observation: Observation,
        info: Dict[str, Any] = {},
        message: Message = None,
        **kwargs,
    ) -> List[ActionModel]:
        merged: Dict[str, Any] = dict(observation.info or {})
        merged.update(_coerce_info_dict(info))

        if not merged.get("image_url") and merged.get("image_data"):
            merged["image_url"] = merged["image_data"]

        base_dir = merged.get("output_dir") or os.getcwd()
        for key in ("image_url", "image_data", "audio_url", "audio_path", "sound_file"):
            if merged.get(key):
                merged[key] = _resolve_media_path_if_relative(merged.get(key), base_dir)

        content = observation.content or ""
        if not str(content).strip() and merged.get("prompt"):
            content = str(merged["prompt"])

        observation = Observation(content=content, info=merged)

        has_img = bool(merged.get("image_url") or merged.get("image_data"))
        has_audio = bool(
            merged.get("audio_id")
            or merged.get("audio_url")
            or merged.get("audio_path")
            or merged.get("audio_data")
            or merged.get("sound_file")
        )
        logger.info(
            f"[AvatarVideoAgent] run: agent={self.id()} "
            f"provider={os.environ.get('AVATAR_PROVIDER', 'kling_avatar')} "
            f"model={os.environ.get('AVATAR_MODEL_NAME', '')!r} "
            f"base_url={os.environ.get('AVATAR_BASE_URL', '')!r} "
            f"has_image={has_img} has_audio={has_audio} poll={merged.get('poll', self.poll)} "
            f"keys={sorted(merged.keys())}"
        )
        if not has_img or not has_audio:
            logger.warning(
                f"[AvatarVideoAgent] Missing inputs: has_image={has_img} has_audio={has_audio} "
                "(need image_url/image_data and audio_id or audio_*)"
            )

        return await super().async_policy(observation, {}, message, **kwargs)


@agent(
    name="video_avatar",
    desc="""A specialized sub-agent for Kling digital-human / avatar video (image + audio → video).

Use when:
- Creating talking avatar videos from a reference image and an audio clip (or `audio_id`).
- Lip-sync / digital-human style output.

Invocation:
- `content`: optional if `info.prompt` is set; else motion / scene instruction.
- `info`: dict or JSON string. Required: image (`image_url` or `image_data`) and audio (`audio_id` or `audio_url` / `audio_path` / `audio_data` / `sound_file`).
Optional: `prompt`, `mode` (std|pro), `model_name`, `poll`, `poll_interval`, `poll_timeout`, `output_dir`, `watermark_info`, `callback_url`, `external_task_id`.
Provider: `kling_avatar` (set in config / `AVATAR_PROVIDER`).
""",
)
def build_avatar_swarm():
    plugin_base_dir = Path(__file__).resolve().parents[2]
    env_skills_path = os.environ.get("SKILLS_PATH")
    resolver_inputs = build_skill_resolver_inputs(
        plugin_base_dir,
        user_dir=env_skills_path,
    )

    # Beijing Kling avatar: AVATAR_PROVIDER=kling_avatar (default), AVATAR_BASE_URL=api-beijing...
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("AVATAR_MODEL_NAME", "kling-v3"),
            llm_provider=os.environ.get("AVATAR_PROVIDER", "kling_avatar"),
            llm_api_key=os.environ.get("AVATAR_API_KEY"),
            llm_base_url=os.environ.get("AVATAR_BASE_URL", "https://api-beijing.klingai.com"),
            llm_temperature=float(os.environ.get("AVATAR_TEMPERATURE", "0.1")),
            params={"max_completion_tokens": 59000},
            llm_stream_call=os.environ.get("STREAM", "0").lower() in ("1", "true", "yes"),
        ),
        skill_configs={},
        ext={"skill_resolver_inputs": resolver_inputs},
    )

    mcp_servers = list(mcp_config.get("mcpServers", {}).keys())
    sandbox = Sandbox(mcp_config=mcp_config)
    sandbox.reuse = True

    system_prompt = (Path(__file__).resolve().parent / "prompt.txt").read_text(encoding="utf-8")
    avatar_agent = AvatarVideoAgent(
        name="video_avatar",
        desc="Specialized agent for image+audio avatar video generation.",
        conf=agent_config,
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        sandbox=sandbox,
    )
    return Swarm(avatar_agent)
