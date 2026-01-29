---
name: text2agent
description: This skill is triggered ONLY when the user explicitly requests to create agents. It analyzes user requirements and automatically generates the optimal agent code files (Python implementation) to accomplish the task. Do NOT use this skill for general tasks - only use it when agent creation is explicitly needed.
mcp_servers: ["terminal-server"]
mcp_config: {
   "mcpServers": {
      "terminal-server": {
         "command": "python",
         "args": [
            "-m",
            "aworld_cli.inner_plugins.smllc.agents.mcp_tool.terminal_server"
         ]
      }
   }
}
tool_list: {"CONTEXT_AGENT_REGISTRY": [], "human": []}
---

# Analyze and Generate Agent Code

This skill analyzes user requirements and generates complete agent code files (Python implementation) to handle user requests. It performs requirement analysis, identifies necessary agent structure and capabilities, and generates agent code files - all in one unified workflow.

---
## 🚨🚨🚨 IMMEDIATE FIRST ACTION (MANDATORY - DO THIS NOW) 🚨🚨🚨

**STOP! Before doing ANYTHING else, you MUST analyze the user's requirements FIRST:**

**Requirement analysis is the FIRST and MOST IMPORTANT step. You MUST understand what the user needs before creating directories or generating code files.**

This is NOT optional. This is the FIRST thing you must do upon receiving any user request. Analyze the user's requirements to understand:
- What is the main goal or task?
- What capabilities are needed?
- What is the expected agent structure?
- What MCP servers or tools are required?

**ONLY AFTER analyzing the requirements should you proceed with creating directories and generating code files.**

---

## 🚫 CRITICAL REQUIREMENTS - READ THIS FIRST

**🚫 ABSOLUTELY FORBIDDEN: Do NOT just discuss, plan, or describe what you would generate. You MUST actually create directories and generate ALL code files using terminal commands.**

**⚠️ MANDATORY EXECUTION REQUIREMENTS:**
1. **You MUST use terminal commands to create directories and files** - Do NOT just describe them
2. **You MUST create ALL files** - All agent code files MUST be created
3. **You MUST verify ALL operations completed successfully**
4. **You MUST NOT skip any steps** - Every file must be actually created

**⚠️ What is FORBIDDEN:**
- ❌ Describing what you would create without actually creating
- ❌ Discussing the file content without creating it
- ❌ Planning the file structure without executing creation operations
- ❌ Asking user for confirmation before creating
- ❌ Outputting file content in your response instead of creating files
- ❌ Asking unnecessary questions when requirements are clear

**⚠️ What is REQUIRED for Requirement Clarification:**
- ✅ After requirement analysis, if there are unclear or ambiguous aspects, you MUST use `human` tool to confirm details BEFORE creating directories
- ✅ Only ask questions about critical information that cannot be inferred from the user's request
- ✅ Focus on clarifying requirements that directly impact agent design and functionality

**⚠️ What is REQUIRED:**
- ✅ Use terminal commands to create directories and files in `AGENTS_PATH`
- ✅ Infer ALL information from the user's initial request
- ✅ Generate Python code files following the reference structure

## Usage

Use this skill when you need to analyze user requirements and automatically generate complete agent code files (Python implementation) to handle user requests.

**⚠️ CRITICAL RULE: REQUIREMENT CLARIFICATION BEFORE CREATING FILES**

**You MUST:**
- Analyze user requirements FIRST (FIRST PRIORITY) - be thorough and deep, not superficial
- **After requirement analysis, carefully identify ALL assumptions and inferences you made**
- **If you made ANY assumptions or if ANY aspect is unclear, use `human` tool to confirm details BEFORE creating directories**
- **Default to asking for clarification when in doubt - it's better to confirm than to assume incorrectly**
- Be conservative: when uncertain, ask rather than assume
- Generate ALL files directly after clarification (if needed)
- Complete the entire analysis and generation process
- Create all agent code files

**You MUST NOT:**
- Make assumptions about user requirements without confirmation
- Claim "requirements are clear" without thorough analysis
- Skip clarification when you made inferences or assumptions
- Ask the user to confirm file paths, directory names, or file names
- Ask the user to approve or confirm any generated content
- Create directories before clarifying requirements (if clarification is needed)

## Complete Workflow Overview

