AWorld supports parallel execution of multiple tasks—not only via local multi-processing, but also through seamless integration with distributed computing engines like **Ray** and **Apache Spark**—making large-scale parallel task processing simple and efficient. This capability is built upon several core design principles:

+ **Unified Abstraction**: Regardless of the underlying compute engine (local, Ray, or Spark), AWorld exposes a consistent API to users.
+ **Transparent Switching**: Users can effortlessly switch between different compute engines without modifying their business logic.
+ **High Scalability**: The framework is designed to support a smooth transition from small-scale local testing to large-scale distributed production environments.

Through elegant design and high-level abstraction, AWorld delivers unified parallel task processing across diverse compute backends. With a single, consistent API, developers can easily parallelize workloads—boosting both development efficiency and system performance. Key advantages include:

+ **Improved Execution Efficiency**: Parallel execution of multiple tasks significantly reduces total runtime, especially when handling large volumes of independent tasks.
+ **Flexible Deployment Options**: Users can choose the most suitable execution engine based on their needs—enabling seamless progression from local development to large-scale production.
+ **Simplified Distributed Computing**: The complexity of distributed systems is abstracted away, allowing developers to focus on business logic rather than infrastructure details.
+ **Strong Extensibility**: A modular, plugin-based architecture makes it easy to add support for new compute engines to meet evolving requirements.

### Tasks Execution Process
![](../imgs/parallel_task.png)

### Runtime Engines
AWorld currently supports three execution engines:

+ **LocalRuntime**: A local multi-process engine requiring no external dependencies—ideal for development and small-scale deployments.
+ **RayRuntime**: A distributed execution engine built on **Ray**, optimized for large-scale parallel processing.
+ **SparkRuntime**: An execution engine based on **Apache Spark**, tailored for big data processing scenarios.

### Runtime Configuration
**RunConfig** is the key to switching between runtime engines. By setting properties such as:

+ `engine_name`: Specifies which compute engine to use (e.g., `"ray"`, `"spark"`, `"local"`),
+ `sequence_dependent`: Indicates whether tasks have sequential dependencies,
+ `in_local`: For distributed engines, enables local mode for testing,
+ `cls`: Allows custom `RuntimeEngine` implementations,

Users can fine-tune execution for optimal performance.

### Unified Entry Point
**Runners** provide a standardized, tool-oriented interface for task submission. Internally, they expose utility methods like `exec_tasks`, enabling on-demand task submission and immediate execution.

```python
from aworld.core.task import Task
from aworld.runner import Runners
from aworld.config import RunConfig, EngineName

# create tasks
tasks = [
    Task(input="what is machine learning?", agent=my_agent, id="task1"),
    Task(input="explain neural networks", agent=my_agent, id="task2"),
    Task(input="what is deep learning?", agent=my_agent, id="task3")
]
# Use Ray
run_conf=RunConfig(
    engine_name=EngineName.RAY,
    worker_num=len(tasks)
)

# utility entry point
results = Runners.sync_run_task(
    task=tasks,
    run_conf=run_conf
)

from aworld.utils.run_util import exec_tasks

# inner utility func
exec_tasks(tasks=tasks, run_conf=run_conf)
```

### Agent-Level Parallelism
While tasks represent a coarser granularity than agents, AWorld also supports parallelism at the agent level via **ParallelizableAgent**.

```python
sub_agents = [google_search, bing_search, wiki, ...]
parallel_agent = ParallelizableAgent(name=f"parallel_search",
                                     agents=sub_agents)
```

Additionally, users can define custom result aggregation functions to process outputs from parallel agent executions.

```python
def custom_aggregate_func(agent: ParallelizableAgent, results: Dict[str, Any]) -> ActionModel:
    # custom logic
    aggregated_result = "...process parallel results..."
    return ActionModel(policy_info=aggregated_result)

parallel_agent = ParallelizableAgent(
    agents=sub_agents,
    aggregate_func=custom_aggregate_func
)
```

