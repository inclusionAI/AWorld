# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
File text index builder: full text per file for semantic/search use.
"""

import time
from typing import Any, Dict, Optional

from aworld.logs.util import logger

from aworld.core.context.amni.indexing.file_list_indexer import _get_file_content
from aworld.core.context.amni.retrieval.artifacts.file import DirArtifact


def build_file_text_index(dir_artifact: DirArtifact) -> Dict[str, str]:
    """
    Build file text index: filename -> full text content.

    Args:
        dir_artifact: DirArtifact with attachments (files).

    Returns:
        Dict mapping filename to full text content.

    Example:
        >>> index = build_file_text_index(dir_artifact)
        >>> text = index.get("main.py", "")
    """
    start = time.time()
    index: Dict[str, str] = {}
    if not dir_artifact.attachments:
        logger.debug(f"📄 FileTextIndex: no attachments in dir_artifact")
        return index

    for att in dir_artifact.attachments:
        try:
            filename = getattr(att, "filename", None) or str(att)
            content = _get_file_content(att)
            if content is not None:
                index[filename] = content
        except Exception as e:
            logger.warning(f"⚠️ FileTextIndex: skip file {getattr(att, 'filename', att)}: {e}")

    elapsed = time.time() - start
    logger.info(f"📄 FileTextIndex: built {len(index)} entries in {elapsed:.3f}s")
    return index
