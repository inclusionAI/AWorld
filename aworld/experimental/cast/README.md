# AWorld CAST Framework

**C**ode **A**ST **S**ystem **T**oolkit - 基于 Tree-sitter 的统一代码分析框架，专为智能体代码理解和优化设计。采用三层分级索引架构，为 LLM 提供精准的代码理解和修改能力。

## 🏗️ 分层架构设计

### 整体架构概览

```
                    AWorld CAST Framework
                        (10,000+ lines)
    ┌─────────────────────────────────────────────────────────┐
    │                🎯 ACast 核心框架                         │
    │                  (core.py - 1,637行)                   │
    └─────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┬─────────────┐
        │             │             │             │
   ┌────▼────┐  ┌────▼────┐  ┌─────▼─────┐ ┌────▼────┐
   │📊分析器层│  │🔍解析器层│  │🔧编码器层 │ │🛠️工具层  │
   │analyzers│  │parsers/│  │ coders/  │ │ tools/ │
   └────┬────┘  └────┬────┘  └─────┬─────┘ └────┬────┘
        │            │             │            │
        │      ┌─────▼────┐  ┌────▼────┐      │
        │      │🌐搜索引擎│  │📄数据模型│      │
        │      │searchers│  │models.py│      │
        │      └─────────┘  └─────────┘      │
        │                                     │
        └─────────────────────────────────────┘
                    │
               ┌────▼─────────────────────────┐
               │      🗄️ 三层分级索引架构           │
               │  L1-逻辑层 | L2-骨架层 | L3-实现层  │
               └──────────────────────────────────┘
```

## 📦 核心模块详解

### 🎯 框架入口层
| 模块 | 行数 | 功能描述 | 设计模式 |
|------|------|----------|----------|
| `core.py` | 1,637 | ACast 主框架入口，统一管理解析器和分析器 | 外观模式、工厂模式 |
| `models.py` | 431 | 数据模型：Symbol、CodeNode、RepositoryMap 等 | 建造者模式、序列化设计 |
| `analyzer.py` | 381 | 抽象分析器接口，PageRank 重要性计算 | 模板方法模式 |
| `parser_utils.py` | - | 解析器工厂函数，自动语言检测 | 工厂方法模式 |

### 🔍 解析器层 (`ast_parsers/`)

采用基于 **Tree-sitter** 的统一解析架构：

```
BaseParser (抽象基类)
├── PythonParser     支持 .py, .pyi, .pyx
├── HtmlParser       支持 .html, .htm
└── [可扩展]         JavaScript, Go, Rust...
```

**核心特性**：
- **统一接口**：所有解析器遵循相同的抽象接口
- **高精度解析**：基于 Tree-sitter 语法解析引擎
- **符号提取**：自动识别函数、类、变量、导入等
- **引用分析**：跟踪调用关系和依赖关系

### 📊 分析器层 (`analyzers/`)

**主要组件**：
- **RepositoryAnalyzer**：主分析器，协调整个分析过程
- **analyzer.py**：抽象分析器接口，支持 PageRank 重要性计算
- **repository_analyzer.py**：多层次代码上下文召回

**分析算法**：
- **PageRank 加权**：基于调用关系的符号重要性计算
- **多维度匹配**：内容、签名、文档、名称四维相关性评分
- **增量缓存**：SQLite 持久化，支持跨会话使用
- **智能过滤**：自动排除缓存文件、编译产物等

### 🔧 编码器层 (`coders/`)

借鉴 **aider** 项目的编码器架构，支持多种代码修改策略：

```
BaseCoder (抽象基类)
├── SearchReplaceCoder   精确匹配的搜索替换操作
├── DmpCoder            基于 difflib 的补丁应用
└── OpCoder             JSON 操作部署via补丁转换
```

**设计原则**：
- **单一职责**：每个编码器处理特定的操作类型
- **一致性**：所有操作返回标准化的 CoderResult 对象
- **可扩展性**：易于添加新的编码器类型

