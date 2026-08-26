import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from document_parse_service.document_artifact_models import DocumentAnchor, DocumentAsset, MarkdownArtifact
from document_parse_service.markdown_assembler import AnchoredMarkdownAssembler, PlaceholderMarkdownAssembler


def test_placeholder_markdown_assembler_replaces_asset_placeholder() -> None:
    artifact = MarkdownArtifact(
        markdown_text="标题\n\n{{asset:word_image_1}}\n\n正文",
        assets=[
            DocumentAsset(
                asset_id="word_image_1",
                kind="embedded_image",
                remote_id="file-123",
                local_path=Path("/tmp/demo.png"),
                order=1,
                meta={"index": "1"},
            )
        ],
    )

    updated_markdown = PlaceholderMarkdownAssembler().assemble(artifact)

    assert "{{asset:word_image_1}}" not in updated_markdown
    assert '<img src="file-123" data-file-id="file-123" alt="图片 1" />' in updated_markdown


def test_placeholder_markdown_assembler_falls_back_to_local_markdown_path() -> None:
    artifact = MarkdownArtifact(
        markdown_text="标题\n\n{{asset:word_image_1}}\n\n正文",
        assets=[
            DocumentAsset(
                asset_id="word_image_1",
                kind="embedded_image",
                remote_id="",
                local_path=Path("/tmp/demo.png"),
                order=1,
                meta={"index": "1", "markdown_path": "t2_images/t2_img_0.png"},
            )
        ],
    )

    updated_markdown = PlaceholderMarkdownAssembler().assemble(artifact)

    assert "{{asset:word_image_1}}" not in updated_markdown
    assert "![图片 1](t2_images/t2_img_0.png)" in updated_markdown


def test_placeholder_markdown_assembler_prefers_remote_url_over_file_id() -> None:
    artifact = MarkdownArtifact(
        markdown_text="标题\n\n{{asset:word_image_1}}\n\n正文",
        assets=[
            DocumentAsset(
                asset_id="word_image_1",
                kind="embedded_image",
                remote_id="file-123",
                local_path=Path("/tmp/demo.png"),
                order=1,
                meta={"index": "1", "remote_url": "https://mdn.example/file-123.png"},
            )
        ],
    )

    updated_markdown = PlaceholderMarkdownAssembler().assemble(artifact)

    assert '<img src="https://mdn.example/file-123.png" data-file-id="file-123" alt="图片 1" />' in updated_markdown
    assert "![图片 1](file-123)" not in updated_markdown
    assert artifact.assets[0].remote_id == "file-123"


def test_anchored_markdown_assembler_uses_local_markdown_path() -> None:
    artifact = MarkdownArtifact(
        markdown_text="### 第 1 页\n\n第一行内容\n第二行内容",
        assets=[
            DocumentAsset(
                asset_id="pdf_image_1",
                kind="embedded_image",
                local_path=Path("/tmp/page-1.png"),
                page_number=1,
                order=1,
                anchor=DocumentAnchor(
                    page_number=1,
                    before_snippets=["第一行内容"],
                ),
                meta={"index": "1", "markdown_path": "pdf_layout/page-1.png"},
            )
        ],
    )

    updated_markdown = AnchoredMarkdownAssembler().assemble(artifact)

    assert "![图片 1](pdf_layout/page-1.png)" in updated_markdown
    assert updated_markdown.index("第一行内容") < updated_markdown.index("![图片 1]")


def test_anchored_markdown_assembler_does_not_duplicate_existing_html_image() -> None:
    artifact = MarkdownArtifact(
        markdown_text='<div><img src="pdf_layout/page-1.png" alt="Image" /></div>',
        assets=[
            DocumentAsset(
                asset_id="pdf_image_1",
                kind="embedded_image",
                local_path=Path("/tmp/page-1.png"),
                page_number=1,
                order=1,
                meta={"index": "1", "markdown_path": "pdf_layout/page-1.png"},
            )
        ],
    )

    updated_markdown = AnchoredMarkdownAssembler().assemble(artifact)

    assert updated_markdown.count("pdf_layout/page-1.png") == 1
    assert "## 提取图片" not in updated_markdown


def test_anchored_markdown_assembler_prefers_remote_url_over_file_id() -> None:
    artifact = MarkdownArtifact(
        markdown_text="### 第 1 页\n\n第一行内容\n第二行内容",
        assets=[
            DocumentAsset(
                asset_id="pdf_image_1",
                kind="embedded_image",
                remote_id="file-123",
                local_path=Path("/tmp/page-1.png"),
                page_number=1,
                order=1,
                anchor=DocumentAnchor(
                    page_number=1,
                    before_snippets=["第一行内容"],
                ),
                meta={"index": "1", "remote_url": "https://mdn.example/file-123.png"},
            )
        ],
    )

    updated_markdown = AnchoredMarkdownAssembler().assemble(artifact)

    assert '<img src="https://mdn.example/file-123.png" data-file-id="file-123" alt="图片 1" />' in updated_markdown
    assert "![图片 1](file-123)" not in updated_markdown
    assert artifact.assets[0].remote_id == "file-123"
