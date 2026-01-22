---
name: agent-creator
description: "Guide for creating Multi-Agent System (MAS) implementations. Use this skill when you need to create agent teams with different coordination patterns: centralized (TeamSwarm with orchestrator) or decentralized (Swarm workflow). Uses filesystem-server tools (write_file, edit_file, read_file) to create complete agent team structures in ./agents/ directory with proper configuration, prompts, and swarm definitions based on task requirements."
---
# Agent Creator

This skill provides guidance and templates for creating Multi-Agent System (MAS) implementations in the AWorld framework. It supports two main coordination patterns: centralized (TeamSwarm) and decentralized (Swarm).

## Quick Start

**How This Skill Works:**
- 🛠️ **Uses MCP Tools**: Leverages filesystem-server's `write_file`, `edit_file`, and `read_file` tools
- 📁 **Target Location**: Creates all agent structures in `./agents/` directory
- 🔄 **Auto-Integration**: Works seamlessly with aworld_cli's agent discovery
- 📋 **Template-Based**: Uses built-in templates and examples to guide implementation

**Typical Workflow:**
1. **Analyze**: Understand task requirements and determine MAS type (TeamSwarm or Swarm)
2. **Design**: Plan agent structure, roles, and responsibilities
3. **Create**: Use `write_file` to create directory structure and files in `./agents/{team_name}/`
4. **Customize**: Implement agent configs, prompts, and swarm initialization
5. **Verify**: Ensure all files are created with correct structure, imports, and **proper indentation** (top-level code at column 0)
6. **Validate Registration**: Run `aworld-cli list` to verify the agent is properly registered and discoverable
7. **Smoke Test**: Run `aworld-cli --task "Hello" --agent="YourTeamName"` to test basic agent functionality

**Example:**
```
User: "Create a research team with an orchestrator and two workers"

Agent Actions:
1. write_file("./agents/ResearchTeam/__init__.py", "")
2. write_file("./agents/ResearchTeam/agents/__init__.py", "")
3. write_file("./agents/ResearchTeam/agents/orchestrator/config.py", "...")
4. write_file("./agents/ResearchTeam/agents/orchestrator/prompt.py", "...")
5. ... (repeat config.py and prompt.py for workers, no agent.py needed)
6. write_file("./agents/ResearchTeam/agents/swarm.py", "...")  # Uses Agent class directly
7. Run: aworld-cli list  # Verify ResearchTeam is registered and visible
8. Run: aworld-cli --task "Hello" --agent="ResearchTeam"  # Smoke test basic functionality
```

**Key Simplification**: No need to create `agent.py` files - use `Agent` class directly in `swarm.py`!

**⚠️ Critical: Python File Indentation**
- When creating Python files (`config.py`, `prompt.py`, `swarm.py`), **all top-level code must start at column 0** (no indentation)
- If using Python code to generate files, ensure string content doesn't have extra indentation
- Always verify generated files using `read_file` to check indentation is correct
- Example: `prompt.py` should start with `"""` at column 0, not indented

## Overview

Multi-Agent Systems can be structured in different ways depending on task complexity and coordination needs:

1. **Centralized (TeamSwarm)**: Uses an orchestrator agent to coordinate and delegate tasks to specialized worker agents. Best for complex tasks requiring dynamic routing and coordination.

2. **Decentralized (Swarm)**: Agents execute in a workflow sequence. Best for well-defined, sequential tasks where each agent has a specific role.

## MAS Type Selection Guide

### When to Use TeamSwarm (Centralized)

Choose TeamSwarm when:

- Task requires dynamic routing based on intermediate results
- Multiple specialized agents need coordination from a central decision-maker
- Task complexity requires adaptive planning and delegation
- Agent selection depends on context and task analysis
- Example: Browser-based research tasks where an orchestrator analyzes requirements and delegates to web browsing or coding agents

**Architecture Pattern:**
```
Orchestrator Agent (Leader)
    ├── Agent 1 (Specialist)
    ├── Agent 2 (Specialist)
    └── Agent 3 (Specialist)
```

