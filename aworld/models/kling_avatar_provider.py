"""Kling 可灵「数字人」HTTP API (image + audio → video).

独立实现，不继承或修改 ``kling_provider`` / ``ant_video_provider`` 等模块。

- ``POST /v1/videos/avatar/image2video`` — 创建任务
- ``GET /v1/videos/avatar/image2video/{task_id}`` — 查询单个任务

配置示例（``~/.aworld/aworld.json`` 中 ``models.avatar``）::

    "avatar": {
      "api_key": "...",
      "model": "kling-v3",
      "provider": "kling_avatar",
      "base_url": "https://api-beijing.klingai.com"
    }

并在 ``llm.py`` 的 ``VIDEO_PROVIDER_CLASSES`` 中注册 ``kling_avatar`` → :class:`KlingAvatarProvider`。
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
import traceback
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests import HTTPError

from aworld.core.context.base import Context
from aworld.core.video_gen_provider import VideoGenProviderBase, VideoGenerationRequest
from aworld.logs.util import logger
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import LLMResponseError, ModelResponse, VideoGenerationResult

_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_POLL_TIMEOUT = 600.0
_DEFAULT_SUBMIT_PATH = "/v1/videos/avatar/image2video"
_DEFAULT_STATUS_PREFIX = "/v1/videos/avatar/image2video"

# Kling task_status → canonical
_STATUS_MAP = {
    "submitted": "submitted",
    "processing": "processing",
    "succeed": "succeeded",
    "failed": "failed",
}


def _truncate_for_log(obj: Any, max_len: int = 1200) -> str:
    s = repr(obj) if not isinstance(obj, str) else obj
    if len(s) > max_len:
        return s[:max_len] + f"... [truncated, len={len(s)}]"
    return s


def _summarize_submit_body(json_body: Dict[str, Any]) -> str:
    """Describe request body without dumping large base64."""
    parts = []
    img = json_body.get("image")
    if isinstance(img, str):
        t = img.strip()
        if t.startswith("http://") or t.startswith("https://"):
            parts.append(f"image=url[{len(t)}]:{t[:96]}...")
        else:
            parts.append(f"image=base64[len={len(t)}]")
    else:
        parts.append("image=<missing>")

    if json_body.get("audio_id"):
        parts.append(f"audio_id={json_body.get('audio_id')!r}")
    else:
        sf = json_body.get("sound_file")
        if isinstance(sf, str):
            s = sf.strip()
            if s.startswith("http://") or s.startswith("https://"):
                parts.append(f"sound_file=url[{len(s)}]:{s[:96]}...")
            else:
                parts.append(f"sound_file=base64[len={len(s)}]")
        else:
            parts.append("sound_file=<missing>")

    parts.append(f"mode={json_body.get('mode')!r}")
    parts.append(f"prompt_len={len(json_body.get('prompt') or '')}")
    return "; ".join(parts)


def _check_kling_code(body: Dict[str, Any], model: str) -> None:
    code = body.get("code", 0)
    if code != 0:
        logger.error(
            f"[KlingAvatarProvider] API business error code={code} "
            f"message={body.get('message')!r} raw={_truncate_for_log(body, 2000)}"
        )
        raise LLMResponseError(
            body.get("message", "Unknown error") or f"code={code}",
            model,
            body,
        )


def _parse_avatar_data_to_response(data: Dict[str, Any], model: str) -> ModelResponse:
    task_id = data.get("task_id", "")
    status_raw = data.get("task_status", "unknown")
    status = _STATUS_MAP.get(status_raw, status_raw)

    if status == "failed":
        logger.error(
            f"[KlingAvatarProvider] Task terminal failed task_id={task_id!r} "
            f"task_status_msg={data.get('task_status_msg')!r} raw={_truncate_for_log(data, 2000)}"
        )

    video_url: Optional[str] = None
    duration: Optional[float] = None
    video_list = []

    task_result = data.get("task_result") or {}
    videos = task_result.get("videos") or []
    if videos:
        first = videos[0]
        video_url = first.get("url")
        raw_dur = first.get("duration")
        try:
            duration = float(raw_dur) if raw_dur is not None else None
        except (TypeError, ValueError):
            duration = None
        video_list = videos

    extra: Dict[str, Any] = {
        "raw_status": status_raw,
        "task_status_msg": data.get("task_status_msg", ""),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "adapter": "kling_avatar",
    }
    if len(video_list) > 1:
        extra["all_videos"] = video_list

    if status == "succeeded" and video_url:
        preview = (video_url[:160] + "...") if len(video_url) > 160 else video_url
        logger.info(
            f"[KlingAvatarProvider] video ready task_id={task_id!r} duration={duration!r} url_preview={preview!r}"
        )

    return ModelResponse(
        id=task_id or f"kling-avatar-{int(time.time())}",
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


class _AvatarPayloadBuilder:
    """Build JSON body for avatar create-task; image/audio normalization."""

    def build(
        self,
        request: VideoGenerationRequest,
        extra: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        image = self._parse_image_input(request.image_url, request.image_path)
        if not image:
            raise ValueError(
                "Kling avatar API requires a reference image: image_url or image_path "
                "(or map image_data to image_url in the agent)."
            )

        audio_id = extra.pop("audio_id", None)
        sound_file = extra.pop("sound_file", None)
        if not audio_id:
            sound_file = sound_file or self._resolve_sound_file(extra)

        if not audio_id and not sound_file:
            raise ValueError(
                "Kling avatar API requires audio_id or sound_file "
                "(audio_url / audio_path / audio_data / sound_file)."
            )
        if audio_id and sound_file:
            raise ValueError("Provide either audio_id or sound_file, not both.")

        submit_path = (os.getenv("KLING_AVATAR_SUBMIT_ENDPOINT") or "").strip() or _DEFAULT_SUBMIT_PATH
        submit_override = extra.pop("submit_endpoint", None)
        if submit_override:
            submit_path = str(submit_override).strip()

        payload: Dict[str, Any] = {
            "image": image,
            "prompt": request.prompt or "",
            "mode": extra.pop("mode", "std"),
        }
        if audio_id:
            payload["audio_id"] = str(audio_id).strip()
        else:
            payload["sound_file"] = sound_file

        for key in ("watermark_info", "callback_url", "external_task_id"):
            if key in extra and extra[key] is not None:
                payload[key] = extra.pop(key)

        if request.video_url or request.video_path:
            logger.warning("[KlingAvatarProvider] video_url / video_path are not supported; ignoring.")

        return submit_path, payload

    def status_path(self, task_id: str) -> str:
        custom = (os.getenv("KLING_AVATAR_STATUS_ENDPOINT") or "").strip()
        if custom:
            if "{task_id}" in custom:
                path = custom.format(task_id=task_id)
            elif "{id}" in custom:
                path = custom.format(id=task_id)
            else:
                path = f"{custom.rstrip('/')}/{task_id}"
        else:
            path = f"{_DEFAULT_STATUS_PREFIX.rstrip('/')}/{task_id}"
        if not path.startswith("/"):
            path = "/" + path
        return path

    def _parse_image_input(self, image_url: Optional[str], image_path: Optional[str]) -> Optional[str]:
        exts = {".jpg", ".jpeg", ".png"}
        image_data: Optional[str] = None

        if image_url:
            url = image_url.strip()
            if url.startswith("data:"):
                if ";base64," in url:
                    image_data = url.split(";base64,", 1)[1]
                else:
                    logger.warning(
                        "[KlingAvatarProvider] image_url starts with 'data:' but has no ';base64,' separator."
                    )
                    image_data = url
            else:
                image_data = url

        if not image_data and image_path:
            ext = os.path.splitext(image_path)[1].lower()
            if ext not in exts:
                logger.warning(
                    f"[KlingAvatarProvider] image_path extension {ext!r}; Kling accepts {sorted(exts)}."
                )
            image_data = VideoGenProviderBase.read_file_as_base64(image_path)

        return image_data

    def _resolve_sound_file(self, extra: Dict[str, Any]) -> Optional[str]:
        audio_url = extra.pop("audio_url", None)
        audio_path = extra.pop("audio_path", None)
        audio_data = extra.pop("audio_data", None)
        if audio_data:
            return self._normalize_audio_value(audio_data)
        if audio_url:
            return self._normalize_audio_value(audio_url)
        if audio_path:
            return self._normalize_audio_value(audio_path)
        return None

    def _normalize_audio_value(self, s: str) -> str:
        s = (s or "").strip()
        if not s:
            return s
        if s.startswith("http://") or s.startswith("https://"):
            return s
        if s.startswith("data:") and ";base64," in s:
            return s.split(";base64,", 1)[1]
        if s.startswith("file://"):
            path = urlparse(s).path
        else:
            path = s
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        return s


class KlingAvatarProvider(VideoGenProviderBase):
    """Kling 官方数字人 API（直连 HTTP）。"""

    DEFAULT_BASE_URL = "https://api-beijing.klingai.com"

    def _resolved_base_url(self) -> str:
        return (self.base_url or os.getenv("AVATAR_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")

    def _resolved_api_key(self) -> str:
        return (
            self.api_key
            or os.getenv("AVATAR_API_KEY", "").strip()
            or os.getenv("KLING_API_KEY", "").strip()
            or os.getenv("ANT_VIDEO_API_KEY", "").strip()
        )

    def _init_provider(self) -> LLMHTTPHandler:
        api_key = self._resolved_api_key()
        if not api_key:
            raise ValueError(
                "Kling avatar API key not found. Set AVATAR_API_KEY / KLING_API_KEY or pass api_key."
            )
        self.api_key = api_key
        base_url = self._resolved_base_url()
        if not base_url:
            raise ValueError("Kling avatar base_url missing.")
        self.base_url = base_url
        logger.info(
            f"[KlingAvatarProvider] init base_url={base_url!r} model_name={self.model_name or '(unset)'!r} "
            f"api_key_set={bool(api_key)} timeout={self.kwargs.get('timeout', 60)!r}"
        )
        return LLMHTTPHandler(
            base_url=base_url,
            api_key=api_key,
            model_name=self.model_name or "",
            headers={
                "x-api-key": api_key,
                "X-API-Key": api_key,
            },
            timeout=self.kwargs.get("timeout", 60),
            max_retries=self.kwargs.get("max_retries", 3),
        )

    def _init_async_provider(self) -> LLMHTTPHandler:
        return self.provider if self.need_sync else self._init_provider()

    def _http_get_json(self, path: str) -> Dict[str, Any]:
        assert self.provider is not None
        base = self._resolved_base_url().rstrip("/")
        url = f"{base}/{path.lstrip('/')}"
        logger.debug(f"[KlingAvatarProvider] GET {url!r}")
        resp = requests.get(
            url,
            headers=self.provider.headers,
            timeout=self.provider.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    @classmethod
    def supported_models(cls) -> list:
        return ["kling-v3", "kling-avatar-v1"]

    def generate_video(
        self,
        request: VideoGenerationRequest,
        context: Context = None,
    ) -> ModelResponse:
        if not self.provider:
            raise RuntimeError("Sync provider not initialised.")

        extra = dict(request.extra_params)
        model = extra.pop("model_name", None) or self.model_name
        if not model:
            raise ValueError("model_name must be set in constructor or extra_params.")

        poll = extra.pop("poll", True)
        poll_interval = float(extra.pop("poll_interval", _DEFAULT_POLL_INTERVAL))
        poll_timeout = float(extra.pop("poll_timeout", _DEFAULT_POLL_TIMEOUT))

        logger.info(
            f"[KlingAvatarProvider] generate_video start model={model!r} base_url={self._resolved_base_url()!r} "
            f"poll={poll!r} interval={poll_interval!r} timeout={poll_timeout!r} "
            f"extra_keys={sorted(extra.keys())!r}"
        )

        builder = _AvatarPayloadBuilder()
        try:
            submit_path, json_body = builder.build(request, extra)
        except ValueError as e:
            logger.error(f"[KlingAvatarProvider] build payload failed: {e!r}")
            raise LLMResponseError(str(e), model) from e

        logger.info(
            f"[KlingAvatarProvider] submit POST endpoint={submit_path!r} {_summarize_submit_body(json_body)}"
        )

        try:
            raw = self.provider.sync_call(json_body, endpoint=submit_path.lstrip("/"))
        except Exception as e:
            logger.error(
                f"[KlingAvatarProvider] HTTP submit exception: {e!r}\n{traceback.format_exc()}"
            )
            raise LLMResponseError(str(e), model) from e

        req_id = raw.get("request_id")
        _check_kling_code(raw, model)
        data = raw.get("data") or {}
        task_id = data.get("task_id", "")
        logger.info(
            f"[KlingAvatarProvider] submit OK request_id={req_id!r} task_id={task_id!r} "
            f"task_status={data.get('task_status')!r}"
        )
        if not task_id:
            logger.error(
                f"[KlingAvatarProvider] submit response missing task_id raw={_truncate_for_log(raw, 3000)}"
            )
            raise LLMResponseError("Missing task_id in submit response", model, raw)

        if not poll:
            return _parse_avatar_data_to_response(data, model)

        deadline = time.monotonic() + poll_timeout
        attempt = 0
        status_path = builder.status_path(task_id)
        logger.info(
            f"[KlingAvatarProvider] polling task_id={task_id!r} status_path={status_path!r} "
            f"until terminal or {poll_timeout:.0f}s"
        )

        while True:
            attempt += 1
            try:
                body = self._http_get_json(status_path)
            except HTTPError as e:
                try:
                    err_body = e.response.json() if e.response is not None else {}
                except Exception:
                    err_body = {}
                msg = err_body.get("message", str(e))
                raise LLMResponseError(f"Status GET failed: {msg}", model, err_body) from e
            except Exception as e:
                logger.error(f"[KlingAvatarProvider] Poll error: {e}\n{traceback.format_exc()}")
                raise LLMResponseError(str(e), model) from e

            _check_kling_code(body, model)
            data = body.get("data") or {}
            status_raw = data.get("task_status", "unknown")
            msg_snip = (data.get("task_status_msg") or "")[:240]
            logger.info(
                f"[KlingAvatarProvider] poll #{attempt} task_id={task_id!r} "
                f"task_status={status_raw!r} msg={msg_snip!r}"
            )

            if status_raw in ("succeed", "failed"):
                return _parse_avatar_data_to_response(data, model)

            if time.monotonic() >= deadline:
                logger.error(
                    f"[KlingAvatarProvider] poll timeout task_id={task_id!r} last_status={status_raw!r}"
                )
                raise TimeoutError(
                    f"Task {task_id} did not finish within {poll_timeout}s (last status={status_raw})"
                )
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    async def agenerate_video(
        self,
        request: VideoGenerationRequest,
        context: Context = None,
    ) -> ModelResponse:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.generate_video(request, context))

    def get_video_task_status(
        self,
        task_id: str,
        context: Context = None,
        **kwargs,
    ) -> ModelResponse:
        if not self.provider:
            raise RuntimeError("Sync provider not initialised.")
        model = kwargs.get("model_name") or self.model_name or "unknown"
        path = _AvatarPayloadBuilder().status_path(task_id)
        logger.info(f"[KlingAvatarProvider] get_video_task_status task_id={task_id!r} path={path!r}")
        try:
            body = self._http_get_json(path)
        except Exception as e:
            logger.error(
                f"[KlingAvatarProvider] get_video_task_status failed: {e!r}\n{traceback.format_exc()}"
            )
            raise LLMResponseError(str(e), model) from e
        _check_kling_code(body, model)
        data = body.get("data") or {}
        return _parse_avatar_data_to_response(data, model)

    async def aget_video_task_status(
        self,
        task_id: str,
        context: Context = None,
        **kwargs,
    ) -> ModelResponse:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.get_video_task_status(task_id, context, **kwargs),
        )
