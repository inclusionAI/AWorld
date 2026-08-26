import asyncio
import importlib.util
import tempfile
from pathlib import Path
from types import ModuleType
import sys


def _load_document_parse_executor_module():
    services_dir = Path(__file__).resolve().parent.parent.parent / "src" / "document_parse_service"
    module_path = services_dir / "document_parse_executor.py"

    package_root = "aworld_test_executor_pkg"
    services_package = f"{package_root}.document_parse_service"
    module_name = f"{services_package}.document_parse_executor"

    root_module = ModuleType(package_root)
    root_module.__path__ = []  # type: ignore[attr-defined]
    services_module = ModuleType(services_package)
    services_module.__path__ = [str(services_dir)]  # type: ignore[attr-defined]
    document_service_stub = ModuleType(f"{services_package}.document_service")

    class _DocumentService:
        pass

    document_service_stub.DocumentService = _DocumentService

    original_modules = {
        name: sys.modules.get(name)
        for name in [
            package_root,
            services_package,
            f"{services_package}.document_service",
            module_name,
        ]
    }

    try:
        sys.modules[package_root] = root_module
        sys.modules[services_package] = services_module
        sys.modules[f"{services_package}.document_service"] = document_service_stub

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


class _StubDocumentService:
    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    async def parse_to_markdown(
        self,
        file_path: Path,
        task_id: str,
        source_file_name: str,
        afts_service=None,
    ) -> Path:
        output_dir = self._workspace_root / "document_parse" / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{source_file_name}.md"
        output_path.write_text("body", encoding="utf-8")
        return output_path


class _StubAftsService:
    async def upload_file(
        self,
        *,
        file_path: Path,
        file_name: str,
        setpublic: bool,
        update_alias: bool,
    ) -> str:
        return f"uploaded-{file_name}"


def test_document_parse_executor_sync_parse_uploads_and_returns_relative_path() -> None:
    module = _load_document_parse_executor_module()

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.txt"
        file_path.write_text("demo", encoding="utf-8")

        result = asyncio.run(
                module.DocumentParseExecutor(
                    _StubDocumentService(module.FS_WORKSPACE_ROOT)
                ).sync_parse(
                file_path=file_path,
                task_id="task-1",
                source_file_id="file-1",
                source_file_name="demo",
                afts_service=_StubAftsService(),
            )
        )

        assert result["task_id"] == "task-1"
        assert result["source_file_id"] == "file-1"
        assert result["output_file_id"] == "uploaded-demo.md"
        assert result["file_path"] == "document_parse/task-1/demo.md"


def test_document_parse_executor_async_parse_background_returns_immediately() -> None:
    module = _load_document_parse_executor_module()

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "demo.txt"
        file_path.write_text("demo", encoding="utf-8")

        result = asyncio.run(
                module.DocumentParseExecutor(
                    _StubDocumentService(module.FS_WORKSPACE_ROOT)
                ).async_parse(
                file_path=file_path,
                task_id="task-2",
                source_file_id="file-2",
                source_file_name="demo",
                afts_service=_StubAftsService(),
                run_in_background=True,
            )
        )

        assert result == {
            "task_id": "task-2",
            "source_file_id": "file-2",
            "output_file_id": None,
            "file_path": "",
        }
