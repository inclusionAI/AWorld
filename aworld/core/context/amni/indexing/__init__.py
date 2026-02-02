# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Freedom space three-layer file indexing + optional semantic index.

- Layer 1: File list index (filename, summary) for agent to find files at each layer.
- Layer 2: File text index (full text per file).
- Layer 3: File code index (Tree-Sitter def/ref + PageRank) for precise code positioning.
- Layer 4 (advanced): Semantic index (chunk + embedding) for meaning-based code search; optional model download.
"""

from aworld.core.context.amni.indexing.models import (
    FileListEntry,
    CodeTag,
    FileCodeIndexResult,
    FileIndexResult,
    SemanticChunk,
    SemanticIndexResult,
)
from aworld.core.context.amni.indexing.file_list_indexer import build_file_list_index
from aworld.core.context.amni.indexing.file_text_indexer import build_file_text_index
from aworld.core.context.amni.indexing.code_indexer import build_file_code_index
from aworld.core.context.amni.indexing.semantic_indexer import build_semantic_index

__all__ = [
    "FileListEntry",
    "CodeTag",
    "FileCodeIndexResult",
    "FileIndexResult",
    "SemanticChunk",
    "SemanticIndexResult",
    "build_file_list_index",
    "build_file_text_index",
    "build_file_code_index",
    "build_semantic_index",
]
