import asyncio
import sys
from pathlib import Path

import pytest


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_parse_requires_workspace_path() -> None:
    _add_src_path()
    from document_parse_service.service import DocumentParseService

    with pytest.raises(ValueError, match="workspace_path is required"):
        asyncio.run(DocumentParseService().parse())
