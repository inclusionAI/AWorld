# AWorld CLI Minimal Example

This is a minimal example demonstrating how to quickly create and use AI Agents with `aworld-cli`.

## Quick Start

### 1. Environment Configuration

Create a `.env` file:

```bash
cp env.template .env
# Edit .env file and fill in your API keys
```

### 2. Run CLI

```bash
# Interactive mode (default)
aworld-cli
```
![/](../../readme_assets/aworld_cli_01.jpg)

## Project Structure

```
aworld_cli_demo/
├── README.md           # This file
├── env.template        # Environment variable template
├── debug_main.py       # Debug script (for debugging main.py)
├── agents/             # Agent definitions directory
│   ├── __init__.py     # Python package initialization file
│   ├── simple_agent.py # Basic Agent definition (Python, BasicAgent)
│   ├── skill_agent.py  # Skill-enabled Agent definition (Python, SkillAgent)
│   ├── pe_team_agent.py # PE Pattern Multi-Agent System (Python, PE Team Agent)
│   └── document_agent.md # Document Management Agent (Markdown, DocumentAgent)
├── skills/             # Custom skills directory
│   └── ...             # Skill definitions
└── .env                # Environment variable configuration (needs to be created)
```

## Agent Definitions

AWorld CLI supports two ways to define agents:

1. **Python Agents** (`.py` files): Define agents programmatically using Python code
2. **Markdown Agents** (`.md` files): Define agents using Markdown with YAML front matter

### Markdown Agent Format

Markdown agents are defined using a simple YAML front matter + Markdown content format. This makes it easy to create and modify agents without writing Python code.

**Example: `agents/document_agent.md`**

```markdown
---
name: DocumentAgent
description: A specialized AI agent focused on document management and generation
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
skills_path: ../skills;https://github.com/user/repo
skill_names: pdf;excel;regex:^context-.*
---
### 🎯 Mission
A friendly and helpful AI assistant...

### 💪 Core Capabilities
- File Operations: Read, write, and manage files
- ...
```

**Key Features:**
- ✅ **Simple YAML front matter**: Define agent metadata, MCP servers, and configuration
- ✅ **Rich Markdown content**: Describe agent capabilities, usage examples, and guidelines
- ✅ **MCP Integration**: Easily configure MCP servers and tools
- ✅ **Skills Support**: Load skills from local paths or GitHub repositories
- ✅ **Regex Pattern Matching**: Use regex patterns to match multiple skills
- ✅ **No Python required**: Perfect for non-developers or quick prototyping

**Skills Configuration:**
- `skills_path`: Skill sources to register (optional, semicolon-separated)
  - Local paths: `../skills` or `/absolute/path/to/skills`
  - GitHub URLs: `https://github.com/user/repo` or `https://github.com/user/repo/tree/branch/skills`
  - Multiple sources: `https://github.com/user/repo;../skills`
- `skill_names`: Skills to use for this agent (optional, semicolon-separated)
  - Exact names: `pdf;excel;browser`
  - Regex patterns: `regex:^context-.*` or `regex:.*browser.*`
  - Mixed: `pdf;excel;regex:^context-.*`

**Available Markdown Agents:**
- `document_agent.md` - DocumentAgent: A specialized agent focused on document management and generation

### Python Agent Format

### Basic Agent

In `agents/simple_agent.py`, we use the `@agent` decorator to define a basic single-agent Swarm:

```python
from aworld.experimental.aworld_cli.core.registry import agent
from aworld.core.agent.swarm import Swarm
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
import os

@agent(
    name="BasicAgent",
    desc="A basic single-agent Swarm that can answer questions and perform simple tasks"
)
def build_simple_swarm():
    """Build a basic Swarm with a single agent."""
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7"))
        )
    )
    
    simple_agent = Agent(
        name="basic_agent",
        desc="Basic AI Agent for simple tasks and Q&A",
        conf=agent_config
    )
    
    return Swarm(simple_agent)
```

### Skill-Enabled Agent

In `agents/skill_agent.py`, we demonstrate how to create an agent with multiple integrated skills and MCP tools:

```python
@agent(
    name="SkillAgent",
    desc="A skill-enabled agent with integrated capabilities for document processing, web browsing, task planning, and knowledge management"
)
def build_skill_agent():
    """Build a skill-enabled agent with multiple integrated capabilities."""
    # Configure skills (planning, browser, custom skills)
    # Configure MCP servers (Playwright, filesystem, Tavily)
    # Return TeamSwarm with the skill-enabled agent
    return TeamSwarm(orchestrator_agent)
```

This agent includes:
- **Bash automation** for command execution
- **Document processing** (Excel, PDF, PPTX)
- **Task planning** and progress tracking
- **Knowledge management** and documentation
- **Web browser automation** via Playwright
- **Web search** via Tavily
- **Custom skills** from the skills directory

