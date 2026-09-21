"""Shared workspace paths for document parsing."""

import os
from pathlib import Path


FILEX_WORKSPACE_ROOT_ENV = "FILEX_WORKSPACE_ROOT"


def get_workspace_root() -> Path:
    """Resolve the process-scoped FileX workspace root.

    Benchmark runners set the environment before FileX is imported in its
    subprocess.  The legacy constants below therefore remain compatible while
    isolated executions no longer write to ``~/workspace``.
    """

    configured = str(os.getenv(FILEX_WORKSPACE_ROOT_ENV) or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{FILEX_WORKSPACE_ROOT_ENV} must be an absolute path")
        return path.resolve()
    return (Path.home() / "workspace").resolve()


def get_document_parse_workspace() -> Path:
    return get_workspace_root() / "document_parse"


FS_WORKSPACE_ROOT = get_workspace_root()
DOCUMENT_PARSE_WORKSPACE = get_document_parse_workspace()
