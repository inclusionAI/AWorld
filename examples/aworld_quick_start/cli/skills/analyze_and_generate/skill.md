---
name: analyze_and_generate
description: Default skill for handling ALL user requests. This skill analyzes any user requirement (PPT creation, document generation, data analysis, content creation, etc.) and automatically generates the optimal agent team to accomplish the task. Use this skill for ANY task the user requests.
tool_list: {"CONTEXT_AGENT_REGISTRY": [], "CONTEXT_SWARM_REGISTRY": []}
active: True
---

# Analyze and Generate Skill

This skill analyzes user requirements and generates complete agent team configurations to handle user requests. It performs requirement analysis, identifies necessary agent types and their responsibilities, generates agent definition files, and creates the team orchestration YAML file - all in one unified workflow.

---
## 🚨🚨🚨 IMMEDIATE FIRST ACTION (MANDATORY - DO THIS NOW) 🚨🚨🚨

**STOP! Before doing ANYTHING else, you MUST execute this tool call IMMEDIATELY:**

**Call `list_desc` from `CONTEXT_AGENT_REGISTRY` NOW to query available templates.**

This is NOT optional. This is the FIRST thing you must do upon receiving any user request. Do NOT analyze the request, do NOT plan, do NOT call any other agent or tool - call `list_desc` FIRST.

**ONLY AFTER receiving the `list_desc` results should you proceed with analyzing the user's request.**

---

## 🚫 CRITICAL REQUIREMENTS - READ THIS FIRST

**🚫 ABSOLUTELY FORBIDDEN: Do NOT just discuss, plan, or describe what you would generate. You MUST actually generate and save ALL files (agent files AND team file) using `save_as_source` action.**

**⚠️ MANDATORY EXECUTION REQUIREMENTS:**
1. **You MUST use `save_as_source` action to save all files** - Do NOT just describe them
2. **You MUST save ALL files** - Agent files AND team YAML file MUST be saved
3. **You MUST verify ALL save operations completed successfully**
4. **You MUST NOT skip any save steps** - Every file must be actually saved

**⚠️ What is FORBIDDEN:**
- ❌ Describing what you would save without actually saving
- ❌ Discussing the file content without saving it
- ❌ Planning the file structure without executing save operations
- ❌ Asking user for confirmation before saving
- ❌ Outputting file content in your response instead of saving to registry
- ❌ Asking the user ANY questions (topics, purposes, details, clarification, etc.)
- ❌ Questions like "Could you please provide more details about..."
- ❌ Questions like "What is the topic or purpose of..."
- ❌ Using CONTEXT_AGENT_REGISTRY's save_as_source to save team files (MUST use CONTEXT_SWARM_REGISTRY for team files)

**⚠️ What is REQUIRED:**
- ✅ Use `CONTEXT_AGENT_REGISTRY` tool to save agent files (refer to `context_agent_registry` skill for usage)
- ✅ Use `CONTEXT_SWARM_REGISTRY` tool to save team files (refer to `context_swarm_registry` skill for usage)
- ✅ Infer ALL information from the user's initial request

## Usage

Use this skill when you need to analyze user requirements and automatically generate complete agent team configurations (both agent definition files and team orchestration file) to handle complex user requests.

**⚠️ CRITICAL RULE: NEVER ASK USER FOR CONFIRMATION OR INFORMATION**

**You MUST NOT:**
- Ask the user to confirm file paths, directory names, or file names
- Ask the user for additional information or clarification
- Ask the user to provide more details
- Ask the user questions about requirements, topics, purposes, content, structure, or any other aspects
- Ask the user to choose between options
- Ask the user to approve or confirm any generated content
- Wait for user input or confirmation before proceeding

**You MUST:**
- Infer ALL required information from the user's request and context
- Generate ALL files directly without any confirmation
- Complete the entire analysis and generation process autonomously
- Make all decisions independently based on the user's input
- Save both agent files AND team YAML file

**⚠️ Format Compliance: All generated files MUST strictly conform to format specifications. Format errors will cause file parsing failures. Always verify format correctness before writing files.**

## Tool Reference

**For detailed tool usage specifications:**
- **Agent Registry Operations**: Refer to the `context_agent_registry` skill for `list_desc`, `load_as_source`, `list_as_source`, and `save_as_source` actions
- **Swarm Registry Operations**: Refer to the `context_swarm_registry` skill for `save_as_source`, `load_as_source`, and `list_as_source` actions

