"""文档解析器基类（parse_file 用：无远程依赖，支持 output_path）"""

import logging
from abc import abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from .base_handler import BaseHandler

logger = logging.getLogger(__name__)


class BaseParser(BaseHandler):
    """解析器基类：parse(file_path, output_path=None, **kwargs) -> Dict with file_path"""

    def get_handler_type(self) -> str:
        return "parser"

    @abstractmethod
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
        """解析文件。当 output_path 传入时，结果写入该路径并返回。"""
        pass

    async def _save_markdown_to_file(
        self,
        markdown_content: str,
        task_id: str,
        source_file_name: str,
        output_path: Optional[Path] = None,
    ) -> Path:
        """将 Markdown 写入文件。

        若提供 output_path 则写入该路径；否则按 home/aworld_workspace/task_id/source_file_name.md 构建路径（仅兼容用）。
        """
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown_content, encoding="utf-8")
            return output_path

        workspace = Path.home() / "aworld_workspace"
        task_dir = workspace / (task_id or "out")
        task_dir.mkdir(parents=True, exist_ok=True)
        out = task_dir / f"{source_file_name or 'out'}.md"
        out.write_text(markdown_content, encoding="utf-8")
        return out

