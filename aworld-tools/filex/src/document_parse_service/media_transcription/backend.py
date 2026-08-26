"""Backend protocol for media transcription and understanding."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .models import TranscriptResult


class MediaTranscriptionBackend(Protocol):
    """Protocol implemented by local and OpenAI-compatible media backends."""

    name: str

    async def transcribe(
        self,
        file_path: Path,
        *,
        media_type: str,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
    ) -> TranscriptResult:
        """Return a normalized text transcript or media description."""
