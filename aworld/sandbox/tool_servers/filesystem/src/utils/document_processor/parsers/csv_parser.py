"""CSV 解析器"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import pandas as pd
    import chardet
except ImportError:
    pd = None
    chardet = None

from ..base_parser import BaseParser

logger = logging.getLogger(__name__)


class CsvParser(BaseParser):
    def get_supported_types(self) -> list[str]:
        return ["csv"]

    def can_handle(self, file_type: str) -> bool:
        return file_type.lower() == "csv"

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
        if pd is None or chardet is None:
            raise RuntimeError("未安装 pandas 或 chardet。请安装: pip install pandas chardet")
        markdown_content = await self._parse_csv_sync(file_path)
        source_name = source_file_name or file_path.stem
        parsed_file_path = await self._save_markdown_to_file(
            markdown_content, task_id, source_name, output_path=output_path
        )
        return {"file_path": parsed_file_path}

    async def _parse_csv_sync(self, file_path: Path) -> str:
        encoding = await self._detect_encoding(file_path)
        delimiter = await self._detect_delimiter(file_path, encoding)
        df = pd.read_csv(file_path, encoding=encoding, delimiter=delimiter, low_memory=False)
        try:
            return df.to_markdown(index=False, tablefmt="github")
        except Exception:
            return df.to_string()

    async def _detect_encoding(self, file_path: Path) -> str:
        try:
            raw = file_path.read_bytes()[:10000]
            r = chardet.detect(raw)
            return r.get("encoding", "utf-8") if (r.get("confidence", 0) > 0.7) else "utf-8"
        except Exception:
            return "utf-8"

    async def _detect_delimiter(self, file_path: Path, encoding: str) -> str:
        try:
            sample = file_path.read_text(encoding=encoding)[:1024]
            for d in [",", ";", "\t", "|", ":"]:
                if d in sample:
                    return d
        except Exception:
            pass
        return ","

