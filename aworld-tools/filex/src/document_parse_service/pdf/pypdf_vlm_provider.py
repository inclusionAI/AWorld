"""PDF understanding provider based on pypdf text hints and generic VLM calls."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..document_artifact_models import MarkdownArtifact
from ..media_transcription.openai_compatible_backend import OpenAICompatibleMediaTranscriptionBackend

logger = logging.getLogger(__name__)

GATEWAY_VLLM_MODEL_ALIASES = {
    "ai_cloud_Kimi_k26_pgc": "kimi_k26_pc",
}


DEFAULT_PDF_PROMPT = (
    "请详细识别并提取这个 PDF 页面中的所有文字内容、知识点、公式、图表信息，"
    "按原文结构完整输出。忽略页眉页脚、扫描水印、本地文件路径等与正文无关的噪音信息，"
    "除非它们本身属于题目或讲义内容。"
)


@dataclass(slots=True)
class PdfTextPage:
    """Text extracted from a single PDF page by pypdf."""

    page_number: int
    text: str


@dataclass(slots=True)
class PdfVlmPage:
    """VLM understanding result for one rendered PDF page."""

    page_number: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PdfRenderedPage:
    """A rendered PDF page ready to send to VLM."""

    page_number: int
    image_data_url: str
    width: int = 0
    height: int = 0


@dataclass(slots=True)
class PypdfVlmResult:
    """Normalized output of the pypdf + VLM PDF provider."""

    provider: str
    tool: str
    source_file_name: str
    pypdf_pages: list[PdfTextPage]
    vlm_pages: list[PdfVlmPage]
    pypdf_status: str
    pypdf_reason: str = ""
    vlm_errors: list[dict[str, Any]] = field(default_factory=list)
    vlm_total_elapsed_ms: float = 0
    vlm_max_concurrency: int = 1
    first_batch_elapsed_ms: float = 0


class PypdfVlmPdfProvider:
    """Extract PDF text first, then render pages for Gateway VLM when needed."""

    name = "pypdf_vlm"

    def __init__(
        self,
        *,
        env_content: dict[str, Any] | None = None,
        vlm_backend: OpenAICompatibleMediaTranscriptionBackend | None = None,
    ) -> None:
        self._env_content = env_content or {}
        self._vlm_backend = vlm_backend or OpenAICompatibleMediaTranscriptionBackend()

    async def understand_pdf(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
    ) -> PypdfVlmResult:
        started_at = time.monotonic()
        pypdf_pages = await self._extract_text_pages(file_path)
        pypdf_char_count = sum(len(page.text.strip()) for page in pypdf_pages)
        min_chars_per_page = self._int_option("pypdf_min_chars_per_page", 120)
        sparse_text = self._is_sparse_text(
            pypdf_pages=pypdf_pages,
            min_chars_per_page=min_chars_per_page,
        )
        prompt = self._resolve_prompt()
        force_vlm = self._bool_option("force_vlm", False)
        should_call_vlm = force_vlm or bool(prompt) or sparse_text

        if not should_call_vlm and pypdf_char_count:
            return PypdfVlmResult(
                provider="pypdf_vlm",
                tool="pypdf_text",
                source_file_name=source_file_name,
                pypdf_pages=pypdf_pages,
                vlm_pages=[],
                pypdf_status="success",
                first_batch_elapsed_ms=round((time.monotonic() - started_at) * 1000, 2),
            )

        vlm_pages, vlm_errors, vlm_total_elapsed_ms, vlm_max_concurrency = await self._understand_rendered_pages(
            file_path=file_path,
            task_id=task_id,
            source_file_name=source_file_name,
            pypdf_pages=pypdf_pages,
            prompt=prompt or DEFAULT_PDF_PROMPT,
        )
        if not vlm_pages and vlm_errors:
            first_error = str(vlm_errors[0].get("error") or "").strip()
            raise RuntimeError(
                f"pypdf_vlm provider failed for all {len(vlm_errors)} rendered page(s): "
                f"{first_error or 'unknown VLM error'}"
            )
        pypdf_status = "skipped" if vlm_pages else ("success" if pypdf_char_count else "empty")
        pypdf_reason = "pdf_text_too_sparse" if sparse_text else ""
        if prompt:
            pypdf_reason = "prompt_requires_vlm"

        return PypdfVlmResult(
            provider="pypdf_vlm",
            tool="pypdf_vlm_page_understand" if vlm_pages else "pypdf_text",
            source_file_name=source_file_name,
            pypdf_pages=pypdf_pages,
            vlm_pages=vlm_pages,
            pypdf_status=pypdf_status,
            pypdf_reason=pypdf_reason,
            vlm_errors=vlm_errors,
            vlm_total_elapsed_ms=vlm_total_elapsed_ms,
            vlm_max_concurrency=vlm_max_concurrency,
            first_batch_elapsed_ms=round((time.monotonic() - started_at) * 1000, 2),
        )

    def to_markdown_artifact(self, result: PypdfVlmResult) -> MarkdownArtifact:
        """Assemble provider result into the shared Markdown artifact model."""

        pages = result.vlm_pages or [
            PdfVlmPage(page_number=page.page_number, text=page.text)
            for page in result.pypdf_pages
            if page.text.strip()
        ]
        lines = [
            f"# {result.source_file_name}",
            "",
        ]
        for page in pages:
            text = page.text.strip()
            if not text:
                continue
            lines.extend([f"### 第 {page.page_number} 页", "", text, ""])

        return MarkdownArtifact(
            markdown_text="\n".join(lines).strip() + "\n",
            diagnostics={
                "provider": result.provider,
                "tool": result.tool,
                "pypdf_status": result.pypdf_status,
                "pypdf_reason": result.pypdf_reason,
                "pypdf_page_count": len(result.pypdf_pages),
                "page_count": len(result.pypdf_pages),
                "pypdf_char_count": sum(len(page.text.strip()) for page in result.pypdf_pages),
                "vlm_page_count": len(result.vlm_pages),
                "rendered_page_count": len(result.vlm_pages) + len(result.vlm_errors),
                "model_call_count": len(result.vlm_pages) + len(result.vlm_errors),
                "vlm_errors": result.vlm_errors,
                "vlm_total_elapsed_ms": result.vlm_total_elapsed_ms,
                "vlm_max_concurrency": result.vlm_max_concurrency,
                "first_batch_duration_ms": result.first_batch_elapsed_ms,
                "vlm_retry_count": sum(
                    int(page.metadata.get("retry_count") or 0) for page in result.vlm_pages
                )
                + sum(int(error.get("retry_count") or 0) for error in result.vlm_errors),
                "vlm_error_types": sorted(
                    {
                        str(error.get("error_type"))
                        for error in result.vlm_errors
                        if error.get("error_type")
                    }
                ),
            },
        )

    async def _extract_text_pages(self, file_path: Path) -> list[PdfTextPage]:
        return await asyncio.to_thread(self._extract_text_pages_sync, file_path)

    @staticmethod
    def _extract_text_pages_sync(file_path: Path) -> list[PdfTextPage]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("pypdf_vlm PDF provider requires pypdf") from exc

        reader = PdfReader(str(file_path))
        pages: list[PdfTextPage] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # pragma: no cover - depends on pypdf internals
                logger.warning("pypdf extract_text failed | page=%s error=%s", index, exc)
                text = ""
            pages.append(PdfTextPage(page_number=index, text=text.strip()))
        return pages

    async def _understand_rendered_pages(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        pypdf_pages: list[PdfTextPage],
        prompt: str,
    ) -> tuple[list[PdfVlmPage], list[dict[str, Any]], float, int]:
        rendered_pages = await asyncio.to_thread(self._render_pages_as_data_urls, file_path)
        text_by_page = {page.page_number: page.text for page in pypdf_pages}
        max_pages = self._int_option("vlm_max_pages", 20)
        if max_pages > 0:
            rendered_pages = rendered_pages[:max_pages]

        max_concurrency = max(1, self._int_option("vlm_max_concurrency", 4))
        semaphore = asyncio.Semaphore(max_concurrency)
        started_at = time.monotonic()
        vlm_pages: list[PdfVlmPage] = []
        vlm_errors: list[dict[str, Any]] = []
        page_results = await asyncio.gather(
            *(
                self._understand_one_rendered_page(
                    rendered_page=rendered_page,
                    task_id=task_id,
                    source_file_name=source_file_name,
                    pypdf_text=text_by_page.get(rendered_page.page_number, ""),
                    prompt=prompt,
                    semaphore=semaphore,
                    max_concurrency=max_concurrency,
                )
                for rendered_page in rendered_pages
            )
        )
        for page_number, page, error in sorted(page_results, key=lambda item: item[0]):
            if error:
                vlm_errors.append(error)
            if page:
                vlm_pages.append(page)
        total_elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
        logger.info(
            "pypdf_vlm PDF VLM summary | rendered_pages=%s success_pages=%s failed_pages=%s "
            "total_elapsed_ms=%s concurrency=%s",
            len(rendered_pages),
            len(vlm_pages),
            len(vlm_errors),
            total_elapsed_ms,
            max_concurrency,
        )
        return vlm_pages, vlm_errors, total_elapsed_ms, max_concurrency

    async def _understand_one_rendered_page(
        self,
        *,
        rendered_page: PdfRenderedPage,
        task_id: str,
        source_file_name: str,
        pypdf_text: str,
        prompt: str,
        semaphore: asyncio.Semaphore,
        max_concurrency: int,
    ) -> tuple[int, PdfVlmPage | None, dict[str, Any] | None]:
        page_number = rendered_page.page_number
        page_started_at = time.monotonic()
        logger.info(
            "pypdf_vlm PDF page started | page=%s width=%s height=%s concurrency=%s",
            page_number,
            rendered_page.width,
            rendered_page.height,
            max_concurrency,
        )
        async with semaphore:
            options = self._build_vlm_options(
                task_id=task_id,
                source_file_name=source_file_name,
                page_number=page_number,
                data_url=rendered_page.image_data_url,
                prompt=self._build_page_prompt(
                    prompt=prompt,
                    page_number=page_number,
                    pypdf_text=pypdf_text,
                ),
            )
            result = None
            last_error: Exception | None = None
            error_type = "vlm_request_failed"
            retry_count = 0
            max_retries = max(0, self._int_option("vlm_max_retries", 3))
            while True:
                try:
                    result = await self._vlm_backend.transcribe(
                        Path(f"{source_file_name}-page-{page_number}.jpg"),
                        media_type="image",
                        file_type="jpg",
                        source_file_name=f"{source_file_name}-page-{page_number}",
                        options=options,
                    )
                    if not result.text.strip() and retry_count < max_retries:
                        retry_count += 1
                        delay_ms = self._retry_delay_ms(retry_count)
                        logger.warning(
                            "pypdf_vlm provider retrying empty VLM response | page=%s retry=%s "
                            "max_retries=%s delay_ms=%s metadata=%s",
                            page_number,
                            retry_count,
                            max_retries,
                            delay_ms,
                            result.metadata,
                        )
                        await asyncio.sleep(delay_ms / 1000)
                        continue
                    break
                except Exception as exc:
                    last_error = exc
                    error_type = self._classify_vlm_error(exc)
                    if retry_count >= max_retries or not self._is_retryable_vlm_error(error_type):
                        break
                    retry_count += 1
                    delay_ms = self._retry_delay_ms(retry_count)
                    logger.warning(
                        "pypdf_vlm provider retrying VLM | page=%s retry=%s max_retries=%s "
                        "delay_ms=%s error_type=%s error=%s",
                        page_number,
                        retry_count,
                        max_retries,
                        delay_ms,
                        error_type,
                        exc,
                    )
                    await asyncio.sleep(delay_ms / 1000)
            if result is None:
                elapsed_ms = round((time.monotonic() - page_started_at) * 1000, 2)
                logger.warning(
                    "pypdf_vlm provider failed to call VLM | page=%s elapsed_ms=%s "
                    "retry_count=%s error_type=%s error=%s",
                    page_number,
                    elapsed_ms,
                    retry_count,
                    error_type,
                    last_error,
                )
                return page_number, None, {
                    "page_number": page_number,
                    "elapsed_ms": elapsed_ms,
                    "error_type": error_type,
                    "retry_count": retry_count,
                    "error": str(last_error or "unknown VLM error"),
                }
            if not result.text.strip():
                elapsed_ms = round((time.monotonic() - page_started_at) * 1000, 2)
                logger.warning(
                    "pypdf_vlm provider got empty VLM response | page=%s elapsed_ms=%s metadata=%s",
                    page_number,
                    elapsed_ms,
                    result.metadata,
                )
                return page_number, None, {
                    "page_number": page_number,
                    "elapsed_ms": elapsed_ms,
                    "error": "empty VLM response",
                    "error_type": "vlm_empty_response",
                    "retry_count": retry_count,
                    "metadata": result.metadata,
                }
        elapsed_ms = round((time.monotonic() - page_started_at) * 1000, 2)
        logger.info(
            "pypdf_vlm PDF page finished | page=%s elapsed_ms=%s content_length=%s",
            page_number,
            elapsed_ms,
            len(result.text.strip()),
        )
        metadata = dict(result.metadata)
        metadata.update(
            {
                "width": rendered_page.width,
                "height": rendered_page.height,
                "elapsed_ms": elapsed_ms,
                "retry_count": retry_count,
            }
        )
        return page_number, PdfVlmPage(
            page_number=page_number,
            text=result.text,
            metadata=metadata,
        ), None

    @staticmethod
    def _classify_vlm_error(exc: Exception) -> str:
        message = str(exc).lower()
        if "429" in message or "rate limit" in message or "限流" in message or "并发" in message:
            return "vlm_rate_limited"
        if "timeout" in message or "timed out" in message or "超时" in message:
            return "vlm_page_timeout"
        if any(status in message for status in ("500", "502", "503", "504")):
            return "vlm_temporary_unavailable"
        if "401" in message or "403" in message or "unauthorized" in message:
            return "vlm_unauthorized"
        return "vlm_request_failed"

    @staticmethod
    def _is_retryable_vlm_error(error_type: str) -> bool:
        return error_type in {"vlm_rate_limited", "vlm_page_timeout", "vlm_temporary_unavailable"}

    def _retry_delay_ms(self, retry_count: int) -> int:
        base_ms = max(0, self._int_option("vlm_retry_base_delay_ms", 500))
        max_ms = max(base_ms, self._int_option("vlm_retry_max_delay_ms", 8000))
        return min(max_ms, base_ms * (2 ** max(0, retry_count - 1)))

    def _render_pages_as_data_urls(self, file_path: Path) -> list[PdfRenderedPage]:
        try:
            from pdf2image import convert_from_path
        except ImportError as exc:
            raise RuntimeError("pypdf_vlm PDF provider requires pdf2image") from exc

        dpi = self._int_option("render_dpi", 150)
        jpeg_quality = self._int_option("jpeg_quality", 85)
        max_pages = self._int_option("vlm_max_pages", 20)
        render_kwargs: dict[str, Any] = {"dpi": dpi}
        if max_pages > 0:
            render_kwargs["last_page"] = max_pages
        pages = convert_from_path(str(file_path), **render_kwargs)
        rendered: list[PdfRenderedPage] = []
        for index, image in enumerate(pages, start=1):
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=jpeg_quality)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            rendered.append(
                PdfRenderedPage(
                    page_number=index,
                    image_data_url=f"data:image/jpeg;base64,{encoded}",
                    width=int(getattr(image, "width", 0) or 0),
                    height=int(getattr(image, "height", 0) or 0),
                )
            )
        return rendered

    def _build_vlm_options(
        self,
        *,
        task_id: str,
        source_file_name: str,
        page_number: int,
        data_url: str,
        prompt: str,
    ) -> dict[str, Any]:
        options = {
            "backend": "openai_compatible",
            "file_id": f"{source_file_name}-page-{page_number}",
            "media_url": data_url,
            "media_content_type": "image_url",
            "session_id": task_id,
            "trace_id": f"{task_id}_page_{page_number}",
            "prompt": prompt,
            "timeout_seconds": self._int_option("vlm_timeout_seconds", 300),
        }
        gateway_vllm_config = self._dict_option("gateway_vllm")
        if gateway_vllm_config:
            for source_key, option_key in {
                "base_url": "base_url",
                "endpoint": "endpoint",
                "api_key": "api_key",
                "auth_token": "auth_token",
                "authorization": "authorization",
                "model_name": "model",
                "model": "model",
                "temperature": "temperature",
                "max_tokens": "max_tokens",
                "media_content_type": "media_content_type",
                "text_content_type": "text_content_type",
            }.items():
                if source_key in gateway_vllm_config and option_key not in options:
                    options[option_key] = gateway_vllm_config[source_key]
            model_name = str(
                gateway_vllm_config.get("http_model_name")
                or gateway_vllm_config.get("resolved_model_name")
                or gateway_vllm_config.get("model_name")
                or gateway_vllm_config.get("model")
                or ""
            ).strip()
            if model_name:
                options["model"] = GATEWAY_VLLM_MODEL_ALIASES.get(model_name, model_name)
            extra_body = dict(gateway_vllm_config.get("extra_body") or {})
            extra_body.setdefault("enable_maya_new_inference_protocol", True)
            extra_body.setdefault("enable_sec_check", True)
            options.setdefault("extra_body", extra_body)
        self._apply_paddle_vlm_fallback(options)
        for env_key, option_key in {
            "vlm_base_url": "base_url",
            "vlm_endpoint": "endpoint",
            "vlm_api_key": "api_key",
            "vlm_auth_token": "auth_token",
            "vlm_authorization": "authorization",
            "vlm_model": "model",
            "vlm_temperature": "temperature",
            "vlm_max_tokens": "max_tokens",
            "openai_compatible_base_url": "base_url",
            "openai_compatible_endpoint": "endpoint",
            "openai_compatible_api_key": "api_key",
            "openai_compatible_auth_token": "auth_token",
            "openai_compatible_authorization": "authorization",
            "openai_compatible_model": "model",
        }.items():
            prefixed_key = f"pdf_{env_key}"
            if prefixed_key in self._env_content and option_key not in options:
                options[option_key] = self._env_content[prefixed_key]
            if env_key in self._env_content and option_key not in options:
                options[option_key] = self._env_content[env_key]
        return options

    def _apply_paddle_vlm_fallback(self, options: dict[str, Any]) -> None:
        fallback_mapping = {
            "base_url": ("pdf_paddle_ocr_vl_rec_server_url", "paddle_ocr_vl_rec_server_url"),
            "api_key": ("pdf_paddle_ocr_vl_rec_api_key", "paddle_ocr_vl_rec_api_key"),
            "model": ("pdf_paddle_ocr_vl_rec_api_model_name", "paddle_ocr_vl_rec_api_model_name"),
        }
        for option_key, candidate_keys in fallback_mapping.items():
            if options.get(option_key):
                continue
            for candidate_key in candidate_keys:
                value = str(self._env_content.get(candidate_key) or "").strip()
                if value:
                    options[option_key] = value
                    break

    def _resolve_prompt(self) -> str:
        return str(
            self._env_content.get("pdf_understand_prompt")
            or self._env_content.get("pdf_vlm_prompt")
            or self._env_content.get("pdf_prompt")
            or self._env_content.get("understand_prompt")
            or self._env_content.get("prompt")
            or ""
        ).strip()

    @staticmethod
    def _build_page_prompt(*, prompt: str, page_number: int, pypdf_text: str) -> str:
        normalized_prompt = str(prompt or "").strip() or DEFAULT_PDF_PROMPT
        lines = [
            normalized_prompt,
            "",
            f"这是 PDF 第 {page_number} 页的渲染图，请只输出这一页可见的有效教学内容。",
        ]
        if pypdf_text.strip():
            lines.extend(
                [
                    "",
                    "以下是 pypdf 从本页抽取到的文本层，仅作参考；如果它和图片内容不一致，请以图片为准：",
                    _truncate_text(pypdf_text.strip(), 4000),
                ]
            )
        return "\n".join(lines).strip()

    @staticmethod
    def _is_sparse_text(*, pypdf_pages: list[PdfTextPage], min_chars_per_page: int) -> bool:
        if not pypdf_pages:
            return True
        char_count = sum(len(page.text.strip()) for page in pypdf_pages)
        return char_count < max(1, len(pypdf_pages)) * max(0, min_chars_per_page)

    def _int_option(self, key: str, default: int) -> int:
        raw_value = self._env_content.get(f"pdf_{key}", self._env_content.get(key, default))
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return default

    def _bool_option(self, key: str, default: bool) -> bool:
        raw_value = self._env_content.get(f"pdf_{key}", self._env_content.get(key, default))
        if isinstance(raw_value, bool):
            return raw_value
        if raw_value is None:
            return default
        return str(raw_value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _dict_option(self, key: str) -> dict[str, Any]:
        for candidate in (f"pdf_{key}", key):
            value = self._env_content.get(candidate)
            if isinstance(value, dict):
                return value
        return {}


def _truncate_text(text: str, limit: int) -> str:
    normalized = str(text or "")
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."
