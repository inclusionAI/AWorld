"""Audio and video document services for filex media parsing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .base_document_service import BaseDocumentService
from .document_artifact_models import DocumentAsset, MarkdownArtifact
from .document_parse_logging import DocumentParseLogger
from .media_file_types import AUDIO_FILE_TYPES, IMAGE_FILE_TYPES, MEDIA_FILE_TYPES, VIDEO_FILE_TYPES
from .media_transcription.backend import MediaTranscriptionBackend
from .media_transcription.gateway_vlm_service import GatewayVlmService
from .media_transcription.image_auto_router import ImageAutoRouterBackend
from .media_transcription.models import TranscriptResult
from .media_transcription.registry import MediaTranscriptionBackendRegistry
from .paths import DOCUMENT_PARSE_WORKSPACE

if TYPE_CHECKING:
    from services.afts_service import AftsService

logger = logging.getLogger(__name__)

MEDIA_STAGE_NAMES = (
    "init",
    "content_extract",
    "markdown_assemble",
    "write_output",
    "finish",
)


class MediaDocumentService(BaseDocumentService):
    """Shared media parsing pipeline for audio and video files."""

    _stage_names = MEDIA_STAGE_NAMES

    def __init__(
        self,
        *,
        media_type: str,
        file_type: str,
        env_content: dict[str, Any] | None = None,
        backend: MediaTranscriptionBackend | None = None,
        backend_options: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._media_type = media_type
        self._file_type = file_type
        self._default_suffix = file_type
        self._empty_error_message = f"{media_type} 解析结果为空"
        if backend is None:
            backend, resolved_options = MediaTranscriptionBackendRegistry.create(
                env_content=env_content,
            )
        else:
            resolved_options = backend_options or {}
        self._backend = backend
        self._backend_options = resolved_options

    async def _build_artifact(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service: Optional["AftsService"],
        stage_logger: DocumentParseLogger,
    ) -> MarkdownArtifact:
        with stage_logger.stage(
            "content_extract",
            media_type=self._media_type,
            backend=getattr(self._backend, "name", type(self._backend).__name__),
        ):
            runtime_options = dict(self._backend_options)
            if self._media_type == "image":
                runtime_options.setdefault(
                    "image_asset_output_dir",
                    str(DOCUMENT_PARSE_WORKSPACE / task_id / "image_objects"),
                )
            result = await self._backend.transcribe(
                file_path,
                media_type=self._media_type,
                file_type=self._file_type,
                source_file_name=source_file_name,
                options=runtime_options,
            )

        with stage_logger.stage("markdown_assemble"):
            markdown_text = self._assemble_markdown(
                result=result,
                source_file_name=source_file_name,
            )
        assets = self._build_image_assets(result)
        return MarkdownArtifact(
            markdown_text=markdown_text,
            assets=assets,
            diagnostics={
                "task_id": task_id,
                "source_file_name": source_file_name,
                "media_type": self._media_type,
                "file_type": self._file_type,
                "backend": result.backend,
                "provider": result.backend,
                "model": result.model,
                "duration": result.duration,
                "segment_count": len(result.segments),
                "speaker_count": len(
                    {segment.speaker for segment in result.segments if segment.speaker}
                ),
                "transcript_char_count": len(result.text),
                "metadata": result.metadata,
            },
        )

    def _build_image_assets(self, result: TranscriptResult) -> list[DocumentAsset]:
        if self._media_type != "image":
            return []
        raw_assets = result.metadata.get("image_assets")
        if not isinstance(raw_assets, list):
            return []
        assets: list[DocumentAsset] = []
        for order, raw_asset in enumerate(raw_assets, start=1):
            if not isinstance(raw_asset, dict):
                continue
            local_path = Path(str(raw_asset.get("local_path") or "")).expanduser()
            if not str(raw_asset.get("local_path") or "").strip():
                continue
            crop_ref = str(raw_asset.get("crop_ref") or "").strip()
            object_id = str(raw_asset.get("object_id") or f"object-{order}").strip()
            assets.append(
                DocumentAsset(
                    asset_id=str(raw_asset.get("asset_id") or object_id),
                    kind="figure_crop",
                    local_path=local_path,
                    order=order,
                    meta={
                        "index": str(order),
                        "object_id": object_id,
                        "bbox": list(raw_asset.get("bbox") or []),
                        "local_path": str(local_path),
                        "markdown_path": crop_ref,
                        "placement": "already_in_markdown",
                    },
                )
            )
        return assets

    def _after_write(
        self,
        *,
        artifact: MarkdownArtifact,
        output_dir: Path,
        source_file_name: str,
        file_path: Path,
        stage_logger: DocumentParseLogger,
    ) -> None:
        if self._media_type != "image":
            return
        metadata = artifact.diagnostics.get("metadata")
        if not isinstance(metadata, dict):
            return
        evidence = metadata.get("image_evidence")
        if not isinstance(evidence, dict):
            return
        evidence_path = output_dir / f"{source_file_name}.evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        metadata["image_evidence_file_path"] = str(evidence_path)
        stage_logger.emit(
            "write_output",
            "evidence_completed",
            evidence_path=evidence_path,
            object_count=len(evidence.get("objects") or []),
        )

    def _assemble_markdown(
        self,
        *,
        result: TranscriptResult,
        source_file_name: str,
    ) -> str:
        lines = [
            f"# {source_file_name}",
            "",
            "## 媒体信息",
            "",
            f"- 类型: {self._media_type}",
            f"- 文件类型: {self._file_type}",
            f"- 后端: {result.backend}",
        ]
        if result.model:
            lines.append(f"- 模型: {result.model}")
        if result.language:
            lines.append(f"- 语言: {result.language}")
        if result.duration is not None:
            lines.append(f"- 时长: {self._format_duration(result.duration)}")

        lines.extend(["", "## 解析结果", ""])
        lines.append(result.text.strip())

        timeline = self._format_timeline(result)
        if timeline:
            lines.extend(["", "## 时间轴", "", timeline])

        return "\n".join(lines).strip() + "\n"

    def _format_timeline(self, result: TranscriptResult) -> str:
        timeline_lines = []
        for segment in result.segments:
            if not segment.text.strip():
                continue
            if segment.start is None and segment.end is None:
                continue
            start = self._format_timestamp(segment.start)
            end = self._format_timestamp(segment.end)
            label = f"{start}-{end}" if end else start
            speaker = f" {segment.speaker}" if segment.speaker else ""
            timeline_lines.append(f"- [{label}]{speaker} {segment.text.strip()}")
        return "\n".join(timeline_lines)

    @staticmethod
    def _format_duration(duration: float) -> str:
        return MediaDocumentService._format_timestamp(duration)

    @staticmethod
    def _format_timestamp(value: float | None) -> str:
        if value is None:
            return ""
        total_seconds = max(0, int(round(value)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"


class AudioDocumentService(MediaDocumentService):
    """Audio file parser."""

    def __init__(
        self,
        *,
        file_type: str,
        env_content: dict[str, Any] | None = None,
        backend: MediaTranscriptionBackend | None = None,
        backend_options: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            media_type="audio",
            file_type=file_type,
            env_content=env_content,
            backend=backend,
            backend_options=backend_options,
        )


class VideoDocumentService(MediaDocumentService):
    """Video file parser."""

    def __init__(
        self,
        *,
        file_type: str,
        env_content: dict[str, Any] | None = None,
        backend: MediaTranscriptionBackend | None = None,
        backend_options: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            media_type="video",
            file_type=file_type,
            env_content=env_content,
            backend=backend,
            backend_options=backend_options,
        )


class ImageDocumentService(MediaDocumentService):
    """Image parser that defaults to the configured Gateway VLM."""

    def __init__(
        self,
        *,
        file_type: str,
        env_content: dict[str, Any] | None = None,
        backend: MediaTranscriptionBackend | None = None,
        backend_options: dict[str, Any] | None = None,
    ) -> None:
        if backend is None:
            resolved_env = dict(env_content or {})
            resolved_env.setdefault("media_parse_backend", "openai_compatible")
            backend_name = str(resolved_env.get("media_parse_backend") or "").strip().lower()
            if backend_name in {"openai", "openai_compatible", "openai_chat_completions"}:
                backend = ImageAutoRouterBackend(
                    GatewayVlmService(env_content=resolved_env)
                )
                backend_options = MediaTranscriptionBackendRegistry.resolve_options(resolved_env)
            else:
                backend, backend_options = MediaTranscriptionBackendRegistry.create(
                    env_content=resolved_env,
                )
        super().__init__(
            media_type="image",
            file_type=file_type,
            env_content=env_content,
            backend=backend,
            backend_options=backend_options,
        )
