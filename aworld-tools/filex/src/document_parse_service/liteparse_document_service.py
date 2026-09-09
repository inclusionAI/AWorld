"""Base service for LiteParse-backed document formats.

PDF and presentation services share extraction, asset processing, Markdown
assembly, and debug-sidecar behavior through this class.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .base_document_service import BaseDocumentService
from .document_artifact_models import MarkdownArtifact
from .document_artifact_writer import DocumentArtifactWriter
from .document_parse_logging import DocumentParseLogger
from .liteparse_pdf_service import LiteParseContentExtractor
from .markdown_assembler import MarkdownAssembler

if TYPE_CHECKING:
    from services.afts_service import AftsService

logger = logging.getLogger(__name__)

# Diagnostics key that retains the raw parse result for debug sidecars.
_PARSE_RESULT_KEY = "_parse_result"


class LiteParseDocumentService(BaseDocumentService):
    """Shared LiteParse implementation for PDF and presentation formats."""

    _empty_error_message = "LiteParse 解析结果为空"

    def __init__(
        self,
        env_content: Optional[dict[str, Any]] = None,
        content_extractor: Optional[LiteParseContentExtractor] = None,
        markdown_assembler: Optional[MarkdownAssembler] = None,
        artifact_writer: Optional[DocumentArtifactWriter] = None,
    ) -> None:
        super().__init__(artifact_writer=artifact_writer)
        self._env_content = env_content or {}
        self._content_extractor = content_extractor or LiteParseContentExtractor(
            env_content=self._env_content,
        )
        self._markdown_assembler = markdown_assembler or self._default_markdown_assembler()

    def _default_markdown_assembler(self) -> MarkdownAssembler:
        """Return the default Markdown assembler for this format."""
        raise NotImplementedError

    async def _build_artifact(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service: Optional["AftsService"],
        stage_logger: DocumentParseLogger,
    ) -> MarkdownArtifact:
        artifact, parse_result = await self._content_extractor.parse_to_artifact_result(
            file_path=file_path,
            task_id=task_id,
            source_file_name=source_file_name,
            afts_service=afts_service,
            markdown_assembler=self._markdown_assembler,
            stage_logger=stage_logger,
        )
        artifact.diagnostics[_PARSE_RESULT_KEY] = parse_result
        return artifact

    def _after_write(
        self,
        *,
        artifact: MarkdownArtifact,
        output_dir: Path,
        source_file_name: str,
        file_path: Path,
        stage_logger: DocumentParseLogger,
    ) -> None:
        if not self._content_extractor.should_output_debug_json():
            return
        parse_result = artifact.diagnostics.get(_PARSE_RESULT_KEY)
        if parse_result is not None:
            self._content_extractor.write_debug_sidecar(
                parse_result,
                output_dir,
                source_file_name,
            )
        else:
            logger.info(
                "liteparse_document_service skip debug sidecar for text-based format | "
                f"file_path={file_path}"
            )
