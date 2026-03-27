# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import base64
import os
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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


def _resolve_image_url_to_base64(image_url: Optional[str]) -> Optional[str]:
    """
    If image_url is a local disk path (or file:// URL), read the file and convert to base64 data URI.
    Otherwise return image_url unchanged (base64 or http(s) URL).
    """
    if not image_url or not isinstance(image_url, str):
        return image_url
    s = image_url.strip()
    if not s:
        return image_url
    # Already base64 data URI or remote URL: pass through
    if s.startswith("data:") or s.startswith("http://") or s.startswith("https://"):
        return image_url
    # file:// URL: extract path
    if s.startswith("file://"):
        s = urlparse(s).path
    # Treat as local path: try to read and convert
    path = Path(s)
    if not path.is_file():
        return image_url
    try:
        raw = path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        # Infer MIME from extension
        ext = path.suffix.lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
        mime = mime_map.get(ext, "image/png")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        logger.warning(f"[VideoAgent] Failed to read image from path {s!r}: {e}")
        return image_url


class VideoAgent(LLMAgent):
    """An agent dedicated to video generation.

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
        download_video: bool = True,
        output_dir: Optional[str] = None,
        **kwargs,
    ):
        """Initialize VideoAgent.

        Args:
            name: Agent name.
            conf: AgentConfig specifying the LLM provider, model, and
                credentials. Must not be None.
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
            download_video: Whether to download the generated video locally
                after generation. Defaults to True.
            output_dir: Directory to save downloaded videos. Defaults to the
                current working directory.
            **kwargs: Forwarded to ``LLMAgent.__init__``.
        """
        if conf is None:
            raise ValueError(
                "conf must be provided. Pass an AgentConfig with llm_provider, "
                "llm_model_name, and llm_api_key."
            )

        super().__init__(
            name=name,
            conf=conf,
            desc=desc or "Video generation agent",
            agent_id=agent_id,
            **kwargs,
        )

        self.poll = poll
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.default_resolution = default_resolution
        self.default_duration = default_duration
        self.default_fps = default_fps
        self.download_video = download_video
        self.output_dir = output_dir

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
                optional overrides in ``info`` (image_url, image_tail,
                resolution, duration, fps, poll, poll_interval, poll_timeout,
                plus any extra kwargs forwarded to the provider).
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
        image_url = _resolve_image_url_to_base64(image_url)

        image_tail: Optional[str] = obs_info.pop("image_tail", None)
        image_tail = _resolve_image_url_to_base64(image_tail)

        reference_images = obs_info.pop("reference_images", None)
        if reference_images:
            reference_images = [
                _resolve_image_url_to_base64(image_url)
                for image_url in reference_images
            ]
        sound: Optional[str] = obs_info.pop("sound", None)
        resolution: Optional[str] = obs_info.pop("resolution", self.default_resolution)
        duration: Optional[float] = obs_info.pop("duration", self.default_duration)
        fps: Optional[int] = obs_info.pop("fps", self.default_fps)
        poll: bool = obs_info.pop("poll", self.poll)
        poll_interval: float = obs_info.pop("poll_interval", self.poll_interval)
        poll_timeout: float = obs_info.pop("poll_timeout", self.poll_timeout)
        download_video: bool = obs_info.pop("download_video", self.download_video)
        output_dir: str = os.path.expanduser(
            obs_info.pop("output_dir", self.output_dir or os.getcwd())
        )

        # Any remaining keys in obs_info are forwarded to the provider
        extra_kwargs = obs_info
        if reference_images:
            extra_kwargs["reference_images"] = reference_images
        if image_tail:
            extra_kwargs["image_tail"] = image_tail
            extra_kwargs["last_frame_url"] = image_tail  # WanX adapter
        if sound:
            extra_kwargs["sound"] = sound

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
            logger.info(f"VideoAgent Execute response: {video_response}")
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
            if download_video:
                result_payload = await self.postprocess(
                    result=result_payload,
                    video_result=video_response.video_result,
                    output_dir=output_dir,
                )
        else:
            result_payload = str(video_response)
            logger.warning(f"[VideoAgent:{self.id()}] Unexpected response: {video_response}")

        if message:
            await LLMAgent.send_agent_response_output(
                self, video_response, message.context, kwargs.get("outputs")
            )

        self._finished = True
        # 设置params标记这是tool result，确保能正确反馈给调用方触发ReAct循环
        params = {"is_tool_result": True}
        policy_result = [ActionModel(agent_name=self.id(), policy_info=result_payload, params=params)]
        logger.info(f"agent_result: {result_payload}")
        return policy_result

    async def postprocess(
        self,
        result: Dict[str, Any],
        video_result: "VideoGenerationResult",
        output_dir: str,
    ) -> Dict[str, Any]:
        """Post-process the video generation result by downloading the video locally.

        Override this method to customise post-processing behaviour (e.g. upload
        to object storage, run a transcoding step, etc.).

        Args:
            result: The serialised ``VideoGenerationResult`` dict that will be
                returned as the agent result.  Mutated in-place and returned.
            video_result: The original ``VideoGenerationResult`` object.
            output_dir: Directory where the video file should be saved.

        Returns:
            Updated result dict.  On success the dict contains a
            ``local_path`` key with the absolute path of the downloaded file.
            On failure the original dict is returned unchanged with an extra
            ``download_error`` key describing the error.
        """
        video_url = video_result.video_url
        if not video_url:
            logger.warning(f"[VideoAgent:{self.id()}] No video_url available; skipping download.")
            return result

        task_id = video_result.task_id or ""
        local_path = await self._download_video(video_url, output_dir, task_id)
        if local_path:
            result["local_path"] = local_path
        return result

    async def _download_video(
        self,
        video_url: str,
        output_dir: str,
        task_id: str = "",
    ) -> Optional[str]:
        """Download a video from *video_url* into *output_dir*.

        Tries ``aiohttp`` first; falls back to ``urllib`` so the method works
        without optional dependencies.

        Args:
            video_url: Remote URL of the video file.
            output_dir: Local directory to save the file.
            task_id: Task ID used as part of the filename when the URL does not
                contain a recognisable filename.

        Returns:
            Absolute path of the saved file, or ``None`` on failure.
        """
        import asyncio

        # Derive a safe filename: video_<url-stem> or video_<task_id> or video_<uuid>
        _MAX_URL_FILENAME_LEN = 32
        try:
            url_path = urlparse(video_url).path
            raw_name = Path(url_path).name  # e.g. "abc123.mp4"
            stem, suffix = Path(raw_name).stem, Path(raw_name).suffix
            url_filename = stem[:_MAX_URL_FILENAME_LEN] + suffix if raw_name else ""
        except Exception:
            url_filename = ""

        if url_filename:
            filename = f"video_{url_filename}"
        elif task_id:
            filename = f"video_{task_id}.mp4"
        else:
            filename = f"video_{uuid.uuid4().hex}.mp4"

        os.makedirs(output_dir, exist_ok=True)
        local_path = os.path.join(output_dir, filename)

        logger.info(f"[VideoAgent:{self.id()}] Downloading video to {local_path} ...")

        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._download_video_sync(video_url, local_path),
            )
            logger.info(f"[VideoAgent:{self.id()}] Video saved to {local_path}")
            return local_path
        except Exception as exc:
            logger.warning(
                f"[VideoAgent:{self.id()}] Failed to download video from {video_url}: {exc}\n"
                f"{traceback.format_exc()}"
            )
            return None

    @staticmethod
    def _download_video_sync(video_url: str, local_path: str) -> None:
        """Blocking download implementation used inside a thread-pool executor.

        Tries ``requests`` first, then falls back to ``urllib`` so it works
        without optional dependencies.

        Args:
            video_url: Remote URL of the video file.
            local_path: Absolute local path to write the file.
        """
        try:
            import requests  # type: ignore
            resp = requests.get(video_url, stream=True, timeout=300)
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        except ImportError:
            import urllib.request
            urllib.request.urlretrieve(video_url, local_path)

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
        """Call the underlying video provider.

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
