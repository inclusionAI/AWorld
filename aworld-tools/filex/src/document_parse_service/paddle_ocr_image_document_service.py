"""PaddleOCR-VL document parsing for standalone image inputs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .asset_reference import AssetReferenceMode, prepare_markdown_asset_references
from .base_document_service import BaseDocumentService
from .document_asset_publisher import (
    AftsDocumentAssetPublisher,
    NoOpDocumentAssetPublisher,
)
from .document_artifact_models import MarkdownArtifact
from .markdown_assembler import AnchoredMarkdownAssembler
from .paths import DOCUMENT_PARSE_WORKSPACE
from .pdf.paddle_ocr_pdf_provider import PaddleOcrPdfProvider

if TYPE_CHECKING:
    from services.afts_service import AftsService


class PaddleOcrImageDocumentService(BaseDocumentService):
    """Run image documents through the same full PaddleOCR-VL pipeline as PDFs."""

    def __init__(
        self,
        *,
        file_type: str,
        env_content: dict[str, Any] | None = None,
        asset_reference_mode: AssetReferenceMode = "remote_id",
        provider: PaddleOcrPdfProvider | None = None,
    ) -> None:
        super().__init__()
        self._default_suffix = file_type
        self._env_content = dict(env_content or {})
        self._asset_reference_mode = asset_reference_mode
        self._provider = provider or PaddleOcrPdfProvider(
            env_content=self._env_content
        )
        self._markdown_assembler = AnchoredMarkdownAssembler()

    async def _build_artifact(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service: "AftsService | None",
        stage_logger: Any,
    ) -> MarkdownArtifact:
        with stage_logger.stage("content_extract", provider="paddle_ocr"):
            result = await self._provider.understand_pdf(
                file_path=file_path,
                task_id=task_id,
                source_file_name=source_file_name,
            )
            artifact = self._provider.to_markdown_artifact(result)

        assets = list(artifact.assets)
        with stage_logger.stage(
            "asset_extract",
            asset_count=len(assets),
            afts_enabled=bool(afts_service),
        ):
            publisher = (
                AftsDocumentAssetPublisher(afts_service)
                if afts_service
                else NoOpDocumentAssetPublisher()
            )
            artifact.assets = await publisher.publish_assets(assets)
            prepare_markdown_asset_references(
                artifact.assets,
                output_dir=DOCUMENT_PARSE_WORKSPACE / task_id,
                asset_reference_mode=self._asset_reference_mode,
            )
            artifact.markdown_text = self._provider.replace_markdown_asset_references(
                artifact.markdown_text,
                artifact.assets,
            )

        with stage_logger.stage("markdown_assemble", asset_count=len(artifact.assets)):
            artifact.markdown_text = self._markdown_assembler.assemble(artifact)
        return artifact
