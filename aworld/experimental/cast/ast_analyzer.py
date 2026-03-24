"""
AWorld AST Framework - Default Implementation
==========================================

Provides default implementation of CodeAnalyzer and related components.
"""

import time
from abc import abstractmethod, ABC
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional
from typing import Set, Any

import networkx as nx

from .models import (
    CodeNode, LogicLayer, SkeletonLayer, ImplementationLayer,
    SymbolType, ReferenceType, Symbol
)
from .models import RepositoryMap
from .utils import logger


class ASTAnalyzer(ABC):
    """Code analyzer abstract base class"""

    def __init__(self, parsers: Dict[str, Any]):
        self.parsers = parsers
        self.cache_enabled = True

    @abstractmethod
    def analyze_repository(self, root_path: Path,
                           file_patterns: Optional[List[str]] = None,
                           ignore_patterns: Optional[List[str]] = None,
                           enable_dependency_graph: bool = False) -> RepositoryMap:
        """
        Analyze entire code repository

        Args:
            root_path: Repository root directory
            file_patterns: File patterns to include
            ignore_patterns: File patterns to ignore
            enable_dependency_graph: Whether to build dependency graph and PageRank (costly for large repos)

        Returns:
            Complete repository mapping
        """
        pass

    @abstractmethod
    def analyze_files(self, file_paths: List[Path]) -> Dict[Path, CodeNode]:
        """
        Analyze specified file list

        Args:
            file_paths: List of file paths to analyze

        Returns:
            Mapping from file path to CodeNode
        """
        pass

    @abstractmethod
    def build_dependency_graph(self, code_nodes: Dict[Path, CodeNode]) -> Dict[Path, Set[Path]]:
        """
        Build file dependency graph

        Args:
            code_nodes: Mapping from file to CodeNode

        Returns:
            Dependency graph: {file_path: {dependent_files}}
        """
        pass

    @abstractmethod
    def calculate_importance(self, code_nodes: Dict[Path, CodeNode],
                             dependency_graph: Dict[Path, Set[Path]],
                             user_mentions: List[str] = None) -> Dict[Path, float]:
        """
        Calculate file importance using PageRank algorithm

        Args:
            code_nodes: Code nodes
            dependency_graph: Dependency graph
            user_mentions: User-mentioned identifiers

        Returns:
            File importance scores
        """
        pass

    def get_parser(self, file_path: Path) -> Optional[Any]:
        """Get appropriate parser based on file path"""
        for parser in self.parsers.values():
            if hasattr(parser, 'can_parse') and parser.can_parse(file_path):
                return parser
        return None


