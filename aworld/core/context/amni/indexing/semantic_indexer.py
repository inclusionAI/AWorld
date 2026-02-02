# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Semantic index builder: chunk code by function/class, optional embedding for meaning-based search.

Optional dependency: sentence-transformers (model downloads on first use, ~100MB+).
Env: FREEDOM_SPACE_USE_SEMANTIC_INDEX, FREEDOM_SPACE_EMBEDDING_MODEL.
"""

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from aworld.logs.util import logger

from aworld.core.context.amni.indexing.models import SemanticChunk, SemanticIndexResult
from aworld.core.context.amni.retrieval.artifacts.file import DirArtifact

# Reuse code file extensions
CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp"}
# Default small embedding model (downloads on first use)
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
# Max chunk text length for embedding
CHUNK_MAX_CHARS = 2000


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


def _get_lang_for_filename(filename: str) -> Optional[str]:
    """Map filename to language for chunking."""
    ext = "." + filename.split(".")[-1].lower() if "." in filename else ""
    if ext not in CODE_EXTENSIONS:
        return None
    return "python" if ext == ".py" else None


def _chunk_python(content: str, filename: str) -> List[SemanticChunk]:
    """Chunk Python file by function and class boundaries."""
    chunks: List[SemanticChunk] = []
    lines = content.split("\n")
    i = 0
    chunk_idx = 0
    while i < len(lines):
        line = lines[i]
        # Match def or class at line start
        m_def = re.match(r"^(\s*def\s+\w+.*)$", line)
        m_class = re.match(r"^(\s*class\s+\w+.*)$", line)
        if m_def or m_class:
            start_line = i + 1
            kind = "function" if m_def else "class"
            # Collect until next def/class at same or lesser indent, or EOF
            indent = len(line) - len(line.lstrip())
            block_lines = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if not next_line.strip():
                    block_lines.append(next_line)
                    j += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent <= indent and (re.match(r"^\s*def\s+", next_line) or re.match(r"^\s*class\s+", next_line)):
                    break
                block_lines.append(next_line)
                j += 1
            end_line = j
            text = "\n".join(block_lines)
            if len(text) > CHUNK_MAX_CHARS:
                text = text[:CHUNK_MAX_CHARS] + "\n..."
            chunk_id = f"{filename}:{start_line}-{end_line}"
            chunks.append(
                SemanticChunk(
                    filename=filename,
                    start_line=start_line,
                    end_line=end_line,
                    text=text,
                    chunk_id=chunk_id,
                    kind=kind,
                    metadata={},
                )
            )
            chunk_idx += 1
            i = j  # advance past this block
        else:
            i += 1
    return chunks


def _embed_chunks(
    chunks: List[SemanticChunk],
    model_name·: str,
) -> Tuple[List[List[float]], str]:
    """
    Embed chunk texts with sentence-transformers. Model downloads on first use.

    Returns:
        (list of vectors, model_name_used).
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.debug("🔮 sentence-transformers not installed, semantic index will have no embeddings")
        return [], ""

    try:
        model = SentenceTransformer(model_name)
        texts = [c.text for c in chunks]
        vectors = model.encode(texts, show_progress_bar=False).tolist()
        return vectors, model_name
    except Exception as e:
        logger.warning(f"⚠️ Semantic index embedding failed: {e}")
        return [], ""


def build_semantic_index(
    dir_artifact: DirArtifact,
    use_embedding: bool = True,
    embedding_model: Optional[str] = None,
) -> SemanticIndexResult:
    """
    Build semantic index: chunk code by function/class, optionally embed for meaning-based search.

    Chunking: Python by def/class; other languages skipped unless extended.
    Embedding: optional sentence-transformers (model downloads on first use if use_embedding=True).

    Args:
        dir_artifact: DirArtifact with attachments (code files).
        use_embedding: Whether to compute embeddings (requires sentence-transformers).
        embedding_model: Model name for embedding; None to use env or default.

    Returns:
        SemanticIndexResult with chunks and optional embeddings.

    Example:
        >>> result = build_semantic_index(dir_artifact, use_embedding=True)
        >>> # Query: embed search text with same model, then cosine similarity over result.embeddings
    """
    from aworld.core.context.amni.indexing.env_config import get_embedding_model

    start = time.time()
    chunks: List[SemanticChunk] = []

    if not dir_artifact.attachments:
        logger.debug("🔮 SemanticIndex: no attachments in dir_artifact")
        return SemanticIndexResult(chunks=chunks, embedding_model=None)

    for att in dir_artifact.attachments:
        filename = getattr(att, "filename", None) or str(att)
        content = _get_file_content(att)
        if content is None:
            continue
        lang = _get_lang_for_filename(filename)
        if lang != "python":
            continue
        file_chunks = _chunk_python(content, filename)
        chunks.extend(file_chunks)

    embedding_model_name: Optional[str] = embedding_model or get_embedding_model()
    embeddings_list: List[List[float]] = []

    if use_embedding and chunks and embedding_model_name:
        try:
            embeddings_list, used_model = _embed_chunks(chunks, embedding_model_name)
            if used_model:
                embedding_model_name = used_model
                logger.info(f"🔮 SemanticIndex: embedded {len(chunks)} chunks with model={used_model}")
        except Exception as e:
            logger.warning(f"⚠️ SemanticIndex: embedding skipped: {e}")
            embedding_model_name = None

    elapsed = time.time() - start
    logger.info(f"🔮 SemanticIndex: built {len(chunks)} chunks, embeddings={len(embeddings_list)} in {elapsed:.3f}s")
    return SemanticIndexResult(
        chunks=chunks,
        embedding_model=embedding_model_name,
        embeddings=embeddings_list if embeddings_list else None,
        metadata={
            "chunk_count": len(chunks),
            "embedding_dim": len(embeddings_list[0]) if embeddings_list else None,
        },
    )
