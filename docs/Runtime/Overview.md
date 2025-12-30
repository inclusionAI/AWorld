AWorld runtime is a general-purpose, flexible, distributed execution framework built on top of pluggable compute engines. It provides a complete agent-oriented implementation and includes the following key capabilities:

1. **Full Lifecycle Management** — Clear phases for initialization, execution, and cleanup
2. **Flexible Execution Modes** — Event-driven, call-driven, or custom execution logic to support arbitrarily complex orchestration
3. **Unified Multi-Engine Support** — Compatible with multiple compute backends including local, Spark, and Ray
4. **Convenient Distributed Execution** — Configuration-based support for distributed task execution and state synchronization
5. **Comprehensive Observability** — Built-in tracing, logging, event states, and trajectory recording
6. **Holistic Context Management** — Full state tracking at the session, task, and agent levels
7. **Powerful Extensibility** — Customizable handlers, callbacks, and hooks across multiple dimensions
8. **Robust Error Handling** — Supports mechanisms such as retries and recovery

### Runtime Architecture
#### Framework
```plain
┌────────────────────────────────────────────────────────────────┐
│                    AWorld Runtime Framework                    │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │               Runner Layer                              │   │
│  │      (XRunner / Handler / Callback / Hooks )            │   │
│  └──────────────────┬──────────────────────────────────────┘   │
│                     │                                          │
│  ┌──────────────────▼──────────────────┐                       │
│  │    Context  Layer                   │                       │
│  │  ┌──────────────┐  ┌──────────────┐ │                       │
│  │  │   Session    │  │   Context    │ │                       │
│  │  └──────────────┘  └──────────────┘ │                       │
│  └──────────────────┬──────────────────┘                       │
│                     │                                          │
│  ┌──────────────────▼──────────────────┐                       │
│  │   Event Management Layer            │                       │
│  │  ┌──────────────┐  ┌──────────────┐ │                       │
│  │  │  EventBus    │  │  EventManager│ │                       │
│  │  └──────────────┘  └──────────────┘ │                       │
│  └──────────────────┬──────────────────┘                       │
│                     │                                          │
│  ┌──────────────────▼──────────────────┐                       │
│  │    Compute Engine Layer             │                       │
│  │  ┌──────────┐ ┌──────────┐ ┌─────┐  │                       │
│  │  │  Local   │ │  Spark   │ │ Ray │  │                       │
│  │  └──────────┘ └──────────┘ └─────┘  │                       │
│  └─────────────────────────────────────┘                       │
│                     │                                          │
│  ┌──────────────────▼──────────────────┐                       │
│  │      Multi-Agent / Sandbox          │                       │
│  │  (Tools, Agents, Storage)           │                       │
│  └─────────────────────────────────────┘                       │
│                                                                │
└────────────────────────────────────────────────────────────────┘

```

#### Key Point
The core execution flow integrates all components—agents, tools, context, memory, and human interaction—into a cohesive, observable, and controllable pipeline.

```plain
Create task
   ↓
[Init Runner]
   ├─ pre_run()        : Verify, Load, Context
   │
[Execution]
   ├─ do_run()         
   │   ├─ Event/Call
   │   ├─ Sync/Async
   │   ├─ Trace
   │   └─ ErrorProcess
   │   └─ Trajectory
   │
[Post process]
   └─ post_run()       : release, notify
   ↓
Task response
```

### Core Components
#### **Task – Task Definition**  
`Task` is one of the three fundamental executable concepts in AWorld (with `Agent` and `Tool`).  
It encapsulates all information required to execute a task, typically including: input data, the agent(s) involved, required tools, contextual dependencies, and execution mode. See a concrete implementation of the `Task` structure for details.

```python
from aworld.core.task import Task
from aworld.core.common import StreamingMode

# example of task
task = Task(
    name="complex_task",
    input=user_input,
    swarm=multi_agent,
    conf={
        "max_steps": 20,
        "run_mode": "interactive",
        "check_input": True
    },
    hooks={
        "start": ["init_logging", "init_context"],
        "error": ["handle_error", "send_alert"]
    },
    streaming_mode=StreamingMode.CORE
)
```

