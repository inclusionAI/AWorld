"""
LiteParse 文档解析服务

当前 filesystem_server 的文档解析主链路统一走 LiteParse。
旧的各格式 legacy 逻辑仍保留在对应 Parser 中，便于后续切回，
但当前服务层不做任何回退。
"""

import asyncio
import inspect
import importlib.metadata
import json
import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

try:
    from liteparse import LiteParse
except ImportError:
    LiteParse = None  # type: ignore[assignment]

from ..document_artifact_models import DocumentAnchor, DocumentAsset, MarkdownArtifact
from ..document_asset_publisher import AftsDocumentAssetPublisher
from ..document_parse_logging import DocumentParseLogger
from ..markdown_assembler import AnchoredMarkdownAssembler, MarkdownAssembler
from ..pdf_image_extract_service import PdfImageExtractService
from ..pdf_figure_extract_service import PdfFigureExtractService
from ..pdf_layout_extract_service import PdfLayoutExtractService

if TYPE_CHECKING:
    from services.afts_service import AftsService

logger = logging.getLogger(__name__)


class LiteParseContentExtractor:
    """LiteParse 正文抽取器，并承载当前 PDF 资源构建逻辑。"""

    _TEXT_BASED_EXTENSIONS = {"txt", "md", "markdown"}
    _CLI_CANDIDATES = ("lit", "liteparse")

    def __init__(
        self,
        env_content: Optional[Dict[str, Any]] = None,
    ):
        self._env_content = env_content or {}

    async def parse_to_artifact_result(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service: Optional["AftsService"],
        markdown_assembler: MarkdownAssembler,
        stage_logger: Optional[DocumentParseLogger] = None,
    ) -> tuple[MarkdownArtifact, Any]:
        if LiteParse is None:
            raise RuntimeError(
                "LiteParse Python SDK 未安装，请确认 filesystem_server 已安装 `liteparse` 依赖"
            )

        parse_result = None
        with self._stage_context(
            stage_logger,
            "content_extract",
            parser="text_reader" if self._is_text_based_format(file_path) else "liteparse",
        ):
            if self._is_text_based_format(file_path):
                markdown_text = await self._parse_text_based_document(file_path)
            else:
                parser = self._create_parser()
                parse_result = await self._parse(parser, file_path)
                markdown_text = self._extract_text(parse_result)

        with self._stage_context(
            stage_logger,
            "asset_extract",
            asset_backend="pdf_extractors",
            afts_enabled=bool(afts_service),
            pdf_upload_enabled=self._should_upload_pdf_images_to_afts(),
        ):
            assets = await self._extract_pdf_assets(
                file_path=file_path,
                task_id=task_id,
                source_file_name=source_file_name,
                afts_service=afts_service,
            )
        artifact = MarkdownArtifact(
            markdown_text=markdown_text,
            assets=assets,
            diagnostics={
                "task_id": task_id,
                "source_file_name": source_file_name,
                "provider": "liteparse",
                "provider_version": self._liteparse_version(),
                "page_count": self._pdf_page_count(file_path),
                "asset_count": len(assets),
            },
        )
        with self._stage_context(
            stage_logger,
            "markdown_assemble",
            asset_count=len(assets),
        ):
            artifact.markdown_text = markdown_assembler.assemble(artifact)
        return artifact, parse_result

    @staticmethod
    def _liteparse_version() -> str | None:
        try:
            return importlib.metadata.version("liteparse")
        except importlib.metadata.PackageNotFoundError:
            return None

    @staticmethod
    def _pdf_page_count(file_path: Path) -> int:
        if file_path.suffix.lower() != ".pdf":
            return 0
        try:
            from pypdf import PdfReader

            return len(PdfReader(str(file_path)).pages)
        except Exception:
            logger.debug("failed to inspect PDF page count", exc_info=True)
            return 0

    def _stage_context(
        self,
        stage_logger: Optional[DocumentParseLogger],
        stage: str,
        **fields: Any,
    ) -> "_NoOpStageContext | Any":
        if stage_logger is None:
            return _NoOpStageContext()
        return stage_logger.stage(stage, **fields)

    async def _extract_pdf_assets(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service: Optional["AftsService"],
    ) -> list[DocumentAsset]:
        if file_path.suffix.lower() != ".pdf":
            return []
        if not afts_service:
            return []
        if not self._should_upload_pdf_images_to_afts():
            return []

        password = self._get_str_option("liteparse_password")
        use_vlm_for_all_pdf_images = self._should_extract_all_pdf_images_with_vlm()
        layout_result = None
        assets: list[DocumentAsset] = []
        if not use_vlm_for_all_pdf_images:
            try:
                layout_service = PdfLayoutExtractService()
                layout_result = layout_service.extract_from_pdf(
                    file_path=file_path,
                    task_id=task_id,
                    source_file_name=source_file_name,
                    password=password,
                )
            except BaseException as e:
                logger.warning(
                    "liteparse_document_service failed to extract PDF layout, fallback to append-only mode | "
                    f"file_path={file_path} error={e}",
                    exc_info=True,
                )

        has_positioned_layout_images = bool(layout_result and layout_result.get("images"))
        if has_positioned_layout_images and not use_vlm_for_all_pdf_images:
            positioned_assets = await self._build_positioned_pdf_image_assets(
                layout_result["pages"],
                afts_service,
            )
            self._validate_published_assets(positioned_assets)
            assets.extend(positioned_assets)

        try:
            figure_service = PdfFigureExtractService(
                figure_locate_url=self._resolve_figure_locate_url(),
            )
            figure_result = figure_service.extract_from_pdf(
                file_path=file_path,
                task_id=task_id,
                source_file_name=source_file_name,
                password=password,
            )
        except BaseException as e:
            logger.warning(
                "liteparse_document_service failed to extract PDF figure screenshots, skip figure crops | "
                f"file_path={file_path} error={e}",
                exc_info=True,
            )
            figure_result = None

        if figure_result and figure_result.get("figures"):
            figure_assets = await self._build_positioned_pdf_figure_assets(
                figure_result["figures"],
                afts_service,
            )
            self._validate_published_assets(figure_assets)
            assets.extend(figure_assets)

        if use_vlm_for_all_pdf_images:
            return assets

        if has_positioned_layout_images:
            return assets

        extract_service = PdfImageExtractService()
        extract_result = extract_service.extract_from_pdf(
            file_path=file_path,
            task_id=task_id,
            source_file_name=source_file_name,
            password=password,
        )
        append_only_assets = await self._build_append_only_pdf_image_assets(
            extract_result["images"],
            afts_service,
        )
        self._validate_published_assets(append_only_assets)
        assets.extend(append_only_assets)
        return assets

    async def _build_positioned_pdf_image_assets(
        self,
        pages: list[dict[str, Any]],
        afts_service: "AftsService",
    ) -> list[DocumentAsset]:
        assets: list[DocumentAsset] = []
        image_index = 1

        for page in pages:
            page_texts = page.get("texts", [])
            for image in page.get("images", []):
                image_path = image.get("path")
                if not isinstance(image_path, Path):
                    continue
                top = int(image.get("top", 0))
                left = int(image.get("left", 0))
                assets.append(
                    DocumentAsset(
                        asset_id=f"embedded_image_{image_index}",
                        kind="embedded_image",
                        local_path=image_path,
                        page_number=int(image.get("page_number", 0)),
                        order=image_index,
                        anchor=DocumentAnchor(
                            page_number=int(image.get("page_number", 0)),
                            top=top,
                            left=left,
                            before_snippets=self._build_layout_context_snippets(
                                page_texts=page_texts,
                                image_top=top,
                                direction="before",
                            ),
                            after_snippets=self._build_layout_context_snippets(
                                page_texts=page_texts,
                                image_top=top,
                                direction="after",
                            ),
                        ),
                        meta={
                            "name": image_path.name,
                            "index": str(image_index),
                        },
                    )
                )
                image_index += 1

        return await self._publish_document_assets(assets, afts_service)

    async def _build_positioned_pdf_figure_assets(
        self,
        figures: list[dict[str, Any]],
        afts_service: "AftsService",
    ) -> list[DocumentAsset]:
        assets: list[DocumentAsset] = []
        for figure_index, figure in enumerate(figures, start=1):
            figure_path = figure.get("path")
            if not isinstance(figure_path, Path):
                continue

            figure_bbox = figure.get("figure_bbox", {}) or {}
            assets.append(
                DocumentAsset(
                    asset_id=f"figure_crop_{figure_index}",
                    kind="figure_crop",
                    local_path=figure_path,
                    page_number=int(figure.get("page_number", 0)),
                    order=figure_index,
                    anchor=DocumentAnchor(
                        page_number=int(figure.get("page_number", 0)),
                        top=int(figure_bbox.get("top", 0)),
                        left=int(figure_bbox.get("left", 0)),
                        before_snippets=[],
                        after_snippets=[
                            snippet
                            for snippet in figure.get("after_snippets", [])
                            if isinstance(snippet, str) and snippet.strip()
                        ],
                    ),
                    meta={
                        "name": figure_path.name,
                        "index": str(figure_index),
                        "caption": str(figure.get("caption", "") or ""),
                        "locator": str(figure.get("locator", "") or ""),
                    },
                )
            )

        return await self._publish_document_assets(assets, afts_service)

    def _build_layout_context_snippets(
        self,
        *,
        page_texts: list[dict[str, Any]],
        image_top: int,
        direction: str,
    ) -> list[str]:
        if not page_texts:
            return []

        before_items = [
            item
            for item in page_texts
            if str(item.get("text", "")).strip() and int(item.get("top", 0)) <= image_top
        ]
        after_items = [
            item
            for item in page_texts
            if str(item.get("text", "")).strip() and int(item.get("top", 0)) > image_top
        ]

        if direction == "before":
            selected = [str(item["text"]).strip() for item in before_items[-3:]]
            candidates = [
                " ".join(selected[-2:]).strip(),
                selected[-1].strip() if selected else "",
            ]
        else:
            selected = [str(item["text"]).strip() for item in after_items[:3]]
            candidates = [
                " ".join(selected[:2]).strip(),
                selected[0].strip() if selected else "",
            ]

        snippets: list[str] = []
        for candidate in candidates:
            cleaned = " ".join(candidate.split()).strip()
            if cleaned and cleaned not in snippets and len(cleaned) >= 8:
                snippets.append(cleaned)
        return snippets

    def _is_text_based_format(self, file_path: Path) -> bool:
        return file_path.suffix.lower().lstrip(".") in self._TEXT_BASED_EXTENSIONS

    async def _parse_text_based_document(self, file_path: Path) -> str:
        start_time = time.time()
        cli_command = self._resolve_cli_command()
        if not cli_command:
            raise RuntimeError("LiteParse CLI 不可用，无法解析文本类文件")

        cmd = cli_command + ["parse", str(file_path), "--format", "text", "-q"]
        logger.info(
            "liteparse_document_service text-based CLI parse started | "
            f"file_path={file_path} cmd={cmd}"
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(
                f"LiteParse CLI 解析失败: {stderr or f'exit code {result.returncode}'}"
            )
        logger.info(
            "liteparse_document_service text-based CLI parse finished | "
            f"file_path={file_path} stdout_length={len(result.stdout or '')} duration={time.time() - start_time:.3f}s"
        )
        return result.stdout

    def _create_parser(self) -> Any:
        parser_kwargs = self._filter_supported_kwargs(
            LiteParse.__init__,
            self._build_parser_init_kwargs(),
        )
        try:
            return LiteParse(**parser_kwargs)
        except TypeError as exc:
            if not parser_kwargs or "unexpected keyword argument" not in str(exc):
                raise

            logger.warning(
                "liteparse_document_service parser init rejected kwargs, retry without kwargs | "
                f"rejected_kwargs={sorted(parser_kwargs)} error={exc}"
            )
            return LiteParse()

    async def _parse(self, parser: Any, file_path: Path) -> Any:
        parse_method = getattr(parser, "parse_async", None)
        parse_method_name = "parse_async"
        if parse_method is None:
            parse_method = getattr(parser, "parse", None)
            parse_method_name = "parse"
        if parse_method is None:
            raise RuntimeError("LiteParse SDK 不存在 parse / parse_async 方法")

        parse_kwargs = self._filter_supported_kwargs(
            parse_method,
            self._build_parse_kwargs(),
        )
        parse_start = time.time()
        logger.info(
            "liteparse_document_service sdk parse started | "
            f"file_path={file_path} parser_type={type(parser).__name__} "
            f"parse_method={parse_method_name} "
            f"parse_kwargs={parse_kwargs}"
        )

        try:
            if parse_method_name == "parse_async":
                result = await parse_method(str(file_path), **parse_kwargs)
            else:
                result = await asyncio.to_thread(parse_method, str(file_path), **parse_kwargs)
        except TypeError as exc:
            if not parse_kwargs or "unexpected keyword argument" not in str(exc):
                raise

            logger.warning(
                "liteparse_document_service parse rejected kwargs, retry without kwargs | "
                f"file_path={file_path} parse_method={parse_method_name} "
                f"rejected_kwargs={sorted(parse_kwargs)} error={exc}"
            )
            if parse_method_name == "parse_async":
                result = await parse_method(str(file_path))
            else:
                result = await asyncio.to_thread(parse_method, str(file_path))

        logger.info(
            "liteparse_document_service sdk parse finished | "
            f"file_path={file_path} result_type={type(result).__name__} total_duration={time.time() - parse_start:.3f}s"
        )
        return result

    def _build_parse_kwargs(self) -> Dict[str, Any]:
        parse_kwargs = {}

        target_pages = self._get_str_option("liteparse_target_pages")
        if target_pages:
            parse_kwargs["target_pages"] = target_pages

        dpi = self._get_int_option("liteparse_dpi")
        if dpi is not None:
            parse_kwargs["dpi"] = dpi

        ocr_enabled = self._get_bool_option("liteparse_ocr_enabled", default=False)
        if ocr_enabled is not None:
            parse_kwargs["ocr_enabled"] = ocr_enabled

        ocr_language = self._get_str_option("liteparse_ocr_language")
        if ocr_language:
            parse_kwargs["ocr_language"] = ocr_language

        ocr_server_url = self._get_str_option("liteparse_ocr_server_url")
        if ocr_server_url:
            parse_kwargs["ocr_server_url"] = ocr_server_url

        num_workers = self._get_int_option("liteparse_num_workers")
        if num_workers is not None:
            parse_kwargs["num_workers"] = num_workers

        max_pages = self._get_int_option("liteparse_max_pages")
        if max_pages is not None:
            parse_kwargs["max_pages"] = max_pages

        preserve_very_small_text = self._get_bool_option("liteparse_preserve_very_small_text")
        if preserve_very_small_text is not None:
            parse_kwargs["preserve_very_small_text"] = preserve_very_small_text

        password = self._get_str_option("liteparse_password")
        if password:
            parse_kwargs["password"] = password

        precise_bounding_box = self._get_bool_option("liteparse_precise_bounding_box")
        if precise_bounding_box is not None:
            parse_kwargs["precise_bounding_box"] = precise_bounding_box

        timeout = self._get_float_option("liteparse_timeout_seconds", default=120.0)
        if timeout is not None:
            parse_kwargs["timeout"] = timeout

        return parse_kwargs

    def _resolve_figure_locate_url(self) -> str | None:
        configured_url = self._get_str_option("liteparse_figure_locate_url")
        if configured_url:
            return configured_url

        ocr_server_url = self._get_str_option("liteparse_ocr_server_url")
        if not ocr_server_url:
            return None

        normalized_url = ocr_server_url.rstrip("/")
        if normalized_url.endswith("/ocr"):
            return f"{normalized_url[:-4]}/figure-locate"
        return None

    def _build_parser_init_kwargs(self) -> Dict[str, Any]:
        parser_kwargs = self._build_parse_kwargs()

        cli_path = self._get_str_option("liteparse_cli_path")
        if cli_path:
            parser_kwargs["cli_path"] = cli_path

        install_if_not_available = self._get_bool_option(
            "liteparse_install_if_not_available", default=True
        )
        if install_if_not_available is not None:
            parser_kwargs["install_if_not_available"] = bool(install_if_not_available)

        return parser_kwargs

    def _resolve_cli_command(self) -> Optional[list[str]]:
        configured_cli_path = self._get_str_option("liteparse_cli_path")
        if configured_cli_path:
            return shlex.split(configured_cli_path)

        for candidate in self._CLI_CANDIDATES:
            resolved = shutil.which(candidate)
            if resolved:
                return [resolved]
        return None

    def _filter_supported_kwargs(
        self,
        callable_obj: Any,
        candidate_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not candidate_kwargs:
            return {}

        try:
            signature = inspect.signature(callable_obj)
        except (TypeError, ValueError):
            logger.info(
                "liteparse_document_service cannot inspect callable signature, keep kwargs as-is | "
                f"callable={callable_obj} keys={sorted(candidate_kwargs)}"
            )
            return candidate_kwargs

        if any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ):
            return candidate_kwargs

        supported_keys = {
            name
            for name, parameter in signature.parameters.items()
            if name not in {"self", "cls"}
            and parameter.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        }
        filtered_kwargs = {
            key: value for key, value in candidate_kwargs.items() if key in supported_keys
        }
        dropped_keys = sorted(set(candidate_kwargs) - set(filtered_kwargs))
        if dropped_keys:
            logger.info(
                "liteparse_document_service drop unsupported liteparse kwargs | "
                f"callable={callable_obj} dropped_keys={dropped_keys}"
            )
        return filtered_kwargs

    def _extract_text(self, parse_result: Any) -> str:
        if parse_result is None:
            return ""

        if isinstance(parse_result, str):
            return parse_result

        for attr_name in ("text", "markdown", "content", "text_content"):
            value = getattr(parse_result, attr_name, None)
            if isinstance(value, str) and value.strip():
                return value

        if isinstance(parse_result, dict):
            for key in ("text", "markdown", "content", "text_content"):
                value = parse_result.get(key)
                if isinstance(value, str) and value.strip():
                    return value

        return str(parse_result)

    async def _upload_pdf_images_to_afts(
        self,
        image_paths: list[Path],
        afts_service: "AftsService",
    ) -> list[dict[str, str]]:
        published_assets = await self._build_append_only_pdf_image_assets(image_paths, afts_service)
        return [
            {
                "path": str(asset.local_path) if asset.local_path is not None else "",
                "file_id": asset.remote_id,
                "name": str(asset.meta.get("name", "")),
                "index": str(asset.meta.get("index", "")),
            }
            for asset in published_assets
            if asset.local_path is not None
        ]

    async def _build_append_only_pdf_image_assets(
        self,
        image_paths: list[Path],
        afts_service: "AftsService",
    ) -> list[DocumentAsset]:
        assets = [
            DocumentAsset(
                asset_id=f"pdf_image_{image_index}",
                kind="embedded_image",
                local_path=image_path,
                order=image_index,
                meta={
                    "name": image_path.name,
                    "index": str(image_index),
                    "placement": "append_only",
                },
            )
            for image_index, image_path in enumerate(image_paths, start=1)
        ]
        return await self._publish_document_assets(assets, afts_service)

    async def _publish_document_assets(
        self,
        assets: list[DocumentAsset],
        afts_service: "AftsService",
    ) -> list[DocumentAsset]:
        publisher = AftsDocumentAssetPublisher(afts_service)
        published_assets = await publisher.publish_assets(assets)
        for asset in published_assets:
            if asset.local_path is None:
                continue
            if asset.remote_id:
                logger.info(
                    "liteparse_document_service published document asset | asset_id=%s local_path=%s remote_id=%s",
                    asset.asset_id,
                    asset.local_path,
                    asset.remote_id,
                )
                continue
            logger.warning(
                "liteparse_document_service failed to publish document asset | asset_id=%s local_path=%s",
                asset.asset_id,
                asset.local_path,
            )
        return published_assets

    @staticmethod
    def _document_asset_to_positioned_info(asset: DocumentAsset) -> dict[str, str | int | list[str]]:
        return {
            "path": str(asset.local_path) if asset.local_path is not None else "",
            "file_id": asset.remote_id,
            "name": str(asset.meta.get("name", "")),
            "index": str(asset.meta.get("index", "")),
            "page_number": asset.page_number,
            "top": asset.anchor.top,
            "left": asset.anchor.left,
            "before_snippets": list(asset.anchor.before_snippets),
            "after_snippets": list(asset.anchor.after_snippets),
        }

    @staticmethod
    def _positioned_info_to_document_asset(
        image_info: dict[str, str | int | list[str]],
    ) -> DocumentAsset:
        return DocumentAsset(
            asset_id=f"compat_{str(image_info.get('index', '') or '')}",
            kind="embedded_image",
            remote_id=str(image_info.get("file_id", "") or ""),
            page_number=int(image_info.get("page_number", 0) or 0),
            order=int(str(image_info.get("index", "0") or "0") or 0),
            anchor=DocumentAnchor(
                page_number=int(image_info.get("page_number", 0) or 0),
                top=int(image_info.get("top", 0) or 0),
                left=int(image_info.get("left", 0) or 0),
                before_snippets=[
                    snippet
                    for snippet in image_info.get("before_snippets", [])
                    if isinstance(snippet, str) and snippet.strip()
                ],
                after_snippets=[
                    snippet
                    for snippet in image_info.get("after_snippets", [])
                    if isinstance(snippet, str) and snippet.strip()
                ],
            ),
            meta={
                "name": str(image_info.get("name", "") or ""),
                "index": str(image_info.get("index", "") or ""),
                "placement": "positioned",
            },
        )

    def _validate_uploaded_pdf_images(self, image_infos: list[dict[str, str]]) -> None:
        missing_file_ids = [
            image_info["name"]
            for image_info in image_infos
            if image_info.get("path") and not image_info.get("file_id")
        ]
        if missing_file_ids:
            raise RuntimeError(
                "PDF 图片上传到 AFTS 失败，无法生成 file_id 引用: " + ", ".join(missing_file_ids)
            )

    def _validate_published_assets(self, assets: list[DocumentAsset]) -> None:
        missing_remote_ids = [
            str(asset.meta.get("name", "") or asset.asset_id)
            for asset in assets
            if asset.local_path is not None and not asset.remote_id
        ]
        if missing_remote_ids:
            raise RuntimeError(
                "PDF 图片上传到 AFTS 失败，无法生成 file_id 引用: " + ", ".join(missing_remote_ids)
            )

    def _build_pdf_images_markdown_section(self, image_infos: list[dict[str, str]]) -> str:
        artifact = MarkdownArtifact(
            markdown_text="",
            assets=[
                DocumentAsset(
                    asset_id=f"append_only_{image_info.get('index', '')}",
                    kind="embedded_image",
                    remote_id=str(image_info.get("file_id", "") or ""),
                    meta={
                        "index": str(image_info.get("index", "") or ""),
                        "placement": "append_only",
                    },
                )
                for image_info in image_infos
            ],
        )
        return AnchoredMarkdownAssembler().assemble(artifact).strip()

    def _insert_pdf_images_into_markdown_by_position(
        self,
        markdown_text: str,
        positioned_image_infos: list[dict[str, str | int | list[str]]],
    ) -> str:
        artifact = MarkdownArtifact(
            markdown_text=markdown_text,
            assets=[
                self._positioned_info_to_document_asset(image_info)
                for image_info in positioned_image_infos
            ],
        )
        return AnchoredMarkdownAssembler().assemble(artifact)

    def _write_debug_sidecar(
        self,
        parse_result: Any,
        output_dir: Path,
        source_file_name: str,
    ) -> None:
        try:
            if hasattr(parse_result, "model_dump"):
                sidecar_data = parse_result.model_dump()
            elif hasattr(parse_result, "dict"):
                sidecar_data = parse_result.dict()
            elif hasattr(parse_result, "__dict__"):
                sidecar_data = parse_result.__dict__
            elif isinstance(parse_result, dict):
                sidecar_data = parse_result
            else:
                sidecar_data = {"raw": str(parse_result)}

            sidecar_path = output_dir / f"{source_file_name}.liteparse.json"
            sidecar_path.write_text(
                json.dumps(sidecar_data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            logger.info(
                "liteparse_document_service debug sidecar saved | "
                f"sidecar_path={sidecar_path}"
            )
        except Exception as e:
            logger.warning(
                "liteparse_document_service failed to save debug sidecar | "
                f"error={e}",
                exc_info=True,
            )

    def _should_output_debug_json(self) -> bool:
        return bool(self._get_bool_option("liteparse_output_debug_json", default=False))

    def should_output_debug_json(self) -> bool:
        return self._should_output_debug_json()

    def write_debug_sidecar(
        self,
        parse_result: Any,
        output_dir: Path,
        source_file_name: str,
    ) -> None:
        self._write_debug_sidecar(parse_result, output_dir, source_file_name)

    def _should_upload_pdf_images_to_afts(self) -> bool:
        return bool(self._get_bool_option("liteparse_upload_pdf_images_to_afts", default=True))

    def _should_extract_all_pdf_images_with_vlm(self) -> bool:
        configured = self._get_bool_option("liteparse_extract_all_pdf_images_with_vlm")
        if configured is not None:
            return bool(configured)
        return bool(self._resolve_figure_locate_url())

    def _get_raw_option(self, key: str) -> Any:
        if key in self._env_content and self._env_content[key] not in (None, ""):
            return self._env_content[key]
        return os.getenv(key.upper())

    def _get_str_option(self, key: str) -> Optional[str]:
        value = self._get_raw_option(key)
        if value in (None, ""):
            return None
        return str(value)

    def _get_int_option(self, key: str) -> Optional[int]:
        value = self._get_raw_option(key)
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning(
                "liteparse_document_service invalid int option | "
                f"key={key} value={value}"
            )
            return None

    def _get_float_option(self, key: str, default: Optional[float] = None) -> Optional[float]:
        value = self._get_raw_option(key)
        if value in (None, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            logger.warning(
                "liteparse_document_service invalid float option | "
                f"key={key} value={value}"
            )
            return default

    def _get_bool_option(self, key: str, default: Optional[bool] = None) -> Optional[bool]:
        value = self._get_raw_option(key)
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value

        value_str = str(value).strip().lower()
        if value_str in {"1", "true", "yes", "on"}:
            return True
        if value_str in {"0", "false", "no", "off"}:
            return False

        logger.warning(
            "liteparse_document_service invalid bool option | "
            f"key={key} value={value}"
        )
        return default


class _NoOpStageContext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class LiteParseDocumentService(LiteParseContentExtractor):
    """兼容旧导入名，待逐步迁移到按文件类型的 DocumentService。"""


class LiteParsePdfService(LiteParseContentExtractor):
    """兼容旧导入名，待逐步迁移到按文件类型的 DocumentService。"""
