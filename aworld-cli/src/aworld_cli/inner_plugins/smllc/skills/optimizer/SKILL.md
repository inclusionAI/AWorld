---
name: optimizer
description: Analyzes and automatically optimizes an existing Agent's code by applying patches to improve performance, quality, security, and functionality.
tool_list: {"AGENT_REGISTRY": [], "CAST_ANALYSIS": [], "CAST_CODER": [], "CAST_SEARCH": []}
---
# Agent Optimization Skill (Optimizer)

## 📌 Mandatory Usage Guidelines

**CRITICAL: READ BEFORE USE.** Adherence to these rules is essential for the skill to function correctly.

1.  **Tool Calls are Direct**:
    *   ✅ **DO** call tool functions like `CAST_ANALYSIS(...)` and `CAST_CODER(...)` directly.
    *   ❌ **DO NOT** write or show Python code examples that import or manually implement tool logic (e.g., `from aworld.experimental.ast import ACast`). The tools are pre-loaded and ready for direct invocation.

2.  **`CAST_ANALYSIS` Query Format**:
    *   ✅ **DO** use **regular expression (regex) patterns** for all `search_ast` queries.
        *   *Example*: `.*MyClassName.*|.*my_function_name.*`
    *   ❌ **DO NOT** use natural language for `search_ast` queries.
        *   *Incorrect*: `"Show me the implementation of the MyClassName class"`

3.  **`CAST_CODER` Workflow**:
    *   ✅ **DO** use `CAST_CODER.generate_snapshot` to create a backup before any modifications.
    *   ✅ **DO** generate patch content (either structured JSON for `search_replace` or `diff` format text) based on your analysis. The LLM's role is to *create* the patch content.
    *   ✅ **DO** use `CAST_CODER` actions (like `search_replace`) to *apply* the generated patch content to the source code.
    *   ❌ **DO NOT** show Python lists of patches to the user (e.g., `patches = [...]`).

4.  **Patch Content Rules**:
    *   ✅ **DO** ensure each patch operation targets **only one file**.
    *   ✅ **DO** create focused patches that modify **one logical block of code at a time** for clarity and safety.
    *   ✅ **DO** verify code with `CAST_ANALYSIS.search_ast` to get accurate line numbers and context before generating a `diff`.

## 📜 Skill Overview

The **Optimizer Skill** is an advanced agent capability designed to analyze and enhance other agents. It leverages Abstract Syntax Tree (AST) analysis to systematically improve an agent's behavior and performance.

It achieves this by focusing on an agent's core behavioral drivers: its **system prompt** (which controls its reasoning and workflow) and its **tool configuration** (mcp_config.py) (which defines its capabilities). By intelligently patching these high-impact areas, the Optimizer can rapidly correct flaws and expand an agent's functionality. This skill treats the target agent as a codebase, applying static analysis and automated patching to achieve its goals.

## ⭐ Strategic Optimization Focus
While this skill can perform any code modification, effective agent optimization primarily targets the two core behavioral drivers: The System Prompt and The Tool Configuration. Your analysis and proposed solutions must prioritize these areas.

1. **The System Prompt (Primary Target)**
*   **What it is**: The system_prompt string variable within the agent's main Python file (e.g., simple_agent.py).
*   **Why it's critical**: It governs the agent's entire reasoning process, workflow logic, persona, current time awareness, constraints, and output format. Most behavioral problems (e.g., incorrect task sequencing, ignoring instructions, wrong output format, unawareness of the current date) are solved by refining the prompt code.
*   **Your Action**: Analyze the prompt for ambiguity, missing steps, or weak constraints. Propose specific, surgical additions or modifications to the prompt text to correct the agent's behavior. 
      Example, to fix a workflow where the agent does A then C instead of A then B, you would strengthen the "Methodology & Workflow" section of its prompt. Example, to fix the agent's unawareness of the current time, you should add the dynamic argument (such as `datetime.now(ZoneInfo("Asia/Shanghai"))` with datetime and ZoneInfo explicitly imported in the simple_agent.py) as the current date with the corresponding description ('Your own data is cutoff to the year 2024, so current date is xxxx, please keep in mind!') in the prompt code, to let the agent be aware of the current time.

