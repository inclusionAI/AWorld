"""PDF provider backed by PaddleOCR's official PaddleOCR-VL pipeline."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from html import escape, unescape
from pathlib import Path
from typing import Any

from ..document_artifact_models import DocumentAnchor, DocumentAsset, MarkdownArtifact
from ..paths import DOCUMENT_PARSE_WORKSPACE
from .text_layer_formatting import (
    document_ir_spans,
    extract_text_layer_spans,
    overlay_text_layer_formatting,
)

logger = logging.getLogger(__name__)
_SHARED_PIPELINES: dict[str, Any] = {}
_SHARED_PIPELINES_LOCK = threading.Lock()

_LEGACY_CHART_PROMPT = "Chart Recognition:"
_STRUCTURED_CHART_PROMPT = """Chart Recognition:
Extract all visible chart data into one or more Markdown tables.
Return Markdown tables only. Do not return prose, bullets, commentary, or a chart summary.
Put every label, series name, category, date or year, and numeric value in its own cell.
Use one data point per row. For approximate points, emit one best numeric estimate rather
than a range. Preserve panel titles as a table column or a short heading immediately before
the corresponding table. Never wrap a narrative sentence in a one-column table."""
_CHART_CORRECTION_PROMPT = """Chart Recognition:
CORRECTION ATTEMPT {attempt}: the previous response violated the chart table contract.
Read the chart image again; do not reformat or summarize the previous answer.
Return one or more Markdown tables and nothing else. Every table must have at least two
columns. Put labels/categories/series in separate cells and every visible numeric value in
its own numeric cell. Use one observation per row. Do not emit prose, bullets, JSON,
one-column tables, ranges, or commentary. If a value is approximate, emit one best numeric
estimate with an optional ~ prefix. Never invent a value that is not visible in the chart.
The rejected output failed these checks: {failures}"""
_MARKDOWN_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_NUMERIC_CHART_CELL = re.compile(
    r"^[~≈]?\s*[$€£¥]?\s*[-+]?(?:\d[\d, ]*|\d*\.\d+)"
    r"(?:\.\d+)?\s*(?:%|[kKmMbBtT]|million|billion|trillion)?$",
    re.IGNORECASE,
)
_HTML_TABLE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.IGNORECASE | re.DOTALL)
_HTML_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
_CHART_PROMPT_CONTEXT = threading.local()


class PaddleOcrModelAssetsError(RuntimeError):
    """A local pipeline model could not be acquired from the model hosts."""


class PaddleOcrChartContractError(RuntimeError):
    """Chart recognition returned content that official chart scorers cannot use."""


@dataclass(slots=True)
class PaddleOcrPdfResult:
    """Normalized output of PaddleOCR-VL PDF parsing."""

    provider: str
    tool: str
    source_file_name: str
    markdown_text: str
    assets: list[DocumentAsset] = field(default_factory=list)
    page_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    total_elapsed_ms: float = 0
    initialization_elapsed_ms: float = 0
    parse_elapsed_ms: float = 0
    model_info: dict[str, Any] = field(default_factory=dict)
    raw_result_count: int = 0
    model_call_count: int = 0
    peak_concurrency: int = 0
    retry_count: int = 0
    provider_version: str = ""
    first_batch_elapsed_ms: float = 0
    document_ir: dict[str, Any] | None = None


class PaddleOcrPdfProvider:
    """Use PaddleOCR's official PaddleOCR-VL document parsing pipeline."""

    name = "paddle_ocr"

    def __init__(
        self, *, env_content: dict[str, Any] | None = None, pipeline: Any | None = None
    ) -> None:
        self._env_content = env_content or {}
        self._pipeline = pipeline

    async def understand_pdf(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_name: str,
    ) -> PaddleOcrPdfResult:
        started_at = time.monotonic()
        return await asyncio.to_thread(
            self._understand_pdf_sync,
            file_path,
            task_id,
            source_file_name,
            started_at,
        )

    def _understand_pdf_sync(
        self,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        started_at: float,
    ) -> PaddleOcrPdfResult:
        pipeline = self._resolve_pipeline()
        initialization_elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
        parse_started_at = time.monotonic()
        predict_kwargs = self._predict_kwargs()
        raw_results, retry_count, first_batch_elapsed_ms = self._predict_with_retries(
            pipeline,
            file_path=file_path,
            predict_kwargs=predict_kwargs,
            started_at=parse_started_at,
        )
        markdown_parts = [self._extract_markdown_data(result) for result in raw_results]
        markdown_text = self._concatenate_markdown(pipeline, markdown_parts)
        text_layer_pages = (
            extract_text_layer_spans(file_path)
            if self._bool_option("text_layer_formatting", False)
            else []
        )
        if text_layer_pages:
            markdown_text = overlay_text_layer_formatting(
                markdown_text, text_layer_pages
            )
        assets = self._write_markdown_images(
            markdown_parts,
            task_id=task_id,
            source_file_name=source_file_name,
        )
        document_ir = self._build_document_ir(
            raw_results, text_layer_pages=text_layer_pages
        )

        return PaddleOcrPdfResult(
            provider="paddle_ocr",
            tool="paddleocr_vl",
            source_file_name=source_file_name,
            markdown_text=markdown_text,
            assets=assets,
            page_count=self._resolve_page_count(raw_results),
            total_elapsed_ms=round((time.monotonic() - started_at) * 1000, 2),
            initialization_elapsed_ms=initialization_elapsed_ms,
            parse_elapsed_ms=round((time.monotonic() - parse_started_at) * 1000, 2),
            model_info=self._model_info(),
            raw_result_count=len(raw_results),
            model_call_count=self._model_call_count(raw_results),
            peak_concurrency=int(self._option("vl_rec_max_concurrency") or 0),
            retry_count=retry_count,
            provider_version=self._provider_version(),
            first_batch_elapsed_ms=first_batch_elapsed_ms,
            document_ir=document_ir,
        )

    def to_markdown_artifact(self, result: PaddleOcrPdfResult) -> MarkdownArtifact:
        markdown_text = result.markdown_text.strip()
        if not markdown_text:
            markdown_text = f"# {result.source_file_name}\n"

        return MarkdownArtifact(
            markdown_text=markdown_text.rstrip() + "\n",
            assets=result.assets,
            document_ir=result.document_ir,
            diagnostics={
                "provider": result.provider,
                "tool": result.tool,
                "page_count": result.page_count,
                "ocr_page_count": result.page_count,
                "vlm_page_count": result.page_count,
                "rendered_page_count": result.page_count,
                "asset_count": len(result.assets),
                "error_count": len(result.errors),
                "errors": result.errors,
                "total_elapsed_ms": result.total_elapsed_ms,
                "initialization_elapsed_ms": result.initialization_elapsed_ms,
                "parse_elapsed_ms": result.parse_elapsed_ms,
                "first_batch_duration_ms": result.first_batch_elapsed_ms,
                "model_info": result.model_info,
                "raw_result_count": result.raw_result_count,
                "model_call_count": result.model_call_count,
                "peak_concurrency": result.peak_concurrency,
                "model_retry_count": result.retry_count,
                "provider_version": result.provider_version,
                "document_ir_schema_version": (
                    str((result.document_ir or {}).get("schema_version") or "")
                ),
                "text_length": len(result.markdown_text),
            },
        )

    @classmethod
    def _build_document_ir(
        cls,
        raw_results: list[Any],
        *,
        text_layer_pages: list[list[Any]] | None = None,
    ) -> dict[str, Any]:
        """Normalize PaddleOCR page/block geometry into a stable FileX contract."""

        pages: list[dict[str, Any]] = []
        for fallback_index, result in enumerate(raw_results):
            payload = cls._json_payload(result)
            elements: list[dict[str, Any]] = []
            blocks = payload.get("parsing_res_list")
            if not isinstance(blocks, list):
                blocks = []
            for fallback_order, block in enumerate(blocks, start=1):
                if not isinstance(block, dict):
                    continue
                bbox = block.get("block_bbox") or block.get("bbox") or []
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    continue
                try:
                    normalized_bbox = [float(value) for value in bbox]
                except (TypeError, ValueError):
                    continue
                order = block.get("block_order")
                elements.append(
                    {
                        "id": str(
                            block.get("global_block_id")
                            or block.get("block_id")
                            or f"p{fallback_index}-b{fallback_order}"
                        ),
                        "type": str(
                            block.get("block_label") or block.get("label") or "unknown"
                        ),
                        "bbox": normalized_bbox,
                        "text": str(
                            block.get("block_content") or block.get("content") or ""
                        ),
                        "reading_order": (
                            int(order) if isinstance(order, (int, float)) else None
                        ),
                        "group_id": block.get("global_group_id")
                        or block.get("group_id"),
                    }
                )
            page_index = payload.get("page_index")
            pages.append(
                {
                    "page_index": (
                        int(page_index)
                        if isinstance(page_index, (int, float))
                        else fallback_index
                    ),
                    "width": cls._numeric_dimension(payload.get("width")),
                    "height": cls._numeric_dimension(payload.get("height")),
                    "elements": elements,
                    "spans": document_ir_spans(text_layer_pages or [], fallback_index),
                }
            )
        return {
            "schema_version": "filex-document-ir-v2",
            "coordinate_system": "pixel_top_left_xyxy",
            "pages": pages,
        }

    @staticmethod
    def _json_payload(result: Any) -> dict[str, Any]:
        json_value = getattr(result, "json", None)
        if callable(json_value):
            json_value = json_value()
        if isinstance(json_value, dict):
            nested = json_value.get("res")
            return nested if isinstance(nested, dict) else json_value
        if isinstance(result, dict):
            return result
        return {}

    @staticmethod
    def _numeric_dimension(value: Any) -> float | None:
        if isinstance(value, list) and value:
            value = value[0]
        return float(value) if isinstance(value, (int, float)) else None

    def _resolve_pipeline(self) -> Any:
        if self._pipeline is not None:
            self._install_chart_prompt(self._pipeline)
            return self._pipeline
        try:
            from paddleocr import PaddleOCRVL
        except ImportError as exc:
            raise RuntimeError(
                "paddle_ocr PDF provider requires paddleocr with PaddleOCRVL support. "
                "Install paddleocr in the filesystem_server runtime or choose another pdf_parse_provider."
            ) from exc

        kwargs = self._pipeline_kwargs()
        cache_key = json.dumps(
            {
                "pipeline": kwargs,
                "chart_prompt": self._chart_recognition_prompt(),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        with _SHARED_PIPELINES_LOCK:
            self._pipeline = _SHARED_PIPELINES.get(cache_key)
            if self._pipeline is None:
                try:
                    self._pipeline = PaddleOCRVL(**kwargs)
                except Exception as exc:
                    if str(exc).strip() != (
                        "No available model hosting platforms detected. "
                        "Please check your network connection."
                    ):
                        raise
                    raise PaddleOcrModelAssetsError(
                        "PaddleOCR pipeline initialization could not find a required "
                        "model in its local cache, and its model-weight download "
                        "hosts are unavailable. Provision the enabled local models "
                        "in the runtime image/cache. For layout detection, set "
                        "FILEX_PADDLE_OCR_LAYOUT_DETECTION_MODEL_DIR to the installed "
                        "layout model directory and make it visible in the task "
                        "container. This error is about model-weight downloads; it "
                        "does not establish that the configured VLM inference API "
                        "is unavailable. Original error: " + str(exc)
                    ) from exc
                self._install_chart_prompt(self._pipeline)
                _SHARED_PIPELINES[cache_key] = self._pipeline
            else:
                self._install_chart_prompt(self._pipeline)
        return self._pipeline

    def _chart_recognition_prompt(self) -> str:
        explicit = self._option("chart_prompt")
        if explicit not in (None, ""):
            prompt = str(explicit).strip()
            if not prompt or len(prompt) > 4096:
                raise ValueError(
                    "paddle_ocr chart_prompt must contain 1-4096 characters"
                )
            return prompt
        mode = self._chart_prompt_mode()
        if mode == "legacy":
            return _LEGACY_CHART_PROMPT
        return _STRUCTURED_CHART_PROMPT

    def _chart_prompt_mode(self) -> str:
        configured = self._option("chart_prompt_mode")
        if configured in (None, ""):
            gateway_vllm = self._gateway_vllm_config()
            external_model = (
                self._option("vl_rec_api_model_name")
                or gateway_vllm.get("http_model_name")
                or gateway_vllm.get("model_name")
            )
            return "structured" if external_model not in (None, "") else "legacy"
        mode = str(configured).strip().lower()
        if mode not in {"structured", "legacy"}:
            raise ValueError(
                "paddle_ocr chart_prompt_mode must be 'structured' or 'legacy'"
            )
        return mode

    def _install_chart_prompt(self, pipeline: Any) -> None:
        """Replace PaddleX's underspecified chart query at its narrow seam.

        PaddleX 3.7 exposes only ``prompt_label='chart'`` and internally turns
        that into the two-word query ``Chart Recognition:``. That task token
        works for the native Paddle model but general gateway VLMs often answer
        it with prose. Keep the token prefix while adding the output contract.
        """

        prompt = self._chart_recognition_prompt()
        if (
            prompt == _LEGACY_CHART_PROMPT
            and self._chart_output_contract_mode() == "off"
        ):
            return
        target = getattr(pipeline, "paddlex_pipeline", pipeline)
        method_name = "_paddleocr_vl_collect_page_vlm_entries_core"
        installed = getattr(target, method_name, None)
        if not callable(installed):
            logger.warning(
                "paddle_ocr chart prompt override unavailable; output contract "
                "validation remains active"
            )
            return
        if getattr(installed, "__filex_structured_chart_prompt__", None) == prompt:
            return
        original = getattr(installed, "__filex_chart_prompt_original__", installed)

        def collect_with_structured_chart_prompt(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            if not isinstance(result, tuple) or not result:
                return result
            entries = result[0]
            if not isinstance(entries, list):
                return result
            updated: list[Any] = []
            active_prompt = str(
                getattr(_CHART_PROMPT_CONTEXT, "prompt", prompt) or prompt
            )
            for entry in entries:
                if (
                    isinstance(entry, tuple)
                    and len(entry) >= 4
                    and str(entry[3]).strip() == _LEGACY_CHART_PROMPT
                ):
                    values = list(entry)
                    values[3] = active_prompt
                    entry = tuple(values)
                updated.append(entry)
            return (updated, *result[1:])

        collect_with_structured_chart_prompt.__filex_structured_chart_prompt__ = (  # type: ignore[attr-defined]
            prompt
        )
        collect_with_structured_chart_prompt.__filex_chart_prompt_original__ = (  # type: ignore[attr-defined]
            original
        )
        try:
            setattr(target, method_name, collect_with_structured_chart_prompt)
        except (AttributeError, TypeError):
            logger.warning(
                "paddle_ocr chart prompt override could not be installed; output "
                "contract validation remains active",
                exc_info=True,
            )

    @staticmethod
    def _model_call_count(raw_results: list[Any]) -> int:
        count = 0
        for result in raw_results:
            try:
                blocks = result.get("parsing_res_list")
            except (AttributeError, TypeError):
                blocks = None
            if isinstance(blocks, list):
                count += len(blocks)
        return count

    @staticmethod
    def _provider_version() -> str:
        try:
            return importlib.metadata.version("paddleocr")
        except importlib.metadata.PackageNotFoundError:
            return ""

    def _pipeline_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "vl_rec_backend": self._str_option("vl_rec_backend", "vllm-server"),
            # Match the service path: recognize chart data instead of emitting
            # only a cropped image under PaddleOCR-VL's disabled default.
            "use_chart_recognition": True,
        }
        for key in (
            "pipeline_version",
            "layout_detection_model_name",
            "layout_detection_model_dir",
            "layout_threshold",
            "layout_nms",
            "layout_unclip_ratio",
            "layout_merge_bboxes_mode",
            "vl_rec_model_name",
            "vl_rec_model_dir",
            "vl_rec_backend",
            "vl_rec_server_url",
            "vl_rec_max_concurrency",
            "vl_rec_api_model_name",
            "vl_rec_api_key",
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_layout_detection",
            "use_chart_recognition",
            "use_seal_recognition",
            "use_ocr_for_image_block",
            "format_block_content",
            "merge_layout_blocks",
            "markdown_ignore_labels",
            "use_queues",
        ):
            value = self._option(key)
            if value not in (None, ""):
                kwargs[key] = value
        gateway_vllm = self._gateway_vllm_config()
        kwargs.setdefault(
            "vl_rec_max_concurrency", self._option("vlm_max_concurrency") or 1
        )
        kwargs.setdefault("vl_rec_server_url", gateway_vllm.get("base_url"))
        kwargs.setdefault(
            "vl_rec_api_model_name",
            gateway_vllm.get("http_model_name") or gateway_vllm.get("model_name"),
        )
        kwargs.setdefault(
            "vl_rec_api_key",
            gateway_vllm.get("api_key") or self._resolve_gateway_vllm_api_key(),
        )
        return {key: value for key, value in kwargs.items() if value not in (None, "")}

    def _predict_with_retries(
        self,
        pipeline: Any,
        *,
        file_path: Path,
        predict_kwargs: dict[str, Any],
        started_at: float,
    ) -> tuple[list[Any], int, float]:
        max_retries = max(0, int(self._option("vlm_max_retries") or 3))
        configured_chart_retries = self._option("chart_contract_retries")
        chart_contract_retries = max(
            0,
            int(1 if configured_chart_retries is None else configured_chart_retries),
        )
        retry_count = 0
        transport_retry_count = 0
        chart_retry_count = 0
        chart_failures: list[str] = []
        while True:
            try:
                raw_results = []
                first_batch_elapsed_ms = 0.0
                retry_prompt = (
                    self._chart_correction_prompt(chart_retry_count, chart_failures)
                    if chart_retry_count
                    else None
                )
                previous_prompt = getattr(_CHART_PROMPT_CONTEXT, "prompt", None)
                if retry_prompt:
                    _CHART_PROMPT_CONTEXT.prompt = retry_prompt
                try:
                    for raw_result in pipeline.predict(
                        str(file_path), **predict_kwargs
                    ):
                        raw_results.append(raw_result)
                        if not first_batch_elapsed_ms:
                            first_batch_elapsed_ms = round(
                                (time.monotonic() - started_at) * 1000, 2
                            )
                finally:
                    if previous_prompt is None:
                        try:
                            del _CHART_PROMPT_CONTEXT.prompt
                        except AttributeError:
                            pass
                    else:
                        _CHART_PROMPT_CONTEXT.prompt = previous_prompt
                chart_failures = self._chart_contract_failures(raw_results)
                if chart_failures:
                    if chart_retry_count >= chart_contract_retries:
                        raise PaddleOcrChartContractError(
                            "PaddleOCR chart output contract failed after "
                            f"{chart_retry_count + 1} attempt(s): "
                            + "; ".join(chart_failures[:5])
                        )
                    chart_retry_count += 1
                    retry_count += 1
                    logger.warning(
                        "paddle_ocr retrying non-tabular chart output | retry=%s "
                        "max_retries=%s failures=%s",
                        chart_retry_count,
                        chart_contract_retries,
                        "; ".join(chart_failures[:5]),
                    )
                    continue
                return raw_results, retry_count, first_batch_elapsed_ms
            except PaddleOcrChartContractError:
                raise
            except Exception as exc:
                if transport_retry_count >= max_retries or not self._is_retryable_error(
                    exc
                ):
                    raise
                transport_retry_count += 1
                retry_count += 1
                delay_ms = self._retry_delay_ms(transport_retry_count)
                logger.warning(
                    "paddle_ocr provider retrying transient VLM failure | retry=%s max_retries=%s "
                    "delay_ms=%s error=%s",
                    transport_retry_count,
                    max_retries,
                    delay_ms,
                    exc,
                )
                time.sleep(delay_ms / 1000)

    @staticmethod
    def _chart_correction_prompt(attempt: int, failures: list[str]) -> str:
        failure_summary = (
            f"{len(failures)} detected chart block(s) lacked a valid multi-column "
            "table with an independent numeric cell"
            if failures
            else "invalid chart table output"
        )
        prompt = _CHART_CORRECTION_PROMPT.format(
            attempt=attempt,
            failures=failure_summary,
        )
        return prompt[:4096]

    def _chart_contract_failures(self, raw_results: list[Any]) -> list[str]:
        mode = self._chart_output_contract_mode()
        if mode == "off":
            return []

        failures: list[str] = []
        for fallback_page, result in enumerate(raw_results, start=1):
            payload = self._json_payload(result)
            page_index = payload.get("page_index")
            try:
                page_number = int(page_index) + 1
            except (TypeError, ValueError):
                page_number = fallback_page
            blocks = payload.get("parsing_res_list")
            if not isinstance(blocks, list):
                continue
            for fallback_block, block in enumerate(blocks, start=1):
                if not isinstance(block, dict):
                    continue
                label = (
                    str(block.get("block_label") or block.get("label") or "")
                    .strip()
                    .lower()
                )
                if label != "chart":
                    continue
                content = str(
                    block.get("block_content") or block.get("content") or ""
                ).strip()
                if not self._has_scorer_compatible_chart_table(content):
                    block_id = str(
                        block.get("global_block_id")
                        or block.get("block_id")
                        or fallback_block
                    )
                    failures.append(
                        f"page={page_number} block={block_id} lacks a multi-column "
                        "table with an independent numeric cell"
                    )
        return failures

    def _chart_output_contract_mode(self) -> str:
        configured = self._option("chart_output_contract")
        if configured in (None, ""):
            has_custom_prompt = self._option("chart_prompt") not in (None, "")
            return (
                "strict"
                if has_custom_prompt or self._chart_prompt_mode() == "structured"
                else "off"
            )
        mode = str(configured).strip().lower()
        if mode not in {"strict", "off"}:
            raise ValueError(
                "paddle_ocr chart_output_contract must be 'strict' or 'off'"
            )
        return mode

    @classmethod
    def _has_scorer_compatible_chart_table(cls, content: str) -> bool:
        return cls._has_markdown_chart_table(content) or cls._has_html_chart_table(
            content
        )

    @classmethod
    def _has_markdown_chart_table(cls, content: str) -> bool:
        lines = [line.strip() for line in content.splitlines()]
        for index in range(len(lines) - 2):
            header = cls._markdown_cells(lines[index])
            separator = cls._markdown_cells(lines[index + 1])
            if len(header) < 2 or len(separator) != len(header):
                continue
            if not all(_MARKDOWN_SEPARATOR_CELL.fullmatch(cell) for cell in separator):
                continue
            for data_line in lines[index + 2 :]:
                cells = cls._markdown_cells(data_line)
                if len(cells) != len(header):
                    break
                if any(cls._is_numeric_chart_cell(cell) for cell in cells):
                    return True
        return False

    @staticmethod
    def _markdown_cells(line: str) -> list[str]:
        if "|" not in line:
            return []
        cells = re.split(r"(?<!\\)\|", line)
        if cells and not cells[0].strip():
            cells = cells[1:]
        if cells and not cells[-1].strip():
            cells = cells[:-1]
        return [cell.replace(r"\|", "|").strip() for cell in cells]

    @classmethod
    def _has_html_chart_table(cls, content: str) -> bool:
        for table_match in _HTML_TABLE.finditer(content):
            rows: list[list[str]] = []
            for row_match in _HTML_ROW.finditer(table_match.group(1)):
                cells = [
                    unescape(_HTML_TAG.sub("", cell).strip())
                    for cell in _HTML_CELL.findall(row_match.group(1))
                ]
                if cells:
                    rows.append(cells)
            if not rows or max(len(row) for row in rows) < 2:
                continue
            for row in rows[1:] if len(rows) > 1 else rows:
                if len(row) >= 2 and any(
                    cls._is_numeric_chart_cell(cell) for cell in row
                ):
                    return True
        return False

    @staticmethod
    def _is_numeric_chart_cell(value: str) -> bool:
        normalized = re.sub(r"\s+", " ", value).strip()
        return bool(_NUMERIC_CHART_CELL.fullmatch(normalized))

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            signal in message
            for signal in (
                "429",
                "rate limit",
                "rpm_limit",
                "额度超限",
                "限流",
                "timeout",
                "timed out",
                "502",
                "503",
                "504",
            )
        )

    def _retry_delay_ms(self, retry_count: int) -> int:
        base_ms = max(0, int(self._option("vlm_retry_base_delay_ms") or 500))
        max_ms = max(base_ms, int(self._option("vlm_retry_max_delay_ms") or 8000))
        return min(max_ms, base_ms * (2 ** max(0, retry_count - 1)))

    def _predict_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"use_chart_recognition": True}
        for key in (
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_layout_detection",
            "use_chart_recognition",
            "use_seal_recognition",
            "use_ocr_for_image_block",
            "layout_threshold",
            "layout_nms",
            "layout_unclip_ratio",
            "layout_merge_bboxes_mode",
            "layout_shape_mode",
            "prompt_label",
            "format_block_content",
            "repetition_penalty",
            "temperature",
            "top_p",
            "min_pixels",
            "max_pixels",
            "max_new_tokens",
            "merge_layout_blocks",
            "markdown_ignore_labels",
        ):
            value = self._option(key)
            if value not in (None, ""):
                kwargs[key] = value
        vlm_extra_args = self._option("vlm_extra_args")
        if isinstance(vlm_extra_args, dict):
            kwargs["vlm_extra_args"] = vlm_extra_args
        return kwargs

    @staticmethod
    def _extract_markdown_data(result: Any) -> dict[str, Any]:
        markdown = getattr(result, "markdown", None)
        if isinstance(markdown, dict):
            return markdown
        if callable(markdown):
            markdown = markdown()
            if isinstance(markdown, dict):
                return markdown
        to_markdown = getattr(result, "_to_markdown", None)
        if callable(to_markdown):
            markdown = to_markdown()
            if isinstance(markdown, dict):
                return markdown
        if isinstance(result, dict):
            return result
        return {"markdown_texts": str(result or "")}

    @staticmethod
    def _concatenate_markdown(
        pipeline: Any, markdown_parts: list[dict[str, Any]]
    ) -> str:
        if not markdown_parts:
            return ""
        concatenate = getattr(pipeline, "concatenate_markdown_pages", None)
        if callable(concatenate):
            try:
                merged = concatenate(markdown_parts)
                if isinstance(merged, str):
                    return merged.strip()
                if isinstance(merged, dict):
                    return str(merged.get("markdown_texts") or "").strip()
            except Exception:
                logger.debug(
                    "paddle_ocr_vl concatenate_markdown_pages failed", exc_info=True
                )
        return "\n\n".join(
            str(part.get("markdown_texts") or "").strip()
            for part in markdown_parts
            if part.get("markdown_texts")
        ).strip()

    def _write_markdown_images(
        self,
        markdown_parts: list[dict[str, Any]],
        *,
        task_id: str,
        source_file_name: str,
    ) -> list[DocumentAsset]:
        output_dir = DOCUMENT_PARSE_WORKSPACE / task_id / "paddleocr_vl_images"
        assets: list[DocumentAsset] = []
        image_index = 0
        for page_index, markdown_data in enumerate(markdown_parts, start=1):
            images = self._extract_images_dict(markdown_data)
            for markdown_path, image_data in images.items():
                image_index += 1
                image_path = self._resolve_image_path(
                    output_dir=output_dir,
                    source_file_name=source_file_name,
                    page_index=page_index,
                    image_index=image_index,
                    markdown_path=str(markdown_path),
                )
                self._save_image(image_path, image_data)
                assets.append(
                    DocumentAsset(
                        asset_id=f"paddle_ocr_vl_image_{image_index}",
                        kind="embedded_image",
                        local_path=image_path,
                        page_number=page_index,
                        order=image_index,
                        anchor=DocumentAnchor(page_number=page_index),
                        meta={
                            "index": str(image_index),
                            "name": image_path.name,
                            "local_path": str(image_path),
                            "markdown_path": self._markdown_path(image_path),
                            "original_markdown_path": str(markdown_path),
                            "placement": "already_in_markdown",
                        },
                    )
                )
        return assets

    @staticmethod
    def _extract_images_dict(markdown_data: dict[str, Any]) -> dict[str, Any]:
        for key in ("markdown_images", "images", "imgs", "image"):
            value = markdown_data.get(key)
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _resolve_image_path(
        *,
        output_dir: Path,
        source_file_name: str,
        page_index: int,
        image_index: int,
        markdown_path: str,
    ) -> Path:
        suffix = Path(markdown_path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            suffix = ".png"
        return output_dir / f"{source_file_name}-p{page_index}-{image_index}{suffix}"

    @staticmethod
    def _save_image(image_path: Path, image_data: Any) -> None:
        image_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(image_data, bytes):
            image_path.write_bytes(image_data)
            return
        if hasattr(image_data, "save"):
            image_data.save(image_path)
            return
        try:
            import numpy as np
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "Saving PaddleOCR-VL markdown images requires pillow and numpy"
            ) from exc
        if isinstance(image_data, np.ndarray):
            Image.fromarray(image_data).save(image_path)
            return
        raise RuntimeError(
            f"Unsupported PaddleOCR-VL markdown image type: {type(image_data)!r}"
        )

    @staticmethod
    def _resolve_page_count(raw_results: list[Any]) -> int:
        page_indexes: set[int] = set()
        for index, result in enumerate(raw_results, start=1):
            getter = getattr(result, "get", None)
            page_index = getter("page_index") if callable(getter) else None
            if page_index is None and isinstance(result, dict):
                page_index = result.get("page_index")
            try:
                page_indexes.add(int(page_index) + 1)
            except (TypeError, ValueError):
                page_indexes.add(index)
        return max(page_indexes) if page_indexes else len(raw_results)

    @staticmethod
    def replace_markdown_asset_references(
        markdown_text: str, assets: list[DocumentAsset]
    ) -> str:
        updated = markdown_text
        for asset in assets:
            original = str(asset.meta.get("original_markdown_path") or "").strip()
            target = str(
                asset.meta.get("remote_url")
                or asset.remote_id
                or asset.meta.get("markdown_path")
                or asset.meta.get("local_path")
                or ""
            ).strip()
            if not original or not target or original == target:
                continue
            updated = PaddleOcrPdfProvider._replace_markdown_image_reference(
                updated,
                original=original,
                target=target,
                file_id=asset.remote_id,
            )
            updated = PaddleOcrPdfProvider._replace_html_image_reference(
                updated,
                original=original,
                target=target,
                file_id=asset.remote_id,
            )
        return updated

    @staticmethod
    def _replace_markdown_image_reference(
        markdown_text: str,
        *,
        original: str,
        target: str,
        file_id: str,
    ) -> str:
        pattern = re.compile(r"!\[([^\]]*)\]\(" + re.escape(original) + r"\)")

        def replace(match: re.Match[str]) -> str:
            alt = match.group(1)
            if not file_id:
                return f"![{alt}]({target})"
            return (
                f'<img src="{escape(target, quote=True)}" '
                f'data-file-id="{escape(file_id, quote=True)}" '
                f'alt="{escape(alt, quote=True)}" />'
            )

        return pattern.sub(replace, markdown_text)

    @staticmethod
    def _replace_html_image_reference(
        markdown_text: str,
        *,
        original: str,
        target: str,
        file_id: str,
    ) -> str:
        updated = markdown_text.replace(f'src="{original}"', f'src="{target}"')
        updated = updated.replace(f"src='{original}'", f"src='{target}'")
        if not file_id:
            return updated
        escaped_file_id = escape(file_id, quote=True)
        updated = re.sub(
            r"(<img\b(?![^>]*\bdata-file-id=)[^>]*\bsrc=\""
            + re.escape(target)
            + r"\"[^>]*)(/?>)",
            rf'\1 data-file-id="{escaped_file_id}"\2',
            updated,
        )
        updated = re.sub(
            r"(<img\b(?![^>]*\bdata-file-id=)[^>]*\bsrc='"
            + re.escape(target)
            + r"'[^>]*)(/?>)",
            rf'\1 data-file-id="{escaped_file_id}"\2',
            updated,
        )
        return updated

    @staticmethod
    def _markdown_path(image_path: Path) -> str:
        try:
            return str(image_path.relative_to(DOCUMENT_PARSE_WORKSPACE))
        except ValueError:
            return str(image_path)

    def _model_info(self) -> dict[str, Any]:
        keys = (
            "pipeline_version",
            "vl_rec_backend",
            "vl_rec_server_url",
            "vl_rec_api_model_name",
            "use_layout_detection",
            "use_ocr_for_image_block",
            "merge_layout_blocks",
        )
        info = {
            key: self._option(key)
            for key in keys
            if self._option(key) not in (None, "")
        }
        gateway_vllm = self._gateway_vllm_config()
        if "vl_rec_api_model_name" not in info:
            model_name = gateway_vllm.get("http_model_name") or gateway_vllm.get(
                "model_name"
            )
            if model_name not in (None, ""):
                info["vl_rec_api_model_name"] = str(model_name)
        info["chart_prompt_mode"] = (
            "custom"
            if self._option("chart_prompt") not in (None, "")
            else self._chart_prompt_mode()
        )
        info["chart_output_contract"] = self._chart_output_contract_mode()
        return info

    def _option(self, key: str) -> Any:
        for candidate in (
            f"pdf_paddle_ocr_{key}",
            f"paddle_ocr_{key}",
            f"pdf_{key}",
            key,
        ):
            if candidate in self._env_content and self._env_content[candidate] not in (
                None,
                "",
            ):
                return self._env_content[candidate]
        environment_value = os.getenv(f"FILEX_PADDLE_OCR_{key.upper()}")
        if environment_value not in (None, ""):
            try:
                return json.loads(environment_value)
            except json.JSONDecodeError:
                return environment_value
        return None

    def _str_option(self, key: str, default: str) -> str:
        value = self._option(key)
        if value in (None, ""):
            return default
        return str(value)

    def _bool_option(self, key: str, default: bool) -> bool:
        value = self._option(key)
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _gateway_vllm_config(self) -> dict[str, Any]:
        config = self._env_content.get("gateway_vllm")
        resolved = dict(config) if isinstance(config, dict) else {}
        for key, environment_name in (
            ("base_url", "GATEWAY_VLLM_BASE_URL"),
            ("model_name", "GATEWAY_VLLM_MODEL_NAME"),
            ("http_model_name", "GATEWAY_VLLM_HTTP_MODEL_NAME"),
        ):
            environment_value = os.getenv(environment_name)
            if key not in resolved and environment_value:
                resolved[key] = environment_value
        return resolved

    @staticmethod
    def _resolve_gateway_vllm_api_key() -> str:
        for env_name in (
            "GATEWAY_VLLM_API_KEY",
            "OPENAI_COMPATIBLE_API_KEY",
            "OPENAI_API_KEY",
        ):
            value = os.getenv(env_name)
            if value:
                return value
        return ""


# Backward-compatible aliases for tests/imports that used the previous provider shape.
PaddleOcrLine = None
PaddleOcrPage = None
