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

from .utils import logger
from .models import (
    CodeNode, RepositoryMap,
    LogicLayer, SkeletonLayer, ImplementationLayer,
    SymbolType, Symbol
)
from .models import (
    Symbol, RepositoryMap
)


class ASTAnalyzer(ABC):
    """Code analyzer abstract base class"""

    def __init__(self, parsers: Dict[str, Any]):
        self.parsers = parsers
        self.cache_enabled = True

    @abstractmethod
    def analyze_repository(self, root_path: Path,
                           file_patterns: Optional[List[str]] = None,
                           ignore_patterns: Optional[List[str]] = None) -> RepositoryMap:
        """
        Analyze entire code repository

        Args:
            root_path: Repository root directory
            file_patterns: File patterns to include
            ignore_patterns: File patterns to ignore

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
                           ignore_patterns: Optional[List[str]] = None) -> RepositoryMap:
        """Analyze entire code repository"""
        logger.info(f"Starting repository analysis: {root_path}")

        # Scan files
        files_to_analyze = self._scan_files(root_path, file_patterns, ignore_patterns)
        logger.info(f"Found {len(files_to_analyze)} files to analyze")

        # Analyze files using tools (tree-sitter, pageIndex, etc.)
        code_nodes = self.analyze_files(files_to_analyze)

        # Build dependency graph
        dependency_graph = self.build_dependency_graph(code_nodes)

        # Calculate importance
        pagerank_scores = self.calculate_importance(code_nodes, dependency_graph)

        # Build three-layer structure
        logic_layer = self._build_logic_layer(code_nodes, dependency_graph)
        skeleton_layer = self._build_skeleton_layer(code_nodes)
        implementation_layer = self._build_implementation_layer(code_nodes)

        return RepositoryMap(
            logic_layer=logic_layer,
            skeleton_layer=skeleton_layer,
            implementation_layer=implementation_layer,
            code_nodes=code_nodes,
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

    def _build_logic_layer(self, code_nodes: Dict[Path, CodeNode],
                           dependency_graph: Dict[Path, Set[Path]]) -> LogicLayer:
        """Build L1 logic layer"""
        # Build project structure
        project_structure = self._build_project_structure(code_nodes.keys())

        # Extract key symbols (without content)
        key_symbols = []
        for node in code_nodes.values():
            # Select important symbols (classes, main functions, etc.)
            for symbol in node.symbols:
                if (symbol.symbol_type in [SymbolType.CLASS, SymbolType.FUNCTION] and
                        (symbol.name.startswith('main') or
                         symbol.name == '__init__' or
                         len(symbol.name) > 3)):
                    # Create symbol copy without content
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
                        content=None,  # Don't record content
                        parent=symbol.parent,
                        modifiers=symbol.modifiers,
                        parameters=symbol.parameters,
                        return_type=symbol.return_type,
                        metadata=symbol.metadata
                    )
                    key_symbols.append(symbol_without_content)

        # Build call graph
        call_graph = {}
        # for file_path, node in code_nodes.items():
        #     for symbol in node.symbols:
        #         calls = []
        #         for ref in node.references:
        #             if ref.reference_type == ReferenceType.CALL:
        #                 calls.append(ref.symbol_name)
        #         if calls:
        #             call_graph[symbol.full_name] = calls

        return LogicLayer(
            project_structure=project_structure,
            key_symbols=key_symbols,
            call_graph=call_graph,
            dependency_graph=dependency_graph
        )

    def _build_skeleton_layer(self, code_nodes: Dict[Path, CodeNode]) -> SkeletonLayer:
        """Build L2 skeleton layer"""
        file_skeletons = {}
        symbol_signatures = {}
        line_mappings = {}

        for file_path, node in code_nodes.items():
            parser = self.get_parser(file_path)
            if parser and file_path.exists():
                try:
                    content = file_path.read_text(encoding='utf-8')
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

        return SkeletonLayer(
            file_skeletons=file_skeletons,
            symbol_signatures=symbol_signatures,
            line_mappings=line_mappings
        )

    def _build_implementation_layer(self, code_nodes: Dict[Path, CodeNode]) -> ImplementationLayer:
        """Build L3 implementation layer"""
        return ImplementationLayer(
            code_nodes=code_nodes
        )

    def _build_project_structure(self, file_paths: List[Path]) -> Dict[str, Any]:
        """Build project directory structure"""
        structure = {}

        for file_path in file_paths:
            parts = file_path.parts
            current = structure

            for part in parts[:-1]:  # All parts except filename
                if part not in current:
                    current[part] = {}
                current = current[part]

            # Add file
            if isinstance(current, dict):
                current[parts[-1]] = str(file_path)

        return structure


class ASTContextBuilder:
    """Main repository analyzer class"""

    def __init__(self, ast_analyzer: ASTAnalyzer):
        self.ast_analyzer = ast_analyzer

    def analyze(self,
                root_path: Path,
                file_patterns: Optional[List[str]] = None,
                ignore_patterns: Optional[List[str]] = None) -> RepositoryMap:
        """
        Perform complete repository analysis

        Args:
            root_path: Repository root directory
            file_patterns: File inclusion patterns
            ignore_patterns: File ignore patterns

        Returns:
            Complete repository mapping
        """
        logger.info(f"Starting repository analysis: {root_path}")

        # Execute code analysis
        repo_map = self.ast_analyzer.analyze_repository(
            root_path, file_patterns, ignore_patterns
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
            logger.info(f"🔄 Processing layer [{layer_idx+1}/{len(context_layers)}]: {layer_name}")

            if layer_name in layer_generators:
                try:
                    logger.info(f"  ⚙️ Calling generator: {layer_generators[layer_name].__name__}")
                    content = layer_generators[layer_name](repo_map, user_mentions)
                    logger.info(f"  📏 Generated content length: {len(content)} characters")

                    # Show content preview (first 200 characters)
                    preview = content[:200].replace('\n', '\\n')
                    logger.debug(f"  👀 Content preview: {preview}...")

                    if content.strip():  # Only add non-empty content
                        layered_content[layer_name] = content
                        logger.info(f"  ✅ Layer {layer_name} content added")
                    else:
                        logger.warning(f"  ⚠️ Layer {layer_name} generated empty content, skipping")

                except Exception as e:
                    logger.error(f"  ❌ Failed to generate {layer_name} layer content: {e}")
                    import traceback
                    logger.debug(f"  📋 Error traceback: {traceback.format_exc()}")
            else:
                logger.warning(f"  ⚠️ Unknown layer type: {layer_name}")
                logger.info(f"  📝 Available layer types: {list(layer_generators.keys())}")

        logger.info(f"🏁 Layered context construction completed")
        logger.info(f"📊 Successfully generated layers: {list(layered_content.keys())}")
        total_length = sum(len(content) for content in layered_content.values())
        logger.info(f"📏 Total content length: {total_length} characters")

        return layered_content

    def _generate_skeleton_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """Generate code skeleton layer context"""
        lines = ["# Code Skeleton"]

        # Generate skeleton for most relevant files
        relevant_files = []

        # Filter relevant files based on user mentions (using regex matching)
        import re
        if user_mentions:
            for file_path, node in repo_map.code_nodes.items():
                score = 0
                for mention in user_mentions:
                    try:
                        if re.search(mention, file_path.name, re.IGNORECASE):
                            score += 10
                        for symbol in node.symbols:
                            if re.search(mention, symbol.name, re.IGNORECASE):
                                score += 5
                    except re.error:
                        # If regex is invalid, fallback to simple string matching
                        mention_lower = mention.lower()
                        if mention_lower in file_path.name.lower():
                            score += 10
                        for symbol in node.symbols:
                            if mention_lower in symbol.name.lower():
                                score += 5
                if score > 0:
                    relevant_files.append((file_path, score))

        if not relevant_files:
            # If no explicitly relevant files, select top 3 files by symbol count
            relevant_files = [(fp, len(node.symbols)) for fp, node in repo_map.code_nodes.items()]

        relevant_files.sort(key=lambda x: x[1], reverse=True)

        for i, (file_path, score) in enumerate(relevant_files[:3], 1):
            lines.append(f"\n## {file_path.name}")

            # Simple skeleton generation
            node = repo_map.code_nodes[file_path]
            for symbol in node.symbols[:10]:
                lines.append(f"  {symbol.symbol_type.value} {symbol.name}")
                if symbol.signature:
                    lines.append(f"    {symbol.signature}")

        return '\n'.join(lines) + '\n'

    def _generate_implementation_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """
        Generate detailed implementation layer context based on regex matching in symbol content
        Includes matching source file content with corresponding line numbers

        Args:
            repo_map: Repository mapping
            user_mentions: List of user-mentioned regex patterns

        Returns:
            Formatted implementation layer context string with matched lines highlighted
        """
        import re
        lines = ["# Key Implementation"]

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
            lines.append("\nImplementation layer code nodes not found")
            return '\n'.join(lines) + '\n'

        # Statistics for implementation layer data
        total_files = len(repo_map.implementation_layer.code_nodes)
        total_symbols = sum(len(node.symbols) for node in repo_map.implementation_layer.code_nodes.values())
        symbols_with_content = 0

        logger.info(f"📈 Implementation layer statistics:")
        logger.info(f"  - Total files: {total_files}")
        logger.info(f"  - Total symbols: {total_symbols}")

        # Iterate through all symbols in code_nodes
        for file_idx, (file_path, code_node) in enumerate(repo_map.implementation_layer.code_nodes.items()):
            logger.info(f"🔍 Processing file [{file_idx+1}/{total_files}]: {file_path}")
            logger.info(f"  - Symbol count: {len(code_node.symbols)}")

            for symbol_idx, symbol in enumerate(code_node.symbols):
                logger.info(f'symbol: {symbol}')
                if not symbol.content:  # Skip symbols without content
                    logger.info(f"  - Skipping symbol [{symbol_idx+1}] {symbol.name}: no content")
                    continue

                symbols_with_content += 1
                logger.info(f"  - Checking symbol [{symbol_idx+1}] {symbol.name} ({symbol.symbol_type.value})")
                logger.info(f"    Content length: {len(symbol.content)} characters")

                symbol_score = 0.0
                match_details = []
                matched_lines_info = []  # New: Store matched line information

                # Search using regex in symbol.content
                for pattern_idx, mention_pattern in enumerate(user_mentions):
                    logger.info(f"    🔎 Applying pattern [{pattern_idx+1}]: '{mention_pattern}'")

                    try:
                        # Search in symbol content
                        content_matches = re.finditer(mention_pattern, symbol.content, re.IGNORECASE | re.MULTILINE)
                        content_match_count = len(list(content_matches))
                        logger.info(f"      Content match count: {content_match_count}")

                        if content_match_count > 0:
                            symbol_score += content_match_count * 15.0  # High score for content matches
                            match_details.append(f"Content matches: {content_match_count}x")
                            logger.info(f"      ✅ Content match +{content_match_count * 15.0} points")

                        # Search in symbol signature (if available)
                        if symbol.signature:
                            signature_match = re.search(mention_pattern, symbol.signature, re.IGNORECASE)
                            logger.info(f"      Signature match: {signature_match is not None}")
                            if signature_match:
                                symbol_score += 12.0
                                match_details.append("Signature match")
                                logger.info(f"      ✅ Signature match +12.0 points")

                        # Search in docstring (if available)
                        if symbol.docstring:
                            docstring_match = re.search(mention_pattern, symbol.docstring, re.IGNORECASE)
                            logger.info(f"      Docstring match: {docstring_match is not None}")
                            if docstring_match:
                                symbol_score += 8.0
                                match_details.append("Docstring match")
                                logger.info(f"      ✅ Docstring match +8.0 points")

                        # Search in symbol name
                        name_match = re.search(mention_pattern, symbol.name, re.IGNORECASE)
                        logger.info(f"      Name match: {name_match is not None}")
                        if name_match:
                            symbol_score += 5.0
                            match_details.append("Name match")
                            logger.info(f"      ✅ Name match +5.0 points")

                    except re.error as e:
                        # If regex is invalid, log error and skip
                        logger.warning(f"❌ Invalid regex '{mention_pattern}': {e}")
                        continue

                # If there are matches, add to results
                if symbol_score > 0:
                    matches.append((symbol, symbol_score, match_details))
                    logger.info(f"🎯 Found matching symbol: {symbol.name} (score: {symbol_score:.1f}, details: {match_details})")

        # Final statistics
        logger.info(f"📊 Search completion statistics:")
        logger.info(f"  - Symbols with content: {symbols_with_content}")
        logger.info(f"  - Matching symbols: {len(matches)}")

        # Sort by relevance score
        matches.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"🏆 Top 5 matches after sorting:")
        for i, (symbol, score, details) in enumerate(matches[:5]):
            logger.info(f"  {i+1}. {symbol.name} - {score:.1f} points ({', '.join(details)})")

        # Generate context content
        if matches:
            logger.info(f"📝 Generating context content, showing top 8 matching results")
            for symbol, score, match_details in matches[:8]:  # Show at most 8 matching results
                lines.append(f"\n## {symbol.name} ({symbol.symbol_type.value})")
                lines.append(f"File: {symbol.file_path}")
                lines.append(f"Line: {symbol.line_number}-{symbol.end_line}")
                lines.append(f"Match score: {score:.1f}")
                lines.append(f"Match details: {', '.join(match_details)}")

                if symbol.signature:
                    lines.append(f"\nSignature:")
                    lines.append(symbol.signature)

                if symbol.docstring:
                    lines.append(f"\nDocumentation:")
                    lines.append(symbol.docstring)

                # Show complete content of symbol (with line numbers)
                source_code = symbol.content
                if source_code:
                    lines.append(f"\nSource code:")
                    lines.append("```")
                    lines.append(source_code)
                    lines.append("```")
        else:
            logger.warning(f"⚠️ No matching implementation code found")
            lines.append("\nNo implementation code matching the query regex found")

        result = '\n'.join(lines) + '\n'
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
