---
name: optimizer
description: Agent Optimization Skill - Optimize existing Agents based on AST analysis and patch generation technology. By analyzing Agent code structure, performance bottlenecks, and architectural issues, automatically generate optimization solutions and apply code patches to improve Agent execution efficiency, maintainability, and functional completeness. Applicable to the following scenarios: optimize Agent performance, improve code quality, enhance execution efficiency, resolve performance bottlenecks, refactor code structure, enhance functional completeness, fix architectural issues, code quality improvement, performance tuning, code improvement, Agent upgrade, performance optimization, code refactoring, architecture optimization, quality improvement, security hardening, code fixes, performance enhancement, feature enhancement, code cleanup, eliminate duplicate code, simplify logic, improve maintainability, optimize algorithms, add caching, asynchronous processing, error handling improvements, log enhancement, documentation supplementation, test enhancement, security vulnerability fixes, input validation, permission control, dependency optimization, interface improvement, design pattern application, decoupling design, extensibility improvement.
tool_list: {"CONTEXT_AGENT_REGISTRY": [], "CAST_ANALYSIS": [], "CAST_PATCH": []}
---

# Agent Optimization Skill (Optimizer Skill)

## ⚠️ CRITICAL: Tool Usage Requirements

**MUST READ BEFORE USE:**

1. **CAST_ANALYSIS Tool**: 
   - ✅ MUST directly call the CAST_ANALYSIS tool function
   - ❌ DO NOT write Python code examples like "from aworld.experimental.ast import ACast"
   - ❌ DO NOT manually implement analysis logic
   - The tool is available - just call it with target_path and analysis_query

2. **CAST_PATCH Tool**:
   - ✅ MUST directly call the CAST_PATCH tool function to generate snapshots and deploy patches
   - ✅ MUST generate diff format patch text directly (not using tool)
   - ❌ DO NOT show Python code examples like "patches = [...]" to users
   - ❌ DO NOT manually write code to apply patches
   - The tool handles snapshot generation and patch deployment automatically

3. **Tool Results**:
   - Use the structured results returned by tools directly
   - Interpret and present the results, don't re-implement the functionality

## 📋 Skill Overview

The Agent Optimization Skill is an intelligent optimization tool based on AST (Abstract Syntax Tree) analysis and patch generation technology, specifically designed to analyze and improve the code quality, performance, and functional implementation of existing Agents. This skill combines static code analysis, dynamic optimization suggestion generation, and automated code patching capabilities.

## 🎯 Core Features

### 1. **Agent Discovery and Query**
- Use `CONTEXT_AGENT_REGISTRY` tool to query and locate target Agents
- Support finding Agents by name, type, and functional characteristics
- Provide Agent dependency relationship analysis

### 2. **Deep Code Analysis**
- Use `CAST_ANALYSIS` tool for comprehensive AST analysis
- Code quality assessment (complexity, readability, maintainability)
- Performance bottleneck identification (algorithm efficiency, memory usage, I/O operations)
- Architectural issue detection (coupling, cohesion, design patterns)
- Security vulnerability scanning (input validation, permission control, data leakage)

### 3. **Intelligent Optimization Solution Generation**
- Generate optimization suggestions based on analysis results
- Automatically identify code refactoring opportunities
- Recommend performance optimization strategies
- Design architectural improvement solutions

### 4. **Automated Code Patching**
- Use `CAST_PATCH` tool to generate snapshots and apply code patches
- Generate diff format patch text directly based on optimization plan
- Support automated implementation of multiple optimization types
- Maintain original functional completeness
- Generate before-and-after comparison reports

## 🔄 Core Workflow

### Phase 1: Agent Discovery and Selection
1. Receive user-specified Agent identifier (name/path/feature description)
2. Call CONTEXT_AGENT_REGISTRY tool to query matching Agents
3. Display found Agent information for user confirmation of optimization target
4. Verify Agent accessibility and modification permissions