2. **The Tool Configuration (mcp_config.py)**
*   **What it is**: The mcp_config dictionary, typically in a dedicated mcp_config.py file.
*   **Why it's critical**: It defines the agent's capabilities. A missing capability (e.g., inability to search the web, read a PDF) is almost always due to a missing tool entry in this configuration.
*   **Your Action**: If an agent lacks a required function, your first step is to verify if the corresponding tool is missing from mcp_config.py. Add the necessary tool configuration block to grant the agent that capability.
*   **MCP Configuration**: Which MCP servers (e.g., pptx, google) are required? The terminal server is a mandatory, non-negotiable tool for every agent you build. It is essential for two primary reasons:
    * **Dependency Management**: Installing missing Python packages via pip install.
    * **File System Operations**: Verifying the current location (pwd) and saving all output files to that consistent, predictable location. You must ensure this tool is always included.

**Core Principle**: Always assume the problem lies in the system_prompt or mcp_config.py first. Only resort to modifying other parts of the Python code if the issue cannot be resolved through these two primary vectors (e.g., adding support for a dynamic variable in the prompt).


## 🎯 Core Features
*   **Agent Discovery**: Locates target agents within the environment using the `AGENT_REGISTRY`.
*   **Deep Code Analysis**: Performs comprehensive AST-based analysis via the `CAST_ANALYSIS` tool to identify bottlenecks, security risks, and architectural flaws.
*   **Intelligent Refactoring**: Generates specific, actionable optimization strategies and code modification plans based on the analysis.
*   **Automated Patching**: Creates codebase snapshots and applies structured code changes using the `CAST_CODER` toolset.

## 🔄 Core Workflow
### Phase 1: Discovery and Selection
1.  **Identify Target**: Receive an agent identifier (name, path, or description) from the user.
2.  **Query Registry**: Call `AGENT_REGISTRY` to find the specified agent(s).
3.  **Confirm Target**: Present the located agent's information to the user for confirmation.

### Phase 2: Deep Code Analysis
1.  **Invoke Analyzer**: Call the `CAST_ANALYSIS` tool with the target agent's path and a precise analysis query. The tool automatically performs a multi-faceted analysis:
    *   **Structure**: Class/function organization, module dependencies.
    *   **Complexity**: Cyclomatic and cognitive complexity scores.
    *   **Performance**: Potential bottlenecks, inefficient algorithms.
    *   **Quality**: Code style, comments, maintainability metrics.
    *   **Security**: Basic checks for common vulnerabilities.
2.  **Interpret Results**: Process the structured report from `CAST_ANALYSIS` to classify issues by severity (High, Medium, Low) and formulate an initial optimization approach.


### Phase 3: Deep Architecture Analysis & Fusion (MANDATORY)

This is where you demonstrate your architectural expertise. You will deconstruct reference agents to extract their core patterns and then fuse them into a new design.

#### Part A: Deconstruction and Analysis
**1. Foundation Analysis (search)**
- **Action:** First, you **MUST** locate the search agent using `AGENT_REGISTRY.list_desc`.
- **Analysis:** Read its SKILL.md using `CAST_SEARCH.read_file`. Your goal is to internalize its foundational architecture: robust ReAct loop, comprehensive error handling, safe file I/O rules, and multi-tool coordination logic. This is your baseline for all new agents.

**2. Specialist Analysis (Other Relevant Agents)**
- **Goal:** To find a specialized agent whose unique logic can be fused with the search foundation.
- **Action (Discovering Specialists):** You must now methodically search both sources for a relevant specialist:
  **Source 1: Built-in Agents**
  - **Command:** Use the AGENT_REGISTRY tool to list all platform-provided skills.
    ```text
    AGENT_REGISTRY.list_desc(source_type="built-in")
    ```
  - **Analysis:** Review the description of each agent returned from the command. Identify and select the agent whose purpose is most specifically aligned with the user's current request.

- **Deep Dive Analysis:** Once you have selected the most relevant specialist agent, read its SKILL.md using `CAST_SEARCH.read_file`. You must now perform a comparative analysis against search. Ask yourself:
    - What is this agent's "secret sauce"? What unique rules, steps, or principles are in its system prompt that are NOT in search's?
    - How is its workflow different? Does it have a specific multi-step process for its domain (e.g., for financial analysis: 1. gather data, 2. perform calculation, 3. add disclaimer, 4. format output)?
    - What are its specialized guardrails? What does it explicitly forbid or require?

**This analysis is critical. You must identify the unique DNA of the specialist agent to be fused into your new design.**

#### Part B: Synthesis and Fusion
**3. Architectural Fusion:** Now, you will construct the new agent's `system_prompt`. This is a fusion process, not a simple copy-paste.
- **Start with the Foundation:** Begin with the robust, general-purpose instruction set you analyzed from search (planning, tool use, file safety, etc.).
- **Inject the Specialization:** Carefully layer the specialist agent's "secret sauce" on top of the search foundation. This means integrating its unique workflow steps, domain-specific rules, and specialized output formats. The new prompt should feel like search's powerful engine has been custom-tuned for a specific purpose.

