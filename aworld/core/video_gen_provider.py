import abc
import base64
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from aworld.core.context.base import Context
from aworld.models.model_response import ModelResponse


# ---------------------------------------------------------------------------
# Enumerations for standardised video parameters
# ---------------------------------------------------------------------------

class AspectRatio(str, Enum):
    """Standard aspect ratios understood by all video-generation providers.

    Sub-classes are responsible for mapping these values to the string or
    numeric format expected by their specific API.
    """
    LANDSCAPE_16_9 = "16:9"
    PORTRAIT_9_16  = "9:16"
    SQUARE_1_1     = "1:1"
    LANDSCAPE_4_3  = "4:3"
    PORTRAIT_3_4   = "3:4"


class VideoResolution(str, Enum):
    """Standard resolution presets understood by all video-generation providers.

    Sub-classes are responsible for converting these to the exact format
    required by their API (e.g. ``"720p"``, ``"1280x720"``, separate width/height
    integers, etc.).
    """
    RES_480P  = "480p"
    RES_720P  = "720p"
    RES_1080P = "1080p"
    RES_4K    = "4k"


# ---------------------------------------------------------------------------
# Request dataclass
# ---------------------------------------------------------------------------

@dataclass
class VideoGenerationRequest:
    """Standardised request object for video generation.

    All fields that are ``None`` are treated as "use provider default".

    Provider-specific parameters that have no counterpart in this schema
    should be placed in ``extra_params``; each sub-class is responsible
    for reading and forwarding them to its underlying API.
    """

    # Core prompt
    prompt: str
    negative_prompt: Optional[str] = None

    # Input media — URL-based (publicly accessible)
    image_url: Optional[str] = None
    """Reference image supplied as an HTTP/HTTPS URL (image-to-video)."""
    video_url: Optional[str] = None
    """Reference video supplied as an HTTP/HTTPS URL (video-to-video / extension)."""

    # Input media — local file paths (sub-classes encode to base64 as needed)
    image_path: Optional[str] = None
    """Reference image supplied as a local filesystem path."""
    video_path: Optional[str] = None
    """Reference video supplied as a local filesystem path."""

    # Output control
    aspect_ratio: Optional[AspectRatio] = None
    """Desired aspect ratio.  Sub-classes convert to provider-specific format."""
    resolution: Optional[VideoResolution] = None
    """Desired resolution preset.  Sub-classes convert to provider-specific format."""
    duration: Optional[float] = None
    """Desired video duration in seconds."""
    fps: Optional[int] = None
    """Desired frame rate."""
    sample_count: Optional[int] = None
    """Number of videos to generate in a single request (if supported)."""

    # Sampling / reproducibility
    seed: Optional[int] = None

    # Escape hatch for provider-specific parameters
    extra_params: Dict[str, Any] = field(default_factory=dict)
    """Provider-specific parameters with no standard counterpart.

    Keys and value types depend entirely on the target provider.  Consult
    each sub-class's documentation for the accepted values.

    Examples::

        # Google Veo
        extra_params={"generateAudio": True, "enhancePrompt": False}

        # 火山方舟 Seedance
        extra_params={"watermark": False}

        # 快手可灵
        extra_params={"cfg_scale": 0.5, "camera_control": {...}}
    """


# ---------------------------------------------------------------------------
# Resolution / aspect-ratio helper constants
# ---------------------------------------------------------------------------

#: Maps VideoResolution → (width, height) in pixels (landscape orientation).
_RESOLUTION_PIXEL_MAP: Dict[VideoResolution, tuple] = {
    VideoResolution.RES_480P:  (854,  480),
    VideoResolution.RES_720P:  (1280, 720),
    VideoResolution.RES_1080P: (1920, 1080),
    VideoResolution.RES_4K:    (3840, 2160),
}

