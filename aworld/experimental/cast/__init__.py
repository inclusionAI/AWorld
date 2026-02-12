"""
AWorld AST Framework
====================

An abstract and extensible AST framework based on Tree-sitter, for agent-oriented code analysis and optimization.

Inspired by aider's design, it implements a three-level hierarchical indexing structure:
1. L1 - Panorama Logic Layer: dynamic call topology and module relationships
2. L2 - Interface Skeleton Layer: pseudo-code signatures to be implemented
3. L3 - Source Implementation Layer: concrete code implementations

Core features:
- Unified Tree-sitter parser architecture
- High-performance multi-language code parsing
- Code importance ranking using the PageRank algorithm
- Dynamic trace mapping and pruning optimization
- Caching and incremental processing
- Extensible parser registration mechanism
"""

# Base parsers and concrete implementations
from .ast_parsers.base_parser import BaseParser
from .ast_parsers.html_parser import HtmlParser
from .ast_parsers.python_parser import PythonParser
# Core framework classes
from .core import (
    ACast,
    ASTContextBuilder,
)
# Data models
from .models import (
    Symbol,
    Reference,
    CodeNode,
    RepositoryMap,
    LogicLayer,
    SkeletonLayer,
    ImplementationLayer,
    SymbolType,
    ReferenceType,
)
# Parser utilities (core)

# Utility classes

__version__ = "2.0.0"
__all__ = [
    # Core framework
    "ACast",
    "ASTContextBuilder",

    # Parser base class and implementations
    "BaseParser",
    "PythonParser",
    "HtmlParser",

    # Data models
    "Symbol",
    "Reference",
    "CodeNode",
    "RepositoryMap",
    "LogicLayer",
    "SkeletonLayer",
    "ImplementationLayer",
    "SymbolType",
    "ReferenceType",

]
