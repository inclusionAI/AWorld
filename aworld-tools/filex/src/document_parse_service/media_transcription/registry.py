"""Backend registry for media parsing."""

from __future__ import annotations

from typing import Any

from .backend import MediaTranscriptionBackend
from .local_backend import LocalMediaTranscriptionBackend
from .openai_compatible_backend import OpenAICompatibleMediaTranscriptionBackend


class MediaTranscriptionBackendRegistry:
    """Creates media backends from env_content and parse options."""

    @classmethod
    def create(
        cls,
        *,
        env_content: dict[str, Any] | None = None,
    ) -> tuple[MediaTranscriptionBackend, dict[str, Any]]:
        options = cls.resolve_options(env_content)
        backend_name = str(
            options.get("backend")
            or (env_content or {}).get("media_parse_backend")
            or "local"
        ).strip().lower()

        if backend_name in {"local", "local_whisper", "faster_whisper"}:
            return LocalMediaTranscriptionBackend(), options
        if backend_name in {"openai", "openai_compatible", "openai_chat_completions"}:
            return OpenAICompatibleMediaTranscriptionBackend(), options
        raise ValueError(f"Unsupported media_parse_backend: {backend_name}")

    @staticmethod
    def resolve_options(env_content: dict[str, Any] | None = None) -> dict[str, Any]:
        raw_env = env_content or {}
        raw_options = raw_env.get("media_parse_options") or {}
        if not isinstance(raw_options, dict):
            raise ValueError("media_parse_options must be an object")

        options = dict(raw_options)
        if "media_parse_backend" in raw_env and "backend" not in options:
            options["backend"] = raw_env["media_parse_backend"]
        for source_key, option_key in {
            "media_parse_base_url": "base_url",
            "media_parse_endpoint": "endpoint",
            "media_parse_api_key": "api_key",
            "media_parse_auth_token": "auth_token",
            "media_parse_bearer_token": "bearer_token",
            "media_parse_authorization": "authorization",
            "media_parse_model": "model",
            "media_parse_file_url": "file_url",
            "media_parse_file_id": "file_id",
            "media_parse_session_id": "session_id",
            "media_parse_trace_id": "trace_id",
            "gateway_auth_token": "gateway_auth_token",
            "gateway_authorization": "authorization",
        }.items():
            if source_key in raw_env and option_key not in options:
                options[option_key] = raw_env[source_key]
        gateway_vllm = raw_env.get("gateway_vllm") or {}
        if isinstance(gateway_vllm, dict):
            for source_key, option_key in {
                "model_name": "model",
                "model": "model",
                "base_url": "base_url",
                "api_key": "api_key",
            }.items():
                if source_key in gateway_vllm and option_key not in options:
                    options[option_key] = gateway_vllm[source_key]
        return options
