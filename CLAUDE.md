# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workspace Context

**This workspace (`aworld-mas/aworld`) is specifically for improving the MAS (multi-agent system) framework layer.** Code modifications should focus primarily on the frameworks layer:

- **Target Area**: `aworld/core/agent/` - Multi-agent orchestration, swarm topologies, agent communication
- **Related Areas**: `aworld/core/tool/`, `aworld/core/context/`, `aworld/memory/` - Supporting infrastructure
- **Validation**: Changes must be validated through GAIA/XBench benchmarks (Benchmark-Driven Development)

When modifying multi-agent architecture, ensure compatibility with existing swarm topologies (Workflow, Handoff, Team, Hybrid) and maintain backward compatibility with single-agent scenarios.

## Project Overview

AWorld is a **multi-agent framework and harness** designed to orchestrate AI agents, tools, memory, context, and execution. Built on multi-agent architecture as its core strength while supporting single-agent scenarios.

**Architectural Positioning: Agent Harness Layer**

```
Raw Code → Agent Frameworks → Agent Harness ← AWorld is here
(基础库)   (运行时/框架层)      (驾驭层)
```

**Key Distinction:**
- **Agent Framework** (e.g., LangChain/LangGraph): For people who want to **build** agents
  - Modular components, many decisions required
  - User chooses memory system, tools, orchestration logic
- **Agent Harness** (e.g., AWorld/DeepAgent): For people who want to **use** agents
  - Pre-configured, minimal setup
  - Built-in memory, context, agent loop, safety checks
  - Add API keys, configure tools, ready to run

**AWorld as Harness:**
- Pre-configured: Agent orchestration, tool integration, memory/context, execution control, skills system
- What you configure: API keys, model selection, specific tools, task descriptions
- What's pre-configured: Memory strategies, context propagation, agent loops, tool orchestration, safety

**Core Capabilities:**
- Multi-agent orchestration (Star, Tree, Mesh, Ring, Hybrid topologies)
- Multiple execution modes: YOLO (quick), Local (persistent), Services (production)
- Flexible tool integration: Local tools, MCP tools, PTC (Programmatic Tool Calling)
- Memory and context management
- Skills system for domain expertise
- CAST (Code Abstract Syntax Tree) for code analysis
- Meta-learning foundations (checkpoint, trajectory, reflection - experimental)

**Framework Versatility:**
- Code agents (built-in Aworld agent)
- Information retrieval agents (GAIA, XBench)
- Specialized agents (video/audio generation, evaluation)

## Development Principles: Benchmark-Driven Development (BDD)

**Core Philosophy:** Every architectural improvement must be validated through real-world agent benchmarks, not just unit tests.

**Traditional TDD:** Write test → Implement → Test passes ✓  
**AWorld BDD:** Benchmark baseline → Improve harness → Agent solves more benchmark tasks ✓

**Primary Benchmarks:**
1. **GAIA** (`examples/gaia/`): Information retrieval, multi-modal reasoning
   - Current: Pass@1: 67.89%, Pass@3: 83.49% (109 tasks)
   - Run: `cd examples/gaia && python run.py --split validation --start 0 --end 50`

2. **XBench** (`examples/xbench/`): Multi-agent web search & reasoning
   - Current: Pass@1: 51%, Pass@3: 61%
   - Run: `cd examples/xbench && python eval.py`

3. **SWE-bench** (TBD): Software engineering tasks

**BDD Workflow:**
1. Identify improvement → Measure baseline
2. Implement harness change
3. Integrate into built-in agent
4. Validate on benchmarks → Compare results
5. Document performance impact in commit message

**Commit Message Format:**
```
Short description

Benchmark validation:
- GAIA: X% → Y% (±Z%)
- Tasks affected: [list]
- Root cause: [explanation]

Test: cd examples/gaia && python run.py --split validation --start 0 --end 20
```

## Installation & Setup

