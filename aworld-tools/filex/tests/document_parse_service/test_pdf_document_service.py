import importlib.util
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType


def _load_pdf_document_service_module():
    services_dir = Path(__file__).resolve().parent.parent.parent / "src" / "document_parse_service"
    module_path = services_dir / "pdf_document_service.py"

    package_root = "aworld_test_pdf_document_pkg"
    services_package = f"{package_root}.document_parse_service"
    module_name = f"{services_package}.pdf_document_service"

    root_module = ModuleType(package_root)
    root_module.__path__ = []  # type: ignore[attr-defined]
    services_module = ModuleType(services_package)
    services_module.__path__ = [str(services_dir)]  # type: ignore[attr-defined]

    liteparse_pdf_stub = ModuleType(f"{services_package}.liteparse_pdf_service")
    markdown_assembler_stub = ModuleType(f"{services_package}.markdown_assembler")
    document_artifact_writer_stub = ModuleType(f"{services_package}.document_artifact_writer")
    document_artifact_models_stub = ModuleType(f"{services_package}.document_artifact_models")
    document_parse_logging_stub = ModuleType(f"{services_package}.document_parse_logging")

    @dataclass
    class _DocumentAnchor:
        page_number: int = 0
        top: int = 0
        left: int = 0
        before_snippets: list[str] = field(default_factory=list)
        after_snippets: list[str] = field(default_factory=list)

    @dataclass
    class _DocumentAsset:
        asset_id: str
        kind: str
        remote_id: str = ""
        local_path: Path | None = None
        page_number: int = 0
        order: int = 0
        anchor: object = field(default_factory=_DocumentAnchor)
        meta: dict = field(default_factory=dict)

    @dataclass
    class _MarkdownArtifact:
        markdown_text: str
        assets: list[_DocumentAsset] = field(default_factory=list)
        diagnostics: dict = field(default_factory=dict)

    class _AnchoredMarkdownAssembler:
        def assemble(self, artifact):
            if not artifact.assets:
                return artifact.markdown_text
            asset = artifact.assets[0]
            asset_reference = asset.remote_id or asset.meta.get("markdown_path") or asset.meta.get("local_path")
            return artifact.markdown_text + f"\n\n![图片 1]({asset_reference})"

    class _DocumentArtifactWriter:
        def write_markdown(self, artifact, *, output_dir, file_name):
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / file_name
            output_path.write_text(artifact.markdown_text, encoding="utf-8")
            return output_path

        def write_metrics(self, metrics, *, output_path):
            metrics_path = output_path.with_suffix(".metrics.json")
            metrics_path.write_text(__import__("json").dumps(metrics), encoding="utf-8")
            return metrics_path

    class _DocumentParseLogger:
        def __init__(self, *args, **kwargs):
            self.stage_durations_ms = {}

        def stage(self, *args, **kwargs):
            return self

        def emit(self, *args, **kwargs):
            return None

        def progress(self, *args, **kwargs):
            return None

        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class _LiteParseContentExtractor:
        def __init__(self, env_content=None):
            self.env_content = env_content or {}

        async def parse_to_artifact_result(
            self,
            *,
            file_path,
            task_id,
            source_file_name,
            afts_service,
            markdown_assembler,
            stage_logger=None,
        ):
            artifact = _MarkdownArtifact(
                markdown_text="正文",
                assets=[
                    _DocumentAsset(
                        asset_id="img-1",
                        kind="embedded_image",
                        remote_id="file-1",
                    )
                ],
            )
            artifact.markdown_text = markdown_assembler.assemble(artifact)
            return artifact, {"raw": "ok"}

        def should_output_debug_json(self):
            return False

        def write_debug_sidecar(self, parse_result, output_dir, source_file_name):
            raise AssertionError("write_debug_sidecar should not be called")

    liteparse_pdf_stub.LiteParseContentExtractor = _LiteParseContentExtractor
    markdown_assembler_stub.MarkdownAssembler = object
    markdown_assembler_stub.AnchoredMarkdownAssembler = _AnchoredMarkdownAssembler
    document_artifact_writer_stub.DocumentArtifactWriter = _DocumentArtifactWriter
    document_artifact_models_stub.MarkdownArtifact = _MarkdownArtifact
    document_artifact_models_stub.DocumentAsset = _DocumentAsset
    document_artifact_models_stub.DocumentAnchor = _DocumentAnchor
    document_parse_logging_stub.DocumentParseLogger = _DocumentParseLogger

    original_modules = {
        name: sys.modules.get(name)
        for name in [
            package_root,
            services_package,
            f"{services_package}.liteparse_pdf_service",
            f"{services_package}.markdown_assembler",
            f"{services_package}.document_artifact_writer",
            f"{services_package}.document_artifact_models",
            f"{services_package}.document_parse_logging",
            f"{services_package}.asset_reference",
            f"{services_package}.document_asset_publisher",
            f"{services_package}.paths",
            f"{services_package}.base_document_service",
            f"{services_package}.liteparse_document_service",
            module_name,
        ]
    }

    try:
        sys.modules[package_root] = root_module
        sys.modules[services_package] = services_module
        sys.modules[f"{services_package}.liteparse_pdf_service"] = liteparse_pdf_stub
        sys.modules[f"{services_package}.markdown_assembler"] = markdown_assembler_stub
        sys.modules[f"{services_package}.document_artifact_writer"] = document_artifact_writer_stub
        sys.modules[f"{services_package}.document_artifact_models"] = document_artifact_models_stub
        sys.modules[f"{services_package}.document_parse_logging"] = document_parse_logging_stub