### Phase 2: Deep Code Analysis
1. **MUST directly call CAST_ANALYSIS tool** - DO NOT write Python code examples
   - Use the CAST_ANALYSIS tool function directly with proper parameters
   - Provide the target Agent path and analysis query
   - The tool will perform comprehensive AST analysis automatically
   
   Analysis dimensions:
   - Code structure analysis: organization of classes, methods, and functions
   - Dependency analysis: imported modules, external API calls
   - Complexity analysis: cyclomatic complexity, cognitive complexity assessment
   - Performance analysis: algorithm time complexity, space complexity
   - Quality analysis: code style, comment quality, test coverage

2. Process and interpret the analysis results returned by CAST_ANALYSIS
   - Problem classification: performance issues, quality issues, architectural issues, security issues
   - Severity rating: high, medium, low three levels
   - Impact scope assessment: local impact, module-level impact, system-level impact
   - Optimization potential score: expected performance improvement range
   
   **CRITICAL**: The CAST_ANALYSIS tool returns structured analysis results - use these results directly, 
   do not attempt to re-implement the analysis logic.

### Phase 3: Optimization Strategy Formulation
1. Formulate optimization strategy based on analysis results
   - Performance optimization: algorithm optimization, caching mechanisms, asynchronous processing
   - Code refactoring: method extraction, duplicate elimination, logic simplification
   - Architectural improvement: decoupling design, pattern application, interface optimization
   - Quality enhancement: add comments, error handling, parameter validation

2. Generate optimization plan
   - Optimization item list: specific code locations and content to modify
   - Implementation order: sorted by dependency relationships and impact level
   - Risk assessment: potential risks of each optimization item
   - Rollback plan: recovery strategy when issues occur

### Phase 4: Snapshot Generation and Code Patching
1. **Generate snapshot using CAST_PATCH tool** - DO NOT write Python code examples
   - Use the CAST_PATCH.generate_snapshot tool function directly with target_dir
   - The tool will create a compressed snapshot (tar.gz) of the target directory
   - Snapshot file naming: `{path_suffix}_{version}.tar.gz` (e.g., `project_v0.tar.gz`)
   - This preserves the original state before applying any modifications
   - **IMPORTANT**: Always generate snapshot before making any code changes
2. 使用search_replace工具替换代码

### Phase 5: Verification and Reporting
1. Optimization effect verification
   - Run basic functionality tests to ensure compatibility
   - Performance benchmark tests comparing before and after optimization
   - Code quality metrics comparison analysis

2. Generate complete report
   - Optimization item summary: list of completed improvement items
   - Performance improvement report: specific performance improvement data
   - Quality improvement report: code quality metric improvements
   - Usage recommendations: how to deploy and use optimized Agent


### **Step 6: Dynamic Registration**
**MANDATORY FINAL STEP: Register the optimized agent with the current swarm.** Use the `CONTEXT_AGENT_REGISTRY` tool.

*   **Action**: `dynamic_register`
*   **Parameters**:
    *   `local_agent_name`: The name of the agent executing this workflow (e.g., "Aworld").
    *   `register_agent_name`: The name of the optimized agent (must match the `@agent` decorator name).
    - ⚠️ **CRITICAL**: Must be lowercase words connected by underscores (snake_case format)
    - ✅ **CORRECT**: `"simple_agent"`, `"my_custom_agent"`, `"data_processor"`
    - ❌ **WRONG**: `"SimpleAgent"`, `"my-agent"`, `"MyAgent"`, `"simpleAgent"`, `"simple agent"`

**Example**: `CONTEXT_AGENT_REGISTRY` tool call with params `{"local_agent_name": "Aworld", "register_agent_name": "optimized_agent"}`

**Important Notes**:
- This step is required after all code optimization and patching is complete
- The optimized agent must be registered to be available for use within the current swarm
- Ensure the agent name matches exactly with the `@agent` decorator in the optimized code
- Registration makes the optimized agent discoverable and executable by other components



## 🛠️ Tool Usage Instructions

### CONTEXT_AGENT_REGISTRY Tool
Purpose: Query and discover Agents that can be optimized
Usage scenarios:
- When user provides Agent name, perform precise lookup of corresponding Agent
- When user provides vague description, search for matching Agent list
- When performing batch optimization, retrieve all Agents of specific type

Output content:
- Agent basic information (name, path, version, author)
- Agent functional description and usage scenarios
- Agent dependency relationships and interface specifications
- Last modification time and version history