#: Maps VideoResolution → short label string (e.g. used by Google Veo).
_RESOLUTION_LABEL_MAP: Dict[VideoResolution, str] = {
    VideoResolution.RES_480P:  "480p",
    VideoResolution.RES_720P:  "720p",
    VideoResolution.RES_1080P: "1080p",
    VideoResolution.RES_4K:    "4k",
}


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class VideoGenProviderBase(abc.ABC):
    """Base class for video-generation providers.

    This class is intentionally independent of :class:`LLMProviderBase` because
    video generation is a fundamentally different capability: it has no concept
    of chat messages, tokens, or streaming text chunks.  Providers that *only*
    do video generation (e.g. Kling, Seedance) should inherit from this class
    directly rather than from ``LLMProviderBase``.

    Lifecycle
    ---------
    1. Construct the provider (calls :meth:`_init_provider` and optionally
       :meth:`_init_async_provider`).
    2. Build a :class:`VideoGenerationRequest` or call the convenience method
       :meth:`generate_video_from_params`.
    3. Submit the request via :meth:`generate_video` / :meth:`agenerate_video`.
    4. If the task is asynchronous, poll with :meth:`get_video_task_status` /
       :meth:`aget_video_task_status` until the status reaches a terminal value.
    """

    def __init__(self,
                 api_key: str = None,
                 base_url: str = None,
                 model_name: str = None,
                 sync_enabled: bool = None,
                 async_enabled: bool = None,
                 **kwargs):
        """Initialise provider.

        Args:
            api_key: API key for the target service.
            base_url: Override the default service endpoint.
            model_name: Default model to use when none is specified per-request.
            sync_enabled: Explicitly control whether the sync client is created.
            async_enabled: Explicitly control whether the async client is created.
            **kwargs: Additional provider-specific configuration.
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.kwargs = kwargs

        self.need_sync = sync_enabled if sync_enabled is not None else async_enabled is not True
        self.need_async = async_enabled if async_enabled is not None else sync_enabled is not True

        self.provider = self._init_provider() if self.need_sync else None
        self.async_provider = self._init_async_provider() if self.need_async else None

    # ------------------------------------------------------------------
    # Initialisation hooks
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def _init_provider(self):
        """Initialise and return the synchronous client instance.

        Returns:
            A provider client object, or ``None`` if synchronous access is not
            needed.
        """

    def _init_async_provider(self):
        """Initialise and return the asynchronous client instance.

        Sub-classes that support async should override this method.

        Returns:
            An async provider client object, or ``None`` (default).
        """
        return None

    @classmethod
    def supported_models(cls) -> list:
        """Return the list of model identifiers supported by this provider."""
        return []

    # ------------------------------------------------------------------
    # Core abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def generate_video(self,
                       request: VideoGenerationRequest,
                       context: Context = None) -> ModelResponse:
        """Synchronously submit a video generation task.

        Args:
            request: Standardised generation request.
            context: Runtime context (optional).

        Returns:
            :class:`~aworld.models.model_response.ModelResponse` with
            ``video_result`` populated (at minimum ``task_id`` and ``status``).

        Raises:
            LLMResponseError: When the provider API returns an error.
            RuntimeError: When the synchronous client is not initialised.
        """

    @abc.abstractmethod
    async def agenerate_video(self,
                              request: VideoGenerationRequest,
                              context: Context = None) -> ModelResponse:
        """Asynchronously submit a video generation task.

        Args:
            request: Standardised generation request.
            context: Runtime context (optional).

        Returns:
            :class:`~aworld.models.model_response.ModelResponse` with
            ``video_result`` populated.

        Raises:
            LLMResponseError: When the provider API returns an error.
            RuntimeError: When the asynchronous client is not initialised.
        """

    @abc.abstractmethod
    def get_video_task_status(self,
                              task_id: str,
                              context: Context = None,
                              **kwargs) -> ModelResponse:
        """Synchronously query the status of a submitted video generation task.

        Video generation is typically a long-running async operation; call
        this method to poll for completion and retrieve the final video URL.

        Args:
            task_id: Task identifier returned by :meth:`generate_video`.
            context: Runtime context (optional).
            **kwargs: Additional provider-specific parameters.

        Returns:
            :class:`~aworld.models.model_response.ModelResponse` with
            ``video_result`` reflecting the current task state.

        Raises:
            LLMResponseError: When the provider API returns an error.
        """

    @abc.abstractmethod
    async def aget_video_task_status(self,
                                     task_id: str,
                                     context: Context = None,
                                     **kwargs) -> ModelResponse:
        """Asynchronously query the status of a submitted video generation task.

        Args:
            task_id: Task identifier returned by :meth:`agenerate_video`.
            context: Runtime context (optional).
            **kwargs: Additional provider-specific parameters.

        Returns:
            :class:`~aworld.models.model_response.ModelResponse` with
            ``video_result`` reflecting the current task state.

        Raises:
            LLMResponseError: When the provider API returns an error.
            RuntimeError: When the asynchronous client is not initialised.
        """

    # ------------------------------------------------------------------
    # Convenience wrapper — keeps existing call-sites intact
    # ------------------------------------------------------------------

    def generate_video_from_params(self,
                                   prompt: str,
                                   negative_prompt: str = None,
                                   image_url: str = None,
                                   image_path: str = None,
                                   video_url: str = None,
                                   video_path: str = None,
                                   aspect_ratio: AspectRatio = None,
                                   resolution: VideoResolution = None,
                                   duration: float = None,
                                   fps: int = None,
                                   sample_count: int = None,
                                   seed: int = None,
                                   context: Context = None,
                                   **extra_params) -> ModelResponse:
        """Convenience wrapper around :meth:`generate_video`.

        Accepts individual keyword arguments instead of a
        :class:`VideoGenerationRequest` object and delegates to
        :meth:`generate_video`.  All provider-specific parameters that are not
        listed explicitly can be passed as additional keyword arguments and will
        be forwarded via ``VideoGenerationRequest.extra_params``.

        This method exists solely to preserve backward-compatibility with
        existing call-sites that use the old flat-argument style.  New code
        should construct a :class:`VideoGenerationRequest` directly and call
        :meth:`generate_video`.

        Args:
            prompt: Text description of the video to generate.
            negative_prompt: Content the model should avoid generating.
            image_url: Reference image as an HTTP/HTTPS URL (image-to-video).
            image_path: Reference image as a local filesystem path.
            video_url: Reference video as an HTTP/HTTPS URL.
            video_path: Reference video as a local filesystem path.
            aspect_ratio: Desired aspect ratio enum value.
            resolution: Desired resolution enum value.
            duration: Desired video duration in seconds.
            fps: Desired frame rate.
            sample_count: Number of videos to generate.
            seed: Random seed for reproducibility.
            context: Runtime context (optional).
            **extra_params: Provider-specific parameters forwarded verbatim via
                ``VideoGenerationRequest.extra_params``.

        Returns:
            :class:`~aworld.models.model_response.ModelResponse` with
            ``video_result`` populated.
        """
        request = VideoGenerationRequest(
            prompt=prompt,
            negative_prompt=negative_prompt,
            image_url=image_url,
            image_path=image_path,
            video_url=video_url,
            video_path=video_path,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            duration=duration,
            fps=fps,
            sample_count=sample_count,
            seed=seed,
            extra_params=extra_params,
        )
        return self.generate_video(request, context=context)

    async def agenerate_video_from_params(self,
                                          prompt: str,
                                          negative_prompt: str = None,
                                          image_url: str = None,
                                          image_path: str = None,
                                          video_url: str = None,
                                          video_path: str = None,
                                          aspect_ratio: AspectRatio = None,
                                          resolution: VideoResolution = None,
                                          duration: float = None,
                                          fps: int = None,
                                          sample_count: int = None,
                                          seed: int = None,
                                          context: Context = None,
                                          **extra_params) -> ModelResponse:
        """Async convenience wrapper around :meth:`agenerate_video`.

        Mirrors :meth:`generate_video_from_params` for the asynchronous path.

        Args:
            prompt: Text description of the video to generate.
            negative_prompt: Content the model should avoid generating.
            image_url: Reference image as an HTTP/HTTPS URL.
            image_path: Reference image as a local filesystem path.
            video_url: Reference video as an HTTP/HTTPS URL.
            video_path: Reference video as a local filesystem path.
            aspect_ratio: Desired aspect ratio enum value.
            resolution: Desired resolution enum value.
            duration: Desired video duration in seconds.
            fps: Desired frame rate.
            sample_count: Number of videos to generate.
            seed: Random seed for reproducibility.
            context: Runtime context (optional).
            **extra_params: Provider-specific parameters forwarded verbatim via
                ``VideoGenerationRequest.extra_params``.

        Returns:
            :class:`~aworld.models.model_response.ModelResponse` with
            ``video_result`` populated.
        """
        request = VideoGenerationRequest(
            prompt=prompt,
            negative_prompt=negative_prompt,
            image_url=image_url,
            image_path=image_path,
            video_url=video_url,
            video_path=video_path,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            duration=duration,
            fps=fps,
            sample_count=sample_count,
            seed=seed,
            extra_params=extra_params,
        )
        return await self.agenerate_video(request, context=context)

    # ------------------------------------------------------------------
    # Utility helpers available to all sub-classes
    # ------------------------------------------------------------------

    @staticmethod
    def read_file_as_base64(path: str) -> str:
        """Read a local file and return its content as a Base64-encoded string.

        Args:
            path: Absolute or relative path to the file.

        Returns:
            Base64-encoded string of the file content.

        Raises:
            FileNotFoundError: When the file does not exist.
            IOError: When the file cannot be read.
        """
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Media file not found: {path}")
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("utf-8")

    @staticmethod
    def resolve_aspect_ratio_str(aspect_ratio: AspectRatio) -> Optional[str]:
        """Convert an :class:`AspectRatio` enum to its canonical string form.

        Args:
            aspect_ratio: Enum value, or ``None``.

        Returns:
            String such as ``"16:9"``, or ``None`` when the input is ``None``.
        """
        if aspect_ratio is None:
            return None
        return aspect_ratio.value

    @staticmethod
    def resolve_resolution_label(resolution: VideoResolution) -> Optional[str]:
        """Convert a :class:`VideoResolution` enum to a short label string.

        Suitable for providers that accept strings like ``"720p"`` or
        ``"1080p"`` (e.g. Google Veo).

        Args:
            resolution: Enum value, or ``None``.

        Returns:
            String such as ``"720p"``, or ``None`` when the input is ``None``.
        """
        if resolution is None:
            return None
        return _RESOLUTION_LABEL_MAP.get(resolution, resolution.value)

    @staticmethod
    def resolve_resolution_pixels(
            resolution: VideoResolution,
            aspect_ratio: AspectRatio = None,
    ) -> tuple:
        """Convert a :class:`VideoResolution` enum to (width, height) integers.

        The default orientation is landscape (16:9).  When *aspect_ratio* is
        ``PORTRAIT_9_16`` the width and height are swapped.

        Suitable for providers that accept separate ``width`` / ``height``
        parameters or a ``"WxH"`` string.

        Args:
            resolution: Enum value, or ``None``.
            aspect_ratio: Used to determine orientation.  ``None`` → landscape.

        Returns:
            ``(width, height)`` tuple, or ``(None, None)`` when *resolution*
            is ``None``.
        """
        if resolution is None:
            return None, None
        w, h = _RESOLUTION_PIXEL_MAP.get(resolution, (None, None))
        if w is None:
            return None, None
        if aspect_ratio in (AspectRatio.PORTRAIT_9_16, AspectRatio.PORTRAIT_3_4):
            return h, w
        return w, h

    @staticmethod
    def resolve_resolution_wh_str(
            resolution: VideoResolution,
            aspect_ratio: AspectRatio = None,
    ) -> Optional[str]:
        """Convert resolution + aspect-ratio enums to a ``"WxH"`` string.

        Suitable for providers that accept a resolution string in ``"WxH"``
        format (e.g. ``"1920x1080"`` used by 火山方舟 Seedance).

        Args:
            resolution: Enum value, or ``None``.
            aspect_ratio: Used to determine orientation.

        Returns:
            String such as ``"1280x720"``, or ``None`` when *resolution* is
            ``None``.
        """
        w, h = VideoGenProviderBase.resolve_resolution_pixels(resolution, aspect_ratio)
        if w is None:
            return None
        return f"{w}x{h}"
