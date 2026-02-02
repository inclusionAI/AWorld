# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
File code index builder: Tree-Sitter def/ref extraction + PageRank for precise code positioning.

Uses tree-sitter (py-tree-sitter) when available; optionally grep_ast can be used via hooks.
Supports multi-language query files (.scm) when language grammars are available.
"""

import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from aworld.logs.util import logger

from aworld.core.context.amni.indexing.models import CodeTag, FileCodeIndexResult
from aworld.core.context.amni.indexing.file_list_indexer import _get_file_content
from aworld.core.context.amni.retrieval.artifacts.file import DirArtifact

# File extensions treated as code for Tree-Sitter (others skipped)
CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp"}
# Python Tree-Sitter query strings (def: function/class; ref: call)
PYTHON_DEF_QUERY = """
(function_definition name: (identifier) @def)
(class_definition name: (identifier) @def)
"""
PYTHON_REF_QUERY = """
(call function: (identifier) @ref)
(call function: (attribute attribute: (identifier) @ref))
"""
# PageRank params
PAGERANK_DAMPING = 0.85
PAGERANK_ITERATIONS = 20


def _get_lang_for_filename(filename: str) -> Optional[str]:
    """Map filename to tree-sitter language name."""
    ext = "." + filename.split(".")[-1].lower() if "." in filename else ""
    if ext not in CODE_EXTENSIONS:
        return None
    return "python" if ext == ".py" else None  # extend for other langs when grammars available


def _extract_def_ref_tree_sitter(
    filename: str,
    content: str,
    lang: str,
) -> Tuple[List[CodeTag], List[CodeTag]]:
    """
    Extract def and ref tags using tree-sitter when available.
    Uses grep_ast or py-tree-sitter + .scm query files when installed; else returns empty.
    Returns (defs, refs). Caller falls back to regex when empty or on error.
    """
    defs: List[CodeTag] = []
    refs: List[CodeTag] = []
    try:
        from tree_sitter import Query
        from tree_sitter_languages import get_parser
    except ImportError:
        logger.debug(f"🌲 Tree-sitter not installed, use regex for {filename}")
        return _extract_def_ref_regex(content, filename, "def"), _extract_def_ref_regex(content, filename, "ref")

    if lang != "python":
        return _extract_def_ref_regex(content, filename, "def"), _extract_def_ref_regex(content, filename, "ref")
    try:
        parser = get_parser("python")
        tree = parser.parse(bytes(content, "utf8"))
        if tree is None or tree.root_node is None:
            return _extract_def_ref_regex(content, filename, "def"), _extract_def_ref_regex(content, filename, "ref")
        lang_obj = parser.language
        q_def = Query(lang_obj, PYTHON_DEF_QUERY)
        q_ref = Query(lang_obj, PYTHON_REF_QUERY)
        lines = content.split("\n")

        def line_for_byte(byte_offset: int) -> int:
            total = 0
            for i, line in enumerate(lines):
                total += len(line.encode("utf8")) + 1
                if byte_offset < total:
                    return i + 1
            return len(lines)

        def run_captures(query: Query, root) -> List[tuple]:
            """Run query and return list of (node, capture_name). Uses QueryCursor if available."""
            try:
                from tree_sitter import QueryCursor
                cursor = QueryCursor()
                return list(cursor.captures(query, root))
            except (ImportError, AttributeError, TypeError):
                if hasattr(query, "captures") and callable(getattr(query, "captures")):
                    return list(query.captures(root))
                return []

        for cap_node, cap_name in run_captures(q_def, tree.root_node):
            start_byte = cap_node.start_byte
            line = line_for_byte(start_byte)
            name = content[cap_node.start_byte:cap_node.end_byte]
            kind = "function" if "function" in str(cap_node.type) else "class"
            defs.append(CodeTag(filename=filename, line=line, identifier=name, tag_type="def", kind=kind))
        for cap_node, cap_name in run_captures(q_ref, tree.root_node):
            start_byte = cap_node.start_byte
            line = line_for_byte(start_byte)
            name = content[cap_node.start_byte:cap_node.end_byte]
            refs.append(CodeTag(filename=filename, line=line, identifier=name, tag_type="ref", kind="call"))
        return defs, refs
    except Exception as e:
        logger.debug(f"🌲 Tree-sitter parse failed for {filename}: {e}")
        return _extract_def_ref_regex(content, filename, "def"), _extract_def_ref_regex(content, filename, "ref")


def _extract_def_ref_regex(content: str, filename: str, tag_type: str) -> List[CodeTag]:
    """Fallback: extract def/ref using regex (Python)."""
    tags: List[CodeTag] = []
    lines = content.split("\n")
    if tag_type == "def":
        for i, line in enumerate(lines, 1):
            # function def
            m = re.match(r"^\s*def\s+(\w+)\s*\(", line)
            if m:
                tags.append(CodeTag(filename=filename, line=i, identifier=m.group(1), tag_type="def", kind="function"))
                continue
            m = re.match(r"^\s*class\s+(\w+)\s*", line)
            if m:
                tags.append(CodeTag(filename=filename, line=i, identifier=m.group(1), tag_type="def", kind="class"))
    else:
        # ref: simple heuristic – identifier followed by ( on same line (call)
        for i, line in enumerate(lines, 1):
            for m in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", line):
                name = m.group(1)
                if name not in ("def", "class", "if", "for", "while", "with", "return", "lambda", "and", "or", "not", "in", "is", "None", "True", "False"):
                    tags.append(CodeTag(filename=filename, line=i, identifier=name, tag_type="ref", kind="call"))
    return tags


def _build_graph_and_pagerank(
    defs_by_file: Dict[str, List[Tuple[str, int]]],
    refs_by_file: Dict[str, List[Tuple[str, int]]],
    all_files: Set[str],
) -> Tuple[Dict[str, float], List[Tuple[str, str]]]:
    """
    Build directed graph: edge (A, B) when file A references identifier defined in file B.
    Then run PageRank; return (file -> score, list of edges).
    """
    # Global def map: identifier -> set of (filename, line) defining it
    ident_to_def: Dict[str, Set[Tuple[str, int]]] = defaultdict(set)
    for f, defs in defs_by_file.items():
        for ident, line in defs:
            ident_to_def[ident].add((f, line))

    edges: List[Tuple[str, str]] = []
    for ref_file, refs in refs_by_file.items():
        for ident, _ in refs:
            for (def_file, _) in ident_to_def.get(ident, set()):
                if def_file != ref_file:
                    edges.append((ref_file, def_file))

    # PageRank: nodes = all_files
    nodes = list(all_files)
    n = len(nodes)
    node_to_idx = {u: i for i, u in enumerate(nodes)}
    idx_to_node = {i: u for u, i in node_to_idx.items()}
    out_edges: Dict[int, List[int]] = defaultdict(list)
    for a, b in edges:
        if a in node_to_idx and b in node_to_idx:
            i, j = node_to_idx[a], node_to_idx[b]
            out_edges[i].append(j)

    scores = [1.0 / max(n, 1)] * n
    for _ in range(PAGERANK_ITERATIONS):
        new_scores = [(1.0 - PAGERANK_DAMPING) / max(n, 1)] * n
        for i in range(n):
            for j in out_edges[i]:
                out_deg = len(out_edges[i])
                if out_deg > 0:
                    new_scores[j] += PAGERANK_DAMPING * scores[i] / out_deg
        scores = new_scores

    file_pagerank = {idx_to_node[i]: scores[i] for i in range(n)}
    return file_pagerank, edges


def build_file_code_index(
    dir_artifact: DirArtifact,
    use_tree_sitter: bool = False,
) -> FileCodeIndexResult:
    """
    Build file code index: Tree-Sitter def/ref extraction + PageRank.

    - def: function, class, method definitions.
    - ref: function call, variable use.
    - Store: filename, line, identifier name, type (def/ref).
    - PageRank: nodes = files; edge A -> B when A references B's def.

    Args:
        dir_artifact: DirArtifact with attachments (code files).
        use_tree_sitter: If True, try tree-sitter; else use regex fallback for Python.

    Returns:
        FileCodeIndexResult with tags, defs_by_file, refs_by_file, file_pagerank, edges.

    Example:
        >>> result = build_file_code_index(dir_artifact)
        >>> for f, score in sorted(result.file_pagerank.items(), key=lambda x: -x[1])[:5]:
        ...     print(f, score)
    """
    start = time.time()
    tags: List[CodeTag] = []
    defs_by_file: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    refs_by_file: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    all_files: Set[str] = set()

    if not dir_artifact.attachments:
        logger.debug(f"🔗 CodeIndex: no attachments in dir_artifact")
        return FileCodeIndexResult(
            tags=tags,
            defs_by_file=dict(defs_by_file),
            refs_by_file=dict(refs_by_file),
            file_pagerank={},
        )

    for att in dir_artifact.attachments:
        filename = getattr(att, "filename", None) or str(att)
        content = _get_file_content(att)
        if content is None:
            continue
        lang = _get_lang_for_filename(filename)
        if lang is None:
            continue
        all_files.add(filename)
        if use_tree_sitter:
            try:
                defs_list, refs_list = _extract_def_ref_tree_sitter(filename, content, lang)
            except Exception as e:
                logger.debug(f"🔗 CodeIndex: tree-sitter failed for {filename}: {e}")
                defs_list = _extract_def_ref_regex(content, filename, "def")
                refs_list = _extract_def_ref_regex(content, filename, "ref")
        else:
            defs_list = _extract_def_ref_regex(content, filename, "def")
            refs_list = _extract_def_ref_regex(content, filename, "ref")
        tags.extend(defs_list)
        tags.extend(refs_list)
        for t in defs_list:
            defs_by_file[filename].append((t.identifier, t.line))
        for t in refs_list:
            refs_by_file[filename].append((t.identifier, t.line))

    file_pagerank, edges = _build_graph_and_pagerank(
        dict(defs_by_file), dict(refs_by_file), all_files
    )
    elapsed = time.time() - start
    logger.info(
        f"🔗 CodeIndex: built defs={sum(len(v) for v in defs_by_file.values())} refs={sum(len(v) for v in refs_by_file.values())} "
        f"edges={len(edges)} pagerank={len(file_pagerank)} files in {elapsed:.3f}s"
    )
    return FileCodeIndexResult(
        tags=tags,
        defs_by_file=dict(defs_by_file),
        refs_by_file=dict(refs_by_file),
        file_pagerank=file_pagerank,
        edges=edges,
    )
