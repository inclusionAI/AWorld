# Multi-agent


## Topology

```python
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.agent.swarm import Swarm

agent_conf = AgentConfig(...)
```
### Workflow
Workflow is a special topological structure that can be executed deterministically, all nodes in the swarm will be executed.

At this point, agents with special abilities are necessary!
```python
"""
Workflow topology:
 ┌─────A─────┐     
 B           C
 └─────D─────┘ 
"""
A = Agent(name="A", conf=agent_conf)
B = Agent(name="B", conf=agent_conf)
C = Agent(name="C", conf=agent_conf)
D = Agent(name="D", conf=agent_conf)
parallel = ParallelizableAgent(name="parallel", agents=[B, C])
# serial = SerializableAgent(name="serial", agents=[B, C])
                        
swarm = Swarm(A, parallel, D)
# or 
#swarm = Swarm(A, serial, D)
```
Assuming there is a cycle D -> A in the topology, define it as:

```python
D = LoopableAgent(name="D", conf=agent_conf, max_run_times=5, loop_point=A.id(), stop_func=...)
swarm = Swarm(A, parallel, D)
# or 
# swarm = Swarm(A, serial, D)
```
`stop_func` is a function that determines whether to terminate prematurely.

### Star
Each agent communicates with a single supervisor agent, also known as star topology, 
a special structure of tree topology.

A plan agent with other executing agents is a typical example.
```python
"""
Star topology:
 ┌───── plan ───┐     
exec1         exec2
"""
plan = Agent(name="plan", conf=agent_conf)
exec1 = Agent(name="exec1", conf=agent_conf)
exec2 = Agent(name="exec2", conf=agent_conf)
```

We have two ways to construct this topology structure.
```python
swarm = Swarm((plan, exec1), (plan, exec2))
```
or use handoffs mechanism:
```python
plan = Agent(name="plan", conf=agent_conf, agent_names=['exec1', 'exec2'])
swarm = Swarm(plan, register_agents=[exec1, exec2])
```
or use topology type:
```python
# The order of the plan agent is the first.
swarm = StarSwarm(plan, exec1, exec2)
```

Note: 
- Whether to execute exec1 or exec2 is decided by LLM.
- If you want to execute all defined nodes with certainty, you need to use the `workflow` pattern.
Like this will execute all the defined nodes:
```python
swarm = Swarm((plan, exec1), (plan, exec2), workflow=True)
```
- If it is necessary to execute exec1, whether to execute exec2 depends on LLM, you can define it as:
```python
plan = Agent(name="plan", conf=agent_conf, agent_names=['exec1', 'exec2'])
swarm = Swarm(plan, exec1, register_agents=[exec2], workflow=True)
```
That means that Workflow=True is set, all nodes within the swarm will be executed.

### Tree
This is a generalization of the star topology and allows for more complex control flows.

#### Hierarchical
```python
"""
Hierarchical topology:
            ┌─────────── root ───────────┐
  ┌───── parent1 ───┐        ┌─────── parent2 ───────┐
leaf1_1         leaf1_2    leaf1_1                leaf2_2
"""

root = Agent(name="root", conf=agent_conf)
parent1 = Agent(name="parent1", conf=agent_conf)
parent2 = Agent(name="parent2", conf=agent_conf)
leaf1_1 = Agent(name="leaf1_1", conf=agent_conf)
leaf1_2 = Agent(name="leaf1_2", conf=agent_conf)
leaf2_1 = Agent(name="leaf2_1", conf=agent_conf)
leaf2_2 = Agent(name="leaf2_2", conf=agent_conf)
```

