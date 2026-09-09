"""Data models for extracted assets and Markdown artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

AssetKind = Literal[
    "embedded_image",
    "figure_crop",
    "page_screenshot",
    "video_keyframe",
    "video_storyboard",
]


@dataclass(slots=True)
class DocumentAnchor:
    """Location hints for an asset inside a document."""

    page_number: int = 0
    top: int = 0
    left: int = 0
    before_snippets: list[str] = field(default_factory=list)
    after_snippets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DocumentAsset:
    """Extracted document asset."""

    asset_id: str
    kind: AssetKind
    remote_id: str = ""
    local_path: Path | None = None
    page_number: int = 0
    order: int = 0
    anchor: DocumentAnchor = field(default_factory=DocumentAnchor)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MarkdownArtifact:
    """Normalized Markdown artifact."""

    markdown_text: str
    assets: list[DocumentAsset] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    document_ir: dict[str, Any] | None = None
