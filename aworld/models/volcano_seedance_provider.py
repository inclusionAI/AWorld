import os
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests import HTTPError

from aworld.core.context.base import Context
from aworld.core.video_gen_provider import VideoGenProviderBase, VideoGenerationRequest
from aworld.logs.util import logger
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import LLMResponseError, ModelResponse, VideoGenerationResult

_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_POLL_TIMEOUT = 600.0

_CREATE_TASK_ENDPOINT = "/api/v3/contents/generations/tasks"

_STATUS_MAP = {
    "queued": "submitted",
    "running": "processing",
    "succeeded": "succeeded",
    "failed": "failed",
    "expired": "failed",
    "cancelled": "failed",
}

_TERMINAL_STATUSES = {"succeeded", "failed", "expired", "cancelled"}


class VolcanoSeedanceProvider(VideoGenProviderBase):
    """Direct Volcano Ark Seedance provider."""

    DEFAULT_BASE_URL = os.getenv("VOLCANO_SEEDANCE_BASE_URL", "https://ark.cn-beijing.volces.com")

    def _resolved_base_url(self) -> str:
        return (self.base_url or self.DEFAULT_BASE_URL).rstrip("/")

    def _resolved_api_key(self) -> str:
        return (
            self.api_key
            or os.getenv("VOLCANO_SEEDANCE_API_KEY", "")
            or os.getenv("ARK_API_KEY", "")
            or os.getenv("DIFFUSION_API_KEY", "")
        )

    def _init_provider(self) -> LLMHTTPHandler:
        api_key = self._resolved_api_key()
        if not api_key:
            raise ValueError(
                "Volcano Seedance API key not found. Set VOLCANO_SEEDANCE_API_KEY/ARK_API_KEY "
                "or pass api_key in config."
            )

        base_url = self._resolved_base_url()
        if not base_url:
            raise ValueError(
                "Volcano Seedance base URL not found. Set VOLCANO_SEEDANCE_BASE_URL "
                "or pass base_url in config."
            )

        self.api_key = api_key
        self.base_url = base_url

        return LLMHTTPHandler(
            base_url=base_url,
            api_key=api_key,
            model_name=self.model_name or "",
            timeout=self.kwargs.get("timeout", 60),
            max_retries=self.kwargs.get("max_retries", 3),
        )

    def _init_async_provider(self) -> LLMHTTPHandler:
        return self.provider if self.need_sync else self._init_provider()

    @classmethod
    def supported_models(cls) -> list:
        return [
            "doubao-seedance-2-0",
            "doubao-seedance-2-0-fast",
            "doubao-seedance-1-5-pro",
            "doubao-seedance-1-0-pro",
            "doubao-seedance-1-0-pro-fast",
            "doubao-seedance-1-0-lite-t2v",
            "doubao-seedance-1-0-lite-i2v",
            "seedance-2.0",
            "seedance-2.0-fast",
        ]

    def _normalize_resolution(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return getattr(value, "value", None)

    def _normalize_ratio(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return getattr(value, "value", None)

    def _build_content_items(
        self,
        request: VideoGenerationRequest,
        extra: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        prompt_text = request.prompt or ""
        content: List[Dict[str, Any]] = []
        if prompt_text.strip():
            content.append({"type": "text", "text": prompt_text})

        image_url = request.image_url
        if not image_url and request.image_path:
            image_url = f"data:image/jpeg;base64,{VideoGenProviderBase.read_file_as_base64(request.image_path)}"

        image_tail = extra.pop("image_tail", None) or extra.pop("last_frame_url", None)
        reference_images = extra.pop("reference_images", None)
        reference_videos = extra.pop("reference_videos", None)
        reference_audios = extra.pop("reference_audios", None)

        if image_url and image_tail:
            content.append({"type": "image_url", "role": "first_frame", "image_url": {"url": image_url}})
            content.append({"type": "image_url", "role": "last_frame", "image_url": {"url": image_tail}})
        elif image_url:
            content.append({"type": "image_url", "image_url": {"url": image_url}})

        if isinstance(reference_images, list):
            for img in reference_images:
                if img:
                    content.append({"type": "image_url", "role": "reference_image", "image_url": {"url": img}})

        if isinstance(reference_videos, list):
            for video in reference_videos:
                if video:
                    content.append({"type": "video_url", "role": "reference_video", "video_url": {"url": video}})

        if isinstance(reference_audios, list):
            for audio in reference_audios:
                if audio:
                    content.append({"type": "audio_url", "role": "reference_audio", "audio_url": {"url": audio}})

        if not content:
            raise ValueError("Seedance request requires at least one valid content item.")

        return content

    @staticmethod
    def _extract_http_error_details(exc: Exception) -> Tuple[str, Dict[str, Any]]:
        if not isinstance(exc, HTTPError) or getattr(exc, "response", None) is None:
            return str(exc), {}

        response = exc.response
        details: Dict[str, Any] = {
            "status_code": response.status_code,
        }
        try:
            body = response.json()
            details["response_body"] = body
            message = body.get("message") or body.get("error", {}).get("message") or str(exc)
            if "request_id" in body:
                details["request_id"] = body.get("request_id")
            elif isinstance(body.get("error"), dict) and body["error"].get("request_id"):
                details["request_id"] = body["error"].get("request_id")
            return message, details
        except Exception:
            text = ""
            try:
                text = response.text
            except Exception:
                pass
            if text:
                details["response_text"] = text
            return (f"{exc}; body={text}" if text else str(exc)), details

    def _build_submit_payload(
        self,
        request: VideoGenerationRequest,
        model: str,
        extra: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "content": self._build_content_items(request, extra),
        }

        ratio = self._normalize_ratio(extra.pop("ratio", None) or request.aspect_ratio)
        resolution = self._normalize_resolution(extra.pop("resolution", None) or request.resolution)
        duration = extra.pop("duration", None)
        if duration is None:
            duration = request.duration
        seed = extra.pop("seed", None)
        if seed is None:
            seed = request.seed

        if ratio is not None:
            payload["ratio"] = ratio
        if resolution is not None:
            payload["resolution"] = resolution
        if duration is not None:
            payload["duration"] = int(duration)
        if seed is not None:
            payload["seed"] = int(seed)

        passthrough_keys = {
            "callback_url",
            "return_last_frame",
            "service_tier",
            "execution_expires_after",
            "generate_audio",
            "draft",
            "tools",
            "safety_identifier",
            "frames",
            "camera_fixed",
            "watermark",
        }
        for key in passthrough_keys:
            if key in extra and extra[key] is not None:
                payload[key] = extra.pop(key)

        return payload

    def _parse_task_response(self, data: Dict[str, Any], model: str) -> ModelResponse:
        task_id = data.get("id", "")
        status_raw = data.get("status", "queued")
        status = _STATUS_MAP.get(status_raw, status_raw)

        content = data.get("content") or {}
        video_url = content.get("video_url") if isinstance(content, dict) else None

        raw_duration = data.get("duration")
        try:
            duration = float(raw_duration) if raw_duration is not None else None
        except (TypeError, ValueError):
            duration = None

        extra = {
            "raw_status": status_raw,
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "ratio": data.get("ratio"),
            "resolution": data.get("resolution"),
            "seed": data.get("seed"),
            "generate_audio": data.get("generate_audio"),
            "usage": data.get("usage"),
            "last_frame_url": content.get("last_frame_url") if isinstance(content, dict) else None,
        }

        return ModelResponse(
            id=task_id or f"volcano-seedance-{int(time.time())}",
            model=model,
            video_result=VideoGenerationResult(
                task_id=task_id,
                video_url=video_url,
                status=status,
                duration=duration,
                resolution=data.get("resolution"),
                extra=extra,
            ),
            raw_response=data,
        )

    def _fetch_status_sync(self, task_id: str) -> Dict[str, Any]:
        url = f"{self._resolved_base_url()}{_CREATE_TASK_ENDPOINT}/{task_id}"
        headers = {
            "Authorization": f"Bearer {self._resolved_api_key()}",
            "Content-Type": "application/json",
        }
        response = requests.get(url, headers=headers, timeout=self.kwargs.get("timeout", 60))
        response.raise_for_status()
        return response.json()

    async def _fetch_status_async(self, task_id: str) -> Dict[str, Any]:
        import aiohttp

        url = f"{self._resolved_base_url()}{_CREATE_TASK_ENDPOINT}/{task_id}"
        headers = {
            "Authorization": f"Bearer {self._resolved_api_key()}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=self.kwargs.get("timeout", 60)) as response:
                response.raise_for_status()
                return await response.json()

    def generate_video(self, request: VideoGenerationRequest, context: Context = None) -> ModelResponse:
        if not self.provider:
            raise RuntimeError("Sync provider not initialised. Set sync_enabled=True.")

        extra = dict(request.extra_params)
        model = extra.pop("model_name", None) or self.model_name
        if not model:
            raise ValueError("model_name must be provided in constructor or extra_params.")

        poll = bool(extra.pop("poll", True))
        poll_interval = float(extra.pop("poll_interval", _DEFAULT_POLL_INTERVAL))
        poll_timeout = float(extra.pop("poll_timeout", _DEFAULT_POLL_TIMEOUT))
        payload = self._build_submit_payload(request, model, extra)

        try:
            body = self.provider.sync_call(payload, endpoint=_CREATE_TASK_ENDPOINT)
        except Exception as e:
            logger.error(f"[VolcanoSeedanceProvider] Submit error: {e}\n{traceback.format_exc()}")
            msg, details = self._extract_http_error_details(e)
            raise LLMResponseError(msg, model, error_details=details)

        response = self._parse_task_response(body, model)
        if not poll:
            return response

        if not response.video_result or not response.video_result.task_id:
            raise LLMResponseError("Missing task id in Seedance submit response.", model, body)

        return self._poll_until_done_sync(
            task_id=response.video_result.task_id,
            model=model,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
        )

    async def agenerate_video(self, request: VideoGenerationRequest, context: Context = None) -> ModelResponse:
        if not self.async_provider:
            raise RuntimeError("Async provider not initialised. Set async_enabled=True.")

        extra = dict(request.extra_params)
        model = extra.pop("model_name", None) or self.model_name
        if not model:
            raise ValueError("model_name must be provided in constructor or extra_params.")

        poll = bool(extra.pop("poll", True))
        poll_interval = float(extra.pop("poll_interval", _DEFAULT_POLL_INTERVAL))
        poll_timeout = float(extra.pop("poll_timeout", _DEFAULT_POLL_TIMEOUT))
        payload = self._build_submit_payload(request, model, extra)

        try:
            body = await self.async_provider.async_call(payload, endpoint=_CREATE_TASK_ENDPOINT)
        except Exception as e:
            logger.error(f"[VolcanoSeedanceProvider] Submit error (async): {e}\n{traceback.format_exc()}")
            msg, details = self._extract_http_error_details(e)
            raise LLMResponseError(msg, model, error_details=details)

        response = self._parse_task_response(body, model)
        if not poll:
            return response

        if not response.video_result or not response.video_result.task_id:
            raise LLMResponseError("Missing task id in Seedance submit response.", model, body)

        return await self._poll_until_done_async(
            task_id=response.video_result.task_id,
            model=model,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
        )

    def get_video_task_status(
        self,
        task_id: str,
        context: Context = None,
        **kwargs,
    ) -> ModelResponse:
        model = kwargs.get("model_name") or self.model_name or "unknown"
        try:
            body = self._fetch_status_sync(task_id)
            return self._parse_task_response(body, model)
        except Exception as e:
            logger.error(f"[VolcanoSeedanceProvider] Status query error: {e}\n{traceback.format_exc()}")
            msg, details = self._extract_http_error_details(e)
            raise LLMResponseError(msg, model, error_details=details)

    async def aget_video_task_status(
        self,
        task_id: str,
        context: Context = None,
        **kwargs,
    ) -> ModelResponse:
        model = kwargs.get("model_name") or self.model_name or "unknown"
        try:
            body = await self._fetch_status_async(task_id)
            return self._parse_task_response(body, model)
        except Exception as e:
            logger.error(f"[VolcanoSeedanceProvider] Status query error (async): {e}\n{traceback.format_exc()}")
            msg, details = self._extract_http_error_details(e)
            raise LLMResponseError(msg, model, error_details=details)

    def _poll_until_done_sync(
        self,
        task_id: str,
        model: str,
        poll_interval: float,
        poll_timeout: float,
    ) -> ModelResponse:
        deadline = time.monotonic() + poll_timeout
        attempt = 0
        while True:
            attempt += 1
            response = self.get_video_task_status(task_id=task_id, model_name=model)
            raw_status = (response.video_result.extra or {}).get("raw_status")
            logger.info(f"[VolcanoSeedanceProvider] Task {task_id} poll #{attempt}: status={raw_status}")
            if raw_status in _TERMINAL_STATUSES:
                return response

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Task {task_id} did not complete within {poll_timeout}s. Last status: {raw_status}"
                )
            time.sleep(min(poll_interval, remaining))

    async def _poll_until_done_async(
        self,
        task_id: str,
        model: str,
        poll_interval: float,
        poll_timeout: float,
    ) -> ModelResponse:
        import asyncio

        deadline = time.monotonic() + poll_timeout
        attempt = 0
        while True:
            attempt += 1
            response = await self.aget_video_task_status(task_id=task_id, model_name=model)
            raw_status = (response.video_result.extra or {}).get("raw_status")
            logger.info(f"[VolcanoSeedanceProvider] Task {task_id} poll #{attempt} (async): status={raw_status}")
            if raw_status in _TERMINAL_STATUSES:
                return response

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Task {task_id} did not complete within {poll_timeout}s. Last status: {raw_status}"
                )
            await asyncio.sleep(min(poll_interval, remaining))