## Complete Workflow Overview

This skill performs the following workflow automatically:
1. **Query Templates**: Call `list_desc` to get available templates for reference
2. **Analyze Requirements**: Parse user input and identify required agents
3. **Read Reference Agents**: Use `load_as_source` to read content of similar agents for reference
4. **Design Agent Team**: Plan agent structure and execution order
5. **Generate Agent Files**: Create all agent definition files using `save_as_source`
6. **Generate Team File**: Create the team configuration file using `CONTEXT_SWARM_REGISTRY` tool with `save_as_source` action
7. **Verify All Files**: Confirm all save operations completed successfully
8. **Execute Team**: Call TeamRunnerAgent to execute the generated team (MANDATORY FINAL STEP)

## Requirement Analysis Process

### Step 0: Query Available Templates (MANDATORY FIRST STEP)

**⚠️ CRITICAL: Before analyzing requirements, you MUST first call `list_desc` to get all available template descriptions. This step cannot be skipped.**

1. **Call `list_desc` Tool**: Use `CONTEXT_AGENT_REGISTRY` with `list_desc` action (refer to `context_agent_registry` skill for details)
2. **Analyze Template Descriptions**: Review the returned template list and identify relevant templates
3. **Use Templates as Reference**: Reference similar existing agents for format and style consistency

### Step 1: Requirement Analysis

**⚠️ Do NOT ask the user for any information. NEVER ask questions. Infer everything from the user's input autonomously.**

Analyze the user's input to understand (ALL must be inferred, NEVER ask):
1. **Core Requirements**: What is the main goal or task the user wants to accomplish?
2. **Task Complexity**: Is this a simple single-step task or a complex multi-step workflow?
3. **Required Capabilities**: What capabilities are needed to complete the task?
4. **Task Dependencies**: Are there dependencies between different steps?
5. **Output Requirements**: What format or type of output is expected?

### Step 2: Read Reference Agents

**⚠️ CRITICAL: Use `load_as_source` action to read similar agents for reference. Refer to `context_agent_registry` skill for detailed usage.**

1. **Select Agents**: Identify relevant agents from the `list_desc` output
2. **Read Source**: Use `load_as_source` action to read their full configuration and system prompts
3. **Analyze Patterns**: Understand how they are structured and prompted to maintain consistency

### Step 3: Agent Identification

**⚠️ Do NOT ask the user which agents to use. Determine agents autonomously based on requirements.**

Based on the requirement analysis, identify the necessary agents:

1. **Agent Types**: Determine what types of agents are needed:
   - **Analysis Agents**: For data analysis, requirement understanding, planning
   - **Collection Agents**: For data gathering, web scraping, file reading
   - **Processing Agents**: For data transformation, computation, filtering
   - **Generation Agents**: For content creation, code generation, report writing
   - **🔧 Execution Agents**: For file creation, PPT generation, script execution (**REQUIRES terminal-server**)
   - **Coordination Agents**: For workflow management, task orchestration

   **⚠️ CRITICAL: If the task requires creating output files (PPT, PDF, etc.), the final agent MUST be an Execution Agent with terminal-server configured and MANDATORY EXECUTION section in its system prompt.**

2. **Agent Responsibilities**: For each identified agent, define:
   - Agent name (descriptive and clear)
   - Primary responsibility
   - Required tools/capabilities
   - Input/output specifications

3. **Execution Order**: Determine the logical execution order of agents

### Step 4: Team Configuration Planning

**⚠️ Do NOT ask the user to confirm team name, swarm type, or agent order. Plan autonomously.**

1. **Team Name**: Generate a descriptive team name based on the task (e.g., "data_analysis", "content_generation", "ppt")

2. **Swarm Type**: Choose appropriate swarm type:
   - `workflow`: For sequential execution (most common)
   - `handoff`: For AI-driven flow control
   - `team`: For leader-follower mode

3. **Agent Order**: List agents in execution order based on dependencies and logic

## Agent File Format Requirements

**⚠️ Critical: All generated agent markdown files MUST strictly conform to the format specification. Format errors will cause file parsing failures.**

