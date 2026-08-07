"""
无附件文档的通用解析 pipeline。

用于承接 TXT / Markdown / CSV / Excel 等最终直接产出 Markdown 的场景。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .base_document_service import BaseDocumentService
from .content_extractor import ContentExtractor
from .document_artifact_models import MarkdownArtifact
from .document_artifact_writer import DocumentArtifactWriter
from .document_parse_logging import DocumentParseLogger
from .markdown_assembler import MarkdownAssembler, PassthroughMarkdownAssembler

if TYPE_CHECKING:
    from services.afts_service import AftsService

logger = logging.getLogger(__name__)

SIMPLE_STAGE_NAMES = (
    "init",
    "content_extract",
    "markdown_assemble",
    "write_output",
    "finish",
)


class SimpleDocumentService(BaseDocumentService):
    """统一处理无附件文件类型的标准 pipeline。"""

    _stage_names = SIMPLE_STAGE_NAMES

    def __init__(
        self,
        *,
        file_type: str,
        content_extractor: ContentExtractor,
        markdown_assembler: Optional[MarkdownAssembler] = None,
        artifact_writer: Optional[DocumentArtifactWriter] = None,
    ) -> None:
        super().__init__(artifact_writer=artifact_writer)
        self._file_type = file_type
        self._default_suffix = file_type
        self._empty_error_message = f"{file_type} 解析结果为空"
        self._content_extractor = content_extractor
        self._markdown_assembler = markdown_assembler or PassthroughMarkdownAssembler()

    async def _build_artifact(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service: Optional["AftsService"],
        stage_logger: DocumentParseLogger,
    ) -> MarkdownArtifact:
        with stage_logger.stage("content_extract"):
            markdown_text, raw_result = await self._content_extractor.extract_content(file_path)

        artifact = MarkdownArtifact(
            markdown_text=markdown_text,
            diagnostics={
                "task_id": task_id,
                "source_file_name": source_file_name,
                "raw_result": raw_result,
            },
        )
        with stage_logger.stage("markdown_assemble"):
            artifact.markdown_text = self._markdown_assembler.assemble(artifact)
        return artifact
