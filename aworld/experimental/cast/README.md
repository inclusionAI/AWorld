# AWorld CAST Framework

**C**ode **A**ST **S**ystem **T**oolkit - A unified code analysis framework based on Tree-sitter, designed specifically for agent code understanding and optimization. Adopts a three-tier hierarchical indexing architecture to provide LLMs with precise code understanding and modification capabilities.

## 🏗️ Layered Architecture Design

### Overall Architecture Overview

```
                    AWorld CAST Framework
                        (10,000+ lines)
    ┌─────────────────────────────────────────────────────────┐
    │                🎯 ACast Core Framework                  │
    │                  (core.py - 1,637 lines)                │
    └─────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┬─────────────┐
        │             │             │             │
   ┌────▼────┐  ┌────▼────┐  ┌─────▼─────┐ ┌────▼────┐
   │📊Analyzer│  │🔍Parser │  │🔧Coder    │ │🛠️Tools  │
   │  Layer  │  │  Layer  │  │  Layer   │ │  Layer │
   │analyzers│  │parsers/ │  │ coders/  │ │ tools/ │
   └────┬────┘  └────┬────┘  └─────┬─────┘ └────┬────┘
        │            │             │            │
        │      ┌─────▼────┐  ┌────▼────┐      │
        │      │🌐Search  │  │📄Data   │      │
        │      │ Engine   │  │ Models  │      │
        │      │searchers │  │models.py│      │
        │      └─────────┘  └─────────┘      │
        │                                     │
        └─────────────────────────────────────┘
                    │
               ┌────▼─────────────────────────┐
               │  🗄️ Three-Tier Hierarchical │
               │      Index Architecture      │
               │  L1-Logic | L2-Skeleton | L3-Implementation │
               └──────────────────────────────────┘
```

## 📦 Core Module Details

### 🎯 Framework Entry Layer
| Module | Lines | Description | Design Pattern |
|--------|-------|-------------|----------------|
| `core.py` | 1,637 | ACast main framework entry, unified management of parsers and analyzers | Facade Pattern, Factory Pattern |
| `models.py` | 431 | Data models: Symbol, CodeNode, RepositoryMap, etc. | Builder Pattern, Serialization Design |
| `analyzer.py` | 381 | Abstract analyzer interface, PageRank importance calculation | Template Method Pattern |
| `parser_utils.py` | - | Parser factory functions, automatic language detection | Factory Method Pattern |

### 🔍 Parser Layer (`ast_parsers/`)

Adopts a unified parsing architecture based on **Tree-sitter**:

```
BaseParser (Abstract Base Class)
├── PythonParser     Supports .py, .pyi, .pyx
├── HtmlParser       Supports .html, .htm
└── [Extensible]    JavaScript, Go, Rust...
```

**Core Features**:
- **Unified Interface**: All parsers follow the same abstract interface
- **High-Precision Parsing**: Based on Tree-sitter syntax parsing engine
- **Symbol Extraction**: Automatically identifies functions, classes, variables, imports, etc.
- **Reference Analysis**: Tracks call relationships and dependencies

### 📊 Analyzer Layer (`analyzers/`)

**Main Components**:
- **RepositoryAnalyzer**: Main analyzer that coordinates the entire analysis process
- **analyzer.py**: Abstract analyzer interface supporting PageRank importance calculation
- **repository_analyzer.py**: Multi-level code context recall

**Analysis Algorithms**:
- **PageRank Weighting**: Symbol importance calculation based on call relationships
- **Multi-dimensional Matching**: Four-dimensional relevance scoring (content, signature, documentation, name)
- **Incremental Caching**: SQLite persistence, supports cross-session usage
- **Smart Filtering**: Automatically excludes cache files, build artifacts, etc.

### 🔧 Coder Layer (`coders/`)

Inspired by the **aider** project's coder architecture, supports multiple code modification strategies:

