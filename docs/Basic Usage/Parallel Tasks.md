AWorld supports parallel execution of multiple tasks—not only via local multi-processing, but also through seamless integration with distributed computing engines like </font>**Ray</font>** and </font>**Apache Spark</font>**—making large-scale parallel task processing simple and efficient. This capability is built upon several core design principles:</font>

+ **Unified Abstraction</font>**: Regardless of the underlying compute engine (local, Ray, or Spark), AWorld exposes a consistent API to users.</font>
+ **Transparent Switching</font>**: Users can effortlessly switch between different compute engines without modifying their business logic.</font>
+ **High Scalability</font>**: The framework is designed to support a smooth transition from small-scale local testing to large-scale distributed production environments.</font>

Through elegant design and high-level abstraction, AWorld delivers unified parallel task processing across diverse compute backends. With a single, consistent API, developers can easily parallelize workloads—boosting both development efficiency and system performance. Key advantages include:</font>

+ **Improved Execution Efficiency</font>**: Parallel execution of multiple tasks significantly reduces total runtime, especially when handling large volumes of independent tasks.</font>
+ **Flexible Deployment Options</font>**: Users can choose the most suitable execution engine based on their needs—enabling seamless progression from local development to large-scale production.</font>
+ **Simplified Distributed Computing</font>**: The complexity of distributed systems is abstracted away, allowing developers to focus on business logic rather than infrastructure details.</font>
+ **Strong Extensibility</font>**: A modular, plugin-based architecture makes it easy to add support for new compute engines to meet evolving requirements.</font>

<h3 id="UuBBo">Tasks Execution Process</h3>
![](https://intranetproxy.alipay.com/skylark/lark/0/2025/png/7350/1766138528576-4a7c95f1-373e-46d4-9b35-8612c2e8d23d.png)

<h3 id="runtime-engines">Runtime Engines</font></h3>
AWorld currently supports three execution engines:</font>

+ **LocalRuntime</font>**: A local multi-process engine requiring no external dependencies—ideal for development and small-scale deployments.</font>
+ **RayRuntime</font>**: A distributed execution engine built on</font> </font>**Ray</font>**, optimized for large-scale parallel processing.</font>
+ **SparkRuntime</font>**: An execution engine based on </font>**Apache Spark</font>**, tailored for big data processing scenarios.</font>

<h3 id="runtime-configuration">Runtime Configuration</font></h3>
**RunConfig</font>** </font>is the key to switching between runtime engines. By setting properties such as:</font>

+ `engine_name</font>`: Specifies which compute engine to use (e.g.,</font> </font>`"ray"</font>`,</font> </font>`"spark"</font>`,</font> </font>`"local"</font>`),</font>
+ `sequence_dependent</font>`: Indicates whether tasks have sequential dependencies,</font>
+ `in_local</font>`: For distributed engines, enables local mode for testing,</font>
+ `cls</font>`: Allows custom</font> </font>`RuntimeEngine</font>` </font>implementations,</font>

Users can fine-tune execution for optimal performance.</font>

<h3 id="unified-entry-point">Unified Entry Point</font></h3>
**Runners</font>** </font>provide a standardized, tool-oriented interface for task submission. Internally, they expose utility methods like</font> </font>`exec_tasks</font>`, enabling on-demand task submission and immediate execution.</font>

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

<h3 id="agent-level-parallelism">Agent-Level Parallelism</font></h3>
While tasks represent a coarser granularity than agents, AWorld also supports parallelism at the agent level via </font>**ParallelizableAgent</font>**.</font>

```python
sub_agents = [google_search, bing_search, wiki, ...]
parallel_agent = ParallelizableAgent(name=f"parallel_search",
                                     agents=sub_agents)
```

Additionally, users can define custom result aggregation functions to process outputs from parallel agent executions.</font>

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

