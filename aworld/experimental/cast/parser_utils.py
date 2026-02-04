"""
AWorld AST Framework - 解析器工具
===============================

核心目的是实例化各种Parser，提供统一的解析器管理接口。
"""

from pathlib import Path
from typing import Dict, List, Optional, Type

from .parsers.base_parser import BaseParser
from .parsers.html_parser import HtmlParser
from .parsers.python_parser import PythonParser
from ...logs.util import logger

# ===============================
# 解析器注册表
# ===============================

_PARSER_REGISTRY: Dict[str, Type[BaseParser]] = {
    'python': PythonParser,
    'html': HtmlParser,
    # 可以轻松添加更多解析器
    # 'javascript': JavaScriptParser,
    # 'typescript': TypeScriptParser,
    # 'go': GoParser,
    # 'rust': RustParser,
}


# ===============================
# 主要工厂函数
# ===============================

def create_parser(language: str) -> Optional[BaseParser]:
    """
    创建指定语言的解析器实例

    Args:
        language: 语言名称（如'python', 'html'等）

    Returns:
        解析器实例，如果不支持该语言则返回None

    Examples:
        >>> parser = create_parser('python')
        >>> if parser:
        ...     code_node = parser.parse_file(Path('example.py'))
    """
    parser_class = _PARSER_REGISTRY.get(language.lower())
    if parser_class:
        try:
            return parser_class()
        except Exception as e:
            logger.error(f"创建{language}解析器失败: {e}")
            return None

    logger.warning(f"不支持的语言: {language}")
    return None



def get_supported_languages() -> List[str]:
    """
    获取当前支持的所有语言列表

    Returns:
        支持的语言名称列表

    Examples:
        >>> languages = get_supported_languages()
        >>> print("支持的语言:", ", ".join(languages))
    """
    return list(_PARSER_REGISTRY.keys())


def get_parser_info(language: str) -> Dict[str, any]:
    """
    获取指定语言解析器的详细信息

    Args:
        language: 语言名称

    Returns:
        包含解析器信息的字典

    Examples:
        >>> info = get_parser_info('python')
        >>> print(f"文件扩展名: {info['extensions']}")
    """
    parser = create_parser(language)
    if not parser:
        return {}

    return {
        'language': parser.language,
        'extensions': list(parser.file_extensions),
        'comment_patterns': getattr(parser, 'comment_patterns', {}),
        'class': parser.__class__.__name__
    }


# ===============================
# 模块初始化
# ===============================

def _initialize_default_parsers():
    """初始化默认解析器（内部使用）"""
    # 默认解析器已经在_PARSER_REGISTRY中注册
    logger.debug(f"初始化了 {len(_PARSER_REGISTRY)} 个默认解析器")


# 自动初始化
_initialize_default_parsers()