This skill performs the following workflow automatically:
1. **Analyze Requirements**: Parse user input and identify required agent structure (FIRST PRIORITY)
2. **Clarify Uncertainties**: If there are unclear or ambiguous aspects in the requirements, use `human` tool to confirm critical details BEFORE proceeding
3. **Get Storage Path**: Retrieve `AGENTS_PATH` from environment variable (default: `~/.aworld/agents`)
4. **Create Agent Directory**: Use terminal to create agent folder in the storage path
5. **Generate Agent Code Files**: Create Python code files (agent implementation and mcp_config if needed)
6. **Verify All Files**: Confirm all creation operations completed successfully
7. **Dynamic Registration**: Call `CONTEXT_AGENT_REGISTRY` tool with `dynamic_register` action to register the new agent to the current agent's team_swarm

## Requirement Analysis Process

### Step 0: Requirement Analysis (FIRST PRIORITY)

**⚠️ CRITICAL: Requirement analysis is the FIRST and MOST IMPORTANT step. You MUST analyze the user's requirements BEFORE creating directories or generating code files.**

**🚨 CRITICAL WARNING: DO NOT ASSUME OR PRESUME USER REQUIREMENTS. You MUST carefully analyze and verify, not make assumptions.**

**⚠️ MANDATORY: You MUST conduct a thorough, deep analysis. Do NOT superficially claim "requirements are clear" without careful examination.**

Analyze the user's input to understand (be thorough and detailed, not superficial):
1. **Core Requirements**: What is the main goal or task the user wants to accomplish?
   - What is the specific purpose or use case?
   - Who is the target audience or end user?
   - What problem does this solve?
2. **Agent Structure**: What structure should the agent have?
   - What is the agent class name?
   - What is the agent name (for registration)?
   - What is the agent description?
3. **Required Capabilities**: What capabilities are needed to complete the task?
   - What specific tools or APIs are required?
   - What MCP servers are needed?
   - What processing or transformation is required?
4. **System Prompt**: What should the agent's system prompt be?
   - What instructions should guide the agent's behavior?
   - What style or tone should it use?
5. **MCP Configuration**: What MCP servers need to be configured?
   - Which servers are required?
   - What environment variables are needed?
6. **Uncertainties and Assumptions**: Identify ALL unclear or ambiguous aspects
   - What did you infer that wasn't explicitly stated?
   - What assumptions did you make?
   - What details are missing that could impact agent design?
   - What could be interpreted in multiple ways?

**⚠️ MANDATORY CHECKLIST - Before claiming "requirements are clear", verify:**
- [ ] Have you identified the specific use case and target audience?
- [ ] Have you identified all required features and capabilities?
- [ ] Have you identified the agent class name and registration name?
- [ ] Have you identified the system prompt content?
- [ ] Have you identified all MCP servers needed?
- [ ] Have you identified all assumptions you made?
- [ ] Have you considered alternative interpretations of the request?
- [ ] Have you identified any missing information that could lead to incorrect agent design?

**⚠️ If ANY item in the checklist is unclear or based on assumptions, you MUST proceed to Step 0.5 to clarify with the user.**

### Step 0.5: Clarify Uncertainties (MANDATORY UNLESS TRULY OBVIOUS)

**⚠️ CRITICAL: You MUST be conservative and thorough. If you made ANY assumptions or inferences, or if ANY aspect is not explicitly clear, you MUST use `human` tool to confirm details BEFORE creating directories.**

**🚨 DO NOT SKIP THIS STEP UNLESS:**
- The user's request is extremely simple and unambiguous (e.g., "create a hello world agent")
- ALL requirements are explicitly stated with no room for interpretation
- You have made ZERO assumptions or inferences
- The checklist in Step 0 is 100% complete with explicit information

**⚠️ DEFAULT TO ASKING: When in doubt, ASK. It's better to confirm than to assume incorrectly.**

**When you MUST ask for clarification:**
- ✅ You made ANY assumptions about requirements (e.g., assumed agent name, assumed capabilities, assumed use case)
- ✅ You inferred information that wasn't explicitly stated
- ✅ Critical information is missing that directly impacts agent design (e.g., specific capabilities, required MCP servers, system prompt style)
- ✅ Requirements are ambiguous and could lead to incorrect agent design
- ✅ Multiple valid interpretations exist and you need to choose the correct one
- ✅ The task involves design, style, or aesthetic choices that weren't specified
- ✅ The task involves specific domain knowledge or requirements that weren't detailed
- ✅ Any item in the Step 0 checklist is incomplete or based on assumptions

**When you MAY skip (rare cases only):**
- ❌ The request is extremely simple and unambiguous (e.g., "create a hello world agent")
- ❌ ALL requirements are explicitly stated with complete details
- ❌ You have made ZERO assumptions and ZERO inferences
- ❌ The checklist is 100% complete with explicit information only