### PE Team Agent (Multi-Agent System)

In `agents/pe_team_agent.py`, we demonstrate how to create a Multi-Agent System (MAS) using the PE (Plan-Execute) pattern:

```python
@agent(
    name="PE Team Agent",
    desc="A Multi-Agent System (MAS) that handles complex tasks through the PE (Plan-Execute) pattern with specialized collaborating agents"
)
def build_swarm():
    """Build a Multi-Agent System with specialized agents working together."""
    # Create planner agent - specializes in task planning and decomposition
    planner_agent = Agent(name="planner", ...)
    
    # Create executor agent - specializes in task execution
    executor_agent = Agent(name="executor", ...)
    
    # Create reviewer agent - specializes in quality review and validation
    reviewer_agent = Agent(name="reviewer", ...)
    
    # Create TeamSwarm with multiple agents
    return TeamSwarm(planner_agent, executor_agent, reviewer_agent)
```

**Swarm Support:**
- ✅ Supports `Swarm` instances: Directly return `Swarm(agent1, agent2, ...)`
- ✅ Supports `TeamSwarm`: For better multi-agent coordination
- ✅ Supports single `Agent`: Automatically wrapped as `Swarm(agent)`
- ✅ Supports lazy initialization: Can use callable functions that return Swarm

## Markdown Agent Examples

### DocumentAgent (document_agent.md)

A versatile agent with filesystem-server capabilities focused on **Document Management & Generation**:

- **Document Reading & Analysis**: Read and analyze existing documents
- **Report Generation**: Generate reports from data files
- **Document Organization**: Organize documents into folders by category/date
- **Document Creation**: Create markdown documentation, summaries, and reports
- **Document Merging**: Merge multiple documents into one
- **Information Extraction**: Extract and summarize information from files

**Use Cases:**
- "Read all markdown files in the docs folder and create a summary document"
- "Generate a report from the data in this CSV file"
- "Organize my documents by date into separate folders"
- "Merge all the meeting notes into one document"
- "Extract key information from these PDF files and create a summary"

See `agents/document_agent.md` for detailed capabilities and usage examples.

### Skills in Markdown Agents

You can easily configure skills for your markdown agents using the `skills_path` and `skill_names` fields:

**Example 1: Basic Skills Configuration**
```markdown
---
name: MyAgent
description: An agent with PDF and Excel skills
skill_names: pdf;excel
---
```

**Example 2: Skills from Multiple Sources**
```markdown
---
name: MyAgent
description: An agent with skills from local and GitHub sources
skills_path: ../skills;https://github.com/user/repo
skill_names: pdf;excel;browser
---
```

**Example 3: Using Regex Patterns**
```markdown
---
name: MyAgent
description: An agent with skills matched by regex patterns
skill_names: pdf;regex:^context-.*;regex:.*browser.*
---
```
This will:
- Load the exact skill named `pdf`
- Load all skills starting with `context-` (e.g., `context-fundamentals`, `context-advanced`)
- Load all skills containing `browser` (e.g., `browser-automation`, `web-browser`)

**Skills are automatically registered from:**
- `./skills` directory (if exists, registered automatically)
- `../skills` directory relative to markdown file (if exists, registered automatically)
- Sources specified in `skills_path` field
- Sources specified in `SKILLS_PATH` environment variable
- Command-line arguments: `aworld-cli --skill-path ../skills --skill-path https://github.com/user/repo`

## Configuration

### Environment Variables

Configure the following variables in the `.env` file:

- `LLM_MODEL_NAME`: LLM model name (e.g., gpt-4, gpt-3.5-turbo)
- `LLM_PROVIDER`: LLM provider (e.g., openai)
- `LLM_API_KEY`: API key
- `LLM_BASE_URL`: API base URL
- `LLM_TEMPERATURE`: Temperature for LLM (default: 0.7)
- `LOCAL_AGENTS_DIR`: Agent definitions directory (defaults to current directory)
- `TAVILY_API_KEY`: Tavily API key (for web search, optional)

**Skills Configuration (Optional):**
- `SKILLS_PATH`: Skill sources (semicolon-separated): `../skills;https://github.com/user/repo`
- `SKILLS_DIR`: Single skills directory path (legacy, use `SKILLS_PATH` for multiple sources)
- `SKILLS_CACHE_DIR`: Cache directory for GitHub repositories (default: `~/.aworld/skills`)

### Command-Line Options

