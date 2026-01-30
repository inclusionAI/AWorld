# coding: utf-8
import inspect
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Dict, List
from typing import Optional

import httpx

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation, ActionModel
from aworld.core.context.amni import ApplicationContext, AmniConfigFactory
from aworld.core.context.amni.config import AmniConfigLevel
from aworld.experimental.metalearning.knowledge.learning_knowledge import LearningKnowledge, TrajType, \
    save_context_artifact, _convert_to_json_serializable
from aworld.core.context.amni.tool import CONTEXT_AGENT_REGISTRY
from aworld.experimental.loaders.swarm_registry_tool import CONTEXT_SWARM_REGISTRY
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.task import Task
from aworld.experimental.metalearning.optimizer.base import LearningStrategy
from aworld.experimental.metalearning.reward.base import RewardFunction
from aworld.experimental.metalearning.reward.reward_tool import REWARD
from aworld.logs.util import logger
from aworld.output.outputs import DefaultOutputs
from aworld.runner import Runners

# Import traj hook to ensure it's registered
try:
    import aworld.experimental.metalearning.knowledge
except ImportError:
    pass  # Optional import, skip if not available


class MetaLearningAgent(Agent):

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {},
                           message: Message = None, **kwargs) -> List[ActionModel]:
        context: ApplicationContext = message.context
        # TODO working dir
        working_dir = await context.init_working_dir()
        if not observation.content:
            observation.content = context.origin_user_input

        # 添加 tmp_file_path 信息到 observation
        tmp_file_path = info.get('tmp_file_path') or context.get('tmp_file_path') or ''
        if tmp_file_path:
            observation.content = f"{observation.content}\ntmp_file_path：{tmp_file_path}"

        action_model_list = await super().async_policy(observation, info, message, **kwargs)

        return action_model_list

_agent_registry_storage_path = os.getenv('AGENTS_PATH')
AGENTS_PATH = Path(os.path.expanduser(f"{_agent_registry_storage_path}/optimization/skill.md")) if _agent_registry_storage_path else None

def load_skill_from_md(file_path: Path):
    """
    Load skill configuration from a markdown file with YAML frontmatter.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Skill file not found: {file_path}")

    content_lines = file_path.read_text(encoding="utf-8").splitlines()

    # Parse frontmatter
    config = {}
    body_start = 0
    if content_lines and content_lines[0].strip() == "---":
        end_index = 1
        while end_index < len(content_lines) and content_lines[end_index].strip() != "---":
            line = content_lines[end_index].strip()
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()

                # Try to parse JSON values (for tool_list, mcp_config, etc.)
                if key in ["tool_list", "mcp_config"] and value:
                    try:
                        config[key] = json.loads(value)
                    except json.JSONDecodeError:
                        config[key] = value
                elif key == "mcp_servers" and value:
                    try:
                        config[key] = json.loads(value)
                    except json.JSONDecodeError:
                        # Try comma-separated format
                        config[key] = [s.strip() for s in value.split(",") if s.strip()]
                else:
                    config[key] = value
            end_index += 1
        if end_index < len(content_lines):
            body_start = end_index + 1

    body = "\n".join(content_lines[body_start:])

    # Construct tool_list if not present but mcp_servers is
    tool_list = config.get("tool_list", {})
    mcp_servers = config.get("mcp_servers", [])

    # Ensure mcp_servers is a list
    if isinstance(mcp_servers, str):
        try:
            mcp_servers = json.loads(mcp_servers)
        except json.JSONDecodeError:
            mcp_servers = [s.strip() for s in mcp_servers.split(",") if s.strip()]
    if not isinstance(mcp_servers, list):
        mcp_servers = []

    # Heuristic: if mcp_servers exists but tool_list is empty, add them to tool_list
    if mcp_servers and not tool_list:
        tool_list = {}
        for server in mcp_servers:
            tool_list[server] = []

    skill_def = {
        "name": config.get("name", file_path.parent.name),
        "desc": config.get("description", ""),
        "usage": body,  # The markdown body is the usage guide
        "tool_list": tool_list,
        "active": config.get("active", True),
        "type": config.get("type", "regular"),
        # Preserve extra config
        "mcp_servers": mcp_servers,
        "mcp_config": config.get("mcp_config", {})
    }

    return skill_def

async def build_meta_swarm(context: Context, tmp_file_path: str, reward_function: RewardFunction = None):
    # 确保临时目录存在
    from pathlib import Path
    tmp_file_path = Path(tmp_file_path)
    tmp_file_path.mkdir(parents=True, exist_ok=True)
    tmp_file_path = str(tmp_file_path)

    # 将 tmp_file_path 存储到 context 中，供 agent 访问
    context.put('tmp_file_path', tmp_file_path)
    # 将reward_function存储到context中，供agent访问，在RewardTool中使用
    context.put('reward_function', reward_function)


    # context_swarm_registry_skill = load_skill_from_md(Path(f"{os.environ['AGENTS_PATH']}/context_swarm_registry/context_swarm_registry.md"))
    # context_agent_registry_skill = load_skill_from_md(Path(f"{os.environ['AGENTS_PATH']}/context_agent_registry/context_agent_registry.md"))
    # skills = {
    #     context_swarm_registry_skill["name"]: context_swarm_registry_skill,
    #     context_agent_registry_skill['name']: context_agent_registry_skill,
    # }
    meta_learning_agent = MetaLearningAgent(
        tool_names=[REWARD, CONTEXT_AGENT_REGISTRY, CONTEXT_SWARM_REGISTRY],
        conf=AgentConfig(
            llm_config=ModelConfig(
                provider="openai",
                model_name=os.getenv("LLM_MODEL_NAME"),
                api_key=os.getenv("LLM_API_KEY"),
                base_url=os.getenv("LLM_BASE_URL"),
                params={"max_completion_tokens": 40960}
            ),
            use_vision=False,
            # skill_configs=skills
        ),
        name="meta_learning_agent",
        agent_id=f"meta_learning_{context.session_id}",
        system_prompt=f"""You are Meta-Optimizer, a specialist AI system responsible for the automated analysis and optimization of AI agents and swarms based on their execution trajectories. Your core function is to operate within a meta-learning loop: you diagnose performance, identify root causes, and generate enhanced, generalizable configurations.