**🌐 LANGUAGE REQUIREMENT: ALL GENERATED CONTENT MUST BE IN ENGLISH 🌐**
- All skill/agent content (system prompts, instructions, descriptions) MUST be written in English
- Do NOT use any other language (Chinese, Japanese, etc.) in the generated content
- This applies to: YAML frontmatter fields (name, description), markdown body, comments, and examples
- Even if the user's input is in another language, the generated agent/skill files MUST be in English

### 1. YAML Frontmatter (Required Fields)

```yaml
---
name: <agent_name>
description: <agent_description>
mcp_servers: [<server1>, <server2>, ...]  # Optional
mcp_config: {
  "mcpServers": {
    "<server_name>": {
      "type": "<server_type>",
      "url": "<server_url>",
      "headers": {...},
      "timeout": <timeout_value>,
      ...
    }
  }
}  # Optional
model_config: {
   "llm_model_name": "matrixllm.claude-sonnet-4-20250514",
   "llm_provider": "openai",
   "llm_temperature": 0.6,
   "llm_base_url": "https://agi.alipay.com/api",
   "llm_api_key": "sk-ec93f5148ee64b11a75e82b41716ced1",
   "params": {"max_completion_tokens": 40960},
   "ext_config": {
     "max_tokens": 40960
   }
}
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

## Generation Steps

**⚠️ MANDATORY: You MUST actually save ALL files using `save_as_source` action. Do NOT just describe what you would generate. ALL files MUST be saved to the registry.**

### Step 1: Query Available Templates (MANDATORY FIRST STEP)

**⚠️ CRITICAL: This step MUST be executed first before any analysis or generation.**

Use `CONTEXT_AGENT_REGISTRY` with `list_desc` action. Refer to `context_agent_registry` skill for detailed usage.

### Step 2: Analyze User Requirement

**⚠️ Do NOT ask the user for clarification. NEVER ask questions. Parse and infer all information from the input autonomously.**

1. **Parse User Input**: Extract key information from user's request
2. **Compare with Available Templates**: Review templates from Step 1
3. **Identify Task Components**: Break down the requirement into actionable components
4. **Determine Complexity**: Assess complexity level

### Step 3: Read Reference Agents (MANDATORY)

**⚠️ CRITICAL: Use `load_as_source` action to read similar agents for reference. Refer to `context_agent_registry` skill for detailed usage.**

1. **Identify Similar Agents**: From the `list_desc` results in Step 1, select agents that are similar to what you need
2. **Read Content**: Use `CONTEXT_AGENT_REGISTRY` with `load_as_source` action to read the full content

### Step 4: Design Agent Team

**⚠️ Do NOT ask the user which agents to create. Design autonomously.**

**⚠️ MANDATORY: Before designing, you MUST read reference materials:**
- **For Agent Design**: Use `CONTEXT_AGENT_REGISTRY` with `load_as_source` action to read similar agent files as reference (refer to `context_agent_registry` skill)
- **For Team Design**: Use `CONTEXT_SWARM_REGISTRY` with `load_as_source` action to read similar team YAML files as reference (refer to `context_swarm_registry` skill)

Do not add an image search agent to the team

This ensures your generated agents and teams follow existing patterns and conventions.

1. **Read Reference Materials**: 
   - Use `CONTEXT_AGENT_REGISTRY` → `load_as_source` to read similar agents
   - Use `CONTEXT_SWARM_REGISTRY` → `load_as_source` to read similar team configurations
2. **Reference Similar Templates**: Use the loaded templates as reference for agent design
3. **Map Requirements to Agents**: For each task component, identify agent name and responsibility
4. **Define Execution Flow**: Determine the order and dependencies
5. **Generate Agent Names**: Create descriptive agent names using snake_case convention

### Step 5: Generate Agent Files (MANDATORY - MUST Actually Execute)

**⚠️ CRITICAL: This step is MANDATORY. You MUST use `save_as_source` action to write ALL agent files. Do NOT skip this step.**

**Refer to `context_agent_registry` skill for `save_as_source` usage details.**

**⚠️ Special Requirement for Agents That Need Terminal Execution:**

If the agent needs to execute terminal commands (e.g., run scripts, create files), it **MUST** have terminal-server configured:

**1. Configure terminal-server in frontmatter:**
```yaml
mcp_servers: ["terminal-server"]
mcp_config: {
  "mcpServers": {
    "terminal-server": {
      "command": "python",
      "args": [
        "-m",
        "examples.aworld_quick_start.mcp_tool.terminal_server"
      ],
      "env": {
      }
    }
  }
}
```

**2. Hello World Example - Agent system prompt:**
```markdown
## Task
Write a hello world program and execute it.