#### **Runner – Task Executor**  
The `Runner` is the concrete executor of a `Task` and serves as the heart of the runtime. Each major task category—such as agent execution, evaluation, data generation, or training—has a dedicated `Runner` responsible for managing its full lifecycle and workflow.

For example, in agent tasks, an **event-driven Runner** manages the entire event loop: receiving and emitting events, orchestrating the execution flow, and linking upstream and downstream components via events.

```python
async def do_run(self):
    # send init msg
    await self.event_mng.emit_message(msg)
    # core process
    await self._do_run()
    # gen trajectory
    await self._save_trajectories()
    # build response
    resp = self._response()
```

#### **RuntimeEngine – Compute Engine**  
`RuntimeEngine` abstracts underlying compute backends (local, Ray, Spark, etc.) behind a unified interface, enabling tasks to run seamlessly on any specified engine—including in distributed mode. It hides the complexity of distributed computing, allowing developers to focus on business logic rather than infrastructure. New capabilities of engine can be added through extension.

```python
class RuntimeEngine(object):
    
    ...
    
    async def execute(self, funcs: List[Callable], *args, **kwargs):
        pass

    # gather as extend
    async def agather(self):
        pass
    
    ...
```

#### **EventBus – Event Communication**  
The `EventBus` is the core messaging infrastructure in AWorld, enabling communication between system components via a **publish-subscribe** model with asynchronous message passing. This decouples components and enhances scalability and concurrency. Agents, Tools, and other modules communicate loosely through the EventBus. Currently supports two implementations: **in-memory** and **Redis**.

```python
async def publish(self, messages: Message, **kwargs):
    """publish msg."""

async def consume(self, message: Message, **kwargs):
    """consume msg."""
```

#### **EventManager – Event Orchestration**  
As the central coordinator of the event system, `EventManager` manages the full lifecycle of events—including registration, dispatching, and storage. It maintains a registry of event handlers, provides subscription interfaces, and supports **event persistence**, enabling replay and audit capabilities for full traceability of system state.

```python
async def emit_message(self, event: Message):
    # store msg
    await self.store.create_data(Data(block_id=event.context.get_task().id, value=event, id=event.id))
    # publish msg
    await self.event_bus.publish(event)
    # for streaming
    await self._handle_streaming(event)

async def messages_by_task_id(self, task_id: str):
    # get messages by special task id
    
```

#### **Handler – Event Processor**  
A `Handler` is the execution unit that processes events delivered via the EventBus. Different handlers specialize in specific event types—for example, `AgentHandler` for agent-related events, `ToolHandler` for tool invocations. Handlers implement standardized interfaces to ensure proper processing and support chaining and asynchronous execution.

#### **Callback – Asynchronous Result Handling**  
The `Callback` mechanism handles results from asynchronous operations—especially after tool execution or long-running tasks. Tightly integrated with state management, it ensures accurate tracking and handling of completion status, preventing state inconsistencies caused by async operations.

#### **Hooks – Extensibility Injection Points**  
`Hooks` provide powerful extensibility by allowing custom logic to be injected at key points in the task execution flow (e.g., before/after LLM calls, before/after tool execution, at task start/end). Developers can implement logging, input/output transformation, permission checks, etc., without modifying core code.

#### **StateManager – State Tracking**  
The `StateManager` tracks and manages all runtime state—including execution status, results, and errors—using structured data models like `RunNode` and `NodeGroup`. It enables hierarchical state organization, dependency coordination via waiting mechanisms, and correct execution of complex workflows.

### Event-Driven Agent Runtime
AWorld implements a complete **event-driven runtime** specifically for agents. 

![](../imgs/runtime.png)

This runtime fully integrates all behaviors, modules, and capabilities—including **context updates**, **trajectory construction**, and **human-in-the-loop interactions**—into a single, coherent execution flow.

To simplify runner invocation, the framework provides a utility class called `**Runners**`, which offers a standardized, tool-like interface for executing various types of tasks on demand.

```python
from aworld.runners import Runners

# run an Agent or Multi-Agent (Swarm)
Runners.run(...)
# run one or multiple tasks
Runners.run_task(...)

# streaming response of running an Agent or Multi-Agent (Swarm)
Runners.streaming_run(...)
# streaming response of running a Task
Runners.streaming_run_task(...)
```

