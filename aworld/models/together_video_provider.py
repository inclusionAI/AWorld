import os
import time
import traceback
from typing import Any, Dict, Optional

from aworld.core.context.base import Context
from aworld.core.video_gen_provider import (
    VideoGenProviderBase,
    VideoGenerationRequest,
    AspectRatio,
    VideoResolution,
)
from aworld.models.model_response import ModelResponse, VideoGenerationResult, LLMResponseError
from aworld.logs.util import logger
from aworld.utils import import_package

# Together video job status constants
_STATUS_COMPLETED  = "completed"
_STATUS_FAILED     = "failed"
_STATUS_CANCELLED  = "cancelled"
_TERMINAL_STATUSES = {_STATUS_COMPLETED, _STATUS_FAILED, _STATUS_CANCELLED}

# Default polling configuration
_DEFAULT_POLL_INTERVAL = 5.0    # seconds between each poll
_DEFAULT_POLL_TIMEOUT  = 600.0  # max total wait time in seconds

# Map Together status strings to our canonical vocabulary
_STATUS_MAP = {
    "queued":      "queued",
    "in_progress": "processing",
    "completed":   "succeeded",
    "failed":      "failed",
    "cancelled":   "cancelled",
}

# Map label-style resolution strings to VideoResolution enums
_LABEL_TO_RESOLUTION = {
    "480p":  VideoResolution.RES_480P,
    "720p":  VideoResolution.RES_720P,
    "1080p": VideoResolution.RES_1080P,
    "4k":    VideoResolution.RES_4K,
}


def _build_video_response(job: Any, model_name: str) -> ModelResponse:
    """Convert a Together video job object into a unified ModelResponse.

    Args:
        job: Together video job object (from create or retrieve).
        model_name: Model name used for this job.

    Returns:
        ModelResponse with video_result populated.
    """
    job_id     = getattr(job, "id",     None) or ""
    status_raw = getattr(job, "status", None) or "unknown"

    video_url: Optional[str] = None
    cost: Optional[Any]      = None
    outputs = getattr(job, "outputs", None)
    if outputs:
        video_url = getattr(outputs, "video_url", None)
        cost      = getattr(outputs, "cost",      None)

    status = _STATUS_MAP.get(status_raw, status_raw)

    extra: Dict[str, Any] = {"raw_status": status_raw}
    if cost is not None:
        extra["cost"] = cost

    info = getattr(job, "info", None)
    if info:
        errors = getattr(info, "errors", None)
        if errors:
            extra["errors"] = errors

    video_result = VideoGenerationResult(
        task_id=job_id,
        video_url=video_url,
        status=status,
        extra=extra,
    )

    return ModelResponse(
        id=job_id or f"together-video-{int(time.time())}",
        model=model_name,
        video_result=video_result,
        raw_response=job,
    )


