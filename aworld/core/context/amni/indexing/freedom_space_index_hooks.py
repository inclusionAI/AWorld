# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Default hooks for freedom space three-layer file index.

Registered with HookFactory; run when freedom space is loaded/refreshed.
"""

from typing import Any, Dict, List

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import (
    FreedomSpaceFileListIndexHook,
    FreedomSpaceFileTextIndexHook,
    FreedomSpaceFileCodeIndexHook,
    FreedomSpaceSemanticIndexHook,
)
from aworld.core.context.amni.indexing.file_list_indexer import build_file_list_index
from aworld.core.context.amni.indexing.file_text_indexer import build_file_text_index
from aworld.core.context.amni.indexing.code_indexer import build_file_code_index
from aworld.core.context.amni.indexing.semantic_indexer import build_semantic_index
from aworld.core.context.amni.indexing.models import FileListEntry, FileCodeIndexResult, SemanticChunk, SemanticIndexResult
from aworld.core.context.amni.retrieval.artifacts.file import DirArtifact


def _get_dir_artifact_from_message(message: Message) -> DirArtifact:
    """Extract DirArtifact from message payload or headers."""
    payload = message.payload if message.payload is not None else {}
    if isinstance(payload, DirArtifact):
        return payload
    if isinstance(payload, dict):
        art = payload.get("dir_artifact")
        if isinstance(art, DirArtifact):
            return art
    ctx = message.headers.get("context") if message.headers else None
    if ctx and hasattr(ctx, "_working_dir"):
        wd = getattr(ctx, "_working_dir", None)
        if isinstance(wd, DirArtifact):
            return wd
    raise ValueError("DirArtifact not found in message payload or context")


def _serialize_file_list(entries: List[FileListEntry]) -> List[Dict[str, Any]]:
    """Serialize file list for payload."""
    return [
        {"filename": e.filename, "summary": e.summary, "path": e.path, "metadata": e.metadata}
        for e in entries
    ]


def _serialize_code_index(result: FileCodeIndexResult) -> Dict[str, Any]:
    """Serialize code index for payload."""
    return {
        "defs_by_file": {k: list(v) for k, v in result.defs_by_file.items()},
        "refs_by_file": {k: list(v) for k, v in result.refs_by_file.items()},
        "file_pagerank": result.file_pagerank,
        "edges": result.edges,
        "tags_count": len(result.tags),
    }


def _serialize_semantic_index(result: SemanticIndexResult) -> Dict[str, Any]:
    """Serialize semantic index for payload (chunks + optional embeddings)."""
    chunks = [
        {
            "filename": c.filename,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "text": c.text,
            "chunk_id": c.chunk_id,
            "kind": c.kind,
            "metadata": c.metadata,
        }
        for c in result.chunks
    ]
    return {
        "chunks": chunks,
        "embedding_model": result.embedding_model,
        "embeddings": result.embeddings,
        "metadata": result.metadata,
    }


@HookFactory.register(name="DefaultFreedomSpaceFileListIndexHook", desc="Default file list index (filename, summary)")
class DefaultFreedomSpaceFileListIndexHook(FreedomSpaceFileListIndexHook):
    """Default hook: build file list index (filename, summary) for agent to find files."""

    async def exec(self, message: Message, context: Context = None) -> Message:
        try:
            dir_artifact = _get_dir_artifact_from_message(message)
            entries = build_file_list_index(dir_artifact)
            payload = {"file_list_index": _serialize_file_list(entries)}
            return Message(
                category="freedom_space_index",
                payload=payload,
                sender=self.__class__.__name__,
                session_id=context.session_id if context else None,
                headers={"context": context},
            )
        except Exception as e:
            logger.warning(f"⚠️ DefaultFreedomSpaceFileListIndexHook failed: {e}")
            return None


@HookFactory.register(name="DefaultFreedomSpaceFileTextIndexHook", desc="Default file text index (full text per file)")
class DefaultFreedomSpaceFileTextIndexHook(FreedomSpaceFileTextIndexHook):
    """Default hook: build file text index (full text per file)."""

    async def exec(self, message: Message, context: Context = None) -> Message:
        try:
            dir_artifact = _get_dir_artifact_from_message(message)
            index = build_file_text_index(dir_artifact)
            payload = {"file_text_index": index}
            return Message(
                category="freedom_space_index",
                payload=payload,
                sender=self.__class__.__name__,
                session_id=context.session_id if context else None,
                headers={"context": context},
            )
        except Exception as e:
            logger.warning(f"⚠️ DefaultFreedomSpaceFileTextIndexHook failed: {e}")
            return None


@HookFactory.register(name="DefaultFreedomSpaceFileCodeIndexHook", desc="Default file code index (def/ref + PageRank)")
class DefaultFreedomSpaceFileCodeIndexHook(FreedomSpaceFileCodeIndexHook):
    """Default hook: build file code index (Tree-Sitter def/ref + PageRank) for precise code positioning."""

    async def exec(self, message: Message, context: Context = None) -> Message:
        try:
            dir_artifact = _get_dir_artifact_from_message(message)
            from aworld.core.context.amni.indexing.env_config import is_use_tree_sitter
            result = build_file_code_index(dir_artifact, use_tree_sitter=is_use_tree_sitter())
            payload = {"file_code_index": _serialize_code_index(result)}
            return Message(
                category="freedom_space_index",
                payload=payload,
                sender=self.__class__.__name__,
                session_id=context.session_id if context else None,
                headers={"context": context},
            )
        except Exception as e:
            logger.warning(f"⚠️ DefaultFreedomSpaceFileCodeIndexHook failed: {e}")
            return None


@HookFactory.register(name="DefaultFreedomSpaceSemanticIndexHook", desc="Default semantic index (chunk + embedding, optional model download)")
class DefaultFreedomSpaceSemanticIndexHook(FreedomSpaceSemanticIndexHook):
    """Default hook: build semantic index (chunk by function/class, optional embedding). Only runs when FREEDOM_SPACE_USE_SEMANTIC_INDEX=1."""

    async def exec(self, message: Message, context: Context = None) -> Message:
        try:
            from aworld.core.context.amni.indexing.env_config import is_semantic_index_enabled
            if not is_semantic_index_enabled():
                return None
            dir_artifact = _get_dir_artifact_from_message(message)
            result = build_semantic_index(dir_artifact, use_embedding=True)
            payload = {"semantic_index": _serialize_semantic_index(result)}
            return Message(
                category="freedom_space_index",
                payload=payload,
                sender=self.__class__.__name__,
                session_id=context.session_id if context else None,
                headers={"context": context},
            )
        except Exception as e:
            logger.warning(f"⚠️ DefaultFreedomSpaceSemanticIndexHook failed: {e}")
            return None
