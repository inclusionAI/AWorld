import asyncio
import sys
from pathlib import Path

import pytest


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_pypdf_vlm_provider_uses_text_layer_when_dense(monkeypatch) -> None:
    _add_src_path()
    from document_parse_service.pypdf_vlm_provider import PdfTextPage, PypdfVlmPdfProvider

    provider = PypdfVlmPdfProvider(env_content={"pdf_pypdf_min_chars_per_page": 5})

    async def _extract_text_pages(file_path):
        return [PdfTextPage(page_number=1, text="这是一段足够长的 PDF 文本层内容。")]

    async def _unexpected_vlm(*args, **kwargs):
        raise AssertionError("VLM should not be called for dense text without prompt")

    monkeypatch.setattr(provider, "_extract_text_pages", _extract_text_pages)
    monkeypatch.setattr(provider, "_understand_rendered_pages", _unexpected_vlm)

    result = asyncio.run(
        provider.understand_pdf(
            file_path=Path("demo.pdf"),
            task_id="task-1",
            source_file_name="demo",
        )
    )
    artifact = provider.to_markdown_artifact(result)

    assert result.tool == "pypdf_text"
    assert "这是一段足够长的 PDF 文本层内容" in artifact.markdown_text
    assert "PDF 信息" not in artifact.markdown_text
    assert "pypdf 状态" not in artifact.markdown_text
    assert "VLM 总耗时" not in artifact.markdown_text
    assert artifact.diagnostics["vlm_page_count"] == 0


def test_pypdf_vlm_provider_uses_vlm_when_prompt_is_set(monkeypatch) -> None:
    _add_src_path()
    from document_parse_service.pypdf_vlm_provider import (
        PdfTextPage,
        PdfRenderedPage,
        PdfVlmPage,
        PypdfVlmPdfProvider,
    )

    provider = PypdfVlmPdfProvider(
        env_content={
            "pdf_pypdf_min_chars_per_page": 1,
            "pdf_understand_prompt": "请完整识别",
        }
    )

    async def _extract_text_pages(file_path):
        return [PdfTextPage(page_number=1, text="文本层提示")]

    async def _understand_rendered_pages(**kwargs):
        assert kwargs["prompt"] == "请完整识别"
        return [PdfVlmPage(page_number=1, text="VLM 识别正文")], [], 123.0, 4

    monkeypatch.setattr(provider, "_extract_text_pages", _extract_text_pages)
    monkeypatch.setattr(provider, "_understand_rendered_pages", _understand_rendered_pages)

    result = asyncio.run(
        provider.understand_pdf(
            file_path=Path("demo.pdf"),
            task_id="task-1",
            source_file_name="demo",
        )
    )
    artifact = provider.to_markdown_artifact(result)

    assert result.tool == "pypdf_vlm_page_understand"
    assert result.pypdf_reason == "prompt_requires_vlm"
    assert "VLM 识别正文" in artifact.markdown_text
    assert "PDF 信息" not in artifact.markdown_text
    assert "pypdf 原因" not in artifact.markdown_text
    assert "VLM 并发数" not in artifact.markdown_text
    assert artifact.diagnostics["vlm_page_count"] == 1
    assert artifact.diagnostics["pypdf_reason"] == "prompt_requires_vlm"


def test_pypdf_vlm_provider_builds_openai_compatible_vlm_options() -> None:
    _add_src_path()
    from document_parse_service.pypdf_vlm_provider import PypdfVlmPdfProvider

    provider = PypdfVlmPdfProvider(
        env_content={
            "pdf_vlm_endpoint": "http://127.0.0.1:8081/v1/chat/completions",
            "pdf_vlm_model": "glm-4.6v",
            "pdf_vlm_auth_token": "token-1",
            "pdf_vlm_max_tokens": 4096,
        }
    )

    options = provider._build_vlm_options(
        task_id="task-1",
        source_file_name="demo",
        page_number=2,
        data_url="data:image/jpeg;base64,abc",
        prompt="请完整识别",
    )

    assert options["backend"] == "openai_compatible"
    assert options["endpoint"] == "http://127.0.0.1:8081/v1/chat/completions"
    assert options["model"] == "glm-4.6v"
    assert options["auth_token"] == "token-1"
    assert options["media_url"] == "data:image/jpeg;base64,abc"
    assert options["media_content_type"] == "image_url"
    assert options["prompt"] == "请完整识别"
    assert options["max_tokens"] == 4096


