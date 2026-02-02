# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Environment variable config for freedom space three-layer file index.

Env vars (all optional):
- FREEDOM_SPACE_BUILD_INDEX: "1"|"true"|"yes" to enable index on load/refresh, else disabled. Default: enabled.
- FREEDOM_SPACE_FILE_LIST_SUMMARY_MAX_CHARS: max chars per file summary. Default: 256.
- FREEDOM_SPACE_USE_TREE_SITTER: "1"|"true"|"yes" to use Tree-Sitter in code index. Default: false (regex fallback).
"""

import os
from typing import Optional


def _parse_bool_env(name: str, default: bool) -> bool:
    """Parse env var as bool: 1/true/yes (case-insensitive) -> True, 0/false/no -> False."""
    val = os.environ.get(name)
    if val is None:
        return default
    v = val.strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return default


def _parse_int_env(name: str, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """Parse env var as int; clamp to [min_val, max_val] if set."""
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        n = int(val.strip())
    except ValueError:
        return default
    if min_val is not None and n < min_val:
        return min_val
    if max_val is not None and n > max_val:
        return max_val
    return n


# Env keys
ENV_BUILD_INDEX = "FREEDOM_SPACE_BUILD_INDEX"
ENV_FILE_LIST_SUMMARY_MAX_CHARS = "FREEDOM_SPACE_FILE_LIST_SUMMARY_MAX_CHARS"
ENV_USE_TREE_SITTER = "FREEDOM_SPACE_USE_TREE_SITTER"
ENV_USE_SEMANTIC_INDEX = "FREEDOM_SPACE_USE_SEMANTIC_INDEX"
ENV_EMBEDDING_MODEL = "FREEDOM_SPACE_EMBEDDING_MODEL"


def is_build_index_enabled() -> bool:
    """Whether to run three-layer file index on load_freedom_space/refresh_freedom_space. Default True."""
    return _parse_bool_env(ENV_BUILD_INDEX, default=True)


def get_file_list_summary_max_chars(default: int = 256) -> int:
    """Max characters per file summary in file list index. Default 256, clamped to [64, 2048]."""
    return _parse_int_env(ENV_FILE_LIST_SUMMARY_MAX_CHARS, default=default, min_val=64, max_val=2048)


def is_use_tree_sitter() -> bool:
    """Whether to use Tree-Sitter in code index (when available). Default False (regex fallback)."""
    return _parse_bool_env(ENV_USE_TREE_SITTER, default=False)


def is_semantic_index_enabled() -> bool:
    """Whether to build semantic index (chunk + embedding). Default False. Model downloads on first use."""
    return _parse_bool_env(ENV_USE_SEMANTIC_INDEX, default=False)


def get_embedding_model(default: str = "all-MiniLM-L6-v2") -> str:
    """Embedding model name for semantic index. Env FREEDOM_SPACE_EMBEDDING_MODEL."""
    val = os.environ.get(ENV_EMBEDDING_MODEL)
    if val is None or not val.strip():
        return default
    return val.strip()