### When to Use Swarm (Decentralized)

Choose Swarm when:

- Task has a clear, sequential workflow
- Each agent performs a specific step in a pipeline
- Agent order is predetermined
- No dynamic routing or coordination needed
- Example: PPT generation workflow: analysis → planning → content → HTML generation

**Architecture Pattern:**
```
Agent 1 → Agent 2 → Agent 3 → Agent 4
```

## Creating a MAS

### Step 1: Analyze Task Requirements

1. Identify the core task and its complexity
2. Determine if dynamic coordination is needed (TeamSwarm) or fixed sequence (Swarm)
3. List required agent roles and their responsibilities
4. Identify MCP servers and tools needed by each agent

### Step 2: Choose MAS Type

Refer to the selection guide above. If unsure, start with Swarm for simpler workflows; upgrade to TeamSwarm if dynamic coordination becomes necessary.

### Step 3: Design Agent Structure

For **TeamSwarm**:
- Design orchestrator agent with coordination logic
- Design specialized worker agents
- Define handoff patterns from orchestrator to workers

For **Swarm**:
- Define sequential agent roles
- Establish data flow between agents
- Ensure each agent has clear input/output responsibilities

### Step 4: Create Agent Implementation

**Using Filesystem Tools:**

Use `write_file` tool from filesystem-server to create files directly in `./agents/` directory:

1. **Create Structure**: Use `write_file` to create files in `./agents/{team_name}/` directory
   - Create `__init__.py` files for Python packages
   - Create agent subdirectories under `agents/` folder (optional, can use Agent directly)
   - Create configuration and prompt files

2. **Customize Content**: Implement agent-specific configs, prompts, and swarm initialization

**Important**: You can directly use `aworld.agents.llm_agent.Agent` class without creating custom Agent classes. Simply create:
- Agent configuration (`config.py`) - Optional, can use default AgentConfig
- System prompts (`prompt.py`) - Required for agent behavior
- Swarm initialization (`swarm.py`) - Required, instantiates Agent directly

**Simplified Approach** (Recommended):
- Create `config.py` and `prompt.py` for each agent role
- In `swarm.py`, directly instantiate `Agent` class with config and prompt
- No need to create custom `agent.py` files unless you need custom behavior

### Step 5: Implement Swarm Initialization

Create `swarm.py` that:
- Imports `Agent` from `aworld.agents.llm_agent` (or custom agent classes if needed)
- Imports config and prompt from agent subdirectories (or defines them inline)
- Instantiates Agent instances directly with config and prompt
- Initializes the appropriate Swarm type (TeamSwarm or Swarm)
- Registers the team with `@agent` decorator from `aworld_cli.core`

## Quick Start Examples

### Example 1: Creating a TeamSwarm

**Simplified approach using Agent directly:**

```python
# agents/swarm.py
from aworld.core.agent.swarm import TeamSwarm
from aworld.core.context.amni.config import AmniConfigFactory
from aworld_cli.core import agent
from aworld.agents.llm_agent import Agent
from .orchestrator.config import orchestrator_config
from .orchestrator.prompt import orchestrator_prompt
from .worker.config import worker_config
from .worker.prompt import worker_prompt

@agent(
    name="MyTeam",
    desc="Team for complex tasks",
    context_config=AmniConfigFactory.create(debug_mode=True),
    metadata={"version": "1.0.0", "creator": "aworld-cli"}
)
def build_swarm() -> TeamSwarm:
    """
    Build and configure the MyTeam swarm.
    
    This creates a TeamSwarm with an orchestrator and worker agents.
    Uses Agent class directly without custom agent classes.
    
    Returns:
        Configured TeamSwarm instance with all agents
    """
    orchestrator = Agent(
        name="orchestrator",
        desc="Coordinates tasks",
        conf=orchestrator_config,
        system_prompt=orchestrator_prompt,
    )
    
    worker = Agent(
        name="worker",
        desc="Executes tasks",
        conf=worker_config,
        system_prompt=worker_prompt,
    )
    
    return TeamSwarm(orchestrator, worker, max_steps=30)
```