```bash
# Clone and setup
git clone https://github.com/inclusionAI/AWorld && cd AWorld
conda create -n aworld_env python=3.11 -y && conda activate aworld_env

# Install framework + CLI
pip install -e . && cd aworld-cli && pip install -e .

# Configure (creates .env in working directory)
aworld-cli --config
```

**Environment Configuration (.env):**
```bash
LLM_MODEL_NAME="your_model_name"  # Claude-Sonnet-4 or gpt-4o
LLM_PROVIDER="openai"             # or "anthropic"
LLM_API_KEY="your_api_key"
LLM_BASE_URL="your_base_url"
```

## Architecture Overview

**Core Concepts:**
- **Agent vs Swarm**: Individual agent vs topological multi-agent structure
- **Tool vs MCP**: General tool interface vs Model Context Protocol standard
- **Task vs Runner**: Task definition vs task executor
- **Memory vs Context**: Operation history vs fine-grained contextual information

**Directory Structure (Key Components):**
```
aworld/                    # Framework + runtime (core harness)
├── core/                  # Agent, tool, context, memory abstractions
│   ├── agent/            # Base agent, swarm orchestration (MAS FOCUS)
│   ├── tool/             # Tool abstractions and factories
│   ├── context/          # Context management
│   └── memory.py         # Memory system
├── agents/               # Pre-built agents
├── tools/                # Built-in tools
├── sandbox/              # **Tool execution abstraction layer**
│   └── script/           # Tool server startup scripts
├── checkpoint/           # State snapshot management (experimental)
├── dataset/              # Trajectory management (experimental)
└── evaluations/reflect/  # Reflection system (experimental)

aworld-cli/               # CLI execution layer (user interface)
├── src/aworld_cli/
│   ├── main.py          # Entry point
│   ├── console.py       # Interactive terminal
│   ├── core/            # Command system, agent registry
│   │   ├── command_system.py  # Slash command framework
│   │   └── agent_registry.py  # Agent discovery
│   └── commands/        # Built-in slash commands
│       ├── help_cmd.py  # /help command
│       ├── commit.py    # /commit command
│       ├── review.py    # /review command
│       └── diff.py      # /diff command
└── inner_plugins/smllc/ # Built-in Aworld agent
    ├── agents/
    │   ├── aworld_agent.py     # Main code agent
    │   ├── developer/          # CAST-based coding
    │   ├── evaluator/          # App/code evaluation
    │   ├── diffusion/          # Video generation
    │   └── audio/              # Audio generation
    └── skills/                 # Built-in skills

aworld-skills/            # Skills hub (dynamic loading)
examples/                 # Usage examples + production agents
├── gaia/                # Production GAIA agent
├── xbench/              # Production XBench multi-agent
└── aworld_quick_start/  # Tutorials
```

**Swarm Topologies:**
- **Workflow**: Deterministic sequential/parallel execution
- **Handoff**: AI-driven dynamic agent delegation
- **Team**: Leader-follower pattern (root coordinates executors)
- **Debate**: Collaborative reasoning (TODO)
- **Hybrid**: Nested topologies for complex systems

## Built-in AWorld Agent (Code Agent)

The default `Aworld` agent is a **code agent** - central coordinator similar to Claude Code capabilities.

**Architecture:** TeamSwarm with specialized sub-agents

**Core Sub-Agents:**
1. **Developer** (`developer/`): Code analysis, development, modification
   - CAST tools: `CAST_ANALYSIS`, `CAST_CODER`, `CAST_SEARCH`
   - Operating modes: Green-field, Brown-field, Code exploration

2. **Evaluator** (`evaluator/`): App/code quality evaluation
   - Provides improvement suggestions
   - Works in evolution loop with Developer

3. **Diffusion** (`diffusion/`): Video generation from images/audio/text

4. **Audio** (`audio/`): Text-to-speech audio generation

