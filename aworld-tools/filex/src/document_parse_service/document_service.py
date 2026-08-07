"""
按文档类型组织的顶层服务抽象。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path
    from services.afts_service import AftsService


class DocumentService(Protocol):
    """面向文件类型的文档服务协议。"""

    async def parse_to_markdown(
        self,
        file_path: "Path",
        task_id: str,
        source_file_name: str,
        afts_service: "AftsService | None" = None,
    ) -> "Path":
        """解析文档并落盘为 Markdown。"""
