"""Versioned, file-type-aware metrics for FileX document parsing."""

from __future__ import annotations

from typing import Any

METRICS_SCHEMA_VERSION = "1.0"


def build_parse_metrics(
    *,
    file_type: str,
    input_bytes: int,
    output_char_count: int,
    asset_count: int,
    stage_durations_ms: dict[str, int],
    total_duration_ms: int,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable common schema and the file-type-specific metrics block."""
    details = _flatten_diagnostics(diagnostics or {})
    normalized_type = str(file_type or "unknown").lower()
    provider = str(details.get("provider") or details.get("parser") or "unknown")
    model_info = (
        details.get("model_info") if isinstance(details.get("model_info"), dict) else {}
    )
    parse_duration_ms = int(
        details.get("parse_elapsed_ms")
        or details.get("total_elapsed_ms")
        or details.get("vlm_total_elapsed_ms")
        or stage_durations_ms.get("content_extract", 0)
    )
    page_count = _first_int(
        details, "page_count", "pypdf_page_count", "raw_result_count"
    )
    failed_count = int(
        details.get("error_count") or len(details.get("vlm_errors") or [])
    )
    succeeded_count = int(
        details.get("page_count_succeeded")
        or details.get("page_count_processed")
        or _work_count(normalized_type, details, page_count)
    )
    processed_count = max(succeeded_count + failed_count, failed_count)

    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "file_type": normalized_type,
        "provider": provider,
        "provider_version": _optional_string(details.get("provider_version")),
        "status": "partial_success" if failed_count and page_count else "success",
        "timings_ms": {
            "queue": 0,
            "initialization": int(
                details.get("initialization_elapsed_ms")
                or stage_durations_ms.get("init", 0)
            ),
            "download": 0,
            "upload": 0,
            "model_wait": int(
                details.get("model_wait_ms") or details.get("model_retry_wait_ms") or 0
            ),
            "first_batch": _optional_int(details.get("first_batch_duration_ms")),
            "parse": parse_duration_ms,
            "total": int(total_duration_ms),
        },
        "work": {
            "unit": _work_unit(normalized_type),
            "requested": _optional_int(details.get("page_count_requested")),
            "processed": processed_count,
            "succeeded": succeeded_count,
            "failed": failed_count,
            "batch_count": int(
                details.get("batch_count") or (1 if processed_count else 0)
            ),
            "resumed_batch_count": int(details.get("resumed_batch_count") or 0),
            "first_batch_count": _optional_int(details.get("first_batch_page_count")),
        },
        "io": {
            "input_bytes": int(input_bytes),
            "output_char_count": int(output_char_count),
            "asset_count": int(asset_count),
        },
        "model": {
            "name": _optional_string(
                details.get("model")
                or model_info.get("model")
                or model_info.get("model_name")
                or model_info.get("vl_rec_api_model_name")
            ),
            "call_count": int(
                details.get("model_call_count") or details.get("vlm_page_count") or 0
            ),
            "retry_count": int(
                details.get("model_retry_count") or details.get("vlm_retry_count") or 0
            ),
            "peak_concurrency": int(
                details.get("peak_concurrency")
                or details.get("vlm_max_concurrency")
                or 0
            ),
            "timeout_count": int(details.get("timeout_count") or 0),
        },
        "error": {
            "type": _optional_string(details.get("error_type")),
            "count": failed_count,
        },
        "type_metrics": {
            normalized_type: _build_type_metrics(
                normalized_type, details, page_count, failed_count
            )
        },
    }


def _build_type_metrics(
    file_type: str,
    details: dict[str, Any],
    page_count: int,
    failed_count: int,
) -> dict[str, Any]:
    if file_type == "pdf":
        elapsed_ms = float(
            details.get("total_elapsed_ms") or details.get("vlm_total_elapsed_ms") or 0
        )
        return {
            "document_page_count": page_count,
            "source_page_count": int(details.get("source_page_count") or page_count),
            "requested_pages": details.get("requested_pages") or [],
            "page_number_base": int(details.get("page_number_base") or 1),
            "text_layer_page_count": int(details.get("pypdf_page_count") or 0),
            "ocr_page_count": int(details.get("ocr_page_count") or 0),
            "vlm_page_count": int(details.get("vlm_page_count") or 0),
            "rendered_page_count": int(details.get("rendered_page_count") or 0),
            "failed_page_count": failed_count,
            "text_layer_char_count": int(details.get("pypdf_char_count") or 0),
            "embedded_image_count": int(details.get("asset_count") or 0),
            "average_page_duration_ms": round(elapsed_ms / page_count, 2)
            if page_count
            else None,
        }
    if file_type in {"ppt", "pptx"}:
        return {
            "slide_count": _first_int(details, "slide_count", "page_count"),
            "text_box_count": int(details.get("text_box_count") or 0),
            "table_count": int(details.get("table_count") or 0),
            "embedded_image_count": int(details.get("asset_count") or 0),
            "speaker_note_count": int(details.get("speaker_note_count") or 0),
            "empty_slide_count": int(details.get("empty_slide_count") or 0),
        }
    if file_type in {"xls", "xlsx", "csv"}:
        metrics = {
            "sheet_count": int(details.get("sheet_count") or 0),
            "row_count": int(details.get("row_count") or 0),
            "column_count": int(details.get("column_count") or 0),
            "cell_count": int(details.get("cell_count") or 0),
            "non_empty_cell_count": int(details.get("non_empty_cell_count") or 0),
            "formula_count": int(details.get("formula_count") or 0),
            "merged_cell_count": int(details.get("merged_cell_count") or 0),
            "chart_count": int(details.get("chart_count") or 0),
            "hidden_sheet_count": int(details.get("hidden_sheet_count") or 0),
        }
        if file_type == "csv":
            metrics.update(
                {
                    "encoding": _optional_string(details.get("encoding")),
                    "encoding_errors": _optional_string(details.get("encoding_errors")),
                    "delimiter": _optional_string(details.get("delimiter")),
                    "delimiter_detection": _optional_string(
                        details.get("delimiter_detection")
                    ),
                    "parser_engine": _optional_string(details.get("parser_engine")),
                    "fallback_reason": _optional_string(details.get("fallback_reason")),
                }
            )
        return metrics
    if file_type in {"doc", "docx"}:
        return {
            "logical_page_count": int(details.get("page_count") or 0),
            "paragraph_count": int(details.get("paragraph_count") or 0),
            "table_count": int(details.get("table_count") or 0),
            "heading_count": int(details.get("heading_count") or 0),
            "embedded_image_count": int(details.get("asset_count") or 0),
            "header_count": int(details.get("header_count") or 0),
            "footer_count": int(details.get("footer_count") or 0),
            "comment_count": int(details.get("comment_count") or 0),
        }
    if file_type in {"png", "jpg", "jpeg", "webp", "gif", "bmp"}:
        return {
            "image_count": 1,
            "width": int(details.get("width") or 0),
            "height": int(details.get("height") or 0),
            "ocr_region_count": int(details.get("ocr_region_count") or 0),
            "ocr_char_count": int(details.get("ocr_char_count") or 0),
            "object_count": int(details.get("object_count") or 0),
            "batch_count": int(details.get("batch_count") or 0),
            "scene_type": _optional_string(details.get("image_scene_type")),
            "scene_confidence": _optional_float(details.get("image_scene_confidence")),
            "selected_pipeline": _optional_string(
                details.get("image_selected_pipeline")
            ),
            "routing_fallback_reason": _optional_string(
                details.get("image_routing_fallback_reason")
            ),
            "extraction_profile": _optional_string(
                details.get("image_extraction_profile")
            ),
            "query_ready_count": int(details.get("image_query_ready_count") or 0),
            "review_required_count": int(
                details.get("image_review_required_count") or 0
            ),
            "evidence_schema_version": _optional_string(
                details.get("image_evidence_schema_version")
            ),
            "evidence_file_path": _optional_string(
                details.get("image_evidence_file_path")
            ),
            "orientation_corrected": bool(
                details.get("orientation_corrected") or False
            ),
        }
    if file_type in {"mp3", "wav", "flac", "ogg", "m4a", "aac", "opus"}:
        return {
            "duration_ms": int(float(details.get("duration") or 0) * 1000),
            "segment_count": int(details.get("segment_count") or 0),
            "speaker_count": int(details.get("speaker_count") or 0),
            "transcript_char_count": int(details.get("transcript_char_count") or 0),
        }
    if file_type in {"mp4", "mpeg", "mpg", "avi", "mkv", "mov", "webm", "m4v"}:
        return {
            "duration_ms": int(float(details.get("duration") or 0) * 1000),
            "sampled_frame_count": int(details.get("sampled_frame_count") or 0),
            "scene_count": int(details.get("scene_count") or 0),
            "segment_count": int(details.get("segment_count") or 0),
            "transcript_char_count": int(details.get("transcript_char_count") or 0),
            "width": int(details.get("video_width") or 0),
            "height": int(details.get("video_height") or 0),
            "video_codec": _optional_string(details.get("video_codec")),
            "audio_codec": _optional_string(details.get("audio_codec")),
            "evidence_file_path": _optional_string(
                details.get("video_evidence_file_path")
            ),
            "storyboard_file_path": _optional_string(
                details.get("video_storyboard_file_path")
            ),
        }
    return {}


def _work_unit(file_type: str) -> str:
    if file_type == "pdf":
        return "page"
    if file_type in {"ppt", "pptx"}:
        return "slide"
    if file_type in {"xls", "xlsx", "csv"}:
        return "sheet"
    if file_type in {"doc", "docx"}:
        return "logical_page"
    if file_type in {"png", "jpg", "jpeg", "webp", "gif", "bmp"}:
        return "image"
    return "document"


def _work_count(file_type: str, details: dict[str, Any], page_count: int) -> int:
    if file_type == "pdf":
        return page_count
    if file_type in {"ppt", "pptx"}:
        return int(details.get("slide_count") or 0)
    if file_type in {"xls", "xlsx", "csv"}:
        return int(details.get("sheet_count") or 0)
    if file_type in {"doc", "docx"}:
        return int(details.get("logical_page_count") or 1)
    if file_type in {"png", "jpg", "jpeg", "webp", "gif", "bmp"}:
        return 1
    return 1


def _flatten_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Normalize producer metadata at the metrics boundary."""
    normalized: dict[str, Any] = {}
    raw_result = diagnostics.get("raw_result")
    if isinstance(raw_result, dict):
        normalized.update(raw_result)
    metadata = diagnostics.get("metadata")
    if isinstance(metadata, dict):
        normalized.update(metadata)
    normalized.update(diagnostics)
    return normalized


def _first_int(values: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = values.get(key)
        if value not in (None, ""):
            return int(value)
    return 0


def _optional_int(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_float(value: Any) -> float | None:
    return None if value in (None, "") else float(value)
