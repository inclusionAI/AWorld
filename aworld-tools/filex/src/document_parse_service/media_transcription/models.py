"""Typed models for media transcription backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TranscriptSegment:
    """A single timed transcript segment."""

    text: str
    start: float | None = None
    end: float | None = None
    speaker: str = ""


@dataclass(slots=True)
class TranscriptResult:
    """Normalized result returned by every media backend."""

    text: str
    backend: str
    model: str = ""
    language: str = ""
    duration: float | None = None
    segments: list[TranscriptSegment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
