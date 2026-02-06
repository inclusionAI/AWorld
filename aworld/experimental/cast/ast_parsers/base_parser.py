"""
AWorld AST Framework - Unified Parser Base Class Based on Tree-sitter
=====================================================================

All parsers are implemented based on Tree-sitter, providing a unified interface and high-performance parsing capabilities.
"""

from abc import ABC, abstractmethod
from importlib import resources
from pathlib import Path
from typing import List, Set, Optional, Tuple

from grep_ast import TreeContext
from grep_ast.tsl import get_language, get_parser

from ..utils import logger
from ..models import (
    Symbol, Reference, CodeNode,
    SymbolType, ReferenceType
)


class BaseParser(ABC):
    """
    Unified parser base class based on Tree-sitter

    All parsers for specific languages should inherit from this class
    """

    def __init__(self, language: str, file_extensions: Set[str]):
        """
        Initialize parser

        Args:
            language: Programming language name (e.g., 'python', 'javascript')
            file_extensions: Set of supported file extensions (e.g., {'.py', '.pyi'})
        """
        self.language = language
        self.file_extensions = file_extensions

        # Cache
        self._parser_cache = None
        self._language_cache = None
        self._query_cache = None

    def can_parse(self, file_path: Path) -> bool:
        """Check if the specified file can be parsed"""
        return file_path.suffix.lower() in self.file_extensions

    def parse_file(self, file_path: Path) -> CodeNode:
        """
        Parse file and return CodeNode

        Args:
            file_path: Path to the file to parse

        Returns:
            CodeNode containing symbol and reference information
        """
        if not file_path.exists():
            logger.warning(f"File does not exist: {file_path}")
            return CodeNode(file_path=file_path)

        try:
            content = file_path.read_text(encoding='utf-8')

            # Get Tree-sitter components
            parser = self._get_parser()
            language = self._get_language()

            if not parser or not language:
                logger.warning(f"Unable to get {self.language} parser")
                return CodeNode(file_path=file_path)

            # Parse code
            tree = parser.parse(bytes(content, "utf-8"))

            # Extract symbols and references
            symbols, references = self._extract_symbols_and_references(
                tree, language, content, file_path
            )

            # Extract imports and exports
            imports = self._extract_imports(tree, content)
            exports = self._extract_exports(tree, content)

            return CodeNode(
                file_path=file_path,
                symbols=symbols,
                references=references,
                imports=imports,
                exports=exports,
                last_modified=file_path.stat().st_mtime,
                metadata={'language': self.language}
            )

        except Exception as e:
            logger.error(f"Failed to parse file {file_path}: {e}")
            return CodeNode(file_path=file_path)

    def generate_skeleton(self, content: str, file_path: Path) -> str:
        """
        Generate code skeleton using TreeContext for intelligent context extraction

        Args:
            content: File content
            file_path: File path

        Returns:
            Formatted code skeleton string
        """
        try:
            # Use TreeContext to generate skeleton
            context = TreeContext(
                filename=str(file_path),
                code=content,
                color=False,
                line_number=True,
                child_context=True,
                last_line=True,
                margin=0,
                mark_lois=False,
                loi_pad=0
            )

            # Get line numbers of all definitions
            symbols = self._get_symbols_quick(content, file_path)
            definition_lines = [
                symbol.line_number for symbol in symbols
                if symbol.symbol_type in [SymbolType.CLASS, SymbolType.FUNCTION, SymbolType.METHOD]
            ]

            if definition_lines:
                context.add_lines_of_interest(definition_lines)
                context.add_context()

            return context.format()

        except Exception as e:
            logger.error(f"Failed to generate skeleton {file_path}: {e}")
            return f"# Skeleton generation failed: {e}\n"

    def extract_symbols(self, content: str, file_path: Path) -> List[Symbol]:
        """Extract symbol definitions"""
        try:
            parser = self._get_parser()
            language = self._get_language()

            if not parser or not language:
                return []

            tree = parser.parse(bytes(content, "utf-8"))
            symbols, _ = self._extract_symbols_and_references(
                tree, language, content, file_path
            )
            return symbols
        except Exception as e:
            logger.error(f"Failed to extract symbols: {e}")
            return []

    def extract_references(self, content: str, file_path: Path) -> List[Reference]:
        """Extract symbol references"""
        try:
            parser = self._get_parser()
            language = self._get_language()

            if not parser or not language:
                return []

            tree = parser.parse(bytes(content, "utf-8"))
            _, references = self._extract_symbols_and_references(
                tree, language, content, file_path
            )
            return references
        except Exception as e:
            logger.error(f"Failed to extract references: {e}")
            return []

    def get_imports(self, content: str) -> List[str]:
        """Extract import statements"""
        try:
            parser = self._get_parser()
            if not parser:
                return []

            tree = parser.parse(bytes(content, "utf-8"))
            return self._extract_imports(tree, content)
        except Exception as e:
            logger.error(f"Failed to extract imports: {e}")
            return []

    def get_exports(self, content: str) -> List[str]:
        """Extract export statements"""
        try:
            parser = self._get_parser()
            if not parser:
                return []

            tree = parser.parse(bytes(content, "utf-8"))
            return self._extract_exports(tree, content)
        except Exception as e:
            logger.error(f"Failed to extract exports: {e}")
            return []

    # ===============================
    # Tree-sitter Internal Implementation Methods
    # ===============================

    def _get_parser(self):
        """Get or cache Tree-sitter parser"""
        if self._parser_cache is None:
            try:
                self._parser_cache = get_parser(self.language)
            except Exception as e:
                logger.error(f"Failed to get {self.language} parser: {e}")
                return None
        return self._parser_cache

    def _get_language(self):
        """Get or cache Tree-sitter language"""
        if self._language_cache is None:
            try:
                self._language_cache = get_language(self.language)
            except Exception as e:
                logger.error(f"Failed to get {self.language} language: {e}")
                return None
        return self._language_cache

    def _get_query_content(self) -> Optional[str]:
        """Get query file content"""
        if self._query_cache is not None:
            return self._query_cache

        try:
            # Try to use built-in query files (aider style)
            query_paths = [
                f"queries/tree-sitter-language-pack/{self.language}-tags.scm",
                f"queries/tree-sitter-languages/{self.language}-tags.scm",
            ]

            for query_path in query_paths:
                try:
                    query_resource = resources.files("aider").joinpath(query_path)
                    if query_resource.exists():
                        self._query_cache = query_resource.read_text()
                        return self._query_cache
                except:
                    continue

            # Use local query file
            local_query = Path(__file__).parent / "queries" / f"{self.language}-tags.scm"
            if local_query.exists():
                self._query_cache = local_query.read_text()
                return self._query_cache

        except Exception as e:
            logger.debug(f"Failed to get query file: {e}")

        # Return default query (implemented by subclasses)
        self._query_cache = self._get_default_query()
        return self._query_cache

    @abstractmethod
    def _get_default_query(self) -> str:
        """Get default query string (must be implemented by subclasses)"""
        pass

    def _extract_symbols_and_references(self, tree, language, content: str,
                                       file_path: Path) -> Tuple[List[Symbol], List[Reference]]:
        """Extract symbol definitions and references"""
        symbols = []
        references = []

        query_content = self._get_query_content()
        if not query_content:
            logger.debug(f"No query file: {self.language}")
            return symbols, references

        try:
            query = language.query(query_content)
            captures = query.captures(tree.root_node)
            lines = content.split('\n')

            # Process captures
            for capture_name, nodes in captures.items():
                for node in nodes:
                    line_num = node.start_point[0] + 1
                    col_num = node.start_point[1]

                    if capture_name.startswith('name.definition'):
                        symbol_type = self._map_definition_type(capture_name)
                        symbol_name = node.text.decode('utf-8')

                        # Get parent context
                        parent_name = self._find_parent_symbol(node, content)

                        # Extract signature and documentation
                        signature = self._extract_signature(node, lines, line_num)
                        docstring = self._extract_docstring(node, lines, line_num)

                        symbol = Symbol(
                            name=symbol_name,
                            symbol_type=symbol_type,
                            file_path=file_path,
                            line_number=line_num,
                            column=col_num,
                            end_line=node.end_point[0] + 1,
                            signature=signature,
                            docstring=docstring,
                            parent=parent_name,
                            metadata={'capture': capture_name, 'language': self.language}
                        )
                        symbols.append(symbol)

                    elif capture_name.startswith('name.reference'):
                        ref_type = self._map_reference_type(capture_name)
                        ref_name = node.text.decode('utf-8')

                        reference = Reference(
                            symbol_name=ref_name,
                            reference_type=ref_type,
                            file_path=file_path,
                            line_number=line_num,
                            column=col_num,
                            metadata={'capture': capture_name, 'language': self.language}
                        )
                        references.append(reference)

        except Exception as e:
            logger.error(f"Failed to extract symbols and references: {e}")

        return symbols, references

    def _map_definition_type(self, capture_name: str) -> SymbolType:
        """Map capture name to symbol type"""
        if 'class' in capture_name:
            return SymbolType.CLASS
        elif 'function' in capture_name:
            return SymbolType.FUNCTION
        elif 'method' in capture_name:
            return SymbolType.METHOD
        elif 'constant' in capture_name:
            return SymbolType.CONSTANT
        elif 'variable' in capture_name:
            return SymbolType.VARIABLE
        else:
            return SymbolType.FUNCTION

    def _map_reference_type(self, capture_name: str) -> ReferenceType:
        """Map capture name to reference type"""
        if 'call' in capture_name:
            return ReferenceType.CALL
        elif 'class' in capture_name:
            return ReferenceType.INHERITANCE
        elif 'import' in capture_name:
            return ReferenceType.IMPORT
        else:
            return ReferenceType.ACCESS

    def _find_parent_symbol(self, node, content: str) -> Optional[str]:
        """Find parent symbol name"""
        current = node.parent
        while current:
            if current.type in ['class_definition', 'function_definition', 'method_definition']:
                for child in current.children:
                    if child.type == 'identifier' or child.type == 'property_identifier':
                        return child.text.decode('utf-8')
            current = current.parent
        return None

    def _extract_signature(self, node, lines: List[str], line_num: int) -> Optional[str]:
        """Extract symbol signature"""
        try:
            def_node = node.parent
            while def_node and def_node.type not in [
                'function_definition', 'class_definition', 'method_definition',
                'function_declaration', 'class_declaration'
            ]:
                def_node = def_node.parent

            if def_node:
                start_line = def_node.start_point[0]
                end_line = def_node.end_point[0]

                if start_line == end_line:
                    return lines[start_line].strip()
                else:
                    signature_lines = []
                    for i in range(start_line, min(end_line + 1, start_line + 3)):
                        line = lines[i].strip()
                        signature_lines.append(line)
                        if line.endswith(':') or line.endswith('{'):
                            break
                    return ' '.join(signature_lines)

        except Exception:
            pass

        try:
            return lines[line_num - 1].strip()
        except:
            return None

    def _extract_docstring(self, node, lines: List[str], line_num: int) -> Optional[str]:
        """Extract docstring (subclasses can override to provide more precise extraction)"""
        return None

    def _extract_imports(self, tree, content: str) -> List[str]:
        """Extract import statements (subclasses should override)"""
        return []

    def _extract_exports(self, tree, content: str) -> List[str]:
        """Extract export statements (subclasses should override)"""
        return []

    def _get_symbols_quick(self, content: str, file_path: Path) -> List[Symbol]:
        """Quickly get symbol list (for skeleton generation)"""
        try:
            parser = self._get_parser()
            language = self._get_language()

            if not parser or not language:
                return []

            tree = parser.parse(bytes(content, "utf-8"))
            symbols, _ = self._extract_symbols_and_references(
                tree, language, content, file_path
            )
            return symbols

        except Exception as e:
            logger.debug(f"Quick symbol extraction failed: {e}")
            return []