### 🌐 搜索引擎层 (`searchers/`)

统一搜索接口，集成多种搜索策略：

```
SearchEngine (搜索引擎核心)
├── GrepSearcher        基于 Ripgrep 的内容搜索
├── GlobSearcher        文件模式匹配搜索
├── ReadSearcher        文件读取搜索
└── RipgrepManager     跨平台的 Ripgrep 二进制管理
```

**特色功能**：
- **工具注册**：支持动态注册新的搜索工具
- **组合搜索**：支持多种搜索工具的组合使用
- **高性能**：集成 Ripgrep 提供极速文本搜索

### 🛠️ 工具层 (`tools/`)

作为 CAST 框架与 AWorld 生态系统的集成桥梁：

| 工具 | 功能描述 | 集成接口 |
|------|----------|----------|
| `cast_analysis_tool.py` | 代码分析和结构提取 | ANALYZE_REPOSITORY, SEARCH_AST |
| `cast_patch_tool.py` | 智能代码补丁和验证 | APPLY_PATCH, VERIFY_PATCH |
| `cast_search_tool.py` | 搜索功能工具 | [开发中] |

## 🗄️ 三层分级索引架构

### L1 - 全景逻辑层 (LogicLayer)

**功能**：提供代码的全局视图，支持快速架构理解

```python
class LogicLayer:
    project_structure: Dict[str, Any]     # 项目目录结构
    key_symbols: List[Symbol]             # 关键符号表
    call_graph: Dict[str, List[str]]      # 调用关系图
    dependency_graph: Dict[Path, Set[Path]] # 依赖关系图
    execution_heatmap: Dict[str, int]     # 执行热图
    module_descriptions: Dict[Path, str]  # 模块描述
```

**应用场景**：
- 快速了解项目整体架构
- 识别核心模块和关键组件
- 分析模块间依赖关系

### L2 - 接口骨架层 (SkeletonLayer)

**功能**：提供接口概览，支持 API 理解和设计分析

```python
class SkeletonLayer:
    file_skeletons: Dict[Path, str]           # 文件骨架代码
    symbol_signatures: Dict[str, str]         # 符号签名映射
    line_mappings: Dict[Path, Dict[int, int]] # 行号映射
```

**特点**：
- 去除具体实现，保留类型标注、文档字符串
- 提供清晰的 API 接口概览
- 支持快速代码结构理解

### L3 - 源码实现层 (ImplementationLayer)

**功能**：提供完整实现，支持精确的代码定位和修改

```python
class ImplementationLayer:
    code_nodes: Dict[Path, CodeNode]  # 完整的代码节点
```

**特点**：
- 包含完整的源代码实现
- 支持精确的符号定位和代码修改
- 提供详细的引用关系分析

## 🚀 快速开始

### 基础用法

```python
from aworld.experimental.cast.core import ACast
from pathlib import Path

# 创建框架实例
framework = ACast()

# 分析代码仓库
repo_map = framework.analyze(
    root_path=Path("./my_project"),
    ignore_patterns=['__pycache__', '*.pyc', '.git'],
    record_name="my_project_analysis"
)

# L1层：快速架构理解
architecture_context = framework.recall(
    record_name="my_project_analysis",
    user_query="项目整体架构",
    context_layers=["logic"]
)

# L2层：接口骨架分析
skeleton_context = framework.recall(
    record_name="my_project_analysis",
    user_query="API接口设计",
    context_layers=["skeleton"]
)

# L3层：精确代码定位
implementation_context = framework.recall(
    record_name="my_project_analysis",
    user_query="class.*Agent|def.*process",  # 正则表达式查询
    context_layers=["implementation"],
    max_tokens=8000
)
```

### 智能体自优化工作流

