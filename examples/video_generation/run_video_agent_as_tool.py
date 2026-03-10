# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

from aworld.core.context.base import Context
from aworld.utils.run_util import exec_agent

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from aworld.agents.video_agent import VideoAgent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.common import Observation
from aworld.core.event.base import Message
from aworld.core.tool.func_to_tool import be_tool
from aworld.runner import Runners

# ---------------------------------------------------------------------------
# Mock tool: read_image_as_base64
# ---------------------------------------------------------------------------
# Always returns a fixed URL regardless of the input path, for testing only.

@be_tool(
    tool_name="read_image_as_base64",
    tool_desc="Read an image file and return its base64-encoded data URI (mock version).",
    name="read_image_as_base64",
    desc="Read the image at the given path and return a base64 data URI suitable for use as image_url.",
)
def read_image_as_base64(image_path: str) -> str:
    """Mock implementation: ignores image_path and returns a fixed image URL.

    Args:
        image_path: Local file path of the image (ignored in this mock).

    Returns:
        A fixed image URL string.
    """
    return "https://mdn.alipayobjects.com/huamei_nmpvp9/afts/img/A*yLMFSKenCn4AAAAARxAAAAgAejqiAQ/original"

# ---------------------------------------------------------------------------
# Configuration — edit these or set the corresponding environment variables
# ---------------------------------------------------------------------------

# API_KEY = os.getenv("TOGETHER_API_KEY", "")
# MODEL   = os.getenv("TOGETHER_VIDEO_MODEL_NAME", "minimax/video-01-director")
VIDEO_API_KEY  = os.getenv("VIDEO_API_KEY")
VIDEO_BASE_URL = os.getenv("VIDEO_BASE_URL")
VIDEO_MODEL_NAME = "kling-v2-6"


if not VIDEO_API_KEY:
    print(
        "ERROR: VIDEO_API_KEY is not set.\n"
        "Export it before running:  export VIDEO_API_KEY=<your_key>"
    )
    sys.exit(1)

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL_NAME = "claude-sonnet-4-20250514"

if not LLM_API_KEY:
    print(
        "ERROR: LLM_API_KEY is not set.\n"
        "Export it before running:  export LLM_API_KEY=<your_key>"
    )
    sys.exit(1)


def example_agent_to_video():
    """Demonstrate LLMAgent calling VideoAgent as a tool via TeamSwarm.

    The LLMAgent acts as the root agent: it parses the user's request, uses
    a local tool to encode the image as base64, then calls the VideoAgent
    (registered as an agent-as-tool) with a structured ``info`` JSON string
    containing ``image_url`` and other video parameters.

    The ``info`` field is a JSON-encoded string so that the LLM can pass
    structured parameters through the standard agent tool-call schema::

        video_gen(content="...", info='{"image_url": "...", "duration": 5}')
    """
    import os
    from aworld.core.agent.swarm import TeamSwarm
    from aworld.agents.llm_agent import LLMAgent
    from aworld.config.conf import AgentConfig, ModelConfig

    # --------------- VideoAgent configuration ---------------
    video_conf = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=VIDEO_MODEL_NAME,
            llm_api_key=VIDEO_API_KEY,
            llm_base_url=VIDEO_BASE_URL,
        )
    )
    video_agent = VideoAgent(
        name="video_gen",
        conf=video_conf,
        desc=(
            "Video generation agent. Accepts a text prompt via 'content' and "
            "optional parameters via 'info' as a JSON string. "
            "Supported info keys: image_url (base64 data URI or URL used as the "
            "first keyframe), resolution (e.g. '720p'), duration (seconds), fps."
        ),
        poll=True,
        poll_interval=8,
        poll_timeout=300,
        default_resolution="720p",
    )

    # --------------- LLM agent configuration ---------------
    llm_conf = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=LLM_MODEL_NAME,
            llm_base_url=LLM_BASE_URL,
            llm_api_key=LLM_API_KEY,
        )
    )

    SYSTEM_PROMPT = """You are a helpful assistant that can generate videos from images.

When the user provides an image path, you MUST:
1. Call the `read_image_as_base64` tool to get the base64-encoded data URI for that image.
2. Call the `video_gen` agent with:
   - content: a detailed English text prompt describing the desired video motion/scene.
   - info: a JSON string with extra video parameters, e.g.:
       {"image_url": "<base64_data_uri_from_step_1>", "duration": 5, "resolution": "720p"}

Always pass image_url inside the info JSON string, NOT in content.
"""

    llm_agent = LLMAgent(
        name="master",
        conf=llm_conf,
        system_prompt=SYSTEM_PROMPT,
        tool_names=["read_image_as_base64"],
        agent_names=["video_gen"],
    )

    # --------------- Build TeamSwarm ---------------
    swarm = TeamSwarm(
        llm_agent,
        video_agent,
        communicate_agent=llm_agent,
        max_steps=10,
    )

    image_path = "~/Downloads/test.png"
    result = Runners.sync_run(
        input=f"In the local path there is an image: {image_path}. Please use this image to generate a video of the cartoon characters in the image dancing.",
        swarm=swarm,
    )
    print(f"answer: {result.answer}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    example_agent_to_video()
