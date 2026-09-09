"""Shared template for document parsing services.

Subclasses implement ``_build_artifact`` while this class owns output writing,
stage logging, empty-result validation, metrics, and post-write processing.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional, Sequence, TYPE_CHECKING

from .document_artifact_models import MarkdownArtifact
from .document_artifact_writer import DocumentArtifactWriter
from .document_parse_logging import DocumentParseLogger
from .document_parse_metrics import build_parse_metrics

if TYPE_CHECKING:
    from services.afts_service import AftsService

from .paths import DOCUMENT_PARSE_WORKSPACE

logger = logging.getLogger(__name__)

# Standard pipeline for formats that may contain embedded assets.
ASSET_STAGE_NAMES: tuple[str, ...] = (
    "init",
    "content_extract",
    "asset_extract",
    "markdown_assemble",
    "write_output",
    "finish",
)


class BaseDocumentService:
    """Common pipeline shared by every document service."""

    #: Stage sequence used for logging; subclasses may override it.
    _stage_names: Sequence[str] = ASSET_STAGE_NAMES
    #: Fallback type used when a file has no suffix.
    _default_suffix: str = "bin"
    #: Error raised when parsing produces no content.
    _empty_error_message: str = "The parse result is empty"

    def __init__(self, *, artifact_writer: Optional[DocumentArtifactWriter] = None) -> None:
        self._artifact_writer = artifact_writer or DocumentArtifactWriter()

    async def parse_to_markdown(
        self,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service: Optional["AftsService"] = None,
    ) -> Path:
        total_start = time.perf_counter()
        stage_logger = self._build_stage_logger(
            file_path=file_path,
            task_id=task_id,
            source_file_name=source_file_name,
        )
        with stage_logger.stage(
            "init",
            file_size=file_path.stat().st_size if file_path.exists() else 0,
        ):
            pass

        artifact = await self._build_artifact(
            file_path=file_path,
            task_id=task_id,
            source_file_name=source_file_name,
            afts_service=afts_service,
            stage_logger=stage_logger,
        )
        if not artifact.markdown_text.strip():
            raise RuntimeError(self._empty_error_message)

        output_dir = DOCUMENT_PARSE_WORKSPACE / task_id
        with stage_logger.stage("write_output", output_dir=output_dir):
            output_path = self._artifact_writer.write_markdown(
                artifact,
                output_dir=output_dir,
                file_name=f"{source_file_name}.md",
            )

        self._after_write(
            artifact=artifact,
            output_dir=output_dir,
            source_file_name=source_file_name,
            file_path=file_path,
            stage_logger=stage_logger,
        )
        document_ir = getattr(artifact, "document_ir", None)
        if document_ir is not None:
            output_path.with_suffix(".document.json").write_text(
                json.dumps(document_ir, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        total_duration_ms = int((time.perf_counter() - total_start) * 1000)
        metrics = build_parse_metrics(
            file_type=file_path.suffix.lower().lstrip(".") or self._default_suffix,
            input_bytes=file_path.stat().st_size if file_path.exists() else 0,
            output_char_count=len(artifact.markdown_text),
            asset_count=len(artifact.assets),
            stage_durations_ms=getattr(stage_logger, "stage_durations_ms", {}),
            total_duration_ms=total_duration_ms,
            diagnostics=artifact.diagnostics,
        )
        artifact.diagnostics["metrics"] = metrics
        metrics_path = self._artifact_writer.write_metrics(metrics, output_path=output_path)
        stage_logger.emit(
            "finish",
            "completed",
            output_path=output_path,
            metrics_path=metrics_path,
            content_length=len(artifact.markdown_text),
            asset_count=len(artifact.assets),
            total_duration_ms=total_duration_ms,
            metrics_schema_version=metrics["schema_version"],
        )
        return output_path

    async def _build_artifact(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service: Optional["AftsService"],
        stage_logger: DocumentParseLogger,
    ) -> MarkdownArtifact:
        """Extract content and assets, then assemble a ready Markdown artifact.

        Subclasses must also record their extraction and assembly stages.
        """
        raise NotImplementedError

    def _after_write(
        self,
        *,
        artifact: MarkdownArtifact,
        output_dir: Path,
        source_file_name: str,
        file_path: Path,
        stage_logger: DocumentParseLogger,
    ) -> None:
        """Run optional post-write processing such as debug sidecars."""
        return None

    def _build_stage_logger(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
    ) -> DocumentParseLogger:
        return DocumentParseLogger(
            logger,
            task_id=task_id,
            file_type=file_path.suffix.lower().lstrip(".") or self._default_suffix,
            file_path=file_path,
            source_file_name=source_file_name,
            stage_names=self._stage_names,
        )