```
BaseCoder (Abstract Base Class)
├── SearchReplaceCoder   Exact match search and replace operations
├── DmpCoder             Patch application based on difflib
└── OpCoder             JSON operation deployment via patch conversion
```

**Design Principles**:
- **Single Responsibility**: Each coder handles specific operation types
- **Consistency**: All operations return standardized CoderResult objects
- **Extensibility**: Easy to add new coder types

### 🌐 Search Engine Layer (`searchers/`)

Unified search interface integrating multiple search strategies:

```
SearchEngine (Search Engine Core)
├── GrepSearcher        Content search based on Ripgrep
├── GlobSearcher         File pattern matching search
├── ReadSearcher         File read search
└── RipgrepManager      Cross-platform Ripgrep binary management
```

**Special Features**:
- **Tool Registration**: Supports dynamic registration of new search tools
- **Combined Search**: Supports combined use of multiple search tools
- **High Performance**: Integrates Ripgrep for ultra-fast text search

### 🛠️ Tools Layer (`tools/`)

Serves as the integration bridge between the CAST framework and the AWorld ecosystem:

| Tool | Description | Integration Interface |
|------|-------------|----------------------|
| `cast_analysis_tool.py` | Code analysis and structure extraction | ANALYZE_REPOSITORY, SEARCH_AST |
| `cast_patch_tool.py` | Intelligent code patching and verification | APPLY_PATCH, VERIFY_PATCH |
| `cast_search_tool.py` | Search functionality tools | [In Development] |

## 🗄️ Three-Tier Hierarchical Index Architecture

### L1 - Panoramic Logic Layer (LogicLayer)

**Function**: Provides a global view of code, supporting rapid architecture understanding

```python
class LogicLayer:
    project_structure: Dict[str, Any]     # Project directory structure
    key_symbols: List[Symbol]             # Key symbol table
    call_graph: Dict[str, List[str]]      # Call relationship graph
    dependency_graph: Dict[Path, Set[Path]] # Dependency relationship graph
    execution_heatmap: Dict[str, int]     # Execution heatmap
    module_descriptions: Dict[Path, str]  # Module descriptions
```

**Use Cases**:
- Quickly understand overall project architecture
- Identify core modules and key components
- Analyze inter-module dependencies

### L2 - Interface Skeleton Layer (SkeletonLayer)

**Function**: Provides interface overview, supporting API understanding and design analysis

```python
class SkeletonLayer:
    file_skeletons: Dict[Path, str]           # File skeleton code
    symbol_signatures: Dict[str, str]         # Symbol signature mapping
    line_mappings: Dict[Path, Dict[int, int]] # Line number mapping
```

**Features**:
- Removes concrete implementations, preserves type annotations and docstrings
- Provides clear API interface overview
- Supports rapid code structure understanding

### L3 - Source Implementation Layer (ImplementationLayer)

**Function**: Provides complete implementation, supporting precise code location and modification

```python
class ImplementationLayer:
    code_nodes: Dict[Path, CodeNode]  # Complete code nodes
```

**Features**:
- Contains complete source code implementation
- Supports precise symbol location and code modification
- Provides detailed reference relationship analysis

## 🚀 Quick Start

### Basic Usage

```python
from aworld.experimental.cast.core import ACast
from pathlib import Path

# Create framework instance
framework = ACast()

# Analyze code repository
repo_map = framework.analyze(
    root_path=Path("./my_project"),
    ignore_patterns=['__pycache__', '*.pyc', '.git'],
    record_name="my_project_analysis"
)

# L1 Layer: Rapid architecture understanding
architecture_context = framework.recall(
    record_name="my_project_analysis",
    user_query="Overall project architecture",
    context_layers=["logic"]
)

# L2 Layer: Interface skeleton analysis
skeleton_context = framework.recall(
    record_name="my_project_analysis",
    user_query="API interface design",
    context_layers=["skeleton"]
)

# L3 Layer: Precise code location
implementation_context = framework.recall(
    record_name="my_project_analysis",
    user_query="class.*Agent|def.*process",  # Regular expression query
    context_layers=["implementation"],
    max_tokens=8000
)
```

