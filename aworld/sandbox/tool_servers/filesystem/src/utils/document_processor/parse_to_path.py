"""薄封装：按 file_type 选 parser，解析并写入 output_path"""

import logging
from pathlib import Path

from .parsers.txt_parser import TxtParser
from .parsers.md_parser import MdParser
from .parsers.csv_parser import CsvParser
from .parsers.excel_parser import ExcelParser
from .parsers.word_parser import WordParser
from .parsers.ppt_parser import PptParser
from .parsers.pdf_parser import PdfParser

logger = logging.getLogger(__name__)

_PARSERS = [
    TxtParser(),
    MdParser(),
    CsvParser(),
    ExcelParser(),
    WordParser(),
    PptParser(),
    PdfParser(),
]


def _get_parser(file_type: str):
    ft = file_type.lower().strip()
    for p in _PARSERS:
        if p.can_handle(ft):
            return p
    return None


async def parse_to_path(
    file_path: str | Path,
    output_path: str | Path,
    file_type: str,
) -> str:
    """解析 file_path 对应文件，将结果写入 output_path。返回写入的绝对路径。"""
    file_path = Path(file_path)
    output_path = Path(output_path)
    parser = _get_parser(file_type)
    if not parser:
        raise ValueError(
            f"不支持的文件类型: {file_type}。支持: pdf, txt, md, doc, docx, xlsx, xls, csv, ppt, pptx"
        )
    result = await parser.parse(
        file_path,
        task_id="",
        source_file_name=file_path.stem,
        output_path=output_path,
    )
    out = result.get("file_path")
    if out is None:
        raise RuntimeError("解析未返回 file_path")
    return str(Path(out).resolve())