**Note**: You can also define config and prompt inline if preferred, or create custom Agent classes only when you need custom behavior.

### Example 2: Creating a Swarm

```python
# agents/swarm.py
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni.config import AmniConfigFactory
from aworld_cli.core import agent
from aworld.agents.llm_agent import Agent

@agent(
    name="MySwarm",
    desc="Swarm for sequential tasks",
    context_config=AmniConfigFactory.create(debug_mode=True),
    metadata={"version": "1.0.0", "creator": "aworld-cli"}
)
def build_swarm() -> Swarm:
    """
    Build and configure the MySwarm sequential workflow.
    
    This creates a Swarm with agents executing in sequence.
    
    Returns:
        Configured Swarm instance with sequential agents
    """
    agent1 = Agent(
        name="analysis_agent",
        desc="Analyzes requirements",
        conf=agent_config,
        system_prompt=analysis_prompt,
    )
    
    agent2 = Agent(
        name="execution_agent",
        desc="Executes tasks",
        conf=agent_config,
        system_prompt=execution_prompt,
    )
    
    return Swarm(agent1, agent2, max_steps=30)
```

### Example 3: Creating a Single Agent (Markdown Format)

**Simple single agent using Markdown format (recommended for simple agents):**

```markdown
---
name: DocumentAgent
description: A specialized AI agent focused on document management and generation using filesystem-server
mcp_servers: ["filesystem-server"]
mcp_config: {
    "mcpServers": {
        "filesystem-server": {
            "type": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                "~/workspace"
            ]
        }
    }
}
---
### 🎯 Mission
A document management assistant that helps you read, analyze, organize, and generate documents.

### 💪 Core Capabilities
- **Document Reading & Analysis**: Read and analyze existing documents
- **Report Generation**: Generate reports from data files
- **Document Organization**: Organize documents into folders by category/date
- **Document Creation**: Create markdown documentation and summaries
- **Document Merging**: Merge multiple documents into one
- **Information Extraction**: Extract and summarize key information from files

### 📥 Input Specification
Users can request:
- Document analysis: "Read all markdown files and create a summary"
- Report generation: "Generate a report from this CSV file"
- Document organization: "Organize my documents by date"
- Document creation: "Create a meeting notes template"
- Information extraction: "Extract key points from these documents"

### 📤 Output Format
- Clear, structured document summaries
- Well-formatted reports and documents
- Logical folder structures
- Extracted key information

### ✅ Usage Examples

**Example 1: Document Summary**
```
User: Read all markdown files in the docs folder and create a summary document
Agent: I'll read all markdown files, analyze their content, and create a comprehensive summary.
```

**Example 2: Report Generation**
```
User: Generate a report from the data in this CSV file
Agent: I'll read the CSV file, analyze the data, and generate a formatted report.
```

**Example 3: Document Organization**
```
User: Organize my documents by date into separate folders
Agent: I'll read the documents, extract their dates, and organize them into folders.
```

### 🎨 Guidelines
- Always read existing files before modifying them
- Create well-structured and formatted documents
- Organize documents logically
- Extract and present information clearly
- Ask clarifying questions if requirements are unclear
```

**File location**: `./agents/document_agent.md`

**Note**: 
- Markdown format is simpler and recommended for single agents
- YAML front matter defines agent configuration (name, description, mCP servers, etc.)
- Markdown body content becomes the system prompt
- No Python code needed - aworld_cli automatically loads `.md` files as agents
- Use Python format (Example 1 & 2) when you need more control or complex logic

## Implementation Workflow

### Using Filesystem Tools (Recommended)

**Step-by-Step Process:**

1. **Analyze Requirements**
   - Determine MAS type (TeamSwarm or Swarm)
   - Identify agent roles and responsibilities
   - List required MCP servers and tools

