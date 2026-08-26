import importlib.util
import sys
import tempfile
from pathlib import Path
from types import ModuleType


def _load_publisher_module():
    services_dir = Path(__file__).resolve().parent.parent.parent / "src" / "document_parse_service"
    models_path = services_dir / "document_artifact_models.py"
    publisher_path = services_dir / "document_asset_publisher.py"

    package_root = "aworld_test_asset_pkg"
    services_package = f"{package_root}.document_parse_service"
    models_module_name = f"{services_package}.document_artifact_models"
    publisher_module_name = f"{services_package}.document_asset_publisher"

    root_module = ModuleType(package_root)
    root_module.__path__ = []  # type: ignore[attr-defined]
    services_module = ModuleType(services_package)
    services_module.__path__ = [str(services_dir)]  # type: ignore[attr-defined]

    original_modules = {
        name: sys.modules.get(name)
        for name in [
            package_root,
            services_package,
            models_module_name,
            publisher_module_name,
        ]
    }

    try:
        sys.modules[package_root] = root_module
        sys.modules[services_package] = services_module

        models_spec = importlib.util.spec_from_file_location(models_module_name, models_path)
        models_module = importlib.util.module_from_spec(models_spec)
        assert models_spec is not None and models_spec.loader is not None
        sys.modules[models_module_name] = models_module
        models_spec.loader.exec_module(models_module)

        publisher_spec = importlib.util.spec_from_file_location(publisher_module_name, publisher_path)
        publisher_module = importlib.util.module_from_spec(publisher_spec)
        assert publisher_spec is not None and publisher_spec.loader is not None
        sys.modules[publisher_module_name] = publisher_module
        publisher_spec.loader.exec_module(publisher_module)
        return models_module, publisher_module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_afts_document_asset_publisher_uploads_and_sets_remote_id() -> None:
    models_module, publisher_module = _load_publisher_module()

    with tempfile.TemporaryDirectory() as tmp_dir:
        image_path = Path(tmp_dir) / "figure.png"
        image_path.write_bytes(b"png")

        uploaded_paths: list[Path] = []

        class FakeAftsService:
            async def upload_file(self, file_path, file_name=None, setpublic=True, update_alias=True):
                uploaded_paths.append(file_path)
                return "file-123"

            async def get_file_url(self, file_id):
                assert file_id == "file-123"
                return "https://mdn.example/file-123"

        asset = models_module.DocumentAsset(
            asset_id="asset-1",
            kind="figure_crop",
            local_path=image_path,
        )
        publisher = publisher_module.AftsDocumentAssetPublisher(FakeAftsService())

        published_assets = __import__("asyncio").run(publisher.publish_assets([asset]))

        assert len(published_assets) == 1
        assert published_assets[0].remote_id == "file-123"
        assert published_assets[0].meta["remote_url"] == "https://mdn.example/file-123"
        assert published_assets[0].meta["markdown_path"] == "https://mdn.example/file-123"
        assert uploaded_paths == [image_path]


def test_afts_document_asset_publisher_skips_missing_local_file() -> None:
    models_module, publisher_module = _load_publisher_module()

    class FakeAftsService:
        async def upload_file(self, file_path, file_name=None, setpublic=True, update_alias=True):
            raise AssertionError("upload_file should not be called")

    missing_asset = models_module.DocumentAsset(
        asset_id="asset-2",
        kind="embedded_image",
        local_path=Path("/tmp/not-found-image.png"),
    )
    publisher = publisher_module.AftsDocumentAssetPublisher(FakeAftsService())

    published_assets = __import__("asyncio").run(publisher.publish_assets([missing_asset]))

    assert published_assets == []
