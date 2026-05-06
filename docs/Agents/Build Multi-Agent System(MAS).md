# Build Multi-Agent System (MAS)

In AWorld, a multi-agent system is built from the same core `Agent` and `Swarm` primitives used elsewhere in the framework. The difference from a fixed workflow is that MAS routing can stay dynamic at runtime instead of being fully pre-defined up front.

## When To Use MAS

Use a MAS when you want:

- dynamic handoff between agents
- runtime decisions about which agent should act next
- selective or repeated calls between cooperating agents
- nested teams that mix deterministic workflow steps with agent-driven delegation

Use a fixed workflow when the step order is stable and should not change at runtime.

## Basic Example

```python
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.agent.graph_build_type import GraphBuildType
from aworld.runner import Runners

agent_conf = AgentConfig(...)
agent1 = Agent(name="planner", conf=agent_conf)
agent2 = Agent(name="researcher", conf=agent_conf)
agent3 = Agent(name="writer", conf=agent_conf)

swarm = Swarm(
    topology=[(agent1, agent2), (agent2, agent3), (agent1, agent3)],
    build_type=GraphBuildType.HANDOFF,
)

Runners.run(input="Create a market summary", swarm=swarm)
```

## Entry Agent

If multiple agents can receive external input, set `root_agent` explicitly:

```python
swarm = Swarm(
    topology=[(agent1, agent2), (agent2, agent3), (agent1, agent3)],
    build_type=GraphBuildType.HANDOFF,
    root_agent=[agent1],
)
```

## Custom Routing

For business-specific routing rules, register a custom handler and point the agent at it:

```python
agent = Agent(..., event_handler_name="your_handler_name")
```

That handler can inspect messages, enforce domain constraints, and decide what should happen next in the swarm.

## Composition

MAS and workflows can be nested. A common pattern is:

1. Use a workflow for deterministic outer stages such as rewrite -> review -> publish.
2. Use a MAS inside one of those stages for open-ended research, planning, or synthesis.

This keeps the overall system predictable while still allowing dynamic collaboration where it matters.