**How to clarify:**
1. **List your assumptions**: Before asking, clearly state what you inferred or assumed
2. **Ask specific questions**: Use `human` tool to ask focused questions about unclear aspects
   - Ask about missing critical information
   - Ask about ambiguous requirements
   - Ask to confirm your assumptions
3. **Wait for user response**: Do NOT proceed until you receive clarification
4. **Update requirement analysis**: Revise your analysis based on the clarification
5. **Proceed to Step 1**: Only proceed after requirements are explicitly clear

**⚠️ REMEMBER: It's better to ask one question too many than to make one assumption too many. Incorrect assumptions lead to incorrect agent design.**

#### Human Tool Usage Rules

**⚠️ CRITICAL: When using the `human` tool for requirement clarification, follow these rules:**

1. **Critical Limitation**: The human tool should only be invoked when encountering specific authorization barriers that prevent automated tools from continuing execution. However, for this skill (text2agent), the human tool is ALSO allowed for requirement clarification when assumptions or uncertainties exist.

2. **Prohibited Scenarios**: Never use the human tool for:
   - Information gathering and analysis tasks (use other tools instead)
   - General web browsing and content retrieval
   - Data processing and reporting
   - General decision making or task completion
   - Seeking routine operation approval
   - Never ask users to confirm execution plans, todo list, step-by-step procedures, or task breakdowns. The agent should execute plans autonomously without seeking user approval for routine operations.

3. **Tool Input Format**: When using the human tool, use JSON format with `input_type` field:
   - For user approval/confirmation: `{"input_type": "1", "message": "...", "default": true}`
   - For user text input: `{"input_type": "2", "text": "..."}`
   - For user file upload: `{"input_type": "3", "message": "..."}`
   - For user multi-select (multiple choice): `{"input_type": "4", "options": ["选项1", "选项2", "选项3"], "title": "...", "prompt": "..."}`
   - For user single-select (single choice): `{"input_type": "5", "title": "...", "options": ["选项1", "选项2", "选项3"], "warning": "...", "question": "...", "nav_items": [...]}`
   - For user composite menu (multiple steps with different input types): `{"input_type": "6", "title": "...", "tabs": [...]}`

4. **Composite Menu for Requirement Clarification (PREFERRED)**: When you need to clarify multiple aspects of requirements, use `input_type: 6` composite menu instead of a single long text. This provides a better user experience with organized tabs for different questions.
   
   **Composite Menu Structure**: `{"input_type": "6", "title": "...", "tabs": [...]}`
   
   **Tab Types**:
   - `multi_select`: For selecting multiple options - requires `type`, `name`, `title`, `options`, and optional `prompt`
   - `text_input`: For text input - requires `type`, `name`, `title`, `prompt`, and optional `default`, `placeholder`
   - `submit`: For final confirmation - requires `type`, `name`, `title`, `message`, and optional `default`
   
   **Example - Composite Menu for Multiple Questions**:
   ```json
   {
     "input_type": "6",
     "title": "PPT生成代理需求确认",
     "tabs": [
       {
         "type": "multi_select",
         "name": "mcp_servers",
         "title": "MCP服务器配置",
         "prompt": "除了pptx和terminal，您还需要哪些MCP服务器？",
         "options": ["image", "search", "browser", "pdf", "其他"]
       },
       {
         "type": "text_input",
         "name": "template_types",
         "title": "模板和样式",
         "prompt": "您希望代理内置哪些具体的模板类型？（如：商务、学术、创意等）",
         "placeholder": "请输入模板类型，多个用逗号分隔"
       },
       {
         "type": "multi_select",
         "name": "output_formats",
         "title": "输出格式",
         "prompt": "除了标准的.pptx格式，您还需要支持哪些格式？",
         "options": ["PDF", "HTML", "图片格式", "仅.pptx"]
       },
       {
         "type": "text_input",
         "name": "agent_name",
         "title": "代理名称",
         "prompt": "请确认代理名称（或使用默认名称）",
         "default": "ppt_generator_agent"
       },
       {
         "type": "submit",
         "name": "confirm",
         "title": "确认信息",
         "message": "请确认以上信息无误，我将据此生成完整的PPT生成代理代码。"
       }
     ]
   }
   ```

