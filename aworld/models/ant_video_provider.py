"""Ant (MatrixCube) unified video generation provider.

All requests are routed through the Ant gateway at ``matrixcube.alipay.com``
using a single ``POST /v1/genericCall`` endpoint.  The actual model-vendor API
is selected at runtime by matching the model name against a registry of
:class:`ModelAdapter` subclasses — one per vendor.

Extending
---------
To add support for a new vendor (e.g. Doubao, Google Veo):

1. Subclass :class:`ModelAdapter` and implement the three abstract methods.
2. Call :func:`AntVideoProvider.register_adapter` with the patterns that
   identify the new vendor's model names.

Example — adding a new vendor adapter::

    from aworld.models.ant_video_provider import ModelAdapter, AntVideoProvider

    class MyVendorAdapter(ModelAdapter):
        def build_submit_payload(self, request, model, extra):
            ...
            return is_image2video, payload

        def build_status_payload(self, task_id, model, is_image2video):
            return {"model": model, "method": "/my/status", "pathParam": {"id": task_id}}

        def parse_response(self, data, model, is_image2video=False):
            ...

        # Override response-shape hooks only when the vendor differs from Kling:
        def check_submit_response(self, body, model): ...
        def extract_submit_data(self, body): return body
        def extract_task_id(self, data): return data.get("id", "")
        def check_status_response(self, body, model): ...
        def extract_status_data(self, body): return body
        def get_status_from_data(self, data): return data.get("status", "unknown")
        def is_terminal_status(self, status_raw): return status_raw in {"succeeded", "failed"}

    AntVideoProvider.register_adapter(
        patterns=[r"^my-vendor-"],
        adapter_class=MyVendorAdapter,
    )
"""

import abc
import base64
import mimetypes
import os
import re
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

from aworld.core.context.base import Context
from aworld.core.video_gen_provider import (
    AspectRatio,
    VideoGenProviderBase,
    VideoGenerationRequest,
    VideoResolution,
)
from aworld.logs.util import logger
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import LLMResponseError, ModelResponse, VideoGenerationResult
from aworld.trace import base

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_POLL_TIMEOUT  = 600.0

# The single gateway endpoint shared by all vendors
_GENERIC_CALL_ENDPOINT = "v1/genericCall"

# Canonical status vocabulary
_STATUS_MAP = {
    "submitted":  "submitted",
    "processing": "processing",
    "succeed":    "succeeded",
    "failed":     "failed",
}

# Veo accepts only these image MIME types
_VEO_ALLOWED_IMAGE_MIMES: frozenset = frozenset({"image/jpeg", "image/png", "image/webp"})

# Image magic bytes for MIME inference (signature -> mime_type)
_IMAGE_MAGIC: Dict[bytes, str] = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


def _infer_image_mime_from_bytes(data: bytes) -> str:
    """Infer MIME type from raw image bytes using magic signatures."""
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    for sig, mime in _IMAGE_MAGIC.items():
        if sig != b"RIFF" and data.startswith(sig):
            return mime
    return "image/jpeg"  # fallback


def _parse_image_for_veo_payload(
    image_data: Optional[str],
    image_path: Optional[str],
) -> Optional[Tuple[str, str]]:
    """Parse image input into (base64_str, mime_type) for Veo bytesBase64Encoded payload.

    - data URL (data:image/xxx;base64,yyy): extract mime from prefix, base64 from body
    - raw base64: infer mime from decoded magic bytes
    - image_path: read file, infer mime from magic bytes (fallback: mimetypes from ext)
    """
    b64_str: Optional[str] = None
    mime: Optional[str] = None

    if image_data:
        s = image_data.strip()
        if s.startswith(("http://", "https://")):
            image_data = None  # fall through to image_path if available
        elif s.startswith("data:") and ";base64," in s:
            prefix, _, b64_part = s.partition(";base64,")
            b64_str = b64_part
            # data:image/jpeg;base64,  -> image/jpeg
            if prefix.lower().startswith("data:image/"):
                mime = prefix[11:].split(";")[0].strip().lower() or "image/jpeg"
            else:
                mime = "image/jpeg"
        else:
            b64_str = s
            mime = None  # infer from bytes

    if not b64_str and image_path:
        b64_str = VideoGenProviderBase.read_file_as_base64(image_path)
        mime_guess, _ = mimetypes.guess_type(image_path)
        if mime_guess and mime_guess.startswith("image/"):
            mime = mime_guess
        else:
            mime = None  # infer from bytes

    if not b64_str:
        return None

    if not mime:
        try:
            chunk = b64_str[:32]
            pad = (4 - len(chunk) % 4) % 4
            raw = base64.b64decode(chunk + "=" * pad)
            mime = _infer_image_mime_from_bytes(raw)
        except Exception:
            mime = "image/jpeg"

    # Veo only accepts image/jpeg, image/png, image/webp
    if mime not in _VEO_ALLOWED_IMAGE_MIMES:
        logger.warning(
            "[VeoAdapter] image mime %r not in allowed set %s; using image/jpeg",
            mime,
            sorted(_VEO_ALLOWED_IMAGE_MIMES),
        )
        mime = "image/jpeg"

    return (b64_str, mime)


# ---------------------------------------------------------------------------
# ModelAdapter — base class for per-vendor payload/response logic
# ---------------------------------------------------------------------------

