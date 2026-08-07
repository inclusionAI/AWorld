import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType


def _load_service_module():
    module_path = (
        Path(__file__).resolve().parent.parent
        .parent
        / "src"
        / "document_parse_service"
        / "liteparse_pdf_service.py"
    )

    package_root = "aworld_test_pkg"
    services_package = f"{package_root}.document_parse_service"
    module_name = f"{services_package}.liteparse_pdf_service"

    root_module = ModuleType(package_root)
    root_module.__path__ = []  # type: ignore[attr-defined]
    services_module = ModuleType(services_package)
    services_module.__path__ = [str(module_path.parent)]  # type: ignore[attr-defined]

    liteparse_stub = ModuleType("liteparse")

    class _LiteParse:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    liteparse_stub.LiteParse = _LiteParse

    pdf_image_stub = ModuleType(f"{services_package}.pdf_image_extract_service")
    pdf_image_stub.PdfImageExtractService = object
    pdf_figure_stub = ModuleType(f"{services_package}.pdf_figure_extract_service")
    pdf_figure_stub.PdfFigureExtractService = object
    pdf_layout_stub = ModuleType(f"{services_package}.pdf_layout_extract_service")
    pdf_layout_stub.PdfLayoutExtractService = object
    document_artifact_models_stub = ModuleType(f"{services_package}.document_artifact_models")
    document_artifact_writer_stub = ModuleType(f"{services_package}.document_artifact_writer")
    document_asset_publisher_stub = ModuleType(f"{services_package}.document_asset_publisher")
    markdown_assembler_path = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "document_parse_service"
        / "markdown_assembler.py"
    )
    markdown_assembler_module_name = f"{services_package}.markdown_assembler"

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
        anchor: _DocumentAnchor = field(default_factory=_DocumentAnchor)
        meta: dict = field(default_factory=dict)

    @dataclass
    class _MarkdownArtifact:
        markdown_text: str
        assets: list[_DocumentAsset] = field(default_factory=list)
        diagnostics: dict = field(default_factory=dict)

    class _AftsDocumentAssetPublisher:
        def __init__(self, afts_service):
            self.afts_service = afts_service

        async def publish_assets(self, assets):
            return assets

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

    document_artifact_models_stub.DocumentAnchor = _DocumentAnchor
    document_artifact_models_stub.DocumentAsset = _DocumentAsset
    document_artifact_models_stub.MarkdownArtifact = _MarkdownArtifact
    document_artifact_writer_stub.DocumentArtifactWriter = _DocumentArtifactWriter
    document_asset_publisher_stub.AftsDocumentAssetPublisher = _AftsDocumentAssetPublisher

    original_modules = {
        name: sys.modules.get(name)
        for name in [
            package_root,
            services_package,
            "liteparse",
            f"{services_package}.document_artifact_models",
            f"{services_package}.document_artifact_writer",
            f"{services_package}.document_asset_publisher",
            markdown_assembler_module_name,
            f"{services_package}.pdf_image_extract_service",
            f"{services_package}.pdf_figure_extract_service",
            f"{services_package}.pdf_layout_extract_service",
            module_name,
        ]
    }

    try:
        sys.modules[package_root] = root_module
        sys.modules[services_package] = services_module
        sys.modules["liteparse"] = liteparse_stub
        sys.modules[f"{services_package}.document_artifact_models"] = document_artifact_models_stub
        sys.modules[f"{services_package}.document_artifact_writer"] = document_artifact_writer_stub
        sys.modules[f"{services_package}.document_asset_publisher"] = document_asset_publisher_stub
        sys.modules[f"{services_package}.pdf_image_extract_service"] = pdf_image_stub
        sys.modules[f"{services_package}.pdf_figure_extract_service"] = pdf_figure_stub
        sys.modules[f"{services_package}.pdf_layout_extract_service"] = pdf_layout_stub

        markdown_assembler_spec = importlib.util.spec_from_file_location(
            markdown_assembler_module_name,
            markdown_assembler_path,
        )
        markdown_assembler_module = importlib.util.module_from_spec(markdown_assembler_spec)
        assert markdown_assembler_spec is not None and markdown_assembler_spec.loader is not None
        sys.modules[markdown_assembler_module_name] = markdown_assembler_module
        markdown_assembler_spec.loader.exec_module(markdown_assembler_module)

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


def test_insert_pdf_images_into_markdown_by_position_inserts_after_before_anchor() -> None:
    module = _load_service_module()
    service = module.LiteParseContentExtractor()

    markdown = (
        "Level 1\n\n"
        "Question: What was the actual enrollment count?\n\n"
        "Ground truth: 90\n\n"
        "Level 2\n\n"
        "Question: If this whole pint is made up of ice cream...\n"
    )
    positioned_image_infos = [
        {
            "file_id": "file-1",
            "index": "1",
            "page_number": 2,
            "top": 196,
            "left": 147,
            "before_snippets": ["Ground truth: 90"],
            "after_snippets": ["Level 2"],
        }
    ]

    updated = service._insert_pdf_images_into_markdown_by_position(
        markdown,
        positioned_image_infos,
    )

    assert (
        'Ground truth: 90\n\n<img src="file-1" data-file-id="file-1" alt="图片 1" />\n\nLevel 2'
        in updated
    )


def test_insert_pdf_images_into_markdown_by_position_falls_back_to_append_section() -> None:
    module = _load_service_module()
    service = module.LiteParseContentExtractor()

    markdown = "Only body text"
    positioned_image_infos = [
        {
            "file_id": "file-2",
            "index": "2",
            "page_number": 5,
            "top": 302,
            "left": 696,
            "before_snippets": ["anchor not found"],
            "after_snippets": ["another missing anchor"],
        }
    ]

    updated = service._insert_pdf_images_into_markdown_by_position(
        markdown,
        positioned_image_infos,
    )

    assert updated.endswith(
        '## 提取图片\n\n<img src="file-2" data-file-id="file-2" alt="图片 2" />'
    )
