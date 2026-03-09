# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import traceback
import uuid
from typing import Any, Dict, List, Optional

from aworld.config.conf import AgentConfig
from aworld.core.agent.base import AgentResult
from aworld.core.common import ActionModel, Observation, Config
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants
from aworld.agents.llm_agent import LLMAgent
from aworld.models.model_response import ModelResponse, VideoGenerationResult
from aworld.logs.util import logger
from aworld.events.util import send_message
from aworld.output.base import Output


class VideoAgent(LLMAgent):
    """An agent dedicated to video generation via Together.ai.

    Each invocation is a single-round call: the agent submits one video
    generation request, waits for the result (optionally), and terminates.
    No tool-calling loop is entered.

    The video prompt is taken from ``Observation.content``.
    Additional video parameters can be supplied via ``Observation.info``
    (keys: ``image_url``, ``resolution``, ``duration``, ``fps``, and any
    provider-specific kwargs accepted by ``generate_video``).
    Instance-level defaults are used as fallbacks.

    Example usage::

        from aworld.agents.video_agent import VideoAgent
        from aworld.config.conf import AgentConfig

        agent = VideoAgent(
            name="video_gen",
            conf=AgentConfig(
                llm_provider="together_video",
                llm_model_name="minimax/video-01-director",
                llm_api_key="YOUR_TOGETHER_API_KEY",
            ),
            default_resolution="1366x768",
            poll=True,
        )
    """

    def __init__(
        self,
        name: str,
        conf: Config | None = None,
        desc: str = None,
        agent_id: str = None,
        *,
        # Video generation defaults (overridable per-call via Observation.info)
        poll: bool = True,
        poll_interval: float = 5.0,
        poll_timeout: float = 600.0,
        default_resolution: Optional[str] = None,
        default_duration: Optional[float] = None,
        default_fps: Optional[int] = None,
        **kwargs,
    ):
        """Initialize VideoAgent.

        Args:
            name: Agent name.
            conf: AgentConfig. If None, falls back to environment variables
                TOGETHER_VIDEO_MODEL_NAME, TOGETHER_API_KEY, and
                TOGETHER_BASE_URL.  ``llm_provider`` is forced to
                ``'together_video'``.
            desc: Agent description exposed as tool description.
            agent_id: Explicit agent ID; auto-generated if None.
            poll: Default polling mode passed to ``generate_video``.
                  When True (default), block until the job completes.
                  When False, return immediately with the task ID.
            poll_interval: Seconds between polling attempts.
            poll_timeout: Maximum seconds to wait for job completion.
            default_resolution: Default video resolution, e.g. '1366x768'.
            default_duration: Default video duration in seconds.
            default_fps: Default frames per second.
            **kwargs: Forwarded to ``LLMAgent.__init__``.
        """
        if conf is None:
            model_name = os.getenv("TOGETHER_VIDEO_MODEL_NAME", "minimax/video-01-director")
            api_key = os.getenv("TOGETHER_API_KEY", "")
            base_url = os.getenv("TOGETHER_BASE_URL", "")

            if not api_key:
                raise ValueError(
                    "TOGETHER_API_KEY environment variable must be set, "
                    "or pass an AgentConfig with llm_api_key."
                )

            conf = AgentConfig(
                llm_provider="together_video",
                llm_model_name=model_name,
                llm_api_key=api_key,
                llm_base_url=base_url or None,
            )
        else:
            # Force the provider to together_video regardless of what was passed
            if hasattr(conf, "llm_config") and conf.llm_config:
                conf.llm_config.llm_provider = "together_video"
            elif isinstance(conf, dict):
                conf.setdefault("llm_provider", "together_video")

        super().__init__(
            name=name,
            conf=conf,
            desc=desc or "Video generation agent powered by Together.ai",
            agent_id=agent_id,
            **kwargs,
        )

        self.poll = poll
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.default_resolution = default_resolution
        self.default_duration = default_duration
        self.default_fps = default_fps

    # ------------------------------------------------------------------
    # Core policy — single-round video generation
    # ------------------------------------------------------------------

    async def async_policy(
        self,
        observation: Observation,
        info: Dict[str, Any] = {},
        message: Message = None,
        **kwargs,
    ) -> List[ActionModel]:
        """Single-round video generation policy.

        Extracts the prompt and video parameters from the observation, calls
        the video generation provider, and returns the result as an
        ActionModel.  The agent is marked as finished immediately so no
        further loop iterations occur.

        Args:
            observation: Contains the video prompt in ``content`` and
                optional overrides in ``info`` (image_url, resolution,
                duration, fps, poll, poll_interval, poll_timeout, plus any
                extra kwargs forwarded to the provider).
            info: Supplementary information dict (merged with
                ``observation.info`` if both are non-empty).
            message: Incoming event message carrying context.
            **kwargs: Additional parameters (unused by this agent).

        Returns:
            A single-element list with an ActionModel whose ``policy_info``
            contains the serialized ``VideoGenerationResult`` dict, or an
            error description string on failure.
        """
        self.context = message.context if message else None
        self._finished = False

        # Merge observation.info and caller-supplied info
        obs_info: Dict[str, Any] = dict(observation.info or {})
        obs_info.update(info or {})

        prompt = observation.content or ""
        if not prompt:
            logger.warning(f"[VideoAgent:{self.id()}] Empty prompt received; video generation may fail.")

        # Resolve video parameters (observation.info overrides instance defaults)
        image_url: Optional[str] = obs_info.pop("image_url", None)
        resolution: Optional[str] = obs_info.pop("resolution", self.default_resolution)
        duration: Optional[float] = obs_info.pop("duration", self.default_duration)
        fps: Optional[int] = obs_info.pop("fps", self.default_fps)
        poll: bool = obs_info.pop("poll", self.poll)
        poll_interval: float = obs_info.pop("poll_interval", self.poll_interval)
        poll_timeout: float = obs_info.pop("poll_timeout", self.poll_timeout)

        # Any remaining keys in obs_info are forwarded to the provider
        extra_kwargs = obs_info

        logger.info(
            f"[VideoAgent:{self.id()}] Generating video: "
            f"model={self.model_name}, prompt={prompt!r:.100}, "
            f"resolution={resolution}, duration={duration}, fps={fps}, "
            f"poll={poll}"
        )

        video_response: Optional[ModelResponse] = None
        try:
            video_response = await self._invoke_video_generation(
                prompt=prompt,
                image_url=image_url,
                resolution=resolution,
                duration=duration,
                fps=fps,
                poll=poll,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
                context=message.context if message else None,
                **extra_kwargs,
            )
        except Exception as exc:
            error_msg = f"Video generation failed: {exc}"
            logger.error(
                f"[VideoAgent:{self.id()}] {error_msg}\n{traceback.format_exc()}"
            )
            if message:
                await send_message(
                    Message(
                        category=Constants.OUTPUT,
                        payload=Output(data=error_msg),
                        sender=self.id(),
                        session_id=message.context.session_id if message.context else "",
                        headers={"context": message.context},
                    )
                )
            self._finished = True
            return [ActionModel(agent_name=self.id(), policy_info=error_msg)]

        # Serialize the result for downstream consumption
        result_payload: Any
        if video_response and video_response.video_result:
            result_payload = video_response.video_result.to_dict()
            logger.info(
                f"[VideoAgent:{self.id()}] Video generation result: "
                f"status={video_response.video_result.status}, "
                f"video_url={video_response.video_result.video_url}"
            )
        else:
            result_payload = str(video_response)
            logger.warning(f"[VideoAgent:{self.id()}] Unexpected response: {video_response}")

        if message:
            await LLMAgent.send_agent_response_output(
                self, video_response, message.context, kwargs.get("outputs")
            )

        self._finished = True
        return [ActionModel(agent_name=self.id(), policy_info=result_payload)]

    async def _invoke_video_generation(
        self,
        prompt: str,
        image_url: Optional[str],
        resolution: Optional[str],
        duration: Optional[float],
        fps: Optional[int],
        poll: bool,
        poll_interval: float,
        poll_timeout: float,
        context: Context = None,
        **extra_kwargs,
    ) -> ModelResponse:
        """Call the underlying Together.ai video provider.

        Runs the blocking ``generate_video`` call in the default thread-pool
        executor so it does not block the event loop.

        Args:
            prompt: Text prompt for video generation.
            image_url: Optional first-frame image URL.
            resolution: Resolution string, e.g. '1366x768'.
            duration: Duration in seconds.
            fps: Frames per second.
            poll: Whether to poll until job completion.
            poll_interval: Seconds between poll attempts.
            poll_timeout: Maximum total wait time.
            context: Runtime context (unused by the provider, passed through).
            **extra_kwargs: Additional provider-specific parameters.

        Returns:
            ModelResponse with ``video_result`` populated.

        Raises:
            Any exception raised by the provider.
        """
        import asyncio

        provider = self.llm.provider
        loop = asyncio.get_event_loop()

        return await loop.run_in_executor(
            None,
            lambda: provider.generate_video_from_params(
                prompt=prompt,
                image_url=image_url,
                duration=duration,
                resolution=resolution,
                fps=fps,
                context=context,
                poll=poll,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
                **extra_kwargs,
            ),
        )

    # ------------------------------------------------------------------
    # Override is_agent_finished — always finish after one round
    # ------------------------------------------------------------------

    def is_agent_finished(self, llm_response: ModelResponse, agent_result: AgentResult) -> bool:
        """VideoAgent always finishes after a single round."""
        self._finished = True
        return True
