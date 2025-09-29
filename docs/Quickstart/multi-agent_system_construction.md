# Building and Running Multi-Agent Systems (MAS)

In the AWorld framework, similar to Workflow Construction, the fundamental building block for MAS is the Agent. By
introducing the Swarm concept, users can easily, quickly, and efficiently build complex Multi-Agent Systems. In summary:

1. **Workflow in AWorld**: Static, pre-defined execution flows
2. **MAS in AWorld**: Dynamic, real-time decision-making execution flows

This design ensures unified underlying capabilities (i.e., Agent, Graph-based Topology) while maintaining extensibility.

MAS uses event mechanism as the communication basis, where the output from the previous agent serves as the input for
the next agent.

## Quick MAS Construction

Similar to Workflows, we can easily define communication networks between Agents through topology. The key difference is
that by using `build_type=GraphBuildType.HANDOFF`, we allow dynamic decision-making for inter-agent calling
relationships:

1. `agent1` can selectively decide to call `agent2` and `agent3`; the number of calls is also dynamic (once or multiple
   times)
2. `agent2` can selectively decide to call `agent3`; the number of calls is also dynamic (once or multiple times)

```python
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm, GraphBuildType
from aworld.runner import Runners

# Configure agents
agent_conf = AgentConfig(...)
agent1 = Agent(name="agent1", conf=agent_conf)
agent2 = Agent(name="agent2", conf=agent_conf)
agent3 = Agent(name="agent3", conf=agent_conf)

# Create swarm with dynamic handoff topology
swarm = Swarm(
    topology=[(agent1, agent2), (agent2, agent3), (agent1, agent3)],
    build_type=GraphBuildType.HANDOFF
)

# Run the swarm
Runners.run(input="your question", swarm=swarm)
```

### Specifying Entry Agent

Since MAS is essentially a Graph by definition, different Agents can accept external input. We can specify which Agent
receives the query using the `root_agent` parameter.

```python
swarm = Swarm(
    topology=[(agent1, agent2), (agent2, agent3), (agent1, agent3)],
    build_type=GraphBuildType.HANDOFF,
    root_agent=[agent1]
)
```

### Actions Group Parallel

The agent's policy may have multiple actions on a decision, including local tools, MCP tools, and agents.
AWorld will merge these actions of the same type and automatically parallelize them. The results of parallel execution
will be automatically merged and returned to the agent.

If you have a custom policy for mixing different types of actions (tools, agents) in parallel, `GroupMessage` events can
be used in `async_post_run()` or `post_run()` of `Agent`.

```python
import uuid
from aworld.core.event.base import GroupMessage, TopicType, Message


def async_post_run(self,
                   policy_result,
                   policy_input,
                   message) -> Message:
    your_actions = [tool1_action1, tool1_action2, agent1, agent2, tool2_action]
    group_id = f"agent_id_{uuid.uuid1().hex}"

    # The setting of other parameters can refer to the implementation of the Agent
    return GroupMessage(payload=your_actions,
                        caller=...,
                        sender=...,
                        session_id=...,
                        group_id=group_id,
                        topic=TopicType.GROUP_ACTIONS,
                        headers=...)


```

### Dynamic Routing

When the `async_policy()` or `policy()` function decides which agent to call next, for special cases, Agents may need
customized routing based on specific business rules. You can override the handler in the corresponding Agent:

```python
# Handler name consistency must be maintained
agent = Agent(..., event_handler_name="your_handler_name")
```

```python
from aworld.runners import HandlerFactory
from aworld.runners.handler import DefaultHandler
from aworld.core.event.base import Message, ToolMessage, AgentMessage
from typing import AsyncGenerator


@HandlerFactory.register(name="your_handler_name")
class YourHandler(DefaultHandler):
    def is_valid_message(self, message: Message) -> bool:
        return message.category == "your_handler_name"

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if not self.is_valid_message(message):
            return

        # The type of data is generally ActionModel, but can be customized
        data = message.payload[0]
        content = data.policy_info

        if "use_tool1" in content:
            # not tool call, but want to use tools
            yield ToolMessage(
                payload=...,
                caller=...,
                sender=...,
                receiver="tool1",
                session_id=...,
                headers=...
            )
        elif "use_agent1" in data:
            # want to use agent as next step
            yield AgentMessage(
                payload=...,
                caller=...,
                sender=...,
                receiver="agent1",
                session_id=...,
                headers=...
            )
        else:
            ...
```

You can refer to the implementation of `DefaultTaskHandler` in AWorld.

#### Two Examples of Overriding Routing: ReAct and Plan-Execute

##### ReAct

```python
@HandlerFactory.register(name='react')
class ReactHandler(AgentHandler):
    def is_valid_message(self, message: Message):
        if message.category != 'react':
            return False
        return True

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        yield message
```

##### Plan-Execute

Compared to ReAct, agent2 and agent3 can execute in parallel simultaneously.

```python
from aworld.core.common import Observation
from aworld.core.event.base import AgentMessage
from aworld.logs.util import logger


@HandlerFactory.register(name='plan_execute')
class PlanExecuteHandler(AgentHandler):
    def is_valid_message(self, message: Message):
        if message.category != 'plan_execute':
            return False
        return True

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        logger.info(f"PlanExecuteHandler|handle|taskid={self.task_id}|is_sub_task={message.context._task.is_sub_task}")
        content = message.payload

        # Parse model plan
        plan = parse_plan(content[0].policy_info)
        logger.info(f"PlanExecuteHandler|plan|{plan}")

        # Execute steps
        output, context = execution_steps(plan.steps)

        # Send event message, notify the next processing agent
        new_plan_input = Observation(content=output)
        yield AgentMessage(
            session_id=message.session_id,
            payload=new_plan_input,
            sender=self.name(),
            receiver=self.swarm.communicate_agent.id(),
            headers={'context': context}
        )
```

For more details, refer to the examples.

## Combination and Recursion of MAS and Workflow

Same or different types of Swarms can be deeply nested, providing multi-level Swarms with different interaction
mechanisms to support complex multi-agent interactions. For example, when creating a travel itinerary planner, using a
combination of Workflow + MAS, where Workflow provides deterministic processes and MAS handles multi-source information
retrieval and integration.

```python
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm, GraphBuildType

# Configure agents
agent_conf = AgentConfig(...)

# Create five agents
rewrite = Agent(name="rewrite", conf=agent_conf)
plan = Agent(name="plan", conf=agent_conf)
search = Agent(name="search", conf=agent_conf)
summary = Agent(name="summary", conf=agent_conf)
report = Agent(name="report", conf=agent_conf)

# Construct a MAS
mas = Swarm(
    topology=[(plan, search), (plan, summary)],
    build_type=GraphBuildType.HANDOFF,
    root_agent=[plan]
)

# Construct a combination of a workflow with the MAS team
combination = Swarm(
    topology=[(rewrite, mas), (mas, report)],
    root_agent=[rewrite]
)
```

