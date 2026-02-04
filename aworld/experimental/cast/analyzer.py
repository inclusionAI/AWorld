"""
AWorld AST Framework - 默认实现
=============================

提供CodeAnalyzer和相关组件的默认实现。
"""

import logging
import time
from abc import abstractmethod, ABC
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Optional, Any

import networkx as nx

from .models import (
    CodeNode, RepositoryMap,
    LogicLayer, SkeletonLayer, ImplementationLayer,
    SymbolType, Symbol
)

class CodeAnalyzer(ABC):
    """代码分析器抽象基类"""

    def __init__(self, parsers: Dict[str, Any]):
        self.parsers = parsers
        self.cache_enabled = True

    @abstractmethod
    def analyze_repository(self, root_path: Path,
                           file_patterns: Optional[List[str]] = None,
                           ignore_patterns: Optional[List[str]] = None) -> RepositoryMap:
        """
        分析整个代码仓库

        Args:
            root_path: 仓库根目录
            file_patterns: 包含的文件模式
            ignore_patterns: 忽略的文件模式

        Returns:
            完整的仓库映射
        """
        pass

    @abstractmethod
    def analyze_files(self, file_paths: List[Path]) -> Dict[Path, CodeNode]:
        """
        分析指定的文件列表

        Args:
            file_paths: 要分析的文件路径列表

        Returns:
            文件路径到CodeNode的映射
        """
        pass

    @abstractmethod
    def build_dependency_graph(self, code_nodes: Dict[Path, CodeNode]) -> Dict[Path, Set[Path]]:
        """
        构建文件依赖关系图

        Args:
            code_nodes: 文件到CodeNode的映射

        Returns:
            依赖关系图：{file_path: {dependent_files}}
        """
        pass

    @abstractmethod
    def calculate_importance(self, code_nodes: Dict[Path, CodeNode],
                             dependency_graph: Dict[Path, Set[Path]],
                             user_mentions: List[str] = None) -> Dict[Path, float]:
        """
        使用PageRank算法计算文件重要性

        Args:
            code_nodes: 代码节点
            dependency_graph: 依赖图
            user_mentions: 用户提及的标识符

        Returns:
            文件重要性分数
        """
        pass

    def get_parser(self, file_path: Path) -> Optional[Any]:
        """根据文件路径获取适当的解析器"""
        for parser in self.parsers.values():
            if hasattr(parser, 'can_parse') and parser.can_parse(file_path):
                return parser
        return None