class ModelAdapter(abc.ABC):
    """Per-vendor adapter that knows how to build payloads and parse responses.

    Each vendor has its own model-level API paths, request parameter names, and
    response field layout.  Subclasses encapsulate those differences so that
    :class:`AntVideoProvider` stays vendor-agnostic.

    Sub-classes should be registered via
    :meth:`AntVideoProvider.register_adapter`.

    Response shape contract
    -----------------------
    Different vendors wrap (or do not wrap) their response body differently:

    - **Kling**: ``{"code": 0, "data": {...}, "message": "..."}``
      The ``data`` sub-dict is passed to :meth:`parse_response`.
    - **Doubao**: The top-level body *is* the data — no ``code``/``data``
      wrapper.  ``check_submit_response`` / ``check_status_response`` should
      inspect the body directly.

    To support a new response shape, override :meth:`check_submit_response`,
    :meth:`extract_submit_data`, :meth:`check_status_response`,
    :meth:`extract_status_data`, and :meth:`get_status_from_data`.
    """

    # ------------------------------------------------------------------
    # Abstract core methods (must be implemented by every adapter)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def build_submit_payload(self,
                              request: VideoGenerationRequest,
                              model: str,
                              extra: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """Build the gateway payload for a video-generation submit request.

        Args:
            request: Standardised generation request.
            model: Resolved model name (gateway ``model`` field).
            extra: Shallow copy of ``request.extra_params`` with provider-
                control keys (poll, poll_interval, poll_timeout, model_name)
                already popped by the caller.

        Returns:
            Tuple of ``(is_image2video, payload_dict)``.  ``is_image2video``
            controls which result endpoint is used during status queries.
        """

    @abc.abstractmethod
    def build_status_payload(self,
                              task_id: str,
                              model: str,
                              is_image2video: bool) -> Dict[str, Any]:
        """Build the gateway payload for a task-status query.

        Args:
            task_id: Task identifier returned by the submit call.
            model: Model name (gateway ``model`` field).
            is_image2video: Whether the original task was image-to-video.

        Returns:
            Payload dict ready for ``POST /v1/genericCall``.
        """

    @abc.abstractmethod
    def parse_response(self,
                        data: Dict[str, Any],
                        model: str,
                        is_image2video: bool = False) -> ModelResponse:
        """Convert the vendor-specific response body into a ModelResponse.

        *data* is whatever :meth:`extract_submit_data` or
        :meth:`extract_status_data` returns for this adapter.

        Args:
            data: Vendor-specific data dict (after extraction).
            model: Model name used in this request.
            is_image2video: Whether this was an image-to-video task.

        Returns:
            ModelResponse with ``video_result`` populated.
        """

    # ------------------------------------------------------------------
    # Response-shape hooks — override when the vendor differs from Kling
    # ------------------------------------------------------------------

    def check_submit_response(self, body: Dict[str, Any], model: str) -> None:
        """Validate the raw HTTP response body for a submit call.

        The default implementation checks for a Kling-style ``code != 0``
        error envelope.  Override for vendors that use a different error
        signalling scheme (e.g. Doubao returns HTTP 4xx/5xx without a
        ``code`` field — in that case this method can be a no-op since
        :class:`LLMHTTPHandler` already raises on non-2xx HTTP status).

        Args:
            body: Parsed JSON response body.
            model: Model name, used in error messages.

        Raises:
            LLMResponseError: When the response signals a business-level error.
        """
        _check_gateway_code(body, model)

    def extract_submit_data(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the relevant data dict from a raw submit response body.

        The default implementation returns ``body["data"]`` (Kling style).
        Override for vendors whose top-level body *is* the data (e.g. Doubao).

        Args:
            body: Parsed JSON response body.

        Returns:
            Dict passed to :meth:`parse_response` and used to read the task ID.
        """
        return body.get("data") or {}

    def extract_task_id(self, data: Dict[str, Any]) -> str:
        """Extract the task identifier from the submit data dict.

        The default implementation reads ``data["task_id"]`` (Kling style).
        Override for vendors that use a different key (e.g. Doubao uses ``id``).

        Args:
            data: Data dict returned by :meth:`extract_submit_data`.

        Returns:
            Task ID string (may be empty if not yet assigned).
        """
        return data.get("task_id", "")

    def check_status_response(self, body: Dict[str, Any], model: str) -> None:
        """Validate the raw HTTP response body for a status-query call.

        Defaults to the same Kling-style ``code != 0`` check.  Override for
        other vendors.

        Args:
            body: Parsed JSON response body.
            model: Model name.

        Raises:
            LLMResponseError: When the response signals a business-level error.
        """
        _check_gateway_code(body, model)

    def extract_status_data(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the relevant data dict from a raw status-query response body.

        Defaults to ``body["data"]`` (Kling style).  Override for vendors
        whose top-level body is the data (e.g. Doubao).

        Args:
            body: Parsed JSON response body.

        Returns:
            Dict passed to :meth:`parse_response` and :meth:`get_status_from_data`.
        """
        return body.get("data") or {}

    def get_status_from_data(self, data: Dict[str, Any]) -> str:
        """Read the raw task-status string from a status-data dict.

        The default implementation reads ``data["task_status"]`` (Kling style).
        Override for vendors that use a different key (e.g. Doubao uses
        ``status``).

        Args:
            data: Data dict returned by :meth:`extract_status_data`.

        Returns:
            Raw status string (e.g. ``"processing"``, ``"succeed"``).
        """
        return data.get("task_status", "unknown")

    def is_terminal_status(self, status_raw: str) -> bool:
        """Return True when *status_raw* is a terminal (done/failed) state.

        The default recognises Kling's ``"succeed"`` and ``"failed"``.
        Override for vendors with different terminal status names.

        Args:
            status_raw: Raw status string from :meth:`get_status_from_data`.
        """
        return status_raw in {"succeed", "failed"}

    def post_process(self, response: ModelResponse, **kwargs) -> ModelResponse:
        """Optional synchronous post-processing step applied after every
        :meth:`parse_response` call.

        The default is a no-op — the response is returned unchanged.
        Override to perform side-effectful enrichment that requires information
        only available at call time (e.g. base URL, API key, expiration time).

        Args:
            response: ModelResponse produced by :meth:`parse_response`.
            **kwargs: Caller-supplied context.  Common keys:

                - ``base_url`` (str): Gateway base URL.
                - ``api_key`` (str): Bearer token for the gateway.
                - ``expiration_time`` (int): Signed-URL expiry in days.

        Returns:
            The same (possibly mutated) ModelResponse.
        """
        return response

    async def apost_process(self, response: ModelResponse, **kwargs) -> ModelResponse:
        """Optional asynchronous post-processing step.

        The default delegates to the synchronous :meth:`post_process`.
        Override when the enrichment step is genuinely async
        (e.g. an HTTP call to resolve a GCS URI).

        Args:
            response: ModelResponse produced by :meth:`parse_response`.
            **kwargs: Same as :meth:`post_process`.

        Returns:
            The same (possibly mutated) ModelResponse.
        """
        return self.post_process(response, **kwargs)


# ---------------------------------------------------------------------------
# KlingAdapter — 可灵 (Kling) vendor implementation
# ---------------------------------------------------------------------------

# Kling model-level API paths
_KLING_METHOD_TEXT2VIDEO        = "/v1/videos/text2video"
_KLING_METHOD_IMAGE2VIDEO        = "/v1/videos/image2video"
_KLING_METHOD_TEXT2VIDEO_RESULT = "/v1/videos/text2video/result"
_KLING_METHOD_IMAGE2VIDEO_RESULT = "/v1/videos/image2video/result"

_KLING_ASPECT_RATIO_MAP: Dict[AspectRatio, str] = {
    AspectRatio.LANDSCAPE_16_9: "16:9",
    AspectRatio.PORTRAIT_9_16:  "9:16",
    AspectRatio.SQUARE_1_1:     "1:1",
}

_KLING_RESOLUTION_MAP: Dict[VideoResolution, str] = {
    VideoResolution.RES_720P:  "720p",
    VideoResolution.RES_1080P: "1080p",
}


class KlingAdapter(ModelAdapter):
    """Adapter for 可灵 (Kling) video models.

    Supported models: ``kling-v1``, ``kling-v1-5``, ``kling-v2``, ``kling-v2-6``.

    Extra params recognised in ``request.extra_params``:

    - ``mode`` (str): ``"std"`` (default) or ``"pro"``.
    - ``negative_prompt`` (str): Alias for ``request.negative_prompt``.
    - ``cfg_scale`` (float): Prompt adherence strength (0–1).
    - ``camera_control`` (dict): Camera movement spec.
    """

    def build_submit_payload(self,
                              request: VideoGenerationRequest,
                              model: str,
                              extra: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:

        image = self._parse_image_input(request.image_url, request.image_path)
        image_tail = self._parse_image_input(extra.pop("image_tail", None), extra.pop("image_tail_path", None))

        is_image2video = image is not None or image_tail is not None

        payload: Dict[str, Any] = {
            "model":  model,
            "method": _KLING_METHOD_IMAGE2VIDEO if is_image2video else _KLING_METHOD_TEXT2VIDEO,
            "prompt": request.prompt,
            "mode":   extra.pop("mode", "std"),
            "sound":  extra.pop("sound", None),
        }

        if is_image2video:
            payload["image"] = image
            payload["image_tail"] = image_tail

        negative_prompt = extra.pop("negative_prompt", None) or request.negative_prompt
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        if request.duration is not None:
            payload["duration"] = int(request.duration)

        if request.aspect_ratio is not None:
            ar_str = _KLING_ASPECT_RATIO_MAP.get(request.aspect_ratio)
            if ar_str:
                payload["aspect_ratio"] = ar_str
            else:
                logger.warning(f"[KlingAdapter] AspectRatio {request.aspect_ratio} has no mapping; skipping.")

        if request.resolution is not None:
            res_str = _KLING_RESOLUTION_MAP.get(request.resolution)
            if res_str:
                payload["resolution"] = res_str
            else:
                logger.warning(f"[KlingAdapter] VideoResolution {request.resolution} has no mapping; skipping.")

        if request.seed is not None:
            payload["seed"] = request.seed

        for key in ("cfg_scale", "camera_control"):
            if key in extra and extra[key] is not None:
                payload[key] = extra.pop(key)

        if request.video_url or request.video_path:
            logger.warning("[KlingAdapter] video_url / video_path are not supported; ignoring.")

        return is_image2video, payload

    def build_status_payload(self,
                              task_id: str,
                              model: str,
                              is_image2video: bool) -> Dict[str, Any]:
        method = _KLING_METHOD_IMAGE2VIDEO_RESULT if is_image2video else _KLING_METHOD_TEXT2VIDEO_RESULT
        return {
            "model":     model,
            "method":    method,
            "pathParam": {"id": task_id},
        }

    def parse_response(self,
                        data: Dict[str, Any],
                        model: str,
                        is_image2video: bool = False) -> ModelResponse:
        task_id    = data.get("task_id", "")
        status_raw = data.get("task_status", "unknown")
        status     = _STATUS_MAP.get(status_raw, status_raw)

        if status == "failed":
            logger.error(f"[KlingAdapter] Task {task_id} failed: {data}")

        video_url: Optional[str]   = None
        duration:  Optional[float] = None
        video_list: List[Dict]     = []

        task_result = data.get("task_result") or {}
        videos      = task_result.get("videos") or []
        if videos:
            first     = videos[0]
            video_url = first.get("url")
            raw_dur   = first.get("duration")
            try:
                duration = float(raw_dur) if raw_dur is not None else None
            except (TypeError, ValueError):
                duration = None
            video_list = videos

        extra: Dict[str, Any] = {
            "raw_status":      status_raw,
            "task_status_msg": data.get("task_status_msg", ""),
            "created_at":      data.get("created_at"),
            "updated_at":      data.get("updated_at"),
            "is_image2video":  is_image2video,
            "adapter":         "kling",
        }
        if len(video_list) > 1:
            extra["all_videos"] = video_list

        return ModelResponse(
            id=task_id or f"ant-video-{int(time.time())}",
            model=model,
            video_result=VideoGenerationResult(
                task_id=task_id,
                video_url=video_url,
                status=status,
                duration=duration,
                extra=extra,
            ),
            raw_response=data,
        )

    def _parse_image_input(self, image_url:str = None, image_path:str = None) -> Optional[str]:
        # Resolve image input.
        # Kling API accepts either a plain HTTP/HTTPS URL or a raw Base64 string
        # (no "data:image/...;base64," prefix allowed).
        _SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
        image_data: Optional[str] = None

        if image_url:
            url = image_url.strip()
            if url.startswith("data:"):
                # Strip the "data:image/xxx;base64," prefix and keep only the raw Base64 payload.
                if ";base64," in url:
                    image_data = url.split(";base64,", 1)[1]
                else:
                    logger.warning(
                        "[KlingAdapter] image_url starts with 'data:' but has no ';base64,' separator; using as-is.")
                    image_data = url
            else:
                # Plain HTTP/HTTPS URL — pass through directly.
                image_data = url

        if not image_data and image_path:
            import os as _os
            ext = _os.path.splitext(image_path)[1].lower()
            if ext not in _SUPPORTED_IMAGE_EXTS:
                logger.warning(
                    f"[KlingAdapter] image_path has unsupported extension '{ext}'; "
                    f"Kling only accepts {sorted(_SUPPORTED_IMAGE_EXTS)}. Proceeding anyway."
                )
            image_data = VideoGenProviderBase.read_file_as_base64(image_path)

        return image_data


# ---------------------------------------------------------------------------
# DoubaoAdapter — 豆包 (Doubao / Seedance) vendor implementation
# ---------------------------------------------------------------------------

# Doubao model-level API paths (forwarded via the ``method`` field)
# Newer Seedance task creation uses the same task collection endpoint as status
# queries; the task id is appended through ``pathParam`` only when polling.
_DOUBAO_METHOD_SUBMIT = "/contents/generations/tasks"
_DOUBAO_METHOD_STATUS = "/contents/generations/tasks"

# Doubao aspect-ratio inline flag format: appended to the prompt text
_DOUBAO_ASPECT_RATIO_MAP: Dict[AspectRatio, str] = {
    AspectRatio.LANDSCAPE_16_9: "16:9",
    AspectRatio.PORTRAIT_9_16:  "9:16",
    AspectRatio.SQUARE_1_1:     "1:1",
    AspectRatio.LANDSCAPE_4_3:  "4:3",
    AspectRatio.PORTRAIT_3_4:   "3:4",
}

# Doubao terminal task statuses
_DOUBAO_TERMINAL_STATUSES = {"succeeded", "failed"}

# Canonical status map for Doubao
_DOUBAO_STATUS_MAP = {
    "running":   "processing",
    "succeeded": "succeeded",
    "failed":    "failed",
}


class DoubaoAdapter(ModelAdapter):
    """Adapter for 豆包 / Seedance video models via the Ant MatrixCube gateway.

    Supported models: ``doubao-seedance-1-5-pro-251215``,
    ``doubao-seedance-1-0-pro-250528``, etc.

    API contract differences from Kling
    ------------------------------------
    - Submit: ``method = "/contents/task/generate"``.  Parameters are carried
      in a ``content`` list (OpenAI message format).  Aspect ratio is embedded
      inline in the ``text`` field as ``--ratio 16:9``.
    - Submit response: **no ``code``/``data`` wrapper** — the top-level body
      is ``{"id": "<task_id>", "request_id": "..."}``.
    - Status: ``method = "/contents/generations/tasks"`` with
      ``pathParam.id = <task_id>``.
    - Status response: top-level body with ``status`` field (``running`` /
      ``succeeded`` / ``failed``).  Video URL lives in
      ``body["content"]["video_url"]``.
    - Terminal statuses: ``"succeeded"`` and ``"failed"`` (not ``"succeed"``).

    Extra params recognised in ``request.extra_params``
    ---------------------------------------------------
    - ``generate_audio`` (bool, default ``True``): Whether to generate audio.
    - ``resolution`` (str): Override resolution string, e.g. ``"720p"``.
      If not set, falls back to the ``request.resolution`` enum mapping.
    - ``ratio`` (str): Override aspect-ratio string, e.g. ``"16:9"``.
      If not set, falls back to ``request.aspect_ratio`` enum mapping.
    - ``seed`` (int): Random seed for reproducibility.
    - ``duration`` (int): Video length in seconds.  Falls back to
      ``request.duration``.
    """

    # ------------------------------------------------------------------
    # Core payload / response methods
    # ------------------------------------------------------------------

    def build_submit_payload(self,
                              request: VideoGenerationRequest,
                              model: str,
                              extra: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        prompt_text = (request.prompt or "").strip()

        ratio_str = (
            extra.pop("ratio", None)
            or extra.pop("aspect_ratio", None)
            or (_DOUBAO_ASPECT_RATIO_MAP.get(request.aspect_ratio) if request.aspect_ratio else None)
        )

        resolution_str = extra.pop("resolution", None)
        if not resolution_str and request.resolution is not None:
            _res_map = {
                VideoResolution.RES_480P: "480p",
                VideoResolution.RES_720P: "720p",
                VideoResolution.RES_1080P: "1080p",
                VideoResolution.RES_4K: "4k",
            }
            resolution_str = _res_map.get(request.resolution)

        duration = extra.pop("duration", None)
        if duration is None and request.duration is not None:
            duration = int(request.duration)

        seed = extra.pop("seed", None)
        if seed is None and request.seed is not None:
            seed = request.seed

        # Image input (image-to-video). The Seedance docs use ``image_url`` as the
        # public request field name, but we still accept local files by converting
        # them to a data URL so older call sites continue to work.
        is_image2video = False
        image_data: Optional[str] = request.image_url
        if not image_data and request.image_path:
            b64 = VideoGenProviderBase.read_file_as_base64(request.image_path)
            image_data = f"data:image/jpeg;base64,{b64}"
        if image_data:
            is_image2video = True

        payload: Dict[str, Any] = {
            "model": model,
            "method": _DOUBAO_METHOD_SUBMIT,
            "prompt": prompt_text,
        }

        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if image_data:
            payload["image_url"] = image_data
        if ratio_str:
            payload["ratio"] = ratio_str
        if resolution_str:
            payload["resolution"] = resolution_str
        if duration is not None:
            payload["duration"] = duration
        if seed is not None:
            payload["seed"] = seed

        # Optional vendor-specific fields documented by Seedance / commonly used
        # by gateway callers. We forward only when explicitly provided.
        passthrough_keys = (
            "watermark",
            "generate_audio",
            "fps",
            "camera_fixed",
            "template_id",
            "user_prompt",
        )
        for key in passthrough_keys:
            value = extra.pop(key, None)
            if value is not None:
                payload[key] = value

        # Keep backward compatibility with older code paths that expected an
        # OpenAI-style ``content`` list. The newer structured fields above are the
        # source of truth for Seedance, while ``content`` helps older gateways.
        content_items: List[Dict[str, Any]] = [{"type": "text", "text": prompt_text}]
        if image_data:
            content_items.append({"type": "image_url", "image_url": {"url": image_data}})
        payload["content"] = content_items

        if request.video_url or request.video_path:
            logger.warning("[DoubaoAdapter] video_url / video_path are not supported; ignoring.")

        return is_image2video, payload

    def build_status_payload(self,
                              task_id: str,
                              model: str,
                              is_image2video: bool) -> Dict[str, Any]:
        return {
            "model":     model,
            "method":    _DOUBAO_METHOD_STATUS,
            "pathParam": {"id": task_id},
        }

    def parse_response(self,
                        data: Dict[str, Any],
                        model: str,
                        is_image2video: bool = False) -> ModelResponse:
        # ``data`` here is the full response body (Doubao commonly has no
        # ``data`` wrapper), but we still accept wrapped variants for safety.
        if isinstance(data.get("data"), dict):
            data = data["data"]

        task_id = data.get("id") or data.get("task_id") or data.get("taskId") or ""
        status_raw = data.get("status") or data.get("task_status") or data.get("state") or "unknown"
        status     = _DOUBAO_STATUS_MAP.get(status_raw, status_raw)

        video_url: Optional[str]   = None
        duration:  Optional[float] = None

        content = data.get("content") or {}
        output = data.get("output") or {}
        result = data.get("result") or {}

        if isinstance(content, dict):
            video_url = (
                content.get("video_url")
                or content.get("url")
                or (content.get("video_urls") or [None])[0]
            )
        if not video_url and isinstance(output, dict):
            video_url = (
                output.get("video_url")
                or output.get("url")
                or (output.get("video_urls") or [None])[0]
            )
        if not video_url and isinstance(result, dict):
            video_url = (
                result.get("video_url")
                or result.get("url")
                or (result.get("video_urls") or [None])[0]
            )
        if not video_url:
            video_url = data.get("video_url") or data.get("url")

        raw_dur = (
            data.get("duration")
            or (data.get("usage") or {}).get("video_duration")
            or output.get("duration")
            or result.get("duration")
        )
        try:
            duration = float(raw_dur) if raw_dur is not None else None
        except (TypeError, ValueError):
            duration = None

        extra_out: Dict[str, Any] = {
            "raw_status":    status_raw,
            "created_at":    data.get("created_at"),
            "updated_at":    data.get("updated_at"),
            "resolution":    data.get("resolution") or output.get("resolution") or result.get("resolution"),
            "ratio":         data.get("ratio") or data.get("aspect_ratio"),
            "seed":          data.get("seed"),
            "framespersecond": data.get("framespersecond"),
            "usage":         data.get("usage"),
            "is_image2video": is_image2video,
            "adapter":       "doubao",
        }

        return ModelResponse(
            id=task_id or f"ant-video-{int(time.time())}",
            model=model,
            video_result=VideoGenerationResult(
                task_id=task_id,
                video_url=video_url,
                status=status,
                duration=duration,
                extra=extra_out,
            ),
            raw_response=data,
        )

    # ------------------------------------------------------------------
    # Response-shape overrides
    # ------------------------------------------------------------------

    def check_submit_response(self, body: Dict[str, Any], model: str) -> None:
        code = body.get("code")
        if code not in (None, 0, "0", 200, "200"):
            raise LLMResponseError(
                body.get("message") or body.get("msg") or f"Doubao submit failed: {body}",
                model,
                "doubao",
                body,
            )

    def extract_submit_data(self, body: Dict[str, Any]) -> Dict[str, Any]:
        # The top-level response body usually IS the data (no "data" wrapper),
        # but some gateways still add a standard ``data`` envelope.
        return body.get("data") if isinstance(body.get("data"), dict) else body

    def extract_task_id(self, data: Dict[str, Any]) -> str:
        return data.get("id") or data.get("task_id") or data.get("taskId") or ""

    def check_status_response(self, body: Dict[str, Any], model: str) -> None:
        code = body.get("code")
        if code not in (None, 0, "0", 200, "200"):
            raise LLMResponseError(
                body.get("message") or body.get("msg") or f"Doubao status failed: {body}",
                model,
                "doubao",
                body,
            )

    def extract_status_data(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return body.get("data") if isinstance(body.get("data"), dict) else body

    def get_status_from_data(self, data: Dict[str, Any]) -> str:
        return data.get("status") or data.get("task_status") or data.get("state") or "unknown"

    def is_terminal_status(self, status_raw: str) -> bool:
        return status_raw in _DOUBAO_TERMINAL_STATUSES


# WanX model-level API paths
_WANX_METHOD_SUBMIT = "/aigc/image2video/video-synthesis"
_WANX_METHOD_STATUS = "/tasks"

# WanX resolution mapping
_WANX_RESOLUTION_MAP: Dict[VideoResolution, str] = {
    VideoResolution.RES_480P: "480P",
    VideoResolution.RES_720P: "720P",
    VideoResolution.RES_1080P: "1080P",
}

# WanX terminal statuses
_WANX_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED"}

# Canonical status map for WanX
_WANX_STATUS_MAP = {
    "PENDING": "submitted",
    "RUNNING": "processing",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
}


class WanXAdapter(ModelAdapter):
    """Adapter for WanX (万相) video models via the Ant MatrixCube gateway.

    Supported models: ``wanx2.1-kf2v-plus``, etc.

    API contract differences from Kling
    ------------------------------------
    - Submit: ``method = "/aigc/image2video/video-synthesis"``.
      Parameters are carried in ``input`` and ``parameters`` dicts.
      Requires ``first_frame_url`` and optionally ``last_frame_url`` for image2video.
    - Submit response: wrapped in ``output`` field —
      ``{"output": {"task_id": "...", "task_status": "PENDING"}}``
    - Status: ``method = "/tasks"`` with ``pathParam.task_id = <task_id>``.
    - Status response: wrapped in ``output`` field with ``task_status``
      (``PENDING``, ``RUNNING``, ``SUCCEEDED``, ``FAILED``).
      Video URL lives in ``output.video_url``.
    - Terminal statuses: ``"SUCCEEDED"`` and ``"FAILED"``.

    Extra params recognised in ``request.extra_params``
    ---------------------------------------------------
    - ``first_frame_url`` (str): URL or base64 of the first frame image.
    - ``last_frame_url`` (str): URL or base64 of the last frame image.
    - ``prompt_extend`` (bool): Whether to extend the prompt.
    - ``resolution`` (str): Override resolution string, e.g. ``"480P"``.
      If not set, falls back to the ``request.resolution`` enum mapping.
    """

    def build_submit_payload(
        self,
        request: VideoGenerationRequest,
        model: str,
        extra: Dict[str, Any],
    ) -> Tuple[bool, Dict[str, Any]]:
        # WanX is always image2video (requires first_frame)
        is_image2video = True

        # Build input dict with first_frame and optional last_frame
        input_dict: Dict[str, Any] = {}

        # Handle first frame (required for WanX)
        first_frame = extra.pop("first_frame_url", None) or request.image_url
        if not first_frame and request.image_path:
            first_frame = VideoGenProviderBase.read_file_as_base64(request.image_path)
            # WanX accepts base64 with data URI prefix
            if first_frame and not first_frame.startswith("data:"):
                first_frame = f"data:image/jpeg;base64,{first_frame}"

        if first_frame:
            input_dict["first_frame_url"] = first_frame

        # Handle last frame (optional)
        last_frame = extra.pop("last_frame_url", None)
        if last_frame:
            input_dict["last_frame_url"] = last_frame

        # Add prompt if provided
        if request.prompt:
            input_dict["prompt"] = request.prompt

        # Build parameters dict
        parameters: Dict[str, Any] = {}

        # Resolution: extra override → enum mapping
        resolution_str = extra.pop("resolution", None)
        if not resolution_str and request.resolution is not None:
            resolution_str = _WANX_RESOLUTION_MAP.get(request.resolution)
        if resolution_str:
            parameters["resolution"] = resolution_str

        # Prompt extend
        prompt_extend = extra.pop("prompt_extend", None)
        if prompt_extend is not None:
            parameters["prompt_extend"] = bool(prompt_extend)

        payload: Dict[str, Any] = {
            "model": model,
            "method": _WANX_METHOD_SUBMIT,
            "input": input_dict,
        }

        if parameters:
            payload["parameters"] = parameters

        if request.video_url or request.video_path:
            logger.warning("[WanXAdapter] video_url / video_path are not supported; ignoring.")

        return is_image2video, payload

    def build_status_payload(
        self,
        task_id: str,
        model: str,
        is_image2video: bool,
    ) -> Dict[str, Any]:
        return {
            "model": model,
            "method": _WANX_METHOD_STATUS,
            "pathParam": {"task_id": task_id},
        }

    def parse_response(
        self,
        data: Dict[str, Any],
        model: str,
        is_image2video: bool = False,
    ) -> ModelResponse:
        # ``data`` here is the extracted ``output`` dict from the response
        task_id = data.get("task_id", "")
        status_raw = data.get("task_status", "unknown")
        status = _WANX_STATUS_MAP.get(status_raw, status_raw.lower())

        if status == "failed":
            logger.error(f"[WanXAdapter] Task {task_id} failed: {data}")

        video_url: Optional[str] = None
        duration: Optional[float] = None

        # Video URL is directly in data for WanX
        video_url = data.get("video_url")

        # Try to get duration from usage info if available
        usage = data.get("usage") or {}
        if usage:
            raw_dur = usage.get("video_duration")
            try:
                duration = float(raw_dur) if raw_dur is not None else None
            except (TypeError, ValueError):
                duration = None

        extra_out: Dict[str, Any] = {
            "raw_status": status_raw,
            "orig_prompt": data.get("orig_prompt"),
            "actual_prompt": data.get("actual_prompt"),
            "submit_time": data.get("submit_time"),
            "scheduled_time": data.get("scheduled_time"),
            "end_time": data.get("end_time"),
            "usage": usage,
            "is_image2video": is_image2video,
            "adapter": "wanx",
        }

        return ModelResponse(
            id=task_id or f"ant-video-{int(time.time())}",
            model=model,
            video_result=VideoGenerationResult(
                task_id=task_id,
                video_url=video_url,
                status=status,
                duration=duration,
                extra=extra_out,
            ),
            raw_response=data,
        )

    # ------------------------------------------------------------------
    # Response-shape overrides (WanX uses ``output`` wrapper like Kling)
    # ------------------------------------------------------------------

    def extract_submit_data(self, body: Dict[str, Any]) -> Dict[str, Any]:
        # WanX wraps response data in ``output`` field
        return body.get("output") or {}

    def extract_status_data(self, body: Dict[str, Any]) -> Dict[str, Any]:
        # WanX wraps response data in ``output`` field
        return body.get("output") or {}

    def is_terminal_status(self, status_raw: str) -> bool:
        return status_raw in _WANX_TERMINAL_STATUSES

# ---------------------------------------------------------------------------
# VeoAdapter — Google Veo vendor implementation
# ---------------------------------------------------------------------------

# Default GCS storage bucket used when storageUri is not overridden
_VEO_DEFAULT_STORAGE_URI = "gs://antgroup_matrix_storage/output"

# Endpoint for converting a GCS URI to a signed HTTPS URL (GET request)
_VEO_GCS_URL_ENDPOINT = "v1/objects/getUrlFromGcs"

# Veo terminal states: done=True means succeeded, done=False (or absent) means running
# A response with done=True but an error field means failed
_VEO_TERMINAL_DONE    = "done"
_VEO_STATUS_RUNNING   = "running"
_VEO_STATUS_SUCCEEDED = "succeeded"
_VEO_STATUS_FAILED    = "failed"


class VeoAdapter(ModelAdapter):
    """Adapter for Google Veo video models via the Ant MatrixCube gateway.

    Supported models: ``veo-3.0-generate-001``, ``veo-3.1-generate-preview``,
    ``veo-3.1-fast-generate-preview``.

    API contract differences from Kling
    ------------------------------------
    - Submit ``method`` is ``/{model_name}:predictLongRunning`` (model-specific).
    - Request body uses ``instances`` list and ``parameters`` dict instead of
      flat fields.
    - Submit response: **no ``code``/``data`` wrapper** — body is
      ``{"name": "<operation_name>", "request_id": "..."}``.
      The task identifier is the ``name`` field (a GCP operation path).
    - Status ``method`` is ``/{model_name}:fetchPredictOperation``; the task ID
      is passed as top-level ``operationName`` (not ``pathParam``).
    - Status response: ``{"name": "...", "done": true/false, "response": {...}}``.
      Completion is signalled by ``done: true``; there is no ``status`` field.
    - Video is returned as a GCS URI (``response.videos[].gcsUri``).  A
      separate GET call to ``/v1/objects/getUrlFromGcs`` converts it to a
      signed HTTPS URL.

    Extra params recognised in ``request.extra_params``
    ---------------------------------------------------
    - ``sample_count`` (int, default ``1``): Number of videos to generate.
    - ``storage_uri`` (str): Override the GCS storage URI. When omitted the
      default bucket ``gs://antgroup_matrix_storage/output`` is used; when
      explicitly set to ``None`` or ``""`` the field is omitted entirely so the
      gateway returns base64 instead.
    - ``expiration_time`` (int, default ``7``): Signed-URL expiry in days,
      passed to ``/v1/objects/getUrlFromGcs``.
    - ``aspect_ratio`` (str): Veo aspect-ratio string, e.g. ``"16:9"``.
      Falls back to ``request.aspect_ratio`` enum mapping.
    - ``resolution`` (str): Override resolution string, e.g. ``"1280x720"``.
      Falls back to ``request.resolution`` enum mapping.
    - ``sound`` (str): Audio track URL or path for video generation.
    - ``image_list`` (list): List of image dictionaries for multi-image video
      generation. Format: ``[{"image": "url_or_path"}, {"image": "url_or_path"}, ...]``.
      The first image becomes the main image, additional images are stored in
      ``additionalImages`` if the model supports it.
    """

    # Veo aspect-ratio strings
    _ASPECT_RATIO_MAP: Dict[AspectRatio, str] = {
        AspectRatio.LANDSCAPE_16_9: "16:9",
        AspectRatio.PORTRAIT_9_16:  "9:16",
        AspectRatio.SQUARE_1_1:     "1:1",
        AspectRatio.LANDSCAPE_4_3:  "4:3",
        AspectRatio.PORTRAIT_3_4:   "3:4",
    }

    # Veo resolution strings (width x height)
    _RESOLUTION_MAP: Dict[VideoResolution, str] = {
        VideoResolution.RES_480P:  "480p",
        VideoResolution.RES_720P:  "720p",
        VideoResolution.RES_1080P: "1080p",
    }

    # ------------------------------------------------------------------
    # Core payload / response methods
    # ------------------------------------------------------------------

    def build_submit_payload(self,
                              request: VideoGenerationRequest,
                              model: str,
                              extra: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        # method is model-specific
        submit_method = f"/{model}:predictLongRunning"

        instance: Dict[str, Any] = {"prompt": request.prompt or ""}

        # Image-to-video: Veo accepts an image in the instance (base64 + mimeType)
        is_image2video = False
        parsed = _parse_image_for_veo_payload(request.image_url, request.image_path)
        if parsed:
            b64_str, mime_type = parsed
            is_image2video = True
            instance["image"] = {"bytesBase64Encoded": b64_str, "mimeType": mime_type}

        parameters: Dict[str, Any] = {}

        # Duration: extra override → request field
        duration = extra.pop("duration", None)
        if duration is None and request.duration is not None:
            duration = int(request.duration)
        if duration is not None:
            parameters["durationSeconds"] = duration

        # Storage URI: use default unless caller overrides
        # Pass storage_uri=None or "" to omit the field (gateway returns base64)
        raw_storage_uri = extra.pop("storage_uri", _VEO_DEFAULT_STORAGE_URI)
        if raw_storage_uri:
            parameters["storageUri"] = raw_storage_uri

        # Sample count (number of videos)
        sample_count = extra.pop("sample_count", 1)
        parameters["sampleCount"] = int(sample_count)

        # Aspect ratio: extra string override → enum mapping
        aspect_ratio_str = extra.pop("aspect_ratio", None)
        if not aspect_ratio_str and request.aspect_ratio is not None:
            aspect_ratio_str = self._ASPECT_RATIO_MAP.get(request.aspect_ratio)
        if aspect_ratio_str:
            parameters["aspectRatio"] = aspect_ratio_str

        # Resolution: extra string override → enum mapping
        resolution_str = extra.pop("resolution", None)
        if not resolution_str and request.resolution is not None:
            resolution_str = self._RESOLUTION_MAP.get(request.resolution)
        if resolution_str:
            parameters["resolution"] = resolution_str

        if request.seed is not None:
            parameters["seed"] = request.seed

        # Sound: audio track for video generation
        sound = extra.pop("sound", None)
        if sound:
            parameters["sound"] = sound

        if request.video_url or request.video_path:
            logger.warning("[VeoAdapter] video_url / video_path are not supported; ignoring.")

        payload: Dict[str, Any] = {
            "model":      model,
            "method":     submit_method,
            "instances":  [instance],
            "parameters": parameters,
        }

        return is_image2video, payload

    def build_status_payload(self,
                              task_id: str,
                              model: str,
                              is_image2video: bool) -> Dict[str, Any]:
        # Status method is also model-specific; task_id IS the full operation name
        return {
            "model":         model,
            "method":        f"/{model}:fetchPredictOperation",
            "operationName": task_id,
        }

    def parse_response(self,
                        data: Dict[str, Any],
                        model: str,
                        is_image2video: bool = False) -> ModelResponse:
        # ``data`` is the full response body (no ``data`` wrapper)
        operation_name = data.get("name", "")
        done           = data.get("done", False)
        error          = data.get("error")

        if error:
            status = _VEO_STATUS_FAILED
        elif done:
            status = _VEO_STATUS_SUCCEEDED
        else:
            status = _VEO_STATUS_RUNNING

        video_url: Optional[str] = None
        gcs_uri:   Optional[str] = None

        response_body = data.get("response") or {}
        videos        = response_body.get("videos") or []
        if videos:
            gcs_uri = videos[0].get("gcsUri")
            # gcs_uri stored in extra; callers can resolve it via get_signed_url()
            # The signed URL is NOT resolved here to keep parse_response side-effect-free.

        extra_out: Dict[str, Any] = {
            "operation_name":          operation_name,
            "done":                    done,
            "gcs_uri":                 gcs_uri,
            "all_videos":              videos,
            "rai_media_filtered_count": response_body.get("raiMediaFilteredCount"),
            "error":                   error,
            "is_image2video":          is_image2video,
            "adapter":                 "veo",
        }

        return ModelResponse(
            id=operation_name or f"ant-video-{int(time.time())}",
            model=model,
            video_result=VideoGenerationResult(
                task_id=operation_name,
                video_url=video_url,
                status=status,
                duration=None,
                extra=extra_out,
            ),
            raw_response=data,
        )

    # ------------------------------------------------------------------
    # Response-shape overrides
    # ------------------------------------------------------------------

    def check_submit_response(self, body: Dict[str, Any], model: str) -> None:
        # Veo errors surface as HTTP 4xx/5xx (raised by LLMHTTPHandler) or as
        # an "error" field in the response body.
        error = body.get("error")
        if error:
            msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise LLMResponseError(f"Veo submit error: {msg}", model, body)

    def extract_submit_data(self, body: Dict[str, Any]) -> Dict[str, Any]:
        # Top-level body IS the data
        return body

    def extract_task_id(self, data: Dict[str, Any]) -> str:
        # Veo uses "name" (the full GCP operation path) as the task identifier
        return data.get("name", "")

    def check_status_response(self, body: Dict[str, Any], model: str) -> None:
        error = body.get("error")
        if error:
            msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise LLMResponseError(f"Veo status error: {msg}", model, body)

    def extract_status_data(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return body

    def get_status_from_data(self, data: Dict[str, Any]) -> str:
        # Veo has no ``status`` field; derive it from ``done`` + ``error``
        if data.get("error"):
            return _VEO_STATUS_FAILED
        if data.get("done"):
            return _VEO_STATUS_SUCCEEDED
        return _VEO_STATUS_RUNNING

    def is_terminal_status(self, status_raw: str) -> bool:
        return status_raw in {_VEO_STATUS_SUCCEEDED, _VEO_STATUS_FAILED}

    def post_process(self, response: ModelResponse, **kwargs) -> ModelResponse:
        """Resolve the GCS URI in *response* to a signed HTTPS URL (synchronous).

        If ``video_result.extra["gcs_uri"]`` is present and
        ``video_result.video_url`` is not yet set, calls
        ``GET /v1/objects/getUrlFromGcs`` on the Ant gateway to obtain a
        time-limited download URL and writes it back to ``video_url``.

        Args:
            response: ModelResponse to enrich in-place.
            **kwargs:
                - ``base_url`` (str): Gateway base URL (required).
                - ``api_key`` (str): Bearer token (required).
                - ``expiration_time`` (int, default 7): Signed-URL expiry in days.

        Returns:
            The same ModelResponse (possibly mutated).
        """
        vr = response.video_result if response else None
        if not vr:
            return response
        gcs_uri = (vr.extra or {}).get("gcs_uri")
        if not gcs_uri or vr.video_url:
            return response

        base_url        = kwargs.get("base_url", "")
        api_key         = kwargs.get("api_key", "")
        expiration_time = int(kwargs.get("expiration_time", 7))

        if not base_url or not api_key:
            logger.warning(
                "[VeoAdapter] post_process: base_url and api_key are required to "
                "resolve GCS URI; skipping signed URL resolution."
            )
            return response

        try:
            signed_url   = VeoAdapter.get_signed_url(base_url, api_key, gcs_uri, expiration_time)
            vr.video_url = signed_url
            logger.info(f"[VeoAdapter] Resolved GCS URI to signed URL: {signed_url[:80]}...")
        except Exception as e:
            logger.warning(f"[VeoAdapter] Failed to resolve GCS URI to signed URL: {e}")
        return response

    async def apost_process(self, response: ModelResponse, **kwargs) -> ModelResponse:
        """Resolve the GCS URI in *response* to a signed HTTPS URL (asynchronous).

        Args:
            response: ModelResponse to enrich in-place.
            **kwargs: Same as :meth:`post_process`.

        Returns:
            The same ModelResponse (possibly mutated).
        """
        vr = response.video_result if response else None
        if not vr:
            return response
        gcs_uri = (vr.extra or {}).get("gcs_uri")
        if not gcs_uri or vr.video_url:
            return response

        base_url        = kwargs.get("base_url", "")
        api_key         = kwargs.get("api_key", "")
        expiration_time = int(kwargs.get("expiration_time", 7))

        if not base_url or not api_key:
            logger.warning(
                "[VeoAdapter] apost_process: base_url and api_key are required to "
                "resolve GCS URI; skipping signed URL resolution."
            )
            return response

        try:
            signed_url   = await VeoAdapter.aget_signed_url(base_url, api_key, gcs_uri, expiration_time)
            vr.video_url = signed_url
            logger.info(f"[VeoAdapter] Resolved GCS URI to signed URL (async): {signed_url[:80]}...")
        except Exception as e:
            logger.warning(f"[VeoAdapter] Failed to resolve GCS URI to signed URL (async): {e}")
        return response

    # ------------------------------------------------------------------
    # GCS URI → signed HTTPS URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_signed_url(base_url: str,
                       api_key: str,
                       gcs_uri: str,
                       expiration_time: int = 7) -> str:
        """Convert a GCS URI to a signed HTTPS URL (synchronous).

        Calls ``GET /v1/objects/getUrlFromGcs`` on the Ant gateway.

        Args:
            base_url: Gateway base URL, e.g. ``https://matrixcube.alipay.com``.
            api_key: Bearer token for the Ant gateway.
            gcs_uri: GCS URI returned in the video task result,
                e.g. ``gs://antgroup_matrix_storage/output/.../sample_0.mp4``.
            expiration_time: Signed URL expiry in days (default 7).

        Returns:
            Signed HTTPS URL string.

        Raises:
            LLMResponseError: When the gateway returns a failure response.
            requests.HTTPError: On HTTP-level errors.
        """
        import requests as _requests

        url = f"{base_url.rstrip('/')}/{_VEO_GCS_URL_ENDPOINT}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        }
        params = {"gcsUri": gcs_uri, "expirationTime": expiration_time}

        resp = _requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()

        body = resp.json()
        if not body.get("success"):
            raise LLMResponseError(
                f"getUrlFromGcs failed: {body.get('message', body)}",
                "veo",
                body,
            )
        return body["result"]

    @staticmethod
    async def aget_signed_url(base_url: str,
                               api_key: str,
                               gcs_uri: str,
                               expiration_time: int = 7) -> str:
        """Convert a GCS URI to a signed HTTPS URL (asynchronous).

        Args:
            base_url: Gateway base URL.
            api_key: Bearer token.
            gcs_uri: GCS URI from the video task result.
            expiration_time: Signed URL expiry in days.

        Returns:
            Signed HTTPS URL string.

        Raises:
            LLMResponseError: When the gateway returns a failure response.
            aiohttp.ClientError: On HTTP-level errors.
        """
        import aiohttp

        url = f"{base_url.rstrip('/')}/{_VEO_GCS_URL_ENDPOINT}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        }
        params = {"gcsUri": gcs_uri, "expirationTime": expiration_time}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=30) as resp:
                resp.raise_for_status()
                body = await resp.json()

        if not body.get("success"):
            raise LLMResponseError(
                f"getUrlFromGcs failed: {body.get('message', body)}",
                "veo",
                body,
            )
        return body["result"]


# ---------------------------------------------------------------------------
# Adapter registry — (compiled_pattern, adapter_instance) ordered list
# ---------------------------------------------------------------------------

# Each entry: (compiled_regex, adapter_instance)
# Evaluated in order; first match wins.
_ADAPTER_REGISTRY: List[Tuple[re.Pattern, ModelAdapter]] = [
    (re.compile(r"kling-"),        KlingAdapter()),
    (re.compile(r"doubao-seedance-"), DoubaoAdapter()),
    (re.compile(r"seedance-"),     DoubaoAdapter()),
    (re.compile(r"doubao-video-"), DoubaoAdapter()),
    (re.compile(r"veo-"),          VeoAdapter()),
    (re.compile(r"Wan-AI/"),       WanXAdapter()),
    (re.compile(r"wanx"),       WanXAdapter()),
]


def _resolve_adapter(model_name: str) -> ModelAdapter:
    """Return the ModelAdapter instance for *model_name*.

    Args:
        model_name: The model identifier string.

    Returns:
        Matching ModelAdapter instance.

    Raises:
        ValueError: When no registered pattern matches *model_name*.
    """
    for pattern, adapter in _ADAPTER_REGISTRY:
        if pattern.match(model_name):
            return adapter
    raise ValueError(
        f"No adapter found for model '{model_name}'. "
        f"Register one via AntVideoProvider.register_adapter()."
    )


# ---------------------------------------------------------------------------
# AntVideoProvider — the single public provider class
# ---------------------------------------------------------------------------

class AntVideoProvider(VideoGenProviderBase):
    """Unified video generation provider routing through the Ant MatrixCube gateway.

    All requests are sent to ``POST /v1/genericCall`` at
    ``matrixcube.alipay.com``.  The correct per-vendor payload is built by
    delegating to the :class:`ModelAdapter` that matches the model name.

    Out-of-the-box supported models
    --------------------------------
    - ``kling-v1``, ``kling-v1-5``, ``kling-v2``, ``kling-v2-6`` → :class:`KlingAdapter`
    - ``doubao-seedance-*`` → :class:`DoubaoAdapter` *(placeholder)*
    - ``veo-*`` → :class:`VeoAdapter` *(placeholder)*

    Adding a new vendor
    -------------------
    ::

        from aworld.models.ant_video_provider import ModelAdapter, AntVideoProvider

        class MyAdapter(ModelAdapter):
            def build_submit_payload(self, request, model, extra): ...
            def build_status_payload(self, task_id, model, is_image2video): ...
            def parse_response(self, data, model, is_image2video=False): ...

        AntVideoProvider.register_adapter(
            patterns=[r"^my-model-"],
            adapter_class=MyAdapter,
        )

    Usage
    -----
    Preferred — structured request::

        from aworld.core.video_gen_provider import VideoGenerationRequest, VideoResolution
        from aworld.models.ant_video_provider import AntVideoProvider

        provider = AntVideoProvider(
            api_key="YOUR_ANT_API_KEY",
            model_name="kling-v2-6",
        )
        request = VideoGenerationRequest(
            prompt="宇航员站起身走了",
            resolution=VideoResolution.RES_720P,
            duration=5,
            extra_params={"mode": "pro", "poll": True},
        )
        response = provider.generate_video(request)
        print(response.video_result.video_url)

    Legacy flat-argument style (backward-compatible)::

        response = provider.generate_video_from_params(
            prompt="宇航员站起身走了",
            resolution=VideoResolution.RES_720P,
            duration=5,
            mode="pro",
            poll=True,
        )
    """

    # DEFAULT_BASE_URL = "https://matrixcube.alipay.com"
    DEFAULT_BASE_URL = os.getenv("ANT_VIDEO_BASE_URL", "")

    # ------------------------------------------------------------------
    # Adapter registry — class-level so it is shared across all instances
    # ------------------------------------------------------------------

    @classmethod
    def register_adapter(cls,
                          patterns: List[str],
                          adapter_class: type,
                          prepend: bool = True) -> None:
        """Register a :class:`ModelAdapter` subclass for a set of model patterns.

        Args:
            patterns: List of regex patterns.  Each pattern is matched against
                the full model name string (``re.match``).  Patterns that do
                not start with ``^`` are automatically anchored at the start.
            adapter_class: A subclass of :class:`ModelAdapter` to instantiate.
            prepend: When ``True`` (default) new patterns are inserted at the
                front of the registry so they take priority over existing ones.

        Raises:
            TypeError: When *adapter_class* is not a subclass of ModelAdapter.
        """
        if not (isinstance(adapter_class, type) and issubclass(adapter_class, ModelAdapter)):
            raise TypeError("adapter_class must be a subclass of ModelAdapter")

        adapter_instance = adapter_class()
        new_entries = []
        for raw in patterns:
            anchored = raw if raw.startswith("^") else f"^{raw}"
            new_entries.append((re.compile(anchored), adapter_instance))

        if prepend:
            for entry in reversed(new_entries):
                _ADAPTER_REGISTRY.insert(0, entry)
        else:
            _ADAPTER_REGISTRY.extend(new_entries)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _resolved_base_url(self) -> str:
        """Return the effective gateway base URL."""
        return (self.base_url or self.DEFAULT_BASE_URL).rstrip("/")

    def _resolved_api_key(self) -> str:
        """Return the effective API key."""
        return self.api_key or os.getenv("ANT_VIDEO_API_KEY", "")

    def _init_provider(self) -> LLMHTTPHandler:
        """Initialise a synchronous LLMHTTPHandler for the Ant gateway."""
        api_key = self._resolved_api_key()
        if not api_key:
            raise ValueError(
                "Ant video API key not found. Set the ANT_VIDEO_API_KEY environment "
                "variable or pass api_key to the constructor."
            )
        self.api_key = api_key
        base_url = self._resolved_base_url()
        if not base_url:
            raise ValueError(
                "Ant video base URL not found. Set the ANT_VIDEO_BASE_URL environment "
                "variable or pass base_url to the constructor."
            )
        self.base_url = base_url
        return LLMHTTPHandler(
            base_url=base_url,
            api_key=api_key,
            model_name=self.model_name or "",
            timeout=self.kwargs.get("timeout", 60),
            max_retries=self.kwargs.get("max_retries", 3),
        )

    def _init_async_provider(self) -> LLMHTTPHandler:
        """Reuse the same LLMHTTPHandler for async calls."""
        return self.provider if self.need_sync else self._init_provider()

    @classmethod
    def supported_models(cls) -> list:
        return [
            # Kling
            "kling",
            "wanx",
            "Wan-AI",

            # Doubao / Seedance
            "doubao-seedance",
            "seedance",
            # Google Veo (placeholder)
            "veo",
        ]

    # ------------------------------------------------------------------
    # Core video generation interface
    # ------------------------------------------------------------------

    def generate_video(self,
                       request: VideoGenerationRequest,
                       context: Context = None) -> ModelResponse:
        """Submit a video generation task to the Ant gateway (sync).

        The adapter matched to the model name builds the payload; the response
        is also parsed by the same adapter.

        Provider-control options in ``request.extra_params``:

        - ``poll`` (bool, default ``True``): Wait for the task to finish.
        - ``poll_interval`` (float, default 5): Seconds between polls.
        - ``poll_timeout`` (float, default 600): Maximum wait in seconds.
        - ``model_name`` (str): Override the model for this single call.

        Additional vendor-specific keys (e.g. ``mode`` for Kling) are
        forwarded to the adapter's ``build_submit_payload``.

        Args:
            request: Standardised video generation request.
            context: Runtime context (unused).

        Returns:
            ModelResponse with ``video_result`` populated.

        Raises:
            LLMResponseError: When the gateway returns a non-zero code or HTTP error.
            ValueError: When no adapter is registered for the model name.
            RuntimeError: When the synchronous client is not initialised.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialised. Set 'sync_enabled=True' in the constructor."
            )

        extra = dict(request.extra_params)
        model = extra.pop("model_name", None) or self.model_name
        if not model:
            raise ValueError("model_name must be provided in the constructor or via extra_params.")

        poll            = extra.pop("poll", True)
        poll_interval   = float(extra.pop("poll_interval", _DEFAULT_POLL_INTERVAL))
        poll_timeout    = float(extra.pop("poll_timeout",  _DEFAULT_POLL_TIMEOUT))
        expiration_time = int(extra.pop("expiration_time", 7))

        adapter                    = _resolve_adapter(model)
        is_image2video, payload    = adapter.build_submit_payload(request, model, extra)

        try:
            logger.info(
                f"[AntVideoProvider] Submitting task: model={model}, "
                f"adapter={type(adapter).__name__}, "
                f"mode={'image2video' if is_image2video else 'text2video'}, "
                f"prompt={request.prompt!r}"
                f"request={request}\n"
                f"payload={payload}\n"
            )
            body = self.provider.sync_call(payload, endpoint=_GENERIC_CALL_ENDPOINT)
        except Exception as e:
            logger.error(f"[AntVideoProvider] Submit error: {e}\n{traceback.format_exc()}")
            raise LLMResponseError(str(e), model)

        adapter.check_submit_response(body, model)

        data    = adapter.extract_submit_data(body)
        task_id = adapter.extract_task_id(data)
        logger.info(f"[AntVideoProvider] Task submitted: task_id={task_id}")

        pp_kwargs = dict(
            base_url=self.base_url,
            api_key=self.api_key,
            expiration_time=expiration_time,
        )

        if not poll:
            return adapter.post_process(
                adapter.parse_response(data, model, is_image2video), **pp_kwargs
            )

        response = self._poll_until_done(
            adapter=adapter,
            task_id=task_id,
            model=model,
            is_image2video=is_image2video,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
        )
        return adapter.post_process(response, **pp_kwargs)

    async def agenerate_video(self,
                              request: VideoGenerationRequest,
                              context: Context = None) -> ModelResponse:
        """Asynchronously submit a video generation task to the Ant gateway.

        See :meth:`generate_video` for parameter documentation.

        Args:
            request: Standardised video generation request.
            context: Runtime context (unused).

        Returns:
            ModelResponse with ``video_result`` populated.

        Raises:
            LLMResponseError: When the gateway returns an error.
            ValueError: When no adapter is registered for the model name.
            RuntimeError: When the async client is not initialised.
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialised. Set 'async_enabled=True' in the constructor."
            )

        extra = dict(request.extra_params)
        model = extra.pop("model_name", None) or self.model_name
        if not model:
            raise ValueError("model_name must be provided in the constructor or via extra_params.")

        poll            = extra.pop("poll", True)
        poll_interval   = float(extra.pop("poll_interval", _DEFAULT_POLL_INTERVAL))
        poll_timeout    = float(extra.pop("poll_timeout",  _DEFAULT_POLL_TIMEOUT))
        expiration_time = int(extra.pop("expiration_time", 7))

        adapter                 = _resolve_adapter(model)
        is_image2video, payload = adapter.build_submit_payload(request, model, extra)

        try:
            logger.info(
                f"[AntVideoProvider] Submitting task (async): model={model}, "
                f"adapter={type(adapter).__name__}, "
                f"prompt={request.prompt!r}"
            )
            body = await self.async_provider.async_call(payload, endpoint=_GENERIC_CALL_ENDPOINT)
        except Exception as e:
            logger.error(f"[AntVideoProvider] Submit error (async): {e}\n{traceback.format_exc()}")
            raise LLMResponseError(str(e), model)

        adapter.check_submit_response(body, model)

        data    = adapter.extract_submit_data(body)
        task_id = adapter.extract_task_id(data)
        logger.info(f"[AntVideoProvider] Task submitted (async): task_id={task_id}")

        pp_kwargs = dict(
            base_url=self._resolved_base_url(),
            api_key=self._resolved_api_key(),
            expiration_time=expiration_time,
        )

        if not poll:
            return await adapter.apost_process(
                adapter.parse_response(data, model, is_image2video), **pp_kwargs
            )

        response = await self._apoll_until_done(
            adapter=adapter,
            task_id=task_id,
            model=model,
            is_image2video=is_image2video,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
        )
        return await adapter.apost_process(response, **pp_kwargs)

    # ------------------------------------------------------------------
    # Task status
    # ------------------------------------------------------------------

    def get_video_task_status(self,
                              task_id: str,
                              context: Context = None,
                              **kwargs) -> ModelResponse:
        """Synchronously query the status of a submitted video task.

        Args:
            task_id: Task ID returned by :meth:`generate_video`.
            context: Runtime context (unused).
            **kwargs:
                - ``model_name`` (str): Required to select the correct adapter
                  and populate the response ``model`` field.
                - ``is_image2video`` (bool, default ``False``): Set to ``True``
                  when the original task was image-to-video.

        Returns:
            ModelResponse with ``video_result`` reflecting the current state.

        Raises:
            LLMResponseError: When the gateway returns an error.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialised. Set 'sync_enabled=True' in the constructor."
            )

        model           = kwargs.get("model_name") or self.model_name or "unknown"
        is_image2video  = bool(kwargs.get("is_image2video", False))
        expiration_time = int(kwargs.get("expiration_time", 7))

        adapter = _resolve_adapter(model)
        payload = adapter.build_status_payload(task_id, model, is_image2video)

        try:
            body = self.provider.sync_call(payload, endpoint=_GENERIC_CALL_ENDPOINT)
        except Exception as e:
            logger.error(f"[AntVideoProvider] Status query error: {e}\n{traceback.format_exc()}")
            raise LLMResponseError(str(e), model)

        adapter.check_status_response(body, model)
        data = adapter.extract_status_data(body)
        logger.debug(
            f"[AntVideoProvider] Task status: task_id={task_id}, "
            f"status={adapter.get_status_from_data(data)}"
        )
        response = adapter.parse_response(data, model, is_image2video)
        return adapter.post_process(
            response,
            base_url=self._resolved_base_url(),
            api_key=self._resolved_api_key(),
            expiration_time=expiration_time,
        )

    async def aget_video_task_status(self,
                                     task_id: str,
                                     context: Context = None,
                                     **kwargs) -> ModelResponse:
        """Asynchronously query the status of a submitted video task.

        Args:
            task_id: Task ID returned by :meth:`agenerate_video`.
            context: Runtime context (unused).
            **kwargs: Same as :meth:`get_video_task_status`.

        Returns:
            ModelResponse with ``video_result`` reflecting the current state.

        Raises:
            LLMResponseError: When the gateway returns an error.
            RuntimeError: When the async client is not initialised.
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialised. Set 'async_enabled=True' in the constructor."
            )

        model           = kwargs.get("model_name") or self.model_name or "unknown"
        is_image2video  = bool(kwargs.get("is_image2video", False))
        expiration_time = int(kwargs.get("expiration_time", 7))

        adapter = _resolve_adapter(model)
        payload = adapter.build_status_payload(task_id, model, is_image2video)

        try:
            body = await self.async_provider.async_call(payload, endpoint=_GENERIC_CALL_ENDPOINT)
        except Exception as e:
            logger.error(f"[AntVideoProvider] Status query error (async): {e}\n{traceback.format_exc()}")
            raise LLMResponseError(str(e), model)

        adapter.check_status_response(body, model)
        data = adapter.extract_status_data(body)
        logger.debug(
            f"[AntVideoProvider] Task status (async): task_id={task_id}, "
            f"status={adapter.get_status_from_data(data)}"
        )
        response = adapter.parse_response(data, model, is_image2video)
        return await adapter.apost_process(
            response,
            base_url=self._resolved_base_url(),
            api_key=self._resolved_api_key(),
            expiration_time=expiration_time,
        )

    # ------------------------------------------------------------------
    # Internal polling helpers
    # ------------------------------------------------------------------

    def _poll_until_done(self,
                         adapter: ModelAdapter,
                         task_id: str,
                         model: str,
                         is_image2video: bool,
                         poll_interval: float,
                         poll_timeout: float) -> ModelResponse:
        """Block and poll until the task reaches a terminal status.

        Args:
            adapter: Adapter instance used to build the status payload and parse
                the response.
            task_id: Task ID to poll.
            model: Model name.
            is_image2video: Passed through to the adapter.
            poll_interval: Seconds between polls.
            poll_timeout: Maximum total wait time in seconds.

        Returns:
            ModelResponse for the final task state.

        Raises:
            LLMResponseError: When the gateway returns an error.
            TimeoutError: When poll_timeout is exceeded.
        """
        deadline = time.monotonic() + poll_timeout
        attempt  = 0

        while True:
            attempt += 1
            payload = adapter.build_status_payload(task_id, model, is_image2video)
            try:
                body = self.provider.sync_call(payload, endpoint=_GENERIC_CALL_ENDPOINT)
                adapter.check_status_response(body, model)
            except LLMResponseError:
                raise
            except Exception as e:
                logger.error(f"[AntVideoProvider] Poll error for {task_id}: {e}")
                raise LLMResponseError(str(e), model)

            data       = adapter.extract_status_data(body)
            status_raw = adapter.get_status_from_data(data)
            logger.info(f"[AntVideoProvider] Task {task_id} poll #{attempt}: status={status_raw}")

            if adapter.is_terminal_status(status_raw):
                return adapter.parse_response(data, model, is_image2video)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Task {task_id} did not complete within {poll_timeout}s. "
                    f"Last status: {status_raw}"
                )
            time.sleep(min(poll_interval, remaining))

    async def _apoll_until_done(self,
                                adapter: ModelAdapter,
                                task_id: str,
                                model: str,
                                is_image2video: bool,
                                poll_interval: float,
                                poll_timeout: float) -> ModelResponse:
        """Asynchronously poll until the task reaches a terminal status.

        Args:
            adapter: Adapter instance.
            task_id: Task ID to poll.
            model: Model name.
            is_image2video: Passed through to the adapter.
            poll_interval: Seconds between polls.
            poll_timeout: Maximum total wait time in seconds.

        Returns:
            ModelResponse for the final task state.

        Raises:
            LLMResponseError: When the gateway returns an error.
            TimeoutError: When poll_timeout is exceeded.
        """
        import asyncio

        deadline = time.monotonic() + poll_timeout
        attempt  = 0

        while True:
            attempt += 1
            payload = adapter.build_status_payload(task_id, model, is_image2video)
            try:
                body = await self.async_provider.async_call(payload, endpoint=_GENERIC_CALL_ENDPOINT)
                adapter.check_status_response(body, model)
            except LLMResponseError:
                raise
            except Exception as e:
                logger.error(f"[AntVideoProvider] Poll error (async) for {task_id}: {e}")
                raise LLMResponseError(str(e), model)

            data       = adapter.extract_status_data(body)
            status_raw = adapter.get_status_from_data(data)
            logger.info(f"[AntVideoProvider] Task {task_id} poll #{attempt} (async): status={status_raw}")

            if adapter.is_terminal_status(status_raw):
                return adapter.parse_response(data, model, is_image2video)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Task {task_id} did not complete within {poll_timeout}s. "
                    f"Last status: {status_raw}"
                )
            await asyncio.sleep(min(poll_interval, remaining))


# New canonical public name without the ``ant`` prefix.
VideoProvider = AntVideoProvider


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _check_gateway_code(body: Dict[str, Any], model: str) -> None:
    """Raise LLMResponseError when the gateway response code is non-zero.

    Args:
        body: Parsed JSON response body.
        model: Model name used in the error message.

    Raises:
        LLMResponseError: When ``body["code"] != 0``.
    """
    code = body.get("code", 0)
    if code != 0:
        message = body.get("message", "Unknown error")
        raise LLMResponseError(
            f"Ant gateway error (code={code}): {message}",
            model,
            body,
        )
