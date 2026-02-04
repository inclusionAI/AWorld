# AWorld AST Framework

基于Tree-sitter的统一代码分析框架，专为智能体代码分析和优化设计。采用分层上下文管理架构，为LLM提供精确的代码理解和修改能力。

## 🏗️ 架构概览

```
                    AWorld AST Framework
                          (6,400+ 行)
    ┌─────────────────────────────────────────────────────────┐
    │                    🎯 ACast 主框架                       │
    │                    (core.py)                          │
    └─────────────────────┬───────────────────────────────────┘
                          │
              ┌───────────┼───────────┐
              │           │           │
    ┌─────────▼─────┐ ┌──▼──────┐ ┌──▼──────────┐
    │  🌐 解析器层    │ │ 📊 分析层 │ │  🛠️ 工具层   │
    │   parsers/    │ │analyzer.py│ │   tools/   │
    └─────────┬─────┘ └─────────┘ └─────────────┘
              │
    ┌─────────▼─────┐
    │  BaseParser   │ ──┐
    │   (基础类)      │   │    ┌─────────────────┐
    └───────────────┘   ├────│  PythonParser   │
                        │    │  HtmlParser     │
                        └────│  (可扩展...)     │
                             └─────────────────┘

            数据流向: 代码文件 → 解析器 → 模型 → 分层上下文
```

## 📦 核心模块

### 🔧 框架核心层
| 模块 | 行数 | 功能描述 |
|------|------|----------|
| `core.py` | 1,637 | ACast主框架入口、分层分析器 |
| `models.py` | 431 | Symbol、CodeNode、RepositoryMap等数据模型 |
| `analyzer.py` | 381 | 抽象分析器接口定义 |
| `utils.py` | 447 | PageRank计算、缓存管理、工具函数 |

### 🌐 语言解析层 (`parsers/`)
- **BaseParser**: Tree-sitter统一解析器基类
- **PythonParser**: Python语言解析器 (`.py`, `.pyi`, `.pyx`)
- **HtmlParser**: HTML解析器 (`.html`, `.htm`)
- **可扩展**: JavaScript、Go、Rust等语言支持

### 🛠️ 分析工具层 (`tools/`)
- **cast_analysis_tool.py**: 代码分析和结构提取
- **cast_patch_tool.py**: 智能代码修补和验证

### 📊 数据存储层
- **acast/**: 分析结果持久化存储 (JSON格式)
- **logs/**: 运行日志和调试信息

## 🎯 核心特性

### 分层上下文架构

**已实现的核心层次：**
- **Skeleton Layer (骨架层)**: 去实现的代码签名、类型注解、文档字符串
- **Implementation Layer (实现层)**: 基于正则表达式的完整源码匹配

### 智能分析算法
- **PageRank权重**: 基于调用关系的符号重要性计算
- **多维度匹配**: 内容、签名、文档、名称四维度相关性评分
- **增量缓存**: SQLite持久化，支持跨会话使用
- **智能过滤**: 自动排除缓存文件、编译产物等

### 技术栈
- **Tree-sitter**: 高精度语法解析引擎
- **NetworkX**: PageRank算法和图分析
- **Python 3.8+**: 现代化类型注解和数据类设计

## 🚀 快速开始

### 基本用法

```python
from aworld.experimental.ast.core import ACast
from pathlib import Path

# 创建框架实例
framework = ACast(auto_register_parsers=True)

# 分析代码仓库
repo_map = framework.analyze(
    root_path=Path("./my_project"),
    auto_record=True,
    record_name="my_project_analysis"
)

# 骨架层：快速理解架构
architecture_context = framework.recall(
    record_name="my_project_analysis",
    user_query=".*",  # 匹配所有
    context_layers=["skeleton"]
)

# 实现层：精确定位代码
implementation_context = framework.recall(
    record_name="my_project_analysis",
    user_query="class.*Agent|def.*process",  # 正则表达式查询
    context_layers=["implementation"],
    max_tokens=8000
)
```

### 智能体自我优化工作流

```python
# 1. 分析目标智能体
repo_map = framework.analyze(Path("./target_agent"), record_name="agent_v0")

# 2. 理解整体架构
arch = framework.recall("agent_v0", ".*", ["skeleton"])

# 3. 定位性能问题
problems = framework.recall("agent_v0", "performance|slow|bottleneck", ["implementation"])

# 4. 应用优化patch
framework.create_enhanced_copy(
    Path("./target_agent"), patch_content, version="v1", strict_validation=True
)
```

## 🎯 应用场景

- **🤖 智能体自我优化**: 代码分析 → 问题定位 → 自动修补
- **📖 代码理解**: 架构分析、文档生成、新人培训
- **🔍 质量分析**: 代码审查、重构建议、技术债务评估

## 🛠️ 扩展开发

### 添加新语言解析器

```python
from aworld.experimental.ast.parsers.base_parser import BaseParser

class JavaScriptParser(BaseParser):
    def __init__(self):
        super().__init__("javascript", {".js", ".jsx"})

    def _get_default_query(self):
        return '''
        (function_declaration name: (identifier) @name) @definition.function
        (class_declaration name: (identifier) @name) @definition.class
        '''

# 注册到框架
framework = ACast(auto_register_parsers=False)
framework.register_parser("javascript", JavaScriptParser())
```

---

*基于Tree-sitter和分层架构，让智能体更精确地理解和优化代码。*