# Dispatch Command Design

**Date**: 2026-04-08  
**Author**: Design Session  
**Status**: Design Document - Ready for Implementation

## 1. Overview

### 1.1 Design Goals

Add background task execution capability to `aworld-cli` to solve three core scenarios:

1. **Primary**: Long-running tasks should not block the current session
2. **Secondary**: Concurrent execution of multiple independent tasks
3. **Secondary**: Asynchronous task tracking (submit and check later)

### 1.2 User Story

```
Developer in interactive session:
1. Debugging code
2. Wants to run a benchmark (takes 10 minutes)
3. Doesn't want to wait or close current session
4. Submits background task: /dispatch → "Run GAIA benchmark"
5. Continues debugging, checks occasionally: /tasks status task-123
```

### 1.3 Core Design Principles

**Minimal Invasiveness**: Reuse existing `Runners.streamed_run_task()` and `StreamingOutputs`. All new code lives in `aworld-cli` layer only.

**Simplicity First**: Simple mode interaction - `/dispatch 'task'` returns task-id, manage with `/tasks`. No complex parameters in Phase 1.

**Session-Level**: Task state exists only in current CLI session memory. No persistence needed (cleared on CLI exit).

**Coexist with Interactive Mode**: `/dispatch` and normal interactive input can be mixed. User can have background tasks running while continuing normal conversation.

## 2. Comparison with Existing Features

### 2.1 Feature Matrix

|  | Interactive Submit | Background Execution | Task Management | Use Case |
|---|---------|---------|---------|---------|
| **Continuous** | ❌ CLI args | ❌ Foreground blocking | ❌ None | Single task with retries |
| **Batch** | ❌ YAML config | ❌ Foreground blocking | ❌ None | Large-scale batch processing |
| **/dispatch** | ✅ Interactive | ✅ Background | ✅ /tasks | **Long tasks in session** |

### 2.2 Existing Functionality

```bash
# 1. ContinuousExecutor - Foreground loop execution
aworld-cli --task "add tests" --agent Aworld --max-runs 5
# ❌ Blocks session, cannot run in parallel
# ✅ Good for tasks that need retries

# 2. BatchExecutor - CSV batch foreground execution
aworld-cli batch-job batch.yaml  # Read task list from CSV
# ❌ Blocks session, requires CSV preparation
# ✅ Good for large-scale batch processing

# 3. /dispatch - Interactive background execution (NEW)
> /dispatch
> Run GAIA benchmark 0-50
# ✅ Non-blocking, returns immediately
# ✅ Interactive, no config files needed
# ✅ In-session management (/tasks)
```

### 2.3 Unique Value

`/dispatch` fills the **"interactive session + background execution"** gap that existing features don't cover.

## 3. Architecture Design

### 3.1 Component Overview

```
User Input (CLI)
    ↓
/dispatch Command (Tool Command)
    ↓
BackgroundTaskManager (In-memory)
    ├── TaskMetadata (status, progress, result)
    └── asyncio.Task (background execution)
    ↓
Runners.streamed_run_task() (Existing)
    ↓
StreamingOutputs → Agent Execution
```

### 3.2 Core Components

#### BackgroundTaskManager

```python
class BackgroundTaskManager:
    """
    Session-level background task manager.
    Lives in AWorldCLI instance, cleared on exit.
    """
    
    def __init__(self):
        self.tasks: Dict[str, TaskMetadata] = {}
        self._task_counter = 0
    
    async def submit_task(
        self, 
        agent_name: str,
        task_content: str,
        context: CommandContext
    ) -> str:
        """Submit task to background, return task-id"""
        
    def list_tasks(self) -> List[TaskMetadata]:
        """List all tasks (running/completed/failed)"""
        
    def get_task(self, task_id: str) -> Optional[TaskMetadata]:
        """Get task by ID"""
        
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel running task"""
```

#### TaskMetadata