**4. Tool Configuration:** Based on this fused architecture, define the final `mcp_config` and `tool_list`. It should include search's foundational tools (like terminal, search) plus any specialized tools required by the new task.

**If no reference clearly fits the requirement, skip this step and proceed to Step 3.**

### Phase 4: Optimization Strategy
1.  **Formulate Plan**: Based on the user's goal and the initial analysis, formulate a precise modification plan. Your plan must adhere to the Strategic Optimization Focus:
*  **Analyze High-Impact Files**: Your first step is to call CAST_ANALYSIS.search_ast to retrieve the contents of the agent's main file (to inspect the system_prompt) and its mcp_config.py.
*  **Prioritize Prompt/Tooling**: Determine if the problem can be solved by modifying the system_prompt or adding/editing a tool in mcp_config.py. This is the preferred solution for most behavioral and capability issues.
*  **Fallback to Code Logic**: If and only if the optimization cannot be achieved through the prompt or tool configuration, identify the specific Python code block that needs to be refactored.
2.  **Generate Operations**: Create a list of specific modification operations (e.g., a JSON object for CAST_CODER.search_replace). Each operation must be atomic, targeting a single code block in a single file.

### Phase 5: Snapshot and Patching
1.  **Create Snapshot**: **Crucial first step.** Call `CAST_CODER.generate_snapshot` with the target agent's directory to create a compressed backup (`.tar.gz`). This ensures a safe rollback point.
2.  **Apply Patches**: Execute the modification plan by calling `CAST_CODER` operations. The preferred method is `search_replace` for its precision and resilience to formatting differences.
    *   Each operation should be atomic and target a single file.
3.  **Verify Changes**: After patching, perform a quick check to ensure the code remains valid and the change was applied as expected.

### Phase 6: Verification and Reporting
1.  **Validate Effects**: (Optional but recommended) Run unit tests or a basic functional check to ensure no regressions were introduced. Compare pre- and post-optimization metrics if applicable.
2.  **Generate Report**: Summarize the analysis findings, the list of applied changes, and the expected benefits for the user.

### Phase 7: Dynamic Registration
**MANDATORY FINAL STEP:** Register the newly optimized agent to make it discoverable and usable within the current swarm.

*   **Tool**: `AGENT_REGISTRY`
*   **Action**: `dynamic_register`
*   **Parameters**:
    *   `local_agent_name`: The name of the agent executing this workflow (e.g., "Aworld").
    *   `register_agent_name`: The snake_case name of the optimized agent (must match the `@agent` decorator).
*   **Example**:
    ```json
    AGENT_REGISTRY.dynamic_register(local_agent_name="Aworld", register_agent_name="optimized_simple_agent")
    ```

---
## 🛠️ Tool Reference

<details>
<summary><h3>AGENT_REGISTRY Tool</h3></summary>

**Purpose**: Discover and retrieve information about existing agents.

**Actions**:
*   `query()`: Search for agents by name, description, or other metadata.
*   `dynamic_register()`: Register a new or modified agent into the current environment's registry, making it active.

**Usage**: Essential for the first (Discovery) and last (Registration) steps of the workflow.

</details>

<details>
<summary><h3>CAST_ANALYSIS Tool</h3></summary>

**Purpose**: Perform deep, AST-based static analysis of Python code.

**Primary Actions**:
*   `analyze_repository()`: Conduct a broad analysis of an entire agent directory to find symbols, complexities, and potential issues.
*   `search_ast()`: Fetch the precise source code for specific symbols (classes, functions) or line ranges.

**Critical Usage Note for `search_ast`**:
The `analysis_query` for this action **MUST** be a regular expression. Natural language queries are not supported and will fail.

*   ✅ **Correct (Regex)**: `user_query=".*MyClass.*|.*my_function.*"`
*   ❌ **Incorrect (Natural Language)**: `user_query="Find the MyClass class and the my_function function"`, `user_query=".*mcp_config\\.py."`, `user_query=".*"`

**Output**: Returns structured JSON data containing detailed information about the code's structure, complexity, and identified issues, which serves as the foundation for the optimization strategy.

</details>

<details>
<summary><h3>CAST_CODER Tool</h3></summary>

**Purpose**: A suite of functions for safely modifying source code files. It handles operations like creating backups and applying intelligent code replacements.

---
#### **Action: `generate_snapshot`**

Creates a compressed (`.tar.gz`) backup of a source directory before modifications are applied.

*   **Parameters**:
    *   `target_dir`: The path to the directory to be backed up.
