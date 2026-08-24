import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType


def _load_service_class():
    services_dir = Path(__file__).resolve().parent.parent.parent / "src" / "document_parse_service"
    module_path = services_dir / "pdf_layout_extract_service.py"
    package_root = "aworld_test_pdf_layout_pkg"
    services_package = f"{package_root}.document_parse_service"
    module_name = f"{services_package}.pdf_layout_extract_service"

    root_module = ModuleType(package_root)
    root_module.__path__ = []  # type: ignore[attr-defined]
    services_module = ModuleType(services_package)
    services_module.__path__ = [str(services_dir)]  # type: ignore[attr-defined]

    original_modules = {
        name: sys.modules.get(name)
        for name in [package_root, services_package, module_name]
    }

    try:
        sys.modules[package_root] = root_module
        sys.modules[services_package] = services_module
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module.PdfLayoutExtractService
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_pdf_layout_extract_service_extracts_texts_and_images() -> None:
    PdfLayoutExtractService = _load_service_class()
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_base = Path(tmp_dir)
        pdf_path = workspace_base / "demo.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 demo")

        def fake_runner(command, **kwargs):
            output_prefix = Path(command[-1])
            output_dir = output_prefix.parent
            output_dir.mkdir(parents=True, exist_ok=True)

            image_path = output_dir / "demo-2_1.jpg"
            image_path.write_bytes(b"jpeg-data")
            xml_path = output_dir / "demo.xml"
            xml_path.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<pdf2xml>
  <page number="2" width="918" height="1188">
    <text top="128" left="130" width="67" height="13">Question:</text>
    <text top="146" left="130" width="382" height="13">What was the actual enrollment count?</text>
    <image top="196" left="147" width="98" height="128" src="%s"/>
    <text top="344" left="130" width="65" height="13">Ground truth:</text>
  </page>
</pdf2xml>
"""
                % image_path,
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

        service = PdfLayoutExtractService(
            workspace_base=workspace_base,
            command_runner=fake_runner,
        )

        result = service.extract_from_pdf(
            pdf_path,
            task_id="task-1",
            source_file_name="demo",
        )

        assert result["page_count"] == 1
        assert result["image_count"] == 1
        assert result["pages"][0]["page_number"] == 2
        assert result["pages"][0]["texts"][0]["text"] == "Question:"
        assert result["pages"][0]["images"][0]["top"] == 196
        assert result["pages"][0]["images"][0]["path"].name == "demo-2_1.jpg"
