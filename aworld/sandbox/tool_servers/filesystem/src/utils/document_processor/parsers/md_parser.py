"""Markdown 解析器"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from ..base_parser import BaseParser

logger = logging.getLogger(__name__)


class MdParser(BaseParser):
    def get_supported_types(self) -> list[str]:
        return ["md"]

    def can_handle(self, file_type: str) -> bool:
        return file_type.lower() in ["md", "markdown"]

    async def parse(
        self,
        file_path: Path,
        task_id: str = "",
        source_file_id: Optional[str] = None,
        source_file_name: Optional[str] = None,
        afts_service: Any = None,
        output_path: Optional[Path] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        content = await self._parse_md_sync(file_path)
        source_name = source_file_name or file_path.stem
        parsed_file_path = await self._save_markdown_to_file(
            content, task_id, source_name, output_path=output_path
        )
        return {"file_path": parsed_file_path}

    async def _parse_md_sync(self, file_path: Path) -> str:
        for enc in ["utf-8", "utf-8-sig", "gbk", "latin-1"]:
            try:
                return file_path.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        raise RuntimeError("无法使用任何编码读取文件")