*   **Usage**: This should **always** be the first action in the patching phase to ensure recoverability.

---
#### **Action: `search_replace`**

Intelligently finds and replaces a block of code in a specified file. This is the **preferred method for applying patches** as it is robust against minor formatting differences. It is based on `aider`'s core matching algorithm.

**Key Features**:
*   **Exact Match**: First attempts a direct, character-for-character match.
*   **Whitespace Flexible Match**: If an exact match fails, it retries while ignoring differences in leading whitespace and indentation. This handles most copy-paste formatting issues.
*   **Similarity Match**: (Optional) If other methods fail, uses a fuzzy text similarity algorithm to find the best match.

**How to Call**:
The operation is defined in a JSON string passed to the `operation_json` parameter.

```python
# Conceptual tool call
action_params = {
    "operation_json": json.dumps({
        "operation": {
            "type": "search_replace",
            "file_path": "path/to/your/file.py",
            "search": "CODE_BLOCK_TO_FIND",
            "replace": "NEW_CODE_BLOCK",
            "exact_match_only": true
        }
    }),
    "source_dir": "/path/to/agent/root", // Base directory for the operation
    "show_details": True
}
CAST_CODER.search_replace(**action_params)
```

**JSON Parameters**:

| Parameter              | Type    | Required | Description                                               |
| ---------------------- | ------- | :------: |-----------------------------------------------------------|
| `type`                 | string  |    ✓     | Must be `"search_replace"`.                               |
| `file_path`            | string  |    ✓     | The relative path to the file from `source_dir`.          |
| `search`               | string  |    ✓     | This field must contain one or more complete lines of the source code.                |
| `replace`              | string  |    ✓     | The multi-line code block to replace it with.             |
| `exact_match_only`     | boolean | -        | fixed as true (Optional, for documentation purposes only) |

**Best Practices**:
* search: The multi-line code block to search for.
    *   Use multi-line `search` blocks that include structural context (like `def` or `class` lines) for better accuracy.
    *   must not be blank!
    *   If the content consists of multiple lines, the content must be continuous and match the source code.

</details>

---

## 📚 Agent Code Structure Reference (Few-Shot Examples)

**⚠️ IMPORTANT**: The following code examples illustrate the standard AWorld agent structure. When generating patch content (`diff` format or for `search_replace`), you **MUST** ensure the resulting code adheres to these conventions to maintain compatibility and correctness within the framework. Pay close attention to imports, class definitions, decorators, and method signatures.

### Standard Agent Code Structure (`simple_agent.py`)
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

    # Mandatory Use - You must use this.
    sandbox = Sandbox(
        mcp_config=mcp_config
    )
    sandbox.reuse = True

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
                        0.  ** Module Dependency Install:** If found relevant modules missing, please use the terminal tool to install the appropriate module.
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
                            * Today is datetime.now(ZoneInfo("Asia/Shanghai")).year (year)-datetime.now(ZoneInfo("Asia/Shanghai")).month (month)-datetime.now(ZoneInfo("Asia/Shanghai")).day(day).
                            * Your internal knowledge cut-off is 2024. For questions regarding current dates, news, or rapidly evolving technology, YOU ENDEAVOR to use the `search` tool to fetch the latest information.
                        3.  **Language:** Ensure your final answer and reasoning style match the user's language.
                        4.  **File & Artifact Management (CRITICAL):**
                            *   **Unified Workspace:** The current working directory is your **one and only** designated workspace.
                            *   **Execution Protocol:** All artifacts you generate (code scripts, documents, data, images, etc.) **MUST** be saved directly into the current working directory. You can use the `terminal` tool with the `pwd` command at any time to confirm your current location.
                            *   **Strict Prohibition:** **DO NOT create any new subdirectories** (e.g., `./output`, `temp`, `./results`). All files MUST be placed in the top-level current directory where the task was initiated.
                            *   **Rationale:** This strict policy ensures all work is organized, immediately accessible to the user, and prevents polluting the file system with nested folders.
                        """,
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
        sandbox=sandbox
    )

    # Return the Swarm containing this Agent
    return Swarm(simple_agent)
```

### Standard MCP Configuration (`mcp_config.py`)
```python
mcp_config = {
    "mcpServers": {
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
        "txt": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.documents.txt"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "ms-playwright": {
            "command": "npx",
            "args": [
                "@playwright/mcp@latest",
                "--no-sandbox",
                "--isolated",
                "--output-dir=/tmp/playwright",
                "--timeout-action=10000",
            ],
            "env": {
                "PLAYWRIGHT_TIMEOUT": "120000",
                "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
            }
        }
    }
}
```