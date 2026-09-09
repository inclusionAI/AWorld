import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from document_parse_service.asset_reference import prepare_markdown_asset_references
from document_parse_service.document_artifact_models import DocumentAsset
from document_parse_service.word_document_service import WordDocumentService


def test_validate_published_assets_includes_local_asset_directory() -> None:
    service = WordDocumentService()
    local_path = Path("/tmp/doc_parse_assets/t2_images/t2_img_0.png")

    try:
        service._validate_published_assets(
            [
                DocumentAsset(
                    asset_id="word_image_1",
                    kind="embedded_image",
                    remote_id="",
                    local_path=local_path,
                    order=1,
                )
            ]
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "word_image_1" in message
        assert str(local_path.parent) in message
    else:
        raise AssertionError("expected RuntimeError when DOCX assets are missing remote_id")


def test_local_path_mode_allows_missing_remote_id_and_sets_markdown_path() -> None:
    service = WordDocumentService(asset_reference_mode="local_path")
    output_dir = Path("/tmp/doc_parse_assets")
    local_path = output_dir / "t2_images" / "t2_img_0.png"
    asset = DocumentAsset(
        asset_id="word_image_1",
        kind="embedded_image",
        remote_id="",
        local_path=local_path,
        order=1,
    )

    prepare_markdown_asset_references(
        [asset],
        output_dir=output_dir,
        asset_reference_mode="local_path",
    )
    service._validate_published_assets([asset])

    assert asset.meta["markdown_path"] == "t2_images/t2_img_0.png"
