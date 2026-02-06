"""
AWorld AST Framework - HTML解析器
================================

基于Tree-sitter的HTML代码解析器实现。
注意：HTML可能需要特殊的Tree-sitter语法支持，这里提供基础实现。
"""

import re
from pathlib import Path
from typing import List, Optional

from aworld.logs.util import logger
from .base_parser import BaseParser


class HtmlParser(BaseParser):
    """HTML Tree-sitter解析器"""

    def __init__(self):
        super().__init__(
            language="html",
            file_extensions={'.html', '.htm', '.xhtml'}
        )
        self.comment_patterns = {
            'line': None,  # HTML没有行注释
            'block': ('<!--', '-->')
        }

    def _get_default_query(self) -> str:
        """HTML的默认Tree-sitter查询"""
        # 注意：这是一个基础查询，实际的HTML tree-sitter查询可能需要调整
        return '''
;; HTML元素
(element
  (start_tag
    (tag_name) @name.definition.tag) @definition.tag)

;; 带ID的元素
(element
  (start_tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @name.definition.id) @definition.id))
  (#eq? @attr_name "id"))

;; 带class的元素
(element
  (start_tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @name.definition.class) @definition.class))
  (#eq? @attr_name "class"))

;; script标签中的JavaScript（简化）
(element
  (start_tag (tag_name) @tag_name)
  (text) @name.definition.script
  (#eq? @tag_name "script"))

;; 链接和资源引用
(element
  (start_tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @name.reference.resource) @reference.resource))
  (#match? @attr_name "^(src|href|action)$"))
'''

    def _extract_docstring(self, node, lines: List[str], line_num: int) -> Optional[str]:
        """HTML没有传统意义的文档字符串，返回None"""
        return None

    def _extract_imports(self, tree, content: str) -> List[str]:
        """提取HTML资源导入（CSS、JS、图片等）"""
        imports = []

        # 使用正则表达式提取资源链接（备用方案）
        patterns = [
            r'<link[^>]+href=["\']([^"\']+)["\']',  # CSS链接
            r'<script[^>]+src=["\']([^"\']+)["\']',  # JS文件
            r'<img[^>]+src=["\']([^"\']+)["\']',     # 图片
            r'@import\s+["\']([^"\']+)["\']'         # CSS @import
        ]

        for pattern in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                # 过滤外部URL
                if not match.startswith(('http://', 'https://', '//', 'data:')):
                    imports.append(match)

        return list(set(imports))  # 去重

    def _extract_exports(self, tree, content: str) -> List[str]:
        """提取HTML导出（可访问的元素ID、表单name等）"""
        exports = []

        # 提取ID属性
        id_pattern = r'id=["\']([^"\']+)["\']'
        id_matches = re.findall(id_pattern, content, re.IGNORECASE)
        exports.extend(id_matches)

        # 提取表单元素的name属性
        name_pattern = r'<(?:input|select|textarea|form)[^>]+name=["\']([^"\']+)["\']'
        name_matches = re.findall(name_pattern, content, re.IGNORECASE)
        exports.extend(name_matches)

        return list(set(exports))  # 去重

    def can_parse(self, file_path: Path) -> bool:
        """重写can_parse，因为HTML的tree-sitter支持可能有限"""
        # 首先检查文件扩展名
        if not super().can_parse(file_path):
            return False

        # 如果tree-sitter不支持HTML，仍然可以进行基础解析
        try:
            parser = self._get_parser()
            language = self._get_language()
            return parser is not None and language is not None
        except:
            # 即使tree-sitter不支持，也返回True，使用正则表达式备用方案
            return True

    def parse_file(self, file_path: Path) -> "CodeNode":
        """重写parse_file，提供HTML特殊处理"""
        from ..models import CodeNode

        if not file_path.exists():
            logger.warning(f"文件不存在: {file_path}")
            return CodeNode(file_path=file_path)

        try:
            content = file_path.read_text(encoding='utf-8')

            # 尝试使用tree-sitter解析
            try:
                return super().parse_file(file_path)
            except Exception as e:
                logger.debug(f"Tree-sitter解析失败，使用正则表达式备用方案: {e}")

            # 备用方案：使用正则表达式解析HTML
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
            logger.error(f"解析HTML文件失败 {file_path}: {e}")
            return CodeNode(file_path=file_path)

    def _extract_html_symbols_regex(self, content: str, file_path: Path) -> List["Symbol"]:
        """使用正则表达式提取HTML符号（备用方案）"""
        from ..models import Symbol, SymbolType

        symbols = []
        lines = content.split('\n')

        # 提取ID
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

        # 提取class
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

        # 提取JavaScript函数
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
        """使用正则表达式提取HTML引用（备用方案）"""
        from ..models import Reference, ReferenceType

        references = []
        lines = content.split('\n')

        # 提取资源引用
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