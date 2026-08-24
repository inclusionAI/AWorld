"""Local media transcription backend.

The backend uses faster-whisper when it is installed in the runtime. It is kept
as an optional dependency so filex can ship the interface without forcing every
filesystem-server image to carry a local ASR model.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .models import TranscriptResult, TranscriptSegment


class LocalMediaTranscriptionBackend:
    """Transcribe media on the local machine with faster-whisper."""

    name = "local"

    async def transcribe(
        self,
        file_path: Path,
        *,
        media_type: str,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
    ) -> TranscriptResult:
        return await asyncio.to_thread(
            self._transcribe_sync,
            file_path,
            media_type=media_type,
            file_type=file_type,
            source_file_name=source_file_name,
            options=options,
        )

    def _transcribe_sync(
        self,
        file_path: Path,
        *,
        media_type: str,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
    ) -> TranscriptResult:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "local media backend requires faster-whisper. "
                "Install faster-whisper or use media_parse_backend='openai_compatible'."
            ) from exc

        model_name = str(
            options.get("model")
            or os.getenv("FILEX_LOCAL_MEDIA_MODEL")
            or os.getenv("FILEX_LOCAL_WHISPER_MODEL")
            or "base"
        )
        device = str(options.get("device") or os.getenv("FILEX_LOCAL_MEDIA_DEVICE") or "auto")
        compute_type = str(
            options.get("compute_type")
            or os.getenv("FILEX_LOCAL_MEDIA_COMPUTE_TYPE")
            or "default"
        )
        language = options.get("language")
        vad_filter = bool(options.get("vad_filter", True))
        initial_prompt = options.get("initial_prompt")

        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments_iter, info = model.transcribe(
            str(file_path),
            language=str(language) if language else None,
            vad_filter=vad_filter,
            initial_prompt=str(initial_prompt) if initial_prompt else None,
        )
        segments = [
            TranscriptSegment(
                text=str(segment.text).strip(),
                start=float(segment.start) if segment.start is not None else None,
                end=float(segment.end) if segment.end is not None else None,
            )
            for segment in segments_iter
            if str(segment.text).strip()
        ]
        text = "\n".join(segment.text for segment in segments).strip()

        return TranscriptResult(
            text=text,
            backend=self.name,
            model=model_name,
            language=str(getattr(info, "language", "") or language or ""),
            duration=float(getattr(info, "duration", 0.0) or 0.0) or None,
            segments=segments,
            metadata={
                "media_type": media_type,
                "file_type": file_type,
                "source_file_name": source_file_name,
                "device": device,
                "compute_type": compute_type,
                "language_probability": getattr(info, "language_probability", None),
            },
        )