### CAST_ANALYSIS Tool
Purpose: Perform deep AST analysis on Agent code

**CRITICAL USAGE REQUIREMENT:**
- MUST directly call the CAST_ANALYSIS tool function - DO NOT write Python code examples
- DO NOT show code snippets like "from aworld.experimental.ast import ACast"
- DO NOT manually implement analysis logic
- The tool is already available - just call it with proper parameters

**⚠️ CRITICAL: Query Format Requirements for recall_impl:**

When calling CAST_ANALYSIS.recall_impl or any recall functionality, the `user_query` parameter MUST follow these strict rules:

1. **FORBIDDEN - Natural Language Queries:**
   - ❌ "PPTGeneratorAgent 类的方法实现，特别是 write_html_to_disk 和 async_policy 方法"
   - ❌ "获取 write_html_to_disk 方法的详细实现"
   - ❌ "显示第750-760行的具体内容"
   - ❌ "查找所有类方法的实现"

2. **REQUIRED - Regular Expression Patterns:**
   - ✅ `.*write_html_to_disk.*|.*async_policy.*`
   - ✅ `.*def write_html_to_disk.*`
   - ✅ `.*class PPTGeneratorAgent.*|.*def.*policy.*`
   - ✅ `.*def .*\(.*\):.*`

How to use:
1. Get the target Agent path (from CONTEXT_AGENT_REGISTRY or user input)
2. **Convert analysis requirements to regex patterns** (following above rules)
3. Directly call CAST_ANALYSIS tool with:
   - target_path: Path to the Agent directory
   - analysis_query: **MUST be regex pattern, NOT natural language**
   - Optional parameters: max_tokens, layer_strategy, etc.
4. Use the returned analysis results directly

Analysis dimensions (automatically performed by tool):
- Structure analysis: class hierarchy, method organization, module division
- Complexity analysis: cyclomatic complexity, nesting depth, function length
- Dependency analysis: external dependencies, internal call relationships, coupling
- Performance analysis: algorithm complexity, resource usage, potential bottlenecks
- Quality analysis: code style, comment coverage, naming conventions
- Security analysis: input validation, permission checks, sensitive information handling

Output format (returned by tool):
- Structured analysis report (JSON/YAML format)
- Problem list sorted by severity
- Optimization suggestions categorized summary
- Visual code structure diagrams

Example tool call (conceptual):
  CAST_ANALYSIS.analyze(
    target_path="/path/to/agent",
    analysis_query="分析代码结构、性能瓶颈和架构问题",
    max_tokens=3000,
    layer_strategy="comprehensive"
  )

## SEARCH_REPLACE工具 - 智能搜索替换功能

### 🎯 概述

SEARCH_REPLACE工具基于aider的核心算法实现，提供智能的代码搜索替换功能。它支持多种匹配策略，能够处理缩进不一致、格式差异等常见问题，特别适合精确的代码修改场景。

### 🔥 核心特性

#### 三种智能匹配策略

1. **精确匹配** (Exact Match)
   - 完全匹配搜索文本和目标代码
   - 最高优先级，性能最好

2. **空白字符灵活匹配** (Whitespace Flexible)
   - 忽略缩进差异，自动调整空白字符
   - 处理复制粘贴导致的缩进不一致

3. **模糊相似度匹配** (Similarity Match)
   - 基于文本相似度的智能匹配
   - 可配置相似度阈值(0.0-1.0)
   - 允许±10%的长度变化

#### 主要优势

- **智能容错**: 自动处理空白字符、缩进等格式问题
- **高成功率**: 多策略组合，显著提高匹配成功率
- **安全可靠**: 基于经过验证的aider算法
- **详细反馈**: 提供匹配策略和错误信息

### 🚀 使用方法

#### 工具调用接口

在CAST_PATCH工具中使用search_replace动作：

```python
# 调用CAST_PATCH.search_replace
action_params = {
    "operation_json": json.dumps({
        "operation": {
            "type": "search_replace",
            "file_path": "相对文件路径",
            "search": "要搜索的代码段",
            "replace": "替换后的代码段",
            "fuzzy_match": True,
            "similarity_threshold": 0.8
        }
    }),
    "source_dir": "/path/to/source",
    "show_details": True
}
```

