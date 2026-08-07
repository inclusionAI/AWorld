"""
按文件类型选择 DocumentService 的工厂。
"""

from __future__ import annotations

from typing import Any

from .asset_reference import AssetReferenceMode
from .document_service import DocumentService
from .media_file_types import AUDIO_FILE_TYPES, IMAGE_FILE_TYPES, VIDEO_FILE_TYPES


class DocumentServiceFactory:
    """按文件类型构造对应的文档服务。"""

    @staticmethod
    def create(
        *,
        file_type: str,
        env_content: dict[str, Any] | None = None,
        asset_reference_mode: AssetReferenceMode = "remote_id",
    ) -> DocumentService:
        normalized_file_type = file_type.lower().strip()

        if normalized_file_type == "pdf":
            from .pdf_document_service import PdfDocumentService

            return PdfDocumentService(
                env_content=env_content,
                asset_reference_mode=asset_reference_mode,
            )
        if normalized_file_type in {"ppt", "pptx"}:
            from .ppt_document_service import PptDocumentService

            return PptDocumentService(
                env_content=env_content,
                asset_reference_mode=asset_reference_mode,
            )
        if normalized_file_type in {"doc", "docx"}:
            from .word_document_service import WordDocumentService

            return WordDocumentService(asset_reference_mode=asset_reference_mode)
        if normalized_file_type in {"xls", "xlsx"}:
            from .tabular_document_service import ExcelDocumentService

            return ExcelDocumentService()
        if normalized_file_type == "csv":
            from .tabular_document_service import CsvDocumentService

            return CsvDocumentService()
        if normalized_file_type == "txt":
            from .text_document_service import TxtDocumentService

            return TxtDocumentService()
        if normalized_file_type in {"md", "markdown"}:
            from .text_document_service import MarkdownDocumentService

            return MarkdownDocumentService()
        if normalized_file_type in AUDIO_FILE_TYPES:
            from .media_document_service import AudioDocumentService

            return AudioDocumentService(file_type=normalized_file_type, env_content=env_content)
        if normalized_file_type in VIDEO_FILE_TYPES:
            from .media_document_service import VideoDocumentService

            return VideoDocumentService(file_type=normalized_file_type, env_content=env_content)
        if normalized_file_type in IMAGE_FILE_TYPES:
            from .media_document_service import ImageDocumentService

            return ImageDocumentService(file_type=normalized_file_type, env_content=env_content)

        raise ValueError(f"Unsupported file type: {file_type}")