```python
@dataclass
class TaskMetadata:
    """Task metadata for tracking"""
    task_id: str                      # e.g., "task-001"
    status: str                       # pending/running/completed/failed/cancelled
    agent_name: str                   # "Aworld"
    task_content: str                 # User input
    submitted_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    
    # Execution tracking
    asyncio_task: Optional[asyncio.Task]
    streaming_outputs: Optional[StreamingOutputs]
    
    # Progress tracking (from StreamingOutputs)
    current_step: int = 0
    total_steps: int = 0
    
    # Result
    result: Optional[str] = None      # Final answer
    error: Optional[str] = None       # Error message if failed
```

### 3.3 Technical Stack

- **Task Management**: Python `asyncio` + dictionary storage (in-memory)
- **Progress Tracking**: Reuse `StreamingOutputs.stream()` async iterator
- **Task Cancellation**: `asyncio.Task.cancel()`
- **Output Capture**: Cache final result to memory (Phase 1: only final result)

## 4. Command Design

### 4.1 `/dispatch` Command

**Command Type**: Tool Command (direct execution, no LLM)

#### Interaction Flow

```
User: /dispatch
    ↓
CLI: 📝 请输入任务内容:
    ↓
User: 运行 GAIA benchmark 0-50
    ↓
CLI:
✓ 任务已提交 [task-001]
  Agent: Aworld (default)
  任务: 运行 GAIA benchmark 0-50
  提交时间: 2026-04-08 14:30:00
  
使用以下命令管理任务:
  /tasks list              查看所有任务
  /tasks status task-001   查看任务进度
  /tasks cancel task-001   取消任务
```

#### Implementation Sketch

```python
@register_command
class DispatchCommand(Command):
    @property
    def name(self) -> str:
        return "dispatch"
    
    @property
    def description(self) -> str:
        return "Submit task to background execution"
    
    @property
    def command_type(self) -> str:
        return "tool"  # Direct execution, no LLM
    
    async def execute(self, context: CommandContext) -> str:
        # 1. Interactive input
        from prompt_toolkit import prompt_async
        task_content = await prompt_async("📝 请输入任务内容: ")
        
        if not task_content or not task_content.strip():
            return "[yellow]任务已取消[/yellow]"
        
        # 2. Get BackgroundTaskManager from context
        task_manager = context.sandbox.background_task_manager
        
        # 3. Submit background task
        task_id = await task_manager.submit_task(
            agent_name="Aworld",  # Default agent
            task_content=task_content.strip(),
            context=context
        )
        
        # 4. Return formatted message
        return self._format_success_message(task_id, task_content)
```

#### Sub-agent Dispatching

User expresses which sub-agent to use through **task description**:

```bash
# Example 1: Explicit specification
"使用 developer agent 分析代码复杂度"
→ Aworld spawns Developer sub-agent

# Example 2: Implicit inference
"重构 aworld/core/agent/ 模块"
→ Aworld automatically selects Developer based on task type

# Example 3: No specification
"运行 GAIA benchmark"
→ Aworld decides (may execute directly or spawn appropriate sub-agent)
```

**Key Insight**: Aworld is a TeamSwarm coordinator with built-in sub-agent dispatching logic. No need for `--agent` parameter in `/dispatch`.

### 4.2 `/tasks` Command

**Command Type**: Tool Command (task management)

#### Subcommands

##### `/tasks list`

```
> /tasks list

📋 后台任务列表:

ID         状态      Agent    提交时间             任务描述
─────────────────────────────────────────────────────────────
task-001   运行中    Aworld   2026-04-08 14:30    运行 GAIA benchmark 0-50
task-002   已完成    Aworld   2026-04-08 14:25    分析代码复杂度
task-003   失败      Aworld   2026-04-08 14:20    生成视频

总计: 3 任务 (1 运行中, 1 已完成, 1 失败)
```

##### `/tasks status <task-id>`

