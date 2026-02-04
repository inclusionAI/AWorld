"""
AWorld AST Framework - 基于Tree-sitter的统一解析器基类
===================================================

所有解析器都基于Tree-sitter实现，提供统一的接口和高性能解析能力。
"""

import logging
from abc import ABC, abstractmethod
from importlib import resources
from pathlib import Path
from typing import List, Set, Optional, Tuple

from grep_ast import TreeContext
from grep_ast.tsl import get_language, get_parser

from ..models import (
    Symbol, Reference, CodeNode,
    SymbolType, ReferenceType
)

class BaseParser(ABC):
    """
    基于Tree-sitter的统一解析器基类

    所有具体语言的解析器都应该继承此类
    """

    def __init__(self, language: str, file_extensions: Set[str]):
        """
        初始化解析器

        Args:
            language: 编程语言名称（如'python', 'javascript'等）
            file_extensions: 支持的文件扩展名集合（如{'.py', '.pyi'}）
        """
        self.language = language
        self.file_extensions = file_extensions
        self.logger = logging.getLogger(f"{__name__}.{language.title()}Parser")

        # 缓存
        self._parser_cache = None
        self._language_cache = None
        self._query_cache = None

    def can_parse(self, file_path: Path) -> bool:
        """判断是否可以解析指定文件"""
        return file_path.suffix.lower() in self.file_extensions

    def parse_file(self, file_path: Path) -> CodeNode:
        """
        解析文件，返回CodeNode

        Args:
            file_path: 要解析的文件路径

        Returns:
            包含符号和引用信息的CodeNode
        """
        if not file_path.exists():
            self.logger.warning(f"文件不存在: {file_path}")
            return CodeNode(file_path=file_path)

        try:
            content = file_path.read_text(encoding='utf-8')

            # 获取Tree-sitter组件
            parser = self._get_parser()
            language = self._get_language()

            if not parser or not language:
                self.logger.warning(f"无法获取{self.language}解析器")
                return CodeNode(file_path=file_path)

            # 解析代码
            tree = parser.parse(bytes(content, "utf-8"))

            # 提取符号和引用
            symbols, references = self._extract_symbols_and_references(
                tree, language, content, file_path
            )

            # 提取导入和导出
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
            self.logger.error(f"解析文件失败 {file_path}: {e}")
            return CodeNode(file_path=file_path)

    def generate_skeleton(self, content: str, file_path: Path) -> str:
        """
        生成代码骨架，使用TreeContext进行智能上下文提取

        Args:
            content: 文件内容
            file_path: 文件路径

        Returns:
            格式化的代码骨架字符串
        """
        try:
            # 使用TreeContext生成骨架
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

            # 获取所有定义的行号
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
            self.logger.error(f"生成骨架失败 {file_path}: {e}")
            return f"# 骨架生成失败: {e}\n"

    def extract_symbols(self, content: str, file_path: Path) -> List[Symbol]:
        """提取符号定义"""
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
            self.logger.error(f"提取符号失败: {e}")
            return []

    def extract_references(self, content: str, file_path: Path) -> List[Reference]:
        """提取符号引用"""
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
            self.logger.error(f"提取引用失败: {e}")
            return []

    def get_imports(self, content: str) -> List[str]:
        """提取导入语句"""
        try:
            parser = self._get_parser()
            if not parser:
                return []

            tree = parser.parse(bytes(content, "utf-8"))
            return self._extract_imports(tree, content)
        except Exception as e:
            self.logger.error(f"提取导入失败: {e}")
            return []

    def get_exports(self, content: str) -> List[str]:
        """提取导出语句"""
        try:
            parser = self._get_parser()
            if not parser:
                return []

            tree = parser.parse(bytes(content, "utf-8"))
            return self._extract_exports(tree, content)
        except Exception as e:
            self.logger.error(f"提取导出失败: {e}")
            return []

    # ===============================
    # Tree-sitter内部实现方法
    # ===============================

    def _get_parser(self):
        """获取或缓存Tree-sitter解析器"""
        if self._parser_cache is None:
            try:
                self._parser_cache = get_parser(self.language)
            except Exception as e:
                self.logger.error(f"获取{self.language}解析器失败: {e}")
                return None
        return self._parser_cache

    def _get_language(self):
        """获取或缓存Tree-sitter语言"""
        if self._language_cache is None:
            try:
                self._language_cache = get_language(self.language)
            except Exception as e:
                self.logger.error(f"获取{self.language}语言失败: {e}")
                return None
        return self._language_cache

    def _get_query_content(self) -> Optional[str]:
        """获取查询文件内容"""
        if self._query_cache is not None:
            return self._query_cache

        try:
            # 尝试使用内置查询文件（aider风格）
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

            # 使用本地查询文件
            local_query = Path(__file__).parent / "queries" / f"{self.language}-tags.scm"
            if local_query.exists():
                self._query_cache = local_query.read_text()
                return self._query_cache

        except Exception as e:
            self.logger.debug(f"获取查询文件失败: {e}")

        # 返回默认查询（由子类实现）
        self._query_cache = self._get_default_query()
        return self._query_cache

    @abstractmethod
    def _get_default_query(self) -> str:
        """获取默认查询字符串（子类必须实现）"""
        pass

    def _extract_symbols_and_references(self, tree, language, content: str,
                                       file_path: Path) -> Tuple[List[Symbol], List[Reference]]:
        """提取符号定义和引用"""
        symbols = []
        references = []

        query_content = self._get_query_content()
        if not query_content:
            self.logger.debug(f"没有查询文件: {self.language}")
            return symbols, references

        try:
            query = language.query(query_content)
            captures = query.captures(tree.root_node)
            lines = content.split('\n')

            # 处理捕获
            for capture_name, nodes in captures.items():
                for node in nodes:
                    line_num = node.start_point[0] + 1
                    col_num = node.start_point[1]

                    if capture_name.startswith('name.definition'):
                        symbol_type = self._map_definition_type(capture_name)
                        symbol_name = node.text.decode('utf-8')

                        # 获取父级上下文
                        parent_name = self._find_parent_symbol(node, content)

                        # 提取签名和文档
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
            self.logger.error(f"提取符号和引用失败: {e}")

        return symbols, references

    def _map_definition_type(self, capture_name: str) -> SymbolType:
        """映射捕获名称到符号类型"""
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
        """映射捕获名称到引用类型"""
        if 'call' in capture_name:
            return ReferenceType.CALL
        elif 'class' in capture_name:
            return ReferenceType.INHERITANCE
        elif 'import' in capture_name:
            return ReferenceType.IMPORT
        else:
            return ReferenceType.ACCESS

    def _find_parent_symbol(self, node, content: str) -> Optional[str]:
        """查找父级符号名称"""
        current = node.parent
        while current:
            if current.type in ['class_definition', 'function_definition', 'method_definition']:
                for child in current.children:
                    if child.type == 'identifier' or child.type == 'property_identifier':
                        return child.text.decode('utf-8')
            current = current.parent
        return None

    def _extract_signature(self, node, lines: List[str], line_num: int) -> Optional[str]:
        """提取符号签名"""
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
        """提取文档字符串（子类可重写以提供更精确的提取）"""
        return None

    def _extract_imports(self, tree, content: str) -> List[str]:
        """提取导入语句（子类应重写）"""
        return []

    def _extract_exports(self, tree, content: str) -> List[str]:
        """提取导出语句（子类应重写）"""
        return []

    def _get_symbols_quick(self, content: str, file_path: Path) -> List[Symbol]:
        """快速获取符号列表（用于骨架生成）"""
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
            self.logger.debug(f"快速符号提取失败: {e}")
            return []