"""Document service for PDF files.

The service uses LiteParse for content and asset extraction, Markdown
assembly, and persistence on top of ``LiteParseDocumentService``.
"""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from typing import Any, Optional, TYPE_CHECKING

from ..asset_reference import AssetReferenceMode, prepare_markdown_asset_references
from ..document_asset_publisher import AftsDocumentAssetPublisher, NoOpDocumentAssetPublisher
from ..document_artifact_models import DocumentAsset, MarkdownArtifact
from ..liteparse_document_service import LiteParseDocumentService
from ..markdown_assembler import AnchoredMarkdownAssembler, MarkdownAssembler
from ..paths import DOCUMENT_PARSE_WORKSPACE
from .pdf_batch_checkpoint import PdfBatchCheckpointStore
from ..pdf_page_selection import create_pdf_subset, parse_page_selection

if TYPE_CHECKING:
    from services.afts_service import AftsService


class PdfDocumentService(LiteParseDocumentService):
    """Provide document parsing for PDF files."""

    _default_suffix = "pdf"

    def __init__(
        self,
        env_content: Optional[dict[str, Any]] = None,
        content_extractor: Any | None = None,
        markdown_assembler: Optional[MarkdownAssembler] = None,
        artifact_writer: Any | None = None,
        pdf_provider: Any | None = None,
        asset_reference_mode: AssetReferenceMode = "remote_id",
    ) -> None:
        super().__init__(
            env_content=env_content,
            content_extractor=content_extractor,
            markdown_assembler=markdown_assembler,
            artifact_writer=artifact_writer,
        )
        self._pdf_provider = pdf_provider
        self._asset_reference_mode = asset_reference_mode

    def _default_markdown_assembler(self) -> MarkdownAssembler:
        return AnchoredMarkdownAssembler()

    async def _build_artifact(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service: Optional["AftsService"],
        stage_logger,
    ):
        selected_pages = parse_page_selection(self._env_content.get("pdf_pages"))
        effective_path, source_page_count = create_pdf_subset(
            file_path,
            DOCUMENT_PARSE_WORKSPACE / task_id / "source" / f"{source_file_name}.selected.pdf",
            selected_pages,
        )
        batch_size = self._page_batch_size()
        requested_pages = selected_pages or list(range(1, source_page_count + 1))
        if not self._use_pdf_parse_provider():
            if batch_size and len(requested_pages) > batch_size:
                artifact = await self._parse_liteparse_batches(
                    source_path=file_path,
                    requested_pages=requested_pages,
                    batch_size=batch_size,
                    task_id=task_id,
                    source_file_name=source_file_name,
                    afts_service=afts_service,
                    stage_logger=stage_logger,
                )
            else:
                artifact = await super()._build_artifact(
                    file_path=effective_path,
                    task_id=task_id,
                    source_file_name=source_file_name,
                    afts_service=afts_service,
                    stage_logger=stage_logger,
                )
            self._apply_page_selection_diagnostics(
                artifact,
                selected_pages=selected_pages,
                source_page_count=source_page_count,
            )
            return artifact

        provider = self._resolve_pdf_provider()
        with stage_logger.stage(
            "content_extract",
            provider=getattr(provider, "name", type(provider).__name__),
        ):
            if batch_size and len(requested_pages) > batch_size:
                artifact = await self._parse_provider_batches(
                    provider=provider,
                    source_path=file_path,
                    requested_pages=requested_pages,
                    batch_size=batch_size,
                    task_id=task_id,
                    source_file_name=source_file_name,
                    stage_logger=stage_logger,
                )
            else:
                result = await provider.understand_pdf(
                    file_path=effective_path,
                    task_id=task_id,
                    source_file_name=source_file_name,
                )
                artifact = provider.to_markdown_artifact(result)
        self._apply_page_selection_diagnostics(
            artifact,
            selected_pages=selected_pages,
            source_page_count=source_page_count,
        )
        assets = list(getattr(artifact, "assets", []) or [])
        with stage_logger.stage(
            "asset_extract",
            asset_count=len(assets),
            afts_enabled=bool(afts_service),
        ):
            publisher = AftsDocumentAssetPublisher(afts_service) if afts_service else NoOpDocumentAssetPublisher()
            published_assets = await publisher.publish_assets(assets)
            prepare_markdown_asset_references(
                published_assets,
                output_dir=DOCUMENT_PARSE_WORKSPACE / task_id,
                asset_reference_mode=self._asset_reference_mode,
            )
            self._validate_published_assets(published_assets)
            artifact.assets = published_assets
            replace_references = getattr(provider, "replace_markdown_asset_references", None)
            if callable(replace_references):
                artifact.markdown_text = replace_references(artifact.markdown_text, published_assets)

        with stage_logger.stage("markdown_assemble", asset_count=len(artifact.assets)):
            artifact.markdown_text = self._markdown_assembler.assemble(artifact)
            return artifact

    def _page_batch_size(self) -> int:
        value = self._env_content.get("pdf_page_batch_size")
        if value in (None, "", 0, "0"):
            return 0
        try:
            batch_size = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("pdf_page_batch_size must be a positive integer") from exc
        if batch_size < 1:
            raise ValueError("pdf_page_batch_size must be a positive integer")
        return batch_size

    async def _parse_provider_batches(
        self,
        *,
        provider: Any,
        source_path: Path,
        requested_pages: list[int],
        batch_size: int,
        task_id: str,
        source_file_name: str,
        stage_logger: Any,
    ) -> MarkdownArtifact:
        artifacts: list[MarkdownArtifact] = []
        batch_details: list[dict[str, Any]] = []
        checkpoint_store = self._batch_checkpoint_store()
        started_at = monotonic()
        batch_total = (len(requested_pages) + batch_size - 1) // batch_size
        if checkpoint_store:
            checkpoint_store.write_progress(
                status="parsing", total_pages=len(requested_pages), total_batches=batch_total,
                completed_pages=0, completed_batches=0,
            )
        for batch_index, offset in enumerate(range(0, len(requested_pages), batch_size), start=1):
            batch_pages = requested_pages[offset:offset + batch_size]
            resumed_artifact = checkpoint_store.load(
                batch_index=batch_index,
                pages=batch_pages,
            ) if checkpoint_store else None
            if resumed_artifact is not None:
                artifacts.append(resumed_artifact)
                batch_details.append(
                    {
                        "batch_index": batch_index,
                        "pages": batch_pages,
                        "status": "resumed",
                        "duration_ms": 0,
                    }
                )
                stage_logger.progress(
                    "content_extract",
                    batch_index=batch_index,
                    batch_total=(len(requested_pages) + batch_size - 1) // batch_size,
                    batch_pages=batch_pages,
                    batch_status="resumed",
                )
                self._update_batch_progress(checkpoint_store, batch_details, len(requested_pages), batch_total, started_at)
                continue
            batch_path = DOCUMENT_PARSE_WORKSPACE / task_id / "source" / f"{source_file_name}.batch-{batch_index}.pdf"
            create_pdf_subset(source_path, batch_path, batch_pages)
            batch_started_at = monotonic()
            try:
                result = await provider.understand_pdf(
                    file_path=batch_path,
                    task_id=f"{task_id}-batch-{batch_index}",
                    source_file_name=f"{source_file_name}-batch-{batch_index}",
                )
            except BaseException as exc:
                if checkpoint_store:
                    checkpoint_store.write_progress(status="failed", failed_batch=batch_index, error=str(exc))
                raise
            artifact = provider.to_markdown_artifact(result)
            self._remap_batch_assets(artifact.assets, batch_pages=batch_pages, batch_index=batch_index)
            if checkpoint_store:
                checkpoint_store.save(batch_index=batch_index, pages=batch_pages, artifact=artifact)
            artifacts.append(artifact)
            batch_details.append(
                {
                    "batch_index": batch_index,
                    "pages": batch_pages,
                    "status": "succeeded",
                    "duration_ms": round((monotonic() - batch_started_at) * 1000, 2),
                }
            )
            stage_logger.progress(
                "content_extract",
                batch_index=batch_index,
                batch_total=(len(requested_pages) + batch_size - 1) // batch_size,
                batch_pages=batch_pages,
                batch_status="succeeded",
                batch_duration_ms=batch_details[-1]["duration_ms"],
            )
            self._update_batch_progress(checkpoint_store, batch_details, len(requested_pages), batch_total, started_at)
        merged = self._merge_batch_artifacts(
            artifacts,
            requested_pages=requested_pages,
            batch_details=batch_details,
            total_elapsed_ms=round((monotonic() - started_at) * 1000, 2),
        )
        if checkpoint_store:
            checkpoint_store.write_progress(status="succeeded", duration_ms=round((monotonic() - started_at) * 1000, 2))
        return merged

    def _batch_checkpoint_store(self) -> PdfBatchCheckpointStore | None:
        resume_id = str(self._env_content.get("pdf_batch_resume_id") or "").strip()
        if not resume_id:
            return None
        return PdfBatchCheckpointStore(
            DOCUMENT_PARSE_WORKSPACE / "pdf_batch_checkpoints",
            resume_id,
        )

    @staticmethod
    def _update_batch_progress(checkpoint_store, batch_details, total_pages, batch_total, started_at) -> None:
        if not checkpoint_store:
            return
        completed_pages = sum(len(item["pages"]) for item in batch_details)
        updates = {
            "status": "parsing",
            "completed_pages": completed_pages,
            "total_pages": total_pages,
            "completed_batches": len(batch_details),
            "total_batches": batch_total,
            "duration_ms": round((monotonic() - started_at) * 1000, 2),
            "last_batch": batch_details[-1],
        }
        if len(batch_details) == 1:
            updates["first_batch_duration_ms"] = updates["duration_ms"]
        checkpoint_store.write_progress(**updates)

    async def _parse_liteparse_batches(
        self,
        *,
        source_path: Path,
        requested_pages: list[int],
        batch_size: int,
        task_id: str,
        source_file_name: str,
        afts_service: Optional["AftsService"],
        stage_logger: Any,
    ) -> MarkdownArtifact:
        artifacts: list[MarkdownArtifact] = []
        batch_details: list[dict[str, Any]] = []
        checkpoint_store = self._batch_checkpoint_store()
        started_at = monotonic()
        batch_total = (len(requested_pages) + batch_size - 1) // batch_size
        if checkpoint_store:
            checkpoint_store.write_progress(
                status="parsing", total_pages=len(requested_pages), total_batches=batch_total,
                completed_pages=0, completed_batches=0,
            )
        for batch_index, offset in enumerate(range(0, len(requested_pages), batch_size), start=1):
            batch_pages = requested_pages[offset:offset + batch_size]
            artifact = checkpoint_store.load(
                batch_index=batch_index,
                pages=batch_pages,
            ) if checkpoint_store else None
            batch_status = "resumed"
            batch_duration_ms = 0.0
            if artifact is None:
                batch_status = "succeeded"
                batch_path = (
                    DOCUMENT_PARSE_WORKSPACE
                    / task_id
                    / "source"
                    / f"{source_file_name}.batch-{batch_index}.pdf"
                )
                create_pdf_subset(source_path, batch_path, batch_pages)
                batch_started_at = monotonic()
                artifact = await super()._build_artifact(
                    file_path=batch_path,
                    task_id=f"{task_id}-batch-{batch_index}",
                    source_file_name=f"{source_file_name}-batch-{batch_index}",
                    afts_service=afts_service,
                    stage_logger=stage_logger,
                )
                batch_duration_ms = round((monotonic() - batch_started_at) * 1000, 2)
                self._remap_batch_assets(
                    artifact.assets,
                    batch_pages=batch_pages,
                    batch_index=batch_index,
                )
                if checkpoint_store:
                    checkpoint_store.save(batch_index=batch_index, pages=batch_pages, artifact=artifact)
            artifacts.append(artifact)
            batch_details.append(
                {
                    "batch_index": batch_index,
                    "pages": batch_pages,
                    "status": batch_status,
                    "duration_ms": batch_duration_ms,
                }
            )
            stage_logger.progress(
                "content_extract",
                batch_index=batch_index,
                batch_total=batch_total,
                batch_pages=batch_pages,
                batch_status=batch_status,
                batch_duration_ms=batch_duration_ms,
            )
            self._update_batch_progress(checkpoint_store, batch_details, len(requested_pages), batch_total, started_at)
        merged = self._merge_batch_artifacts(
            artifacts,
            requested_pages=requested_pages,
            batch_details=batch_details,
            total_elapsed_ms=round((monotonic() - started_at) * 1000, 2),
        )
        if checkpoint_store:
            checkpoint_store.write_progress(status="succeeded", duration_ms=round((monotonic() - started_at) * 1000, 2))
        return merged

    @staticmethod
    def _remap_batch_assets(assets: list[DocumentAsset], *, batch_pages: list[int], batch_index: int) -> None:
        for order, asset in enumerate(assets, start=1):
            local_page = max(int(asset.page_number or 1), 1)
            mapped_page = batch_pages[min(local_page - 1, len(batch_pages) - 1)]
            asset.asset_id = f"batch_{batch_index}_{asset.asset_id}"
            asset.page_number = mapped_page
            asset.order = order
            asset.anchor.page_number = mapped_page
            asset.meta["batch_index"] = batch_index
            asset.meta["source_page_number"] = mapped_page

    @staticmethod
    def _merge_batch_artifacts(
        artifacts: list[MarkdownArtifact],
        *,
        requested_pages: list[int],
        batch_details: list[dict[str, Any]],
        total_elapsed_ms: float,
    ) -> MarkdownArtifact:
        diagnostics = dict(artifacts[0].diagnostics) if artifacts else {}
        sum_keys = (
            "initialization_elapsed_ms",
            "parse_elapsed_ms",
            "model_call_count",
            "model_retry_count",
            "vlm_retry_count",
            "timeout_count",
            "error_count",
            "ocr_page_count",
            "vlm_page_count",
            "rendered_page_count",
            "raw_result_count",
        )
        for key in sum_keys:
            diagnostics[key] = sum(float(artifact.diagnostics.get(key) or 0) for artifact in artifacts)
        diagnostics["peak_concurrency"] = max(
            (int(artifact.diagnostics.get("peak_concurrency") or 0) for artifact in artifacts),
            default=0,
        )
        diagnostics.update(
            {
                "page_count": len(requested_pages),
                "page_count_requested": len(requested_pages),
                "page_count_processed": len(requested_pages),
                "page_count_succeeded": len(requested_pages),
                "requested_pages": requested_pages,
                "batch_count": len(batch_details),
                "resumed_batch_count": sum(
                    1 for batch in batch_details if batch["status"] == "resumed"
                ),
                "first_batch_page_count": len(batch_details[0]["pages"]) if batch_details else 0,
                "first_batch_duration_ms": batch_details[0]["duration_ms"] if batch_details else None,
                "total_elapsed_ms": total_elapsed_ms,
                "page_batches": batch_details,
            }
        )
        return MarkdownArtifact(
            markdown_text="\n\n".join(
                artifact.markdown_text.strip() for artifact in artifacts if artifact.markdown_text.strip()
            ),
            assets=[asset for artifact in artifacts for asset in artifact.assets],
            diagnostics=diagnostics,
            document_ir=PdfDocumentService._merge_document_ir(
                artifacts,
                requested_pages=requested_pages,
            ),
        )

    @staticmethod
    def _merge_document_ir(
        artifacts: list[MarkdownArtifact],
        *,
        requested_pages: list[int],
    ) -> dict[str, Any] | None:
        pages: list[dict[str, Any]] = []
        source_page_offset = 0
        for artifact in artifacts:
            document_ir = getattr(artifact, "document_ir", None)
            if not isinstance(document_ir, dict):
                continue
            for local_page in document_ir.get("pages") or []:
                if not isinstance(local_page, dict):
                    continue
                merged_page = dict(local_page)
                local_index = int(merged_page.get("page_index") or 0)
                requested_index = min(source_page_offset + local_index, len(requested_pages) - 1)
                merged_page["page_index"] = requested_pages[requested_index] - 1
                pages.append(merged_page)
            source_page_offset += len(document_ir.get("pages") or [])
        if not pages:
            return None
        has_v2_spans = any(
            str((getattr(artifact, "document_ir", None) or {}).get("schema_version") or "")
            == "filex-document-ir-v2"
            for artifact in artifacts
            if isinstance(getattr(artifact, "document_ir", None), dict)
        )
        return {
            "schema_version": (
                "filex-document-ir-v2" if has_v2_spans else "filex-document-ir-v1"
            ),
            "coordinate_system": "pixel_top_left_xyxy",
            "pages": pages,
        }

    def _apply_page_selection_diagnostics(
        self,
        artifact,
        *,
        selected_pages: list[int],
        source_page_count: int,
    ) -> None:
        processed_count = len(selected_pages) if selected_pages else source_page_count
        requested_pages = selected_pages or list(range(1, source_page_count + 1))
        first_batch_count = min(
            max(int(self._env_content.get("first_batch_page_count") or 1), 1),
            processed_count,
        ) if processed_count else 0
        artifact.diagnostics.update(
            {
                "source_page_count": source_page_count,
                "requested_pages": requested_pages,
                "page_count_requested": len(requested_pages),
                "page_count_processed": processed_count,
                "page_count_succeeded": processed_count,
                "page_number_base": 1,
            }
        )
        artifact.diagnostics.setdefault("first_batch_page_count", first_batch_count)
        artifact.diagnostics.setdefault("batch_count", 1 if processed_count else 0)
        artifact.diagnostics.setdefault(
            "first_batch_duration_ms",
            artifact.diagnostics.get("parse_elapsed_ms")
            or artifact.diagnostics.get("vlm_total_elapsed_ms")
            or artifact.diagnostics.get("total_elapsed_ms"),
        )

    def _use_pdf_parse_provider(self) -> bool:
        provider = str(
            self._env_content.get("pdf_parse_provider")
            or self._env_content.get("pdf_provider")
            or ""
        ).strip().lower()
        return provider in {"pypdf_vlm", "pypdf+vlm", "vlm_pdf", "paddle_ocr", "paddleocr", "paddle"}

    def _resolve_pdf_provider(self):
        if self._pdf_provider is not None:
            return self._pdf_provider
        provider = str(
            self._env_content.get("pdf_parse_provider")
            or self._env_content.get("pdf_provider")
            or ""
        ).strip().lower()
        if provider in {"paddle_ocr", "paddleocr", "paddle"}:
            from .paddle_ocr_pdf_provider import PaddleOcrPdfProvider

            self._pdf_provider = PaddleOcrPdfProvider(env_content=self._env_content)
            return self._pdf_provider
        from .pypdf_vlm_provider import PypdfVlmPdfProvider

        self._pdf_provider = PypdfVlmPdfProvider(env_content=self._env_content)
        return self._pdf_provider

    def _validate_published_assets(self, assets: list[DocumentAsset]) -> None:
        if self._asset_reference_mode == "local_path":
            return

        missing_assets = [
            asset for asset in assets if asset.local_path is not None and not asset.remote_id
        ]
        if not missing_assets:
            return

        missing_remote_assets = [asset.asset_id for asset in missing_assets]
        local_asset_dirs = sorted(
            {
                str(asset.local_path.parent)
                for asset in missing_assets
                if asset.local_path is not None
            }
        )
        local_asset_dir_message = ""
        if local_asset_dirs:
            local_asset_dir_message = "；本地图片目录: " + ", ".join(local_asset_dirs)
        raise RuntimeError(
            "PDF 图片上传到 AFTS 失败，无法生成 markdown 图片引用: "
            + ", ".join(missing_remote_assets)
            + local_asset_dir_message
        )
