"""Document services for text files.

This module provides the shared parsing pipeline for TXT and Markdown files
without embedded assets.
"""

from __future__ import annotations

import logging
from pathlib import Path

try:
    import chardet
except ImportError:
    chardet = None

from .simple_document_service import SimpleDocumentService

logger = logging.getLogger(__name__)


class TxtContentExtractor:
    """Extract content from TXT files."""

    async def extract_content(self, file_path: Path) -> tuple[str, dict[str, str]]:
        if chardet is None:
            raise RuntimeError("未安装chardet。请安装: pip install chardet")

        try:
            encoding = self._detect_encoding(file_path)
            fallback_encodings = [encoding, "utf-8", "gbk", "latin-1", "cp1252"]
            content = None
            used_encoding = None

            for candidate in fallback_encodings:
                try:
                    content = file_path.read_text(encoding=candidate, errors="replace")
                    used_encoding = candidate
                    break
                except (UnicodeDecodeError, LookupError):
                    continue

            if content is None:
                raise RuntimeError("无法使用任何编码读取文件")
            return content, {"encoding": used_encoding or "unknown"}
        except BaseException as exc:
            logger.warning(
                "txt_content_extractor.extract_content failed | file_path=%s error=%s",
                file_path,
                exc,
                exc_info=True,
            )
            raise RuntimeError(f"提取TXT内容失败: {exc}") from exc

    def _detect_encoding(self, file_path: Path) -> str:
        try:
            raw_data = file_path.read_bytes()
            if raw_data.startswith(b"\xef\xbb\xbf"):
                return "utf-8-sig"
            if raw_data.startswith(b"\xff\xfe"):
                return "utf-16-le"
            if raw_data.startswith(b"\xfe\xff"):
                return "utf-16-be"

            result = chardet.detect(raw_data)
            encoding = result.get("encoding", "utf-8")
            confidence = result.get("confidence", 0)
            return encoding if confidence > 0.7 else "utf-8"
        except BaseException:
            logger.warning(
                "txt_content_extractor._detect_encoding failed, using utf-8 | file_path=%s",
                file_path,
                exc_info=True,
            )
            return "utf-8"


class MarkdownContentExtractor:
    """Extract content from Markdown files."""

    async def extract_content(self, file_path: Path) -> tuple[str, dict[str, str]]:
        try:
            encodings = ["utf-8", "utf-8-sig", "gbk", "latin-1"]
            content = None
            used_encoding = None

            for encoding in encodings:
                try:
                    content = file_path.read_text(encoding=encoding)
                    used_encoding = encoding
                    break
                except (UnicodeDecodeError, LookupError):
                    continue

            if content is None:
                raise RuntimeError("无法使用任何编码读取文件")
            return content, {"encoding": used_encoding or "unknown"}
        except BaseException as exc:
            logger.warning(
                "markdown_content_extractor.extract_content failed | file_path=%s error=%s",
                file_path,
                exc,
                exc_info=True,
            )
            raise RuntimeError(f"提取Markdown内容失败: {exc}") from exc


class TxtDocumentService(SimpleDocumentService):
    """Top-level document service for TXT files."""

    def __init__(self) -> None:
        super().__init__(file_type="txt", content_extractor=TxtContentExtractor())


class MarkdownDocumentService(SimpleDocumentService):
    """Top-level document service for Markdown files."""

    def __init__(self) -> None:
        super().__init__(file_type="md", content_extractor=MarkdownContentExtractor())
