"""PDF 解析器占位：本地 parse_file 暂不支持 PDF（原实现依赖远程 Layotto/Maya）。"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from ..base_parser import BaseParser

logger = logging.getLogger(__name__)


class PdfParser(BaseParser):
    def get_supported_types(self) -> list[str]:
        return ["pdf"]

    def can_handle(self, file_type: str) -> bool:
        return file_type.lower() == "pdf"

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
        raise NotImplementedError(
            "PDF 解析在本地 parse_file 中暂不支持；原实现依赖远程 Layotto/Maya 服务。"
        )

