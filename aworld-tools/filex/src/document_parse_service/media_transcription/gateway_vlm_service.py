"""Shared Gateway VLM configuration and invocation service."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
from pathlib import Path
from typing import Any

from ..filex_config import load_filex_config
from .file_rate_limiter import FileBackedRateLimiter
from .models import TranscriptResult
from .openai_compatible_backend import (
    OpenAICompatibleMediaError,
    OpenAICompatibleMediaTranscriptionBackend,
)


GATEWAY_VLLM_MODEL_ALIASES = {
    "ai_cloud_Kimi_k26_pgc": "kimi_k26_pc",
}

logger = logging.getLogger(__name__)


class GatewayVlmService:
    """Apply Gateway-specific VLM protocol options before invoking the HTTP backend."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        env_content: dict[str, Any] | None = None,
        backend: OpenAICompatibleMediaTranscriptionBackend | None = None,
        rate_limiter: FileBackedRateLimiter | None = None,
    ) -> None:
        self._config = config if config is not None else load_filex_config()
        self._env_content = env_content or {}
        self._backend = backend or OpenAICompatibleMediaTranscriptionBackend()
        self._rate_limiter = rate_limiter or FileBackedRateLimiter()

    async def transcribe(
        self,
        file_path: Path,
        *,
        media_type: str,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
    ) -> TranscriptResult:
        resolved_options = self.build_options(options)
        configured_max_retries = resolved_options.get("max_retries")
        max_retries = max(
            0,
            int(3 if configured_max_retries is None else configured_max_retries),
        )
        retry_count = 0
        retry_wait_ms = 0
        rate_limit_wait_ms = 0
        while True:
            try:
                rate_limit_wait_ms += await self._acquire_rate_limit_slot(resolved_options)
                result = await self._backend.transcribe(
                    file_path,
                    media_type=media_type,
                    file_type=file_type,
                    source_file_name=source_file_name,
                    options=resolved_options,
                )
                if not result.text.strip():
                    usage = result.metadata.get("usage") or {}
                    finish_reason = result.metadata.get("finish_reason") or ""
                    raise RuntimeError(
                        "Gateway VLM returned empty content: "
                        f"finish_reason={finish_reason or '<empty>'} usage={usage}"
                    )
                result.metadata.update(
                    {
                        "model_call_count": retry_count + 1,
                        "model_retry_count": retry_count,
                        "peak_concurrency": 1,
                        "model_retry_wait_ms": retry_wait_ms,
                        "model_rate_limit_wait_ms": rate_limit_wait_ms,
                        "model_wait_ms": retry_wait_ms + rate_limit_wait_ms,
                        "ocr_char_count": len(result.text),
                    }
                )
                return result
            except Exception as exc:  # noqa: BLE001
                if retry_count >= max_retries or not self._is_retryable_error(exc):
                    if retry_count:
                        raise RuntimeError(
                            "Gateway VLM retry exhausted: "
                            f"attempts={retry_count + 1} retries={retry_count} error={exc}"
                        ) from exc
                    raise
                retry_count += 1
                delay_ms = self._retry_delay_ms(
                    retry_count=retry_count,
                    options=resolved_options,
                    error=exc,
                )
                retry_wait_ms += delay_ms
                logger.warning(
                    "Gateway VLM retrying transient failure | retry=%s max_retries=%s "
                    "delay_ms=%s status=%s",
                    retry_count,
                    max_retries,
                    delay_ms,
                    getattr(exc, "status", None),
                )
                await asyncio.sleep(delay_ms / 1000)

    def build_options(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        options: dict[str, Any] = {}
        self._apply_gateway_config(options, self._config.get("gateway_vllm"))
        self._apply_gateway_config(options, self._env_content.get("gateway_vllm"))
        options.update(overrides or {})
        self._normalize_gateway_protocol(options)
        self._apply_paddle_vlm_fallback(options)
        self._apply_environment_overrides(options)
        return options

    @staticmethod
    def _apply_gateway_config(options: dict[str, Any], raw_config: Any) -> None:
        if not isinstance(raw_config, dict):
            return
        for source_key, option_key in {
            "base_url": "base_url",
            "endpoint": "endpoint",
            "api_key": "api_key",
            "auth_token": "auth_token",
            "authorization": "authorization",
            "temperature": "temperature",
            "max_tokens": "max_tokens",
            "max_retries": "max_retries",
            "retry_base_delay_ms": "retry_base_delay_ms",
            "retry_max_delay_ms": "retry_max_delay_ms",
            "retry_jitter_ms": "retry_jitter_ms",
            "requests_per_minute": "requests_per_minute",
        }.items():
            if source_key in raw_config:
                options[option_key] = raw_config[source_key]

        model_name = str(
            raw_config.get("http_model_name")
            or raw_config.get("resolved_model_name")
            or raw_config.get("model")
            or raw_config.get("model_name")
            or ""
        ).strip()
        if model_name:
            options["model"] = GATEWAY_VLLM_MODEL_ALIASES.get(model_name, model_name)

        extra_body = raw_config.get("extra_body") or {}
        if isinstance(extra_body, dict):
            options["extra_body"] = dict(extra_body)

    @staticmethod
    def _normalize_gateway_protocol(options: dict[str, Any]) -> None:
        model_name = str(options.get("model") or "").strip()
        if model_name:
            options["model"] = GATEWAY_VLLM_MODEL_ALIASES.get(model_name, model_name)
        extra_body = dict(options.get("extra_body") or {})
        extra_body.setdefault("enable_maya_new_inference_protocol", True)
        extra_body.setdefault("enable_sec_check", True)
        options["extra_body"] = extra_body

    def _apply_paddle_vlm_fallback(self, options: dict[str, Any]) -> None:
        document_parse = self._config.get("document_parse") or {}
        pdf_config = document_parse.get("pdf") if isinstance(document_parse, dict) else {}
        if not isinstance(pdf_config, dict):
            return
        for option_key, config_key in {
            "base_url": "paddle_ocr_vl_rec_server_url",
            "api_key": "paddle_ocr_vl_rec_api_key",
            "model": "paddle_ocr_vl_rec_api_model_name",
        }.items():
            if not options.get(option_key) and pdf_config.get(config_key):
                options[option_key] = pdf_config[config_key]

    @staticmethod
    def _apply_environment_overrides(options: dict[str, Any]) -> None:
        for env_key, option_key in {
            "FILEX_OCR_VLM_BASE_URL": "base_url",
            "FILEX_OCR_VLM_ENDPOINT": "endpoint",
            "FILEX_OCR_VLM_API_KEY": "api_key",
            "FILEX_OCR_VLM_AUTH_TOKEN": "auth_token",
            "FILEX_OCR_VLM_AUTHORIZATION": "authorization",
            "FILEX_OCR_VLM_MODEL": "model",
        }.items():
            value = os.getenv(env_key)
            if value:
                options[option_key] = value

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        if isinstance(exc, OpenAICompatibleMediaError):
            return exc.status in {408, 409, 425, 429, 500, 502, 503, 504}
        if isinstance(exc, (asyncio.TimeoutError, ConnectionError)):
            return True
        message = str(exc).lower()
        error_class = type(exc).__name__.lower()
        return any(
            signal in message
            for signal in (
                "429",
                "rate limit",
                "rpm_limit",
                "额度超限",
                "限流",
                "timeout",
                "timed out",
                "connection reset",
                "server disconnected",
                "disconnected",
                "finish_reason=length",
                "502",
                "503",
                "504",
            )
        ) or "timeout" in error_class or "connection" in error_class

    @staticmethod
    def _retry_delay_ms(
        *,
        retry_count: int,
        options: dict[str, Any],
        error: Exception,
    ) -> int:
        retry_after = getattr(error, "retry_after_seconds", None)
        if retry_after is not None:
            return max(0, round(float(retry_after) * 1000))
        base_ms = max(0, int(options.get("retry_base_delay_ms") or 500))
        max_ms = max(base_ms, int(options.get("retry_max_delay_ms") or 8000))
        jitter_ms = max(0, int(options.get("retry_jitter_ms") or 0))
        exponential_ms = min(max_ms, base_ms * (2 ** max(0, retry_count - 1)))
        return exponential_ms + random.randint(0, jitter_ms)

    async def _acquire_rate_limit_slot(self, options: dict[str, Any]) -> int:
        requests_per_minute = max(0, int(options.get("requests_per_minute") or 0))
        if requests_per_minute <= 0:
            return 0
        endpoint = OpenAICompatibleMediaTranscriptionBackend._resolve_endpoint(options)
        model = str(options.get("model") or "").strip()
        credential = str(options.get("api_key") or options.get("auth_token") or "").strip()
        credential_digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        return await self._rate_limiter.acquire(
            key=f"{endpoint}|{model}|{credential_digest}",
            requests_per_minute=requests_per_minute,
        )
