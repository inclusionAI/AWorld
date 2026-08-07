import importlib.util
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import ModuleType


def _load_service_class():
    services_dir = Path(__file__).resolve().parent.parent.parent / "src" / "document_parse_service"
    module_path = services_dir / "pdf_image_extract_service.py"
    package_root = "aworld_test_pdf_image_pkg"
    services_package = f"{package_root}.document_parse_service"
    module_name = f"{services_package}.pdf_image_extract_service"

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
        return module.PdfImageExtractService
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_pdf_image_extract_service_extracts_images_and_creates_archive() -> None:
    PdfImageExtractService = _load_service_class()
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_base = Path(tmp_dir)
        pdf_path = workspace_base / "demo.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 demo")

        def fake_runner(command, **kwargs):
            if command[1] == "-list":
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=(
                        "page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio\n"
                        "--------------------------------------------------------------------------------------------\n"
                        "   1     0 image     100   200  rgb     3   8  jpeg   no        10  0   300   300 12K 1.0%\n"
                        "   2     1 image      80    60  rgb     3   8  image  no        11  0   150   150  8K 0.5%\n"
                    ),
                    stderr="",
                )

            output_prefix = Path(command[-1])
            first_image = output_prefix.parent / f"{output_prefix.name}-001.jpg"
            second_image = output_prefix.parent / f"{output_prefix.name}-002.png"
            first_image.write_bytes(b"jpeg-data")
            second_image.write_bytes(b"png-data")
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=f"{first_image}\n{second_image}\n",
                stderr="",
            )

        service = PdfImageExtractService(
            workspace_base=workspace_base,
            command_runner=fake_runner,
        )

        result = service.extract_from_pdf(
            pdf_path,
            task_id="task-1",
            source_file_name="demo",
        )

        assert result["image_count"] == 2
        assert [path.name for path in result["images"]] == ["demo-001.jpg", "demo-002.png"]
        assert result["archive_path"] is not None
        assert result["archive_path"].exists()
        assert result["manifest"][0]["page"] == 1
        with zipfile.ZipFile(result["archive_path"], "r") as archive:
            assert sorted(archive.namelist()) == [
                "pdf_images/demo-001.jpg",
                "pdf_images/demo-002.png",
            ]


def test_pdf_image_extract_service_returns_empty_when_no_images() -> None:
    PdfImageExtractService = _load_service_class()
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_base = Path(tmp_dir)
        pdf_path = workspace_base / "empty.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 empty")

        def fake_runner(command, **kwargs):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=(
                    "page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio\n"
                    "--------------------------------------------------------------------------------------------\n"
                ),
                stderr="",
            )

        service = PdfImageExtractService(
            workspace_base=workspace_base,
            command_runner=fake_runner,
        )

        result = service.extract_from_pdf(
            pdf_path,
            task_id="task-2",
            source_file_name="empty",
        )

        assert result["image_count"] == 0
        assert result["images"] == []
        assert result["archive_path"] is None
