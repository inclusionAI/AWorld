"""TXT 解析器"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import chardet
except ImportError:
    chardet = None

from ..base_parser import BaseParser

logger = logging.getLogger(__name__)


class TxtParser(BaseParser):
    def get_supported_types(self) -> list[str]:
        return ["txt"]

    def can_handle(self, file_type: str) -> bool:
        return file_type.lower() == "txt"

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
        if chardet is None:
            raise RuntimeError("未安装 chardet。请安装: pip install chardet")
        markdown_content = await self._parse_txt_sync(file_path)
        source_name = source_file_name or file_path.stem
        parsed_file_path = await self._save_markdown_to_file(
            markdown_content, task_id, source_name, output_path=output_path
        )
        return {"file_path": parsed_file_path}

    async def _parse_txt_sync(self, file_path: Path) -> str:
        encoding = await self._detect_encoding(file_path)
        for enc in [encoding, "utf-8", "gbk", "latin-1", "cp1252"]:
            try:
                return file_path.read_text(encoding=enc, errors="replace")
            except (UnicodeDecodeError, LookupError):
                continue
        raise RuntimeError("无法使用任何编码读取文件")

    async def _detect_encoding(self, file_path: Path) -> str:
        try:
            raw = file_path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                return "utf-8-sig"
            if raw.startswith(b"\xff\xfe"):
                return "utf-16-le"
            if raw.startswith(b"\xfe\xff"):
                return "utf-16-be"
            r = chardet.detect(raw)
            enc = r.get("encoding", "utf-8")
            return enc if (r.get("confidence", 0) > 0.7) else "utf-8"
        except Exception:
            return "utf-8"

