---
name: agent-creator
description: "Guide for creating Multi-Agent System (MAS) implementations. Use this skill when you need to create agent teams with different coordination patterns: centralized (TeamSwarm with orchestrator) or decentralized (Swarm workflow). Uses filesystem-server tools (write_file, edit_file, read_file) to create complete agent team structures in ./agents/ directory with proper configuration, prompts, and swarm definitions based on task requirements."
ptc_tools: ["read_file","write_file","edit_file"]
---
# Agent Creator

This skill provides guidance and templates for creating Multi-Agent System (MAS) implementations in the AWorld framework. It supports two main coordination patterns: centralized (TeamSwarm) and decentralized (Swarm).

## Quick Start

**How This Skill Works:**
- 🛠️ **Uses MCP Tools**: Leverages filesystem-server's `write_file`, `edit_file`, and `read_file` tools
- 📁 **Target Location**: Creates all agent structures in `./agents/` directory
- 🔄 **Auto-Integration**: Works seamlessly with aworld_cli's agent discovery
- 📋 **Template-Based**: Reads templates from `references/` and adapts them to your needs

**Typical Workflow:**
1. **Analyze**: Understand task requirements and determine MAS type (TeamSwarm or Swarm)
2. **Read**: Use `read_file` to read appropriate templates from `references/` directory
3. **Create**: Use `write_file` to create directory structure and files in `./agents/{team_name}/`
4. **Customize**: Adapt template content to specific task requirements
5. **Verify**: Ensure all files are created with correct structure and imports

**Example:**
```
User: "Create a research team with an orchestrator and two workers"

Agent Actions:
1. read_file("references/teamswarm_template.md")
2. write_file("./agents/ResearchTeam/__init__.py", "")
3. write_file("./agents/ResearchTeam/agents/__init__.py", "")
4. write_file("./agents/ResearchTeam/agents/orchestrator/config.py", "...")
5. write_file("./agents/ResearchTeam/agents/orchestrator/prompt.py", "...")
6. write_file("./agents/ResearchTeam/agents/orchestrator/agent.py", "...")
... (repeat for workers)
7. write_file("./agents/ResearchTeam/agents/swarm.py", "...")
```

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

1. **Read Templates**: Use `read_file` to read templates from `references/` directory
   - `references/teamswarm_template.md` - Complete TeamSwarm implementation template
   - `references/swarm_template.md` - Complete Swarm implementation template

2. **Create Structure**: Use `write_file` to create files in `./agents/{team_name}/` directory
   - Create `__init__.py` files for Python packages
   - Create agent subdirectories under `agents/` folder
   - Create configuration, prompt, and implementation files

3. **Customize Content**: Adapt template content to specific task requirements

Each template includes:
- Agent configuration (`config.py`)
- System prompts (`prompt.py`)
- Agent class definition (`agent.py`)
- Swarm initialization (`swarm.py`)
- Directory structure

### Step 5: Implement Swarm Initialization

Create `swarm.py` that:
- Imports all agent classes
- Configures agent instances with their prompts and configs
- Initializes the appropriate Swarm type (TeamSwarm or Swarm)
- Registers the team with `@agent_team` decorator

## Quick Start Examples

### Example 1: Creating a TeamSwarm

```python
# agents/swarm.py
from aworld.core.agent.swarm import TeamSwarm
from aworld.core.context.amni import ApplicationContext
from aworldappinfra.core.registry import agent_team
from .orchestrator_agent.agent import OrchestratorAgent
from .worker_agent.agent import WorkerAgent

@agent_team(
    name="MyTeam",
    desc="Team for complex tasks",
    context_config=AmniConfigFactory.create(debug_mode=True)
)
async def build_swarm(context: ApplicationContext) -> TeamSwarm:
    orchestrator = OrchestratorAgent(
        name="orchestrator",
        desc="Coordinates tasks",
        conf=orchestrator_config,
        system_prompt=orchestrator_prompt,
    )
    
    worker = WorkerAgent(
        name="worker",
        desc="Executes tasks",
        conf=worker_config,
        system_prompt=worker_prompt,
    )
    
    return TeamSwarm(orchestrator, worker, max_steps=30)
```

### Example 2: Creating a Swarm

```python
# agents/swarm.py
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import ApplicationContext
from aworldappinfra.core.registry import agent_team
from aworld.agents.llm_agent import Agent

@agent_team(
    name="MySwarm",
    desc="Swarm for sequential tasks",
    context_config=AmniConfigFactory.create(debug_mode=True)
)
async def build_swarm(context: ApplicationContext) -> Swarm:
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

## Implementation Workflow

### Using Filesystem Tools (Recommended)

**Step-by-Step Process:**

1. **Analyze Requirements**
   - Determine MAS type (TeamSwarm or Swarm)
   - Identify agent roles and responsibilities
   - List required MCP servers and tools

2. **Read Templates**
   ```
   Use read_file tool to read:
   - references/teamswarm_template.md (for TeamSwarm)
   - references/swarm_template.md (for Swarm)
   - references/agent_config_reference.md (for configuration options)
   ```

3. **Create Directory Structure**
   ```
   Target location: ./agents/{team_name}/
   
   For TeamSwarm:
   ./agents/{team_name}/
   ├── __init__.py
   └── agents/
       ├── __init__.py
       ├── {orchestrator_name}/
       │   ├── __init__.py
       │   ├── config.py
       │   ├── prompt.py
       │   └── agent.py
       ├── {worker1_name}/
       │   ├── __init__.py
       │   ├── config.py
       │   ├── prompt.py
       │   └── agent.py
       └── swarm.py
   
   For Swarm:
   ./agents/{swarm_name}/
   ├── __init__.py
   └── agents/
       ├── __init__.py
       ├── config.py
       ├── prompt.py
       └── swarm.py
   ```

4. **Create Files Using write_file**
   - Use `write_file` tool to create each file
   - Adapt template content to specific requirements
   - Customize agent prompts based on task needs
   - Configure MCP servers and tools as needed

5. **Verify Structure**
   - Ensure all `__init__.py` files are created
   - Check that imports are correct
   - Validate configuration parameters

**Key Points:**
- 📁 Always create in `./agents/` directory (auto-integrates with aworld_cli)
- 🛠️ Use filesystem-server tools: `write_file`, `edit_file`, `read_file`
- 📋 Follow templates from `references/` directory
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

### references/

- `teamswarm_template.md` - Complete TeamSwarm implementation guide with code templates
- `swarm_template.md` - Complete Swarm implementation guide with code templates
- `agent_config_reference.md` - AgentConfig and ModelConfig parameter reference

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

6. **Documentation**: Document agent responsibilities, expected inputs/outputs, and coordination patterns.

## Common Patterns

### Pattern 1: Research Team (TeamSwarm)
Orchestrator analyzes task → delegates to research agents → synthesizes results

### Pattern 2: Content Generation Pipeline (Swarm)
Analysis → Planning → Content Creation → Formatting

### Pattern 3: Code Review Team (TeamSwarm)
Orchestrator routes code → different reviewers (security, style, performance) → aggregates feedback

See `references/` for detailed implementation examples of each pattern.
