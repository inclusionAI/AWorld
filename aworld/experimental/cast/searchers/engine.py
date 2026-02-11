"""
Search engine core architecture
=============

Unified search interface that integrates multiple tools such as Grep, Glob and Read.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Union, AsyncIterator, Iterator
from pathlib import Path
from enum import Enum


class SearchType(Enum):
    """Enumeration of search types."""
    GREP = "grep"          # Content search
    GLOB = "glob"          # File pattern matching
    READ = "read"          # File reading
    TREE = "tree"          # Directory tree structure
    FILES = "files"        # File listing


@dataclass
class SearchResult:
    """Search result data structure."""
    title: str
    search_type: SearchType
    matches: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    output: str
    truncated: bool = False
    total_count: int = 0
    execution_time: float = 0.0


@dataclass
class SearchParams:
    """Search parameters."""
    pattern: Optional[str] = None           # Search pattern / regular expression
    path: Optional[str] = None              # Search path
    include_patterns: Optional[List[str]] = None  # File patterns to include
    exclude_patterns: Optional[List[str]] = None  # File patterns to exclude
    max_results: int = 100                  # Maximum number of results
    max_line_length: int = 2000            # Maximum line length
    follow_symlinks: bool = True           # Follow symbolic links
    search_hidden: bool = True             # Search hidden files
    case_sensitive: bool = False           # Case sensitivity
    offset: int = 0                        # Result offset
    limit: int = 2000                      # Result limit
    max_depth: Optional[int] = None        # Maximum search depth
    context_lines: int = 0                 # Number of context lines


class Searcher(ABC):
    """Abstract base class for search tools."""

    @abstractmethod
    def search(self, params: SearchParams) -> SearchResult:
        """Execute a search."""
        pass

    @abstractmethod
    def get_search_type(self) -> SearchType:
        """Return the search tool type."""
        pass

    @abstractmethod
    def validate_params(self, params: SearchParams) -> bool:
        """Validate search parameters."""
        pass


class SearchEngine:
    """
    Unified search engine.

    Integrates multiple search tools and exposes a unified search interface.
    Based on opencode's design philosophy, supports tool composition and result aggregation.
    """

    def __init__(self, root_path: Optional[Union[str, Path]] = None):
        self.root_path = Path(root_path) if root_path else Path.cwd()
        self.searchers: Dict[SearchType, Searcher] = {}

    def register_searcher(self, searcher: Searcher):
        """Register a search tool implementation."""
        search_type = searcher.get_search_type()
        self.searchers[search_type] = searcher

    async def search(self, search_type: SearchType, params: SearchParams) -> SearchResult:
        """Execute a search of the specified type."""
        if search_type not in self.searchers:
            raise ValueError(f"Search tool not registered: {search_type}")

        searcher = self.searchers[search_type]
        if not searcher.validate_params(params):
            raise ValueError(f"Invalid search parameters: {params}")

        return await searcher.search(params)

    def multi_search(self, searches: List[tuple[SearchType, SearchParams]]) -> List[SearchResult]:
        """Execute multiple search operations."""
        results = []
        for search_type, params in searches:
            try:
                result = self.search(search_type, params)
                results.append(result)
            except Exception as e:
                # Create an error result
                error_result = SearchResult(
                    title=f"Search failed: {search_type.value}",
                    search_type=search_type,
                    matches=[],
                    metadata={"error": str(e)},
                    output=f"Search failed: {str(e)}",
                    truncated=False
                )
                results.append(error_result)
        return results

    def combined_search(self, pattern: str, search_types: List[SearchType] = None) -> Dict[SearchType, SearchResult]:
        """Combined search: use multiple search strategies at the same time."""
        if search_types is None:
            search_types = [SearchType.GREP, SearchType.GLOB]

        results = {}
        base_params = SearchParams(pattern=pattern, path=str(self.root_path))

        for search_type in search_types:
            try:
                result = self.search(search_type, base_params)
                results[search_type] = result
            except Exception as e:
                results[search_type] = SearchResult(
                    title=f"Combined search failed: {search_type.value}",
                    search_type=search_type,
                    matches=[],
                    metadata={"error": str(e)},
                    output=f"Search failed: {str(e)}",
                    truncated=False
                )

        return results

    def get_available_searchers(self) -> List[SearchType]:
        """Return the list of available search tools."""
        return list(self.searchers.keys())

    def set_root_path(self, path: Union[str, Path]):
        """Set the root path for searches."""
        self.root_path = Path(path)
        # Notify all tools that the root path has changed
        for searcher in self.searchers.values():
            if hasattr(searcher, 'set_root_path'):
                searcher.set_root_path(self.root_path)