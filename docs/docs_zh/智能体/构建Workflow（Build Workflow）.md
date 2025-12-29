AWorld使用经典的图语法来描述AWorld中的工作流。以下是构建代理工作流的基本场景。

# Agent Workflow
## 顺序
```python
"""
Sequential Agent Pipeline: agent1 → agent2 → agent3

Executes agents in sequence where each agent's output becomes 
the next agent's input, enabling multi-step collaborative processing.
"""

swarm = Swarm([(agent1, agent2), (agent2, agent3)], root_agent=[agent1])
result: TaskResponse = Runners.run(input=question, swarm=swarm)
```

## 并行
```python
"""
Parallel Agent Execution with Barrier Synchronization

    Input ──┬─→ agent1 ──┐
            │            ├──→ agent3 (barrier wait)
            └─→ agent2 ──┘

- agent1 and agent2 execute in parallel
- agent3 acts as a barrier, waiting for both agents
- agent3 processes combined outputs from agent1 and agent2
"""

swarm = Swarm([(agent1, agent3), (agent2, agent3)], root_agent=[agent1, agent2])
result: TaskResponse = Runners.run(input=question, swarm=swarm)
```

## 并行多路径
```python
"""
Parallel Multi-Path Agent Execution

    Input ──→ agent1 ──┬──→ agent2 ──┐
                       │             │
                       └──→ agent3 ←─┘ (barrier wait for agent1 & agent2)

- Single input enters only through agent1
- agent1 distributes to both agent2 and agent3
- agent2 processes and feeds agent3
- agent3 waits for both agent1 and agent2 completion
- agent3 synthesizes outputs from both agent1 and agent2
"""

swarm = Swarm([(agent1, agent2), (agent1, agent3), (agent2, agent3)], root_agent=[agent1])
result: TaskResponse = Runners.run(input=question, swarm=swarm)
```

# 面向任务的Workflow
在分布式或其他易于出现耦合的场景中，进一步支持了面向任务的Workflow，以隔离代理运行时和环境，在需要工具隔离的分布式或其他场景中特别有用。 

```python
task1 = Task(input="my question", agent=agent1)
task2 = Task(agent=agent2)
task3 = Task(agent=agent3)
tasks = [task1, task2, task3]

result: Dict[str, TaskResponse] = Runners.run_task(tasks, RunConfig(sequence_dependent=True))
```

