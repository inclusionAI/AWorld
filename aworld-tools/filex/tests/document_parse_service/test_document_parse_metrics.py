from document_parse_service.document_parse_metrics import build_parse_metrics
from document_parse_service.document_parse_executor import DocumentParseExecutor


def test_pdf_metrics_keep_common_and_pdf_specific_fields_separate() -> None:
    metrics = build_parse_metrics(
        file_type="pdf",
        input_bytes=4096,
        output_char_count=1200,
        asset_count=3,
        stage_durations_ms={"init": 20, "content_extract": 7000},
        total_duration_ms=7100,
        diagnostics={
            "provider": "paddle_ocr",
            "page_count": 7,
            "total_elapsed_ms": 7000,
            "model_call_count": 87,
            "peak_concurrency": 8,
            "model_info": {"model_name": "paddle-ocr-vl"},
        },
    )

    assert metrics["schema_version"] == "1.0"
    assert metrics["provider"] == "paddle_ocr"
    assert metrics["timings_ms"]["parse"] == 7000
    assert metrics["work"] == {
        "unit": "page",
        "requested": None,
        "processed": 7,
        "succeeded": 7,
        "failed": 0,
        "batch_count": 1,
        "resumed_batch_count": 0,
        "first_batch_count": None,
    }
    assert metrics["model"]["call_count"] == 87
    assert metrics["type_metrics"]["pdf"] == {
        "document_page_count": 7,
        "source_page_count": 7,
        "requested_pages": [],
        "page_number_base": 1,
        "text_layer_page_count": 0,
        "ocr_page_count": 0,
        "vlm_page_count": 0,
        "rendered_page_count": 0,
        "failed_page_count": 0,
        "text_layer_char_count": 0,
        "embedded_image_count": 0,
        "average_page_duration_ms": 1000.0,
    }


def test_file_types_use_distinct_work_units_and_type_metrics() -> None:
    pptx = build_parse_metrics(
        file_type="pptx",
        input_bytes=1,
        output_char_count=2,
        asset_count=0,
        stage_durations_ms={},
        total_duration_ms=3,
        diagnostics={"provider": "python_pptx", "slide_count": 12},
    )
    xlsx = build_parse_metrics(
        file_type="xlsx",
        input_bytes=1,
        output_char_count=2,
        asset_count=0,
        stage_durations_ms={},
        total_duration_ms=3,
        diagnostics={"provider": "openpyxl", "sheet_count": 4, "row_count": 50},
    )

    assert pptx["work"]["unit"] == "slide"
    assert pptx["type_metrics"]["pptx"] == {
        "slide_count": 12,
        "text_box_count": 0,
        "table_count": 0,
        "embedded_image_count": 0,
        "speaker_note_count": 0,
        "empty_slide_count": 0,
    }
    assert xlsx["work"]["unit"] == "sheet"
    assert xlsx["type_metrics"]["xlsx"]["sheet_count"] == 4


def test_raw_result_populates_tabular_metrics_and_work_count() -> None:
    metrics = build_parse_metrics(
        file_type="xlsx",
        input_bytes=10,
        output_char_count=20,
        asset_count=0,
        stage_durations_ms={"content_extract": 30},
        total_duration_ms=40,
        diagnostics={
            "raw_result": {
                "provider": "openpyxl",
                "sheet_count": 3,
                "row_count": 100,
                "formula_count": 12,
            }
        },
    )

    assert metrics["provider"] == "openpyxl"
    assert metrics["work"]["unit"] == "sheet"
    assert metrics["work"]["processed"] == 3
    assert metrics["type_metrics"]["xlsx"]["formula_count"] == 12


def test_csv_metrics_include_dialect_and_parser_diagnostics() -> None:
    metrics = build_parse_metrics(
        file_type="csv",
        input_bytes=10,
        output_char_count=20,
        asset_count=0,
        stage_durations_ms={"content_extract": 30},
        total_duration_ms=40,
        diagnostics={
            "provider": "pandas",
            "sheet_count": 1,
            "row_count": 2339,
            "column_count": 3,
            "encoding": "ascii",
            "delimiter": ",",
            "delimiter_detection": "csv_sniffer_validated",
            "parser_engine": "c",
            "fallback_reason": "",
        },
    )

    csv_metrics = metrics["type_metrics"]["csv"]
    assert csv_metrics["row_count"] == 2339
    assert csv_metrics["column_count"] == 3
    assert csv_metrics["encoding"] == "ascii"
    assert csv_metrics["delimiter"] == ","
    assert csv_metrics["delimiter_detection"] == "csv_sniffer_validated"
    assert csv_metrics["parser_engine"] == "c"
    assert csv_metrics["fallback_reason"] is None


def test_runtime_download_duration_updates_total_duration() -> None:
    metrics = {
        "timings_ms": {"queue": 0, "download": 0, "upload": 0, "parse": 80, "total": 100}
    }

    DocumentParseExecutor._apply_runtime_metrics(metrics, {"queue": 5, "download": 20})

    assert metrics["timings_ms"] == {
        "queue": 5,
        "download": 20,
        "upload": 0,
        "parse": 80,
        "total": 125,
    }


def test_image_metrics_include_model_retry_and_wait_time() -> None:
    metrics = build_parse_metrics(
        file_type="jpeg",
        input_bytes=1024,
        output_char_count=120,
        asset_count=0,
        stage_durations_ms={"content_extract": 1800},
        total_duration_ms=1900,
        diagnostics={
            "provider": "openai_compatible",
            "model": "image-model",
            "metadata": {
                "model_call_count": 2,
                "model_retry_count": 1,
                "peak_concurrency": 1,
                "model_retry_wait_ms": 500,
                "ocr_char_count": 120,
            },
        },
    )

    assert metrics["timings_ms"]["model_wait"] == 500
    assert metrics["model"] == {
        "name": "image-model",
        "call_count": 2,
        "retry_count": 1,
        "peak_concurrency": 1,
        "timeout_count": 0,
    }
    assert metrics["type_metrics"]["jpeg"]["ocr_char_count"] == 120