# Exercise the real base class with its dependencies stubbed above.
        for real_name in ("base_document_service", "liteparse_document_service"):
            real_module_name = f"{services_package}.{real_name}"
            real_spec = importlib.util.spec_from_file_location(
                real_module_name, services_dir / f"{real_name}.py"
            )
            real_module = importlib.util.module_from_spec(real_spec)
            assert real_spec is not None and real_spec.loader is not None
            sys.modules[real_module_name] = real_module
            real_spec.loader.exec_module(real_module)

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        module.DocumentAsset = _DocumentAsset
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_pdf_document_service_parse_to_markdown_writes_assembled_markdown() -> None:
    module = _load_pdf_document_service_module()

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.pdf"
        file_path.write_bytes(b"%PDF-1.4 demo")

        service = module.PdfDocumentService()
        output_path = __import__("asyncio").run(
            service.parse_to_markdown(
                file_path=file_path,
                task_id="task-1",
                source_file_name="demo",
            )
        )

        assert output_path.exists()
        assert output_path.read_text(encoding="utf-8") == "正文\n\n![图片 1](file-1)"


def test_pdf_document_service_can_use_pypdf_vlm_provider() -> None:
    module = _load_pdf_document_service_module()

    @dataclass
    class _ProviderResult:
        markdown_text: str

    @dataclass
    class _ProviderArtifact:
        markdown_text: str
        assets: list = field(default_factory=list)
        diagnostics: dict = field(default_factory=dict)

    class _PdfProvider:
        name = "pypdf_vlm"

        async def understand_pdf(self, *, file_path, task_id, source_file_name):
            return _ProviderResult(markdown_text=f"{source_file_name}:{task_id}")

        def to_markdown_artifact(self, result):
            return _ProviderArtifact(markdown_text=f"PDF provider result {result.markdown_text}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.pdf"
        file_path.write_bytes(b"%PDF-1.4 demo")

        service = module.PdfDocumentService(
            env_content={"pdf_parse_provider": "pypdf_vlm"},
            pdf_provider=_PdfProvider(),
        )
        output_path = __import__("asyncio").run(
            service.parse_to_markdown(
                file_path=file_path,
                task_id="task-pdf",
                source_file_name="demo",
            )
        )

        assert output_path.exists()
        assert output_path.read_text(encoding="utf-8") == "PDF provider result demo:task-pdf"


def test_pdf_document_service_can_use_paddle_ocr_provider() -> None:
    module = _load_pdf_document_service_module()

    @dataclass
    class _ProviderResult:
        markdown_text: str

    @dataclass
    class _ProviderArtifact:
        markdown_text: str
        assets: list = field(default_factory=list)
        diagnostics: dict = field(default_factory=dict)

    class _PdfProvider:
        name = "paddle_ocr"

        async def understand_pdf(self, *, file_path, task_id, source_file_name):
            return _ProviderResult(markdown_text=f"{source_file_name}:{task_id}")

        def to_markdown_artifact(self, result):
            return _ProviderArtifact(markdown_text=f"PaddleOCR provider result {result.markdown_text}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.pdf"
        file_path.write_bytes(b"%PDF-1.4 demo")

        service = module.PdfDocumentService(
            env_content={"pdf_parse_provider": "paddle_ocr"},
            pdf_provider=_PdfProvider(),
        )
        output_path = __import__("asyncio").run(
            service.parse_to_markdown(
                file_path=file_path,
                task_id="task-paddle",
                source_file_name="demo",
            )
        )

        assert output_path.exists()
        assert output_path.read_text(encoding="utf-8") == "PaddleOCR provider result demo:task-paddle"


def test_pdf_document_service_selects_pages_before_provider_and_records_mapping() -> None:
    module = _load_pdf_document_service_module()
    from pypdf import PdfReader, PdfWriter

    @dataclass
    class _ProviderArtifact:
        markdown_text: str
        assets: list = field(default_factory=list)
        diagnostics: dict = field(default_factory=lambda: {"provider": "paddle_ocr", "page_count": 2})

    class _PdfProvider:
        name = "paddle_ocr"

        async def understand_pdf(self, *, file_path, task_id, source_file_name):
            reader = PdfReader(str(file_path))
            return [float(page.mediabox.width) for page in reader.pages]

        def to_markdown_artifact(self, result):
            return _ProviderArtifact(markdown_text=f"selected widths: {result}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_blank_page(width=200, height=200)
        writer.add_blank_page(width=300, height=300)
        with file_path.open("wb") as output_file:
            writer.write(output_file)

        service = module.PdfDocumentService(
            env_content={
                "pdf_parse_provider": "paddle_ocr",
                "pdf_pages": "3,1",
                "first_batch_page_count": 1,
            },
            pdf_provider=_PdfProvider(),
        )
        output_path = __import__("asyncio").run(
            service.parse_to_markdown(
                file_path=file_path,
                task_id="task-selected-pages",
                source_file_name="demo",
            )
        )

        assert output_path.read_text(encoding="utf-8") == "selected widths: [300.0, 100.0]"
        metrics = __import__("json").loads(output_path.with_suffix(".metrics.json").read_text())
        assert metrics["work"]["requested"] == 2
        assert metrics["work"]["processed"] == 2
        assert metrics["work"]["first_batch_count"] == 1
        assert metrics["type_metrics"]["pdf"]["source_page_count"] == 3
        assert metrics["type_metrics"]["pdf"]["requested_pages"] == [3, 1]


def test_pdf_document_service_processes_provider_in_page_batches() -> None:
    module = _load_pdf_document_service_module()
    from pypdf import PdfReader, PdfWriter

    @dataclass
    class _ProviderArtifact:
        markdown_text: str
        assets: list = field(default_factory=list)
        diagnostics: dict = field(default_factory=lambda: {"provider": "paddle_ocr"})

    class _PdfProvider:
        name = "paddle_ocr"

        def __init__(self):
            self.calls = []

        async def understand_pdf(self, *, file_path, task_id, source_file_name):
            widths = [float(page.mediabox.width) for page in PdfReader(str(file_path)).pages]
            self.calls.append({"task_id": task_id, "widths": widths})
            return widths

        def to_markdown_artifact(self, result):
            return _ProviderArtifact(
                markdown_text=f"batch widths: {result}",
                diagnostics={
                    "provider": "paddle_ocr",
                    "page_count": len(result),
                    "model_call_count": len(result),
                },
            )

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.pdf"
        writer = PdfWriter()
        for width in (100, 200, 300, 400):
            writer.add_blank_page(width=width, height=width)
        with file_path.open("wb") as output_file:
            writer.write(output_file)

        provider = _PdfProvider()
        service = module.PdfDocumentService(
            env_content={
                "pdf_parse_provider": "paddle_ocr",
                "pdf_pages": "4,2,1",
                "pdf_page_batch_size": 2,
            },
            pdf_provider=provider,
        )
        output_path = __import__("asyncio").run(
            service.parse_to_markdown(
                file_path=file_path,
                task_id="task-page-batches",
                source_file_name="demo",
            )
        )

        assert [call["widths"] for call in provider.calls] == [[400.0, 200.0], [100.0]]
        assert output_path.read_text(encoding="utf-8") == (
            "batch widths: [400.0, 200.0]\n\nbatch widths: [100.0]"
        )
        metrics = __import__("json").loads(output_path.with_suffix(".metrics.json").read_text())
        assert metrics["work"]["requested"] == 3
        assert metrics["work"]["processed"] == 3
        assert metrics["work"]["batch_count"] == 2
        assert metrics["work"]["first_batch_count"] == 2
        assert metrics["model"]["call_count"] == 3
        assert metrics["type_metrics"]["pdf"]["requested_pages"] == [4, 2, 1]


def test_pdf_document_service_resumes_completed_page_batches() -> None:
    module = _load_pdf_document_service_module()
    from pypdf import PdfReader, PdfWriter

    @dataclass
    class _ProviderArtifact:
        markdown_text: str
        assets: list = field(default_factory=list)
        diagnostics: dict = field(default_factory=lambda: {"provider": "paddle_ocr"})

    class _PdfProvider:
        name = "paddle_ocr"

        def __init__(self):
            self.calls = 0

        async def understand_pdf(self, *, file_path, task_id, source_file_name):
            self.calls += 1
            return [float(page.mediabox.width) for page in PdfReader(str(file_path)).pages]

        def to_markdown_artifact(self, result):
            return _ProviderArtifact(markdown_text=f"batch widths: {result}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "resume.pdf"
        writer = PdfWriter()
        for width in (100, 200, 300):
            writer.add_blank_page(width=width, height=width)
        with file_path.open("wb") as output_file:
            writer.write(output_file)

        env_content = {
            "pdf_parse_provider": "paddle_ocr",
            "pdf_page_batch_size": 2,
            "pdf_batch_resume_id": f"resume-test-{Path(tmp_dir).name}",
        }
        first_provider = _PdfProvider()
        first_output = __import__("asyncio").run(
            module.PdfDocumentService(env_content=env_content, pdf_provider=first_provider).parse_to_markdown(
                file_path=file_path,
                task_id="task-first-run",
                source_file_name="resume",
            )
        )
        second_provider = _PdfProvider()
        second_output = __import__("asyncio").run(
            module.PdfDocumentService(env_content=env_content, pdf_provider=second_provider).parse_to_markdown(
                file_path=file_path,
                task_id="task-second-run",
                source_file_name="resume",
            )
        )

        assert first_provider.calls == 2
        assert second_provider.calls == 0
        assert second_output.read_text(encoding="utf-8") == first_output.read_text(encoding="utf-8")
        metrics = __import__("json").loads(second_output.with_suffix(".metrics.json").read_text())
        assert metrics["work"]["batch_count"] == 2
        assert metrics["work"]["resumed_batch_count"] == 2


def test_pdf_document_service_retry_only_runs_failed_page_batch() -> None:
    module = _load_pdf_document_service_module()
    from pypdf import PdfReader, PdfWriter

    @dataclass
    class _ProviderArtifact:
        markdown_text: str
        assets: list = field(default_factory=list)
        diagnostics: dict = field(default_factory=lambda: {"provider": "paddle_ocr"})

    class _FailSecondBatchProvider:
        name = "paddle_ocr"

        def __init__(self):
            self.calls = 0

        async def understand_pdf(self, *, file_path, task_id, source_file_name):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("second batch failed")
            return [float(page.mediabox.width) for page in PdfReader(str(file_path)).pages]

        def to_markdown_artifact(self, result):
            return _ProviderArtifact(markdown_text=f"batch widths: {result}")

    class _SuccessfulProvider(_FailSecondBatchProvider):
        async def understand_pdf(self, *, file_path, task_id, source_file_name):
            self.calls += 1
            return [float(page.mediabox.width) for page in PdfReader(str(file_path)).pages]

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "retry.pdf"
        writer = PdfWriter()
        for width in (100, 200, 300, 400):
            writer.add_blank_page(width=width, height=width)
        with file_path.open("wb") as output_file:
            writer.write(output_file)

        env_content = {
            "pdf_parse_provider": "paddle_ocr",
            "pdf_page_batch_size": 2,
            "pdf_batch_resume_id": f"retry-test-{Path(tmp_dir).name}",
        }
        failing_provider = _FailSecondBatchProvider()
        try:
            __import__("asyncio").run(
                module.PdfDocumentService(
                    env_content=env_content,
                    pdf_provider=failing_provider,
                ).parse_to_markdown(
                    file_path=file_path,
                    task_id="task-failed-run",
                    source_file_name="retry",
                )
            )
        except RuntimeError as exc:
            assert "second batch failed" in str(exc)
        else:
            raise AssertionError("expected the second page batch to fail")

        retry_provider = _SuccessfulProvider()
        output_path = __import__("asyncio").run(
            module.PdfDocumentService(
                env_content=env_content,
                pdf_provider=retry_provider,
            ).parse_to_markdown(
                file_path=file_path,
                task_id="task-retry-run",
                source_file_name="retry",
            )
        )

        assert failing_provider.calls == 2
        assert retry_provider.calls == 1
        assert output_path.read_text(encoding="utf-8") == (
            "batch widths: [100.0, 200.0]\n\nbatch widths: [300.0, 400.0]"
        )
        metrics = __import__("json").loads(output_path.with_suffix(".metrics.json").read_text())
        assert metrics["work"]["batch_count"] == 2
        assert metrics["work"]["resumed_batch_count"] == 1


def test_pdf_document_service_batches_liteparse_path() -> None:
    module = _load_pdf_document_service_module()
    from pypdf import PdfReader, PdfWriter

    class _ContentExtractor:
        def __init__(self):
            self.page_counts = []

        async def parse_to_artifact_result(
            self,
            *,
            file_path,
            task_id,
            source_file_name,
            afts_service,
            markdown_assembler,
            stage_logger,
        ):
            page_count = len(PdfReader(str(file_path)).pages)
            self.page_counts.append(page_count)
            return (
                module.MarkdownArtifact(
                    markdown_text=f"liteparse batch pages: {page_count}",
                    diagnostics={"provider": "liteparse", "page_count": page_count},
                ),
                {"page_count": page_count},
            )

        def should_output_debug_json(self):
            return False

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "liteparse.pdf"
        writer = PdfWriter()
        for _ in range(5):
            writer.add_blank_page(width=100, height=100)
        with file_path.open("wb") as output_file:
            writer.write(output_file)

        extractor = _ContentExtractor()
        output_path = __import__("asyncio").run(
            module.PdfDocumentService(
                env_content={"pdf_page_batch_size": 2},
                content_extractor=extractor,
            ).parse_to_markdown(
                file_path=file_path,
                task_id="task-liteparse-batches",
                source_file_name="liteparse",
            )
        )

        assert extractor.page_counts == [2, 2, 1]
        assert output_path.read_text(encoding="utf-8") == (
            "liteparse batch pages: 2\n\nliteparse batch pages: 2\n\nliteparse batch pages: 1"
        )
        metrics = __import__("json").loads(output_path.with_suffix(".metrics.json").read_text())
        assert metrics["provider"] == "liteparse"
        assert metrics["work"]["processed"] == 5
        assert metrics["work"]["batch_count"] == 3


def test_pdf_document_service_provider_publishes_assets_to_afts() -> None:
    module = _load_pdf_document_service_module()

    @dataclass
    class _ProviderResult:
        markdown_text: str

    @dataclass
    class _ProviderArtifact:
        markdown_text: str
        assets: list = field(default_factory=list)
        diagnostics: dict = field(default_factory=dict)

    class _PdfProvider:
        name = "paddle_ocr"

        async def understand_pdf(self, *, file_path, task_id, source_file_name):
            return _ProviderResult(markdown_text=f"{source_file_name}:{task_id}")

        def to_markdown_artifact(self, result):
            image_path = Path(tempfile.gettempdir()) / "paddle-provider-image.png"
            image_path.write_bytes(b"image")
            return _ProviderArtifact(
                markdown_text=f"PaddleOCR provider result {result.markdown_text}",
                assets=[
                    module.DocumentAsset(
                        asset_id="img-1",
                        kind="embedded_image",
                        local_path=image_path,
                        page_number=1,
                        order=1,
                        meta={"index": "1"},
                    )
                ],
            )

    class _AftsService:
        async def upload_file(self, *, file_path, file_name, setpublic, update_alias):
            assert file_name == "paddle-provider-image.png"
            assert setpublic is True
            assert update_alias is True
            return "A*remote-image"

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.pdf"
        file_path.write_bytes(b"%PDF-1.4 demo")

        service = module.PdfDocumentService(
            env_content={"pdf_parse_provider": "paddle_ocr"},
            pdf_provider=_PdfProvider(),
        )
        output_path = __import__("asyncio").run(
            service.parse_to_markdown(
                file_path=file_path,
                task_id="task-paddle-afts",
                source_file_name="demo",
                afts_service=_AftsService(),
            )
        )

        assert output_path.exists()
        assert output_path.read_text(encoding="utf-8") == (
            "PaddleOCR provider result demo:task-paddle-afts\n\n![图片 1](A*remote-image)"
        )
