from __future__ import annotations

from pathlib import Path

from document_parse_service.paths import (
    DOCUMENT_PARSE_WORKSPACE,
    FS_WORKSPACE_ROOT,
    get_document_parse_workspace,
    get_workspace_root,
)


def test_filex_uses_mounted_workspace_root() -> None:
    assert FS_WORKSPACE_ROOT == Path.home() / "workspace"
    assert DOCUMENT_PARSE_WORKSPACE == FS_WORKSPACE_ROOT / "document_parse"


def test_filex_workspace_root_can_be_isolated_by_environment(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "isolated-workspace"
    monkeypatch.setenv("FILEX_WORKSPACE_ROOT", str(workspace))

    assert get_workspace_root() == workspace.resolve()
    assert get_document_parse_workspace() == workspace.resolve() / "document_parse"