#### JSON参数说明

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| type | string | ✓ | 固定值"search_replace" |
| file_path | string | ✓ | 相对于source_dir的文件路径 |
| search | string | ✓ | 要搜索的代码段 |
| replace | string | ✓ | 替换后的代码段 |
| fuzzy_match | boolean | ✗ | 是否启用模糊匹配(默认true) |
| similarity_threshold | float | ✗ | 相似度阈值0.0-1.0(默认0.8) |

#### 响应格式

**成功响应：**
```json
{
    "success": true,
    "modified": true,
    "file_affected": "example.py",
    "operation_type": "search_replace",
    "fuzzy_match_used": false,
    "match_strategy": "exact_match"
}
```

**失败响应：**
```json
{
    "success": false,
    "error": "未找到匹配的代码段进行替换",
    "suggestions": [
        "检查搜索文本是否存在于目标文件中",
        "尝试调整similarity_threshold参数",
        "确认文件路径是否正确",
        "检查搜索文本的格式和缩进"
    ]
}
```

### 💡 实际使用示例

#### 示例1：函数重命名

**原始代码:**
```python
def old_function():
    print("old implementation")
    return "old"
```

**操作JSON:**
```json
{
    "operation": {
        "type": "search_replace",
        "file_path": "example.py",
        "search": "def old_function():\n    print(\"old implementation\")\n    return \"old\"",
        "replace": "def new_function():\n    print(\"new implementation\")\n    return \"new\"",
        "fuzzy_match": true
    }
}
```

**结果:** 使用精确匹配策略成功替换

#### 示例2：处理缩进差异

**原始代码（有特定缩进）:**
```python
class MyClass:
        def old_method(self):
            print("old method")
            return True
```

**搜索文本（缩进不同）:**
```python
def old_method(self):
    print("old method")
    return True
```

**结果:** 使用空白字符灵活匹配策略自动调整缩进并成功替换

#### 示例3：模糊匹配处理

**原始代码:**
```python
def calculate_sum(a, b):
    # 这是一个计算函数
    result = a + b
    print(f"Result: {result}")
    return result
```

**搜索文本（忽略注释）:**
```python
def calculate_sum(a, b):
    result = a + b
    print(f"Result: {result}")
    return result
```

**结果:** 使用相似度匹配策略(0.925相似度)成功替换

### 🔧 技术实现细节

#### 核心算法结构

基于aider的EditBlockCoder实现，包含以下核心方法：

**主入口方法：**
```python
def search_replace_in_file(self, file_path: Path, search_text: str,
                          replace_text: str, fuzzy_match: bool = True,
                          similarity_threshold: float = 0.8) -> Dict[str, Any]
```

**核心匹配算法：**
```python
def _fuzzy_search_replace(self, content: str, search_text: str,
                         replace_text: str, fuzzy_match: bool = True,
                         similarity_threshold: float = 0.8) -> Optional[str]
```

#### 匹配算法优先级

1. 尝试精确匹配 (Perfect Match) - 最快
2. 尝试空白字符灵活匹配 (Whitespace Flexible) - 中等
3. 尝试相似度模糊匹配 (Similarity Match) - 较慢但最灵活
4. 返回失败结果

### 📝 最佳实践

#### ✅ 推荐做法

1. **逐步调整阈值**: 从高阈值(0.9)开始，失败后逐步降低到0.7
2. **保留关键结构**: 搜索文本应包含函数签名、类名等关键标识符
3. **避免过短文本**: 搜索文本至少包含2-3行代码
4. **测试验证**: 在正式环境前先在测试环境验证效果

#### ❌ 避免做法

1. **搜索单行**: 避免搜索过短的代码段，容易产生误匹配
2. **忽略语法**: 确保搜索和替换文本都是语法正确的代码
3. **过低阈值**: 避免使用低于0.6的相似度阈值
4. **批量操作**: 避免一次性处理多个复杂的搜索替换

### ⚠️ 重要注意事项

#### 使用场景选择

**使用SEARCH_REPLACE当：**
- 需要精确替换特定函数或方法
- 处理缩进或格式差异
- 重命名变量、函数或类
- 替换算法实现

### 🛡️ 安全保障