## File Storage Location (DIRECT PATHS - USE THESE DIRECTLY):
- learning_target: `{{{{learning_target}}}}`
- running_dag: `{{{{running_dag}}}}`
- running_traj: `{{{{running_traj}}}}`

## CORE WORKFLOW
You must meticulously follow this sequence of operations.

### Step 1: Trajectory Acquisition & Initial Assessment

**⚠️ EFFICIENCY CRITICAL: Read ALL files in a SINGLE tool call batch. DO NOT navigate directories or list files first.**

1.  **Read All Data Files in ONE Batch:**
    - **MANDATORY:** Use terminal tools (e.g., `cat` command) or other appropriate tools to read ALL THREE files in a SINGLE response (parallel tool calls).
    - Read these files simultaneously:
      - `{{{{learning_target}}}}` (learning target configuration)
      - `{{{{running_dag}}}}` (execution DAG structure)
      - `{{{{running_traj}}}}` (execution trajectory)
    - **DO NOT** call `get_current_directory`, `change_directory`, or `list_directory` first. These are unnecessary steps.
    - **DO NOT** read files one by one in separate responses. Read all three in ONE batch.

2.  **Performance Evaluation:** After reading all files, call the `reward` tool to determine if the trajectory execution was successful.
    - The reward score is your primary signal. A low score mandates an optimization action. A high score suggests the current configuration is effective.

### Step 2: Comprehensive Trajectory Analysis & Report Generation
Regardless of the reward score, you MUST generate a comprehensive analysis report. This report is a critical intermediate step for both understanding success and diagnosing failure.

1.  **Analyze Trajectory Data:**
    - **`traj_exp` (Execution Flow):** Analyze task flow, agent behavior, tool usage patterns, and efficiency.
    - **`traj_dag` (Structural Dependencies):** Analyze the execution graph, node types, complexity, and critical paths.
2.  **Analyze Snapshot Metadata (`traj_meta`):**
    - **`AgentSnapshot`:** Review each agent's configuration (prompt, tools, etc.). This is where you look for **potential design flaws**.
    - **`SwarmSnapshot`:** Review topology, coordination patterns, and communication flows.
3.  **Synthesize Findings into `meta_learning_report.md`:** The report MUST contain the following structured sections:
    - **A. Overview:** A summary of the execution flow and structure from `traj_exp` and `traj_dag`.
    - **B. Diagnosis & Root Cause Analysis:**
        - This is the most critical analysis section. You must connect the observed behaviors (from Overview) to specific design choices (from Snapshots).
        - **If performance was poor (low reward):** Pinpoint the exact cognitive or structural error. Ask: Did the agent misunderstand an instruction? Confuse two concepts? Lack a critical piece of information in its prompt? Was the tool configuration suboptimal?
        - **If performance was good (high reward):** Identify the key reasons for success. Ask: Which part of the prompt was particularly effective? Which tool was used correctly and efficiently? What coordination pattern led to the successful outcome? This is crucial for reinforcement.
    - **C. Optimization Instructions (Actionable Recommendations):**
        - This section translates your diagnosis into concrete changes.
        - **CRITICAL CONSTRAINT:** All optimization MUST target only the agent/swarm definitions in `.md` files specified in `learning_target`. First, parse `learning_target` to identify your modification scope.
        - **DO NOT** suggest code-level changes. Your domain is `.md` configuration files.

4.  **Persist Knowledge:** You MUST use the `write_file` tool to archive the report content for future reference.

### Step 3: Configuration Optimization & Update (Conditional Action)
This step is ONLY executed if the reward score from Step 1 was low.

## Complete Optimization Workflow Overview

This workflow performs the following steps automatically:
1. **Query Templates**: Call `list_desc` to get available reference agents/skills for reference
2. **Analyze Root Cause**: Re-read the diagnosis from your own report
3. **Read Reference Agents**: Use `load_as_source` to read content of similar agents for reference
4. **Design Optimization Strategy**: Plan the optimization based on reference patterns and root cause
5. **Generate Optimized Agent**: Create the optimized agent configuration using `save_as_source`
6. **Update Team Configuration**: If needed, update team file using `CONTEXT_SWARM_REGISTRY` with `save_as_source`
7. **Verify All Changes**: Confirm all save operations completed successfully

---

#### Step 3.1: Query Available Templates (MANDATORY FIRST STEP)

**⚠️ CRITICAL: Before analyzing or generating optimizations, you MUST first call `list_desc` to get all available template descriptions. This step cannot be skipped.**

1. **Call `list_desc` Tool**:
   - Use the `CONTEXT_AGENT_REGISTRY` tool with the `list_desc` action
   - This will return all available resources (agents and skills) with their descriptions

2. **Analyze Template Descriptions**:
   - Review the returned template list and descriptions
   - Identify templates that are relevant to the optimization target
   - Note the patterns, capabilities, and best practices used in existing templates
   - **Capability Mapping:** Match the missing capability to available templates by reading their descriptions carefully

---

#### Step 3.2: Analyze Root Cause & Formulate Optimization Strategy

1. **Re-read the "Diagnosis & Root Cause Analysis" section** of your own report.