2. **Create Directory Structure**
   ```
   Target location: ./agents/{team_name}/
   
   For TeamSwarm (Simplified - using Agent directly):
   ./agents/{team_name}/
   ├── __init__.py
   └── agents/
       ├── __init__.py
       ├── {orchestrator_name}/
       │   ├── __init__.py
       │   ├── config.py          # Optional, can use default
       │   └── prompt.py          # Required
       ├── {worker1_name}/
       │   ├── __init__.py
       │   ├── config.py          # Optional, can use default
       │   └── prompt.py          # Required
       └── swarm.py               # Instantiates Agent directly
   
   For Swarm (Simplified - using Agent directly):
   ./agents/{swarm_name}/
   ├── __init__.py
   └── agents/
       ├── __init__.py
       ├── {agent1_name}/
       │   ├── __init__.py
       │   ├── config.py          # Optional
       │   └── prompt.py          # Required
       ├── {agent2_name}/
       │   ├── __init__.py
       │   ├── config.py          # Optional
       │   └── prompt.py          # Required
       └── swarm.py               # Instantiates Agent directly
   
   Note: agent.py files are optional - only create them if you need custom Agent behavior.
   Otherwise, use Agent class directly in swarm.py.
   ```

3. **Create Files Using write_file**
   - Use `write_file` tool to create each file
   - Create `config.py` and `prompt.py` for each agent (config is optional)
   - **Important**: You don't need to create `agent.py` files - use `Agent` class directly in `swarm.py`
   - Customize agent prompts based on task needs
   - Configure MCP servers and tools in config or directly in swarm.py
   
   **⚠️ CRITICAL: Python File Indentation**
   - When creating Python files (`config.py`, `prompt.py`, `swarm.py`), ensure content starts at column 0 (no indentation)
   - Top-level code (imports, constants, functions) should have NO leading spaces
   - If using Python code to generate files, ensure string content doesn't have extra indentation
   - Example of CORRECT format:
     ```python
     # prompt.py - CORRECT (no indentation)
     """
     Agent System Prompt
     """
     
     AGENT_PROMPT = """
     Your prompt content here...
     """
     ```
   - Example of WRONG format (has extra indentation):
     ```python
     # prompt.py - WRONG (has 4-space indentation)
         """
         Agent System Prompt
         """
         
         AGENT_PROMPT = """
         Your prompt content here...
         """
     ```
   - Always verify generated files have correct indentation before using them
   
   **⚠️ CRITICAL: When Generating Files with Python Code**
   
   If you're writing Python code that generates files (e.g., creating markdown files with multi-line strings), be aware of indentation issues:
   
   **Problem**: When Python code is executed in an indented context (function, if block, etc.), multi-line strings inherit that indentation, causing generated files to have unwanted leading spaces.
   
   **Solution 1: Use `textwrap.dedent()` (Recommended)**
   ```python
   from textwrap import dedent
   
   usage_guide = dedent("""\
   # Title
   
   ## Section
   Content here...
   """)
   
   with open("file.md", "w", encoding="utf-8") as f:
       f.write(usage_guide)
   ```
   
   **Solution 2: Use `write_file` tool directly (Best Practice)**
   Instead of generating Python code, use the `write_file` MCP tool directly:
   ```
   write_file(
       file_path="./agents/usage_guide.md",
       content="# Title\n\n## Section\nContent here..."
   )
   ```
   This avoids indentation issues entirely since the content is passed as a parameter, not embedded in code.
   
   **Solution 3: Start string at column 0, use explicit newlines**
   ```python
   usage_guide = """# Title

## Section
Content here...
"""
   # Note: First line starts immediately after """, no indentation
   ```
   
   **Common Mistake to Avoid:**
   ```python
   # WRONG - string content inherits indentation
   def create_file():
       usage_guide = '''# Title
       ## Section
       Content here...
       '''
       with open("file.md", "w") as f:
           f.write(usage_guide)  # File will have unwanted indentation!
   ```
   
   **Best Practice**: Always use `write_file` MCP tool directly instead of generating Python code that writes files. This is simpler, more reliable, and avoids indentation issues.

