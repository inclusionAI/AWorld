"""
AWorld AST Framework
====================

一个基于Tree-sitter的抽象可扩展AST框架，用于智能体代码分析和优化。

基于aider的设计思想，实现三层分级索引结构：
1. L1 - 全景逻辑层：动态调用拓扑图和模块关系
2. L2 - 接口骨架层：去实现的伪代码签名
3. L3 - 源码实现层：具体的代码实现

核心特性：
- 统一的Tree-sitter解析器架构
- 高性能多语言代码解析
- PageRank算法的代码重要性排序
- 动态轨迹映射和剪枝优化
- 缓存和增量处理
- 可扩展的解析器注册机制
"""

# 基础解析器和具体实现
from .ast_parsers.base_parser import BaseParser
from .ast_parsers.html_parser import HtmlParser
from .ast_parsers.python_parser import PythonParser
# 核心框架类
from .core import (
    ACast,
    ASTContextBuilder,
)
# 数据模型
from .models import (
    Symbol,
    Reference,
    CodeNode,
    RepositoryMap,
    LogicLayer,
    SkeletonLayer,
    ImplementationLayer,
    SymbolType,
    ReferenceType,
)
# 解析器工具（核心）

# 工具类

__version__ = "2.0.0"
__all__ = [
    # 核心框架
    "ACast",
    "ASTContextBuilder",

    # 解析器基类和实现
    "BaseParser",
    "PythonParser",
    "HtmlParser",

    # 数据模型
    "Symbol",
    "Reference",
    "CodeNode",
    "RepositoryMap",
    "LogicLayer",
    "SkeletonLayer",
    "ImplementationLayer",
    "SymbolType",
    "ReferenceType",

]

# 版本信息
__author__ = "AWorld Team"
__email__ = "aworld@example.com"
__description__ = "基于Tree-sitter的统一AST框架，用于智能体代码分析和优化"

# 快速开始示例
__doc__ += """

快速开始
========

推荐使用方式（统一Tree-sitter架构）：

```python
from aworld.experimental.cast import (
    create_parser,
    auto_create_parser,
    get_supported_languages,
    quick_parse,
    ACast
)
from pathlib import Path

# 方式1: 自动创建解析器（最简单）
code_node = quick_parse(Path("example.py"))
if code_node:
    print(f"找到 {len(code_node.symbols)} 个符号")

# 方式2: 手动创建解析器
parser = create_parser("python")  # 或者 auto_create_parser(Path("example.py"))
if parser:
    code_node = parser.parse_file(Path("example.py"))
    skeleton = parser.generate_skeleton(content, Path("example.py"))

# 方式3: 查看所有支持的语言
print("支持的语言:", get_supported_languages())

# 方式4: 完整框架使用
framework = ACast()

# 自动注册所有支持的解析器
for lang in get_supported_languages():
    parser = create_parser(lang)
    if parser:
        framework.register_parser(lang, parser)

# 分析代码库
analyzer = framework.create_analyzer()
repo_map = analyzer.analyze(Path("./my_project"))

# 生成智能上下文
context = analyzer.get_optimized_context(
    repo_map=repo_map,
    user_query="优化性能问题",
    max_tokens=8000
)
```

扩展新语言：

```python
from aworld.experimental.cast import BaseParser, register_parser

class JavaScriptParser(BaseParser):
    def __init__(self):
        super().__init__("javascript", {".js", ".jsx"})

    def _get_default_query(self):
        return '''
        (function_declaration
          name: (identifier) @name.definition.function) @definition.function
        '''

# 注册新解析器
register_parser("javascript", JavaScriptParser)

# 现在可以使用了
js_parser = create_parser("javascript")
```

当前支持的语言：""" + ", ".join(["Python", "HTML"]) + """

更多示例请参考相关文档。
"""
