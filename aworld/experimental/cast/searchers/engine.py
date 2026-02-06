"""
搜索引擎核心架构
=============

统一的搜索接口，集成Grep、Glob、Read等多种搜索工具。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Union, AsyncIterator, Iterator
from pathlib import Path
from enum import Enum


class SearchType(Enum):
    """搜索类型枚举"""
    GREP = "grep"          # 内容搜索
    GLOB = "glob"          # 文件模式匹配
    READ = "read"          # 文件读取
    TREE = "tree"          # 目录树结构
    FILES = "files"        # 文件列表


@dataclass
class SearchResult:
    """搜索结果数据结构"""
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
    """搜索参数"""
    pattern: Optional[str] = None           # 搜索模式/正则表达式
    path: Optional[str] = None              # 搜索路径
    include_patterns: Optional[List[str]] = None  # 包含文件模式
    exclude_patterns: Optional[List[str]] = None  # 排除文件模式
    max_results: int = 100                  # 最大结果数
    max_line_length: int = 2000            # 最大行长度
    follow_symlinks: bool = True           # 跟随符号链接
    search_hidden: bool = True             # 搜索隐藏文件
    case_sensitive: bool = False           # 大小写敏感
    offset: int = 0                        # 结果偏移量
    limit: int = 2000                      # 结果限制
    max_depth: Optional[int] = None        # 最大搜索深度
    context_lines: int = 0                 # 上下文行数


class Searcher(ABC):
    """搜索工具抽象基类"""

    @abstractmethod
    def search(self, params: SearchParams) -> SearchResult:
        """执行搜索操作"""
        pass

    @abstractmethod
    def get_search_type(self) -> SearchType:
        """获取搜索工具类型"""
        pass

    @abstractmethod
    def validate_params(self, params: SearchParams) -> bool:
        """验证搜索参数"""
        pass


class SearchEngine:
    """
    统一搜索引擎

    集成多种搜索工具，提供统一的搜索接口。
    基于opencode的设计理念，支持工具组合和结果聚合。
    """

    def __init__(self, root_path: Optional[Union[str, Path]] = None):
        self.root_path = Path(root_path) if root_path else Path.cwd()
        self.searchers: Dict[SearchType, Searcher] = {}

    def register_searcher(self, searcher: Searcher):
        """注册搜索工具"""
        search_type = searcher.get_search_type()
        self.searchers[search_type] = searcher

    async def search(self, search_type: SearchType, params: SearchParams) -> SearchResult:
        """执行指定类型的搜索"""
        if search_type not in self.searchers:
            raise ValueError(f"搜索工具未注册: {search_type}")

        searcher = self.searchers[search_type]
        if not searcher.validate_params(params):
            raise ValueError(f"搜索参数无效: {params}")

        return await searcher.search(params)

    def multi_search(self, searches: List[tuple[SearchType, SearchParams]]) -> List[SearchResult]:
        """执行多个搜索操作"""
        results = []
        for search_type, params in searches:
            try:
                result = self.search(search_type, params)
                results.append(result)
            except Exception as e:
                # 创建错误结果
                error_result = SearchResult(
                    title=f"搜索失败: {search_type.value}",
                    search_type=search_type,
                    matches=[],
                    metadata={"error": str(e)},
                    output=f"搜索失败: {str(e)}",
                    truncated=False
                )
                results.append(error_result)
        return results

    def combined_search(self, pattern: str, search_types: List[SearchType] = None) -> Dict[SearchType, SearchResult]:
        """组合搜索：同时使用多种搜索策略"""
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
                    title=f"组合搜索失败: {search_type.value}",
                    search_type=search_type,
                    matches=[],
                    metadata={"error": str(e)},
                    output=f"搜索失败: {str(e)}",
                    truncated=False
                )

        return results

    def get_available_searchers(self) -> List[SearchType]:
        """获取可用的搜索工具列表"""
        return list(self.searchers.keys())

    def set_root_path(self, path: Union[str, Path]):
        """设置搜索根路径"""
        self.root_path = Path(path)
        # 通知所有工具路径变更
        for searcher in self.searchers.values():
            if hasattr(searcher, 'set_root_path'):
                searcher.set_root_path(self.root_path)