**Evolution Loop:** Build → Evaluate → Evolve (iterative improvement until quality threshold met)

**Key Built-in Tools:**

**CAST Tools** (Code Abstract Syntax Tree):
- `CAST_ANALYSIS`: Deep codebase analysis (structure, complexity, performance, security)
- `CAST_CODER`: Automated code modification/creation with snapshots and rollback
- `CAST_SEARCH`: AST-based code search

**MCP Tools:**
- `terminal`: Execute shell commands (path restricted to working directory)
- `ms-playwright`: Browser automation and testing
- `CONTEXT_TOOL`: Access conversation context and memory

**Filesystem Tools** (via sandbox builtin):
- `read_file`, `write_file`, `edit_file`, `search_content`
- `list_directory`, `create_directory`, `move_file`, `parse_file`

**Git Tools** (native Python implementations):
- `git_status`, `git_diff`, `git_log`, `git_commit`, `git_blame`

**Glob Tool:**
- `glob`: Fast file pattern matching (e.g., `**/*.py`)

## Slash Command System

**NEW:** Claude Code-style slash commands for efficient high-frequency operations.

**Command Types:**
1. **Tool Commands**: Direct execution without agent (e.g., `/help`)
2. **Prompt Commands**: Generate prompts for agent to execute (e.g., `/commit`, `/review`)

**Available Commands:**
- `/help` - List available commands (tool command)
- `/commit` - Smart git commit with analysis (prompt command)
- `/review` - Code review assistant (prompt command)
- `/diff [ref]` - Summarize changes vs ref (prompt command)

**Usage in Interactive Mode:**
```bash
aworld-cli
> /help          # Show available commands
> /commit        # Intelligent git commit
> /review        # Review current changes
> /diff main     # Summarize changes vs main branch
```

**Command Flow:**
```
User: /command args
  ↓
CLI detects "/" prefix (console.py)
  ↓
CommandRegistry routes to command
  ↓
[Tool Command]              [Prompt Command]
     ↓                           ↓
Command.execute()          Command.get_prompt()
     ↓                           ↓
Direct result              Agent → Tools → Result
```

**Creating Custom Commands:**
```python
from aworld_cli.core.command_system import Command, CommandContext, register_command

@register_command
class MyCommand(Command):
    @property
    def name(self) -> str:
        return "mycommand"
    
    @property
    def description(self) -> str:
        return "Does something useful"
    
    @property
    def command_type(self) -> str:
        return "prompt"  # or "tool"
    
    @property
    def allowed_tools(self) -> List[str]:
        return ["terminal:git*", "git_status"]  # Tool whitelist
    
    async def get_prompt(self, context: CommandContext) -> str:
        # Gather context and generate prompt
        return "Prompt for agent..."
```

Place in directory specified by `LOCAL_AGENTS_DIR` environment variable.

## Common Development Commands

**Execution Modes:**
```bash
# YOLO Mode (quick execution)
aworld-cli --task "Create a React todo app"

# Local Mode (interactive with state)
aworld-cli

# Services Mode (production deployment)
aworld web --host 0.0.0.0 --port 8000
```

**CLI Commands:**
```bash
aworld-cli                    # Interactive mode
aworld-cli list               # List available agents
aworld-cli --task "..." --agent Aworld  # Execute task directly
aworld-cli --agent-dir ./my_agents      # Use custom agents
```

**Benchmark Validation (Primary Testing):**
```bash
# GAIA benchmark
cd examples/gaia
python run.py --split validation --start 0 --end 50

# XBench benchmark
cd examples/xbench
python eval.py

# Quick validation (10 tasks)
cd examples/gaia
python run.py --split validation --start 0 --end 10
```

**Traditional Unit Tests (Supporting):**
```bash
python -m pytest tests/                    # All tests
python -m pytest tests/core/              # Core module
python -m pytest tests/core/test_agent/   # Agent tests
python -m pytest --cov=aworld            # With coverage
```