**Server Mode Options:**
- `serve`: Start HTTP and/or MCP servers
- `--http`: Enable HTTP server
- `--http-host HOST`: HTTP server host (default: 0.0.0.0)
- `--http-port PORT`: HTTP server port (default: 8000)
- `--mcp`: Enable MCP server
- `--mcp-name NAME`: MCP server name (default: AWorldAgent)
- `--mcp-transport TYPE`: MCP transport (stdio/sse/streamable-http, default: stdio)
- `--mcp-host HOST`: MCP server host for SSE/streamable-http (default: 0.0.0.0)
- `--mcp-port PORT`: MCP server port for SSE/streamable-http (default: 8001)

**Agent Loading Options:**
- `--agent-dir DIR`: Agent directory (can be specified multiple times)
- `--agent-file FILE`: Individual agent file (can be specified multiple times)
- `--remote-backend URL`: Remote backend URL (can be specified multiple times)
- `--skill-path PATH`: Skill source path (can be specified multiple times)

### Using aworld-cli

1. **Interactive Mode**: Run `aworld-cli` to display available agents, select one, and start a conversation
![/](../../readme_assets/aworld_cli_02.jpg)

2. **Server Mode**: Start HTTP and/or MCP servers to expose agents via API

#### HTTP Server

Start an HTTP server to expose agents via REST API (OpenAI-compatible):

```bash
# Start HTTP server on default port 8000
aworld-cli serve --http

# Start HTTP server on custom port
aworld-cli serve --http --http-port 8080

# Start HTTP server with custom agent directory
aworld-cli serve --http --agent-dir ./agents
```

Once started, the HTTP server provides:
- **OpenAI-compatible API**: `/v1/chat/completions` and `/chat/completions`
- **Agent listing**: `GET /agents` - List all available agents
- **Health check**: `GET /health` - Server health status

**Example API Usage:**
```bash
# List available agents
curl http://localhost:8000/agents

# Chat completion (OpenAI format)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "BasicAgent",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

#### MCP Server

Start an MCP (Model Context Protocol) server to expose agents via MCP protocol:

```bash
# Start MCP server in stdio mode (for CLI usage)
aworld-cli serve --mcp

# Start MCP server in streamable-http mode (for HTTP clients)
aworld-cli serve --mcp --mcp-transport streamable-http --mcp-port 8001

# Start MCP server with custom name
aworld-cli serve --mcp --mcp-name MyAgentServer
```

**MCP Transport Modes:**
- **stdio**: Standard input/output mode (for CLI and direct integration)
- **sse**: Server-Sent Events mode (HTTP+SSE)
- **streamable-http**: Streamable HTTP mode (compatible with MCP streamable-http clients)

**MCP Tools Available:**
- `list_agents`: List all available agents
- `get_agent_info`: Get information about a specific agent
- `run_task`: Run a task directly with an agent
- `health_check`: Health check endpoint

#### Combined Servers

Start both HTTP and MCP servers simultaneously:

```bash
# Start both HTTP and MCP servers
aworld-cli serve --http --http-port 8000 --mcp --mcp-transport streamable-http --mcp-port 8001

# With custom agent directory
aworld-cli serve --http --mcp --agent-dir ./agents
```

**Server Options:**
- `--http`: Start HTTP server
- `--http-host HOST`: HTTP server host (default: 0.0.0.0)
- `--http-port PORT`: HTTP server port (default: 8000)
- `--mcp`: Start MCP server
- `--mcp-name NAME`: MCP server name (default: AWorldAgent)
- `--mcp-transport TYPE`: MCP transport type: stdio, sse, or streamable-http (default: stdio)
- `--mcp-host HOST`: MCP server host for SSE/streamable-http (default: 0.0.0.0)
- `--mcp-port PORT`: MCP server port for SSE/streamable-http (default: 8001)

Press `Ctrl+C` to stop all servers gracefully.

## Agent Types Comparison

| Feature | Python Agents | Markdown Agents |
|---------|--------------|-----------------|
| **Definition Format** | Python code with `@agent` decorator | YAML front matter + Markdown |
| **Complexity** | More flexible, requires Python knowledge | Simpler, no coding required |
| **MCP Integration** | ✅ Full support | ✅ Full support |
| **Multi-Agent** | ✅ Full support (Swarm, TeamSwarm) | ✅ Supported via configuration |
| **Skills** | ✅ Full support | ✅ Full support (with regex pattern matching) |
| **Best For** | Complex logic, custom behavior | Quick prototyping, documentation-focused agents |

## More Examples

### In This Directory
- **Python Agents**: `simple_agent.py`, `skill_agent.py`, `pe_team_agent.py`
- **Markdown Agents**: `document_agent.md`

### Other Examples
Refer to `examples/skill_agent` for more complex Agent definitions, including:
- Multi-agent collaboration (Swarm)
- Skill system
- Advanced MCP tool integration