- **原子操作**: 搜索替换是原子操作，失败时不会部分修改文件
- **编码处理**: 自动处理UTF-8编码，支持中文等多语言
- **错误恢复**: 提供详细错误信息和修复建议
- **测试验证**: 核心算法经过完整的单元测试验证

这个SEARCH_REPLACE工具将显著提升精确代码修改的效率和成功率，特别适合需要智能匹配和容错处理的代码重构场景。



## 📚 Agent 代码结构参考示例 (Few-Shot Examples)

**⚠️ 重要说明：以下代码示例展示了标准的 Agent 代码结构，供生成 diff 格式 patch 文本时作为参考，确保生成的代码符合 AWorld 框架规范并能正常运行。**

在生成 diff 格式 patch 文本时，应参考以下标准代码结构，确保生成的代码：
- 导入语句正确且完整
- 类继承关系正确
- 装饰器使用规范
- 方法签名符合框架要求
- 代码风格与现有代码保持一致

### 标准 Agent 代码结构示例

**`simple_agent.py`**
```python
import os
from typing import Dict, Any, List

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.event.base import Message
# use logger to log
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PreLLMCallHook, PostLLMCallHook
from aworld_cli.core import agent
from simple_agent.mcp_config import mcp_config


@HookFactory.register(name="pre_simple_agent_hook")
class PreSimpleAgentHook(PreLLMCallHook):
    """Hook triggered before LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        # Important: This if-check cannot be removed and must match the current agent's name (here 'simple_agent').
        # This ensures the Hook only processes messages belonging to the current agent, avoiding side effects on other agents.
        if message.sender.startswith('simple_agent'):
            # ⚠️ Important Note: The Message object (aworld.core.event.base.Message) is the communication carrier between agents in AWorld.
            # It uses the 'payload' attribute to carry actual data, distinct from a direct 'content' attribute.
            # In PreLLMCallHook, message.payload is usually an Observation object. To access content, use message.payload.content.
            # Incorrect Example: message.content  # ❌ AttributeError: 'Message' object has no attribute 'content'
            # Correct Example: message.payload.content if hasattr(message.payload, 'content') else None  # ✅
            # Note: Do not modify message.payload or other input/output content here.
            # Hooks should be used for:
            # - Logging and monitoring
            # - Counting calls and performance metrics
            # - Permission checks or auditing
            # - Other auxiliary functions that do not affect I/O
            pass
        return message


@HookFactory.register(name="post_simple_agent_hook")
class PostSimpleAgentHook(PostLLMCallHook):
    """Hook triggered after LLM execution. Used for monitoring, logging, etc. Should NOT modify input/output content."""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        # Important: This if-check cannot be removed and must match the current agent's name (here 'simple_agent').
        # This ensures the Hook only processes messages belonging to the current agent.
        if message.sender.startswith('simple_agent'):
            # Note: Do not modify input/output content (like message.content) here.
            # Hooks should be used for:
            # - Logging and monitoring
            # - Counting calls and performance metrics
            # - Result auditing or quality checks
            # - Other auxiliary functions that do not affect I/O
            pass
        return message


class SimpleAgent(Agent):
    """A minimal Agent implementation capable of performing basic LLM calls."""

    def __init__(self, name: str, conf: AgentConfig = None, desc: str = None,
                 system_prompt: str = None, tool_names: List[str] = None, **kwargs):
        super().__init__(name=name, conf=conf, desc=desc, **kwargs)
        self.system_prompt = system_prompt or "You are a helpful AI assistant."
        self.model_name = conf.llm_config.llm_model_name if conf and conf.llm_config else "gpt-3.5-turbo"

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        # Important Notes:
        # 1. async_policy represents the model invocation; calling super().async_policy directly completes the LLM call.
        # 2. Do not modify the observation object within async_policy; the observation should remain immutable.
        # 3. Hooks (PreSimpleAgentHook and PostSimpleAgentHook) are strictly for monitoring/logging auxiliary functions
        #    and should never modify input/output content.
        return await super().async_policy(observation, info, message, **kwargs)


@agent(
    # ⚠️ CRITICAL: name MUST be lowercase words connected by underscores (snake_case)
    #   - ✅ CORRECT: "simple_agent", "my_custom_agent", "data_processor"
    #   - ❌ WRONG: "SimpleAgent", "my-agent", "MyAgent", "simpleAgent", "simple agent"
    #   - name should be unique and match the filename (without .py extension)
    name="simple_agent",
    desc="A minimal agent that can perform basic LLM calls"
)
def build_simple_swarm():
    # Create Agent configuration
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.1")),  # temperature = 0.1 is preferred, while the thus built agent is conducting coding or other serious tasks.
            params={"max_completion_tokens": 40960}
        )
    )

    # Extract all server keys from mcp_config
    mcp_servers = list(mcp_config.get("mcpServers", {}).keys())

    # Create SimpleAgent instance
    simple_agent = SimpleAgent(
        name="simple_agent",
        desc="A simple AI Agent specific for basic LLM calls and tool execution",
        conf=agent_config,
        # Note: If the Agent needs to read/write files, remind the agent in the system_prompt to use absolute paths.
        # Relative paths should be avoided. Use os.path.abspath() or Path(__file__).parent to resolve paths.
        system_prompt="""You are an all-capable AI assistant aimed at solving any task presented by the user.
                        ## 1. Self Introduction
                        *   **Name:** DeepResearch Team.
                        *   **Knowledge Boundary:** Do not mention your LLM model or other specific proprietary models outside your defined role.

                        ## 2. Methodology & Workflow
                        Complex tasks must be solved step-by-step using a generic ReAct (Reasoning + Acting) approach:

                        1.  **Task Analysis:** Break down the user's request into sub-tasks.
                        2.  **Tool Execution:** Select and use the appropriate tool for the current sub-task.
                        3.  **Analysis:** Review the tool's output. If the result is insufficient, try a different approach or search query.
                        4.  **Iteration:** Repeat the loop until you have sufficient information.
                        5.  **Final Answer:** Conclude with the final formatted response.

                        ## 3. Critical Guardrails
                        1.  **Tool Usage:**
                            *   **During Execution:** Every response MUST contain exactly one tool call. Do not chat without acting until the task is done.
                            *   **Completion:** If the task is finished, your VERY NEXT and ONLY action is to provide the final answer in the `<answer>` tag. Do not call almost any tool once the task is solved.
                        2.  **Time Sensitivity:**
                            *   Your internal knowledge cut-off is 2024. For questions regarding current dates, news, or rapidly evolving technology, YOU ENDEAVOR to use the `search` tool to fetch the latest information.
                        3.  **Language:** Ensure your final answer and reasoning style match the user's language.
                        """,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config
    )

    # Return the Swarm containing this Agent
    return Swarm(simple_agent)
```