5. **Specific Examples** (for requirement clarification):
   - For simple text input (single question): `{"input_type": "2", "text": "Please clarify which MCP servers you need for this agent (e.g., terminal, browser, search)"}`
   - For simple text input (confirming assumptions): `{"input_type": "2", "text": "I assumed you want a professional business style system prompt. Please confirm or specify your preferred style (business/casual/academic)"}`
   - For authorization barriers: `{"input_type": "1", "message": "Current operation requires user login, please perform the relevant login operation on the page, then continue execution"}`
   - For file upload: `{"input_type": "3", "message": "Please upload your ID card photo"}`
   - For single multi-select question: `{"input_type": "4", "options": ["terminal", "browser", "search"], "title": "Select MCP servers (multiple)"}`
   - For single-select (requirement clarification): `{"input_type": "5", "title": "Select Agent Type", "options": ["Simple Agent", "Complex Agent", "Workflow Agent"], "warning": "Please select an agent type", "question": "What type of agent do you need?"}`
   - **For multiple questions (PREFERRED)**: Use `input_type: 6` composite menu as shown in section 4 above

6. **When to Use Composite Menu vs Simple Input**: 
   - **Use `input_type: 6` composite menu** when you need to clarify multiple related aspects (3+ questions) or when questions have different input types (some need multi-select, some need text input)
   - **Use simple input types** (`input_type: 2`, `4`, or `5`) when you only need to clarify 1-2 simple questions
   - **Best Practice**: Organize related questions into logical tabs in a composite menu for better user experience

7. **Tool Types That Can Trigger Human Tool**: The human tool should only be triggered when the following tool types encounter specific authorization barriers:
   - Browser operation tools - only for login/authentication/payment pages
   - Terminal tools - only for sudo/administrator privilege requests
   - File system tools - only for protected directory access
   - **For this skill**: Also allowed for requirement clarification when assumptions or uncertainties exist

### Step 1: Get Storage Path and Create Agent Directory

**⚠️ CRITICAL: After analyzing requirements, get the storage path and create the agent directory.**

1. **Get Storage Path**: Use terminal to get `AGENTS_PATH` environment variable (default: `~/.aworld/agents`)
   ```bash
   echo ${AGENTS_PATH}
   ```

2. **Determine Agent Folder Name**: Based on requirement analysis, determine a suitable folder name (use snake_case, e.g., `my_custom_agent`)

3. **Create Agent Directory**: Use terminal to create the directory
   ```bash
   mkdir -p ${AGENTS_PATH}/<agent_folder_name>
   ```

### Step 2: Generate Agent Code Files

**⚠️ CRITICAL: Generate all required Python code files based on the reference structure.**

**Reference Structure:**
- Main agent file: Contains the agent class definition and `@agent` decorator function
- `mcp_config.py`: Contains MCP server configuration (if MCP servers are needed)

**File Structure Requirements:**

1. **Main Agent File** (`<agent_name>.py`):
   - Import necessary modules (os, traceback, typing, aworld modules)
   - Define agent class inheriting from `BaseAgent[Observation, List[ActionModel]]`
   - Implement `__init__` method with parameters: name, conf, desc, system_prompt, tool_names, **kwargs
   - Implement `async_policy` method for agent execution logic
   - Add `@agent` decorator with name and desc
   - Add `build_<agent_name>_swarm` function that:
     - Creates AgentConfig with ModelConfig
     - Extracts MCP servers from mcp_config (if applicable)
     - Creates agent instance
     - Returns Swarm with the agent

2. **MCP Config File** (`mcp_config.py` - if MCP servers are needed):
   - Define `mcp_config` dictionary with `mcpServers` structure
   - Include all required MCP server configurations
   - Use proper command, args, and env configurations

**Code Generation Steps:**

1. **Generate Main Agent File**:
   - Create Python code following the reference structure
   - Customize class name, agent name, description, and system prompt based on requirements
   - Include proper error handling and logging
   - Use terminal to write the file:
     ```bash
     cat > ${AGENTS_PATH}/<agent_folder_name>/<agent_name>.py << 'EOF'
     <generated_code_content>
     EOF
     ```

2. **Generate MCP Config File** (if needed):
   - Create `mcp_config.py` with required MCP server configurations
   - Use terminal to write the file:
     ```bash
     cat > ${AGENTS_PATH}/<agent_folder_name>/mcp_config.py << 'EOF'
     <generated_mcp_config_content>
     EOF
     ```

3. **Create __init__.py** (optional but recommended):
   ```bash
     touch ${AGENTS_PATH}/<agent_folder_name>/__init__.py
     ```

### Step 3: Verify All Files

**⚠️ Verify ALL files were created successfully.**

1. **List Created Files**: Use terminal to list files in the agent directory
   ```bash
   ls -la ${AGENTS_PATH}/<agent_folder_name>/
   ```