class TogetherVideoProvider(VideoGenProviderBase):
    """Video generation provider backed by Together.ai's video API.

    Inherits from :class:`~aworld.core.video_gen_provider.VideoGenProviderBase`
    and is purpose-built for video generation only.

    Preferred usage — structured request::

        from aworld.core.video_gen_provider import VideoGenerationRequest, VideoResolution
        from aworld.models.together_video_provider import TogetherVideoProvider

        provider = TogetherVideoProvider(
            api_key="YOUR_TOGETHER_API_KEY",
            model_name="minimax/video-01-director",
        )
        request = VideoGenerationRequest(
            prompt="A serene sunset over the ocean",
            resolution=VideoResolution.RES_720P,
            duration=5,
            extra_params={"poll": True},
        )
        response = provider.generate_video(request)
        print(response.video_result.video_url)

    Legacy flat-argument usage (still supported via the inherited convenience wrapper)::

        response = provider.generate_video_from_params(
            prompt="A serene sunset over the ocean",
            resolution="1366x768",   # raw "WxH" string still accepted
            duration=5,
            poll=True,
        )
    """

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_provider(self):
        """Initialise Together synchronous client."""
        try:
            from together import Together
        except ImportError:
            logger.error(
                "The 'together' package is required for TogetherVideoProvider. "
                "Install it with: pip install together"
            )
            import_package("together")
            from together import Together

        api_key = self.api_key or os.getenv("TOGETHER_API_KEY", "")
        if not api_key:
            raise ValueError(
                "Together API key not found. Set the TOGETHER_API_KEY environment "
                "variable or pass api_key to the constructor."
            )

        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        timeout = self.kwargs.get("timeout", 60)
        if timeout:
            client_kwargs["timeout"] = timeout

        return Together(**client_kwargs)

    def _init_async_provider(self):
        """Async client is not supported; always returns None."""
        return None

    @classmethod
    def supported_models(cls) -> list:
        return [
            "minimax/video-01-director",
            "minimax/hailuo-02",
            "google/veo-2.0",
            "google/veo-3.0",
            "google/veo-3.0-audio",
            "google/veo-3.0-fast",
            "google/veo-3.0-fast-audio",
            "ByteDance/Seedance-1.0-lite",
            "ByteDance/Seedance-1.0-pro",
            "pixverse/pixverse-v5",
            "kwaivgI/kling-2.1-master",
            "kwaivgI/kling-2.1-standard",
            "kwaivgI/kling-2.1-pro",
            "kwaivgI/kling-2.0-master",
            "kwaivgI/kling-1.6-standard",
            "kwaivgI/kling-1.6-pro",
            "Wan-AI/Wan2.2-I2V-A14B",
            "Wan-AI/Wan2.2-T2V-A14B",
            "vidu/vidu-2.0",
            "vidu/vidu-q1",
            "openai/sora-2",
            "openai/sora-2-pro",
        ]

    # ------------------------------------------------------------------
    # Core video generation interface
    # ------------------------------------------------------------------

    def generate_video(self,
                       request: VideoGenerationRequest,
                       context: Context = None) -> ModelResponse:
        """Submit a video generation job to Together.ai and optionally wait for completion.

        Together-specific options should be supplied via ``request.extra_params``:

        - ``poll`` (bool, default ``True``): Block until the job finishes.
        - ``poll_interval`` (float, default 5): Seconds between poll attempts.
        - ``poll_timeout`` (float, default 600): Maximum wait time in seconds.
        - ``steps`` (int): Diffusion steps.
        - ``guidance_scale`` (float): Prompt adherence strength.
        - ``output_format`` (str): ``'MP4'`` or ``'GIF'``.
        - ``output_quality`` (int): Bitrate/quality.
        - ``model_name`` (str): Override the model for this single call.

        Args:
            request: Standardised video generation request.
            context: Runtime context (unused, reserved for future use).

        Returns:
            ModelResponse with ``video_result`` populated.

        Raises:
            LLMResponseError: When the Together.ai API returns an error.
            RuntimeError: When the synchronous client is not initialised.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialised. Set 'sync_enabled=True' in the constructor."
            )

        extra = dict(request.extra_params)  # shallow copy — don't mutate caller's dict
        model = extra.pop("model_name", None) or self.model_name
        if not model:
            raise ValueError("model_name must be provided in the constructor or via extra_params.")

        poll          = extra.pop("poll", True)
        poll_interval = float(extra.pop("poll_interval", _DEFAULT_POLL_INTERVAL))
        poll_timeout  = float(extra.pop("poll_timeout",  _DEFAULT_POLL_TIMEOUT))

        create_params: Dict[str, Any] = {"model": model, "prompt": request.prompt}

        # Resolution — Together.ai expects separate width/height integers.
        # Check for a raw "WxH" string injected by generate_video_from_params first,
        # then fall back to enum-based conversion.
        raw_wh = extra.pop("_raw_wh", None)
        if raw_wh:
            try:
                parts = raw_wh.lower().split("x")
                if len(parts) == 2:
                    create_params["width"]  = int(parts[0])
                    create_params["height"] = int(parts[1])
            except (ValueError, AttributeError):
                logger.warning(f"Invalid raw resolution '{raw_wh}'; ignoring.")
        else:
            w, h = self.resolve_resolution_pixels(request.resolution, request.aspect_ratio)
            if w:
                create_params["width"] = w
            if h:
                create_params["height"] = h

        if request.duration is not None:
            create_params["seconds"] = int(request.duration)
        if request.fps is not None:
            create_params["fps"] = request.fps
        if request.seed is not None:
            create_params["seed"] = request.seed

        # negative_prompt: request field takes precedence; extra_params can override
        negative_prompt = extra.pop("negative_prompt", None) or request.negative_prompt
        if negative_prompt:
            create_params["negative_prompt"] = negative_prompt

        # Input image (first keyframe) — prefer URL, fall back to local file
        image_url = request.image_url
        if not image_url and request.image_path:
            b64 = self.read_file_as_base64(request.image_path)
            image_url = f"data:image/jpeg;base64,{b64}"
        if image_url:
            create_params["frame_images"] = [image_url]

        if request.video_url or request.video_path:
            logger.warning(
                "TogetherVideoProvider does not support video_url / video_path; "
                "these fields will be ignored."
            )

        # Forward well-known Together-specific optional parameters
        for key in ("steps", "guidance_scale", "output_format", "output_quality"):
            if key in extra and extra[key] is not None:
                create_params[key] = extra.pop(key)

        try:
            logger.info(f"Submitting Together.ai video job: model={model}, prompt={request.prompt!r}")
            job = self.provider.videos.create(**create_params)
            logger.info(f"Together.ai video job submitted: id={job.id}, status={job.status}")
        except Exception as e:
            logger.error(f"Together.ai video create error: {e}\n{traceback.format_exc()}")
            raise LLMResponseError(str(e), model)

        if not poll:
            return _build_video_response(job, model)

        return self._poll_until_done(
            task_id=job.id,
            model=model,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
        )

    async def agenerate_video(self,
                              request: VideoGenerationRequest,
                              context: Context = None) -> ModelResponse:
        """Async video generation is not supported. Use :meth:`generate_video` instead."""
        raise NotImplementedError(
            "TogetherVideoProvider does not support async video generation. "
            "Use generate_video() instead."
        )

    # ------------------------------------------------------------------
    # Task status
    # ------------------------------------------------------------------

    def get_video_task_status(self,
                              task_id: str,
                              context: Context = None,
                              **kwargs) -> ModelResponse:
        """Query the current status of a Together.ai video generation job.

        Args:
            task_id: Job ID returned by :meth:`generate_video` (when
                ``poll=False`` is set in ``extra_params``).
            context: Runtime context (unused).
            **kwargs: Optional ``model_name`` (str) used only to populate the
                response ``model`` field.

        Returns:
            ModelResponse with ``video_result`` reflecting the latest job state.

        Raises:
            LLMResponseError: When the Together.ai API returns an error.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialised. Set 'sync_enabled=True' in the constructor."
            )

        model = kwargs.get("model_name") or self.model_name or "unknown"

        try:
            job = self.provider.videos.retrieve(task_id)
            logger.debug(f"Together.ai video job status: id={task_id}, status={job.status}")
        except Exception as e:
            logger.error(f"Together.ai video retrieve error: {e}\n{traceback.format_exc()}")
            raise LLMResponseError(str(e), model)

        return _build_video_response(job, model)

    async def aget_video_task_status(self,
                                     task_id: str,
                                     context: Context = None,
                                     **kwargs) -> ModelResponse:
        """Async status query is not supported. Use :meth:`get_video_task_status` instead."""
        raise NotImplementedError(
            "TogetherVideoProvider does not support async status queries. "
            "Use get_video_task_status() instead."
        )

    # ------------------------------------------------------------------
    # Backward-compatible flat-argument wrapper
    # ------------------------------------------------------------------

    def generate_video_from_params(self,
                                   prompt: str,
                                   negative_prompt: str = None,
                                   image_url: str = None,
                                   image_path: str = None,
                                   video_url: str = None,
                                   video_path: str = None,
                                   aspect_ratio: AspectRatio = None,
                                   resolution=None,
                                   duration: float = None,
                                   fps: int = None,
                                   sample_count: int = None,
                                   seed: int = None,
                                   context: Context = None,
                                   **extra_params) -> ModelResponse:
        """Backward-compatible wrapper that accepts flat keyword arguments.

        Preserves the original call-site style used before the migration to
        :class:`VideoGenerationRequest`.  All Together-specific parameters
        (``poll``, ``poll_interval``, ``poll_timeout``, ``steps``,
        ``guidance_scale``, ``output_format``, ``output_quality``) can be
        passed as keyword arguments here.

        ``resolution`` may be:

        - A :class:`VideoResolution` enum value (preferred for new code).
        - A label string such as ``"720p"`` or ``"1080p"``.
        - A raw ``"WxH"`` pixel string such as ``"1366x768"`` (legacy; still
          accepted for backward compatibility).

        Args:
            prompt: Text description of the video.
            negative_prompt: Content the model should avoid generating.
            image_url: Reference image as an HTTP/HTTPS URL.
            image_path: Reference image as a local filesystem path.
            video_url: Reference video URL (unsupported by Together; ignored).
            video_path: Reference video path (unsupported by Together; ignored).
            aspect_ratio: Desired aspect ratio enum value.
            resolution: :class:`VideoResolution` enum, label string, or ``"WxH"`` string.
            duration: Desired video duration in seconds.
            fps: Desired frame rate.
            sample_count: Number of videos to generate (unsupported; ignored).
            seed: Random seed for reproducibility.
            context: Runtime context (optional).
            **extra_params: Additional Together-specific parameters.

        Returns:
            ModelResponse with ``video_result`` populated.
        """
        resolved_resolution: Optional[VideoResolution] = None
        raw_wh: Optional[str] = None

        if isinstance(resolution, VideoResolution):
            resolved_resolution = resolution
        elif isinstance(resolution, str):
            lower = resolution.lower()
            if lower in _LABEL_TO_RESOLUTION:
                resolved_resolution = _LABEL_TO_RESOLUTION[lower]
            else:
                # Treat as raw "WxH" pixel string (e.g. "1366x768")
                raw_wh = resolution

        if raw_wh:
            extra_params.setdefault("_raw_wh", raw_wh)

        request = VideoGenerationRequest(
            prompt=prompt,
            negative_prompt=negative_prompt,
            image_url=image_url,
            image_path=image_path,
            video_url=video_url,
            video_path=video_path,
            aspect_ratio=aspect_ratio,
            resolution=resolved_resolution,
            duration=duration,
            fps=fps,
            sample_count=sample_count,
            seed=seed,
            extra_params=extra_params,
        )
        return self.generate_video(request, context=context)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _poll_until_done(self,
                         task_id: str,
                         model: str,
                         poll_interval: float = _DEFAULT_POLL_INTERVAL,
                         poll_timeout: float = _DEFAULT_POLL_TIMEOUT) -> ModelResponse:
        """Block and poll a Together.ai video job until it reaches a terminal status.

        Args:
            task_id: Together.ai job ID.
            model: Model name, used for logging and response construction.
            poll_interval: Seconds between poll attempts.
            poll_timeout: Maximum total seconds to wait.

        Returns:
            ModelResponse representing the final job state.

        Raises:
            LLMResponseError: When the API returns an error during polling.
            TimeoutError: When poll_timeout is exceeded before the job completes.
        """
        deadline = time.monotonic() + poll_timeout
        attempt  = 0

        while True:
            attempt += 1
            try:
                job = self.provider.videos.retrieve(task_id)
            except Exception as e:
                logger.error(f"Error polling Together.ai job {task_id}: {e}")
                raise LLMResponseError(str(e), model)

            status = getattr(job, "status", None)
            logger.info(f"Together.ai video job {task_id} poll #{attempt}: status={status}")

            if status in _TERMINAL_STATUSES:
                return _build_video_response(job, model)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Together.ai video job {task_id} did not complete within "
                    f"{poll_timeout}s. Last status: {status}"
                )

            time.sleep(min(poll_interval, remaining))