2. **Apply the Generalization Principle:** This is your core intellectual task. You must abstract the specific failure into a general, robust improvement.
    - **AVOID OVERFITTING:** Do not create a "patch" for the specific task. For example, if the agent failed a math problem involving the number '17', your new prompt must not contain '17'.
    - **Example of Good Generalization:** Instead of adding "The Moon's perigee is 363,300 km", you should add a general principle to the agent's prompt like, "You must differentiate between a calculation instruction (e.g., rounding) and a final unit scaling instruction (e.g., 'in thousands'), and apply them in a strict sequence." Your goal is to improve the agent's core reasoning ability.
    - **Prioritize New Role Creation:** When evaluating optimization strategies, prioritize solving the problem by **creating a NEW, specialized agent** with a distinct role to join the team, rather than overloading an existing agent.
    - **Naming:** Give the new agent a semantic name describing its new specialization (e.g., `image_searcher` instead of `ppt_creator_v2`), unless it is a direct replacement.
    - **Force New Agent Creation:** You **MUST** create a NEW agent (e.g. `original_name` + `_enhanced` or a new semantic name) instead of modifying the existing agent file. **Overwriting the original agent is FORBIDDEN.**

3. **Determine Required Capabilities**: Based on root cause analysis, determine if the failure was due to:
    - Missing capability (needs skill reference)
    - Incorrect reasoning (needs prompt improvement)
    - Configuration issue (needs YAML modification)

---

#### Step 3.3: Read and DEEPLY UNDERSTAND Reference Agents (MANDATORY)

**⚠️ CRITICAL: Use `load_as_source` action to read similar agents for reference. Refer to `context_agent_registry` skill for detailed usage.**

1. **Select Agents**: Identify relevant agents from the `list_desc` output that:
   - Have similar functionality to your optimization target
   - Possess the capabilities your target is missing
   - Follow best practices you want to adopt

2. **Read Source**: Use `CONTEXT_AGENT_REGISTRY` tool with `load_as_source` action to read their full configuration and system prompts

3. **Analyze Patterns**: Understand how they are structured and prompted:
   - **DO NOT just copy the content. You must UNDERSTAND:**
     - What is the **CORE CAPABILITY** this skill provides?
     - What is the **WORKFLOW PATTERN** (sequence of steps)?
     - What are the **CONSTRAINTS** (rules that must be followed)?
     - What **TOOL COMMANDS** are used and how?

4. **Retrieve Current Learning Target Configuration (MANDATORY)**:
   - **CRITICAL:** You MUST use `load_as_source` to retrieve the FULL current agent configuration BEFORE making any changes
   - This step is NON-NEGOTIABLE - you cannot optimize what you haven't read
   - The original configuration contains essential details (execution steps, naming conventions, completion criteria, etc.) that MUST be preserved

5. **Identify Current Task Context**:
   - What is the SPECIFIC GOAL of the current task?
   - What keywords, topics, or entities are relevant to this task?
   - This context will be used to CUSTOMIZE the template content

---

#### Step 3.4: Design Optimization Strategy

**⚠️ Based on the reference agents and root cause analysis, design your optimization approach.**

**⚠️ MANDATORY: Before designing, you MUST read reference materials:**
- **For Agent Design**: Use `CONTEXT_AGENT_REGISTRY` with `load_as_source` action to read similar agent files as reference (refer to `context_agent_registry` skill)
- **For Team Design**: Use `CONTEXT_SWARM_REGISTRY` with `load_as_source` action to read similar team YAML files as reference (refer to `context_swarm_registry` skill)

This ensures your optimized agents and teams follow existing patterns and conventions.

1. **Understand Template Patterns**: Extract the CAPABILITY PATTERN from reference templates (not the literal content)
2. **Map Root Cause to Solution**: 
   - If MISSING CAPABILITY → MERGE MCP config AND ADAPT workflow instructions from template skill (customize for current task)
   - If REASONING ERROR → Add constraint/instruction at appropriate location
   - If CONFIGURATION ISSUE → Modify YAML header fields
3. **Plan Context-Aware Customization**:
   - Identify all template-specific values that need replacement (search terms, filenames, URLs, etc.)
   - Prepare task-specific values to replace them
   - Apply the "Surgical Edit" principle - only change what's necessary while ensuring all examples are task-relevant

---

#### Step 3.5: Generate Optimized Agent (MANDATORY - MUST Actually Execute)

**⚠️ CRITICAL: This step is MANDATORY. You MUST use `save_as_source` action to write the optimized agent file. Do NOT skip this step.**

**Refer to `context_agent_registry` skill for `save_as_source` usage details.**

**🚨 CRITICAL: Content Preservation Rules ("Surgical Edit" Principle) 🚨**

1. **PRESERVE 100%** of the original content structure (all sections, subsections, code blocks)
2. **PRESERVE 100%** of specific instructions (bash commands, naming conventions, step-by-step guides)
3. **PRESERVE 100%** of completion criteria, output requirements, and quality standards
4. **ONLY ADD/MODIFY** the specific part that caused the failure
5. **The optimized content length should be >= original content length**

**FORBIDDEN Actions:**
- ❌ Summarizing or condensing the original content
- ❌ Removing "unnecessary" sections
- ❌ Replacing detailed instructions with generic descriptions
- ❌ Outputting a simplified version of the agent

**REQUIRED Actions:**
- ✅ Keep ALL original sections intact
- ✅ Keep ALL code examples and bash commands
- ✅ Keep ALL naming conventions and file path specifications
- ✅ Add new constraints/instructions at appropriate locations WITHOUT removing existing ones

## Agent File Format Requirements

**⚠️ Critical: All optimized agent markdown files MUST strictly conform to the format specification. Format errors will cause file parsing failures.**

