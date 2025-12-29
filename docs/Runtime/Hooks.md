**Hooks** are a runtime lifecycle interception mechanism in the AWorld framework, embodying the Open-Closed Principle (OCP). They allow developers to inject custom logic at critical points during task execution—without modifying the framework’s core code.

### Why Use Hooks?
In architectures without hooks, extending functionality typically requires modifying framework code directly. With hooks, developers can **register callback functions** to enhance behavior while keeping the main execution flow untouched. When used properly, hooks enable elegant, non-intrusive extensions that improve code clarity and maintainability.

Key benefits include:

1. **Non-intrusive extension** – No need to alter framework internals
2. **Clear lifecycle interception** – Logic inserted precisely at well-defined points
3. **Flexible composition** – Supports hook chains and conditional execution
4. **Powerful observability** – Enables end-to-end event tracking
5. **Maintainable code** – Achieves separation of concerns

### Core Purposes of Hooks
Hooks are instrumental in the following scenarios:

1. **Monitoring & Logging** – Record data at key execution points
2. **Performance Profiling** – Measure time consumption across stages
3. **Data Transformation** – Clean or transform data between phases
4. **Business Logic Injection** – Insert domain-specific rules
5. **Error Handling & Recovery** – Intervene when failures occur
6. **Auditing & Tracing** – Log all critical operations for compliance or debugging

### Hook Mechanism
#### **Hook Points**
Hook points are predefined moments in the task lifecycle where custom logic can be injected. The framework provides a standard set:

```python
class HookPoint:
    START = "start"                    # Task starts
    FINISHED = "finished"              # Task completes
    ERROR = "error"                    # Task encounters an error
    PRE_LLM_CALL = "pre_llm_call"      # Before LLM invocation
    POST_LLM_CALL = "post_llm_call"    # After LLM invocation
    PRE_TOOL_CALL = "pre_tool_call"    # Before tool invocation
    POST_TOOL_CALL = "post_tool_call"  # After tool invocation
    OUTPUT_PROCESS = "output_process"  # During output processing
```

#### **Hook (The Executable Unit)**
A `Hook` is an executable component triggered at a specific hook point:

```python
class Hook(abc.ABC):
    @abc.abstractmethod
    def point(self) -> str:
        """Return the hook point this hook belongs to."""

    @abc.abstractmethod
    async def exec(self, message: Message, context: Context = None) -> Message:
        """
        Execute hook logic.
        
        Args:
            message: Current message object
            context: Execution context
            
        Returns:
            Modified (or original) message object
        """
```

#### **Hook Chain**
Multiple hooks can be registered at the same hook point and will execute **sequentially**, forming a processing chain:

```plain
Incoming Message
       ↓
[Hook1 executes] → may modify message
       ↓
[Hook2 executes] → may further modify message
       ↓
[Hook3 executes] → may modify again
       ↓
Outgoing Message
```

#### **Hook Registration**
Hooks can be auto-registered via the `HookFactory` using decorators:

```python
@HookFactory.register(name="PreLLMCallContextProcessHook", desc="Processes context before LLM call")
class PreLLMCallContextProcessHook(PreLLMCallHook):
    def name(self):
        return convert_to_snake("PreLLMCallContextProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # Custom logic here
        pass
```

#### **Hook Execution**
The framework executes all hooks at a given point via an async generator:

```python
async def run_hooks(self, message: Message, hook_point: str) -> AsyncGenerator[Message, None]:
    """Execute all hooks registered at the specified hook point."""
    if not self.hooks:
        return

    hooks = self.hooks.get(hook_point, [])
    for hook in hooks:
        try:
            msg = await hook.exec(message)
            if msg:
                yield msg
        except Exception as e:
            logger.error(f"Hook execution failed: {e}")
```

### Custom Hooks Examples
By leveraging hooks, developers can build highly extensible, observable, and maintainable agent systems—while keeping business logic cleanly decoupled from the framework’s execution engine.

```python
from aworld.runners.hook.hooks import StartHook, FinishedHook

class MonitoringStartHook(StartHook):
    """Clear naming and documentation.
    
    Logs task startup details for monitoring.
    """
    async def exec(self, message, context):
        task = context.get_task()
        print(f"✓ Task started: {task.id}")
        print(f"  Input: {task.input}")
        return message

class MonitoringFinishedHook(FinishedHook):
    """Robust error handling in hooks."""
    async def exec(self, message, context):
        try:
            task = context.get_task()
            print(f"✓ Task finished: {task.id}")
            print(f"  Result: {message.payload}")
        except Exception:
            pass  # Avoid hook failures breaking main flow
        return message

# Usage
task = Task(name="test", input="What is AI?", agent=agent)
runner = TaskEventRunner(task)

runner.hooks = {
    HookPoint.START: [MonitoringStartHook()],
    HookPoint.FINISHED: [MonitoringFinishedHook()]
}

response = await runner.run()
```

