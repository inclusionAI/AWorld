import asyncio
import importlib.util
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType


def _load_provider_module():
    services_dir = Path(__file__).resolve().parent.parent.parent / "src" / "document_parse_service"
    module_path = services_dir / "paddle_ocr_pdf_provider.py"
    package_root = "aworld_test_paddle_pdf_pkg"
    services_package = f"{package_root}.document_parse_service"
    module_name = f"{services_package}.paddle_ocr_pdf_provider"

    root_module = ModuleType(package_root)
    root_module.__path__ = []  # type: ignore[attr-defined]
    services_module = ModuleType(services_package)
    services_module.__path__ = [str(services_dir)]  # type: ignore[attr-defined]
    document_artifact_models_stub = ModuleType(f"{services_package}.document_artifact_models")
    paths_stub = ModuleType(f"{services_package}.paths")

    @dataclass
    class _DocumentAnchor:
        page_number: int = 0
        top: int = 0
        left: int = 0
        before_snippets: list = field(default_factory=list)
        after_snippets: list = field(default_factory=list)

    @dataclass
    class _DocumentAsset:
        asset_id: str
        kind: str
        remote_id: str = ""
        local_path: Path | None = None
        page_number: int = 0
        order: int = 0
        anchor: _DocumentAnchor = field(default_factory=_DocumentAnchor)
        meta: dict = field(default_factory=dict)

    @dataclass
    class _MarkdownArtifact:
        markdown_text: str
        assets: list[_DocumentAsset] = field(default_factory=list)
        diagnostics: dict = field(default_factory=dict)

    document_artifact_models_stub.DocumentAnchor = _DocumentAnchor
    document_artifact_models_stub.DocumentAsset = _DocumentAsset
    document_artifact_models_stub.MarkdownArtifact = _MarkdownArtifact
    tmp_workspace = Path(tempfile.mkdtemp())
    paths_stub.DOCUMENT_PARSE_WORKSPACE = tmp_workspace
    original_modules = {
        name: sys.modules.get(name)
        for name in [
            package_root,
            services_package,
            f"{services_package}.document_artifact_models",
            f"{services_package}.paths",
            module_name,
        ]
    }

    try:
        sys.modules[package_root] = root_module
        sys.modules[services_package] = services_module
        sys.modules[f"{services_package}.document_artifact_models"] = document_artifact_models_stub
        sys.modules[f"{services_package}.paths"] = paths_stub
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        module.DocumentAsset = _DocumentAsset
        module.DOCUMENT_PARSE_WORKSPACE = tmp_workspace
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_paddle_ocr_vl_provider_uses_official_pipeline_markdown() -> None:
    module = _load_provider_module()

    class _FakeResult(dict):
        @property
        def markdown(self):
            return {
                "markdown_texts": "标题\n\n![图](images/fig.png)",
                "markdown_images": {"images/fig.png": b"image-bytes"},
            }

    class _FakePipeline:
        def __init__(self):
            self.predict_kwargs = None

        def predict(self, input_path, **kwargs):
            self.predict_kwargs = kwargs
            assert input_path.endswith("demo.pdf")
            return [_FakeResult(page_index=0, page_count=1)]

        def concatenate_markdown_pages(self, markdown_list):
            return "\n\n".join(item["markdown_texts"] for item in markdown_list)

    fake_pipeline = _FakePipeline()
    provider = module.PaddleOcrPdfProvider(
        env_content={
            "pdf_paddle_ocr_vl_rec_backend": "vllm-server",
            "pdf_paddle_ocr_vl_rec_server_url": "http://127.0.0.1:18081/v1",
            "pdf_paddle_ocr_vl_rec_api_model_name": "local-vlm",
            "pdf_paddle_ocr_max_new_tokens": 128,
        },
        pipeline=fake_pipeline,
    )

    result = asyncio.run(
        provider.understand_pdf(
            file_path=Path("/tmp/demo.pdf"),
            task_id="task-1",
            source_file_name="demo",
        )
    )
    artifact = provider.to_markdown_artifact(result)

    assert fake_pipeline.predict_kwargs["max_new_tokens"] == 128
    assert artifact.markdown_text == "标题\n\n![图](images/fig.png)\n"
    assert artifact.assets[0].local_path.read_bytes() == b"image-bytes"
    assert artifact.assets[0].meta["original_markdown_path"] == "images/fig.png"
    assert artifact.diagnostics["tool"] == "paddleocr_vl"
    assert artifact.diagnostics["first_batch_duration_ms"] >= 0


def test_paddle_ocr_metrics_count_vlm_blocks() -> None:
    provider = _load_provider_module().PaddleOcrPdfProvider(env_content={})

    count = provider._model_call_count(
        [
            {"parsing_res_list": [{"label": "text"}, {"label": "formula"}]},
            {"parsing_res_list": [{"label": "table"}]},
        ]
    )

    assert count == 3


def test_paddle_ocr_pipeline_maps_concurrency_and_retries_429(monkeypatch) -> None:
    module = _load_provider_module()

    class _FakePipeline:
        def __init__(self):
            self.calls = 0

        def predict(self, _input_path, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("429 RPM_LIMIT_EXCEEDED")
            return [{"page_index": 0, "markdown_texts": "恢复成功"}]

        @staticmethod
        def concatenate_markdown_pages(markdown_list):
            return "\n\n".join(item["markdown_texts"] for item in markdown_list)

    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    pipeline = _FakePipeline()
    provider = module.PaddleOcrPdfProvider(
        env_content={
            "pdf_vlm_max_concurrency": 1,
            "pdf_vlm_max_retries": 2,
            "pdf_vlm_retry_base_delay_ms": 1,
        },
        pipeline=pipeline,
    )

    result = asyncio.run(
        provider.understand_pdf(
            file_path=Path("/tmp/demo.pdf"),
            task_id="task-retry",
            source_file_name="demo",
        )
    )

    assert pipeline.calls == 2
    assert result.retry_count == 1
    assert provider._pipeline_kwargs()["vl_rec_max_concurrency"] == 1
    assert provider.to_markdown_artifact(result).diagnostics["model_retry_count"] == 1


def test_replace_markdown_asset_references_prefers_remote_url() -> None:
    module = _load_provider_module()
    asset = module.DocumentAsset(
        asset_id="img-1",
        kind="embedded_image",
        remote_id="A*remote",
        meta={
            "original_markdown_path": "images/fig.png",
            "remote_url": "https://mdn.example/file.jpg",
            "markdown_path": "local/fig.png",
        },
    )

    updated = module.PaddleOcrPdfProvider.replace_markdown_asset_references(
        "![图](images/fig.png)\n<img src=\"images/fig.png\">",
        [asset],
    )

    assert '<img src="https://mdn.example/file.jpg" data-file-id="A*remote" alt="图" />' in updated
    assert '<img src="https://mdn.example/file.jpg" data-file-id="A*remote">' in updated