**🌐 LANGUAGE REQUIREMENT: ALL GENERATED CONTENT MUST BE IN ENGLISH 🌐**
- All skill/agent content (system prompts, instructions, descriptions) MUST be written in English
- Do NOT use any other language (Chinese, Japanese, etc.) in the generated content
- This applies to: YAML frontmatter fields (name, description), markdown body, comments, and examples

### 1. YAML Frontmatter (Required Fields)

```yaml
---
name: <agent_name>
description: <agent_description>
mcp_servers: [<server1>, <server2>, ...]  # Optional
mcp_config: {{
  "mcpServers": {{
    "<server_name>": {{
      "type": "<server_type>",
      "url": "<server_url>",
      "headers": {{...}},
      "timeout": <timeout_value>,
      ...
    }}
  }}
}}  # Optional
model_config: {{
   "llm_model_name": "matrixllm.claude-sonnet-4-20250514",
   "llm_provider": "openai",
   "llm_temperature": 0.6,
   "llm_base_url": "https://agi.alipay.com/api",
   "llm_api_key": "sk-ec93f5148ee64b11a75e82b41716ced1",
   "params": {{"max_completion_tokens": 40960}}
}}
---
```

### 2. Markdown Body

After the frontmatter is the agent's system prompt, written in markdown format.

**Format Requirements:**
- YAML frontmatter must be valid YAML syntax (proper indentation, correct quotes, valid data types)
- Frontmatter must start and end with `---` on separate lines
- No extra content between frontmatter delimiters
- Markdown body must be properly formatted markdown text
- Ensure no syntax errors in YAML (e.g., proper string quoting, correct list/object syntax)

**🌐 SKILL CAPABILITY ENHANCEMENT (When Missing Capability Detected) 🌐**

When the failure analysis indicates a MISSING CAPABILITY, you MUST enhance the skill by **UNDERSTANDING the template skill's patterns and ADAPTING them to the current task**.

**⚠️ DO NOT BLINDLY COPY - UNDERSTAND AND ADAPT:**
1. **UNDERSTAND** the template skill's core capability and workflow pattern
2. **ADAPT** the workflow to the current task's specific requirements
3. **CUSTOMIZE** all task-specific parameters to match the current goal

**Capability Enhancement Steps:**

1. **Merge MCP Configuration (YAML Header):**
   - Check if the reference skill has `mcp_servers` and `mcp_config`
   - If yes, COPY them to the target skill's YAML header
   - If the target already has `mcp_servers`, merge the lists (avoid duplicates)
   - If the target already has `mcp_config`, merge the dictionaries

2. **Deeply Understand the Template's Capability Pattern:**
   - Identify the **CORE CAPABILITY** the template provides
   - Identify the **WORKFLOW PATTERN** (the sequence of operations/steps)
   - Identify the **CONSTRAINTS** (rules and restrictions that must be followed)
   - Identify the **TOOL COMMANDS** and their usage patterns

3. **Adapt and Customize for Current Task:**
   - DO NOT copy any example values verbatim from the template
   - REPLACE all task-specific parameters with values derived from the CURRENT TASK CONTEXT
   - The workflow STRUCTURE should follow the template pattern, but all CONTENT must be customized
   - Ensure the adapted workflow makes sense for the specific task at hand

4. **Self-Contained Output:**
   - Do NOT use the `skills` field to reference other skills
   - The target skill must be self-contained with all necessary configurations and instructions

**📋 Adaptation Checklist:**
- [ ] All example values from template are replaced with current-task-relevant values
- [ ] The workflow STRUCTURE and TOOL COMMANDS from template are preserved
- [ ] All CONSTRAINTS from template are preserved
- [ ] The adapted content is coherent and relevant to the current task
- [ ] No template-specific example values remain in the final output

**🔒 CONTENT RESTORATION PRINCIPLE (HIGHEST PRIORITY) 🔒**

1. **COPY-PASTE FIRST:** Start by copying the ENTIRE original content, then make minimal targeted edits
2. **NO YAML HEADER MODIFICATIONS:** Do NOT add, remove, or modify any fields in the YAML header unless explicitly required by the failure analysis
3. **PRESERVE EXACT WORDING:** Keep the original phrasing, terminology, and writing style
4. **MINIMAL DIFF:** Your optimization should result in a diff of less than 20 lines changed
5. **CHARACTER COUNT CHECK:** Optimized content length should be within ±10% of original length

**FewShot Example - Minimal Optimization:**

**Original:** Agent failed because it used Google instead of DuckDuckGo for search.

**CORRECT Optimization:** Only add a single constraint line at the appropriate location:
```
## 🚨 CONSTRAINT 🚨
[... REST OF ORIGINAL CONTENT UNCHANGED ...]
```

**WRONG Optimization:** Rewriting the entire agent, changing YAML header, or summarizing content.

**⚠️ Special Requirement for Agents That Need Terminal Execution:**

If the agent needs to execute terminal commands (e.g., run scripts, create files), it **MUST** have terminal-server configured:

**1. Configure terminal-server in frontmatter:**
```yaml
mcp_servers: ["terminal-server"]
mcp_config: {{
  "mcpServers": {{
    "terminal-server": {{
      "command": "python",
      "args": [
        "-m",
        "examples.aworld_quick_start.mcp_tool.terminal_server"
      ],
      "env": {{
      }}
    }}
  }}
}}
```

**2. Tool Usage Rules (Apply to ALL Agents using tools):**