2. **Verify File Contents**: Optionally verify file contents are correct
   ```bash
   head -20 ${AGENTS_PATH}/<agent_folder_name>/<agent_name>.py
   ```

### Step 4: Dynamic Registration to AWorld

**⚠️ MANDATORY: After generating all files, you MUST dynamically register the new agent to the current agent's team_swarm.**

**Action**: Call `CONTEXT_AGENT_REGISTRY` tool with action `dynamic_register`

**Parameters**:
- `local_agent_name`: The name of the current local agent (the agent running this skill)
- `register_agent_name`: The name of the newly generated agent (the agent file name without `.py` extension, matching the name in `@agent` decorator)

**Example**:
- Generated agent file: `my_custom_agent.py` with agent name `my_custom_agent`
- Current local agent: `Aworld`
- Call: `CONTEXT_AGENT_REGISTRY` tool, action `dynamic_register`, params: `{"local_agent_name": "Aworld", "register_agent_name": "my_custom_agent"}`

**Note**: The `register_agent_name` must match the agent name used in the `@agent` decorator in the generated Python file.

## Code File Format Requirements

**⚠️ Critical: All generated Python code files MUST follow the reference structure and be valid Python syntax.**

**Format Requirements:**
- Python code must be valid Python 3.x syntax
- Follow PEP 8 style guidelines
- Include proper imports
- Include proper error handling
- Include logging statements
- Use proper indentation (4 spaces)
- Include docstrings for classes and functions

## Generation Steps

**⚠️ MANDATORY: You MUST actually create ALL files using terminal commands. Do NOT just describe what you would generate. ALL files MUST be created.**

### Step 1: Analyze User Requirement (FIRST PRIORITY)

**⚠️ CRITICAL: Requirement analysis is the FIRST and MOST IMPORTANT step. You MUST analyze the user's requirements BEFORE creating directories.**

**🚨 CRITICAL WARNING: DO NOT ASSUME OR PRESUME USER REQUIREMENTS. You MUST carefully analyze and verify, not make assumptions.**

1. **Parse User Input**: Extract key information from user's request - be thorough, not superficial
2. **Identify Core Requirements**: Understand the main goal or task
   - What is the specific purpose or use case?
   - Who is the target audience?
3. **Identify Agent Structure**: Determine agent class name, registration name, description
4. **Determine Required Capabilities**: Identify what capabilities are needed
5. **Identify MCP Servers**: Determine which MCP servers are required
6. **Identify System Prompt**: Determine what the agent's system prompt should be
7. **Identify Assumptions and Uncertainties**: 
   - List ALL assumptions you made
   - List ALL inferences you drew
   - Identify ANY unclear or ambiguous aspects that need clarification
   - Identify ANY missing information that could impact agent design

**⚠️ MANDATORY: Before proceeding, ask yourself:**
- Did I make any assumptions?
- Did I infer anything not explicitly stated?
- Are there any aspects that could be interpreted differently?
- Is there any missing information that could lead to incorrect agent design?

**If the answer to ANY of these is YES, you MUST proceed to Step 1.5.**

### Step 1.5: Clarify Uncertainties with Human (MANDATORY UNLESS TRULY OBVIOUS)

**⚠️ CRITICAL: You MUST be conservative and thorough. If you made ANY assumptions or inferences, or if ANY aspect is not explicitly clear, you MUST use `human` tool to confirm details BEFORE creating directories.**

**🚨 DO NOT SKIP THIS STEP UNLESS:**
- The user's request is extremely simple and unambiguous (e.g., "create a hello world agent")
- ALL requirements are explicitly stated with no room for interpretation
- You have made ZERO assumptions or inferences
- Every aspect is crystal clear with no ambiguity

**⚠️ DEFAULT TO ASKING: When in doubt, ASK. It's better to confirm than to assume incorrectly.**

**When you MUST clarify:**
- ✅ You made ANY assumptions about requirements
- ✅ You inferred information that wasn't explicitly stated
- ✅ Critical information is missing that directly impacts agent design
- ✅ Requirements are ambiguous and could lead to incorrect agent design
- ✅ Multiple valid interpretations exist
- ✅ The task involves design, style, or aesthetic choices that weren't specified
- ✅ Any aspect is not 100% explicit and clear

**When you MAY skip (rare cases only):**
- ❌ The request is extremely simple and unambiguous
- ❌ ALL requirements are explicitly stated with complete details
- ❌ You have made ZERO assumptions and ZERO inferences