```
> /tasks status task-001

📊 任务状态:

任务ID: task-001
状态: 运行中 (Step 12/50)
Agent: Aworld
提交时间: 2026-04-08 14:30:00
运行时长: 2m 35s

进度:
  ████████████░░░░░░░░░░░░ 24% (12/50)

当前步骤: Processing validation task 12...
```

##### `/tasks cancel <task-id>`

```
> /tasks cancel task-001

⚠️  确认取消任务 [task-001]?
任务: 运行 GAIA benchmark 0-50
状态: 运行中 (Step 12/50)

输入 'yes' 确认: yes

✓ 任务已取消 [task-001]
```

#### Implementation Sketch

```python
@register_command
class TasksCommand(Command):
    @property
    def name(self) -> str:
        return "tasks"
    
    @property
    def command_type(self) -> str:
        return "tool"
    
    async def execute(self, context: CommandContext) -> str:
        # Parse subcommand from context.user_args
        args = context.user_args.strip().split()
        
        if not args or args[0] == "list":
            return await self._list_tasks(context)
        elif args[0] == "status" and len(args) >= 2:
            return await self._show_status(args[1], context)
        elif args[0] == "cancel" and len(args) >= 2:
            return await self._cancel_task(args[1], context)
        else:
            return self._help_text()
```

## 5. Implementation Plan

### 5.1 Phase 1 (MVP) - Core Functionality

**Scope**:
```
✅ /dispatch           # Single-line input, submit to background
✅ /tasks list         # List all tasks
✅ /tasks status <id>  # Show status and progress
✅ /tasks cancel <id>  # Cancel running task
```

**Deliverables**:
1. `aworld-cli/src/aworld_cli/commands/dispatch.py` - DispatchCommand
2. `aworld-cli/src/aworld_cli/commands/tasks.py` - TasksCommand
3. `aworld-cli/src/aworld_cli/core/background_task_manager.py` - BackgroundTaskManager
4. `aworld-cli/src/aworld_cli/models.py` - TaskMetadata dataclass
5. Integration with `console.py` (attach manager to AWorldCLI instance)

**Complexity**: ⭐⭐ (Medium)  
**Estimated Effort**: 4-6 hours

**Technical Requirements**:
- Reuse existing `Runners.streamed_run_task()`
- Use `asyncio.create_task()` for background execution
- Extract progress from `StreamingOutputs.stream()`
- Store tasks in `AWorldCLI.background_task_manager: BackgroundTaskManager`

### 5.2 Phase 2 (Optional Enhancements) - ⏸️ Deferred

#### Feature 2.1: Multi-line Input ⚠️ **DEFERRED**
```python
# User presses Esc+Enter or Alt+Enter to submit multi-line
task_content = """
运行 GAIA benchmark:
- split: validation
- start: 0
- end: 50
"""
```

**Complexity**: ⭐⭐⭐ (Complex)  
**Reason**: Requires custom prompt_toolkit key bindings  
**Decision**: ⚠️ **Defer** - Single-line input covers 90% of use cases

#### Feature 2.2: `/tasks result <id>` ⚠️ **Simplified**
```python
# Show final result only (not full streaming output)
> /tasks result task-001

✓ 任务: 运行 GAIA benchmark 0-50
状态: 已完成
运行时长: 8m 42s

结果:
  Pass@1: 68.5%
  Pass@3: 84.2%
  完成任务: 45/50
```

**Complexity**: ⭐⭐ (Medium - Simplified)  
**Implementation**: Store only `TaskMetadata.result: str` (final answer)  
**Decision**: ✅ Can be added to Phase 1+ if needed

#### Feature 2.3: `/dispatch --agent <name>` ✅ **NOT NEEDED**
**Status**: Not implementing - Aworld automatically dispatches sub-agents based on task description.

### 5.3 Phase 1+ (Compromise)

If output viewing is needed, add simplified version in Phase 1:

```python
# /tasks result <id> - Show final result only
TaskMetadata.result: str  # Store final answer when task completes
```

