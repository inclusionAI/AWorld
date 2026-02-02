# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
File list index builder: filename and summary for agent to find files at each layer.
"""

import time
from typing import Any, Dict, List, Optional

from aworld.logs.util import logger

from aworld.core.context.amni.indexing.models import FileListEntry
from aworld.core.context.amni.retrieval.artifacts.file import DirArtifact

# Max chars for summary (first line or prefix of content). Override via env FREEDOM_SPACE_FILE_LIST_SUMMARY_MAX_CHARS.
SUMMARY_MAX_CHARS = 256


def _get_file_content(attachment: Any) -> Optional[str]:
    """Get text content from attachment."""
    if attachment is None:
        return None
    content = getattr(attachment, "content", None)
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8", errors="replace")
        except Exception:
            return None
    return str(content)


def _make_summary(filename: str, content: Optional[str], metadata: Optional[Dict[str, Any]]) -> str:
    """Build short summary from content or metadata."""
    if metadata and isinstance(metadata.get("summary"), str):
        return (metadata["summary"] or "")[:SUMMARY_MAX_CHARS]
    if not content:
        return f"File: {filename}"
    lines = content.strip().split("\n")
    first_line = (lines[0] if lines else "").strip()
    if len(first_line) > SUMMARY_MAX_CHARS:
        return first_line[:SUMMARY_MAX_CHARS] + "..."
    return first_line or f"File: {filename}"


def build_file_list_index(
    dir_artifact: DirArtifact,
    summary_max_chars: Optional[int] = None,
) -> List[FileListEntry]:
    """
    Build file list index: list of (filename, summary) for agent to find files.
    When summary_max_chars is None, uses env FREEDOM_SPACE_FILE_LIST_SUMMARY_MAX_CHARS (default 256).

    Args:
        dir_artifact: DirArtifact with attachments (files).
        summary_max_chars: Max characters per summary; None to use env.

    Returns:
        List of FileListEntry (filename, summary, path, metadata).

    Example:
        >>> entries = build_file_list_index(dir_artifact)
        >>> for e in entries:
        ...     print(e.filename, e.summary)
    """
    if summary_max_chars is None:
        from aworld.core.context.amni.indexing.env_config import get_file_list_summary_max_chars
        summary_max_chars = get_file_list_summary_max_chars(SUMMARY_MAX_CHARS)
    start = time.time()
    entries: List[FileListEntry] = []
    if not dir_artifact.attachments:
        logger.debug(f"📁 FileListIndex: no attachments in dir_artifact")
        return entries

    for att in dir_artifact.attachments:
        try:
            filename = getattr(att, "filename", None) or str(att)
            path = getattr(att, "path", None)
            meta = getattr(att, "metadata", None) or {}
            content = _get_file_content(att)
            summary = _make_summary(filename, content, meta)
            if len(summary) > summary_max_chars:
                summary = summary[:summary_max_chars] + "..."
            entries.append(
                FileListEntry(
                    filename=filename,
                    summary=summary,
                    path=path,
                    metadata=meta,
                )
            )
        except Exception as e:
            logger.warning(f"⚠️ FileListIndex: skip file {getattr(att, 'filename', att)}: {e}")

    elapsed = time.time() - start
    logger.info(f"📁 FileListIndex: built {len(entries)} entries in {elapsed:.3f}s")
    return entries
