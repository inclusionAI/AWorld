"""
AWorld AST Framework - HTML Parser
==================================

Tree-sitter based HTML code parser implementation.
Note: HTML may require special Tree-sitter syntax support, this provides a basic implementation.
"""

import re
from pathlib import Path
from typing import List, Optional

from ..utils import logger
from .base_parser import BaseParser


class HtmlParser(BaseParser):
    """HTML Tree-sitter parser"""

    def __init__(self):
        super().__init__(
            language="html",
            file_extensions={'.html', '.htm', '.xhtml'}
        )
        self.comment_patterns = {
            'line': None,  # HTML has no line comments
            'block': ('<!--', '-->')
        }

    def _get_default_query(self) -> str:
        """Default Tree-sitter query for HTML"""
        # Note: This is a basic query, actual HTML tree-sitter queries may need adjustment
        return '''
;; HTML elements
(element
  (start_tag
    (tag_name) @name.definition.tag) @definition.tag)

;; Elements with ID
(element
  (start_tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @name.definition.id) @definition.id))
  (#eq? @attr_name "id"))

;; Elements with class
(element
  (start_tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @name.definition.class) @definition.class))
  (#eq? @attr_name "class"))

;; JavaScript in script tags (simplified)
(element
  (start_tag (tag_name) @tag_name)
  (text) @name.definition.script
  (#eq? @tag_name "script"))

;; Links and resource references
(element
  (start_tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @name.reference.resource) @reference.resource))
  (#match? @attr_name "^(src|href|action)$"))
'''

    def _extract_docstring(self, node, lines: List[str], line_num: int) -> Optional[str]:
        """HTML has no traditional docstrings, return None"""
        return None

    def _extract_imports(self, tree, content: str) -> List[str]:
        """Extract HTML resource imports (CSS, JS, images, etc.)"""
        imports = []

        # Use regex to extract resource links (fallback approach)
        patterns = [
            r'<link[^>]+href=["\']([^"\']+)["\']',  # CSS links
            r'<script[^>]+src=["\']([^"\']+)["\']',  # JS files
            r'<img[^>]+src=["\']([^"\']+)["\']',     # Images
            r'@import\s+["\']([^"\']+)["\']'         # CSS @import
        ]

        for pattern in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                # Filter external URLs
                if not match.startswith(('http://', 'https://', '//', 'data:')):
                    imports.append(match)

        return list(set(imports))  # Remove duplicates

    def _extract_exports(self, tree, content: str) -> List[str]:
        """Extract HTML exports (accessible element IDs, form names, etc.)"""
        exports = []

        # Extract ID attributes
        id_pattern = r'id=["\']([^"\']+)["\']'
        id_matches = re.findall(id_pattern, content, re.IGNORECASE)
        exports.extend(id_matches)

        # Extract name attributes of form elements
        name_pattern = r'<(?:input|select|textarea|form)[^>]+name=["\']([^"\']+)["\']'
        name_matches = re.findall(name_pattern, content, re.IGNORECASE)
        exports.extend(name_matches)

        return list(set(exports))  # Remove duplicates

    def can_parse(self, file_path: Path) -> bool:
        """Override can_parse because HTML tree-sitter support may be limited"""
        # First check file extension
        if not super().can_parse(file_path):
            return False

        # If tree-sitter doesn't support HTML, still allow basic parsing
        try:
            parser = self._get_parser()
            language = self._get_language()
            return parser is not None and language is not None
        except:
            # Even if tree-sitter doesn't support it, return True, use regex fallback
            return True

    def parse_file(self, file_path: Path) -> "CodeNode":
        """Override parse_file to provide HTML-specific handling"""
        from ..models import CodeNode

        if not file_path.exists():
            logger.warning(f"File does not exist: {file_path}")
            return CodeNode(file_path=file_path)

        try:
            content = file_path.read_text(encoding='utf-8')

            # Try to parse with tree-sitter
            try:
                return super().parse_file(file_path)
            except Exception as e:
                logger.debug(f"Tree-sitter parsing failed, using regex fallback: {e}")

            # Fallback: Use regex to parse HTML
            symbols = self._extract_html_symbols_regex(content, file_path)
            references = self._extract_html_references_regex(content, file_path)
            imports = self._extract_imports(None, content)
            exports = self._extract_exports(None, content)

            return CodeNode(
                file_path=file_path,
                symbols=symbols,
                references=references,
                imports=imports,
                exports=exports,
                last_modified=file_path.stat().st_mtime,
                metadata={'language': self.language, 'parser': 'regex_fallback'}
            )

        except Exception as e:
            logger.error(f"Failed to parse HTML file {file_path}: {e}")
            return CodeNode(file_path=file_path)

    def _extract_html_symbols_regex(self, content: str, file_path: Path) -> List["Symbol"]:
        """Extract HTML symbols using regex (fallback approach)"""
        from ..models import Symbol, SymbolType

        symbols = []
        lines = content.split('\n')

        # Extract IDs
        id_pattern = r'id=["\']([^"\']+)["\']'
        for line_num, line in enumerate(lines, 1):
            for match in re.finditer(id_pattern, line, re.IGNORECASE):
                symbol = Symbol(
                    name=match.group(1),
                    symbol_type=SymbolType.CONSTANT,
                    file_path=file_path,
                    line_number=line_num,
                    column=match.start(),
                    metadata={'attribute_type': 'id', 'parser': 'regex'}
                )
                symbols.append(symbol)

        # Extract classes
        class_pattern = r'class=["\']([^"\']+)["\']'
        for line_num, line in enumerate(lines, 1):
            for match in re.finditer(class_pattern, line, re.IGNORECASE):
                class_names = match.group(1).split()
                for class_name in class_names:
                    symbol = Symbol(
                        name=class_name,
                        symbol_type=SymbolType.CONSTANT,
                        file_path=file_path,
                        line_number=line_num,
                        column=match.start(),
                        metadata={'attribute_type': 'class', 'parser': 'regex'}
                    )
                    symbols.append(symbol)

        # Extract JavaScript functions
        js_func_pattern = r'function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
        for line_num, line in enumerate(lines, 1):
            for match in re.finditer(js_func_pattern, line):
                symbol = Symbol(
                    name=match.group(1),
                    symbol_type=SymbolType.FUNCTION,
                    file_path=file_path,
                    line_number=line_num,
                    column=match.start(),
                    metadata={'language': 'javascript', 'inline': True, 'parser': 'regex'}
                )
                symbols.append(symbol)

        return symbols

    def _extract_html_references_regex(self, content: str, file_path: Path) -> List["Reference"]:
        """Extract HTML references using regex (fallback approach)"""
        from ..models import Reference, ReferenceType

        references = []
        lines = content.split('\n')

        # Extract resource references
        resource_patterns = [
            (r'href=["\']([^"\']+)["\']', 'href'),
            (r'src=["\']([^"\']+)["\']', 'src'),
            (r'action=["\']([^"\']+)["\']', 'action')
        ]

        for pattern, ref_type in resource_patterns:
            for line_num, line in enumerate(lines, 1):
                for match in re.finditer(pattern, line, re.IGNORECASE):
                    url = match.group(1)
                    if not url.startswith(('http://', 'https://', 'mailto:', 'tel:', '#')):
                        reference = Reference(
                            symbol_name=url,
                            reference_type=ReferenceType.IMPORT,
                            file_path=file_path,
                            line_number=line_num,
                            column=match.start(),
                            metadata={'reference_type': ref_type, 'parser': 'regex'}
                        )
                        references.append(reference)

        return references