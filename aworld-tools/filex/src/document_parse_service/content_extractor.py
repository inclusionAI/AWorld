"""Content extraction protocol shared by parsing engines."""

from __future__ import annotations

from typing import Any, Protocol


class ContentExtractor(Protocol):
    """Protocol for extracting document content."""

    async def extract_content(self, file_path: Any) -> tuple[str, Any]:
        """Return normalized text and the raw parser result."""