class DefaultCodeAnalyzer(CodeAnalyzer):
    """默认代码分析器实现"""

    def __init__(self, parsers: Dict[str, Any]):
        super().__init__(parsers)
        self.logger = logging.getLogger(f"{__name__}.DefaultCodeAnalyzer")

    def analyze_repository(self, root_path: Path,
                           file_patterns: Optional[List[str]] = None,
                           ignore_patterns: Optional[List[str]] = None) -> RepositoryMap:
        """分析整个代码仓库"""
        self.logger.info(f"开始分析仓库: {root_path}")

        # 扫描文件
        files_to_analyze = self._scan_files(root_path, file_patterns, ignore_patterns)
        self.logger.info(f"发现 {len(files_to_analyze)} 个文件需要分析")

        # 使用工具分析文件（tree-sitter, pageIndex ...）
        code_nodes = self.analyze_files(files_to_analyze)

        # 构建依赖图
        dependency_graph = self.build_dependency_graph(code_nodes)

        # 计算重要性
        pagerank_scores = self.calculate_importance(code_nodes, dependency_graph)

        # 构建三层结构
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
        """分析指定的文件列表"""
        code_nodes = {}

        for file_path in file_paths:
            try:
                parser = self.get_parser(file_path)
                if parser:
                    code_node = parser.parse_file(file_path)
                    code_nodes[file_path] = code_node
                    self.logger.debug(f"解析文件: {file_path}")
                else:
                    self.logger.warning(f"找不到合适的解析器: {file_path}")
            except Exception as e:
                self.logger.error(f"解析文件失败 {file_path}: {e}")

        return code_nodes

    def build_dependency_graph(self, code_nodes: Dict[Path, CodeNode]) -> Dict[Path, Set[Path]]:
        """构建文件依赖关系图"""
        dependency_graph = defaultdict(set)

        for file_path, node in code_nodes.items():
            # 基于import语句建立依赖关系
            for import_name in node.imports:
                # 尝试解析import到文件路径
                target_files = self._resolve_import(import_name, code_nodes.keys())
                for target_file in target_files:
                    if target_file != file_path:
                        dependency_graph[file_path].add(target_file)
                        code_nodes[target_file].dependents.add(file_path)

            # 基于符号引用建立依赖关系
            for reference in node.references:
                # 查找定义了这个符号的文件
                target_files = self._find_symbol_definition(reference.symbol_name, code_nodes)
                for target_file in target_files:
                    if target_file != file_path:
                        dependency_graph[file_path].add(target_file)
                        code_nodes[target_file].dependents.add(file_path)

        return dict(dependency_graph)

    def calculate_importance(self, code_nodes: Dict[Path, CodeNode],
                             dependency_graph: Dict[Path, Set[Path]],
                             user_mentions: List[str] = None) -> Dict[Path, float]:
        """使用PageRank算法计算文件重要性"""
        if not code_nodes:
            return {}

        # 创建NetworkX图
        G = nx.DiGraph()

        # 添加节点
        for file_path in code_nodes.keys():
            G.add_node(str(file_path))

        # 添加边（依赖关系）
        for source, targets in dependency_graph.items():
            for target in targets:
                G.add_edge(str(source), str(target))

        # 计算基础PageRank分数
        try:
            pagerank = nx.pagerank(G, alpha=0.85, max_iter=100)
        except nx.PowerIterationFailedConvergence:
            self.logger.warning("PageRank计算未收敛，使用均匀分布")
            pagerank = {str(path): 1.0 / len(code_nodes) for path in code_nodes.keys()}

        # 应用权重调整
        weighted_scores = {}
        for file_path, node in code_nodes.items():
            base_score = pagerank.get(str(file_path), 0.0)

            # 用户提及权重
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
        """扫描目录获取需要分析的文件"""
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
        """解析import语句到实际文件路径"""
        # 简化实现：基于名称匹配
        result = []
        import_parts = import_name.replace('.', '/').split('/')

        for file_path in available_files:
            file_str = str(file_path).lower()
            if any(part.lower() in file_str for part in import_parts if part):
                result.append(file_path)

        return result

    def _find_symbol_definition(self, symbol_name: str,
                                code_nodes: Dict[Path, CodeNode]) -> List[Path]:
        """查找符号定义所在的文件"""
        result = []

        for file_path, node in code_nodes.items():
            for symbol in node.symbols:
                if symbol.name == symbol_name or symbol.full_name == symbol_name:
                    result.append(file_path)

        return result

    def _build_logic_layer(self, code_nodes: Dict[Path, CodeNode],
                           dependency_graph: Dict[Path, Set[Path]]) -> LogicLayer:
        """构建L1逻辑层"""
        # 构建项目结构
        project_structure = self._build_project_structure(code_nodes.keys())

        # 提取关键符号（不带content）
        key_symbols = []
        for node in code_nodes.values():
            # 选择重要的符号（类、主函数等）
            for symbol in node.symbols:
                if (symbol.symbol_type in [SymbolType.CLASS, SymbolType.FUNCTION] and
                        (symbol.name.startswith('main') or
                         symbol.name == '__init__' or
                         len(symbol.name) > 3)):
                    # 创建不带content的symbol副本
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
                        content=None,  # 不记录content
                        parent=symbol.parent,
                        modifiers=symbol.modifiers,
                        parameters=symbol.parameters,
                        return_type=symbol.return_type,
                        metadata=symbol.metadata
                    )
                    key_symbols.append(symbol_without_content)

        # # 构建调用图
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
        """构建L2骨架层"""
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

                    # 构建符号签名映射
                    for symbol in node.symbols:
                        if symbol.signature:
                            symbol_signatures[symbol.full_name] = symbol.signature

                    # TODO: 实现行号映射
                    line_mappings[file_path] = {}

                except Exception as e:
                    self.logger.error(f"生成骨架失败 {file_path}: {e}")

        return SkeletonLayer(
            file_skeletons=file_skeletons,
            symbol_signatures=symbol_signatures,
            line_mappings=line_mappings
        )

    def _build_implementation_layer(self, code_nodes: Dict[Path, CodeNode]) -> ImplementationLayer:
        """构建L3实现层"""
        return ImplementationLayer(
            code_nodes=code_nodes
        )

    def _build_project_structure(self, file_paths: List[Path]) -> Dict[str, Any]:
        """构建项目目录结构"""
        structure = {}

        for file_path in file_paths:
            parts = file_path.parts
            current = structure

            for part in parts[:-1]:  # 除了文件名的所有部分
                if part not in current:
                    current[part] = {}
                current = current[part]

            # 添加文件
            if isinstance(current, dict):
                current[parts[-1]] = str(file_path)

        return structure