**Trade-off**:
- ✅ Low implementation cost
- ✅ Covers most use cases (users want final result, not full trace)
- ⚠️ Cannot view intermediate steps (acceptable for MVP)

## 6. Technical Implementation Details

### 6.1 Background Task Execution

```python
async def submit_task(
    self, 
    agent_name: str,
    task_content: str,
    context: CommandContext
) -> str:
    # Generate task ID
    task_id = f"task-{self._task_counter:03d}"
    self._task_counter += 1
    
    # Create metadata
    metadata = TaskMetadata(
        task_id=task_id,
        status="pending",
        agent_name=agent_name,
        task_content=task_content,
        submitted_at=datetime.now()
    )
    self.tasks[task_id] = metadata
    
    # Submit to background
    asyncio_task = asyncio.create_task(
        self._run_task_background(task_id, agent_name, task_content, context)
    )
    metadata.asyncio_task = asyncio_task
    
    return task_id

async def _run_task_background(
    self,
    task_id: str,
    agent_name: str,
    task_content: str,
    context: CommandContext
):
    metadata = self.tasks[task_id]
    metadata.status = "running"
    metadata.started_at = datetime.now()
    
    try:
        # Build Task (reuse existing logic from LocalAgentExecutor)
        from aworld.core.task import Task
        from aworld.runner import Runners
        
        task = Task(
            input=task_content,
            agent=agent_name,  # or get swarm from registry
            session_id=context.sandbox.session_id
        )
        
        # Run with streaming (existing infrastructure)
        streaming_outputs = Runners.streamed_run_task(task)
        metadata.streaming_outputs = streaming_outputs
        
        # Track progress
        async for message in streaming_outputs.stream():
            # Extract step info from message
            if hasattr(message, 'step_info'):
                metadata.current_step = message.step_info.current
                metadata.total_steps = message.step_info.total
        
        # Wait for completion
        final_result = await streaming_outputs._run_impl_task
        metadata.result = final_result.get(task.id).answer
        metadata.status = "completed"
        
    except asyncio.CancelledError:
        metadata.status = "cancelled"
    except Exception as e:
        metadata.status = "failed"
        metadata.error = str(e)
    finally:
        metadata.completed_at = datetime.now()
```

### 6.2 Progress Tracking

```python
# Extract progress from StreamingOutputs
async def _track_progress(self, task_id: str):
    metadata = self.tasks[task_id]
    streaming = metadata.streaming_outputs
    
    if not streaming:
        return
    
    async for message in streaming.stream():
        # Parse message for progress info
        # (depends on Message structure - needs investigation)
        if message.type == "step":
            metadata.current_step = message.data.get("current_step", 0)
            metadata.total_steps = message.data.get("total_steps", 0)
```

### 6.3 Integration Points

#### In `console.py`:

```python
class AWorldCLI:
    def __init__(self):
        self.console = console
        self.user_input = UserInputHandler(console)
        self.background_task_manager = BackgroundTaskManager()  # NEW
    
    # Pass manager to CommandContext
    async def _execute_command(self, cmd_name: str, user_args: str):
        context = CommandContext(
            cwd=os.getcwd(),
            user_args=user_args,
            sandbox=self.sandbox,
            background_task_manager=self.background_task_manager  # NEW
        )
        # ... execute command
```

#### In `command_system.py`:

```python
@dataclass
class CommandContext:
    cwd: str
    user_args: str
    sandbox: Optional[Any] = None
    agent_config: Optional[Any] = None
    background_task_manager: Optional[Any] = None  # NEW
```

## 7. Testing Strategy

### 7.1 Unit Tests

```python
# tests/test_background_task_manager.py
async def test_submit_task():
    manager = BackgroundTaskManager()
    task_id = await manager.submit_task("Aworld", "test task", context)
    assert task_id == "task-000"
    assert manager.tasks[task_id].status == "pending"

async def test_cancel_task():
    manager = BackgroundTaskManager()
    task_id = await manager.submit_task("Aworld", "long task", context)
    await asyncio.sleep(0.1)  # Let task start
    success = await manager.cancel_task(task_id)
    assert success
    assert manager.tasks[task_id].status == "cancelled"
```

