# TeamSwarm Implementation Template

Complete template for creating a centralized Multi-Agent System with orchestrator coordination.

## Directory Structure

```
team_name/
├── __init__.py
├── agents/
│   ├── __init__.py
│   ├── swarm.py
│   ├── orchestrator_agent/
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── config.py
│   │   └── prompt.py
│   ├── worker_agent_1/
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── config.py
│   │   └── prompt.py
│   └── worker_agent_2/
│       ├── __init__.py
│       ├── agent.py
│       ├── config.py
│       └── prompt.py
└── mcp/ (optional)
    ├── __init__.py
    └── mcp_config.py
```

## 1. Orchestrator Agent

### orchestrator_agent/config.py

```python
import os
from aworld.config import AgentConfig, ModelConfig

orchestrator_agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.1,  # Lower temperature for coordination tasks
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL")
    ),
    use_vision=False
)
```

### orchestrator_agent/prompt.py

```python
orchestrator_agent_system_prompt = """
You are an orchestrator agent responsible for coordinating tasks and delegating work to specialized agents.

## Core Responsibilities:
1. **Task Analysis**: Analyze incoming tasks and break them down into subtasks
2. **Agent Selection**: Choose appropriate worker agents based on task requirements
3. **Coordination**: Manage workflow and ensure proper handoffs between agents
4. **Result Synthesis**: Aggregate results from worker agents and provide final output

## Workflow:
1. Receive and analyze the task
2. Determine which agents are needed
3. Delegate subtasks to appropriate agents
4. Wait for agent responses
5. Synthesize results and provide final answer

## Agent Selection Guidelines:
- Use worker_agent_1 for [describe use case]
- Use worker_agent_2 for [describe use case]
- Coordinate multiple agents when task requires parallel or sequential processing

## Output Format:
- Clearly indicate which agent you're delegating to
- Provide context and requirements for delegated tasks
- Synthesize responses from multiple agents when applicable
"""
```

### orchestrator_agent/agent.py

```python
from typing import Dict, Any, List
from aworld.agents.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger


class OrchestratorAgent(Agent):
    """
    Orchestrator agent that coordinates tasks and delegates to worker agents.
    
    Example:
        >>> orchestrator = OrchestratorAgent(
        ...     name="orchestrator",
        ...     desc="Coordinates tasks",
        ...     conf=orchestrator_agent_config,
        ...     system_prompt=orchestrator_agent_system_prompt
        ... )
    """
    
    max_loop = 50  # Maximum number of coordination loops

    async def async_policy(
        self, 
        observation: Observation, 
        info: Dict[str, Any] = {}, 
        message: Message = None,
        **kwargs
    ) -> List[ActionModel]:
        """
        Policy function that handles task coordination.
        
        Args:
            observation: Current observation/state
            info: Additional information dictionary
            message: Message from other agents
            **kwargs: Additional keyword arguments
            
        Returns:
            List of action models representing agent decisions
        """
        action_model_list = await super().async_policy(observation, info, message, **kwargs)
        
        if self._finished:
            logger.info(f"[OrchestratorAgent] Task completed: {action_model_list[0].policy_info}")
            
        if self._finished and not action_model_list[0].policy_info:
            action_model_list[0].policy_info += "\n\n" + observation.content
            
        return action_model_list

    async def should_terminate_loop(self, message: Message) -> bool:
        """
        Determine if the coordination loop should terminate.
        
        Args:
            message: Current message context
            
        Returns:
            True if loop should terminate, False otherwise
        """
        return self.loop_step >= self.max_loop
```

## 2. Worker Agent

### worker_agent_1/config.py

```python
import os
from aworld.config import AgentConfig, ModelConfig

worker_agent_1_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.6,  # Higher temperature for creative tasks
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL")
    ),
    use_vision=False
)

# Optional: MCP servers for this agent
worker_agent_1_mcp_servers = ["mcp-server-1", "mcp-server-2"]
```

### worker_agent_1/prompt.py

