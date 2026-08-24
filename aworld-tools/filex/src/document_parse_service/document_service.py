"""Top-level protocol implemented by file-type document services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path
    from services.afts_service import AftsService


class DocumentService(Protocol):
    """Protocol for a file-type-specific document service."""

    async def parse_to_markdown(
        self,
        file_path: "Path",
        task_id: str,
        source_file_name: str,
        afts_service: "AftsService | None" = None,
    ) -> "Path":
        """Parse a document and persist it as Markdown."""