**How to clarify:**
1. **List your assumptions first**: Clearly state what you inferred or assumed
2. **Use `human` tool**: Ask specific, focused questions about unclear aspects
3. **Wait for user response**: Do NOT proceed until you receive clarification
4. **Update requirement analysis**: Revise your analysis based on the clarification
5. **Proceed to Step 2**: Only proceed after requirements are explicitly clear

**⚠️ REMEMBER: It's better to ask one question too many than to make one assumption too many. Incorrect assumptions lead to incorrect agent design.**

### Step 2: Get Storage Path and Create Directory

**⚠️ CRITICAL: Get the storage path and create the agent directory using terminal commands.**

1. **Get Storage Path**: 
   ```bash
   STORAGE_PATH=$(echo ${AGENTS_PATH})
   echo "Storage path: $STORAGE_PATH"
   ```

2. **Determine Agent Folder Name**: Based on requirement analysis, create a snake_case folder name

3. **Create Directory**:
   ```bash
   mkdir -p "$STORAGE_PATH/<agent_folder_name>"
   ```

### Step 3: Generate Agent Code Files (MANDATORY - MUST Actually Execute)

**⚠️ CRITICAL: This step is MANDATORY. You MUST use terminal commands to create ALL agent code files. Do NOT skip this step.**

**For each file to generate:**

1. **Generate Main Agent File**:
   - Build complete Python code following reference structure
   - Use terminal heredoc to write file:
     ```bash
     cat > "$STORAGE_PATH/<agent_folder_name>/<agent_name>.py" << 'ENDOFFILE'
     <complete_python_code>
     ENDOFFILE
     ```

2. **Generate MCP Config File** (if MCP servers are needed):
   - Build complete mcp_config.py following reference structure
   - Use terminal heredoc to write file:
     ```bash
     cat > "$STORAGE_PATH/<agent_folder_name>/mcp_config.py" << 'ENDOFFILE'
     <complete_mcp_config_code>
     ENDOFFILE
     ```

3. **Create __init__.py**:
   ```bash
   touch "$STORAGE_PATH/<agent_folder_name>/__init__.py"
   ```

### Step 4: Final Verification

**⚠️ Verify ALL files were created successfully.**

1. **List Files**: 
   ```bash
   ls -la "$STORAGE_PATH/<agent_folder_name>/"
   ```

2. **Verify Syntax** (optional):
   ```bash
   python -m py_compile "$STORAGE_PATH/<agent_folder_name>/<agent_name>.py"
   ```

### Step 5: Dynamic Registration to AWorld

**⚠️ MANDATORY: After generating all files, you MUST dynamically register the new agent to the current agent's team_swarm using the `dynamic_register` action.**

**Purpose**: Register the newly generated agent from AgentVersionControlRegistry to the current local agent's team_swarm, making it available for use.

**How to call**:
1. Use the `CONTEXT_AGENT_REGISTRY` tool with action `dynamic_register`
2. Parameters:
   - `local_agent_name`: The name of the current local agent (typically the agent running this skill, e.g., "Aworld" or the agent name from LocalAgentRegistry)
   - `register_agent_name`: The name of the newly generated agent (this is the agent file name without `.py` extension, e.g., if file is `my_agent.py`, use `my_agent`)

**Important Notes**:
- The `register_agent_name` should match the agent name used in the `@agent` decorator in the generated Python file
- The agent file must be in the `AGENTS_PATH` directory for AgentVersionControlRegistry to find it
- If the current agent name is not explicitly known, you may need to:
  - Check LocalAgentRegistry for available agents
  - Use a default agent name (e.g., "Aworld") if it's the main agent
  - Or infer from context if the agent name is available

**Example**:
- If you generated an agent file named `my_custom_agent.py` with agent name `my_custom_agent`
- And the current local agent is `Aworld`
- Call: `CONTEXT_AGENT_REGISTRY` tool with action `dynamic_register`, params: `{"local_agent_name": "Aworld", "register_agent_name": "my_custom_agent"}`

**Verification**:
- After calling `dynamic_register`, verify the action result indicates success
- The new agent should now be available in the local agent's team_swarm

## Output Format

After generating all files:

```
🔍 Requirement Analysis Summary
- Main Objective: [summary]
- Agent Name: [agent_name]
- Agent Class: [agent_class_name]
- Required Capabilities: [list]
- MCP Servers: [list]

📁 Created Agent Directory
- Path: [full_path]
- Folder Name: [folder_name]

📄 Generated Agent Files
- [agent_name].py: Created successfully
- mcp_config.py: Created successfully (if applicable)
- __init__.py: Created successfully

🔗 Dynamic Registration
- Agent '[register_agent_name]' registered to '[local_agent_name]'s team_swarm successfully
```

