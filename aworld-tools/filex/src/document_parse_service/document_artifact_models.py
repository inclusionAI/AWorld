"""
文档产物统一数据模型。

用于承接后续的“内容提取 -> 资源发布 -> Markdown 组装 -> 落盘”抽象，
当前仅提供结构定义，不承载旧链路迁移逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


AssetKind = Literal["embedded_image", "figure_crop", "page_screenshot"]


@dataclass(slots=True)
class DocumentAnchor:
    """文档内资源锚点。"""

    page_number: int = 0
    top: int = 0
    left: int = 0
    before_snippets: list[str] = field(default_factory=list)
    after_snippets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DocumentAsset:
    """文档资源定义。"""

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
    """统一的 Markdown 产物。"""

    markdown_text: str
    assets: list[DocumentAsset] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
