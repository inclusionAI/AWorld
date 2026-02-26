"""
AWorld AST Framework - Core Interface
====================================

Defines the core abstract interfaces and main components of the AST analysis framework.
"""

import json
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Any, TYPE_CHECKING, Union, Type

from . import PythonParser, HtmlParser
from .ast_analyzer import ASTContextBuilder, DefaultASTAnalyzer
from .coders import (
    BaseCoder, SearchReplaceCoder, DmpCoder, OpCoder
)
from .models import (
    CodeNode, RepositoryMap
)
from .searchers.engine import SearchEngine, SearchType, SearchParams, SearchResult
from .searchers.searchers import GrepSearcher, GlobSearcher, ReadSearcher
from .utils import logger

# Import BaseParser (no circular dependency as parsers.base_parser doesn't import core)
if TYPE_CHECKING:
    from.ast_parsers.base_parser import BaseParser
else:
    from.ast_parsers.base_parser import BaseParser

_PARSER_REGISTRY: Dict[str, Type[BaseParser]] = {
    'python': PythonParser,
    'html': HtmlParser,
    # more parsers
    # 'javascript': JavaScriptParser,
    # 'typescript': TypeScriptParser,
    # 'go': GoParser,
    # 'rust': RustParser,
}

class ACast:
    """AST Framework Main Entry Class"""

    def __init__(self, auto_register_parsers: bool = True, tmp_path: str = "~/.aworld/acast"):
        self.parsers: Dict[str, BaseParser] = {}
        self.analyzer: Optional[ASTContextBuilder] = None
        # Expand user directory symbol ~
        self.tmp_path = Path(tmp_path).expanduser()

        # Initialize coder registry for different operation types
        self._coders_registry: Dict[str, BaseCoder] = {}

        # Initialize search engine with searchers
        self.search_engine: Optional[SearchEngine] = None
        self._searchers_registry: Dict[SearchType, Any] = {}

        if auto_register_parsers:
            self._auto_register_all_parsers()

        self.analyzer = self.create_analyzer()
        self._init_search_engine()

    """
    code parser
    """

    def create_parser(self, language: str) -> Optional[BaseParser]:
        parser_class = _PARSER_REGISTRY.get(language.lower())
        if parser_class:
            try:
                return parser_class()
            except Exception as e:
                logger.error(f"Failed to create {language} parser: {e}")
                return None

        logger.warning(f"Unsupported language: {language}")
        return None

    def get_supported_languages(self) -> List[str]:
        return list(_PARSER_REGISTRY.keys())

    def _auto_register_all_parsers(self) -> None:
        """Automatically register all available parsers"""
        try:

            supported_languages = self.get_supported_languages()
            logger.info(f"Auto-registering parsers, supported languages: {', '.join(supported_languages)}")

            for lang in supported_languages:
                try:
                    parser = self.create_parser(lang)
                    if parser:
                        self.parsers[lang] = parser
                        logger.debug(f"✅ Auto-registered parser: {lang}")
                except Exception as e:
                    logger.warning(f"❌ Unable to register {lang} parser: {e}")

            logger.info(f"Parser auto-registration completed, registered {len(self.parsers)} parsers")

        except Exception as e:
            logger.error(f"Auto-registration of parsers failed: {e}")
            # Don't raise exception, allow manual registration

    def register_parser(self, language: str, parser: BaseParser) -> None:
        """Register language parser"""
        self.parsers[language] = parser
        logger.info(f"Registered parser: {language}")

    def list_supported_languages(self) -> List[str]:
        """List supported programming languages"""
        return list(self.parsers.keys())

    def get_parser_info(self, language: str) -> Dict[str, Any]:
        """Get parser information"""
        if language not in self.parsers:
            return {}

        parser = self.parsers[language]
        return {
            'language': parser.language,
            'file_extensions': list(parser.file_extensions),
            'comment_patterns': parser.comment_patterns,
        }

    def parse(self, file_path: Path) -> Optional[CodeNode]:
        """
        Convenient method for parsing files

        Args:
            file_path: File path

        Returns:
            Parsed CodeNode, or None if failed
        """
        parser = self.get_parser(file_path)
        if parser:
            return parser.parse_file(file_path)
        return None

    def get_parser(self, file_path: Path) -> Optional[BaseParser]:
        """Get appropriate parser based on file path"""
        for parser in self.parsers.values():
            if parser.can_parse(file_path):
                return parser
        return None

    def get_coder(self,
                  coder_type: str,
                  source_dir: Path,
                  **kwargs) -> BaseCoder:
        """
        Get or create a coder instance for the specified type and source directory

        Args:
            coder_type: Type of coder ('search_replace', 'dmp', 'op')
            source_dir: Source directory for operations
            **kwargs: Additional arguments for coder initialization

        Returns:
            Appropriate coder instance

        Raises:
            ValueError: If coder type is not supported
        """
        # Create unique key for coder based on type and source dir
        coder_key = f"{coder_type}_{str(source_dir)}"

        if coder_key not in self._coders_registry:
            # Create new coder instance
            if coder_type == "search_replace":
                coder = SearchReplaceCoder(source_dir, **kwargs)
            elif coder_type == "dmp":
                coder = DmpCoder(source_dir, **kwargs)
            elif coder_type == "op":
                coder = OpCoder(source_dir, **kwargs)
            else:
                raise ValueError(f"Unsupported coder type: {coder_type}")

            self._coders_registry[coder_key] = coder
            logger.info(f"Created new {coder_type} coder for {source_dir}")

        return self._coders_registry[coder_key]

    def clear_coders_cache(self):
        """Clear the coders cache"""
        self._coders_registry.clear()
        logger.debug("Cleared coders registry cache")

    """
    Searchers management and unified search interface
    """

    def clear_searchers_cache(self):
        """Clear the searchers cache and reinitialize search engine"""
        self._searchers_registry.clear()
        self.search_engine = None
        self._init_search_engine()
        logger.debug("Cleared searchers registry and reinitialized search engine")

    def _init_search_engine(self):
        """Initialize search engine and register searchers"""
        try:
            # Create search engine instance
            self.search_engine = SearchEngine()

            # Register built-in searchers
            grep_searcher = GrepSearcher()
            glob_searcher = GlobSearcher()
            read_searcher = ReadSearcher()

            self.search_engine.register_searcher(grep_searcher)
            self.search_engine.register_searcher(glob_searcher)
            self.search_engine.register_searcher(read_searcher)

            # Cache searchers for direct access
            self._searchers_registry = {
                SearchType.GREP: grep_searcher,
                SearchType.GLOB: glob_searcher,
                SearchType.READ: read_searcher
            }

            logger.info("Search engine initialized with Grep, Glob, and Read searchers")

        except Exception as e:
            logger.error(f"Failed to initialize search engine: {e}")
            self.search_engine = None

    async def search(self,
               search_type: Union[str, SearchType],
               pattern: Optional[str] = None,
               path: Optional[Union[str, Path]] = None,
               **kwargs) -> SearchResult:
        """
        Unified search interface

        Args:
            search_type: Search type ('grep', 'glob', 'read' or SearchType enum)
            pattern: Search pattern or file path for read operations
            path: Search path (optional, defaults to current working directory)
            **kwargs: Additional search parameters

        Returns:
            SearchResult object containing search results

        Examples:
            # Content search
            result = acast.search('grep', 'def function_name')

            # File pattern search
            result = acast.search('glob', '*.py')

            # File reading
            result = acast.search('read', path='src/main.py')
        """
        if not self.search_engine:
            raise RuntimeError("Search engine not initialized. Please check initialization logs.")

        # Convert string to SearchType enum
        if isinstance(search_type, str):
            try:
                search_type = SearchType(search_type.lower())
            except ValueError:
                raise ValueError(f"Unsupported search type: {search_type}. "
                               f"Supported types: {[t.value for t in SearchType]}")

        # Build search parameters
        search_params = SearchParams(
            pattern=pattern,
            path=str(path) if path else None,
            **kwargs
        )

        try:
            result = await self.search_engine.search(search_type, search_params)
            logger.info(f"Search completed: {search_type.value}, found {result.total_count} results")
            return result

        except Exception as e:
            logger.error(f"Search failed for {search_type.value}: {e} {traceback.format_exc()}")
            raise RuntimeError(f"Search operation failed: {e}")

    async def grep(self, pattern: str, path: Optional[Union[str, Path]] = None, **kwargs) -> SearchResult:
        """
        Content search using Grep

        Args:
            pattern: Regular expression pattern to search
            path: Search path (optional)
            **kwargs: Additional search parameters (case_sensitive, context_lines, etc.)

        Returns:
            SearchResult with matching lines
        """
        return await self.search(SearchType.GREP, pattern=pattern, path=path, **kwargs)

    async def glob(self, pattern: str, path: Optional[Union[str, Path]] = None, **kwargs) -> SearchResult:
        """
        File pattern matching using Glob

        Args:
            pattern: File pattern to match (e.g., '*.py', 'src/**/*.js')
            path: Search path (optional)
            **kwargs: Additional search parameters (max_depth, search_hidden, etc.)

        Returns:
            SearchResult with matching file paths
        """
        return await self.search(SearchType.GLOB, pattern=pattern, path=path, **kwargs)

    async def read(self, file_path: Union[str, Path], **kwargs) -> SearchResult:
        """
        Read file content

        Args:
            file_path: Path to file to read
            **kwargs: Additional read parameters (limit, offset, etc.)

        Returns:
            SearchResult with file content
        """
        return await self.search(SearchType.READ, path=file_path, **kwargs)

    """
    Code modification operations using specialized coders
    """

    def create_analyzer(self, code_analyzer_class: type = None) -> ASTContextBuilder:
        """Create repository analyzer"""
        if code_analyzer_class is None:
            code_analyzer_class = DefaultASTAnalyzer

        code_analyzer = code_analyzer_class(self.parsers)
        self.analyzer = ASTContextBuilder(code_analyzer)
        return self.analyzer

    def analyze(self, *args, **kwargs) -> RepositoryMap:
        """
        Convenient method for repository analysis, automatically record analysis results to tmp_path directory

        Args:
            *args: Positional arguments to pass to analyzer.analyze
            **kwargs: Keyword arguments to pass to analyzer.analyze, including:
                - root_path: Repository root directory (used for generating filename)
                - auto_record: Whether to automatically record (default True)
                - record_name: Record filename (optional, defaults to generated based on root_path and timestamp)

        Returns:
            Complete repository mapping
        """
        if not self.analyzer:
            raise RuntimeError("Please call create_analyzer() first to create an analyzer")

        # Extract recording-related parameters (extract root_path before calling analyze)
        auto_record = kwargs.pop('auto_record', True)
        record_name = kwargs.pop('record_name', None)

        # Execute analysis
        repo_map = self.analyzer.analyze(*args, **kwargs)

        # Auto-record analysis results
        if auto_record:
            try:
                # Generate filename
                name = record_name
                # Record analysis results
                self.record_analyze_result(name, repo_map)
            except Exception as e:
                # Recording failure doesn't affect returning analysis results
                logger.warning(f"Auto-recording analysis results failed: {e}")

        return repo_map

    def record_analyze_result(self, name, repo_map):
        """
        Record repo_map to tmp_path directory, marked with file name

        Args:
            name: Filename (without extension)
            repo_map: Repository mapping object to save

        Returns:
            Saved file path
        """
        tmp_dir = self.tmp_path
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Save as JSON file
        file_path = tmp_dir / f"{name}.json"
        try:
            # Use RepositoryMap's to_dict method for serialization
            json_data = repo_map.to_dict()
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Analysis results saved to: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to save analysis results: {e}")
            raise

    def load_analyze_result(self, name: str) -> Optional[RepositoryMap]:
        """
        Load saved analysis results from tmp_path directory

        Args:
            name: Filename (without extension)

        Returns:
            Repository mapping object, returns None if file doesn't exist or loading fails
        """
        tmp_dir = self.tmp_path
        file_path = tmp_dir / f"{name}.json"

        if not file_path.exists():
            logger.warning(f"Analysis report does not exist: {file_path}")
            return None

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            # Use RepositoryMap's from_dict method for deserialization
            repo_map = RepositoryMap.from_dict(json_data)
            # print('repo_map', repo_map)
            logger.info(f"Successfully loaded analysis report: {file_path}")
            return repo_map
        except Exception as e:
            logger.error(f"Failed to load analysis report: {e}")
            return None

    def search_ast(self,
                   repo_map: Optional[RepositoryMap] = None,
                   user_query: str = "",
                   max_tokens: int = 8000,
                   context_layers: Optional[List[str]] = None,
                   record_name: Optional[str] = None) -> str:
        """
        Get optimized context information for LLM
        Prioritize recall from recorded analysis reports from record_analyze_result

        Args:
            repo_map: Repository mapping (optional, if record_name is provided, recorded analysis report takes priority)
            user_query: User query
            max_tokens: Maximum number of tokens
            context_layers: Specified context layers to include
            record_name: Saved analysis report name (takes priority)

        Returns:
            Formatted context string
        """
        if not self.analyzer:
            raise RuntimeError("Please call create_analyzer() first to create an analyzer")

        # Prioritize loading from recorded analysis reports
        if record_name:
            loaded_repo_map = self.load_analyze_result(record_name)
            if loaded_repo_map is not None:
                repo_map = loaded_repo_map
            elif repo_map is None:
                logger.warning(f"Unable to load analysis report '{record_name}', and no repo_map parameter provided")

        # If there's still no repo_map, raise error
        if repo_map is None:
            raise ValueError("Must provide repo_map or valid record_name")

        # If advanced parameters are specified, use analyzer's advanced recall method
        if context_layers is not None:
            return self.analyzer.recall(
                repo_map=repo_map,
                user_query=user_query,
                max_tokens=max_tokens,
                context_layers=context_layers,
            )
        else:
            # Use default simple recall method
            return self.analyzer.recall(repo_map, user_query, max_tokens)

    """
    snapshot
    """

    def generate_snapshot(self, target_dir: Path, version: str = "v0") -> Path:
        """
        Generate compressed snapshot of target directory

        Args:
            target_dir: Target directory path, directory to create snapshot for
            version: Version string, defaults to "v0"

        Returns:
            Path to saved snapshot file
        """
        import tarfile

        target_dir = Path(target_dir)
        if not target_dir.exists():
            raise ValueError(f"Target directory does not exist: {target_dir}")

        # Save snapshot to tmp_path directory
        tmp_dir = self.tmp_path
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Generate snapshot filename: {path_suffix}_{version}.tar.gz
        path_suffix = target_dir.name or "default"
        snapshot_filename = f"{path_suffix}_{version}.tar.gz"
        snapshot_path = tmp_dir / snapshot_filename

        # Create compressed snapshot
        with tarfile.open(snapshot_path, "w:gz") as tar:
            tar.add(target_dir, arcname=target_dir.name,
                    filter=lambda tarinfo: None if '__pycache__' in tarinfo.name or '.pyc' in tarinfo.name else tarinfo)

        logger.info(f"Generated snapshot saved to: {snapshot_path}")

        return snapshot_path

    """
    coder
    """

    def deploy_dmp(self,
                   source_dir: Path,
                   patch_content: str,
                   version: str = "v0",
                   strict_validation: bool = True,
                   max_context_mismatches: int = 0,
                   **kwargs) -> Path:
        """
        In-place update source code directory and apply patch using DmpCoder

        Args:
            source_dir: Source code directory (will be updated in place)
            patch_content: Patch file content
            version: Version number (like "v0", "v1"), used for naming patch file
            strict_validation: Whether to enable strict validation mode (default True)
            max_context_mismatches: Maximum allowed context mismatches (default 0)
            **kwargs: Additional arguments for DmpCoder

        Returns:
            Updated directory path (same as source_dir)

        Raises:
            ValidationError: When context validation fails and exceeds allowed mismatches
        """
        logger.info(f"🚀 Executing enhanced patch application via DmpCoder")

        source_dir = Path(source_dir)
        if not source_dir.exists():
            raise ValueError(f"Source directory does not exist: {source_dir}")

        try:
            # Get DmpCoder instance
            coder = self.get_coder("dmp", source_dir,
                                  strict_validation=strict_validation,
                                  max_context_mismatches=max_context_mismatches,
                                  **kwargs)

            # Prepare operation data
            operation_data = {
                "operation": {
                    "type": "apply_patch",
                    "patch_content": patch_content,
                    "version": version,
                    "strict_validation": strict_validation,
                    "max_context_mismatches": max_context_mismatches
                }
            }

            # Execute patch application
            result = coder.execute(operation_data)

            if result.success:
                logger.info(f"✅ Enhanced copy completed successfully")
                return source_dir
            else:
                raise RuntimeError(f"Patch application failed: {result.error}")

        except Exception as e:
            logger.error(f"❌ Enhanced copy failed: {e}")
            raise RuntimeError(f"Enhanced copy failed: {e}")

    def deploy_ops(self,
                   operations_json: str,
                   source_dir: Path,
                   version: str = "v0",
                   strict_validation: bool = True,
                   max_context_mismatches: int = 0,
                   **kwargs) -> Path:
        """
        Deploy code changes based on JSON operation instructions using OpCoder

        This method combines json_operations_to_patch and create_enhanced_copy functionality,
        providing a convenient interface to deploy directly from JSON operations to source code directory.

        Args:
            operations_json: JSON format operation instructions
            source_dir: Source code directory
            version: Version number
            strict_validation: Whether to enable strict validation
            max_context_mismatches: Maximum allowed context mismatches
            **kwargs: Additional arguments for OpCoder

        Returns:
            Updated directory path
        """
        logger.info(f"🚀 Executing JSON operations deployment via OpCoder")

        try:
            # Get OpCoder instance
            coder = self.get_coder("op", source_dir,
                                  strict_validation=strict_validation,
                                  max_context_mismatches=max_context_mismatches,
                                  **kwargs)

            # Prepare operation data with configuration
            if isinstance(operations_json, str):
                operation_data = json.loads(operations_json)
            else:
                operation_data = operations_json.copy()
            
            # Add configuration parameters to operation data
            operation_data["version"] = version
            operation_data["strict_validation"] = strict_validation
            operation_data["max_context_mismatches"] = max_context_mismatches

            # Execute operations deployment
            result = coder.execute(operation_data)

            if result.success:
                logger.info(f"✅ Operations deployment completed successfully")
                return source_dir
            else:
                raise RuntimeError(f"Operations deployment failed: {result.error}")

        except Exception as e:
            logger.error(f"❌ Operations deployment failed: {e}")
            raise RuntimeError(f"Operations deployment failed: {e}")

    def search_replace_in_file(self,
                              file_path: Path,
                              search_text: str,
                              replace_text: str,
                              fuzzy_match: bool = False,  # Changed default to False for safety
                              similarity_threshold: float = 0.8) -> Dict[str, Any]:
        """
        Execute search-replace operation in file using SearchReplaceCoder

        Args:
            file_path: Target file path
            search_text: Code segment to search for
            replace_text: Replacement code segment
            fuzzy_match: Whether to enable fuzzy matching (default False for safety)
            similarity_threshold: Fuzzy matching similarity threshold (0.0-1.0)

        Returns:
            Dictionary containing operation results:
            {
                "success": bool,
                "modified": bool,
                "original_content": str,
                "new_content": str,
                "match_info": dict,
                "error": str
            }
        """
        logger.info(f"🔍 Executing file-level search-replace via SearchReplaceCoder")

        try:
            # Create operation JSON
            operation_json = {
                "operation": {
                    "type": "search_replace",
                    "file_path": file_path.name,  # Use relative path
                    "search": search_text,
                    "replace": replace_text,
                    "exact_match_only": not fuzzy_match  # Invert for clarity
                }
            }

            # Get SearchReplaceCoder instance
            coder = self.get_coder("search_replace", file_path.parent,
                                  fuzzy_match_enabled=fuzzy_match,
                                  similarity_threshold=similarity_threshold)

            # Execute operation
            result = coder.execute(operation_json)

            # Convert to legacy format
            return {
                "success": result.success,
                "modified": result.modified,
                "original_content": result.original_content,
                "new_content": result.new_content,
                "match_info": result.metadata,
                "error": result.error or ""
            }

        except Exception as e:
            logger.error(f"❌ File search-replace failed: {e}")
            return {
                "success": False,
                "modified": False,
                "original_content": "",
                "new_content": "",
                "match_info": {},
                "error": str(e)
            }

    def search_replace_operation(self,
                                source_dir: Path,
                                operation_json: str,
                                **kwargs) -> Dict[str, Any]:
        """
        Execute search-replace operation using SearchReplaceCoder

        Args:
            source_dir: Source code directory
            operation_json: JSON format search-replace operation instruction
            **kwargs: Additional arguments for SearchReplaceCoder

        Returns:
            Operation result dictionary

        Example JSON:
            {
                "operation": {
                    "type": "search_replace",
                    "file_path": "example.py",
                    "search": "def old_function():\n    pass",
                    "replace": "def new_function():\n    print('updated')",
                    "exact_match_only": true
                }
            }

        Note: For code modification precision and safety, this method only performs exact matching.
              No longer supports fuzzy matching or whitespace flexible matching.
        """
        logger.info(f"🔍 Executing search-replace operation via SearchReplaceCoder")

        try:
            # Get SearchReplaceCoder instance
            coder = self.get_coder("search_replace", source_dir, **kwargs)

            # Execute operation
            result = coder.execute(operation_json)

            # Convert CoderResult to legacy dictionary format for compatibility
            return {
                "success": result.success,
                "modified": result.modified,
                "original_content": result.original_content,
                "new_content": result.new_content,
                "error": result.error,
                "message": result.message,
                "metadata": result.metadata
            }

        except Exception as e:
            logger.error(f"❌ Search-replace operation failed: {e}")
            return {
                "success": False,
                "modified": False,
                "original_content": "",
                "new_content": "",
                "error": str(e),
                "message": "Operation failed",
                "metadata": {}
            }

    def retrieve_source_lines_from_file(self,
                                       file_path: Path,
                                       query_patterns: List[str],
                                       context_lines: int = 3,
                                       max_matches: int = 10,
                                       include_line_numbers: bool = True) -> Dict[str, Any]:
        """
        Retrieve matching code lines from source files, based on aider project best practices

        This is an independent source code retrieval function that does not modify the existing _generate_implementation_context,
        specifically for precise matching and extraction of code lines from source files.

        Features:
        1. Support multiple regex pattern matching
        2. Intelligent context expansion (configurable context lines)
        3. Precise line number annotation
        4. Match result sorting and deduplication
        5. Detailed statistics

        Args:
            file_path: Source file path to search
            query_patterns: List of regex patterns
            context_lines: Number of context lines before and after matched lines
            max_matches: Maximum number of match results
            include_line_numbers: Whether to include line numbers

        Returns:
            Retrieval result dictionary containing:
            {
                'matches': List[Dict],      # List of match results
                'file_info': Dict,          # File information
                'search_stats': Dict        # Search statistics
            }

        Examples:
            >>> retriever = ASTContextBuilder(analyzer)
            >>> result = retriever.retrieve_source_lines_from_file(
            ...     file_path=Path("example.py"),
            ...     query_patterns=["def.*generate.*context", "import.*re"],
            ...     context_lines=2
            ... )
            >>> for match in result['matches']:
            ...     print(f"Line {match['line_num']}: {match['content']}")
        """
        import re

        result = {
            'matches': [],
            'file_info': {
                'path': str(file_path),
                'exists': False,
                'total_lines': 0,
                'file_size': 0
            },
            'search_stats': {
                'patterns_count': len(query_patterns),
                'total_matches': 0,
                'unique_lines': 0,
                'search_time': 0.0
            }
        }

        if not file_path.exists():
            logger.warning(f"File does not exist: {file_path}")
            return result

        try:
            import time
            start_time = time.time()

            # Read file content
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                file_content = f.read()

            lines = file_content.splitlines()
            result['file_info'].update({
                'exists': True,
                'total_lines': len(lines),
                'file_size': len(file_content)
            })

            # Store all match results, avoid duplicates
            all_matches = {}  # line_num -> match_info

            logger.info(f"🔍 Searching in {file_path} ({len(lines)} lines)")
            logger.info(f"📝 Query patterns: {query_patterns}")

            # Search for each pattern
            for pattern_idx, pattern in enumerate(query_patterns):
                logger.debug(f"🔎 Applying pattern [{pattern_idx+1}/{len(query_patterns)}]: '{pattern}'")

                try:
                    compiled_pattern = re.compile(pattern, re.IGNORECASE)

                    # Search in each line
                    for line_num, line in enumerate(lines, 1):
                        match = compiled_pattern.search(line)
                        if match:
                            # Calculate match quality score
                            match_score = self._calculate_line_match_score(
                                line, pattern, match, line_num, len(lines)
                            )

                            match_info = {
                                'line_num': line_num,
                                'content': line,
                                'pattern': pattern,
                                'pattern_index': pattern_idx,
                                'match_start': match.start(),
                                'match_end': match.end(),
                                'match_text': match.group(),
                                'match_score': match_score,
                                'context': None  # Will be added later
                            }

                            # If this line already has a match, choose the one with higher score
                            if line_num in all_matches:
                                if match_score > all_matches[line_num]['match_score']:
                                    all_matches[line_num] = match_info
                            else:
                                all_matches[line_num] = match_info

                            logger.debug(f"  ✅ Match at line {line_num}: {line[:50]}...")

                except re.error as e:
                    logger.warning(f"❌ Invalid regex pattern '{pattern}': {e}")
                    continue

            # Sort by score and limit quantity
            sorted_matches = sorted(all_matches.values(),
                                  key=lambda x: x['match_score'],
                                  reverse=True)[:max_matches]

            # Add context for each match
            for match_info in sorted_matches:
                line_num = match_info['line_num']
                context = self._extract_line_context(
                    lines, line_num, context_lines, include_line_numbers
                )
                match_info['context'] = context
                result['matches'].append(match_info)

            # Update statistics
            search_time = time.time() - start_time
            result['search_stats'].update({
                'total_matches': len(all_matches),
                'unique_lines': len(set(m['line_num'] for m in result['matches'])),
                'search_time': search_time
            })

            logger.info(f"📊 Search completed: {len(result['matches'])} matches found in {search_time:.3f}s")

            return result

        except Exception as e:
            logger.error(f"❌ Error retrieving source lines from {file_path}: {e}")
            result['search_stats']['error'] = str(e)
            return result

    def _calculate_line_match_score(self,
                                   line: str,
                                   pattern: str,
                                   match_obj,
                                   line_num: int,
                                   total_lines: int) -> float:
        """
        Calculate quality score for single line match
        Based on match position, line length, file position and other factors
        """
        score = 10.0  # Base score

        # 1. Match completeness bonus
        match_length = match_obj.end() - match_obj.start()
        line_length = len(line.strip())
        if line_length > 0:
            match_ratio = match_length / line_length
            score += match_ratio * 5.0

        # 2. Match position bonus (matches at line start are more important)
        match_position = match_obj.start()
        stripped_line = line.lstrip()
        leading_spaces = len(line) - len(stripped_line)

        if match_position <= leading_spaces + 10:  # Near line start
            score += 3.0

        # 3. Keyword bonus
        line_lower = line.lower()
        keywords = ['def ', 'class ', 'import ', 'from ', 'return ', 'if ', 'for ', 'while ']
        for keyword in keywords:
            if keyword in line_lower:
                score += 2.0
                break

        # 4. Line number position influence (middle section code is usually more important)
        if total_lines > 10:
            line_position_ratio = line_num / total_lines
            if 0.2 <= line_position_ratio <= 0.8:  # Middle section
                score += 1.0

        # 5. Line length influence (lines that are too short or too long have slightly lower scores)
        if 10 <= line_length <= 120:
            score += 1.0

        return score

    def _extract_line_context(self,
                             lines: List[str],
                             target_line_num: int,
                             context_lines: int,
                             include_line_numbers: bool = True) -> Dict[str, Any]:
        """
        Extract context information for specified line
        """
        start_idx = max(0, target_line_num - 1 - context_lines)
        end_idx = min(len(lines), target_line_num + context_lines)

        context_info = {
            'before': [],
            'target': '',
            'after': [],
            'range': (start_idx + 1, end_idx),
            'total_lines': end_idx - start_idx
        }

        # Extract context lines
        for i in range(start_idx, end_idx):
            line_num = i + 1
            line_content = lines[i]

            if include_line_numbers:
                formatted_line = f"{line_num}→{line_content}"
            else:
                formatted_line = line_content

            if line_num < target_line_num:
                context_info['before'].append(formatted_line)
            elif line_num == target_line_num:
                if include_line_numbers:
                    context_info['target'] = f"{line_num}▶{line_content}"  # Use special marker
                else:
                    context_info['target'] = line_content
            else:
                context_info['after'].append(formatted_line)

        return context_info

    def batch_retrieve_source_lines(self,
                                   file_paths: List[Path],
                                   query_patterns: List[str],
                                   **kwargs) -> Dict[str, Any]:
        """
        Batch process source code retrieval for multiple files

        Args:
            file_paths: List of file paths
            query_patterns: List of query patterns
            **kwargs: Other parameters passed to retrieve_source_lines_from_file

        Returns:
            Batch retrieval results
        """
        batch_results = {
            'files': {},
            'summary': {
                'total_files': len(file_paths),
                'processed_files': 0,
                'total_matches': 0,
                'failed_files': [],
                'processing_time': 0.0
            }
        }

        import time
        start_time = time.time()

        logger.info(f"🚀 Starting batch source retrieval for {len(file_paths)} files")

        for file_idx, file_path in enumerate(file_paths):
            logger.info(f"📄 Processing file [{file_idx+1}/{len(file_paths)}]: {file_path}")

            try:
                result = self.retrieve_source_lines_from_file(
                    file_path, query_patterns, **kwargs
                )

                batch_results['files'][str(file_path)] = result
                batch_results['summary']['total_matches'] += result['search_stats']['total_matches']
                batch_results['summary']['processed_files'] += 1

                logger.info(f"  ✅ Found {result['search_stats']['total_matches']} matches")

            except Exception as e:
                logger.error(f"  ❌ Failed to process {file_path}: {e}")
                batch_results['summary']['failed_files'].append({
                    'path': str(file_path),
                    'error': str(e)
                })

        batch_results['summary']['processing_time'] = time.time() - start_time

        logger.info(f"📊 Batch processing completed:")
        logger.info(f"  - Processed: {batch_results['summary']['processed_files']}/{len(file_paths)} files")
        logger.info(f"  - Total matches: {batch_results['summary']['total_matches']}")
        logger.info(f"  - Processing time: {batch_results['summary']['processing_time']:.3f}s")

        return batch_results