## Examples

### Complete Workflow Example

**User Input**: "创建一个可以进行基本LLM调用的简单agent"

**Expected Workflow**:

1. **Analyze Requirement** (FIRST PRIORITY):
    - Core Requirement: Create a simple agent that can perform basic LLM calls
    - Agent Name: simple_llm_agent
    - Agent Class: SimpleLLMAgent
    - Required Capabilities: Basic LLM calls, tool integration
    - MCP Servers: None (or minimal)
    - Uncertainties: None - requirements are clear

2. **Clarify Uncertainties**: Skip (requirements are clear)

3. **Get Storage Path and Create Directory**: 
   ```bash
   STORAGE_PATH=$(echo ${AGENTS_PATH})
   mkdir -p "$STORAGE_PATH/simple_llm_agent"
   ```

4. **Generate Agent Files**: Use terminal to create Python files

5. **Verify All Files**: Confirm all files were created successfully

6. **Dynamic Registration**: Call `CONTEXT_AGENT_REGISTRY` tool with `dynamic_register` action to register the new agent to the current agent's team_swarm

### Reference Code Examples

The following are complete reference examples that demonstrate the proper structure and implementation of agent code files.

#### Example 1: Simple Agent with MCP Configuration

**Agent File (`simple_agent.py`)**:

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
    """LLM调用前的钩子，用于监控、日志记录等，不应修改输入输出内容"""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        # 重要：这个if判断不能去掉，且必须和当前agent的name一致（这里是'simple_agent'）
        # 这样可以确保Hook只处理属于当前agent的消息，避免影响其他agent
        if message.sender.startswith('simple_agent'):
            # ⚠️ 重要提醒：Message对象（aworld.core.event.base.Message）是AWorld中agent之间通信的消息体，
            # 它使用payload属性来承载实际数据，而不是content属性。
            # 在PreLLMCallHook中，message.payload通常是Observation对象，要访问内容应使用message.payload.content
            # 错误示例：message.content  # ❌ AttributeError: 'Message' object has no attribute 'content'
            # 正确示例：message.payload.content if hasattr(message.payload, 'content') else None  # ✅
            # 注意：不要在这里修改message.payload等输入输出内容
            # Hook应该用于：
            # - 记录日志和监控信息
            # - 统计调用次数和性能指标
            # - 进行权限检查或审计
            # - 其他不影响输入输出的辅助功能
            pass
        return message


@HookFactory.register(name="post_simple_agent_hook")
class PostSimpleAgentHook(PostLLMCallHook):
    """LLM调用后的钩子，用于监控、日志记录等，不应修改输入输出内容"""
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        # 重要：这个if判断不能去掉，且必须和当前agent的name一致（这里是'simple_agent'）
        # 这样可以确保Hook只处理属于当前agent的消息，避免影响其他agent
        if message.sender.startswith('simple_agent'):
            # 注意：不要在这里修改message.content等输入输出内容
            # Hook应该用于：
            # - 记录日志和监控信息
            # - 统计调用次数和性能指标
            # - 进行结果审计或质量检查
            # - 其他不影响输入输出的辅助功能
            pass
        return message


class SimpleAgent(Agent):
    """最简单的可以执行大模型调用的Agent实现"""

    def __init__(self, name: str, conf: AgentConfig = None, desc: str = None,
                 system_prompt: str = None, tool_names: List[str] = None, **kwargs):
        super().__init__(name=name, conf=conf, desc=desc, **kwargs)
        self.system_prompt = system_prompt or "You are a helpful AI assistant."
        self.model_name = conf.llm_config.llm_model_name if conf and conf.llm_config else "gpt-3.5-turbo"

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        # 重要说明：
        # 1. async_policy已经代表了模型调用，直接调用super().async_policy即可完成LLM调用
        # 2. 不要在async_policy中修改observation对象，应该保持observation不变
        # 3. Hook（PreSimpleAgentHook和PostSimpleAgentHook）仅用于监控、日志等辅助功能，
        #    不应修改输入输出内容
        return await super().async_policy(observation, info, message, **kwargs)