def test_pypdf_vlm_provider_maps_gateway_vllm_options() -> None:
    _add_src_path()
    from document_parse_service.pypdf_vlm_provider import PypdfVlmPdfProvider

    provider = PypdfVlmPdfProvider(
        env_content={
            "pdf_gateway_vllm": {
                "model_name": "ai_cloud_Kimi_k26_pgc",
                "api_key": "token-1",
                "base_url": "https://antchat.alipay.com/v1",
            }
        }
    )

    options = provider._build_vlm_options(
        task_id="task-1",
        source_file_name="demo",
        page_number=1,
        data_url="data:image/jpeg;base64,abc",
        prompt="请识别",
    )

    assert options["base_url"] == "https://antchat.alipay.com/v1"
    assert options["model"] == "kimi_k26_pc"
    assert options["api_key"] == "token-1"
    assert options["media_content_type"] == "image_url"
    assert options["extra_body"] == {
        "enable_maya_new_inference_protocol": True,
        "enable_sec_check": True,
    }


def test_pypdf_vlm_provider_reuses_paddle_vlm_config_when_gateway_key_missing() -> None:
    _add_src_path()
    from document_parse_service.pypdf_vlm_provider import PypdfVlmPdfProvider

    provider = PypdfVlmPdfProvider(
        env_content={
            "pdf_gateway_vllm": {
                "model_name": "ai_cloud_Kimi_k26_pgc",
            },
            "pdf_paddle_ocr_vl_rec_server_url": "https://antchat.alipay.com/v1",
            "pdf_paddle_ocr_vl_rec_api_model_name": "aisearch_paaldocr_vl_16",
            "pdf_paddle_ocr_vl_rec_api_key": "paddle-key",
        }
    )

    options = provider._build_vlm_options(
        task_id="task-1",
        source_file_name="demo",
        page_number=1,
        data_url="data:image/jpeg;base64,abc",
        prompt="请识别",
    )

    assert options["base_url"] == "https://antchat.alipay.com/v1"
    assert options["api_key"] == "paddle-key"
    assert options["model"] == "kimi_k26_pc"


def test_pypdf_vlm_provider_fails_when_all_vlm_pages_fail(monkeypatch) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.models import TranscriptResult
    from document_parse_service.pypdf_vlm_provider import (
        PdfRenderedPage,
        PdfTextPage,
        PypdfVlmPdfProvider,
    )

    class _EmptyBackend:
        async def transcribe(self, *args, **kwargs):
            return TranscriptResult(
                text="",
                backend="openai_compatible",
                model="model-1",
                metadata={"finish_reason": "stop"},
            )

    provider = PypdfVlmPdfProvider(
        env_content={"pdf_understand_prompt": "请识别"},
        vlm_backend=_EmptyBackend(),
    )

    async def _extract_text_pages(file_path):
        return [PdfTextPage(page_number=1, text="文本层")]

    def _render_pages(file_path):
        return [PdfRenderedPage(page_number=1, image_data_url="data:image/jpeg;base64,abc")]

    monkeypatch.setattr(provider, "_extract_text_pages", _extract_text_pages)
    monkeypatch.setattr(provider, "_render_pages_as_data_urls", _render_pages)

    with pytest.raises(RuntimeError, match="pypdf_vlm provider failed for all 1 rendered page"):
        asyncio.run(
            provider.understand_pdf(
                file_path=Path("demo.pdf"),
                task_id="task-1",
                source_file_name="demo",
            )
        )


