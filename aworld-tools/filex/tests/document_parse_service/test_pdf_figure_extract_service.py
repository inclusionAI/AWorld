import importlib.util
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import ModuleType

from PIL import Image


def _load_service_class(fake_layout_service_class):
    module_path = (
        Path(__file__).resolve().parent.parent
        .parent
        / "src"
        / "document_parse_service"
        / "pdf_figure_extract_service.py"
    )
    package_root = f"aworld_test_figure_pkg_{uuid.uuid4().hex}"
    services_package = f"{package_root}.document_parse_service"
    module_name = f"{services_package}.pdf_figure_extract_service"

    root_module = ModuleType(package_root)
    root_module.__path__ = []  # type: ignore[attr-defined]
    services_module = ModuleType(services_package)
    services_module.__path__ = [str(module_path.parent)]  # type: ignore[attr-defined]
    layout_module = ModuleType(f"{services_package}.pdf_layout_extract_service")
    layout_module.PdfLayoutExtractService = fake_layout_service_class

    original_modules = {
        name: sys.modules.get(name)
        for name in [
            package_root,
            services_package,
            f"{services_package}.pdf_layout_extract_service",
            module_name,
        ]
    }

    try:
        sys.modules[package_root] = root_module
        sys.modules[services_package] = services_module
        sys.modules[f"{services_package}.pdf_layout_extract_service"] = layout_module

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module.PdfFigureExtractService
    finally:
        loaded_package_modules = [
            name for name in sys.modules if name == package_root or name.startswith(f"{package_root}.")
        ]
        for name in loaded_package_modules:
            sys.modules.pop(name, None)
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_pdf_figure_extract_service_extracts_caption_based_figure_crop() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_base = Path(tmp_dir)
        pdf_path = workspace_base / "demo.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 demo")

        class FakePdfLayoutExtractService:
            def __init__(self, workspace_base=None, command_runner=None):
                pass

            def extract_from_pdf(self, file_path, *, task_id, source_file_name, password=None):
                return {
                    "xml_path": workspace_base / task_id / "pdf_layout" / "demo.xml",
                    "pages": [
                        {
                            "page_number": 9,
                            "width": 918,
                            "height": 1188,
                            "images": [],
                            "texts": [
                                {"top": 99, "left": 535, "width": 198, "height": 17, "text": "Score of LLMs per capability at Level 1"},
                                {"top": 317, "left": 457, "width": 21, "height": 13, "text": "0.000"},
                                {"top": 361, "left": 493, "width": 22, "height": 13, "text": "GPT4"},
                                {"top": 397, "left": 417, "width": 52, "height": 12, "text": "Figure 5"},
                                {"top": 397, "left": 479, "width": 332, "height": 12, "text": "Score of various LLMs at Level 1 per capability."},
                            ],
                        }
                    ],
                }

        def fake_runner(command, **kwargs):
            render_prefix = Path(command[-1])
            render_prefix.parent.mkdir(parents=True, exist_ok=True)
            rendered_path = render_prefix.parent / f"{render_prefix.name}-9.png"
            Image.new("RGB", (918, 1188), "white").save(rendered_path, format="PNG")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

        PdfFigureExtractService = _load_service_class(FakePdfLayoutExtractService)
        service = PdfFigureExtractService(
            workspace_base=workspace_base,
            command_runner=fake_runner,
        )

        result = service.extract_from_pdf(
            pdf_path,
            task_id="task-1",
            source_file_name="demo",
        )

        assert result["figure_count"] == 1
        figure = result["figures"][0]
        assert figure["page_number"] == 9
        assert figure["caption"].startswith("Figure 5")
        assert figure["path"].exists()
        assert figure["figure_bbox"]["top"] < figure["caption_bbox"]["top"]


