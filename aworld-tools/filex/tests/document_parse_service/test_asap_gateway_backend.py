import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_asap_gateway_backend_passes_prompt(monkeypatch) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.asap_gateway_backend import AsapGatewayBackend

    captured = {}

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return json.dumps({"data": {"file_content": "识别结果"}})

        async def json(self, content_type=None):
            return {"data": {"file_content": "识别结果"}}

    class _Session:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, headers, json):
            captured["endpoint"] = endpoint
            captured["headers"] = headers
            captured["payload"] = json
            return _Response()

    aiohttp_stub = ModuleType("aiohttp")
    aiohttp_stub.ClientSession = _Session
    aiohttp_stub.ClientTimeout = lambda total: {"total": total}
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp_stub)

    result = asyncio.run(
        AsapGatewayBackend().transcribe(
            Path("page.jpg"),
            media_type="image",
            file_type="jpg",
            source_file_name="page",
            options={
                "base_url": "http://127.0.0.1:8081",
                "file_url": "data:image/jpeg;base64,abc",
                "prompt": "请完整识别",
            },
        )
    )

    assert result.text == "识别结果"
    assert captured["endpoint"] == "http://127.0.0.1:8081/api/files/understand"
    assert captured["payload"]["prompt"] == "请完整识别"


def test_asap_gateway_backend_extracts_afts_file_id_from_url() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.asap_gateway_backend import AsapGatewayBackend

    assert AsapGatewayBackend._extract_afts_file_id_from_url(
        "https://mdn.alipayobjects.com/afts/img/A*5cG3TZ86_yQAAAAAQSAAAAgAegAAAQ/original?bz=NebulaBiz"
    ) == "A*5cG3TZ86_yQAAAAAQSAAAAgAegAAAQ"
    assert AsapGatewayBackend._extract_afts_file_id_from_url(
        "https://mdn.alipayobjects.com/aiop_cognihome/afts/file/A*JbQOSIxgUv4AAAAAQlAAAAgAegAAAQ?af_fileName=test.docx"
    ) == "A*JbQOSIxgUv4AAAAAQlAAAAgAegAAAQ"


def test_asap_gateway_backend_normalizes_sanitized_afts_file_id_text() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.asap_gateway_backend import AsapGatewayBackend

    assert AsapGatewayBackend._normalize_candidate_file_id(
        "https___mdn.alipayobjects.com_afts_img_A_iMSiTYEvJZEAAAAAQSAAAAgAegAAAQ_original_bz_NebulaBiz"
    ) == "A*iMSiTYEvJZEAAAAAQSAAAAgAegAAAQ"


def test_asap_gateway_backend_normalizes_url_file_id_in_payload(monkeypatch) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.asap_gateway_backend import AsapGatewayBackend

    captured = {}
    image_url = "https://mdn.alipayobjects.com/afts/img/A*jJJLTYZ4HBoAAAAARzAAAAgAegAAAQ/original?bz=NebulaBiz"

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return json.dumps({"data": {"file_content": "图片解析正文"}})

        async def json(self, content_type=None):
            return {"data": {"file_content": "图片解析正文"}}

    class _Session:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, headers, json):
            captured["payload"] = json
            return _Response()

    aiohttp_stub = ModuleType("aiohttp")
    aiohttp_stub.ClientSession = _Session
    aiohttp_stub.ClientTimeout = lambda total: {"total": total}
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp_stub)

    result = asyncio.run(
        AsapGatewayBackend().transcribe(
            Path("worksheet.png"),
            media_type="image",
            file_type="image",
            source_file_name="worksheet",
            options={
                "base_url": "http://127.0.0.1:8081",
                "file_id": image_url,
            },
        )
    )

    assert result.text == "图片解析正文"
    assert captured["payload"]["file_id"] == "A*jJJLTYZ4HBoAAAAARzAAAAgAegAAAQ"
    assert captured["payload"]["attachments"][0]["fileId"] == "A*jJJLTYZ4HBoAAAAARzAAAAgAegAAAQ"
    assert captured["payload"]["attachments"][0]["fileUrl"] == image_url


def test_openai_compatible_backend_merges_extra_body() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.openai_compatible_backend import (
        OpenAICompatibleMediaTranscriptionBackend,
    )

    payload = OpenAICompatibleMediaTranscriptionBackend()._build_payload(
        file_path=Path("page.jpg"),
        media_type="image",
        file_type="jpg",
        source_file_name="page",
        model="kimi_k26_pc",
        options={
            "media_url": "data:image/jpeg;base64,abc",
            "extra_body": {
                "enable_maya_new_inference_protocol": True,
                "enable_sec_check": True,
            },
        },
    )

    assert payload["enable_maya_new_inference_protocol"] is True
    assert payload["enable_sec_check"] is True