### 7.2 Integration Tests

```python
# tests/integration/test_dispatch_command.py
async def test_dispatch_command_flow():
    cli = AWorldCLI()
    
    # Simulate /dispatch
    cmd = CommandRegistry.get("dispatch")
    # Mock user input
    with patch('prompt_toolkit.prompt_async', return_value="test task"):
        result = await cmd.execute(context)
    
    assert "task-000" in result
    assert len(cli.background_task_manager.tasks) == 1
```

### 7.3 Manual Testing Checklist

```
□ /dispatch with valid input → task submitted
□ /dispatch with empty input → cancellation message
□ /tasks list shows all tasks with correct status
□ /tasks status shows progress (running task)
□ /tasks status shows result (completed task)
□ /tasks cancel cancels running task
□ Multiple concurrent tasks run independently
□ Task status updates correctly (pending → running → completed)
□ Failed tasks show error message
□ CLI exit doesn't leave zombie background tasks
```

## 8. Future Enhancements (Beyond Phase 2)

**Not in current scope, but documented for future reference:**

1. **Persistent Task History**
   - Store task metadata to SQLite or JSON file
   - Survive CLI restarts

2. **Task Priority**
   - `/dispatch --priority high "urgent task"`
   - Priority queue for task scheduling

3. **Task Dependencies**
   - `/dispatch --after task-001 "next task"`
   - Chain tasks with dependencies

4. **Scheduled Tasks**
   - `/dispatch --at "14:00" "scheduled task"`
   - Cron-like scheduling

5. **Remote Task Execution**
   - Submit tasks to remote agent server
   - Distributed task execution

## 9. Success Criteria

### 9.1 Functional Requirements

- ✅ User can submit task to background without blocking
- ✅ User can view all background tasks and their status
- ✅ User can cancel running tasks
- ✅ Task progress is visible (current step / total steps)
- ✅ Completed tasks show final result
- ✅ Failed tasks show error message

### 9.2 Non-Functional Requirements

- ✅ No changes to core `aworld` framework
- ✅ Minimal memory footprint (in-memory storage only)
- ✅ Tasks clean up on CLI exit
- ✅ No zombie processes left behind
- ✅ Responsive CLI (background tasks don't block input)

### 9.3 User Experience

- ✅ Simple interaction (no complex parameters)
- ✅ Clear feedback (task-id, status, progress)
- ✅ Intuitive commands (/dispatch, /tasks)
- ✅ Consistent with existing CLI patterns

## 10. Open Questions

**Q1**: How to extract step progress from `StreamingOutputs`?  
**A1**: Needs investigation into `Message` structure from `streaming.stream()`. May need to add step info to messages if not already present.

**Q2**: Should we limit max concurrent background tasks?  
**A2**: Phase 1: No limit. Phase 2: Can add `max_concurrent_tasks` config if needed.

**Q3**: What happens to background tasks when CLI crashes?  
**A3**: They will be terminated with the Python process. No persistence in Phase 1 (by design).

**Q4**: How to handle very long output (e.g., 10MB streaming messages)?  
**A4**: Phase 1: Only store final result. Phase 2 (if needed): Use `deque(maxlen=N)` to limit buffer size.

## 11. References

- Existing command system: `aworld-cli/src/aworld_cli/core/command_system.py`
- Existing executors: `aworld-cli/src/aworld_cli/executors/`
- Streaming infrastructure: `aworld/output.py` (`StreamingOutputs`)
- Task execution: `aworld/runner.py` (`Runners`)
- Similar feature: `ContinuousExecutor` (foreground loop)
- Similar feature: `BatchExecutor` (batch processing)

---

**Document Status**: Ready for Implementation  
**Next Steps**: Enter Plan Mode → Create detailed implementation plan → Begin Phase 1 development
