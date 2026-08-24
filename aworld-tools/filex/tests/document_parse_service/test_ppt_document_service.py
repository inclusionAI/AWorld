import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys


SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from document_parse_service.document_artifact_models import DocumentAsset
from document_parse_service.ppt_document_service import (
    DEFAULT_PPTX_PARSE_PROVIDER,
    LiteParseProvider,
    PptDocumentService,
    PythonPptxProvider,
)


def test_default_pptx_provider_is_python_pptx() -> None:
    service = PptDocumentService(asset_reference_mode="local_path")

    provider = service._resolve_text_provider()

    assert DEFAULT_PPTX_PARSE_PROVIDER == "python_pptx"
    assert isinstance(provider, PythonPptxProvider)
    assert provider.name == "python_pptx"


def test_explicit_liteparse_provider_keeps_canonical_name() -> None:
    service = PptDocumentService(
        env_content={"pptx_parse_provider": "liteparse"},
        asset_reference_mode="local_path",
    )

    provider = service._resolve_text_provider()

    assert isinstance(provider, LiteParseProvider)
    assert provider.name == "liteparse"


def test_liteparse_provider_disables_ocr_by_default(monkeypatch) -> None:
    captured = {}

    def _run(command, **_kwargs):
        captured["command"] = command
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text("slide text", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "document_parse_service.ppt_document_service.shutil.which",
        lambda _candidate: "/usr/local/bin/liteparse",
    )
    monkeypatch.setattr(
        "document_parse_service.ppt_document_service.subprocess.run",
        _run,
    )

    result = asyncio.run(LiteParseProvider().extract_markdown(Path("demo.pptx")))

    assert result == "slide text"
    assert "--no-ocr" in captured["command"]


def test_unknown_pptx_provider_is_rejected() -> None:
    service = PptDocumentService(
        env_content={"pptx_parse_provider": "liteparse_cli"},
        asset_reference_mode="local_path",
    )

    try:
        service._resolve_text_provider()
    except ValueError as exc:
        assert "liteparse_cli" in str(exc)
        assert "python_pptx, liteparse" in str(exc)
    else:
        raise AssertionError("expected invalid PPTX provider to be rejected")


def test_python_pptx_provider_preserves_slide_order_and_tables(monkeypatch) -> None:
    class _Cell:
        def __init__(self, text):
            self.text = text

    class _Row:
        def __init__(self, *values):
            self.cells = [_Cell(value) for value in values]

    class _Table:
        rows = [_Row("Metric", "Value"), _Row("ARR", "128|M")]

    class _Shape:
        def __init__(self, text="", table=None):
            self.text = text
            self.has_table = table is not None
            self.table = table

    class _Slide:
        def __init__(self, *shapes):
            self.shapes = shapes

    class _Presentation:
        slides = [
            _Slide(_Shape("First slide")),
            _Slide(_Shape("Second slide"), _Shape(table=_Table())),
        ]

    monkeypatch.setattr(
        "document_parse_service.ppt_document_service._load_presentation",
        lambda _path: _Presentation(),
    )

    markdown = asyncio.run(PythonPptxProvider().extract_markdown(Path("demo.pptx")))

    assert markdown.index("## 幻灯片 1") < markdown.index("## 幻灯片 2")
    assert "First slide" in markdown
    assert "| Metric | Value |" in markdown
    assert "| ARR | 128\\|M |" in markdown


def test_extract_embedded_images_records_slide_number(monkeypatch, tmp_path) -> None:
    class _Image:
        blob = b"png-bytes"
        content_type = "image/png"
        ext = "png"

    class _Picture:
        image = _Image()

    class _Slide:
        shapes = [_Picture()]

    class _Presentation:
        slides = [_Slide()]

    monkeypatch.setattr(
        "document_parse_service.ppt_document_service._load_presentation",
        lambda _path: _Presentation(),
    )
    monkeypatch.setattr(
        "document_parse_service.ppt_document_service._is_picture_shape",
        lambda _shape: True,
    )

    assets = PptDocumentService._extract_image_assets(
        Path("demo.pptx"),
        tmp_path,
    )

    assert len(assets) == 1
    assert assets[0].kind == "embedded_image"
    assert assets[0].page_number == 1
    assert assets[0].local_path is not None
    assert assets[0].local_path.read_bytes() == b"png-bytes"


def test_extract_embedded_images_skips_non_image_part(monkeypatch, tmp_path, caplog) -> None:
    class _BrokenPicture:
        name = "Unsupported linked picture"

        @property
        def image(self):
            raise AttributeError("'Part' object has no attribute 'image'")

    class _Slide:
        shapes = [_BrokenPicture()]

    class _Presentation:
        slides = [_Slide()]

    monkeypatch.setattr(
        "document_parse_service.ppt_document_service._load_presentation",
        lambda _path: _Presentation(),
    )
    monkeypatch.setattr(
        "document_parse_service.ppt_document_service._is_picture_shape",
        lambda _shape: True,
    )

    assets = PptDocumentService._extract_image_assets(Path("demo.pptx"), tmp_path)

    assert assets == []
    assert "skip unsupported picture part" in caplog.text
    assert "slide_number=1" in caplog.text


def test_extract_embedded_webp_does_not_read_unsupported_python_pptx_ext(
    monkeypatch,
    tmp_path,
) -> None:
    class _WebpImage:
        blob = b"RIFF\x00\x00\x00\x00WEBPwebp-bytes"

        @property
        def content_type(self):
            raise ValueError("python-pptx derives content type from ext")

        @property
        def ext(self):
            raise ValueError("python-pptx does not map WEBP to an extension")

    class _Picture:
        image = _WebpImage()

    class _Slide:
        shapes = [_Picture()]

    class _Presentation:
        slides = [_Slide()]

    monkeypatch.setattr(
        "document_parse_service.ppt_document_service._load_presentation",
        lambda _path: _Presentation(),
    )
    monkeypatch.setattr(
        "document_parse_service.ppt_document_service._is_picture_shape",
        lambda _shape: True,
    )

    assets = PptDocumentService._extract_image_assets(Path("demo.pptx"), tmp_path)

    assert len(assets) == 1
    assert assets[0].local_path is not None
    assert assets[0].local_path.suffix == ".webp"
    assert assets[0].local_path.read_bytes() == b"RIFF\x00\x00\x00\x00WEBPwebp-bytes"


def test_remote_mode_requires_published_image_ids() -> None:
    service = PptDocumentService()

    try:
        service._validate_published_assets(
            [
                DocumentAsset(
                    asset_id="pptx_image_1",
                    kind="embedded_image",
                    local_path=Path("/tmp/image.png"),
                )
            ]
        )
    except RuntimeError as exc:
        assert "pptx_image_1" in str(exc)
    else:
        raise AssertionError("expected missing remote image id to fail")