```markdown
## 🚨 Tool Usage Rules (MANDATORY)

For terminal command execution and file operations, refer to the `terminal_server` skill for detailed tool usage specifications and examples.

### ⚠️ CRITICAL: NEVER Pass Empty Object to execute_command

**ABSOLUTELY FORBIDDEN:** Do NOT call `execute_command` with empty object `{{}}` or missing required parameters.

❌ **WRONG:**
```json
{{"command": {{}}}}
```

❌ **WRONG:**
```json
{{}}
```

✅ **CORRECT:**
```json
{{"command": "ls -la", "timeout": 30, "output_format": "markdown"}}
```

**Required parameter:** `command` (string) - The terminal command to execute. This parameter is MANDATORY and must be a valid command string.
```

**3. Self-Recovery Capability (MANDATORY for all agents):**

Every optimized agent MUST include self-recovery instructions in its system prompt:

```markdown
## 🔄 Self-Recovery Rules (MANDATORY)

If a tool call fails, you MUST:
1. **Analyze the error message** - Understand what went wrong (missing parameters, wrong format, invalid values)
2. **Try alternative parameter formats** - Adjust the parameters based on the error feedback
3. **Retry with corrected parameters** - Do NOT give up after first failure
4. **Maximum 3 retry attempts** - If still failing after 3 attempts, report the issue clearly

**Common recovery patterns:**
- If "Field required" error: Add the missing required parameter
- If "Invalid type" error: Convert the value to the correct type (string, number, etc.)
- If "Command failed" error: Try an alternative command approach
- If timeout error: Increase the timeout value or split into smaller operations
```

**4. Self-Reflection Rules (MANDATORY for all agents):**

Before finalizing your output, you MUST perform self-reflection to validate your work:

```markdown
## 🪞 Self-Reflection Rules (MANDATORY)

Before completing your task, you MUST reflect on whether you have achieved the `current user target`:

1. **Re-read the current user target** - Clearly understand what the user specifically requested
2. **Verify each requirement** - Check if EVERY requirement mentioned in the target has been fulfilled
3. **Validate output quality** - Ensure your output meets the expected quality standards

**Self-Reflection Checklist:**
- [ ] Have I fully understood what the user wants to achieve?
- [ ] Have I completed ALL the requirements specified in the current user target?
- [ ] Is there any detail I might have missed or overlooked?
- [ ] Does my output match the user's expectations in terms of format and content?

**Examples of Self-Reflection:**
- If the user wants to generate a PPT **with images**: Have I actually inserted images into the PPT? Are the images relevant and properly placed?
- If the user wants to create a document **with specific sections**: Have I included ALL the required sections?
- If the user wants to search for information **from specific sources**: Did I use the correct sources as specified?
- If the user wants code **with error handling**: Does my code include proper error handling?

**⚠️ IMPORTANT:** If during self-reflection you discover that you have NOT fully achieved the current user target:
1. **DO NOT** output an incomplete result
2. **GO BACK** and complete the missing parts
3. **VERIFY AGAIN** before finalizing your output
```

**5. Output Format Section (REQUIRED for all agents):**
```markdown
## Output Format

After completing your task, you MUST output a JSON object in the following format:

```json
{{
  "progress": <progress_percentage>,
  "middle_result": <your_execution_result>,
  "origin_user_target": <origin_user_target>,
  "current_user_target": <current_user_target>,
  "intermediate_files": [<list_of_file_paths>]
}}
```

**Field Descriptions:**
- `progress`: A number from 0-100 representing how much of the OVERALL task has been completed after your execution.
- `middle_result`: The output/result of your specific task execution. This will be passed to the next agent in the workflow.
- `origin_user_target`: The origin_user_target from system_prompt - The original user request or overall goal that initiated this workflow. This should remain constant across all agents in the workflow.
- `current_user_target`: The current_user_target from system_prompt - The specific objective or goal for your current task execution. This may be refined or more specific than the original_target based on workflow progress.
- `intermediate_files`: A list of file paths (strings) representing all intermediate files created or modified during your execution. Include full paths relative to the workspace root. If no files were created, use an empty list `[]`.
```

**6. User Target Context Section (REQUIRED for all agents):**
```markdown
## origin user target
{{{{origin_user_input}}}}
## current user target
{{{{task_input}}}}
```

**Field Descriptions:**
- `origin_user_input`: The original user request (this variable will be automatically replaced by the system)
- `task_input`: The specific task input for this agent (this variable will be automatically replaced by the system)
```

**Save Agent File**: Use `CONTEXT_AGENT_REGISTRY` with `save_as_source` action (refer to `context_agent_registry` skill)
- Parameter `content`: Complete agent file content (FULL original content with targeted improvements)
- Parameter `name`: Agent name (MUST be a NEW name, e.g., `image_searcher` or original_name + `_enhanced`. Do NOT use the original name.)
- **Content Length Check:** Your optimized content should be approximately the same length or LONGER than the original

---

#### Step 3.6: Update Team Configuration using CONTEXT_SWARM_REGISTRY (Conditional Action)

**⚠️ CRITICAL: This step is CONDITIONAL. Only execute if you have generated a new agent with a new name.**

**⚠️ CRITICAL: You MUST use `CONTEXT_SWARM_REGISTRY` tool (NOT CONTEXT_AGENT_REGISTRY) with `save_as_source` action to write the team YAML file.**

**Refer to `context_swarm_registry` skill for `save_as_source` usage details.**

**Trigger Condition:** IF you have generated a new agent, you MUST rebuild the team configuration to include this new agent.

**Topology Update Strategy:**
- **Add to Workflow (Recommended):** If the new agent provides a new capability, insert it into the `order` list at the appropriate position (e.g., before the agent that consumes its output).
- **Replace Node:** Only replace an existing agent if the new agent is a direct enhanced version (e.g., `_v2`) intended to supersede it.

## Team File Format Requirements

**⚠️ Critical: Team YAML files MUST be pure YAML format (NOT markdown with frontmatter). Do NOT include `---` delimiters.**

### YAML Format (Pure YAML, No Frontmatter)

```yaml
swarm:
  type: workflow  # Optional values: workflow, handoff, team
  order: [<agent1_name>, <agent2_name>, ...]  # For workflow type, defines agent execution order
