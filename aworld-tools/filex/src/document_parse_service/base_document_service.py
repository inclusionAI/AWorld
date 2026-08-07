"""
文档服务基类。

用模板方法固定「init -> 内容抽取 -> 写盘 -> 落盘后处理 -> finish」骨架，
子类只需实现 `_build_artifact`，负责正文/资源抽取与 Markdown 组装。
写盘、阶段日志、空结果校验统一收口在此，不再由各文件类型重复实现。
"""

from __future__ import annotations

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

# 含资源提取阶段的标准流程（PDF / PPT / Word）。
ASSET_STAGE_NAMES: tuple[str, ...] = (
    "init",
    "content_extract",
    "asset_extract",
    "markdown_assemble",
    "write_output",
    "finish",
)


class BaseDocumentService:
    """所有文件类型文档服务的公共骨架。"""

    #: 阶段日志使用的阶段集合，子类按需覆盖。
    _stage_names: Sequence[str] = ASSET_STAGE_NAMES
    #: 文件无后缀时用于日志的兜底类型。
    _default_suffix: str = "bin"
    #: 正文为空时抛出的错误信息。
    _empty_error_message: str = "解析结果为空"

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
        """抽取正文与资源并组装 Markdown，返回已就绪的产物。

        子类必须实现，并负责记录 content_extract / asset_extract /
        markdown_assemble 等阶段日志。
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
        """落盘后的可选处理（如 debug sidecar），默认无操作。"""
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