**`mcp_config.py`**
```python
mcp_config = {
    "mcpServers": {
        "browser": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.browser"
            ],
            "env": {
                "LLM_MODEL_NAME": "${LLM_MODEL_NAME}",
                "LLM_API_KEY": "${LLM_API_KEY}",
                "LLM_BASE_URL": "${LLM_BASE_URL}"
            },
            "client_session_timeout_seconds": 9999.0
        },
        "csv": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.documents.mscsv"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "docx": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.documents.msdocx"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "download": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.download"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "xlsx": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.documents.msxlsx"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "image": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.media.image"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "pdf": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.documents.pdf"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "pptx": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.documents.mspptx"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "search": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.search"
            ],
            "env": {
                "GOOGLE_API_KEY": "${GOOGLE_API_KEY}",
                "GOOGLE_CSE_ID": "${GOOGLE_CSE_ID}"
            },
            "client_session_timeout_seconds": 9999.0
        },
        "terminal": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.terminal"
            ]
        },
        "video": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.media.video"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "wayback": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.wayback"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "wikipedia": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.wiki"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "txt": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.documents.txt"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        }
    }
}
```

### 关键代码结构要点

在生成 diff 格式 patch 文本时，请确保生成的代码遵循以下规范：

1. **导入语句规范**：
   - 标准库导入在前（如 `os`, `typing`）
   - 第三方库导入在中
   - 项目内部导入在后（如 `aworld.*`, `aworld_cli.*`）
   - 相对导入放在最后

