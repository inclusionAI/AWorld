"""
按解析引擎组织的正文抽取抽象。
"""

from __future__ import annotations

from typing import Any, Protocol


class ContentExtractor(Protocol):
    """正文内容抽取协议。"""

    async def extract_content(self, file_path: Any) -> tuple[str, Any]:
        """返回正文文本和原始解析结果。"""
