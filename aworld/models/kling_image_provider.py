# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Kling Image Provider — official Kling HTTP API for image generation.

Endpoints:
- Text / single-reference: ``POST /v1/images/generations``
- Multi-reference: ``POST /v1/images/multi-image2image``

Async task flow: create task → poll ``GET`` until terminal status → read image URLs.

Configure with ``provider: kling_image`` in model config (see ``aworld.json``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from aworld.core.llm_provider import LLMProviderBase
from aworld.logs.util import logger
from aworld.models.image_provider import ImageProvider
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import LLMResponseError, ModelResponse

_DEFAULT_MODEL = "kling-v2-1"
_DEFAULT_POLL_INTERVAL = 2.0
_DEFAULT_POLL_TIMEOUT = 300.0

_KLING_ASPECT_RATIOS = (
    "16:9",
    "9:16",
    "1:1",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "21:9",
)

# Official multi-image2image API only accepts these model_name values.
_MULTI_IMAGE2IMAGE_MODELS = frozenset({"kling-v2", "kling-v2-1"})


def _check_kling_body(body: Dict[str, Any], model: str) -> None:
    code = body.get("code", 0)
    if code != 0:
        msg = body.get("message", "Unknown error")
        raise LLMResponseError(f"Kling image API error (code={code}): {msg}", model, body)


def _parse_kling_http_error_body(status_code: int, text: str) -> tuple[str, Any]:
    """Return (short_message, parsed_or_raw) for logging and LLMResponseError."""
    if not text or not text.strip():
        return (f"HTTP {status_code} (empty body)", None)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            msg = parsed.get("message") or parsed.get("msg") or text[:2000]
            return (f"HTTP {status_code}: {msg}", parsed)
    except json.JSONDecodeError:
        pass
    return (f"HTTP {status_code}: {text[:2000]}", {"raw": text[:4000]})


class KlingImageProvider(LLMProviderBase):
    """Kling image generation (txt2img, img2img, multi-image2image) via async task API."""

    DEFAULT_MODEL = _DEFAULT_MODEL

    @staticmethod
    def _resolve_model_for_multi_image2image(model_name: str) -> str:
        """multi-image2image only supports kling-v2 / kling-v2-1 (official docs)."""
        m = (model_name or "").strip()
        if m in _MULTI_IMAGE2IMAGE_MODELS:
            return m
        logger.warning(
            "[KlingImageProvider] multi-image2image only supports %s; got %r — using kling-v2-1",
            sorted(_MULTI_IMAGE2IMAGE_MODELS),
            m,
        )
        return "kling-v2-1"

    @staticmethod
    def _discard_multi_image_incompatible_extra(extra: Dict[str, Any]) -> None:
        """Remove fields valid for /generations but not for /multi-image2image (avoids HTTP 400)."""
        for key in ("image_reference", "image_fidelity", "human_fidelity", "element_list", "resolution"):
            if key in extra:
                logger.info(
                    "[KlingImageProvider] omitting %r from multi-image2image request (not supported on this endpoint)",
                    key,
                )
                extra.pop(key, None)

    def _kling_sync_post_json(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST JSON like LLMHTTPHandler.sync_call but include response body on HTTP errors."""
        if not self.provider:
            raise RuntimeError("Sync HTTP handler not initialized.")
        h = self.provider
        url = f"{h.base_url}/{endpoint.lstrip('/')}"
        headers = dict(h.headers)
        retries = 0
        last_error: Optional[Exception] = None
        while retries < h.max_retries:
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=h.timeout)
                text = resp.text or ""
                if resp.status_code >= 400:
                    short, parsed = _parse_kling_http_error_body(resp.status_code, text)
                    raise LLMResponseError(short, self.model_name, parsed)
                if not text.strip():
                    return {}
                return json.loads(text)
            except LLMResponseError:
                raise
            except Exception as e:
                last_error = e
                retries += 1
                if retries < h.max_retries:
                    logger.warning(
                        "Kling sync POST failed (%s/%s): %s",
                        retries,
                        h.max_retries,
                        e,
                    )
                    backoff = min(2**retries + random.uniform(0, 1), 10)
                    time.sleep(backoff)
                else:
                    logger.error("Kling sync POST failed after %s retries: %s", h.max_retries, e)
                    raise last_error if last_error else e
        raise last_error if last_error else RuntimeError("Kling sync POST failed")

    async def _kling_async_post_json(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST JSON like LLMHTTPHandler.async_call but include response body on HTTP errors."""
        import aiohttp

        if not self.async_provider:
            raise RuntimeError("Async HTTP handler not initialized.")
        h = self.async_provider
        url = f"{h.base_url}/{endpoint.lstrip('/')}"
        headers = dict(h.headers)
        timeout = aiohttp.ClientTimeout(total=h.timeout)
        retries = 0
        last_error: Optional[Exception] = None
        while retries < h.max_retries:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        text = await resp.text()
                        if resp.status >= 400:
                            short, parsed = _parse_kling_http_error_body(resp.status, text or "")
                            raise LLMResponseError(short, self.model_name, parsed)
                        if not (text or "").strip():
                            return {}
                        return json.loads(text)
            except LLMResponseError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
                last_error = e
                retries += 1
                if retries < h.max_retries:
                    logger.warning(
                        "Kling async POST failed (%s/%s): %s",
                        retries,
                        h.max_retries,
                        e,
                    )
                    backoff = min(2**retries + random.uniform(0, 1), 10)
                    await asyncio.sleep(backoff)
                else:
                    logger.error("Kling async POST failed after %s retries: %s", h.max_retries, e)
                    raise last_error if last_error else e
        raise last_error if last_error else RuntimeError("Kling async POST failed")

    @classmethod
    def supported_models(cls) -> list:
        return [
            "kling-v1",
            "kling-v1-5",
            "kling-v2",
            "kling-v2-new",
            "kling-v2-1",
            "kling-v3",
        ]

    @staticmethod
    def _coerce_image_inputs(**kwargs) -> List[str]:
        return ImageProvider._coerce_image_inputs(**kwargs)

    @staticmethod
    def _is_remote_url(value: str) -> bool:
        return ImageProvider._is_remote_url(value)

    @staticmethod
    def _is_data_url(value: str) -> bool:
        return ImageProvider._is_data_url(value)

    @staticmethod
    def _looks_like_local_path(value: str) -> bool:
        return ImageProvider._looks_like_local_path(value)

    @staticmethod
    def _read_local_as_b64(value: str) -> str:
        p = Path(os.path.expanduser(value.strip()))
        if value.strip().startswith("file://"):
            p = Path(os.path.expanduser(value.strip()[7:]))
        if not p.exists() or not p.is_file():
            raise ValueError(f"Local image file not found: {value}")
        return base64.b64encode(p.read_bytes()).decode("ascii")

    @classmethod
    def _normalize_image_ref(cls, raw: str) -> str:
        s = (raw or "").strip()
        if not s:
            raise ValueError("Empty image input")
        if cls._is_remote_url(s):
            return s
        if cls._is_data_url(s):
            _prefix, sep, b64 = s.partition(";base64,")
            if not sep:
                raise ValueError("Invalid data:image URL (expected ;base64,)")
            return b64.strip()
        if cls._looks_like_local_path(s):
            return cls._read_local_as_b64(s)
        return s

    @staticmethod
    def _size_to_aspect_ratio(size: Optional[str]) -> str:
        if not size:
            return "16:9"
        normalized = str(size).strip().lower().replace(" ", "").replace("*", "x")
        direct = {
            "1024x1024": "1:1",
            "1024x768": "4:3",
            "768x1024": "3:4",
            "1280x720": "16:9",
            "720x1280": "9:16",
            "1152x896": "4:3",
            "896x1152": "3:4",
            "1536x672": "21:9",
        }
        if normalized in direct:
            return direct[normalized]
        if "x" in normalized:
            try:
                w_s, h_s = normalized.split("x", 1)
                w, h = float(w_s), float(h_s)
                if w <= 0 or h <= 0:
                    return "16:9"
                r = w / h
                candidates = {
                    "16:9": 16 / 9,
                    "9:16": 9 / 16,
                    "1:1": 1.0,
                    "4:3": 4 / 3,
                    "3:4": 3 / 4,
                    "3:2": 3 / 2,
                    "2:3": 2 / 3,
                    "21:9": 21 / 9,
                }
                best = min(candidates.items(), key=lambda kv: abs(kv[1] - r))
                return best[0]
            except (ValueError, ZeroDivisionError):
                pass
        if normalized in _KLING_ASPECT_RATIOS:
            return normalized
        return "16:9"

    def _resolved_model_name(self) -> str:
        return (self.model_name or self.DEFAULT_MODEL).strip() or self.DEFAULT_MODEL

    def _init_provider(self) -> LLMHTTPHandler:
        api_key = self.api_key or os.getenv("KLING_IMAGE_API_KEY", "") or os.getenv("IMAGE_API_KEY", "")
        if not api_key:
            raise ValueError(
                "Kling image API key not found. Set KLING_IMAGE_API_KEY / IMAGE_API_KEY "
                "or pass api_key in config."
            )
        base_url = self.base_url or os.getenv("KLING_IMAGE_BASE_URL", "https://api-beijing.klingai.com")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        return LLMHTTPHandler(
            base_url=self.base_url,
            api_key=api_key,
            model_name=self._resolved_model_name(),
            timeout=int(self.kwargs.get("timeout", 120)),
            max_retries=int(self.kwargs.get("max_retries", 3)),
        )

    def _init_async_provider(self) -> LLMHTTPHandler:
        return self.provider if self.need_sync else self._init_provider()

    def _http_get_sync(self, path: str) -> Dict[str, Any]:
        assert self.provider is not None
        url = f"{self.provider.base_url}/{path.lstrip('/')}"
        headers = dict(self.provider.headers)
        resp = requests.get(url, headers=headers, timeout=self.provider.timeout)
        resp.raise_for_status()
        return resp.json()

    async def _http_get_async(self, path: str) -> Dict[str, Any]:
        import aiohttp

        assert self.async_provider is not None
        url = f"{self.async_provider.base_url}/{path.lstrip('/')}"
        headers = dict(self.async_provider.headers)
        timeout = aiohttp.ClientTimeout(total=self.async_provider.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.json()

    def _poll_task_sync(
        self,
        task_id: str,
        status_path_prefix: str,
        poll_interval: float,
        poll_timeout: float,
        model: str,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + poll_timeout
        attempt = 0
        while True:
            attempt += 1
            body = self._http_get_sync(f"{status_path_prefix}/{task_id}")
            _check_kling_body(body, model)
            data = body.get("data") or {}
            status = (data.get("task_status") or "").lower()
            logger.info(f"[KlingImageProvider] poll #{attempt} task={task_id} status={status}")
            if status in ("succeed", "success"):
                return data
            if status == "failed":
                msg = data.get("task_status_msg") or "task failed"
                raise LLMResponseError(f"Kling image task failed: {msg}", model, body)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Kling image task {task_id} did not finish within {poll_timeout}s")
            time.sleep(min(poll_interval, remaining))

    async def _poll_task_async(
        self,
        task_id: str,
        status_path_prefix: str,
        poll_interval: float,
        poll_timeout: float,
        model: str,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + poll_timeout
        attempt = 0
        while True:
            attempt += 1
            body = await self._http_get_async(f"{status_path_prefix}/{task_id}")
            _check_kling_body(body, model)
            data = body.get("data") or {}
            status = (data.get("task_status") or "").lower()
            logger.info(f"[KlingImageProvider] async poll #{attempt} task={task_id} status={status}")
            if status in ("succeed", "success"):
                return data
            if status == "failed":
                msg = data.get("task_status_msg") or "task failed"
                raise LLMResponseError(f"Kling image task failed: {msg}", model, body)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Kling image task {task_id} did not finish within {poll_timeout}s")
            await asyncio.sleep(min(poll_interval, remaining))

    def _extract_first_image_url(self, data: Dict[str, Any]) -> str:
        tr = data.get("task_result") or {}
        images = tr.get("images") or []
        if not images:
            raise LLMResponseError("No images in Kling task result", self.model_name, data)
        url = (images[0] or {}).get("url")
        if not url:
            raise LLMResponseError("Kling task result missing image url", self.model_name, data)
        return url

    def _build_model_response(
        self,
        image_url: str,
        raw: Dict[str, Any],
        output_format: str,
        output_path: Optional[str],
    ) -> ModelResponse:
        rid = raw.get("request_id") or f"kling-img-{uuid.uuid4().hex[:12]}"
        resp = ModelResponse(
            id=rid,
            model=self._resolved_model_name(),
            content="",
            usage={"output_format": output_format, "provider": "kling_image"},
            finish_reason="success",
            raw_response=raw,
        )
        resp.image_url = image_url
        resp.image_format = output_format
        resp.output_path = output_path
        return resp

    def _pop_kling_extras(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        keys = (
            "n",
            "aspect_ratio",
            "resolution",
            "image_reference",
            "image_fidelity",
            "human_fidelity",
            "element_list",
            "scene_image",
            "style_image",
            "watermark_info",
            "callback_url",
            "external_task_id",
            "poll_interval",
            "poll_timeout",
        )
        out: Dict[str, Any] = {}
        for k in keys:
            if k in kwargs:
                v = kwargs.pop(k)
                if v is not None:
                    out[k] = v
        return out

    def _run_generation_pipeline(
        self,
        *,
        create_endpoint: str,
        status_prefix: str,
        payload: Dict[str, Any],
        poll_interval: float,
        poll_timeout: float,
        output_format: str,
        output_path: Optional[str],
        raw_wrap: Optional[Dict[str, Any]] = None,
    ) -> ModelResponse:
        if not self.provider:
            raise RuntimeError("Sync provider not initialized.")
        body = self._kling_sync_post_json(create_endpoint, payload)
        _check_kling_body(body, self.model_name)
        data = body.get("data") or {}
        task_id = data.get("task_id")
        if not task_id:
            raise LLMResponseError("Missing task_id in Kling response", self.model_name, body)
        final = self._poll_task_sync(
            task_id, status_prefix, poll_interval, poll_timeout, self.model_name
        )
        image_url = self._extract_first_image_url(final)
        merged = {"create": body, "final": {"code": 0, "data": final}}
        if raw_wrap:
            merged.update(raw_wrap)
        return self._build_model_response(image_url, merged, output_format, output_path)

    async def _arun_generation_pipeline(
        self,
        *,
        create_endpoint: str,
        status_prefix: str,
        payload: Dict[str, Any],
        poll_interval: float,
        poll_timeout: float,
        output_format: str,
        output_path: Optional[str],
        raw_wrap: Optional[Dict[str, Any]] = None,
    ) -> ModelResponse:
        if not self.async_provider:
            raise RuntimeError("Async provider not initialized.")
        body = await self._kling_async_post_json(create_endpoint, payload)
        _check_kling_body(body, self.model_name)
        data = body.get("data") or {}
        task_id = data.get("task_id")
        if not task_id:
            raise LLMResponseError("Missing task_id in Kling response", self.model_name, body)
        final = await self._poll_task_async(
            task_id, status_prefix, poll_interval, poll_timeout, self.model_name
        )
        image_url = self._extract_first_image_url(final)
        merged = {"create": body, "final": {"code": 0, "data": final}}
        if raw_wrap:
            merged.update(raw_wrap)
        return self._build_model_response(image_url, merged, output_format, output_path)

    def generate_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        size: Optional[str] = None,
        response_format: Optional[str] = None,
        output_format: Optional[str] = None,
        output_compression: Optional[int] = None,
        seed: Optional[int] = None,
        user: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs,
    ) -> ModelResponse:
        del output_compression, seed, user
        if not self.provider:
            raise RuntimeError("Sync provider not initialized (sync_enabled=True required).")
        if not (prompt or "").strip():
            raise ValueError("Prompt parameter is required and cannot be empty")

        kw = dict(kwargs)
        extra = self._pop_kling_extras(kw)
        images = self._coerce_image_inputs(**kw)

        poll_interval = float(extra.pop("poll_interval", _DEFAULT_POLL_INTERVAL))
        poll_timeout = float(extra.pop("poll_timeout", _DEFAULT_POLL_TIMEOUT))
        n = int(extra.pop("n", 1))
        aspect_ratio = extra.pop("aspect_ratio", None) or self._size_to_aspect_ratio(size)
        resolution = extra.pop("resolution", "1k")
        model_name = self._resolved_model_name()

        output_format = output_format or "png"
        response_format = response_format or "url"

        try:
            if len(images) >= 2:
                if len(images) > 4:
                    raise ValueError(
                        "Kling multi-image2image supports at most 4 subject images in subject_image_list."
                    )
                self._discard_multi_image_incompatible_extra(extra)
                multi_model = self._resolve_model_for_multi_image2image(self._resolved_model_name())
                subject_list = [{"subject_image": self._normalize_image_ref(x)} for x in images]
                payload = {
                    "model_name": multi_model,
                    "prompt": prompt,
                    "subject_image_list": subject_list,
                    "n": n,
                    "aspect_ratio": aspect_ratio,
                }
                if extra.get("scene_image"):
                    payload["scene_image"] = self._normalize_image_ref(str(extra.pop("scene_image")))
                if extra.get("style_image"):
                    payload["style_image"] = self._normalize_image_ref(str(extra.pop("style_image")))
                for k in ("watermark_info", "callback_url", "external_task_id"):
                    if k in extra and extra[k] is not None:
                        payload[k] = extra.pop(k)
                if extra:
                    logger.warning(
                        "[KlingImageProvider] ignoring unsupported multi-image2image fields: %s",
                        list(extra.keys()),
                    )
                return self._run_generation_pipeline(
                    create_endpoint="/v1/images/multi-image2image",
                    status_prefix="/v1/images/multi-image2image",
                    payload=payload,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    output_format=output_format,
                    output_path=output_path,
                )

            payload = {
                "model_name": model_name,
                "prompt": prompt,
                "n": n,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
            }
            if len(images) == 1:
                payload["image"] = self._normalize_image_ref(images[0])
            else:
                if negative_prompt:
                    payload["negative_prompt"] = negative_prompt

            for key in ("image_reference", "image_fidelity", "human_fidelity", "element_list"):
                if key in extra and extra[key] is not None:
                    payload[key] = extra.pop(key)

            for k in ("watermark_info", "callback_url", "external_task_id"):
                if k in extra and extra[k] is not None:
                    payload[k] = extra.pop(k)

            if extra:
                payload.update(extra)

            return self._run_generation_pipeline(
                create_endpoint="/v1/images/generations",
                status_prefix="/v1/images/generations",
                payload=payload,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
                output_format=output_format,
                output_path=output_path,
                raw_wrap={"response_format": response_format},
            )
        except LLMResponseError:
            raise
        except Exception as e:
            logger.error(f"[KlingImageProvider] generate_image failed: {e}\n{traceback.format_exc()}")
            raise LLMResponseError(str(e), "kling_image", None)

    async def agenerate_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        size: Optional[str] = None,
        response_format: Optional[str] = None,
        output_format: Optional[str] = None,
        output_compression: Optional[int] = None,
        seed: Optional[int] = None,
        user: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs,
    ) -> ModelResponse:
        del output_compression, seed, user
        if not self.async_provider:
            raise RuntimeError("Async provider not initialized (async_enabled=True required).")
        if not (prompt or "").strip():
            raise ValueError("Prompt parameter is required and cannot be empty")

        kw = dict(kwargs)
        extra = self._pop_kling_extras(kw)
        images = self._coerce_image_inputs(**kw)

        poll_interval = float(extra.pop("poll_interval", _DEFAULT_POLL_INTERVAL))
        poll_timeout = float(extra.pop("poll_timeout", _DEFAULT_POLL_TIMEOUT))
        n = int(extra.pop("n", 1))
        aspect_ratio = extra.pop("aspect_ratio", None) or self._size_to_aspect_ratio(size)
        resolution = extra.pop("resolution", "1k")
        model_name = self._resolved_model_name()

        output_format = output_format or "png"
        response_format = response_format or "url"

        try:
            if len(images) >= 2:
                if len(images) > 4:
                    raise ValueError(
                        "Kling multi-image2image supports at most 4 subject images in subject_image_list."
                    )
                self._discard_multi_image_incompatible_extra(extra)
                multi_model = self._resolve_model_for_multi_image2image(self._resolved_model_name())
                subject_list = [{"subject_image": self._normalize_image_ref(x)} for x in images]
                payload = {
                    "model_name": multi_model,
                    "prompt": prompt,
                    "subject_image_list": subject_list,
                    "n": n,
                    "aspect_ratio": aspect_ratio,
                }
                if extra.get("scene_image"):
                    payload["scene_image"] = self._normalize_image_ref(str(extra.pop("scene_image")))
                if extra.get("style_image"):
                    payload["style_image"] = self._normalize_image_ref(str(extra.pop("style_image")))
                for k in ("watermark_info", "callback_url", "external_task_id"):
                    if k in extra and extra[k] is not None:
                        payload[k] = extra.pop(k)
                if extra:
                    logger.warning(
                        "[KlingImageProvider] ignoring unsupported multi-image2image fields: %s",
                        list(extra.keys()),
                    )
                return await self._arun_generation_pipeline(
                    create_endpoint="/v1/images/multi-image2image",
                    status_prefix="/v1/images/multi-image2image",
                    payload=payload,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    output_format=output_format,
                    output_path=output_path,
                )

            payload = {
                "model_name": model_name,
                "prompt": prompt,
                "n": n,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
            }
            if len(images) == 1:
                payload["image"] = self._normalize_image_ref(images[0])
            else:
                if negative_prompt:
                    payload["negative_prompt"] = negative_prompt

            for key in ("image_reference", "image_fidelity", "human_fidelity", "element_list"):
                if key in extra and extra[key] is not None:
                    payload[key] = extra.pop(key)

            for k in ("watermark_info", "callback_url", "external_task_id"):
                if k in extra and extra[k] is not None:
                    payload[k] = extra.pop(k)

            if extra:
                payload.update(extra)

            return await self._arun_generation_pipeline(
                create_endpoint="/v1/images/generations",
                status_prefix="/v1/images/generations",
                payload=payload,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
                output_format=output_format,
                output_path=output_path,
                raw_wrap={"response_format": response_format},
            )
        except LLMResponseError:
            raise
        except Exception as e:
            logger.error(f"[KlingImageProvider] agenerate_image failed: {e}\n{traceback.format_exc()}")
            raise LLMResponseError(str(e), "kling_image", None)

    def completion(self, messages, temperature: float = 0.0, max_tokens=None, stop=None, context=None, **kwargs):
        raise NotImplementedError("KlingImageProvider does not support completion(); use generate_image().")

    def postprocess_response(self, response: Any) -> ModelResponse:
        raise NotImplementedError("KlingImageProvider uses generate_image / agenerate_image.")
