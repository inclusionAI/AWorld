# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Data models for freedom space three-layer file index.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FileListEntry:
    """Single entry in file list index: filename and summary for agent to find files."""

    filename: str
    summary: str
    path: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class CodeTag:
    """Code tag from Tree-Sitter: def (definition) or ref (reference)."""

    filename: str
    line: int
    identifier: str
    tag_type: str  # "def" | "ref"
    kind: Optional[str] = None  # e.g. "function", "class", "method"
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class FileCodeIndexResult:
    """Result of code index: defs/refs per file and PageRank scores."""

    tags: List[CodeTag]
    # defs: filename -> list of (identifier, line)
    defs_by_file: Dict[str, List[tuple]]
    # refs: filename -> list of (identifier, line)
    refs_by_file: Dict[str, List[tuple]]
    # PageRank: filename -> score (higher = more important / more referenced)
    file_pagerank: Dict[str, float]
    # edges: (from_file, to_file) when from_file references defs in to_file
    edges: List[tuple] = field(default_factory=list)


@dataclass
class SemanticChunk:
    """Single chunk for semantic index: file, line range, text, optional embedding ref."""

    filename: str
    start_line: int
    end_line: int
    text: str
    chunk_id: Optional[str] = None
    kind: Optional[str] = None  # e.g. "function", "class"
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class SemanticIndexResult:
    """Result of semantic index: chunks + optional embeddings (model may be downloaded on first use)."""

    chunks: List[SemanticChunk]
    embedding_model: Optional[str] = None
    # Optional: list of vectors (one per chunk); omit for large repos or when using external vector store
    embeddings: Optional[List[List[float]]] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class FileIndexResult:
    """Aggregated result of three-layer file index (+ optional semantic index)."""

    file_list_index: List[FileListEntry]
    file_text_index: Dict[str, str]  # filename -> full text
    file_code_index: Optional[FileCodeIndexResult] = None
    semantic_index: Optional[SemanticIndexResult] = None