**Production Agents:**
```bash
# GAIA agent with Web UI
cd examples/gaia/cmd && aworld web

# XBench evaluation
cd examples/xbench && python eval.py
```

## Development Patterns

**Creating Single Agent:**
```python
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent

agent = Agent(
    conf=AgentConfig(llm_provider="openai", llm_model_name="gpt-4o"),
    name="my_agent",
    system_prompt="You are a helpful agent.",
    tool_names=["search_api", "calculator"]
)
```

**Creating Multi-Agent Swarm:**
```python
from aworld.core.agent.swarm import Swarm, GraphBuildType
# Or use convenient aliases:
from aworld.core.agent.swarm import WorkflowSwarm, HandoffSwarm, TeamSwarm, HybridSwarm

# Workflow (deterministic)
swarm = Swarm(agent1, [agent2, agent3], agent4)
# Or: swarm = WorkflowSwarm(agent1, [agent2, agent3], agent4)

# Handoff (AI-driven)
swarm = Swarm((agent1, agent2), (agent1, agent3), build_type=GraphBuildType.HANDOFF)
# Or: swarm = HandoffSwarm((agent1, agent2), (agent1, agent3))

# Team (leader-follower)
swarm = Swarm(leader, executor1, executor2, build_type=GraphBuildType.TEAM)
# Or: swarm = TeamSwarm(leader, executor1, executor2)

# Hybrid (centralized + peer-to-peer)
swarm = Swarm(coordinator, worker1, worker2, build_type=GraphBuildType.HYBRID)
# Or: swarm = HybridSwarm(coordinator, worker1, worker2)
```

**Creating Custom Tools:**
```python
from aworld.core.tool.func_to_tool import be_tool
from pydantic import Field

@be_tool(tool_name='my_tool', tool_desc="Tool description")
def my_function(param: str = Field(description="Parameter")) -> str:
    return f"Result: {param}"
```

**Running Tasks:**
```python
from aworld.runner import Runners

# Synchronous
result = Runners.sync_run(input="Your task", swarm=swarm)

# Asynchronous
result = await Runners.async_run(input="Your task", swarm=swarm)
```

**Working with Sandbox (Tool Abstraction):**
```python
from aworld.sandbox import create_sandbox

sandbox = create_sandbox(
    sandbox_id="my_sandbox",
    mcp_servers=["server1", "server2"],
    skill_configs=my_skill_configs,
    custom_env_tools=my_tools,
    timeout=300
)

# Start tool servers
./aworld/sandbox/script/start_tool_servers.sh
```

**Working with Checkpoint & Trajectory (Experimental):**
```python
from aworld.checkpoint import create_checkpoint, InMemoryCheckpointRepository
from aworld.dataset import TrajectoryDataset

# Checkpoint
checkpoint = create_checkpoint(values={"agent_state": state}, metadata=metadata)
repo = InMemoryCheckpointRepository()
repo.put(checkpoint)

# Trajectory
trajectory_dataset = TrajectoryDataset(enable_storage=True)
await trajectory_dataset.append_trajectory(message, task_id="task-456")
```

**Registering Custom CLI Agents:**
```python
from aworld_cli.core.agent_registry import agent
from aworld.core.agent.swarm import Swarm

@agent(name="MyAgent", desc="Custom agent description")
def build_my_swarm() -> Swarm:
    return Swarm(my_agent)
```

## Important Notes

### Git Workflow and Remote Operations

**Critical Rule:** Never automatically push or create PRs without explicit human instruction.

**Proper Workflow:**
1. Develop on local branch
2. Commit locally as needed
3. **Validate with benchmarks** (run GAIA/XBench, compare against baseline)
4. Run unit tests if applicable
5. **Clean up temporary files** (see below)
6. **Wait for explicit instruction**: "push to remote" or "create PR"
7. Only then perform remote operations

