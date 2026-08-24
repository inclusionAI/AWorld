import pytest

from document_parse_service.service import DocumentParseService
from document_parse_service.provider_registry import (
    default_provider_for_format,
    list_provider_descriptors,
    normalize_provider_env,
)


def test_registry_lists_format_specific_providers() -> None:
    assert [item.provider_key for item in list_provider_descriptors("pdf")] == [
        "paddle_ocr",
        "liteparse",
        "pypdf_vlm",
    ]
    assert [item.provider_key for item in list_provider_descriptors("jpg")] == ["image_vlm"]
    assert default_provider_for_format("pptx") == "python_pptx"


def test_registry_normalizes_alias_and_records_version() -> None:
    env = normalize_provider_env("pdf", {"pdf_parse_provider": "paddleocr"})

    assert env["filex_parse_provider"] == "paddle_ocr"
    assert env["pdf_parse_provider"] == "paddle_ocr"
    assert env["filex_provider_version"] == "paddleocr-vl-1.6"


def test_pre_download_validation_does_not_override_config_default() -> None:
    assert normalize_provider_env("pdf", {}, use_default=False) == {}


def test_registry_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unsupported_provider: paddle_ocrr"):
        normalize_provider_env("pdf", {"filex_parse_provider": "paddle_ocrr"})


def test_registry_rejects_provider_for_wrong_format() -> None:
    with pytest.raises(ValueError, match="unsupported_provider_for_format"):
        normalize_provider_env("jpg", {"filex_parse_provider": "paddle_ocr"})


def test_legacy_doc_is_not_declared_as_docx() -> None:
    with pytest.raises(ValueError, match="Unsupported file type: doc"):
        normalize_provider_env("doc", {})


def test_parse_result_metrics_use_canonical_provider_identity() -> None:
    result = {"metrics": {"provider": "paddleocr", "provider_version": ""}}

    DocumentParseService._apply_provider_identity(
        result,
        {
            "filex_parse_provider": "paddle_ocr",
            "filex_provider_version": "paddleocr-vl-1.6",
        },
    )

    assert result["metrics"]["provider"] == "paddle_ocr"
    assert result["metrics"]["provider_version"] == "paddleocr-vl-1.6"
