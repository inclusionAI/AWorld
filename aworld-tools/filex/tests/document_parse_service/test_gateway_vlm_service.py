import asyncio
import sys
from pathlib import Path

import pytest


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_gateway_vlm_service_normalizes_gateway_protocol_options() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.gateway_vlm_service import GatewayVlmService

    service = GatewayVlmService(
        config={},
        env_content={
            "gateway_vllm": {
                "model_name": "ai_cloud_Kimi_k26_pgc",
                "base_url": "https://antchat.example/v1",
                "api_key": "test-key",
            }
        },
    )

    options = service.build_options({"timeout_seconds": 180})

    assert options["model"] == "kimi_k26_pc"
    assert options["base_url"] == "https://antchat.example/v1"
    assert options["api_key"] == "test-key"
    assert options["timeout_seconds"] == 180
    assert options["extra_body"] == {
        "enable_maya_new_inference_protocol": True,
        "enable_sec_check": True,
    }


def test_gateway_vlm_service_rejects_empty_completion(tmp_path: Path) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.gateway_vlm_service import GatewayVlmService
    from document_parse_service.media_transcription.models import TranscriptResult

    class _EmptyBackend:
        async def transcribe(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return TranscriptResult(
                text="",
                backend="openai_compatible",
                metadata={
                    "finish_reason": "stop",
                    "usage": {"completion_tokens": 0},
                },
            )

    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    service = GatewayVlmService(config={}, backend=_EmptyBackend())

    async def _transcribe() -> None:
        await service.transcribe(
            image_path,
            media_type="image",
            file_type="jpg",
            source_file_name=image_path.name,
            options={"model": "image-model"},
        )

    with pytest.raises(RuntimeError, match="Gateway VLM returned empty content"):
        asyncio.run(_transcribe())


def test_gateway_vlm_service_retries_429_using_retry_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.gateway_vlm_service import GatewayVlmService
    from document_parse_service.media_transcription.models import TranscriptResult
    from document_parse_service.media_transcription.openai_compatible_backend import (
        OpenAICompatibleMediaError,
    )

    class _RateLimitedBackend:
        calls = 0

        async def transcribe(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.calls += 1
            if self.calls == 1:
                raise OpenAICompatibleMediaError(
                    status=429,
                    response='{"code":"RPM_LIMIT"}',
                    retry_after_seconds=1.25,
                )
            return TranscriptResult(text="识别成功", backend="openai_compatible")

    delays = []

    async def _record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _record_sleep)
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    backend = _RateLimitedBackend()
    service = GatewayVlmService(config={}, backend=backend)

    result = asyncio.run(
        service.transcribe(
            image_path,
            media_type="image",
            file_type="jpg",
            source_file_name=image_path.name,
            options={"model": "image-model", "max_retries": 2},
        )
    )

    assert backend.calls == 2
    assert delays == [1.25]
    assert result.metadata["model_call_count"] == 2
    assert result.metadata["model_retry_count"] == 1
    assert result.metadata["peak_concurrency"] == 1
    assert result.metadata["model_retry_wait_ms"] == 1250
    assert result.metadata["model_rate_limit_wait_ms"] == 0
    assert result.metadata["model_wait_ms"] == 1250
    assert result.metadata["ocr_char_count"] == 4


def test_gateway_vlm_service_reports_retry_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.gateway_vlm_service import GatewayVlmService
    from document_parse_service.media_transcription.openai_compatible_backend import (
        OpenAICompatibleMediaError,
    )

    class _AlwaysRateLimitedBackend:
        calls = 0

        async def transcribe(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.calls += 1
            raise OpenAICompatibleMediaError(status=429, response="RPM_LIMIT")

    async def _skip_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _skip_sleep)
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    backend = _AlwaysRateLimitedBackend()
    service = GatewayVlmService(config={}, backend=backend)

    with pytest.raises(RuntimeError, match="retry exhausted: attempts=3 retries=2"):
        asyncio.run(
            service.transcribe(
                image_path,
                media_type="image",
                file_type="jpg",
                source_file_name=image_path.name,
                options={
                    "model": "image-model",
                    "max_retries": 2,
                    "retry_base_delay_ms": 1,
                },
            )
        )

    assert backend.calls == 3


def test_gateway_vlm_service_does_not_retry_unauthorized(tmp_path: Path) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.gateway_vlm_service import GatewayVlmService
    from document_parse_service.media_transcription.openai_compatible_backend import (
        OpenAICompatibleMediaError,
    )

    class _UnauthorizedBackend:
        calls = 0

        async def transcribe(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.calls += 1
            raise OpenAICompatibleMediaError(status=401, response="unauthorized")

    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    backend = _UnauthorizedBackend()
    service = GatewayVlmService(config={}, backend=backend)

    with pytest.raises(OpenAICompatibleMediaError, match="status=401"):
        asyncio.run(
            service.transcribe(
                image_path,
                media_type="image",
                file_type="jpg",
                source_file_name=image_path.name,
                options={"model": "image-model"},
            )
        )

    assert backend.calls == 1


def test_openai_compatible_backend_parses_retry_after_seconds() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.openai_compatible_backend import (
        OpenAICompatibleMediaTranscriptionBackend,
    )

    assert OpenAICompatibleMediaTranscriptionBackend._parse_retry_after("2.5") == 2.5
    assert OpenAICompatibleMediaTranscriptionBackend._parse_retry_after("invalid") is None


def test_gateway_vlm_service_retries_server_disconnected_error() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.gateway_vlm_service import (
        GatewayVlmService,
    )

    class ServerDisconnectedError(RuntimeError):
        pass

    assert GatewayVlmService._is_retryable_error(
        ServerDisconnectedError("Server disconnected")
    )
    assert GatewayVlmService._is_retryable_error(
        RuntimeError("Gateway VLM returned empty content: finish_reason=length")
    )