```python
worker_agent_1_system_prompt = """
You are a specialized worker agent focused on [specific domain/task].

## Your Responsibilities:
- [Specific task responsibility 1]
- [Specific task responsibility 2]
- [Specific task responsibility 3]

## Workflow:
1. Receive task from orchestrator
2. Execute specialized task
3. Return results to orchestrator

## Guidelines:
- Focus only on your area of expertise
- Provide clear, actionable results
- Ask for clarification if task is outside your scope
"""
```

### worker_agent_1/agent.py

```python
from aworld.agents.llm_agent import Agent
from .config import worker_agent_1_config
from .prompt import worker_agent_1_system_prompt

# Simple worker agent using base Agent class
# If you need custom behavior, extend Agent class similar to OrchestratorAgent
```

## 3. Swarm Initialization

### agents/swarm.py

```python
from aworld.core.agent.swarm import TeamSwarm
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.config import ContextEnvConfig, AmniConfigFactory, AmniConfigLevel
from aworldappinfra.core.registry import agent_team
from aworldappinfra.ui.ui_template import build_markdown_ui
from .orchestrator_agent.agent import OrchestratorAgent
from .orchestrator_agent.config import orchestrator_agent_config
from .orchestrator_agent.prompt import orchestrator_agent_system_prompt
from .worker_agent_1.agent import WorkerAgent1
from .worker_agent_1.config import worker_agent_1_config, worker_agent_1_mcp_servers
from .worker_agent_1.prompt import worker_agent_1_system_prompt
from .worker_agent_2.agent import WorkerAgent2
from .worker_agent_2.config import worker_agent_2_config
from .worker_agent_2.prompt import worker_agent_2_system_prompt
# Import MCP config if needed
# from ..mcp.mcp_config import build_mcp_config


@agent_team(
    name="MyTeam",
    desc="Team description for complex coordination tasks",
    context_config=AmniConfigFactory.create(
        AmniConfigLevel.NAVIGATOR,
        debug_mode=True,
        env_config=ContextEnvConfig(
            env_type="local",  # or "remote"
            enabled_file_share=False,
        )
    ),
    ui=build_markdown_ui,
    metadata={
        "version": "1.0.0",
        "creator": "your-name",
        "create_time": "2025-01-01"
    }
)
async def build_swarm(context: ApplicationContext) -> TeamSwarm:
    """
    Build and return the TeamSwarm instance.
    
    Args:
        context: Application context for configuration
        
    Returns:
        Configured TeamSwarm instance
    """
    # Create orchestrator agent
    orchestrator_agent = OrchestratorAgent(
        name="orchestrator_agent",
        desc="Orchestrator agent for task coordination",
        conf=orchestrator_agent_config,
        system_prompt=orchestrator_agent_system_prompt,
    )

    # Create worker agents
    worker_agent_1 = WorkerAgent1(
        name="worker_agent_1",
        desc="Worker agent 1 description",
        conf=worker_agent_1_config,
        system_prompt=worker_agent_1_system_prompt,
        mcp_servers=worker_agent_1_mcp_servers,
        # mcp_config=await build_mcp_config(context),  # If using MCP
    )

    worker_agent_2 = WorkerAgent2(
        name="worker_agent_2",
        desc="Worker agent 2 description",
        conf=worker_agent_2_config,
        system_prompt=worker_agent_2_system_prompt,
    )

    # Return TeamSwarm with orchestrator as first agent (leader)
    return TeamSwarm(
        orchestrator_agent, 
        worker_agent_1, 
        worker_agent_2, 
        max_steps=30  # Maximum execution steps
    )
```

## 4. Team Registration

The `@agent_team` decorator automatically registers the team. Make sure the `swarm.py` file is imported in your application's agent discovery mechanism.

## Key Differences from Swarm

1. **First Agent is Leader**: In TeamSwarm, the first agent passed to the constructor is the orchestrator/leader
2. **Orchestrator Coordinates**: The orchestrator agent decides when and how to use worker agents
3. **Dynamic Routing**: Worker agents are activated based on orchestrator's decisions
4. **Custom Orchestrator Logic**: Often extends Agent class to add coordination-specific logic

## Example: XbenchTeam Reference

See real-world example at:
- `xbench_team/agents/swarm.py` - TeamSwarm initialization
- `xbench_team/agents/orchestrator_agent/` - Orchestrator implementation
- `xbench_team/agents/coding_agent/` - Worker agent example

