"""
parse_file 专用：按文件头 magic 校验文件类型（不依赖扩展名）
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def verify_file_type(file_path: Path, expected_type: str) -> bool:
    """校验文件类型是否与预期一致（读前 16 字节）。

    expected_type: pdf, docx, doc, xlsx, xls, pptx, ppt, csv, txt, md 等。
    对无 magic 的类型（csv/txt/md）直接返回 True。
    """
    if not file_path.exists():
        logger.debug("verify_file_type: file not exists path=%s", file_path)
        return False
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)
        if len(header) == 0:
            return False
        signatures = {
            "pdf": [b"%PDF"],
            "docx": [b"PK\x03\x04"],
            "doc": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
            "xlsx": [b"PK\x03\x04"],
            "xls": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
            "pptx": [b"PK\x03\x04"],
            "ppt": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
            "csv": None,
            "txt": None,
            "md": None,
            "markdown": None,
        }
        expected_signatures = signatures.get(expected_type.lower())
        if expected_signatures is None or None in (expected_signatures or [None]):
            return True
        for sig in expected_signatures:
            if header.startswith(sig):
                return True
        return False
    except Exception as e:
        logger.warning("verify_file_type exception path=%s expected=%s error=%s", file_path, expected_type, e)
        return False