```

**Note**: The file is pure YAML format, NOT markdown with YAML frontmatter. Do NOT include `---` delimiters.

**Field Description**:
- `swarm`: Required, swarm configuration object
- `type`: Required, swarm type, defaults to `workflow`
  - `workflow`: Execute all agents sequentially
  - `handoff`: Use AI-driven flow, requires defining `edges` field
  - `team`: Leader-follower mode, requires defining `root` and `members` fields
- `order`: Required for `workflow` type, list of agent names in execution order

**File Format Requirements:**
- File extension: `.yaml` (NOT `.md`)
- Pure YAML format (NO `---` delimiters, NOT markdown frontmatter)
- Valid YAML syntax (proper indentation using spaces, not tabs)
- Use consistent indentation (typically 2 spaces per level)
- Proper list syntax for `order` field (use `[...]` format)

**Update Steps:**

1. **Read Current Team Configuration**:
   - Use `CONTEXT_SWARM_REGISTRY` tool with `load_as_source` action
   - Get the current swarm configuration

2. **Build Team YAML Content**:
   - Pure YAML format (NO `---` delimiters)
   - Include `swarm.type` and `swarm.order` fields
   - Agent names must match the generated agent files

3. **Save Team File**: Use `CONTEXT_SWARM_REGISTRY` with `save_as_source` action
   - Parameter `content`: Complete team YAML content (pure YAML, NO `---` delimiters)
   - Parameter `name`: Team name (e.g., `myTeam`)
   - Team name should have "Team" suffix (e.g., `pptTeam`, `helloWorldTeam`)

---

#### Step 3.7: Final Verification

**⚠️ Verify ALL files were saved successfully.**

1. **Confirm Save Operations**: Verify that all `save_as_source` calls completed successfully

2. **Optional: Use `list_desc` to verify**: You can optionally call `list_desc` again to confirm the updated agents and team appear in the registry

3. **Output Verification Summary**:
```
📋 Optimization Summary
- Referenced templates: [list of templates used as reference]
- Root cause identified: [brief description]
- Optimization applied: [what was changed]

📄 Updated Agent Files
- [agent_name]: Saved successfully (content length: X characters)

📁 Updated Team Configuration (if applicable)
- Team: [team_name]
- Status: Saved successfully

