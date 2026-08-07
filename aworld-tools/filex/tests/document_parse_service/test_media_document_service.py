import asyncio
import sys
import tempfile
from pathlib import Path


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


class _StubMediaBackend:
    name = "stub"

    async def transcribe(
        self,
        file_path,
        *,
        media_type,
        file_type,
        source_file_name,
        options,
    ):
        from document_parse_service.media_transcription.models import (
            TranscriptResult,
            TranscriptSegment,
        )

        return TranscriptResult(
            text="第一段内容\n第二段内容",
            backend=self.name,
            model="stub-model",
            language="zh",
            duration=65.0,
            segments=[
                TranscriptSegment(text="第一段内容", start=0.0, end=10.0),
                TranscriptSegment(text="第二段内容", start=10.0, end=65.0, speaker="S1"),
            ],
            metadata={"file_type": file_type, "media_type": media_type},
        )


def test_audio_document_service_writes_media_markdown() -> None:
    _add_src_path()
    from document_parse_service.media_document_service import AudioDocumentService

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.mp3"
        file_path.write_bytes(b"ID3demo")
        service = AudioDocumentService(
            file_type="mp3",
            backend=_StubMediaBackend(),
            backend_options={},
        )

        output_path = asyncio.run(
            service.parse_to_markdown(
                file_path=file_path,
                task_id="media-audio-test",
                source_file_name="demo",
            )
        )

        content = output_path.read_text(encoding="utf-8")
        assert "# demo" in content
        assert "- 类型: audio" in content
        assert "- 后端: stub" in content
        assert "## 解析结果" in content
        assert "第一段内容" in content
        assert "- [00:10-01:05] S1 第二段内容" in content


def test_media_backend_registry_supports_local_and_openai_compatible() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.registry import (
        MediaTranscriptionBackendRegistry,
    )

    local_backend, local_options = MediaTranscriptionBackendRegistry.create(
        env_content={"media_parse_backend": "local"}
    )
    openai_backend, openai_options = MediaTranscriptionBackendRegistry.create(
        env_content={
            "media_parse_backend": "openai_compatible",
            "media_parse_model": "glm-4.6v",
            "media_parse_options": {"base_url": "https://example.test/v1"},
        }
    )
    assert local_backend.name == "local"
    assert local_options["backend"] == "local"
    assert openai_backend.name == "openai_compatible"
    assert openai_options["model"] == "glm-4.6v"
    assert openai_options["base_url"] == "https://example.test/v1"


def test_openai_compatible_backend_builds_video_url_payload() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.openai_compatible_backend import (
        OpenAICompatibleMediaTranscriptionBackend,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.mp4"
        file_path.write_bytes(b"\x00\x00\x00\x18ftypmp42demo")

        item = OpenAICompatibleMediaTranscriptionBackend()._build_media_content_item(
            file_path=file_path,
            media_type="video",
            file_type="mp4",
            media_url="",
            item_type="",
        )

    assert item["type"] == "video_url"
    assert item["video_url"]["url"].startswith("data:video/mp4;base64,")


def test_openai_compatible_backend_builds_image_url_payload() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.openai_compatible_backend import (
        OpenAICompatibleMediaTranscriptionBackend,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.png"
        file_path.write_bytes(b"\x89PNG\r\n\x1a\npng")

        item = OpenAICompatibleMediaTranscriptionBackend()._build_media_content_item(
            file_path=file_path,
            media_type="image",
            file_type="png",
            media_url="",
            item_type="",
        )

    assert item["type"] == "image_url"
    assert item["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_compatible_backend_accepts_explicit_auth_token() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.openai_compatible_backend import (
        OpenAICompatibleMediaTranscriptionBackend,
    )

    headers = OpenAICompatibleMediaTranscriptionBackend._build_headers(
        {"auth_token": "gateway-token"}
    )

    assert headers["Authorization"] == "Bearer gateway-token"


def test_openai_compatible_backend_keeps_explicit_authorization_header() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.openai_compatible_backend import (
        OpenAICompatibleMediaTranscriptionBackend,
    )

    headers = OpenAICompatibleMediaTranscriptionBackend._build_headers(
        {
            "auth_token": "ignored-token",
            "headers": {"Authorization": "Bearer explicit-token"},
        }
    )

    assert headers["Authorization"] == "Bearer explicit-token"
