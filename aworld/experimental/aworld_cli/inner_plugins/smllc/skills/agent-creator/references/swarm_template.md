# Swarm Implementation Template

Complete template for creating a decentralized Multi-Agent System with workflow execution.

## Directory Structure

```
swarm_name/
├── __init__.py
├── agents/
│   ├── __init__.py
│   ├── swarm.py
│   ├── agent1/
│   │   └── agent.py (optional, if agent needs custom implementation)
│   └── agent2/
│       └── agent.py (optional)
```

Note: For simple Swarm implementations, agents can be defined directly in `swarm.py` using the base `Agent` class.

## 1. Shared Agent Configuration

### agents/config.py

```python
import os
from aworld.config import AgentConfig, ModelConfig

agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.6,
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL"),
        params={"max_completion_tokens": 40960}
    ),
    use_vision=False
)
```

## 2. Agent Prompts

### agents/prompt.py

```python
# Analysis Agent Prompt
analysis_prompt = """
# Role Description
You are a professional analysis agent specializing in [domain].

## Core Capabilities:
- Analyze and understand user requirements
- Extract key information and constraints
- Identify task objectives and success criteria

## Task Instructions:
Your task is to analyze the user input: {{input}}, and generate a comprehensive analysis report including:
- Key requirements identification
- Constraints and limitations
- Success criteria definition
- Output format requirements

Output the analysis report in a structured format for the next agent.
"""

# Planning Agent Prompt
planning_prompt = """
# Role Description
You are a professional planning agent specializing in [domain].

## Core Capabilities:
- Create structured plans based on analysis
- Break down tasks into actionable steps
- Define execution sequence and dependencies

## Task Instructions:
Based on the analysis report {{report}} from the previous agent, create a detailed execution plan:
- Task breakdown into steps
- Execution sequence
- Dependencies between steps
- Resource requirements

Output the plan in a structured format for the next agent.
"""

# Execution Agent Prompt
execution_prompt = """
# Role Description
You are a professional execution agent specializing in [domain].

## Core Capabilities:
- Execute plans and generate content
- Apply domain-specific knowledge
- Produce high-quality outputs

## Task Instructions:
Based on the plan {{plan}} from the previous agent, execute the task and generate the required output:
- Follow the plan structure
- Apply best practices
- Ensure quality and completeness

Output the final result in the specified format.
"""

# Optional: Additional agent prompts
formatting_prompt = """
# Role Description
You are a formatting agent responsible for final output formatting.

## Task Instructions:
Format the content {{content}} according to the specified requirements:
- Apply proper structure and formatting
- Ensure consistency and readability
- Validate output format compliance

Output the final formatted result.
"""
```

## 3. Swarm Initialization

### agents/swarm.py

```python
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import ApplicationContext, AmniConfigFactory
from aworldappinfra.core.registry import agent_team
from .config import agent_config
from .prompt import (
    analysis_prompt,
    planning_prompt,
    execution_prompt,
    formatting_prompt  # if needed
)


@agent_team(
    name="MySwarm",
    desc="Swarm description for sequential workflow tasks",
    context_config=AmniConfigFactory.create(
        debug_mode=True,
        neuron_names=["task"]  # Optional: specify context neurons
    ),
    metadata={
        "version": "1.0.0",
        "creator": "your-name",
        "create_time": "2025-01-01"
    }
)
async def build_swarm(context: ApplicationContext) -> Swarm:
    """
    Build and return the Swarm instance.
    
    Args:
        context: Application context for configuration
        
    Returns:
        Configured Swarm instance with workflow agents
    """
    
    # Analysis Agent - First in sequence
    analysis_agent = Agent(
        name="analysis_agent",
        desc="Analyzes user requirements and extracts key information",
        conf=agent_config,
        system_prompt=analysis_prompt,
    )

    # Planning Agent - Second in sequence
    planning_agent = Agent(
        name="planning_agent",
        desc="Creates structured plans based on analysis",
        conf=agent_config,
        system_prompt=planning_prompt,
    )

    # Execution Agent - Third in sequence
    execution_agent = Agent(
        name="execution_agent",
        desc="Executes plans and generates content",
        conf=agent_config,
        system_prompt=execution_prompt,
    )

    # Optional: Formatting Agent - Final in sequence
    formatting_agent = Agent(
        name="formatting_agent",
        desc="Formats the final output",
        conf=agent_config,
        system_prompt=formatting_prompt,
        mcp_servers=["filesystem-server"],  # Optional: if file operations needed
        mcp_config={
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
    )

    # Return Swarm with agents in execution order
    # Agents execute sequentially: analysis → planning → execution → formatting
    return Swarm(
        analysis_agent, 
        planning_agent, 
        execution_agent, 
        formatting_agent,  # optional
        max_steps=30  # Maximum execution steps
    )
```

## 4. Agent Communication Pattern

In Swarm workflow, agents communicate through the observation/content:

1. **Analysis Agent** receives user input → outputs analysis report
2. **Planning Agent** receives analysis report → outputs execution plan
3. **Execution Agent** receives execution plan → outputs content
4. **Formatting Agent** receives content → outputs formatted result

Each agent's output becomes the input for the next agent in the sequence.

## Key Differences from TeamSwarm

1. **No Orchestrator**: All agents are equal and execute in sequence
2. **Fixed Order**: Agent execution order is determined by the order passed to Swarm constructor
3. **Workflow Pattern**: Data flows linearly through agents
4. **Simplicity**: Easier to understand and debug for straightforward tasks

## Example: PPTTeam Reference

See real-world example at:
- `ppt_team/agents/swarm.py` - Swarm initialization
- `ppt_team/agents/prompt.py` - Agent prompt definitions
- `ppt_team/agents/config.py` - Shared configuration

## Advanced: Custom Agent Classes

If you need custom behavior for specific agents, create custom agent classes:

### agents/custom_agent/agent.py

```python
from typing import Dict, Any, List
from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message


class CustomAgent(Agent):
    """
    Custom agent with specialized behavior.
    
    Example:
        >>> agent = CustomAgent(
        ...     name="custom_agent",
        ...     desc="Custom agent description",
        ...     conf=agent_config,
        ...     system_prompt=custom_prompt
        ... )
    """
    
    async def async_policy(
        self, 
        observation: Observation, 
        info: Dict[str, Any] = {}, 
        message: Message = None,
        **kwargs
    ) -> List[ActionModel]:
        """
        Custom policy implementation.
        
        Args:
            observation: Current observation/state
            info: Additional information dictionary
            message: Message from other agents
            **kwargs: Additional keyword arguments
            
        Returns:
            List of action models
        """
        # Pre-process observation if needed
        # ... custom logic ...
        
        # Call parent policy
        action_model_list = await super().async_policy(observation, info, message, **kwargs)
        
        # Post-process actions if needed
        # ... custom logic ...
        
        return action_model_list
```

Then use it in `swarm.py`:

```python
from .custom_agent.agent import CustomAgent

custom_agent = CustomAgent(
    name="custom_agent",
    desc="Custom agent description",
    conf=agent_config,
    system_prompt=custom_prompt,
)
```

## MCP Server Configuration

To add MCP servers to agents:

```python
agent = Agent(
    name="agent_name",
    desc="Agent description",
    conf=agent_config,
    system_prompt=agent_prompt,
    mcp_servers=["mcp-server-1", "mcp-server-2"],
    mcp_config={
        "mcpServers": {
            "mcp-server-1": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-example"]
            }
        }
    }
)
```