## Execution Steps
1. Create the file: `echo 'print("Hello, World!")' > hello.py`
2. Run the program: `python hello.py`
3. Verify output shows "Hello, World!"
```

**3. Tool Usage Rules (Apply to ALL Agents using tools):**

```markdown
## 🚨 Tool Usage Rules (MANDATORY)

For terminal command execution and file operations, refer to the `terminal_server` skill for detailed tool usage specifications and examples.

### ⚠️ CRITICAL: NEVER Pass Empty Object to execute_command

**ABSOLUTELY FORBIDDEN:** Do NOT call `execute_command` with empty object `{}` or missing required parameters.

❌ **WRONG:**
```json
{"command": {}}
```

❌ **WRONG:**
```json
{}
```

✅ **CORRECT:**
```json
{"command": "ls -la", "timeout": 30, "output_format": "markdown"}
```

**Required parameter:** `command` (string) - The terminal command to execute. This parameter is MANDATORY and must be a valid command string.
```

**4. Self-Recovery Capability (MANDATORY for all agents):**

Every generated agent MUST include self-recovery instructions in its system prompt:

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

**5. Adaptive Skill Generation Rule (MANDATORY):**

**⚠️ CRITICAL: When an agent needs specific capabilities, you MUST generate NEW skill content tailored to the task, NOT copy from existing skills.**

Based on task requirements:
- Analyze what tools/APIs are needed for the specific task
- Generate appropriate tool parameters and usage patterns for this task context
- Create task-specific examples that demonstrate the exact workflow needed
- Design error handling strategies relevant to the task domain

This ensures each agent has precisely the capabilities it needs, optimized for its specific purpose.

**⚠️ MANDATORY: Every generated agent file MUST end with the following sections:**

**2. Tool Call Warning Section (REQUIRED for agents using terminal-server):**
```markdown
## ⚠️ Tool Call Warning

**NEVER pass empty object `{}` when calling `execute_command`!**

- ❌ WRONG: `{"command": {}}` or `{}`
- ✅ CORRECT: `{"command": "your_command_here", "timeout": 30, "output_format": "markdown"}`

The `command` parameter is REQUIRED and must be a valid command string.
```

**3. Output Format Section (REQUIRED for all agents):**
```markdown
## Output Format

After completing your task, you MUST output a JSON object in the following format:

```json
{
  "progress": <progress_percentage>,
  "middle_result": <your_execution_result>,
  "origin_user_target": <origin_user_target>,
  "current_user_target": <current_user_target>,
  "intermediate_files": [<list_of_file_paths>]
}
```

**Field Descriptions:**
- `progress`: A number from 0-100 representing how much of the OVERALL task has been completed after your execution. Consider your role in the workflow and estimate the percentage of the entire user request that is now complete.
- `middle_result`: The output/result of your specific task execution. This will be passed to the next agent in the workflow.
- `origin_user_target`: The origin_user_target from system_prompt.
- `current_user_target`: The current_user_target from system_prompt.
- `intermediate_files`: A list of file paths (strings) representing all intermediate files created or modified during your execution. Include full paths relative to the workspace root. If no files were created, use an empty list `[]`.

**Example:**
```json
{
  "progress": 30,
  "middle_result": "Successfully generated HTML content for Beckham introduction page with header, bio section, and career highlights.",
  "origin_user_target": "Create a presentation about David Beckham's career",
  "current_user_target": "Generate HTML content for Beckham introduction page",
  "intermediate_files": ["output/beckham_intro.html", "assets/beckham_photo.jpg"]
}
```

This ensures:
- The agent receives the original user request context during execution
- Each agent reports its progress and intermediate results for workflow tracking
- The workflow maintains visibility into the original goal and current objectives
- All intermediate files are tracked for reference and debugging

**4. User Target Context Section (REQUIRED for all agents):**
```markdown
## origin user target
{{origin_user_input}}
## current user target
{{task_input}}
```