2. **Hook 类规范**：
   - 必须使用 `@HookFactory.register(name="...")` 装饰器
   - Hook 类必须继承 `PreLLMCallHook` 或 `PostLLMCallHook`
   - `exec` 方法必须是 `async` 方法，返回 `Message` 对象
   - 必须检查 `message.sender` 以确保只处理当前 Agent 的消息
   - 不要修改 `message.payload` 或其他输入/输出内容

3. **Agent 类规范**：
   - 必须继承 `Agent` 基类
   - `__init__` 方法必须调用 `super().__init__()`
   - `async_policy` 方法签名必须符合框架要求：`async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None, **kwargs) -> List[ActionModel]`
   - 不要修改 `observation` 对象（它是不可变的）

4. **装饰器函数规范**：
   - 必须使用 `@agent(name="...", desc="...")` 装饰器
   - `name` 必须是小写字母和下划线连接的 snake_case 格式
   - `name` 必须唯一且与文件名（不含 .py 扩展名）匹配
   - 装饰器函数必须返回 `Swarm` 对象

5. **配置对象规范**：
   - `AgentConfig` 和 `ModelConfig` 的使用方式
   - 环境变量的读取方式（`os.environ.get()`）
   - MCP 配置的引用方式

6. **代码风格**：
   - 保持与现有代码的缩进风格一致（通常是 4 个空格）
   - 添加适当的注释说明关键逻辑
   - 遵循 Python PEP 8 代码风格规范


## ⚠️ CRITICAL: Tool Usage Rules

**DO NOT:**
- ❌ Write Python code examples showing how to use ACast, ACastAnalyzer, etc.
- ❌ Show code snippets like "from aworld.experimental.ast import ACast"
- ❌ Manually implement analysis or patching logic
- ❌ Display patches list as Python code examples to users
- ❌ Generate diff format patch text without explaining line number sources
- ❌ **Include multiple files in a single patch_content**
- ❌ **Include multiple code blocks in a single patch_content**
- ❌ **Use natural language queries in CAST_ANALYSIS.recall_impl calls**
  - ❌ "PPTGeneratorAgent 类的方法实现，特别是 write_html_to_disk 和 async_policy 方法"
  - ❌ "显示第750-760行的具体内容"
  - ❌ "获取 write_html_to_disk 方法的详细实现"

**DO:**
- ✅ Directly call CAST_ANALYSIS tool function with proper parameters
- ✅ **Use ONLY regex patterns in CAST_ANALYSIS.recall_impl queries**
  - ✅ `.*write_html_to_disk.*|.*async_policy.*`
  - ✅ `.*def write_html_to_disk.*`
- ✅ Directly call CAST_PATCH.generate_snapshot to create snapshots
- ✅ **⚠️ CRITICAL: MUST perform file content verification before generating patch text:**
  - **MANDATORY**: Use CAST_ANALYSIS.recall_impl to read actual file content around calculated insertion point
  - **MANDATORY**: Verify that calculated line numbers match actual file content
  - **MANDATORY**: Adjust insertion position based on verification results
  - **MANDATORY**: Use verified file content as context lines in diff format
- ✅ **Generate diff format patch text directly (not using tool)**
- ✅ **⚠️ CRITICAL: Each patch_content can ONLY contain changes for ONE FILE**
- ✅ **⚠️ CRITICAL: Each patch_content should ONLY modify ONE CODE BLOCK at a time**
- ✅ **⚠️ MUST explain line number sources and verification results before generating patch text:**
  - Which tool call provided the line numbers (e.g., CAST_ANALYSIS.analyze_repository)
  - Which data field was used (e.g., Symbol.end_line, Symbol.line_number)
  - **File content verification process and results**
  - **Insertion position calculation** (e.g., end_line + 1 = 754 for insertion after function)
  - **Context start line calculation** (e.g., 750 for diff format, which is different from insertion position)
  - **⚠️ CRITICAL**: Must clearly distinguish between insertion position (754) and context start line (750)
  - **Adjustment based on verification** (e.g., if verification shows 754 is not suitable, use 755 instead)
  - How the line numbers were verified and any corrections made
- ✅ Use the results returned by tools directly
- ✅ Show tool call results and interpretation, not implementation code