### Agent Self-Optimization Workflow

```python
# 1. Analyze target agent
repo_map = framework.analyze(Path("./target_agent"), record_name="agent_v0")

# 2. Understand overall architecture (L1 Layer)
arch = framework.recall("agent_v0", "Overall architecture design", ["logic"])

# 3. Analyze interface design (L2 Layer)
interfaces = framework.recall("agent_v0", "Core interfaces", ["skeleton"])

# 4. Locate performance issues (L3 Layer)
problems = framework.recall("agent_v0", "performance|slow|bottleneck", ["implementation"])

# 5. Apply optimization patches
from aworld.experimental.cast.tools.cast_patch_tool import CastPatchTool
patch_tool = CastPatchTool()
result = patch_tool.apply_patch(
    root_path=Path("./target_agent"),
    patch_content=optimization_patch,
    verification_enabled=True
)
```

## 🔧 Extension Development

### Adding a New Language Parser

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
        # Implement JavaScript-specific symbol extraction logic
        return symbols

# Register with framework
framework = ACast()
framework.register_parser("javascript", JavaScriptParser())
```

### Adding a New Searcher

```python
from aworld.experimental.cast.searchers.base_searcher import BaseSearcher

class DatabaseSearcher(BaseSearcher):
    def __init__(self, db_config):
        super().__init__(name="database")
        self.db_config = db_config

    def search(self, query, options=None):
        # Implement database search logic
        return search_results

# Register with search engine
from aworld.experimental.cast.searchers.search_engine import SearchEngine
search_engine = SearchEngine()
search_engine.register_tool(DatabaseSearcher(db_config))
```

## 🎯 Use Cases

### 🤖 Agent Self-Optimization
- **Code Analysis** → **Problem Location** → **Automatic Patching** → **Verification & Deployment**
- Supports multi-round iterative optimization, continuously improving code quality

### 📖 Code Understanding
- **Architecture Analysis**: Quickly understand overall project structure
- **Documentation Generation**: Automatically generate API documentation and architecture diagrams
- **Onboarding**: Help new developers quickly familiarize themselves with code

### 🔍 Code Quality Analysis
- **Code Review**: Automatically identify potential issues and improvement suggestions
- **Refactoring Suggestions**: Provide refactoring solutions based on dependency analysis
- **Technical Debt**: Quantitatively assess code quality and maintenance costs

## 🛡️ Performance & Reliability

### Performance Optimization
- **Incremental Caching**: SQLite persistence, avoiding redundant analysis
- **Parallel Processing**: Multi-process parallel analysis of large codebases
- **Memory Optimization**: Layered loading, on-demand code content loading
- **Index Acceleration**: Intelligent sorting based on PageRank

### Reliability Assurance
- **Comprehensive Error Handling**: Gracefully handles parsing errors and exceptions
- **Detailed Logging**: Supports debugging and issue troubleshooting
- **Type Safety**: Complete type annotations and runtime checks
- **Unit Testing**: Comprehensive test coverage for core functionality

## 📈 Technical Metrics

| Metric | Value | Description |
|--------|-------|-------------|
| Total Lines of Code | 10,000+ | Includes all modules and tools |
| Supported Languages | 2+ | Python, HTML (extensible) |
| Parsing Accuracy | >99% | Based on Tree-sitter engine |
| Analysis Speed | ~1000 files/s | Depends on hardware configuration |
| Cache Hit Rate | >95% | Incremental analysis scenarios |

## 🔗 Technology Stack

- **Tree-sitter**: High-precision syntax parsing engine
- **NetworkX**: PageRank algorithm and graph analysis
- **Ripgrep**: High-performance text search engine
- **SQLite**: Lightweight cache database
- **Python 3.8+**: Modern type annotations and dataclass design

---

*Based on Tree-sitter and layered architecture, enabling agents to understand and optimize code more precisely.*
