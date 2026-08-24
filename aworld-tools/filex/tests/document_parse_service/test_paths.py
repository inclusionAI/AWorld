from pathlib import Path

from document_parse_service.paths import DOCUMENT_PARSE_WORKSPACE, FS_WORKSPACE_ROOT


def test_filex_uses_mounted_workspace_root() -> None:
    assert FS_WORKSPACE_ROOT == Path.home() / "workspace"
    assert DOCUMENT_PARSE_WORKSPACE == FS_WORKSPACE_ROOT / "document_parse"
