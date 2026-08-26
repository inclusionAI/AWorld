"""OpenAI-compatible Chat Completions media backend."""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from .models import TranscriptResult


class OpenAICompatibleMediaError(RuntimeError):
    """HTTP failure returned by an OpenAI-compatible media endpoint."""

    def __init__(
        self,
        *,
        status: int,
        response: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.status = status
        self.response = response
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            "openai_compatible media backend failed: "
            f"status={status} response={response[:1000]}"
        )


class OpenAICompatibleMediaTranscriptionBackend:
    """Send media to an OpenAI-compatible multimodal chat endpoint."""

    name = "openai_compatible"

    async def transcribe(
        self,
        file_path: Path,
        *,
        media_type: str,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
    ) -> TranscriptResult:
        endpoint = self._resolve_endpoint(options)
        model = self._resolve_model(options)
        timeout_seconds = float(options.get("timeout_seconds") or options.get("timeout") or 300)
        payload = self._build_payload(
            file_path=file_path,
            media_type=media_type,
            file_type=file_type,
            source_file_name=source_file_name,
            model=model,
            options=options,
        )

        headers = self._build_headers(options)

        try:
            import aiohttp
        except ImportError as exc:
            raise RuntimeError(
                "openai_compatible media backend requires aiohttp. "
                "Install aiohttp or use media_parse_backend='local'."
            ) from exc

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session:
            async with session.post(endpoint, headers=headers, json=payload) as response:
                raw = await response.text()
                if response.status >= 400:
                    raise OpenAICompatibleMediaError(
                        status=response.status,
                        response=raw,
                        retry_after_seconds=self._parse_retry_after(
                            response.headers.get("Retry-After")
                        ),
                    )
                data = await response.json(content_type=None)

        text = self._extract_message_text(data)
        return TranscriptResult(
            text=text,
            backend=self.name,
            model=model,
            metadata={
                "endpoint": endpoint,
                "media_type": media_type,
                "file_type": file_type,
                "source_file_name": source_file_name,
                "usage": data.get("usage", {}),
                "finish_reason": self._extract_finish_reason(data),
            },
        )

    def _build_payload(
        self,
        *,
        file_path: Path,
        media_type: str,
        file_type: str,
        source_file_name: str,
        model: str,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = str(
            options.get("prompt")
            or (
                "请解析这个媒体文件，输出可读的 Markdown。"
                "如果是音频，请转写主要语音内容；如果是视频，请结合画面和声音总结内容；"
                "如果是图片，请提取标题、正文、表格、图表和关键视觉信息，"
                "并尽量保留层级结构、时间点、标题、文字和表格信息。"
            )
        )
        content = [{"type": "text", "text": prompt}]
        content.append(
            self._build_media_content_item(
                file_path=file_path,
                media_type=media_type,
                file_type=file_type,
                media_url=str(options.get("media_url") or "").strip(),
                item_type=str(options.get("media_content_type") or "").strip(),
            )
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
        }
        extra_body = options.get("extra_body") or {}
        if extra_body:
            if not isinstance(extra_body, dict):
                raise ValueError("openai_compatible extra_body must be an object")
            payload.update(extra_body)
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        if "max_tokens" in options:
            payload["max_tokens"] = options["max_tokens"]
        if "stream" in options:
            payload["stream"] = bool(options["stream"])
        return payload

    def _build_media_content_item(
        self,
        *,
        file_path: Path,
        media_type: str,
        file_type: str,
        media_url: str,
        item_type: str,
    ) -> dict[str, Any]:
        resolved_type = item_type or self._default_content_type(media_type)
        if media_url:
            return self._url_content_item(resolved_type, media_url, file_type)

        mime_type = mimetypes.guess_type(file_path.name)[0] or f"{media_type}/{file_type}"
        data = base64.b64encode(file_path.read_bytes()).decode("ascii")

        if resolved_type == "input_audio":
            return {
                "type": "input_audio",
                "input_audio": {
                    "data": data,
                    "format": file_type,
                },
            }

        data_url = f"data:{mime_type};base64,{data}"
        return self._url_content_item(resolved_type, data_url, file_type)

    @staticmethod
    def _default_content_type(media_type: str) -> str:
        if media_type == "audio":
            return "input_audio"
        if media_type == "image":
            return "image_url"
        return "video_url"

    @staticmethod
    def _url_content_item(item_type: str, url: str, file_type: str) -> dict[str, Any]:
        if item_type == "file_url":
            return {"type": "file_url", "file_url": {"url": url}}
        if item_type == "image_url":
            return {"type": "image_url", "image_url": {"url": url}}
        if item_type == "audio_url":
            return {"type": "audio_url", "audio_url": {"url": url}}
        if item_type == "input_audio":
            return {"type": "audio_url", "audio_url": {"url": url, "format": file_type}}
        return {"type": item_type or "video_url", item_type or "video_url": {"url": url}}

    @staticmethod
    def _resolve_endpoint(options: dict[str, Any]) -> str:
        explicit_endpoint = str(options.get("endpoint") or "").strip()
        if explicit_endpoint:
            return explicit_endpoint
        base_url = str(
            options.get("base_url")
            or os.getenv("GATEWAY_VLLM_BASE_URL")
            or os.getenv("OPENAI_COMPATIBLE_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        return f"{base_url}/chat/completions"

    @staticmethod
    def _resolve_api_key(options: dict[str, Any]) -> str:
        return str(
            options.get("api_key")
            or os.getenv("GATEWAY_VLLM_API_KEY")
            or os.getenv("OPENAI_COMPATIBLE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()

    @staticmethod
    def _build_headers(options: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        extra_headers = options.get("headers") or {}
        if extra_headers:
            if not isinstance(extra_headers, dict):
                raise ValueError("media_parse_options.headers must be an object")
            headers.update({str(key): str(value) for key, value in extra_headers.items()})

        if not any(key.lower() == "authorization" for key in headers):
            authorization = OpenAICompatibleMediaTranscriptionBackend._resolve_authorization(options)
            if authorization:
                headers["Authorization"] = authorization
        return headers

    @staticmethod
    def _resolve_authorization(options: dict[str, Any]) -> str:
        explicit_authorization = str(
            options.get("authorization")
            or options.get("auth_header")
            or options.get("authHeader")
            or ""
        ).strip()
        if explicit_authorization:
            return explicit_authorization

        auth_token = str(
            options.get("auth_token")
            or options.get("authToken")
            or options.get("bearer_token")
            or OpenAICompatibleMediaTranscriptionBackend._resolve_api_key(options)
            or ""
        ).strip()
        if not auth_token:
            return ""
        if auth_token.lower().startswith("bearer "):
            return auth_token
        return f"Bearer {auth_token}"

    @staticmethod
    def _resolve_model(options: dict[str, Any]) -> str:
        model = str(
            options.get("model")
            or os.getenv("GATEWAY_VLLM_MODEL_NAME")
            or os.getenv("OPENAI_COMPATIBLE_MEDIA_MODEL")
            or os.getenv("OPENAI_MODEL")
            or ""
        ).strip()
        if not model:
            raise ValueError(
                "openai_compatible media backend requires a model. "
                "Set media_parse_options.model or OPENAI_COMPATIBLE_MEDIA_MODEL."
            )
        return model

    @staticmethod
    def _extract_message_text(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
                elif item:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(content or "").strip()

    @staticmethod
    def _extract_finish_reason(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        return str(choices[0].get("finish_reason") or "")

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            return max(0.0, float(normalized))
        except ValueError:
            return None
