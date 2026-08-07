import sys
from pathlib import Path


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_image_document_service_defaults_to_gateway_vllm() -> None:
    _add_src_path()
    from document_parse_service.media_document_service import ImageDocumentService

    service = ImageDocumentService(
        file_type="jpg",
        env_content={
            "gateway_vllm": {
                "model_name": "image-model",
                "base_url": "https://model.example.test/v1",
                "api_key": "image-key",
            }
        },
    )

    assert service._backend.name == "openai_compatible"
    assert service._backend_options == {
        "backend": "openai_compatible",
        "model": "image-model",
        "base_url": "https://model.example.test/v1",
        "api_key": "image-key",
    }


def test_image_document_service_keeps_explicit_backend_override() -> None:
    _add_src_path()
    from document_parse_service.media_document_service import ImageDocumentService

    service = ImageDocumentService(
        file_type="png",
        env_content={
            "media_parse_backend": "asap_gateway",
            "media_parse_options": {"base_url": "http://gateway.example.test"},
        },
    )

    assert service._backend.name == "asap_gateway"
    assert service._backend_options["base_url"] == "http://gateway.example.test"