```python
swarm = Swarm((root, parent1), (root, parent2), 
              (parent1, leaf1_1), (parent1, leaf1_2), 
              (parent2, leaf2_1), (parent2, leaf2_2))
```
or use handoffs mechanism:
```python
root = Agent(name="root", conf=agent_conf, agent_names=['parent1', 'parent2'])
parent1 = Agent(name="parent1", conf=agent_conf, agent_names=['leaf1_1', 'leaf1_2'])
parent2 = Agent(name="parent2", conf=agent_conf, agent_names=['leaf2_1', 'leaf2_2'])

swarm = Swarm((root, parent1), (root, parent2), register_agents=[leaf1_1, leaf1_2, leaf2_1, leaf2_2])
```
or use topology type:
```python
swarm = TreeSwarm((root, parent1, parent2),
                  (parent1, leaf1_1, leaf1_2),
                  (parent2, leaf2_1, leaf2_2))
```
#### Map-reduce
If the topology structure becomes further complex:
```
            ┌─────────── root ───────────┐
  ┌───── parent1 ───┐        ┌────── parent2 ──────┐
leaf1_1         leaf1_2   leaf1_1               leaf2_2
  └─────result1─────┘        └───────result2───────┘   
            └───────────final───────────┘ 
```
We define it as Map-reduce topology, this way to build:

```python
result1 = Agent(name="result1", conf=agent_conf)
result2 = Agent(name="result2", conf=agent_conf)
final = Agent(name="final", conf=agent_conf)

swarm = Swarm(
    (root, p_parent), 
    (parent1, leaf1_1), (parent1, leaf1_2), 
    (parent2, leaf2_1), (parent2, leaf2_2),
    (leaf1_1, result1), (leaf1_2, result1), 
    (leaf2_1, result2), (leaf2_2, result2),
    (result1, final),
    (result2, final)
)
```
**Note:**
If you want to execute all nodes, you need to set **workflow=True**, and it is a Workflow mode during agents' execution.
Means that the `handoffs` mechanism can no longer be used, as both leaf1_1/leaf2_1 and leaf1_2/leaf2_2 
need to be executed, and the results need to be aggregated to the result1/result2, then generate the final result.

### Mesh
Divided into a fully meshed topology and a partially meshed topology. 
Fully meshed topology means that each agent can communicate with every other agent, 
any agent can decide which other agent to call next.

```python
"""
Fully Meshed topology:
    ┌─────────── A ──────────┐
    B ───────────|────────── C 
    └─────────── D  ─────────┘
"""
A = Agent(name="A", conf=agent_conf)
B = Agent(name="B", conf=agent_conf)
C = Agent(name="C", conf=agent_conf)
D = Agent(name="D", conf=agent_conf)
```

Network topology need to use the `handoffs` mechanism:
```python
swarm = HandoffsSwarm((A, B), (B, A),
                      (A, C), (C, A),
                      (A, D), (D, A),
                      (B, C), (C, B),
                      (B, D), (D, B),
                      (C, D), (D, C))
```
If a few pairs are removed, it becomes a partially meshed topology.
Or use topology type:
```python
A = Agent(name="A", conf=agent_conf)
B = Agent(name="B", conf=agent_conf)
C = Agent(name="C", conf=agent_conf)
D = Agent(name="D", conf=agent_conf)
swarm = MeshSwarm(A, B, C, D)
```
`MeshSwarm` means fully meshed.

### Ring
A ring topology structure is a closed loop formed by nodes.

```python
"""
Ring topology:
    ┌───────────> A >──────────┐
    B                          C 
    └───────────< D  <─────────┘
"""
A = Agent(name="A", conf=agent_conf)
B = Agent(name="B", conf=agent_conf)
C = Agent(name="C", conf=agent_conf)
D = Agent(name="D", conf=agent_conf)
```


```python
swarm = Swarm((A, C), (C, D), (D, B), (B, A), workflow=True)
```
Or use topology type:
```python
swarm = RingSwarm(A, C, D, B)
```
**Note:**
- This defined loop can only be executed once.
- If you want to execute multiple times, need to define it as:

```python
B = LoopableAgent(name="B", max_run_times=5, stop_func=...)
swarm = Swarm((A, C), (C, D), (D, B), (B, A), workflow=True)
```
### hybrid
A generalization of topology, supporting an arbitrary combination of topologies, internally capable of 
loops, parallel, serial dependencies, and groups.

## Execution