**Examples:**
- ✅ "Push this branch to origin"
- ✅ "Create a pull request for this feature"
- ❌ Automatic push after commit (NOT allowed)
- ❌ Auto-creating PR without instruction (NOT allowed)

**Exclude Temporary Process Files from PRs:**

When preparing commits or PRs, **never include** temporary files generated during local development:

**Exclude Patterns:**
- `Claude-Sessions/` - Session logs
- `*_FINAL*.md`, `*_COMPLETE*.md`, `*_FIX*.md` - Process docs
- `test_*.py`, `test_*.sh` (in root) - Ad-hoc tests
- `*__tmp_action.py` - Temporary files
- See `.gitignore` for full list

**Before Creating PR:**
1. Review `git status` carefully
2. Only stage **production code changes** (core logic, tests in `tests/`, docs in `docs/`)
3. Exclude all temporary/exploratory files from the commit
4. Use `.gitignore` to prevent accidental commits

**Example Git Commands:**
```bash
# Good: Stage specific production files
git add aworld-cli/src/aworld_cli/core/command_system.py
git add aworld-cli/src/aworld_cli/commands/
git add tests/test_tool_filter.py

# Bad: Stage everything (includes temporary files)
git add .  # ❌ DON'T DO THIS

# Review what will be committed
git diff --staged
```

**Rationale:** Temporary process files clutter the repository history and make code review difficult. Only commit **production-ready code** that provides lasting value to the project.

### Tool Execution & Sandbox

**Current Implementation:** All tools run locally in `aworld_env` (Python 3.11, conda)
- Tools executed through sandbox abstraction layer
- Sandbox provides unified interface regardless of tool location
- No OS-level container isolation at present

**Sandbox Benefits:**
- Consistent interface for all tool types
- Enables future containerization without agent code changes
- Unified error handling and logging

### Environment Variables

- `LOCAL_AGENTS_DIR`: Custom agent directories (semicolon-separated)
- `REMOTE_AGENTS_BACKEND`: Remote agent backend URLs
- `SKILLS_PATH`: Skill sources (local dirs or GitHub URLs)
- `AWORLD_DISABLE_CONSOLE_LOG`: Set to 'true' to disable console logging

### Memory and Context

- **Memory**: Persists agent operation history
- **Context**: Fine-grained execution context
- Both managed automatically but can be customized

### Checkpoint, Trajectory & Meta-Learning

⚠️ **Early Stage:** Implementation may not be fully polished. APIs may evolve.

- **Checkpoint**: Save/restore agent execution states with versioning
- **Trajectory**: Record State-Action-Reward sequences for analysis
- **Reflection**: Analyze execution results and generate insights
- Use as building blocks for custom meta-learning systems

### CAST (Code Abstract Syntax Tree)

- Hierarchical code navigation for agents
- Compresses code context to break context window limits
- Enables surgical code modifications with dependency awareness
- Used internally by developer agents in evolution loop

## Production-Ready Agent Products

**GAIA Agent** (`examples/gaia/`): Complete information retrieval agent
- Performance: Pass@1: 67.89%, Pass@3: 83.49%
- Features: Web UI, CLI, MCP tools, multi-modal capabilities
- See: `examples/gaia/README_GUARD.md`

**XBench System** (`examples/xbench/`): Advanced multi-agent web search
- Performance: Pass@1: 51%, Pass@3: 61%
- Architecture: TeamSwarm + Amni Context, orchestrator pattern
- See: `examples/xbench/README.md`

## Key Configuration Files

- `aworld/requirements.txt` - Framework dependencies
- `aworld/config/*.yaml` - Configuration templates
- `.env` - Local environment configuration
- `aworld-cli/pyproject.toml` - CLI tool dependencies
- `aworld/sandbox/script/start_tool_servers.sh` - Tool server startup
- `examples/gaia/aworld-gaia.yml` - GAIA conda environment
- `examples/xbench/.env_example` - XBench configuration template