4. **Verify Structure**
   - Ensure all `__init__.py` files are created
   - Check that imports are correct
   - Validate configuration parameters
   - **⚠️ CRITICAL: Verify Python file indentation**
     - All top-level code (imports, constants, functions) must start at column 0
     - No leading spaces for module-level code
     - Use `read_file` to verify generated files have correct indentation
     - If files have incorrect indentation, use `edit_file` to fix them

5. **Validate Agent Registration**
   - Run `aworld-cli list` command to verify the agent is registered
   - Check that your agent appears in the list with correct name and description
   - If agent is not listed, check for:
     - Python syntax errors in generated files (imports, indentation)
     - Missing `@agent` decorator in `swarm.py`
     - Incorrect package structure (missing `__init__.py` files)
   - Example expected output:
     ```
     Available Agents:
     ✓ MyTeam - Team for complex tasks (version: 1.0.0)
     ```

6. **Smoke Test Agent**
   - Run a simple test to verify the agent can execute basic tasks
   - Command: `aworld-cli --task "Hello" --agent="YourTeamName"`
   - This ensures:
     - Agent initialization works correctly
     - MCP servers and tools are properly configured
     - Agent can process and respond to basic queries
     - No runtime errors in agent logic
   - Example expected behavior:
     ```bash
     $ aworld-cli --task "Hello" --agent="MyTeam"
     🚀 Starting agent: MyTeam
     🤖 Processing task: Hello
     ✅ Task completed successfully
     ```
   - If errors occur, check:
     - MCP server configurations in agent config
     - Tool permissions and availability
     - Agent prompt logic and handoff patterns
     - System dependencies and environment setup

**Key Points:**
- 📁 Always create in `./agents/` directory (auto-integrates with aworld_cli)
- 🛠️ Use filesystem-server tools: `write_file`, `edit_file`, `read_file`
- 📋 Follow examples and patterns in this guide
- 🔄 No need to run external scripts - all done through MCP tools

### Alternative: Using Script (Optional)

For quick prototyping, you can also use the provided script:

```bash
# Creates structure in ./agents/MyTeam
python scripts/create_mas.py --type teamswarm --name MyTeam --agents orchestrator,worker1,worker2

# Creates structure in ./agents/MySwarm
python scripts/create_mas.py --type swarm --name MySwarm --agents agent1,agent2,agent3
```

**Note**: The script approach is less flexible than using filesystem tools directly, as it generates boilerplate code that still requires customization.

## Resources

### scripts/

- `create_mas.py` - Script to generate MAS structure from command line
- `validate_structure.py` - Validates generated MAS structure

### assets/

- `teamswarm_structure.txt` - Directory structure template for TeamSwarm
- `swarm_structure.txt` - Directory structure template for Swarm

## Best Practices

1. **Start Simple**: Begin with Swarm for straightforward workflows; upgrade to TeamSwarm only if dynamic coordination is needed.

2. **Clear Agent Roles**: Each agent should have a single, well-defined responsibility.

3. **Prompt Design**: System prompts should clearly define agent behavior, decision criteria, and interaction patterns.

4. **Error Handling**: Implement robust error handling in orchestrator agents for TeamSwarm.

5. **Testing**: Test each agent independently before integrating into the swarm.

6. **Validate Registration**: Always run `aworld-cli list` after creating an agent to verify it's properly registered and discoverable by the system.

7. **Smoke Test**: Run `aworld-cli --task "Hello" --agent="YourTeamName"` to perform a basic functionality test before deploying or using the agent in production workflows.

8. **Documentation**: Document agent responsibilities, expected inputs/outputs, and coordination patterns.

## Common Patterns

### Pattern 1: Research Team (TeamSwarm)
Orchestrator analyzes task → delegates to research agents → synthesizes results

### Pattern 2: Content Generation Pipeline (Swarm)
Analysis → Planning → Content Creation → Formatting

### Pattern 3: Code Review Team (TeamSwarm)
Orchestrator routes code → different reviewers (security, style, performance) → aggregates feedback

Refer to the Quick Start Examples section above for detailed implementation examples of each pattern.
