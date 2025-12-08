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

# List all available agents
aworld-cli list
```

### 3. Debug Mode

If you want to debug `aworld-cli`'s main function in an IDE, you can use `debug_main.py`:

```bash
# Debug interactive mode
python debug_main.py

# Debug list command
python debug_main.py list
```

In an IDE (e.g., PyCharm):
1. Right-click on `debug_main.py`
2. Select "Debug 'debug_main'"
3. You can set breakpoints in `main.py` for debugging

## Project Structure

```
aworld_cli_demo/
├── README.md           # This file
├── env.template        # Environment variable template
├── debug_main.py       # Debug script (for debugging main.py)
├── agents/             # Agent definitions directory
│   ├── __init__.py     # Python package initialization file
│   ├── simple_agent.py # Basic Agent definition (BasicAgent)
│   ├── skill_agent.py  # Skill-enabled Agent definition (SkillAgent)
│   └── pe_team_agent.py # PE Pattern Multi-Agent System (PE Team Agent)
└── .env                # Environment variable configuration (needs to be created)
```

## Agent Definitions

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

### Using aworld-cli

1. **Interactive Mode**: Run `aworld-cli` to display available agents, select one, and start a conversation
2. **List Mode**: Run `aworld-cli list` to view all available agents

## More Examples

Refer to `examples/skill_agent` for more complex Agent definitions, including:
- Multi-agent collaboration (Swarm)
- Skill system
- MCP tool integration