**Field Descriptions:**
- `origin_user_input`: The original user request (this variable will be automatically replaced by the system)
- `task_input`: The specific task input for this agent (this variable will be automatically replaced by the system)
```

For each agent identified:

1. **Build Agent File Content**:
   - Build YAML frontmatter with strict format compliance
   - Generate comprehensive system prompt as markdown body
   - **Add at the END of the markdown body:**
     - `## Output Format` section with progress and middle_result JSON specification
     - `## origin user target` and `## current user target` sections with variables
   - Verify format correctness
   - Combine frontmatter and body into complete content string

2. **Save Agent File**: Use `CONTEXT_AGENT_REGISTRY` with `save_as_source` action (refer to `context_agent_registry` skill)

### Step 6: Generate Team File using CONTEXT_SWARM_REGISTRY (MANDATORY - MUST Actually Execute)

**⚠️ CRITICAL: This step is MANDATORY. You MUST use `CONTEXT_SWARM_REGISTRY` tool (NOT CONTEXT_AGENT_REGISTRY) with `save_as_source` action to write the team YAML file. Do NOT skip this step.**

**Refer to `context_swarm_registry` skill for `save_as_source` usage details.**

1. **Build Team YAML Content**:
   - Pure YAML format (NO `---` delimiters)
   - Include `swarm.type` and `swarm.order` fields
   - Agent names must match the generated agent files

2. **Save Team File**: Use `CONTEXT_SWARM_REGISTRY` with `save_as_source` action
   - Team name should have "Team" suffix (e.g., `pptTeam`, `helloWorldTeam`)

### Step 7: Final Verification

**⚠️ Verify ALL files were saved successfully.**

1. **Confirm Save Operations**: Verify that all `save_as_source` calls completed successfully
2. **Optional: Use `list_desc` to verify**: You can optionally call `list_desc` again to confirm the newly saved agents and team appear in the registry

### Step 8: Execute Generated Team (MANDATORY FINAL STEP)

**⚠️ CRITICAL: This step is MANDATORY. After generating and verifying all files, you MUST call TeamRunnerAgent to execute the generated team. Do NOT skip this step. Do NOT just summarize what was created - you MUST actually execute the team.**

**🚨🚨🚨 IMMEDIATELY AFTER FILE VERIFICATION, YOU MUST:**

Call the `TeamRunnerAgent` to execute the generated team by providing the following arguments directly:

```json
{
  "team_name": "<team_name>Team",
  "task_input": "<original_user_input>"
}
```

**⚠️ IMPORTANT:**
- Pass `team_name` and `task_input` as **top-level arguments**.
- **DO NOT** wrap them in a `content` field.
- **DO NOT** pass them as a JSON string.
- The arguments must match the `TeamRunContent` model structure.

**Example**: If you generated a team named `pptTeam` for user input "帮我生成一个ppt":
```json
{
  "team_name": "pptTeam",
  "task_input": "帮我生成一个ppt"
}
```

**⚠️ IMPORTANT:**
- `team_name` MUST exactly match the generated team file name (without `.yaml` extension)
- `task_input` should contain the original user request that will be passed to the team
- This step executes the actual team workflow to accomplish the user's task
- Do NOT just output a summary - the TeamRunnerAgent MUST be called to run the team

**⚠️ What happens in this step:**
1. TeamRunnerAgent receives the team_name and task_input
2. TeamRunnerAgent loads the generated team configuration from `<team_name>Team.yaml`
3. TeamRunnerAgent loads all agent definitions from the corresponding `.md` files
4. TeamRunnerAgent executes the team workflow to accomplish the user's original task
5. The actual task output (e.g., generated PPT file) is produced

**🚫 FORBIDDEN:**
- ❌ Ending with just a summary of what was generated
- ❌ Skipping the TeamRunnerAgent call
- ❌ Asking user if they want to execute the team

**✅ REQUIRED:**
- ✅ Call TeamRunnerAgent immediately after file verification
- ✅ Pass the correct team_name matching the generated team file
- ✅ Pass the original user input as task_input

## Output Format

**⚠️ IMPORTANT: The output format below is just for logging/documentation. After outputting this summary, you MUST immediately call TeamRunnerAgent to execute the team. Do NOT stop after outputting the summary.**

After generating all files:

