import sys
from pathlib import Path


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_build_default_env_content_maps_yaml_to_env_content() -> None:
    _add_src_path()
    from document_parse_service.filex_config import build_default_env_content

    env = build_default_env_content(
        file_type="png",
        media_type="image",
        config={
            "document_parse": {
                "liteparse": {
                    "ocr_enabled": True,
                    "ocr_server_url": "http://127.0.0.1:8081",
                }
            },
            "media_parse": {
                "image": {
                    "backend": "asap_gateway",
                    "asap_gateway": {
                        "base_url": "http://127.0.0.1:8081",
                    },
                }
            },
        },
    )

    assert env["liteparse_ocr_enabled"] is True
    assert env["liteparse_ocr_server_url"] == "http://127.0.0.1:8081"
    assert env["media_parse_backend"] == "asap_gateway"
    assert env["media_parse_options"]["base_url"] == "http://127.0.0.1:8081"


def test_build_default_env_content_maps_pdf_provider_options() -> None:
    _add_src_path()
    from document_parse_service.filex_config import build_default_env_content

    env = build_default_env_content(
        file_type="pdf",
        config={
            "document_parse": {
                "pdf": {
                    "parse_provider": "pypdf_vlm",
                    "force_vlm": True,
                    "vlm_base_url": "http://127.0.0.1:8081/v1",
                    "vlm_max_pages": 3,
                }
            },
        },
    )

    assert env["pdf_parse_provider"] == "pypdf_vlm"
    assert env["pdf_force_vlm"] is True
    assert env["pdf_vlm_base_url"] == "http://127.0.0.1:8081/v1"
    assert env["pdf_vlm_max_pages"] == 3


def test_build_default_env_content_maps_pptx_provider() -> None:
    _add_src_path()
    from document_parse_service.filex_config import build_default_env_content

    env = build_default_env_content(
        file_type="pptx",
        config={
            "document_parse": {
                "pptx": {
                    "parse_provider": "python_pptx",
                }
            },
        },
    )

    assert env["pptx_parse_provider"] == "python_pptx"


def test_build_default_env_content_maps_top_level_gateway_vllm() -> None:
    _add_src_path()
    from document_parse_service.filex_config import build_default_env_content

    env = build_default_env_content(
        file_type="pdf",
        config={
            "gateway_vllm": {
                "model_name": "ai_cloud_Kimi_k26_pgc",
                "base_url": "https://antchat.alipay.com/v1",
            },
            "document_parse": {
                "pdf": {
                    "parse_provider": "pypdf_vlm",
                }
            },
        },
    )

    assert env["pdf_parse_provider"] == "pypdf_vlm"
    assert env["gateway_vllm"] == {
        "model_name": "ai_cloud_Kimi_k26_pgc",
        "base_url": "https://antchat.alipay.com/v1",
    }


def test_merge_env_content_deep_merges_caller_overrides() -> None:
    _add_src_path()
    from document_parse_service.filex_config import merge_env_content

    merged = merge_env_content(
        {
            "media_parse_backend": "local",
            "media_parse_options": {
                "model": "base",
                "device": "cpu",
            },
        },
        {
            "media_parse_options": {
                "model": "small",
            },
        },
    )

    assert merged["media_parse_backend"] == "local"
    assert merged["media_parse_options"] == {
        "model": "small",
        "device": "cpu",
    }
