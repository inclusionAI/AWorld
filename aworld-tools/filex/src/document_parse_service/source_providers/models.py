"""Shared source-provider result models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceResolution:
    """A remotely identified source prepared for an existing parse provider."""

    local_path: Path
    file_type: str
    manifest: dict[str, Any]
