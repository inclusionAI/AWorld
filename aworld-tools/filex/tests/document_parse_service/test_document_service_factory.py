import importlib.util
import sys
from types import ModuleType
from pathlib import Path


def _load_document_service_factory_module():
    services_dir = Path(__file__).resolve().parent.parent.parent / "src" / "document_parse_service"
    module_path = services_dir / "document_service_factory.py"

    package_root = "aworld_test_factory_pkg"
    services_package = f"{package_root}.document_parse_service"
    module_name = f"{services_package}.document_service_factory"

    root_module = ModuleType(package_root)
    root_module.__path__ = []  # type: ignore[attr-defined]
    services_module = ModuleType(services_package)
    services_module.__path__ = [str(services_dir)]  # type: ignore[attr-defined]

    document_service_stub = ModuleType(f"{services_package}.document_service")
    pdf_service_stub = ModuleType(f"{services_package}.pdf_document_service")
    ppt_service_stub = ModuleType(f"{services_package}.ppt_document_service")
    tabular_service_stub = ModuleType(f"{services_package}.tabular_document_service")
    text_service_stub = ModuleType(f"{services_package}.text_document_service")
    word_service_stub = ModuleType(f"{services_package}.word_document_service")
    media_service_stub = ModuleType(f"{services_package}.media_document_service")
    media_file_types_stub = ModuleType(f"{services_package}.media_file_types")

    class _DocumentService:
        pass

    class _PdfDocumentService:
        def __init__(self, env_content=None, asset_reference_mode="remote_id"):
            self.env_content = env_content
            self.asset_reference_mode = asset_reference_mode

    class _PptDocumentService:
        def __init__(self, env_content=None, asset_reference_mode="remote_id"):
            self.env_content = env_content
            self.asset_reference_mode = asset_reference_mode

    class _WordDocumentService:
        def __init__(self, asset_reference_mode="remote_id"):
            self.asset_reference_mode = asset_reference_mode

    class _ExcelDocumentService:
        pass

    class _CsvDocumentService:
        pass

    class _TxtDocumentService:
        pass

    class _MarkdownDocumentService:
        pass

    class _AudioDocumentService:
        def __init__(self, file_type, env_content=None):
            self.file_type = file_type
            self.env_content = env_content

    class _VideoDocumentService:
        def __init__(self, file_type, env_content=None):
            self.file_type = file_type
            self.env_content = env_content

    class _ImageDocumentService:
        def __init__(self, file_type, env_content=None):
            self.file_type = file_type
            self.env_content = env_content

    document_service_stub.DocumentService = _DocumentService
    pdf_service_stub.PdfDocumentService = _PdfDocumentService
    ppt_service_stub.PptDocumentService = _PptDocumentService
    word_service_stub.WordDocumentService = _WordDocumentService
    tabular_service_stub.ExcelDocumentService = _ExcelDocumentService
    tabular_service_stub.CsvDocumentService = _CsvDocumentService
    text_service_stub.TxtDocumentService = _TxtDocumentService
    text_service_stub.MarkdownDocumentService = _MarkdownDocumentService
    media_service_stub.AudioDocumentService = _AudioDocumentService
    media_service_stub.VideoDocumentService = _VideoDocumentService
    media_service_stub.ImageDocumentService = _ImageDocumentService
    media_file_types_stub.AUDIO_FILE_TYPES = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"}
    media_file_types_stub.VIDEO_FILE_TYPES = {"mp4", "mov", "mkv", "webm", "avi", "m4v", "mpeg", "mpg"}
    media_file_types_stub.IMAGE_FILE_TYPES = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
    media_file_types_stub.MEDIA_FILE_TYPES = (
        media_file_types_stub.AUDIO_FILE_TYPES
        | media_file_types_stub.VIDEO_FILE_TYPES
        | media_file_types_stub.IMAGE_FILE_TYPES
    )

    sys.modules[package_root] = root_module
    sys.modules[services_package] = services_module
    sys.modules[f"{services_package}.document_service"] = document_service_stub
    sys.modules[f"{services_package}.pdf_document_service"] = pdf_service_stub
    sys.modules[f"{services_package}.ppt_document_service"] = ppt_service_stub
    sys.modules[f"{services_package}.tabular_document_service"] = tabular_service_stub
    sys.modules[f"{services_package}.text_document_service"] = text_service_stub
    sys.modules[f"{services_package}.word_document_service"] = word_service_stub
    sys.modules[f"{services_package}.media_document_service"] = media_service_stub
    sys.modules[f"{services_package}.media_file_types"] = media_file_types_stub
    root_module.document_parse_service = services_module
    services_module.document_service = document_service_stub
    services_module.pdf_document_service = pdf_service_stub
    services_module.ppt_document_service = ppt_service_stub
    services_module.tabular_document_service = tabular_service_stub
    services_module.text_document_service = text_service_stub
    services_module.word_document_service = word_service_stub
    services_module.media_document_service = media_service_stub
    services_module.media_file_types = media_file_types_stub

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_document_service_factory_routes_by_file_type() -> None:
    module = _load_document_service_factory_module()

    assert type(module.DocumentServiceFactory.create(file_type="pdf")).__name__ == "_PdfDocumentService"
    assert type(module.DocumentServiceFactory.create(file_type="pptx")).__name__ == "_PptDocumentService"
    assert type(module.DocumentServiceFactory.create(file_type="docx")).__name__ == "_WordDocumentService"
    assert type(module.DocumentServiceFactory.create(file_type="xlsx")).__name__ == "_ExcelDocumentService"
    assert type(module.DocumentServiceFactory.create(file_type="csv")).__name__ == "_CsvDocumentService"
    assert type(module.DocumentServiceFactory.create(file_type="txt")).__name__ == "_TxtDocumentService"
    assert type(module.DocumentServiceFactory.create(file_type="markdown")).__name__ == "_MarkdownDocumentService"
    assert type(module.DocumentServiceFactory.create(file_type="mp3")).__name__ == "_AudioDocumentService"
    assert type(module.DocumentServiceFactory.create(file_type="mp4")).__name__ == "_VideoDocumentService"
    assert type(module.DocumentServiceFactory.create(file_type="png")).__name__ == "_ImageDocumentService"


def test_document_service_factory_rejects_unknown_file_type() -> None:
    module = _load_document_service_factory_module()

    try:
        module.DocumentServiceFactory.create(file_type="unknown")
    except ValueError as exc:
        assert "Unsupported file type" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown file type")


def test_document_service_factory_passes_pdf_asset_reference_mode() -> None:
    module = _load_document_service_factory_module()

    service = module.DocumentServiceFactory.create(
        file_type="pdf",
        asset_reference_mode="local_path",
    )

    assert service.asset_reference_mode == "local_path"


def test_document_service_factory_passes_pptx_asset_reference_mode() -> None:
    module = _load_document_service_factory_module()

    service = module.DocumentServiceFactory.create(
        file_type="pptx",
        asset_reference_mode="local_path",
    )

    assert service.asset_reference_mode == "local_path"