class DefaultASTAnalyzer(ASTAnalyzer):
    """Default code analyzer implementation"""

    def __init__(self, parsers: Dict[str, Any]):
        super().__init__(parsers)

    def analyze_repository(self, root_path: Path,
                           file_patterns: Optional[List[str]] = None,
                           ignore_patterns: Optional[List[str]] = None,
                           enable_dependency_graph: bool = False) -> RepositoryMap:
        """Analyze entire code repository"""
        t_start = time.perf_counter()
        logger.info(f"[analyze_repository] Starting: root_path={root_path}, enable_dependency_graph={enable_dependency_graph}")

        # Scan files
        t0 = time.perf_counter()
        files_to_analyze = self._scan_files(root_path, file_patterns, ignore_patterns)
        t_scan = time.perf_counter() - t0
        logger.info(f"[analyze_repository] _scan_files: {len(files_to_analyze)} files, {t_scan:.3f}s")

        # Analyze files using tools (tree-sitter, pageIndex, etc.)
        t0 = time.perf_counter()
        code_nodes = self.analyze_files(files_to_analyze)
        t_parse = time.perf_counter() - t0
        logger.info(f"[analyze_repository] analyze_files: {len(code_nodes)} nodes, {t_parse:.3f}s")

        # Build dependency graph (skip when disabled - saves ~96s for 1k+ files)
        if enable_dependency_graph:
            t0 = time.perf_counter()
            dependency_graph = self.build_dependency_graph(code_nodes)
            t_dep = time.perf_counter() - t0
            n_edges = sum(len(t) for t in dependency_graph.values())
            logger.info(f"[analyze_repository] build_dependency_graph: {len(dependency_graph)} nodes, {n_edges} edges, {t_dep:.3f}s")

            t0 = time.perf_counter()
            pagerank_scores = self.calculate_importance(code_nodes, dependency_graph)
            t_pagerank = time.perf_counter() - t0
            logger.info(f"[analyze_repository] calculate_importance: {len(pagerank_scores)} scores, {t_pagerank:.3f}s")
        else:
            dependency_graph = {}
            n = len(code_nodes)
            pagerank_scores = {path: 1.0 / n for path in code_nodes.keys()} if n else {}
            t_dep = t_pagerank = 0.0
            logger.info(f"[analyze_repository] build_dependency_graph: skipped (enable_dependency_graph=False)")
            logger.info(f"[analyze_repository] calculate_importance: {len(pagerank_scores)} scores (uniform), 0.000s")

        # Build three-layer structure
        t0 = time.perf_counter()
        logic_layer = self._build_logic_layer(root_path, code_nodes, dependency_graph)
        t_logic = time.perf_counter() - t0
        logger.info(f"[analyze_repository] _build_logic_layer: {t_logic:.3f}s")

        t0 = time.perf_counter()
        skeleton_layer = self._build_skeleton_layer(code_nodes)
        t_skeleton = time.perf_counter() - t0
        n_skeletons = len(skeleton_layer.file_skeletons)
        logger.info(f"[analyze_repository] _build_skeleton_layer: {n_skeletons} skeletons, {t_skeleton:.3f}s")

        t0 = time.perf_counter()
        implementation_layer = self._build_implementation_layer(code_nodes)
        t_impl = time.perf_counter() - t0
        n_symbols = sum(len(n.symbols) for n in implementation_layer.code_nodes.values())
        logger.info(f"[analyze_repository] _build_implementation_layer: {len(implementation_layer.code_nodes)} files, {n_symbols} symbols, {len(skeleton_layer.key_symbols)} key_symbols (in skeleton), {t_impl:.3f}s")

        t_total = time.perf_counter() - t_start
        logger.info(
            f"[analyze_repository] Done: total={t_total:.3f}s "
            f"(scan={t_scan:.2f}s parse={t_parse:.2f}s dep={t_dep:.2f}s pagerank={t_pagerank:.2f}s "
            f"logic={t_logic:.2f}s skeleton={t_skeleton:.2f}s impl={t_impl:.2f}s)"
        )

        return RepositoryMap(
            logic_layer=logic_layer,
            skeleton_layer=skeleton_layer,
            implementation_layer=implementation_layer,
            pagerank_scores=pagerank_scores,
            last_updated=time.time()
        )

    def analyze_files(self, file_paths: List[Path]) -> Dict[Path, CodeNode]:
        """Analyze specified file list"""
        code_nodes = {}

        for file_path in file_paths:
            try:
                parser = self.get_parser(file_path)
                if parser:
                    code_node = parser.parse_file(file_path)
                    code_nodes[file_path] = code_node
                    logger.debug(f"Parsed file: {file_path}")
                else:
                    logger.warning(f"No suitable parser found: {file_path}")
            except Exception as e:
                logger.error(f"Failed to parse file {file_path}: {e}")

        return code_nodes

    def build_dependency_graph(self, code_nodes: Dict[Path, CodeNode]) -> Dict[Path, Set[Path]]:
        """Build file dependency graph"""
        dependency_graph = defaultdict(set)

        for file_path, node in code_nodes.items():
            # Build dependency relationships based on import statements
            for import_name in node.imports:
                # Try to resolve import to file paths
                target_files = self._resolve_import(import_name, code_nodes.keys())
                for target_file in target_files:
                    if target_file != file_path:
                        dependency_graph[file_path].add(target_file)
                        code_nodes[target_file].dependents.add(file_path)

            # Build dependency relationships based on symbol references
            for reference in node.references:
                # Find files that define this symbol
                target_files = self._find_symbol_definition(reference.symbol_name, code_nodes)
                for target_file in target_files:
                    if target_file != file_path:
                        dependency_graph[file_path].add(target_file)
                        code_nodes[target_file].dependents.add(file_path)

        return dict(dependency_graph)

    def calculate_importance(self, code_nodes: Dict[Path, CodeNode],
                             dependency_graph: Dict[Path, Set[Path]],
                             user_mentions: List[str] = None) -> Dict[Path, float]:
        """Calculate file importance using PageRank algorithm"""
        if not code_nodes:
            return {}

        # Create NetworkX graph
        G = nx.DiGraph()

        # Add nodes
        for file_path in code_nodes.keys():
            G.add_node(str(file_path))

        # Add edges (dependency relationships)
        for source, targets in dependency_graph.items():
            for target in targets:
                G.add_edge(str(source), str(target))

        # Calculate basic PageRank scores
        try:
            pagerank = nx.pagerank(G, alpha=0.85, max_iter=100)
        except nx.PowerIterationFailedConvergence:
            logger.warning("PageRank calculation did not converge, using uniform distribution")
            pagerank = {str(path): 1.0 / len(code_nodes) for path in code_nodes.keys()}

        # Apply weight adjustments
        weighted_scores = {}
        for file_path, node in code_nodes.items():
            base_score = pagerank.get(str(file_path), 0.0)

            # User mention weight
            mention_weight = 1.0
            if user_mentions:
                for symbol in node.symbols:
                    if any(mention.lower() in symbol.name.lower() for mention in user_mentions):
                        mention_weight = 10.0
                        break

            weighted_scores[file_path] = base_score * mention_weight

        return weighted_scores

    def _scan_files(self, root_path: Path,
                    file_patterns: Optional[List[str]] = None,
                    ignore_patterns: Optional[List[str]] = None) -> List[Path]:
        """Scan directory to get files that need to be analyzed"""
        files = []
        ignore_patterns = ignore_patterns or ['.git', '__pycache__', 'node_modules', '.pytest_cache']

        def should_ignore(path: Path) -> bool:
            path_str = str(path)
            return any(pattern in path_str for pattern in ignore_patterns)

        def collect_files(directory: Path):
            if should_ignore(directory):
                return

            for item in directory.iterdir():
                if item.is_file():
                    if self.get_parser(item) and not should_ignore(item):
                        files.append(item)
                elif item.is_dir():
                    collect_files(item)

        collect_files(root_path)
        return files

    def _resolve_import(self, import_name: str, available_files: List[Path]) -> List[Path]:
        """Resolve import statement to actual file paths"""
        # Simplified implementation: based on name matching
        result = []
        import_parts = import_name.replace('.', '/').split('/')

        for file_path in available_files:
            file_str = str(file_path).lower()
            if any(part.lower() in file_str for part in import_parts if part):
                result.append(file_path)

        return result

    def _find_symbol_definition(self, symbol_name: str,
                                code_nodes: Dict[Path, CodeNode]) -> List[Path]:
        """Find files where symbol is defined"""
        result = []

        for file_path, node in code_nodes.items():
            for symbol in node.symbols:
                if symbol.name == symbol_name or symbol.full_name == symbol_name:
                    result.append(file_path)

        return result

    def _build_logic_layer(self, root_path: Path, code_nodes: Dict[Path, CodeNode],
                           dependency_graph: Dict[Path, Set[Path]]) -> LogicLayer:
        """Build L1 logic layer (project structure + dependency graph only)"""
        project_structure = self._build_project_structure(root_path, code_nodes.keys())
        return LogicLayer(
            project_structure=project_structure,
            dependency_graph=dependency_graph
        )

    def _build_skeleton_layer(self, code_nodes: Dict[Path, CodeNode]) -> SkeletonLayer:
        """Build L2 skeleton layer"""
        file_skeletons = {}
        symbol_signatures = {}
        line_mappings = {}
        call_graph = {}

        for file_path, node in code_nodes.items():
            parser = self.get_parser(file_path)
            if parser and file_path.exists():
                try:
                    content = file_path.read_text(encoding='utf-8', errors='replace')
                    skeleton = parser.generate_skeleton(content, file_path)
                    file_skeletons[file_path] = skeleton

                    # Build symbol signature mapping
                    for symbol in node.symbols:
                        if symbol.signature:
                            symbol_signatures[symbol.full_name] = symbol.signature

                    # TODO: implement line number mapping
                    line_mappings[file_path] = {}

                except Exception as e:
                    logger.error(f"Failed to generate skeleton {file_path}: {e}")

        # Build call graph from all code_nodes (associate references with symbols by scope)
        for file_path, node in code_nodes.items():
            for symbol in node.symbols:
                calls = []
                end_line = symbol.end_line or symbol.line_number
                for ref in node.references:
                    if ref.reference_type == ReferenceType.CALL:
                        if symbol.line_number <= ref.line_number <= end_line:
                            calls.append(ref.symbol_name)
                if calls:
                    call_graph[symbol.full_name] = calls

        # Build key symbol table (classes, main functions, etc.)
        key_symbols = []
        for node in code_nodes.values():
            for symbol in node.symbols:
                if (symbol.symbol_type in [SymbolType.CLASS, SymbolType.FUNCTION] and
                        (symbol.name.startswith('main') or
                         symbol.name == '__init__' or
                         len(symbol.name) > 3)):
                    symbol_without_content = Symbol(
                        name=symbol.name,
                        symbol_type=symbol.symbol_type,
                        file_path=symbol.file_path,
                        line_number=symbol.line_number,
                        column=symbol.column,
                        end_line=symbol.end_line,
                        end_column=symbol.end_column,
                        signature=symbol.signature,
                        docstring=symbol.docstring,
                        content=None,
                        parent=symbol.parent,
                        modifiers=symbol.modifiers,
                        parameters=symbol.parameters,
                        return_type=symbol.return_type,
                        metadata=symbol.metadata
                    )
                    key_symbols.append(symbol_without_content)

        return SkeletonLayer(
            file_skeletons=file_skeletons,
            symbol_signatures=symbol_signatures,
            line_mappings=line_mappings,
            call_graph=call_graph,
            key_symbols=key_symbols
        )

    def _build_implementation_layer(self, code_nodes: Dict[Path, CodeNode]) -> ImplementationLayer:
        """Build L3 implementation layer (code_nodes only; key_symbols moved to SkeletonLayer)"""
        return ImplementationLayer(code_nodes=code_nodes)

    def _build_project_structure(self, root_path: Path, file_paths: List[Path]) -> Dict[str, Any]:
        """Build project directory structure. Uses relative paths; leaf files store empty string (no full path)."""
        structure = {}

        for file_path in file_paths:
            try:
                rel_path = file_path.relative_to(root_path)
            except ValueError:
                rel_path = file_path
            parts = rel_path.parts
            current = structure

            for part in parts[:-1]:  # All parts except filename
                if part not in current:
                    current[part] = {}
                current = current[part]

            # Add file - use empty string, do not store full path
            if isinstance(current, dict):
                current[parts[-1]] = ""

        return structure