```python
# 1. 分析目标智能体
repo_map = framework.analyze(Path("./target_agent"), record_name="agent_v0")

# 2. 理解整体架构 (L1层)
arch = framework.recall("agent_v0", "整体架构设计", ["logic"])

# 3. 分析接口设计 (L2层)
interfaces = framework.recall("agent_v0", "核心接口", ["skeleton"])

# 4. 定位性能问题 (L3层)
problems = framework.recall("agent_v0", "performance|slow|bottleneck", ["implementation"])

# 5. 应用优化补丁
from aworld.experimental.cast.tools.cast_patch_tool import CastPatchTool
patch_tool = CastPatchTool()
result = patch_tool.apply_patch(
    root_path=Path("./target_agent"),
    patch_content=optimization_patch,
    verification_enabled=True
)
```

## 🔧 扩展开发

### 添加新语言解析器

```python
from aworld.experimental.cast.ast_parsers.base_parser import BaseParser

class JavaScriptParser(BaseParser):
    def __init__(self):
        super().__init__("javascript", {".js", ".jsx", ".ts", ".tsx"})

    def _get_default_query(self):
        return '''
        (function_declaration name: (identifier) @name) @definition.function
        (class_declaration name: (identifier) @name) @definition.class
        (method_definition key: (property_identifier) @name) @definition.method
        '''

    def _extract_symbols(self, captures):
        # 实现 JavaScript 特定的符号提取逻辑
        return symbols

# 注册到框架
framework = ACast()
framework.register_parser("javascript", JavaScriptParser())
```

### 添加新搜索器

```python
from aworld.experimental.cast.searchers.base_searcher import BaseSearcher

class DatabaseSearcher(BaseSearcher):
    def __init__(self, db_config):
        super().__init__(name="database")
        self.db_config = db_config

    def search(self, query, options=None):
        # 实现数据库搜索逻辑
        return search_results

# 注册到搜索引擎
from aworld.experimental.cast.searchers.search_engine import SearchEngine
search_engine = SearchEngine()
search_engine.register_tool(DatabaseSearcher(db_config))
```

## 🎯 应用场景

### 🤖 智能体自优化
- **代码分析** → **问题定位** → **自动补丁** → **验证部署**
- 支持多轮迭代优化，持续提升代码质量

### 📖 代码理解
- **架构分析**：快速理解项目整体结构
- **文档生成**：自动生成 API 文档和架构图
- **新人入职**：帮助新开发者快速熟悉代码

### 🔍 代码质量分析
- **代码审查**：自动识别潜在问题和改进建议
- **重构建议**：基于依赖分析提供重构方案
- **技术债务**：量化评估代码质量和维护成本

## 🛡️ 性能与可靠性

### 性能优化
- **增量缓存**：SQLite 持久化，避免重复分析
- **并行处理**：多进程并行分析大型代码库
- **内存优化**：分层加载，按需加载代码内容
- **索引加速**：基于 PageRank 的智能排序

### 可靠性保障
- **完善的错误处理**：优雅处理解析错误和异常情况
- **详细的日志记录**：支持调试和问题排查
- **类型安全**：完整的类型标注和运行时检查
- **单元测试**：核心功能的全面测试覆盖

## 📈 技术指标

| 指标 | 数值 | 说明 |
|------|------|------|
| 总代码行数 | 10,000+ | 包含所有模块和工具 |
| 支持语言 | 2+ | Python, HTML (可扩展) |
| 解析精度 | >99% | 基于 Tree-sitter 引擎 |
| 分析速度 | ~1000 files/s | 取决于硬件配置 |
| 缓存命中率 | >95% | 增量分析场景 |

## 🔗 技术栈

- **Tree-sitter**: 高精度语法解析引擎
- **NetworkX**: PageRank 算法和图分析
- **Ripgrep**: 高性能文本搜索引擎
- **SQLite**: 轻量级缓存数据库
- **Python 3.8+**: 现代类型标注和 dataclass 设计

---

*基于 Tree-sitter 和分层架构，让智能体更精准地理解和优化代码。*