def test_pypdf_vlm_provider_retries_rate_limit_and_records_retry(monkeypatch) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.models import TranscriptResult
    from document_parse_service.pypdf_vlm_provider import PdfRenderedPage, PypdfVlmPdfProvider

    class _RateLimitedOnceBackend:
        def __init__(self) -> None:
            self.call_count = 0

        async def transcribe(self, *args, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                raise RuntimeError("status=429 rate limit exceeded")
            return TranscriptResult(text="重试成功", backend="openai_compatible", model="model-1")

    backend = _RateLimitedOnceBackend()
    provider = PypdfVlmPdfProvider(
        env_content={"pdf_vlm_max_retries": 2, "pdf_vlm_retry_base_delay_ms": 0},
        vlm_backend=backend,
    )
    rendered_page = PdfRenderedPage(page_number=1, image_data_url="data:image/jpeg;base64,abc")

    page_number, page, error = asyncio.run(
        provider._understand_one_rendered_page(
            rendered_page=rendered_page,
            task_id="task-1",
            source_file_name="demo",
            pypdf_text="",
            prompt="请识别",
            semaphore=asyncio.Semaphore(1),
            max_concurrency=1,
        )
    )

    assert page_number == 1
    assert error is None
    assert page is not None
    assert page.text == "重试成功"
    assert page.metadata["retry_count"] == 1
    assert backend.call_count == 2


def test_pypdf_vlm_provider_retries_empty_response() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.models import TranscriptResult
    from document_parse_service.pypdf_vlm_provider import PdfRenderedPage, PypdfVlmPdfProvider

    class _EmptyOnceBackend:
        def __init__(self) -> None:
            self.call_count = 0

        async def transcribe(self, *args, **kwargs):
            self.call_count += 1
            text = "" if self.call_count == 1 else "第二次识别成功"
            return TranscriptResult(text=text, backend="openai_compatible", model="model-1")

    backend = _EmptyOnceBackend()
    provider = PypdfVlmPdfProvider(
        env_content={"pdf_vlm_max_retries": 2, "pdf_vlm_retry_base_delay_ms": 0},
        vlm_backend=backend,
    )

    _, page, error = asyncio.run(
        provider._understand_one_rendered_page(
            rendered_page=PdfRenderedPage(
                page_number=1,
                image_data_url="data:image/jpeg;base64,abc",
            ),
            task_id="task-1",
            source_file_name="demo",
            pypdf_text="",
            prompt="请识别",
            semaphore=asyncio.Semaphore(1),
            max_concurrency=1,
        )
    )

    assert error is None
    assert page is not None
    assert page.text == "第二次识别成功"
    assert page.metadata["retry_count"] == 1
    assert backend.call_count == 2


def test_pypdf_vlm_provider_does_not_retry_unauthorized_error() -> None:
    _add_src_path()
    from document_parse_service.pypdf_vlm_provider import PdfRenderedPage, PypdfVlmPdfProvider

    class _UnauthorizedBackend:
        def __init__(self) -> None:
            self.call_count = 0

        async def transcribe(self, *args, **kwargs):
            self.call_count += 1
            raise RuntimeError("status=401 unauthorized")

    backend = _UnauthorizedBackend()
    provider = PypdfVlmPdfProvider(
        env_content={"pdf_vlm_max_retries": 3, "pdf_vlm_retry_base_delay_ms": 0},
        vlm_backend=backend,
    )

    _, page, error = asyncio.run(
        provider._understand_one_rendered_page(
            rendered_page=PdfRenderedPage(
                page_number=1,
                image_data_url="data:image/jpeg;base64,abc",
            ),
            task_id="task-1",
            source_file_name="demo",
            pypdf_text="",
            prompt="请识别",
            semaphore=asyncio.Semaphore(1),
            max_concurrency=1,
        )
    )

    assert page is None
    assert error is not None
    assert error["error_type"] == "vlm_unauthorized"
    assert error["retry_count"] == 0
    assert backend.call_count == 1