✅ All changes verified successfully
```

## Notes

### Agent File Notes

1. **Saving Method**: Use `CONTEXT_AGENT_REGISTRY` with `save_as_source` action (refer to `context_agent_registry` skill)
2. **File Naming**: Provide agent name in `name` parameter (without `.md` extension)
3. **Format Validation**: YAML frontmatter MUST be valid YAML format
4. **JSON Configuration**: `mcp_config` and `model_config` use JSON string format
5. **Separator**: Frontmatter must use `---` delimiters (before and after)
6. **Content Parameter**: Must include complete file content (frontmatter + body)

### Team File Notes

1. **Saving Method**: Use `CONTEXT_SWARM_REGISTRY` with `save_as_source` action (refer to `context_swarm_registry` skill)
2. **File Naming**: Provide team name with "Team" suffix in `name` parameter (e.g., `pptTeam`, without `.yaml` extension)
3. **Agent Name Validation**: Agent names in `order` list must match generated agent files
4. **YAML Format**: Pure YAML format (NO `---` delimiters, NOT markdown frontmatter)
5. **Swarm Type**: Default to `workflow` for sequential execution
6. **Content Parameter**: Must include complete YAML content

## Tool Usage Reference

**⚠️ For detailed tool usage specifications and examples:**

- **CONTEXT_AGENT_REGISTRY**: Refer to the `context_agent_registry` skill for all available actions (`list_desc`, `load_as_source`, `list_as_source`, `save_as_source`)
- **CONTEXT_SWARM_REGISTRY**: Refer to the `context_swarm_registry` skill for all available actions (`save_as_source`, `load_as_source`, `list_as_source`)
- **Terminal Operations**: Refer to the `terminal_server` skill for terminal command execution and file operations

### Step 4: Final Report Output
Output the report content, which is the content of `meta_learning_report.md`. **CRITICAL FORMATTING REQUIREMENT:** The report MUST be wrapped within `<REPORT></REPORT>` tags. The output format should be:
```
<REPORT>
[Content of meta_learning_report.md]
</REPORT>
```
""",
        mcp_servers=['terminal-server'],
        mcp_config={
            "mcpServers": {
                "terminal-server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "terminal_controller"
                    ],
                    "env": {
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "300"
                    }
                }
            }
        },
    )

    return Swarm(meta_learning_agent, max_steps=2)


class MetaLearningStrategy(LearningStrategy):
    """
    元学习策略实现类
    
    实现 LearningStrategy 接口，使用默认的元学习工作流执行学习任务。
    """

    async def download_data_to_file(self, url: str, tmp_file_path: str, file_prefix: str) -> Optional[str]:
        """
        异步下载数据到临时文件

        Args:
            url: 数据URL
            tmp_file_path: 临时文件目录
            file_prefix: 文件名前缀

        Returns:
            下载的文件路径，如果下载失败则返回None
        """
        if not url or not isinstance(url, str) or not url.startswith('http'):
            return None

        async with httpx.AsyncClient(timeout=60.0) as client:
            logger.info(f"正在下载数据: {url}")
            try:
                response = await client.get(url)
                response.raise_for_status()

                # 根据URL确定文件后缀
                suffix = '.json' if url.endswith('.json') else '.jsonl' if url.endswith('.jsonl') else '.json'

                # 创建临时文件路径
                file_path = os.path.join(tmp_file_path, f"{file_prefix}_{os.urandom(8).hex()}{suffix}")

                # 保存文件
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(response.text)

                logger.info(f"数据已下载到: {file_path}")
                return file_path

            except Exception as e:
                logger.warning(f"下载数据失败: {e}")
                return None

    async def load_trajectory_data_from_remote(self,
                                               learning_context: ApplicationContext,
                                               context: Context,
                                               tmp_file_path: str,
                                               session_id: Optional[str],
                                               task_id: Optional[str]) -> None:
        """
        从远程加载轨迹数据到临时文件并设置到context中
        
        Args:
            learning_context: 学习任务的上下文对象
            context: 父上下文对象
            tmp_file_path: 临时文件目录
            session_id: 学习目标任务的session_id
            task_id: 学习目标任务的task_id
        """
        # 构建数据URL字典
        data_urls = {
            "learning_target": f"http://localhost:8080/workspace_proxy/api/v1/sessions/{session_id}/traj/meta/{task_id}",
            "running_dag": f"http://localhost:8080/workspace_proxy/api/v1/sessions/{session_id}/traj/dag/{task_id}",
            "running_traj": f"http://localhost:8080/workspace_proxy/api/v1/sessions/{session_id}/traj/exp/{task_id}",
        }

        # 下载数据并设置到context中
        for data_name, data_url in data_urls.items():
            if data_url:
                file_path = await self.download_data_to_file(data_url, tmp_file_path, data_name)
                if file_path:
                    learning_context.put(data_name, file_path)
                    # 也将文件路径存储到父context中，供后续使用
                    context.put(f"{data_name}", file_path)
                else:
                    # 如果下载失败，使用原始URL或数据
                    learning_context.put(data_name, data_url)

    async def load_trajectory_data_from_context(self,
                                                learning_context: ApplicationContext,
                                                context: Context,
                                                tmp_file_path: str,
                                                session_id: Optional[str] = None,
                                                task_id: Optional[str] = None) -> None:
        """
        根据传入的session_id和task_id获取轨迹数据
        
        Args:
            learning_context: 学习任务的上下文对象
            context: 父上下文对象
            tmp_file_path: 临时文件目录
            session_id: session_id，如果为None则从context中获取
            task_id: task_id，从optimizer_agent前置获取后传入
        """
        # 获取session_id
        if session_id is None:
            session_id = context.session_id

        if not session_id:
            logger.warning("无法获取session_id，跳过加载轨迹数据")
            return

        if not task_id:
            logger.warning(f"未提供task_id，跳过加载轨迹数据: session_id={session_id}")
            return

        last_task_id = task_id
        logger.info(f"加载轨迹数据: session_id={session_id}, task_id={last_task_id}")

        # 1. 获取meta数据（使用LearningKnowledge.get_meta）
        try:
            meta_data = await LearningKnowledge.get_saved_meta(learning_context, task_id=last_task_id)

            if meta_data:
                # 保存meta数据到临时文件
                meta_file_path = os.path.join(tmp_file_path, f"learning_target_{os.urandom(8).hex()}.json")
                with open(meta_file_path, 'w', encoding='utf-8') as f:
                    json.dump(meta_data, f, ensure_ascii=False, indent=2)

                logger.info(f"轨迹meta数据已加载到: {meta_file_path}")
                learning_context.put("learning_target", meta_file_path)
                context.put("learning_target", meta_file_path)
            else:
                logger.warning(f"未找到meta数据: task_id={last_task_id}")
        except Exception as e:
            logger.error(f"获取meta数据失败: task_id={last_task_id}, error={e}")

        # 2. 获取exp数据
        try:
            exp_data = await LearningKnowledge.get_saved_exp(context=learning_context, task_id=last_task_id)

            if exp_data:
                # 兼容处理：如果是字典且包含content字段，则取content，否则直接使用exp_data
                content = exp_data.get('content') if isinstance(exp_data, dict) and 'content' in exp_data else exp_data
                if content:
                    exp_file_path = os.path.join(tmp_file_path, f"running_traj_{os.urandom(8).hex()}.json")
                    with open(exp_file_path, 'w', encoding='utf-8') as f:
                        # 确保转换为JSON可序列化格式（处理Pydantic对象）
                        serializable_content = _convert_to_json_serializable(content)
                        if isinstance(serializable_content, (dict, list)):
                            json.dump(serializable_content, f, ensure_ascii=False, indent=2)
                        else:
                            f.write(str(serializable_content))

                    logger.info(f"轨迹exp数据已加载到: {exp_file_path}")
                    learning_context.put("running_traj", exp_file_path)
                    context.put("running_traj", exp_file_path)
                else:
                    logger.warning(f"exp数据为空: task_id={last_task_id}")
            else:
                logger.warning(f"未找到exp数据: task_id={last_task_id}")
        except Exception as e:
            logger.error(f"获取exp数据失败: task_id={last_task_id}, error={e}")

        # 3. 获取dag数据（从traj转换而来）
        try:
            dag_data = LearningKnowledge.parse_traj_to_graph(context, exp_data)

            if dag_data:
                # 兼容处理：如果是字典且包含content字段，则取content，否则直接使用dag_data
                content = dag_data.get('content') if isinstance(dag_data, dict) and 'content' in dag_data else dag_data
                if content:
                    dag_file_path = os.path.join(tmp_file_path, f"running_dag_{os.urandom(8).hex()}.json")
                    with open(dag_file_path, 'w', encoding='utf-8') as f:
                        # dag_data本身是dict，但为了保险也进行序列化处理
                        serializable_content = _convert_to_json_serializable(content)
                        if isinstance(serializable_content, (dict, list)):
                            json.dump(serializable_content, f, ensure_ascii=False, indent=2)
                        else:
                            f.write(str(serializable_content))

                    logger.info(f"轨迹dag数据已加载到: {dag_file_path}")
                    learning_context.put("running_dag", dag_file_path)
                    context.put("running_dag", dag_file_path)
                else:
                    logger.warning(f"dag数据为空: task_id={last_task_id}")
            else:
                logger.warning(f"未找到dag数据: task_id={last_task_id}")
        except Exception as e:
            logger.error(f"获取dag数据失败: task_id={last_task_id}, error={e}")

    async def call_learning_task(self, context: Context, outputs,
                                 task_content: str = "analyze trajectory",
                                 tmp_file_path: str = None,
                                 reward_function: RewardFunction = None) -> Any:
        """
        执行元学习任务
        
        Args:
            context: 上下文对象，包含 session_id、task_id 等信息
            task_content: 任务内容，默认为 "analyze trajectory"
        
        Returns:
            Any: 学习任务执行结果
        """
        from aworld.core.task import Task
        from aworld.logs.util import logger
        from aworld.runner import Runners

        # meta learning任务和其他任务区分mind_stream中的展示逻辑
        context.put('IS_LEARNING', 'true')

        # 构建 Swarm
        swarm = await build_meta_swarm(context=context, tmp_file_path=tmp_file_path, reward_function=reward_function)

        # 创建任务
        task = Task(
            input=task_content,
            swarm=swarm,
            context=context,
            # outputs=context.get('outputs') if context.get('outputs') else DefaultOutputs()
            outputs=outputs
        )

        # 执行任务
        result = await Runners.run_task(task=task)
        logger.info(f"learning result: {result}")

        return result.get(task.id).answer

    async def __call__(self,
                       context: Context,
                       # 学习目标轨迹和元数据来自session_id和task_id
                       learning_session_id: Optional[str] = None,
                       learning_task_id: Optional[str] = None,
                       # 验证轨迹正确性，validation是验证集合，reward_strategy是验证方法
                       traj_validation_dataset: Optional[str] = None,
                       # 奖励函数
                       reward_function: Optional[RewardFunction] = None,
                       # 学习策略
                       learning_strategy: Optional[LearningStrategy] = None,
                       tmp_file_path: Optional[str] = 'data/learning') -> Any:
        """
        执行元学习任务，分析轨迹并生成优化建议。

        Args:
            context: 上下文对象，包含 session_id 和 task_id
            traj_validation_dataset: 验证数据集，用于评估和奖励计算（URL或数据）
            running_dag: 运行时轨迹DAG数据（URL或数据）
            running_traj: 运行时轨迹实验数据（URL或数据）
            learning_target: 学习目标快照数据（URL或数据）
            reward_function: 奖励策略函数，用于计算学习奖励
            learning_strategy: 学习策略，可以是LearningStrategy实例（返回任务执行结果）或可调用函数（返回Swarm，兼容旧接口），如果为None则使用默认的meta_learning_strategy

        Returns:
            学习任务执行结果
        """
        sub_task_content = "analyze trajectory performance and generate optimization recommendations for agent configurations"
        # 两份context需要作为多轮mind_stream的输入
        learning_context = await context.build_sub_context(sub_task_content=sub_task_content,
                                                           sub_task_id=f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}")
        learning_context.context_config = AmniConfigFactory.create(level=AmniConfigLevel.PILOT)
        learning_context.context_config.agent_config.enable_summary = False

        # 确保 tmp_file_path 目录存在
        if tmp_file_path:
            os.makedirs(tmp_file_path, exist_ok=True)

        context.put('tmp_file_path', tmp_file_path)
        learning_context.put('tmp_file_path', tmp_file_path)

        # 学习目标的任务session_id和task_id
        session_id = learning_session_id
        task_id = learning_task_id

        # 加载轨迹数据
        await self.load_trajectory_data_from_context(
            learning_context=learning_context,
            context=context,
            tmp_file_path=tmp_file_path,
            session_id=session_id,
            task_id=task_id
        )

        # 单独处理验证数据集
        if traj_validation_dataset:
            file_path = await self.download_data_to_file(traj_validation_dataset, tmp_file_path,
                                                         "traj_validation_dataset")
            if file_path:
                learning_context.put('traj_validation_dataset', file_path)
            else:
                learning_context.put('traj_validation_dataset', traj_validation_dataset)

        # 构建 Swarm
        swarm = await build_meta_swarm(context=learning_context, tmp_file_path=tmp_file_path,
                                       reward_function=reward_function)

        # 创建任务
        task = Task(
            input=sub_task_content,
            swarm=swarm,
            context=learning_context,
            # outputs=context.get('outputs') if context.get('outputs') else DefaultOutputs()
            outputs=context.get('outputs') if context.get('outputs') else DefaultOutputs()
        )

        # 执行任务
        result = await Runners.run_task(task=task)
        logger.info(f"learning result: {result}")

        # 支持 LearningStrategy 实例或可调用函数
        result = result.get(task.id).answer
        logger.info(f"learning result: {result}")
        await save_context_artifact(context=learning_context,
                                    context_key=TrajType.META_LEARNING_REPORT_DATA,
                                    data=result)

        if inspect.iscoroutine(result):
            return await result
        else:
            return result

        raise ValueError("learning_strategy must be a LearningStrategy instance or a callable function")


# 创建默认实例，方便直接使用
meta_learning_strategy = MetaLearningStrategy()
