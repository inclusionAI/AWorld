# Agent Skills in AWorld Framework

## Overview

This example demonstrates how to build specialized AI agents using **Agent Skills** in the AWorld Framework. Inspired by [Anthropic's Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills), this implementation shows how to equip general-purpose agents with domain-specific expertise through composable, modular capabilities.

Agent Skills transform general-purpose agents into specialized agents by packaging procedural knowledge, tools, and resources into discoverable, activatable units.

## Table of Contents

- [Core Concepts](#core-concepts)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Implementation Details](#implementation-details)
- [Best Practices](#best-practices)
- [Core Implementation Logic](./IMPLEMENTATION.md) 📄

## Core Concepts

### What is an Agent Skill?

An **Agent Skill** is a composable capability unit that contains:

1. **Metadata**: Name, description, and usage guidelines
2. **Tool Mapping**: Specific MCP tools the skill provides access to
3. **Active State**: Optional auto-activation flag

### Progressive Disclosure

AWorld Skills follow the **progressive disclosure** principle:

1. **Level 1 - Metadata**: Skill metadata loaded into agent's system prompt
2. **Level 2 - Activation**: Agent activates skill when needed (manual or auto)
3. **Level 3 - Tool Usage**: Agent accesses skill tools and usage guide

```
System Prompt (Metadata) → Skill Activation → Tool Usage
     [browser, planning]  →  active_skill()  →  [add_todo, get_todo]
```

## Architecture

```
examples/skill_agent/
├── quick_start.py              # Entry point demonstrating skill-based agent
├── agents/
│   ├── swarm.py                # Swarm configuration with skill definitions
│   └── orchestrator_agent/     # Orchestrator agent implementation
│       ├── agent.py            # Agent logic
│       ├── config.py           # Agent configuration
│       └── prompt.py           # System prompt
├── mcp_tools/
│   ├── mcp_config.py           # MCP server configurations
│   ├── contextserver.py        # Context management MCP server
│   ├── terminal_server.py      # Terminal operations MCP server
│   ├── document_server.py      # Document handling MCP server
│   └── image_server.py         # Image processing MCP server
└── data/                       # Session data and workspace
```

## Quick Start

### 1. Installation

```bash
# Install AWorld Framework
cd /path/to/AWorld
pip install -e .

# Install dependencies
pip install -r aworld/requirements.txt
```

### 2. Environment Setup

Create a `.env` file with the following configuration:

```bash
# LLM Configuration
LLM_MODEL_NAME=claude-3-5-sonnet-20241022
LLM_PROVIDER=anthropic
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.anthropic.com

# AmniContext Configuration
AMNI_RAG_TYPE=local
WORKSPACE_TYPE=file
WORKSPACE_PATH=./examples/skill_agent/data
DB_PATH=./examples/skill_agent/data/amni_context.db

# Vector Store Configuration
VECTOR_STORE_PROVIDER=chroma
CHROMA_PATH=./examples/skill_agent/data/chroma_db

# Embedding Configuration
EMBEDDING_PROVIDER=openai
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=your_embedding_api_key
EMBEDDING_MODEL_NAME=text-embedding-3-small
EMBEDDING_MODEL_DIMENSIONS=1536

# Chunking Configuration
CHUNK_PROVIDER=langchain
CHUNK_SIZE=1000
CHUNK_OVERLAP=200
CHUNK_SEPARATOR=\n\n

# Reranker Configuration (Optional)
RERANKER_PROVIDER=http
RERANKER_BASE_URL=your_reranker_url
RERANKER_API_KEY=your_reranker_key
RERANKER_MODEL_NAME=bge-reranker-v2-m3
```

### 3. Run the Example

```bash
cd examples/skill_agent
python quick_start.py
```

## How It Works

### Step 1: Define Skills

Skills are defined in `agents/swarm.py` using the `skill_configs` parameter:

```python
skill_configs={
    "browser": {
        "name": "Browser",
        "desc": "Web browsing and automation",
        "usage": "Use for navigating websites, clicking elements, taking screenshots",
        "active": False,  # Not auto-activated
        "tool_list": {
            "ms-playwright": []  # Empty list = all tools from this MCP server
        }
    },
    "planning": {
        "name": "Planning",
        "desc": "Task planning and progress tracking",
        "usage": "Use for breaking down tasks and tracking progress with todos",
        "active": True,  # Auto-activated at initialization
        "tool_list": {
            "amnicontext-server": ["add_todo", "get_todo"]  # Specific tools only
        }
    },
    "scratchpad": {
        "name": "Scratchpad",
        "desc": "Knowledge documentation and retrieval",
        "usage": "Use for recording and retrieving important information during task execution",
        "tool_list": {
            "amnicontext-server": ["add_knowledge", "get_knowledge", "update_knowledge"]
        }
    }
}
```

### Step 2: Configure MCP Servers

MCP servers provide the actual tool implementations in `mcp_tools/mcp_config.py`:

```python
MCP_CONFIG = {
    "mcpServers": {
        "ms-playwright": {
            "command": "npx",
            "args": ["@playwright/mcp@0.0.37", "--no-sandbox"],
            # ... configuration
        },
        "amnicontext-server": {
            "command": "python",
            "args": ["-m", "mcp_tools.contextserver"],
            # ... environment variables
        }
    }
}
```

### Step 3: Initialize Context with Skills

The context is configured with the `skills` neuron in `quick_start.py`:

```python
context_config = AmniConfigFactory.create(
    AmniConfigLevel.NAVIGATOR,
    ["basic", "task", "work_dir", "todo", "action_info", "skills"],  # ← skills enabled
    debug_mode=True
)
```

### Step 4: Agent Discovers and Activates Skills

The agent's system prompt includes skill metadata. Skill lifecycle:

1. **Initialization**: Skills with `"active": True` are auto-activated
2. **Discovery**: Agent sees available skills in system prompt
3. **Activation**: Agent calls `active_skill("skill_name")` when needed
4. **Usage**: Agent accesses skill tools and receives usage guide
5. **Offloading**: Agent calls `offload_skill("skill_name")` when done

## Skill Configuration

### Skill Structure

```python
{
    "skill_name": {
        "name": "Display Name",           # Human-readable name
        "desc": "Brief description",      # What the skill does
        "usage": "Usage guidelines",      # Detailed instructions
        "active": False,                  # Optional: auto-activate at init
        "tool_list": {                    # MCP servers and tools
            "mcp_server_name": ["tool1", "tool2"]  # Specific tools
            # or
            "mcp_server_name": []         # Empty list = all tools
        }
    }
}
```

### Tool List Patterns

- **All Tools**: `[]` includes all tools from the MCP server
- **Specific Tools**: `["tool1", "tool2"]` for fine-grained control

## Implementation Details

### Core APIs

**AmniContext Skill Management** (`aworld/core/context/amni/__init__.py`):

```python
# Initialize skills for an agent (called at agent startup)
await context.init_skill_list(skill_configs, namespace="agent_name")

# Activate a skill (returns usage guide)
result = await context.active_skill("planning", namespace="agent_name")
# Returns: "skill planning activated, current skills: ['planning']
#          <skill_guide>Use for breaking down tasks...</skill_guide>"

# Offload a skill
await context.offload_skill("planning", namespace="agent_name")

# Query skills
active_skills = await context.get_active_skills(namespace="agent_name")  # List[str]
all_skills = await context.get_skill_list(namespace="agent_name")       # Dict[str, Any]
skill_names = await context.get_skill_name_list(namespace="agent_name") # List[str]
```

**Skill Tool Translation** (`aworld/mcp_client/utils.py`):

The `skill_translate_tools()` function filters available tools based on activated skills:
- If no skills activated → Only non-MCP tools available
- If skills activated → Only tools from those skills' `tool_list`
- Empty `tool_list: []` → All tools from that MCP server
- Specific `tool_list: ["tool1"]` → Only specified tools

**Skills Neuron** (`aworld/core/context/amni/prompt/neurons/skill_neuron.py`):

Formats available skills into the system prompt with activation instructions.

**Context Skill Tool** (`aworld/core/context/amni/tool/context_skill_tool.py`):

Provides `active_skill` and `offload_skill` as callable tools for the agent.

### Key Features

- **Namespace Isolation**: Each agent manages its own skill state
- **Auto-Activation**: Skills with `"active": True` load at initialization  
- **Progressive Loading**: Tools only available after skill activation
- **Usage Guidance**: Agent receives skill-specific instructions on activation

## Best Practices

### Design Principles

**Focus and Clarity**
- Keep skills single-purpose and focused
- Use descriptive names and clear descriptions
- Write detailed `usage` guidelines for each skill

**Tool Selection**
- Use empty list `[]` for skill needing all tools from a server
- Specify exact tools for focused skills
- Avoid mixing unrelated tools in one skill

### Skill Lifecycle Management

**Activation Strategy**
- Set `"active": True` for foundational skills (e.g., planning)
- Let agent activate specialized skills on-demand
- Offload skills when task phase completes

**Context Efficiency**
- Keep skill metadata concise
- Monitor active skills to avoid context bloat
- Use specific tool lists over `[]` when possible

### Security

**Tool Access Control**
```python
black_tool_actions = {
    "amnicontext-server": ["delete_all_knowledge"],
}

sandbox = Sandbox(
    mcp_servers=mcp_servers,
    mcp_config=mcp_config,
    black_tool_actions=black_tool_actions,
    skill_configs=skill_configs
)
```

### Debugging

Enable debug mode for detailed logging:
```python
context_config = AmniConfigFactory.create(
    AmniConfigLevel.NAVIGATOR,
    ["skills"],
    debug_mode=True
)
```

Monitor:
- Skill activation/offloading events in logs
- Tool usage patterns within each skill
- Agent reasoning about skill selection

## Advanced Topics

### Multi-Agent Skills

Skills can be shared across agents in a swarm:

```python
shared_skills = {"common_tools": {...}}

agent1 = Agent(name="researcher", skill_configs={**shared_skills, "research": {...}})
agent2 = Agent(name="coder", skill_configs={**shared_skills, "coding": {...}})
```

### Skill Composition Patterns

**Sequential Activation**: Activate/offload skills as task phases progress
**Parallel Activation**: Multiple skills active simultaneously for complex tasks

## Comparison with Anthropic's Agent Skills

| Feature | Anthropic Skills | AWorld Skills |
|---------|-----------------|---------------|
| **Skill Definition** | SKILL.md files | Python dict configuration |
| **Progressive Disclosure** | ✅ 3-level (metadata → file → linked files) | ✅ 3-level (metadata → activation → tools) |
| **Tool Integration** | Code execution + instructions | MCP servers + tools |
| **Context Management** | Filesystem-based | AmniContext (DB + vector store) |
| **Activation Model** | Implicit (agent reads files) | Explicit (`active_skill`/`offload_skill`) |
| **Namespace Support** | Per-agent filesystem | ✅ Multi-agent namespaces |
| **Auto-Activation** | ❌ | ✅ `"active": True` |

## Troubleshooting

**Skills Not Appearing**
- Enable `"skills"` in context config neurons list
- Pass `skill_configs` to agent initialization

**Tools Not Available After Activation**
- Verify MCP server is running
- Check tool names match configuration
- Review `black_tool_actions` for conflicts

**Activation Failures**
- Confirm skill name exists in `skill_configs`
- Verify namespace matches agent name
- Check logs for detailed error messages

## Additional Resources

- [AWorld Framework](../../README.md)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [Anthropic Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Multi-Agent Examples](../multi_agents/)

---

**Built with 🚀 by the AWorld Team**

