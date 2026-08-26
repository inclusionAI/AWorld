import sys
from pathlib import Path


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_ocr_server_builds_vlm_options_from_gateway_config() -> None:
    _add_src_path()
    from document_parse_service.ocr_server import OcrServer

    server = OcrServer(
        config={
            "gateway_vllm": {
                "model_name": "ai_cloud_Kimi_k26_pgc",
                "http_model_name": "kimi_k26_pc",
                "base_url": "https://antchat.alipay.com/v1",
                "api_key": "test-key",
            }
        }
    )

    options = server._build_vlm_options(  # noqa: SLF001
        data_url="data:image/png;base64,AAAA",
        language="zh",
        filename="page.png",
    )

    assert options["base_url"] == "https://antchat.alipay.com/v1"
    assert options["api_key"] == "test-key"
    assert options["model"] == "kimi_k26_pc"
    assert options["media_url"] == "data:image/png;base64,AAAA"
    assert options["media_content_type"] == "image_url"
    assert options["extra_body"]["enable_maya_new_inference_protocol"] is True


def test_ocr_server_loads_explicit_filex_config(tmp_path: Path) -> None:
    _add_src_path()
    from document_parse_service.ocr_server import OcrServer

    config_path = tmp_path / "filex.yaml"
    config_path.write_text(
        "gateway_vllm:\n"
        "  model_name: configured-model\n"
        "  base_url: https://gateway.example/v1\n"
        "  api_key: configured-key\n",
        encoding="utf-8",
    )

    server = OcrServer(config_path=config_path)
    options = server._build_vlm_options(  # noqa: SLF001
        data_url="data:image/png;base64,AAAA",
        language="zh",
        filename="page.png",
    )

    assert options["model"] == "configured-model"
    assert options["base_url"] == "https://gateway.example/v1"
    assert options["api_key"] == "configured-key"


def test_ocr_server_uses_paddle_vlm_config_when_gateway_key_missing() -> None:
    _add_src_path()
    from document_parse_service.ocr_server import OcrServer

    server = OcrServer(
        config={
            "gateway_vllm": {
                "model_name": "ai_cloud_Kimi_k26_pgc",
            },
            "document_parse": {
                "pdf": {
                    "paddle_ocr_vl_rec_server_url": "https://antchat.alipay.com/v1",
                    "paddle_ocr_vl_rec_api_model_name": "aisearch_paaldocr_vl_16",
                    "paddle_ocr_vl_rec_api_key": "paddle-key",
                }
            },
        }
    )

    options = server._build_vlm_options(  # noqa: SLF001
        data_url="data:image/png;base64,AAAA",
        language="zh",
        filename="page.png",
    )

    assert options["base_url"] == "https://antchat.alipay.com/v1"
    assert options["api_key"] == "paddle-key"
    assert options["model"] == "aisearch_paaldocr_vl_16"


def test_ocr_server_prefers_paddle_vlm_config_over_generic_gateway() -> None:
    _add_src_path()
    from document_parse_service.ocr_server import OcrServer

    server = OcrServer(
        config={
            "gateway_vllm": {
                "base_url": "https://gateway.example/v1",
                "api_key": "gateway-key",
                "model_name": "gateway-model",
            },
            "document_parse": {
                "pdf": {
                    "paddle_ocr_vl_rec_server_url": "https://paddle.example/v1",
                    "paddle_ocr_vl_rec_api_model_name": "paddle-model",
                    "paddle_ocr_vl_rec_api_key": "paddle-key",
                }
            },
        }
    )

    options = server._build_vlm_options(  # noqa: SLF001
        data_url="data:image/png;base64,AAAA",
        language="zh",
        filename="page.png",
    )

    assert options["base_url"] == "https://paddle.example/v1"
    assert options["api_key"] == "paddle-key"
    assert options["model"] == "paddle-model"


def test_ocr_server_converts_text_lines_to_liteparse_results() -> None:
    _add_src_path()
    from document_parse_service.ocr_server import OcrServer

    results = OcrServer._text_to_ocr_results(  # noqa: SLF001
        "第一行\n第二行",
        width=200,
        height=100,
    )

    assert results == [
        {"text": "第一行", "bbox": [0, 0, 200, 50], "confidence": 0.5},
        {"text": "第二行", "bbox": [0, 50, 200, 100], "confidence": 0.5},
    ]


def test_ocr_server_removes_paddle_location_tokens() -> None:
    _add_src_path()
    from document_parse_service.ocr_server import OcrServer

    normalized = OcrServer._normalize_ocr_text(  # noqa: SLF001
        "第一行<|LOC_12|><|LOC_34|>\n\n第二行<|LOC_56|>"
    )

    assert normalized == "第一行\n第二行"
