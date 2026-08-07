import asyncio
import importlib.util
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType


def _load_simple_document_service_module():
    services_dir = Path(__file__).resolve().parent.parent.parent / "src" / "document_parse_service"
    module_path = services_dir / "simple_document_service.py"

    package_root = "aworld_test_simple_service_pkg"
    services_package = f"{package_root}.document_parse_service"
    module_name = f"{services_package}.simple_document_service"

    root_module = ModuleType(package_root)
    root_module.__path__ = []  # type: ignore[attr-defined]
    services_module = ModuleType(services_package)
    services_module.__path__ = [str(services_dir)]  # type: ignore[attr-defined]

    content_extractor_stub = ModuleType(f"{services_package}.content_extractor")
    document_artifact_models_stub = ModuleType(f"{services_package}.document_artifact_models")
    document_artifact_writer_stub = ModuleType(f"{services_package}.document_artifact_writer")
    document_parse_logging_stub = ModuleType(f"{services_package}.document_parse_logging")
    markdown_assembler_stub = ModuleType(f"{services_package}.markdown_assembler")

    @dataclass
    class _MarkdownArtifact:
        markdown_text: str
        assets: list = field(default_factory=list)
        diagnostics: dict = field(default_factory=dict)

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
            pass

        def stage(self, *args, **kwargs):
            return self

        def emit(self, *args, **kwargs):
            return None

        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class _PassthroughMarkdownAssembler:
        def assemble(self, artifact):
            return artifact.markdown_text + "\n\nassembled"

    class _ContentExtractor:
        async def extract_content(self, file_path):
            return "body", {"raw": "ok"}

    content_extractor_stub.ContentExtractor = _ContentExtractor
    document_artifact_models_stub.MarkdownArtifact = _MarkdownArtifact
    document_artifact_writer_stub.DocumentArtifactWriter = _DocumentArtifactWriter
    document_parse_logging_stub.DocumentParseLogger = _DocumentParseLogger
    markdown_assembler_stub.MarkdownAssembler = object
    markdown_assembler_stub.PassthroughMarkdownAssembler = _PassthroughMarkdownAssembler

    original_modules = {
        name: sys.modules.get(name)
        for name in [
            package_root,
            services_package,
            f"{services_package}.content_extractor",
            f"{services_package}.document_artifact_models",
            f"{services_package}.document_artifact_writer",
            f"{services_package}.document_parse_logging",
            f"{services_package}.markdown_assembler",
            f"{services_package}.base_document_service",
            module_name,
        ]
    }

    try:
        sys.modules[package_root] = root_module
        sys.modules[services_package] = services_module
        sys.modules[f"{services_package}.content_extractor"] = content_extractor_stub
        sys.modules[f"{services_package}.document_artifact_models"] = document_artifact_models_stub
        sys.modules[f"{services_package}.document_artifact_writer"] = document_artifact_writer_stub
        sys.modules[f"{services_package}.document_parse_logging"] = document_parse_logging_stub
        sys.modules[f"{services_package}.markdown_assembler"] = markdown_assembler_stub

        # 用真实基类参与测试（其依赖的 models/writer/logging 已被上面桩替换）
        base_module_name = f"{services_package}.base_document_service"
        base_spec = importlib.util.spec_from_file_location(
            base_module_name, services_dir / "base_document_service.py"
        )
        base_module = importlib.util.module_from_spec(base_spec)
        assert base_spec is not None and base_spec.loader is not None
        sys.modules[base_module_name] = base_module
        base_spec.loader.exec_module(base_module)

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_simple_document_service_runs_through_unified_pipeline() -> None:
    module = _load_simple_document_service_module()

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.txt"
        file_path.write_text("demo", encoding="utf-8")
        service = module.SimpleDocumentService(
            file_type="txt",
            content_extractor=module.ContentExtractor(),
        )

        output_path = asyncio.run(
            service.parse_to_markdown(
                file_path=file_path,
                task_id="task-1",
                source_file_name="demo",
            )
        )

        assert output_path.exists()
        assert output_path.read_text(encoding="utf-8") == "body\n\nassembled"
