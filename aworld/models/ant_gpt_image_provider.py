# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Ant GPT Image Provider — MatrixCube gateway adapter for GPT Image models.

All requests are routed through ``POST /v1/genericCall`` on the Ant gateway
(e.g. ``https://matrixcube.alipay.com``). The upstream vendor method is passed
in the JSON/multipart body:

- Text-to-image: ``method=/images/generations`` with ``model``, ``prompt``, ...
- Image edit: ``method=/images/edits`` (multipart) with ``model``, ``prompt``,
  ``image``, ``quality``, ...

Configure with ``provider: ant_gpt_image`` in model config (see ``aworld.json``).
"""

from __future__ import annotations

import base64
import mimetypes
import os
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from aworld.core.llm_provider import LLMProviderBase
from aworld.logs.util import logger
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import LLMResponseError, ModelResponse

_GENERIC_CALL_ENDPOINT = "/v1/genericCall"
_METHOD_GENERATIONS = "/images/generations"
_METHOD_EDITS = "/images/edits"
_DEFAULT_BASE_URL = "https://matrixcube.alipay.com"


class AntGptImageProvider(LLMProviderBase):
    """GPT Image provider (``gpt-image-2``) via Ant MatrixCube ``genericCall`` gateway."""

    DEFAULT_MODEL = "gpt-image-2"
    DEFAULT_SIZE = "auto"
    DEFAULT_QUALITY = "auto"
    DEFAULT_FORMAT = "png"
    DEFAULT_MODERATION = "auto"
    DEFAULT_BACKGROUND = "auto"

    SUPPORTED_SIZES = [
        "auto",
        "1024x1024",
        "1536x1024",
        "1024x1536",
        "2048x2048",
        "2048x1152",
        "3840x2160",
        "2160x3840",
    ]
    SUPPORTED_QUALITIES = ["auto", "low", "medium", "high"]
    SUPPORTED_FORMATS = ["png", "jpeg", "webp"]
    SUPPORTED_MODERATIONS = ["auto", "low"]
    SUPPORTED_BACKGROUNDS = ["auto", "opaque"]

    UNSUPPORTED_PASSTHROUGH_KEYS = frozenset(
        {
            "negative_prompt",
            "seed",
            "response_format",
            "watermark",
            "prompt_extend",
            "num_inference_steps",
            "guidance_scale",
            "strength",
            "input_fidelity",
        }
    )

    # ImageAgent / caller aliases — never forward to the gateway JSON body.
    AGENT_INPUT_ALIASES = frozenset(
        {
            "image",
            "image_url",
            "image_urls",
            "images",
            "input_image",
            "input_images",
            "reference_images",
            "image_paths",
            "input_images_urls",
            "mask",
            "mask_url",
            "mask_path",
        }
    )

    def _resolved_model_name(self) -> str:
        return (self.model_name or self.DEFAULT_MODEL).strip() or self.DEFAULT_MODEL

    @staticmethod
    def _normalize_size(size: Optional[str]) -> Optional[str]:
        if not size:
            return size
        normalized = str(size).strip().lower().replace(" ", "")
        return normalized.replace("*", "x")

    @staticmethod
    def _coerce_image_inputs(
        image: Optional[str] = None,
        image_urls: Optional[List[str]] = None,
        input_image: Optional[str] = None,
        input_images: Optional[List[str]] = None,
        reference_images: Optional[List[str]] = None,
        **kwargs,
    ) -> List[str]:
        images: List[str] = []

        def add(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    images.append(normalized)
                return
            if isinstance(value, list):
                for item in value:
                    add(item)

        add(image)
        add(image_urls)
        add(input_image)
        add(input_images)
        add(reference_images)
        add(kwargs.get("images"))
        add(kwargs.get("image_url"))
        add(kwargs.get("image_paths"))
        add(kwargs.get("input_images_urls"))

        deduped: List[str] = []
        seen = set()
        for item in images:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    @staticmethod
    def _is_remote_url(value: str) -> bool:
        normalized = (value or "").strip().lower()
        return normalized.startswith("https://") or normalized.startswith("http://")

    @staticmethod
    def _is_data_url(value: str) -> bool:
        return (value or "").strip().lower().startswith("data:image/")

    @staticmethod
    def _looks_like_local_path(value: str) -> bool:
        normalized = (value or "").strip()
        if not normalized:
            return False
        if normalized.startswith(("http://", "https://", "data:image/", "file://")):
            return False
        return (
            normalized.startswith(("/", "./", "../", "~/"))
            or os.path.exists(os.path.expanduser(normalized))
        )

    @staticmethod
    def _read_local_image_file(value: str) -> Dict[str, Any]:
        normalized = (value or "").strip()
        if normalized.startswith("file://"):
            normalized = normalized[7:]
        path = Path(os.path.expanduser(normalized))
        if not path.exists() or not path.is_file():
            raise ValueError(f"Local image file not found: {value}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return {
            "filename": path.name,
            "content": path.read_bytes(),
            "content_type": content_type,
        }

    @staticmethod
    def _read_data_url_image(value: str) -> Dict[str, Any]:
        normalized = (value or "").strip()
        if "," not in normalized:
            raise ValueError("Invalid data:image URL")
        header, encoded = normalized.split(",", 1)
        content_type = "image/png"
        if ";" in header:
            content_type = header.split(";", 1)[0].replace("data:", "", 1) or content_type
        ext = mimetypes.guess_extension(content_type) or ".png"
        filename = f"input{ext}"
        return {
            "filename": filename,
            "content": base64.b64decode(encoded),
            "content_type": content_type,
        }

    @classmethod
    def _fetch_remote_image(cls, url: str) -> Dict[str, Any]:
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            content_type = mimetypes.guess_type(url)[0] or "application/octet-stream"
        ext = mimetypes.guess_extension(content_type) or ".png"
        filename = Path(url.split("?", 1)[0]).name or f"input{ext}"
        if "." not in filename:
            filename = f"{filename}{ext}"
        return {
            "filename": filename,
            "content": response.content,
            "content_type": content_type,
        }

    @classmethod
    def _resolve_image_file(cls, value: str) -> Dict[str, Any]:
        if cls._is_data_url(value):
            return cls._read_data_url_image(value)
        if cls._is_remote_url(value):
            return cls._fetch_remote_image(value)
        if cls._looks_like_local_path(value):
            return cls._read_local_image_file(value)
        raise ValueError(
            "Unsupported image input. Provide a remote HTTP(S) URL, local file path, or data:image/... input."
        )

    @classmethod
    def _pop_agent_input_aliases(cls, extra_kwargs: Dict[str, Any]) -> None:
        for key in cls.AGENT_INPUT_ALIASES:
            extra_kwargs.pop(key, None)

    def _resolve_edit_inputs(self, images: List[str]) -> List[Dict[str, Any]]:
        if not images:
            raise ValueError(
                "Image edit requests require at least one input image. "
                "Provide image_url/image_urls/input_image/input_images with a remote URL, local file path, or data:image/... input."
            )
        return [self._resolve_image_file(candidate) for candidate in images]

    def _resolve_mask_input(self, extra_kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mask_value = extra_kwargs.pop("mask", None)
        if mask_value is None:
            mask_value = extra_kwargs.pop("mask_url", None)
        if mask_value is None:
            mask_value = extra_kwargs.pop("mask_path", None)
        if not mask_value:
            return None
        return self._resolve_image_file(str(mask_value).strip())

    @staticmethod
    def _map_output_format(output_format: Optional[str], extra_kwargs: Dict[str, Any]) -> str:
        fmt = output_format or extra_kwargs.pop("output_format", None) or extra_kwargs.pop("format", None)
        fmt = fmt or AntGptImageProvider.DEFAULT_FORMAT
        return str(fmt).strip().lower()

    @staticmethod
    def _map_quality(extra_kwargs: Dict[str, Any], *, default: str = DEFAULT_QUALITY) -> str:
        quality = extra_kwargs.pop("quality", None) or default
        return str(quality).strip().lower()

    @staticmethod
    def _map_moderation(extra_kwargs: Dict[str, Any]) -> str:
        moderation = extra_kwargs.pop("moderation", None) or AntGptImageProvider.DEFAULT_MODERATION
        return str(moderation).strip().lower()

    @staticmethod
    def _map_background(extra_kwargs: Dict[str, Any]) -> str:
        background = extra_kwargs.pop("background", None) or AntGptImageProvider.DEFAULT_BACKGROUND
        background = str(background).strip().lower()
        if background == "transparent":
            logger.warning(
                "[AntGptImageProvider] gpt-image-2 does not support background=transparent; using auto instead."
            )
            return "auto"
        return background

    @staticmethod
    def _apply_optional_generation_fields(
        payload: Dict[str, Any],
        *,
        size: str,
        output_format: str,
        output_compression: Optional[int],
        extra_kwargs: Dict[str, Any],
    ) -> None:
        size_norm = AntGptImageProvider._normalize_size(size)
        if size_norm and size_norm != "auto":
            payload["size"] = size_norm

        quality = AntGptImageProvider._map_quality(extra_kwargs)
        if quality and quality != "auto":
            payload["quality"] = quality

        if output_format and output_format != AntGptImageProvider.DEFAULT_FORMAT:
            payload["output_format"] = output_format

        moderation = AntGptImageProvider._map_moderation(extra_kwargs)
        if moderation and moderation != "auto":
            payload["moderation"] = moderation

        background = AntGptImageProvider._map_background(extra_kwargs)
        if background and background != "auto":
            payload["background"] = background

        if output_compression is not None:
            payload["output_compression"] = output_compression

        passthrough_keys = ("n", "partial_images", "stream", "user")
        for key in passthrough_keys:
            value = extra_kwargs.pop(key, None)
            if value is not None:
                payload[key] = value

    def _build_generation_payload(
        self,
        *,
        prompt: str,
        size: str,
        output_format: str,
        output_compression: Optional[int],
        extra_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self._resolved_model_name(),
            "method": _METHOD_GENERATIONS,
            "prompt": prompt,
        }
        self._apply_optional_generation_fields(
            payload,
            size=size,
            output_format=output_format,
            output_compression=output_compression,
            extra_kwargs=extra_kwargs,
        )
        for key in list(extra_kwargs.keys()):
            if key in self.UNSUPPORTED_PASSTHROUGH_KEYS or key in self.AGENT_INPUT_ALIASES:
                logger.debug(f"[AntGptImageProvider] Ignoring unsupported parameter: {key}")
                extra_kwargs.pop(key, None)
        if extra_kwargs:
            logger.debug(
                f"[AntGptImageProvider] Forwarding extra generation fields: {list(extra_kwargs.keys())}"
            )
            payload.update(extra_kwargs)
        return payload

    def _build_edit_form_payload(
        self,
        *,
        prompt: str,
        size: str,
        output_format: str,
        output_compression: Optional[int],
        images: List[str],
        extra_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        file_inputs = self._resolve_edit_inputs(images)
        mask_input = self._resolve_mask_input(extra_kwargs)
        self._pop_agent_input_aliases(extra_kwargs)

        quality = self._map_quality(extra_kwargs, default="medium")
        if quality == "auto":
            quality = "medium"

        payload: Dict[str, Any] = {
            "model": self._resolved_model_name(),
            "method": _METHOD_EDITS,
            "prompt": prompt,
            "quality": quality,
            "image": file_inputs[0] if len(file_inputs) == 1 else file_inputs,
        }

        size_norm = self._normalize_size(size)
        if size_norm and size_norm != "auto":
            payload["size"] = size_norm
        if output_format and output_format != self.DEFAULT_FORMAT:
            payload["output_format"] = output_format
        if mask_input is not None:
            payload["mask"] = mask_input
        if output_compression is not None:
            payload["output_compression"] = output_compression

        passthrough_keys = ("n", "user")
        for key in passthrough_keys:
            value = extra_kwargs.pop(key, None)
            if value is not None:
                payload[key] = value
        for key in list(extra_kwargs.keys()):
            if key in self.UNSUPPORTED_PASSTHROUGH_KEYS or key in self.AGENT_INPUT_ALIASES:
                logger.debug(f"[AntGptImageProvider] Ignoring unsupported parameter: {key}")
                extra_kwargs.pop(key, None)
        if extra_kwargs:
            logger.debug(
                f"[AntGptImageProvider] Forwarding extra edit fields: {list(extra_kwargs.keys())}"
            )
            payload.update(extra_kwargs)
        return payload

    def _build_request(
        self,
        *,
        prompt: str,
        size: Optional[str],
        output_format: Optional[str],
        output_compression: Optional[int],
        extra_kwargs: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any], str, str]:
        images = self._coerce_image_inputs(**extra_kwargs)
        size = size or self.DEFAULT_SIZE
        output_format = self._map_output_format(output_format, extra_kwargs)

        if images:
            payload = self._build_edit_form_payload(
                prompt=prompt,
                size=size,
                output_format=output_format,
                output_compression=output_compression,
                images=images,
                extra_kwargs=extra_kwargs,
            )
            return _GENERIC_CALL_ENDPOINT, payload, output_format, "multipart"

        # ImageAgent always passes image_urls=... even for pure text-to-image; strip before JSON body.
        self._pop_agent_input_aliases(extra_kwargs)

        payload = self._build_generation_payload(
            prompt=prompt,
            size=size,
            output_format=output_format,
            output_compression=output_compression,
            extra_kwargs=extra_kwargs,
        )
        return _GENERIC_CALL_ENDPOINT, payload, output_format, "json"

    @staticmethod
    def _unwrap_response_body(response_data: Dict[str, Any]) -> Dict[str, Any]:
        inner = response_data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("data"), list):
            return inner
        return response_data

    def _init_provider(self) -> LLMHTTPHandler:
        api_key = (
            self.api_key
            or os.getenv("TEXT_TO_IMAGE_API_KEY")
            or os.getenv("ANT_GPT_IMAGE_API_KEY")
            or os.getenv("IMAGE_API_KEY", "")
        )
        if not api_key:
            raise ValueError(
                "Ant GPT Image API key not found. Set TEXT_TO_IMAGE_API_KEY, "
                "ANT_GPT_IMAGE_API_KEY, or pass api_key to the constructor."
            )

        base_url = (
            self.base_url
            or os.getenv("TEXT_TO_IMAGE_BASE_URL")
            or os.getenv("ANT_GPT_IMAGE_BASE_URL")
            or os.getenv("IMAGE_BASE_URL", _DEFAULT_BASE_URL)
        )

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

        return LLMHTTPHandler(
            base_url=self.base_url,
            api_key=api_key,
            model_name=self._resolved_model_name(),
            timeout=self.kwargs.get("timeout", 180),
            max_retries=self.kwargs.get("max_retries", 3),
        )

    def _init_async_provider(self) -> LLMHTTPHandler:
        return self.provider if self.need_sync else self._init_provider()

    @classmethod
    def supported_models(cls) -> list:
        return [
            cls.DEFAULT_MODEL,
            "gpt-image-1.5",
            "gpt-image-1",
            "gpt-image-1-mini",
            "ant_gpt_image",
        ]

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
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Set 'sync_enabled=True' in the constructor."
            )
        if not prompt:
            raise ValueError("Prompt parameter is required and cannot be empty")
        if negative_prompt:
            logger.debug("[AntGptImageProvider] negative_prompt is not supported; ignoring.")
        if seed is not None:
            logger.debug("[AntGptImageProvider] seed is not supported; ignoring.")
        if response_format and response_format != "b64_json":
            logger.debug(
                "[AntGptImageProvider] Gateway returns base64 image data; "
                f"ignoring response_format={response_format!r}."
            )

        extra_kwargs = dict(kwargs)
        if user:
            extra_kwargs.setdefault("user", user)

        endpoint, payload, resolved_format, request_body_type = self._build_request(
            prompt=prompt,
            size=size,
            output_format=output_format,
            output_compression=output_compression,
            extra_kwargs=extra_kwargs,
        )

        logger.info(
            f"[AntGptImageProvider] Generating image: prompt_length={len(prompt)}, "
            f"method={payload.get('method')}, endpoint={endpoint}"
        )

        try:
            response_data = self.provider.sync_call(
                payload,
                endpoint=endpoint,
                request_body_type=request_body_type,
            )
            return self._parse_image_response(
                response_data,
                output_format=resolved_format,
                output_path=output_path,
            )
        except Exception as exc:
            error_msg = f"Ant GPT Image generation failed: {exc}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, "ant_gpt_image", None) from exc

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
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Set 'async_enabled=True' in the constructor."
            )
        if not prompt:
            raise ValueError("Prompt parameter is required and cannot be empty")
        if negative_prompt:
            logger.debug("[AntGptImageProvider] negative_prompt is not supported; ignoring.")
        if seed is not None:
            logger.debug("[AntGptImageProvider] seed is not supported; ignoring.")
        if response_format and response_format != "b64_json":
            logger.debug(
                "[AntGptImageProvider] Gateway returns base64 image data; "
                f"ignoring response_format={response_format!r}."
            )

        extra_kwargs = dict(kwargs)
        if user:
            extra_kwargs.setdefault("user", user)

        endpoint, payload, resolved_format, request_body_type = self._build_request(
            prompt=prompt,
            size=size,
            output_format=output_format,
            output_compression=output_compression,
            extra_kwargs=extra_kwargs,
        )

        logger.info(
            f"[AntGptImageProvider] Generating image (async): prompt_length={len(prompt)}, "
            f"method={payload.get('method')}, endpoint={endpoint}"
        )

        try:
            response_data = await self.async_provider.async_call(
                payload,
                endpoint=endpoint,
                request_body_type=request_body_type,
            )
            return self._parse_image_response(
                response_data,
                output_format=resolved_format,
                output_path=output_path,
            )
        except Exception as exc:
            error_msg = f"Ant GPT Image generation failed (async): {exc}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, "ant_gpt_image", None) from exc

    def _parse_image_response(
        self,
        response_data: Dict[str, Any],
        output_format: str,
        output_path: Optional[str] = None,
    ) -> ModelResponse:
        if response_data.get("error"):
            error_info = response_data["error"]
            error_msg = error_info.get("message", "Unknown error")
            error_code = error_info.get("code", "unknown")
            logger.error(
                f"[AntGptImageProvider] API error: code={error_code}, message={error_msg}"
            )
            raise LLMResponseError(
                f"Ant GPT Image API error (code {error_code}): {error_msg}",
                "ant_gpt_image",
                response_data,
            )

        code = response_data.get("code")
        if code not in (None, 0):
            message = response_data.get("message", "Unknown error")
            raise LLMResponseError(
                f"Ant gateway error (code={code}): {message}",
                "ant_gpt_image",
                response_data,
            )

        body = self._unwrap_response_body(response_data)
        data_list = body.get("data", [])
        if not data_list:
            raise LLMResponseError(
                "No image data in response",
                "ant_gpt_image",
                response_data,
            )

        image_data = data_list[0]
        b64_data = image_data.get("b64_json")
        image_url = image_data.get("url")
        image_bytes = None

        if b64_data:
            try:
                image_bytes = base64.b64decode(b64_data)
                logger.info(
                    f"[AntGptImageProvider] Image generated successfully: size={len(image_bytes)} bytes"
                )
            except Exception as exc:
                raise LLMResponseError(
                    f"Failed to decode image data: {exc}",
                    "ant_gpt_image",
                    response_data,
                ) from exc
        elif image_url:
            logger.info(f"[AntGptImageProvider] Image generated successfully: url={image_url}")
        else:
            raise LLMResponseError(
                "No image data (b64_json or url) in response",
                "ant_gpt_image",
                response_data,
            )

        resolved_format = output_format
        if isinstance(body, dict):
            resolved_format = body.get("output_format") or output_format

        if output_path and image_bytes:
            try:
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_bytes(image_bytes)
                logger.info(f"[AntGptImageProvider] Image saved to: {output_path}")
            except Exception as exc:
                logger.warning(
                    f"[AntGptImageProvider] Failed to save image to {output_path}: {exc}"
                )

        response_id = (
            (body.get("request_id") if isinstance(body, dict) else None)
            or response_data.get("request_id")
            or response_data.get("id")
            or f"img-{uuid.uuid4().hex[:8]}"
        )
        usage = body.get("usage") if isinstance(body, dict) else None
        response = ModelResponse(
            id=response_id,
            model=self._resolved_model_name(),
            content="",
            usage={
                "image_size": body.get("size") if isinstance(body, dict) else image_data.get("size", "unknown"),
                "output_format": resolved_format,
                "quality": body.get("quality") if isinstance(body, dict) else None,
                "provider": "ant_gpt_image",
                "gateway_usage": usage,
            },
            finish_reason="success",
            raw_response=response_data,
        )
        if image_bytes:
            response.image_data = image_bytes
            response.image_bytes = len(image_bytes)
        if image_url:
            response.image_url = image_url
        response.image_format = resolved_format
        response.output_path = output_path
        return response

    def completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        context: Any = None,
        **kwargs,
    ) -> ModelResponse:
        raise NotImplementedError(
            "AntGptImageProvider is an image generation provider and does not support completion(). "
            "Use generate_image() instead."
        )

    def postprocess_response(self, response: Any) -> ModelResponse:
        raise NotImplementedError(
            "AntGptImageProvider uses custom response processing in generate_image() "
            "and agenerate_image()."
        )