class ASTContextBuilder:
    """Main repository analyzer class"""

    def __init__(self, ast_analyzer: ASTAnalyzer):
        self.ast_analyzer = ast_analyzer

    def analyze(self,
                root_path: Path,
                file_patterns: Optional[List[str]] = None,
                ignore_patterns: Optional[List[str]] = None,
                enable_dependency_graph: bool = False) -> RepositoryMap:
        """
        Perform complete repository analysis

        Args:
            root_path: Repository root directory
            file_patterns: File inclusion patterns
            ignore_patterns: File ignore patterns
            enable_dependency_graph: Whether to build dependency graph and PageRank (costly for large repos)

        Returns:
            Complete repository mapping
        """
        logger.info(f"Starting repository analysis: {root_path}, enable_dependency_graph={enable_dependency_graph}")

        # Execute code analysis
        repo_map = self.ast_analyzer.analyze_repository(
            root_path, file_patterns, ignore_patterns, enable_dependency_graph
        )

        logger.info("Repository analysis completed")
        return repo_map

    def recall(self,
               repo_map: RepositoryMap,
               user_query: str = "",
               max_tokens: int = 8000,
               context_layers: Optional[List[str]] = None) -> str:
        """
        Multi-layered code context recall
        Referencing aider's layered context management design

        Args:
            repo_map: Repository mapping
            user_query: User query
            max_tokens: Maximum number of tokens
            context_layers: Specified context layers to include

        Returns:
            Formatted layered context string
        """
        logger.info(f"🚀 Starting multi-layered code context recall")
        logger.info(f"📝 User query: '{user_query}'")
        logger.info(f"🎯 Max tokens: {max_tokens}")
        logger.info(f"📋 Specified layers: {context_layers}")

        # Extract keywords from user query
        user_mentions = [user_query]
        logger.info(f"🔍 Extracted query keywords: {user_mentions}")

        # Build layered context
        logger.info(f"🏗️ Starting to build layered context...")
        logger.info('repo_map: ', repo_map)
        layered_context = self._build_layered_context(
            repo_map, user_mentions, context_layers
        )
        logger.info(f"📊 Layered context build result: {len(layered_context)} layers")
        return layered_context
        # Optimize token budget
        # logger.info(f"⚖️ Starting token budget optimization...")
        # final_context = self._optimize_token_budget(layered_context, max_tokens)
        # logger.info(f"✅ Context recall completed, final length: {len(final_context)} characters")

        # return final_context

    def _build_layered_context(self,
                               repo_map: RepositoryMap,
                               user_mentions: List[str],
                               context_layers: List[str]) -> Dict[str, str]:
        """Build layered context content"""

        logger.info(f"🏗️ Starting to build layered context")
        logger.info(f"📋 Requested layers: {context_layers}")
        logger.info(f"🔍 User query: {user_mentions}")

        layer_generators = {
            "skeleton": self._generate_skeleton_context,
            "implementation": self._generate_implementation_context,
        }

        layered_content = {}
        for layer_idx, layer_name in enumerate(context_layers):
            logger.debug(f"🔄 Processing layer [{layer_idx+1}/{len(context_layers)}]: {layer_name}")

            if layer_name in layer_generators:
                try:
                    logger.debug(f"  ⚙️ Calling generator: {layer_generators[layer_name].__name__}")
                    content = layer_generators[layer_name](repo_map, user_mentions)
                    logger.debug(f"  📏 Generated content length: {len(content)} characters")

                    # Show content preview (first 200 characters)
                    preview = content[:200].replace('\n', '\\n')
                    logger.debug(f"  👀 Content preview: {preview}...")

                    if content.strip():  # Only add non-empty content
                        layered_content[layer_name] = content
                        logger.debug(f"  ✅ Layer {layer_name} content added")
                    else:
                        logger.debug(f"  ⚠️ Layer {layer_name} generated empty content, skipping")

                except Exception as e:
                    import traceback
                    tb_str = traceback.format_exc()
                    logger.debug(f"  ❌ Failed to generate {layer_name} layer content: {e}\n{tb_str}")
                    logger.debug(f"  📋 Error traceback: {tb_str}")
            else:
                logger.debug(f"  ⚠️ Unknown layer type: {layer_name}")
                logger.debug(f"  📝 Available layer types: {list(layer_generators.keys())}")

        logger.info(f"🏁 Layered context construction completed")
        logger.info(f"📊 Successfully generated layers: {list(layered_content.keys())}")
        total_length = sum(len(content) for content in layered_content.values())
        logger.info(f"📏 Total content length: {total_length} characters")

        return layered_content

    def _generate_skeleton_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """Generate skeleton context as a JSON array string of matched items."""
        import json
        import re

        # Build list of (file_path, score) from skeleton_layer (file_skeletons + key_symbols)
        relevant_files = []
        file_scores: Dict[Path, float] = {}

        for file_path, skeleton_text in repo_map.skeleton_layer.file_skeletons.items():
            score = 0.0
            if user_mentions:
                for mention in user_mentions:
                    try:
                        if re.search(mention, file_path.name, re.IGNORECASE):
                            score += 10
                        if skeleton_text and re.search(mention, skeleton_text, re.IGNORECASE):
                            score += 5
                    except re.error:
                        mention_lower = mention.lower()
                        if mention_lower in file_path.name.lower():
                            score += 10
                        if skeleton_text and mention_lower in skeleton_text.lower():
                            score += 5
            else:
                score = len(skeleton_text) if skeleton_text else 0
            file_scores[file_path] = file_scores.get(file_path, 0) + score

        # Retrieve key_symbols from SkeletonLayer: boost score when user mentions match key symbol names
        for symbol in repo_map.skeleton_layer.key_symbols:
            if user_mentions:
                for mention in user_mentions:
                    try:
                        if re.search(mention, symbol.name, re.IGNORECASE):
                            file_scores[symbol.file_path] = file_scores.get(symbol.file_path, 0) + 8
                            break
                        if symbol.full_name and re.search(mention, symbol.full_name, re.IGNORECASE):
                            file_scores[symbol.file_path] = file_scores.get(symbol.file_path, 0) + 8
                            break
                    except re.error:
                        mention_lower = mention.lower()
                        if mention_lower in symbol.name.lower():
                            file_scores[symbol.file_path] = file_scores.get(symbol.file_path, 0) + 8
                            break
                        if symbol.full_name and mention_lower in symbol.full_name.lower():
                            file_scores[symbol.file_path] = file_scores.get(symbol.file_path, 0) + 8
                            break
            else:
                # No mentions: ensure files with key symbols get non-zero score
                file_scores[symbol.file_path] = file_scores.get(symbol.file_path, 0) + 1

        relevant_files = [(fp, s) for fp, s in file_scores.items() if s > 0 or not user_mentions]
        relevant_files.sort(key=lambda x: x[1], reverse=True)

        items: List[Dict[str, Any]] = []
        for file_path, score in relevant_files[:3]:
            skeleton = repo_map.skeleton_layer.get_skeleton(file_path)
            # Include key symbols for this file from SkeletonLayer
            key_syms = [s for s in repo_map.skeleton_layer.key_symbols if s.file_path == file_path]
            item: Dict[str, Any] = {
                "type": "skeleton_match",
                "file_path": str(file_path),
                "file_name": file_path.name,
                "score": score,
                "skeleton": skeleton.strip() if skeleton else "",
                "key_symbols": [
                    {
                        "name": s.name,
                        "full_name": s.full_name,
                        "symbol_type": s.symbol_type.value if hasattr(s.symbol_type, "value") else str(s.symbol_type or ""),
                        "line_number": s.line_number,
                        "end_line": s.end_line,
                    }
                    for s in key_syms[:5]
                ],
            }
            items.append(item)

        return json.dumps(items, ensure_ascii=False)

    def _get_symbols(self, node: Any) -> List:
        """Safely get symbols from CodeNode or dict (e.g. from JSON deserialization)."""
        if hasattr(node, 'symbols'):
            return node.symbols
        if isinstance(node, dict):
            return node.get('symbols', [])
        return []

    def _get_attr(self, obj: Any, attr: str, default: Any = None) -> Any:
        """Safely get attribute from object or dict."""
        if hasattr(obj, attr):
            return getattr(obj, attr, default)
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return default

    def _generate_implementation_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """
        Generate detailed implementation layer context based on regex matching in symbol content
        Includes matching source file content with corresponding line numbers

        Args:
            repo_map: Repository mapping
            user_mentions: List of user-mentioned regex patterns

        Returns:
            JSON array string where each element is one matched symbol item
        """
        import json
        import re

        # Add detailed debugging logs
        logger.info(f"🔍 Starting implementation layer context generation")
        logger.info(f"📝 User query patterns: {user_mentions}")

        # Search for matching symbols from implementation_layer code_nodes
        matches = []

        # Check repo_map structure
        logger.info(f"📊 Repository mapping structure check:")
        logger.info(f"  - repo_map exists: {repo_map is not None}")
        logger.info(f"  - implementation_layer exists: {hasattr(repo_map, 'implementation_layer') and repo_map.implementation_layer is not None}")

        if not repo_map.implementation_layer or not repo_map.implementation_layer.code_nodes:
            logger.warning(f"⚠️ Missing implementation layer data:")
            logger.warning(f"  - implementation_layer: {repo_map.implementation_layer}")
            if repo_map.implementation_layer:
                logger.warning(f"  - code_nodes: {repo_map.implementation_layer.code_nodes}")
            return json.dumps([], ensure_ascii=False)

        # Statistics for implementation layer data (support both CodeNode and dict)
        total_files = len(repo_map.implementation_layer.code_nodes)
        total_symbols = sum(len(self._get_symbols(node)) for node in repo_map.implementation_layer.code_nodes.values())
        symbols_with_content = 0

        logger.info(f"📈 Implementation layer statistics:")
        logger.info(f"  - Total files: {total_files}")
        logger.debug(f"  - Total symbols: {total_symbols}")

        # Iterate through all symbols in code_nodes (support both CodeNode and dict)
        for file_idx, (file_path, code_node) in enumerate(repo_map.implementation_layer.code_nodes.items()):
            symbols_list = self._get_symbols(code_node)
            logger.debug(f"🔍 Processing file [{file_idx+1}/{total_files}]: {file_path}")
            logger.debug(f"  - Symbol count: {len(symbols_list)}")

            for symbol_idx, symbol in enumerate(symbols_list):
                logger.debug(f'symbol: {symbol}')
                content = self._get_attr(symbol, 'content')
                if not content:  # Skip symbols without content
                    logger.debug(f"  - Skipping symbol [{symbol_idx+1}] {self._get_attr(symbol, 'name')}: no content")
                    continue

                symbols_with_content += 1
                symbol_type_val = self._get_attr(symbol, 'symbol_type')
                st_str = symbol_type_val.value if hasattr(symbol_type_val, 'value') else str(symbol_type_val or '')
                logger.debug(f"  - Checking symbol [{symbol_idx+1}] {self._get_attr(symbol, 'name')} ({st_str})")
                logger.debug(f"    Content length: {len(content)} characters")

                symbol_score = 0.0
                match_details = []

                # Search using regex in symbol.content
                for pattern_idx, mention_pattern in enumerate(user_mentions):
                    logger.debug(f"    🔎 Applying pattern [{pattern_idx+1}]: '{mention_pattern}'")

                    try:
                        # Search in symbol content
                        content_matches = re.finditer(mention_pattern, content, re.IGNORECASE | re.MULTILINE)
                        content_match_count = len(list(content_matches))
                        logger.debug(f"      Content match count: {content_match_count}")

                        if content_match_count > 0:
                            symbol_score += content_match_count * 15.0  # High score for content matches
                            match_details.append(f"Content matches: {content_match_count}x")
                            logger.debug(f"      ✅ Content match +{content_match_count * 15.0} points")

                        # Search in symbol signature (if available)
                        signature = self._get_attr(symbol, 'signature')
                        if signature:
                            signature_match = re.search(mention_pattern, signature, re.IGNORECASE)
                            logger.debug(f"      Signature match: {signature_match is not None}")
                            if signature_match:
                                symbol_score += 12.0
                                match_details.append("Signature match")
                                logger.debug(f"      ✅ Signature match +12.0 points")

                        # Search in docstring (if available)
                        docstring = self._get_attr(symbol, 'docstring')
                        if docstring:
                            docstring_match = re.search(mention_pattern, docstring, re.IGNORECASE)
                            logger.debug(f"      Docstring match: {docstring_match is not None}")
                            if docstring_match:
                                symbol_score += 8.0
                                match_details.append("Docstring match")
                                logger.debug(f"      ✅ Docstring match +8.0 points")

                        # Search in symbol name
                        name_match = re.search(mention_pattern, str(self._get_attr(symbol, 'name') or ''), re.IGNORECASE)
                        logger.debug(f"      Name match: {name_match is not None}")
                        if name_match:
                            symbol_score += 5.0
                            match_details.append("Name match")
                            logger.debug(f"      ✅ Name match +5.0 points")

                    except re.error as e:
                        # If regex is invalid, log error and skip
                        logger.debug(f"❌ Invalid regex '{mention_pattern}': {e}")
                        continue

                # If there are matches, add to results
                if symbol_score > 0:
                    matches.append((symbol, symbol_score, match_details))
                    logger.debug(f"🎯 Found matching symbol: {self._get_attr(symbol, 'name')} (score: {symbol_score:.1f}, details: {match_details})")

        # Final statistics
        logger.info(f"📊 Search completion statistics:")
        logger.debug(f"  - Symbols with content: {symbols_with_content}")
        logger.info(f"  - Matching symbols: {len(matches)}")

        # Sort by relevance score
        matches.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"🏆 Top 5 matches after sorting:")
        for i, (symbol, score, details) in enumerate(matches[:5]):
            logger.info(f"  {i+1}. {self._get_attr(symbol, 'name')} - {score:.1f} points ({', '.join(details)})")

        # Generate JSON array items
        result_items: List[Dict[str, Any]] = []
        if matches:
            logger.info(f"📝 Generating context content, showing top 8 matching results")
            for symbol, score, match_details in matches[:8]:  # Show at most 8 matching results
                st = self._get_attr(symbol, 'symbol_type')
                st_str = st.value if hasattr(st, 'value') else str(st or '')
                result_items.append({
                    "type": "implementation_match",
                    "name": self._get_attr(symbol, 'name'),
                    "symbol_type": st_str,
                    "file_path": str(self._get_attr(symbol, 'file_path') or ""),
                    "line_number": self._get_attr(symbol, 'line_number'),
                    "end_line": self._get_attr(symbol, 'end_line'),
                    "score": score,
                    "match_details": match_details,
                    "signature": self._get_attr(symbol, 'signature') or "",
                    "docstring": self._get_attr(symbol, 'docstring') or "",
                    "content": self._get_attr(symbol, 'content') or "",
                })
        else:
            logger.warning(f"⚠️ No matching implementation code found")

        result = json.dumps(result_items, ensure_ascii=False)
        logger.info(f"✅ Implementation layer context generation complete, total length: {len(result)} characters")
        return result

    # add code line
    def _get_symbol_source_code(self, repo_map: RepositoryMap, symbol: Symbol) -> Optional[str]:
        """Get symbol source code content with line numbers prefixed to each line"""
        try:
            # Prefer using content field in Symbol object
            if hasattr(symbol, 'content') and symbol.content:
                # If Symbol already contains code content, use directly and add line numbers
                content_lines = symbol.content.split('\n')
                numbered_lines = []
                for i, line in enumerate(content_lines):
                    line_number = symbol.line_number + i  # Calculate from symbol's starting line number
                    numbered_lines.append(f"{line_number}→{line}")

                return '\n'.join(numbered_lines)

            # Fallback: read directly from filesystem (new ImplementationLayer no longer caches file_contents)
            file_path = symbol.file_path
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()

                # Extract source code lines corresponding to the symbol
                lines = file_content.split('\n')
                start_idx = max(0, symbol.line_number - 1)  # Convert to 0-based index
                end_idx = min(len(lines), symbol.end_line) if symbol.end_line > 0 else start_idx + 1

                # Add line number prefix to each line
                numbered_lines = []
                for i, line in enumerate(lines[start_idx:end_idx]):
                    line_number = start_idx + i + 1  # Convert back to 1-based line number
                    # Direct concatenation without additional formatting spaces
                    numbered_lines.append(f"{line_number}→{line}")

                source_code = '\n'.join(numbered_lines)
                return source_code.strip() if source_code else None
            else:
                return None

        except Exception as e:
            logger.warning(f"Failed to get symbol source code {symbol.name}: {e}")
            return None

    def _has_structured_naming(self, name: str) -> bool:
        """Check if it's structured naming (camel case, underscore, etc.)"""
        import re

        # camelCase or PascalCase
        if re.search(r'[a-z][A-Z]', name):
            return True

        # snake_case
        if '_' in name and any(c.isalpha() for c in name):
            return True

        # CONSTANT_CASE
        if name.isupper() and '_' in name:
            return True

        # Naming with numbers
        if re.search(r'\w+\d+', name):
            return True

        return False

    def _extract_mentions(self, query: str) -> List[str]:
        """
        Extract matching strings from user query
        Return query as regex matching string directly
        """
        return [query]