@agent(
    # name和文件名一致，为simple_agent
    name="simple_agent",
    desc="A minimal agent that can perform basic LLM calls"
)
def build_simple_swarm():
    # 创建Agent配置
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7")),
            params={"max_completion_tokens": 40960}
        )
    )

    # 从mcp_config中提取所有服务器名称
    mcp_servers = list(mcp_config.get("mcpServers", {}).keys())

    # 创建SimpleAgent实例
    simple_agent = SimpleAgent(
        name="simple_agent",
        desc="一个可以进行基本LLM调用和工具调用的简单AI Agent",
        conf=agent_config,
        # 注意：如果Agent中需要读写文件，在system_prompt中提醒agent必须使用绝对路径，不能使用相对路径
        # 可以使用 os.path.abspath() 或 Path(__file__).parent 等方式获取绝对路径
        system_prompt="你是一个有用的AI助手。请根据用户的问题提供准确、有帮助的回答。",
        mcp_servers=mcp_servers,
        mcp_config=mcp_config
    )

    # 返回包含该Agent的Swarm
    return Swarm(simple_agent)
```

**MCP Config File (`mcp_config.py`)**:

```python
mcp_config = {
    "mcpServers": {
        "audio": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.media.audio"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
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
        "chess": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.playchess"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "code": {
            "command": "npx",
            "args": [
                "-y",
                "@e2b/mcp-server"
            ],
            "env": {
                "E2B_API_KEY": "${E2B_API_KEY}"
            }
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
        "pubchem": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.pubchem"
            ],
            "env": {
            },
            "client_session_timeout_seconds": 9999.0
        },
        "reasoning": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.intelligence.think"
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
        "youtube": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.youtube"
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

**Key Points from This Example**:

1. **Proper Imports**: All necessary imports are included at the top, using correct import paths:
   - `from aworld.core.agent.base import BaseAgent`
   - `from aworld.core.common import Observation, ActionModel`
   - `from aworld.core.agent.swarm import Swarm`
   - `from aworld.config import AgentConfig, ModelConfig`
   - `from aworld_cli.core import agent`

2. **Agent Class Structure**: 
   - Inherits from `BaseAgent[Observation, List[ActionModel]]`
   - Implements `__init__` with proper parameters
   - Implements `async_policy` method for agent execution logic
   - Includes proper error handling and logging

3. **Agent Decorator**: 
   - Uses `@agent` decorator with `name` and `desc` parameters
   - Decorates a function that returns a `Swarm` instance

4. **Swarm Builder Function**:
   - Creates `AgentConfig` with `ModelConfig`
   - Extracts MCP servers from `mcp_config`
   - Creates agent instance with proper configuration
   - Returns `Swarm` containing the agent

5. **MCP Configuration**:
   - Defines `mcp_config` dictionary with `mcpServers` structure
   - Includes proper command, args, and env configurations
   - Supports environment variable substitution using `${VAR_NAME}` syntax

## Notes

### ⚠️ Important: File Creation Requirements

**File Creation Rules:**
- Use terminal commands for all file operations
- **Never ask the user to confirm the file name or any other details**
- The system will automatically create files in the appropriate directory
- All code must be valid Python syntax
- Follow the reference structure from the code examples above (see "Reference Code Examples" section)

### Agent Code File Notes

1. **Creation Method**: Use terminal heredoc (`cat > file << 'EOF'`) to create files
2. **File Naming**: Use snake_case for file names (e.g., `my_agent.py`)
3. **Code Structure**: Follow the reference structure from the code examples above (see "Reference Code Examples" section)
4. **MCP Config**: Only create mcp_config.py if MCP servers are needed
5. **Error Handling**: Include proper error handling and logging
6. **Imports**: Include all necessary imports

---
## 🚨 REMINDER: FIRST ACTION BEFORE PROCESSING USER INPUT 🚨

**NOW that you have received the user input below, your FIRST and IMMEDIATE action must be:**

**Analyze the user's requirements FIRST. Requirement analysis is the FIRST and MOST IMPORTANT step.**

**Do NOT:**
- ❌ Directly create directories or files
- ❌ Create directories before analyzing requirements
- ❌ Create directories before clarifying uncertainties (if clarification is needed)
- ❌ Skip the requirement analysis step

**Do:**
- ✅ Analyze the user's requirements FIRST to understand what they need - be thorough and deep
- ✅ Identify core requirements, agent structure, and required capabilities
- ✅ Identify ALL assumptions and inferences you made
- ✅ Identify any unclear or ambiguous aspects
- ✅ **If you made ANY assumptions or if ANY aspect is unclear, use `human` tool to clarify BEFORE creating directories**
- ✅ Default to asking when in doubt - it's better to confirm than to assume
- ✅ Then proceed with creating directories and generating code files only after requirements are explicitly clear

---