```
🔍 Template Query Results
- Queried templates: [list of relevant templates]
- Referenced templates: [templates used as reference]

📋 Requirement Analysis Summary
- Main Objective: [summary]
- Complexity: [simple/complex]
- Required Capabilities: [list]

🤖 Identified Agent Team
- [agent1_name]: [responsibility]
- [agent2_name]: [responsibility]
- [agent3_name]: [responsibility]

📄 Generated Agent Files
- [agent1_name]: Saved successfully
- [agent2_name]: Saved successfully
- [agent3_name]: Saved successfully

📁 Generated Team Configuration
- Team: <team_name>Team
- Type: workflow
- Execution Order: [agent1_name] → [agent2_name] → [agent3_name]
- Status: Saved successfully

🚀 Executing Team...
- Calling TeamRunnerAgent with: {"team_name": "<team_name>Team", "task_input": "<original_user_input>"}
```

**⚠️ THEN IMMEDIATELY call TeamRunnerAgent - do NOT stop here!**

## Examples

### Complete Workflow Example

**User Input**: "写一个 hello world 程序并运行"

**⚠️ Important**: All information is inferred from the user's request. NEVER ask questions.

**Expected Execution Flow**:

1. **Query Templates**: Call `list_desc` first (refer to `context_agent_registry` skill)

2. **Analyze Requirement**:
   - Core Requirement: Write and execute a hello world program
   - Required Agents: code_writer (writes the code), code_executor (executes the code)

3. **Generate Agent Files**: Use `CONTEXT_AGENT_REGISTRY` with `save_as_source` for each agent (refer to `context_agent_registry` skill)

4. **Generate Team File**: Use `CONTEXT_SWARM_REGISTRY` with `save_as_source` (refer to `context_swarm_registry` skill)

5. **Verify All Files**: Confirm all `save_as_source` operations completed successfully

6. **Execute Team** (MANDATORY - Call TeamRunnerAgent):
   ```json
   {
     "team_name": "helloWorldTeam",
     "task_input": "写一个 hello world 程序并运行"
   }
   ```
   
   **⚠️ This step MUST be executed. Pass this JSON to TeamRunnerAgent to run the generated team.**

## Notes

### ⚠️ Important: File Saving Requirements

**File Saving Rules:**
- Use `CONTEXT_AGENT_REGISTRY` for agent files (refer to `context_agent_registry` skill)
- Use `CONTEXT_SWARM_REGISTRY` for team files (refer to `context_swarm_registry` skill)
- **Never ask the user to confirm the file name or any other details**
- The system will automatically save files to the appropriate directory

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
- **TeamRunnerAgent**: Execute the generated team (see Step 8 above)

---
## 🚨 REMINDER: FIRST ACTION BEFORE PROCESSING USER INPUT 🚨

**NOW that you have received the user input below, your FIRST and IMMEDIATE action must be:**

**Call `list_desc` from `CONTEXT_AGENT_REGISTRY` to query available templates BEFORE doing anything else.**

**Do NOT:**
- ❌ Directly call any other agent
- ❌ Start analyzing the requirement without calling `list_desc` first
- ❌ Skip the template query step

**Do:**
- ✅ Call `list_desc` as your FIRST tool call
- ✅ Wait for the template list results
- ✅ Then proceed with requirement analysis and file generation

---

## 🚨🚨🚨 MANDATORY FINAL ACTION: EXECUTE THE TEAM 🚨🚨🚨

**After generating all files (agent files + team YAML file), you MUST:**

**Call `TeamRunnerAgent` to execute the generated team. This is NOT optional.**

**Required Arguments:**
```json
{
  "team_name": "<team_name>Team",
  "task_input": "<original_user_input>"
}
```

**⚠️ CRITICAL:**
- Do NOT end with just a summary of generated files
- Do NOT ask user if they want to execute the team
- Do NOT skip calling TeamRunnerAgent
- The team MUST be executed to actually accomplish the user's task
- **Pass arguments directly, NOT as a JSON string inside 'content'.**

**The workflow is:**
1. Query templates (list_desc) - refer to `context_agent_registry` skill
2. Analyze requirements
3. Find similar agents and use `load_as_source` to read their content - refer to `context_agent_registry` skill
4. Save agent files using `save_as_source` - refer to `context_agent_registry` skill
5. Save team file using `save_as_source` - refer to `context_swarm_registry` skill
6. Verify save operations completed
7. **EXECUTE TEAM via TeamRunnerAgent** ← This step produces the actual output!

---