def test_pdf_figure_extract_service_prefers_vlm_bbox_when_available() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_base = Path(tmp_dir)
        pdf_path = workspace_base / "demo.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 demo")

        class FakePdfLayoutExtractService:
            def __init__(self, workspace_base=None, command_runner=None):
                pass

            def extract_from_pdf(self, file_path, *, task_id, source_file_name, password=None):
                return {
                    "xml_path": workspace_base / task_id / "pdf_layout" / "demo.xml",
                    "pages": [
                        {
                            "page_number": 1,
                            "width": 400,
                            "height": 300,
                            "images": [],
                            "texts": [
                                {"top": 30, "left": 60, "width": 180, "height": 18, "text": "chart title"},
                                {"top": 230, "left": 80, "width": 58, "height": 12, "text": "Figure 1"},
                                {"top": 230, "left": 144, "width": 180, "height": 12, "text": "Score comparison"},
                            ],
                        }
                    ],
                }

        def fake_runner(command, **kwargs):
            render_prefix = Path(command[-1])
            render_prefix.parent.mkdir(parents=True, exist_ok=True)
            rendered_path = render_prefix.parent / f"{render_prefix.name}-1.png"
            Image.new("RGB", (400, 300), "white").save(rendered_path, format="PNG")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "regions": [
                        {"label": "figure", "bbox": [70, 40, 330, 210], "confidence": 0.95},
                    ]
                }

        def fake_post(url, data, files, timeout):
            assert url == "http://127.0.0.1:8081/figure-locate"
            assert data["detect_mode"] in {"all", "primary"}
            assert files["file"][0] == "page.png"
            return FakeResponse()

        PdfFigureExtractService = _load_service_class(FakePdfLayoutExtractService)
        service = PdfFigureExtractService(
            workspace_base=workspace_base,
            command_runner=fake_runner,
            figure_locate_url="http://127.0.0.1:8081/figure-locate",
            request_post=fake_post,
        )

        result = service.extract_from_pdf(
            pdf_path,
            task_id="task-1",
            source_file_name="demo",
        )

        assert result["figure_count"] == 1
        figure = result["figures"][0]
        assert figure["locator"] == "vlm_all"
        assert figure["figure_bbox"] == {
            "left": 70,
            "top": 40,
            "right": 330,
            "bottom": 210,
        }


def test_pdf_figure_extract_service_extracts_multiple_vlm_regions_per_page() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_base = Path(tmp_dir)
        pdf_path = workspace_base / "demo.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 demo")

        class FakePdfLayoutExtractService:
            def __init__(self, workspace_base=None, command_runner=None):
                pass

            def extract_from_pdf(self, file_path, *, task_id, source_file_name, password=None):
                return {
                    "xml_path": workspace_base / task_id / "pdf_layout" / "demo.xml",
                    "pages": [
                        {
                            "page_number": 1,
                            "width": 500,
                            "height": 400,
                            "images": [],
                            "texts": [
                                {"top": 260, "left": 40, "width": 90, "height": 14, "text": "Figure 1"},
                                {"top": 260, "left": 135, "width": 120, "height": 14, "text": "Left chart"},
                                {"top": 260, "left": 300, "width": 90, "height": 14, "text": "Figure 2"},
                                {"top": 260, "left": 395, "width": 90, "height": 14, "text": "Right chart"},
                            ],
                        }
                    ],
                }

        def fake_runner(command, **kwargs):
            render_prefix = Path(command[-1])
            render_prefix.parent.mkdir(parents=True, exist_ok=True)
            rendered_path = render_prefix.parent / f"{render_prefix.name}-1.png"
            Image.new("RGB", (500, 400), "white").save(rendered_path, format="PNG")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "regions": [
                        {"label": "figure", "bbox": [40, 40, 220, 230], "confidence": 0.95},
                        {"label": "figure", "bbox": [280, 50, 470, 230], "confidence": 0.93},
                    ]
                }

        def fake_post(url, data, files, timeout):
            assert data["detect_mode"] == "all"
            return FakeResponse()

        PdfFigureExtractService = _load_service_class(FakePdfLayoutExtractService)
        service = PdfFigureExtractService(
            workspace_base=workspace_base,
            command_runner=fake_runner,
            figure_locate_url="http://127.0.0.1:8081/figure-locate",
            request_post=fake_post,
        )

        result = service.extract_from_pdf(
            pdf_path,
            task_id="task-1",
            source_file_name="demo",
        )

        assert result["figure_count"] == 2
        assert [item["locator"] for item in result["figures"]] == ["vlm_all", "vlm_all"]
        assert result["figures"][0]["figure_bbox"] == {
            "left": 40,
            "top": 40,
            "right": 220,
            "bottom": 230,
        }
        assert result["figures"][1]["figure_bbox"] == {
            "left": 280,
            "top": 50,
            "right": 470,
            "bottom": 230,
        }
