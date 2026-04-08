# Aworld Cron Scheduler Design

**Date:** 2026-04-08  
**Status:** Design Phase  
**Author:** AI Assistant + wuman

## 1. Overview

### 1.1 Goal

Add cron-like scheduled task capabilities to Aworld, enabling:
- Periodic task execution (e.g., daily benchmarks)
- One-time delayed tasks (e.g., "remind me in 30 minutes")
- Agent-driven reminders (e.g., Agent sets up reminders during conversation)

### 1.2 Core Requirements

**Primary Use Cases:**
- **A. Periodic Execution**: Run tasks on a schedule (daily, hourly, etc.)
- **B. Delayed Tasks**: One-time tasks triggered after a delay
- **D. Agent Reminders**: Agent proactively reminds users in conversation

**Execution Modes:**
- **Main Session**: Continue in the original conversation session
- **Isolated Instance**: Create a new independent Agent instance

**Storage Strategy:**
- **Hybrid**: Support project-level, user-level, and in-memory storage
- Project tasks → `.aworld/cron.json`
- User tasks → `~/.aworld/cron.json`
- Temporary tasks → Memory

**Scheduler Runtime:**
- **CLI Mode**: Built-in scheduler (Priority)
- **Services Mode**: Service-integrated scheduler with extensibility hooks
- **Daemon Option**: Optional standalone daemon process

**User Interaction:**
- **Agent Tool**: `cron_tool` for natural language interaction
- **Slash Command**: `/cron` for quick operations in interactive sessions
- **CLI Subcommand**: `aworld-cli cron` for batch management

### 1.3 Design Principles

1. **Definition-Execution Separation**: `CronJob` (persistent definition) vs `Task` (runtime instance)
2. **Reuse Existing Infrastructure**: Leverage `Task` and `TaskRunner` for execution
3. **Minimal Invasion**: No modification to core Agent/Runner logic
4. **Extensibility**: Reserve hooks for Services mode
5. **Graceful Degradation**: Automatic fallback when sessions become inactive

## 2. Architecture

### 2.1 Component Layout

```
┌─────────────────────────────────────┐
│  User Interface Layer                │
│  - Agent CronTool                   │
│  - /cron Slash Command              │
│  - aworld-cli cron subcommand       │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│  Scheduler Core (aworld/core/scheduler/) │
│  - CronScheduler                    │
│  - CronStore                        │
│  - CronExecutor                     │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│  Execution Layer (Reuse Existing)   │
│  - TaskRunner (execute Task)        │
│  - Swarm/Agent (actual execution)   │
└─────────────────────────────────────┘
```

### 2.2 Directory Structure

```
aworld/
├── core/
│   └── scheduler/              # NEW: Scheduler core
│       ├── __init__.py
│       ├── types.py            # Data models
│       ├── scheduler.py        # CronScheduler
│       ├── store.py            # CronStore abstraction
│       ├── executor.py         # CronExecutor
│       ├── backends/           # Backend implementations
│       │   ├── base.py
│       │   └── local.py
│       └── missed_reminders.py # Missed reminder store
├── tools/
│   └── builtin/
│       └── cron_tool.py        # NEW: Cron tool
└── core/
    └── context/
        └── session_manager.py  # NEW: Session management

aworld-cli/
├── src/aworld_cli/
│   ├── commands/
│   │   └── cron_cmd.py         # NEW: /cron slash command
│   └── cron_cli.py             # NEW: Subcommand implementation
└── inner_plugins/smllc/agents/
    └── aworld_agent.py         # MODIFY: Add cron to default tools
```

## 3. Data Models

### 3.1 Core Types

```python
# aworld/core/scheduler/types.py

@dataclass
class CronSchedule:
    """Scheduling configuration"""
    kind: Literal["at", "every", "cron"]
    
    # at: One-time task
    at: Optional[str] = None  # ISO 8601 timestamp
    
    # every: Interval repetition
    every_seconds: Optional[int] = None
    anchor_time: Optional[str] = None
    
    # cron: Cron expression
    cron_expr: Optional[str] = None
    timezone: Optional[str] = None
    stagger_ms: Optional[int] = None  # Prevent peak-hour clustering

@dataclass
class CronPayload:
    """Task execution content (serializable)"""
    kind: Literal["agent_turn", "system_event"]
    
    # agent_turn: Let Agent execute
    message: Optional[str] = None
    agent_name: Optional[str] = None  # Store name, not instance
    tool_names: List[str] = field(default_factory=list)
    model_override: Optional[str] = None
    timeout_seconds: Optional[int] = None
    
    # system_event: System notification
    event_text: Optional[str] = None

@dataclass
class CronExecutionMode:
    """Execution mode"""
    target: Literal["main_session", "isolated"]
    
    # main_session: Continue in original session
    session_id: Optional[str] = None
    context_messages: int = 10  # How many history messages to include
    
    # isolated: Independent instance (no extra fields needed)

@dataclass
class CronJob:
    """Cron task definition (serializable to file)"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    enabled: bool = True
    delete_after_run: bool = False  # One-time task
    
    schedule: CronSchedule
    payload: CronPayload
    execution_mode: CronExecutionMode
    
    # Metadata
    created_at: str  # ISO 8601
    updated_at: str
    
    # Runtime state (optional)
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_status: Optional[Literal["ok", "error", "skipped"]] = None
    consecutive_errors: int = 0

@dataclass
class CronJobExecution:
    """Single execution record (optional, for history tracking)"""
    id: str
    job_id: str
    started_at: str
    finished_at: Optional[str] = None
    status: Literal["running", "ok", "error", "timeout"]
    error: Optional[str] = None
    task_id: Optional[str] = None  # Associated Task ID
    summary: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
```

### 3.2 Storage Format

```json
// .aworld/cron.json (Project-level)
{
  "version": 1,
  "jobs": [
    {
      "id": "job-abc",
      "name": "Daily Benchmark",
      "schedule": {
        "kind": "cron",
        "cron_expr": "0 9 * * *",
        "timezone": "Asia/Shanghai"
      },
      "payload": {
        "kind": "agent_turn",
        "message": "Run GAIA benchmark validation",
        "agent_name": "Aworld",
        "tool_names": ["terminal", "read_file"]
      },
      "execution_mode": {
        "target": "isolated"
      },
      "enabled": true,
      "created_at": "2026-04-08T10:00:00Z",
      "next_run_at": "2026-04-09T09:00:00+08:00"
    }
  ]
}
```

## 4. Core Components

### 4.1 CronScheduler

**Responsibilities:**
- Manage timers and trigger tasks
- Calculate next run times
- Handle failure retries

**Key Methods:**
```python
class CronScheduler:
    async def start()                    # Start scheduler
    async def stop()                     # Stop scheduler
    async def add_job(job: CronJob)      # Add new job
    async def update_job(id, patch)      # Update job
    async def remove_job(id)             # Remove job
    async def run_job(id, force=False)   # Manually trigger
    async def list_jobs()                # List all jobs
    async def get_status()               # Scheduler status
```

**Scheduling Loop:**
```python
async def _schedule_loop(self):
    while self.running:
        # 1. Get all enabled jobs
        jobs = await self.store.list_enabled_jobs()
        
        # 2. Find next job to run
        next_job, wait_seconds = self._find_next_job(jobs)
        
        if next_job:
            # 3. Wait until execution time
            await asyncio.sleep(wait_seconds)
            
            # 4. Trigger execution
            await self._trigger_job(next_job)
        else:
            await asyncio.sleep(60)
```

### 4.2 CronStore

**Responsibilities:**
- Persist task definitions
- Support multiple backends (file/memory)

**Abstract Interface:**
```python
class CronStore(ABC):
    @abstractmethod
    async def add_job(self, job: CronJob) -> CronJob
    
    @abstractmethod
    async def update_job(self, job_id: str, **updates) -> CronJob
    
    @abstractmethod
    async def remove_job(self, job_id: str)
    
    @abstractmethod
    async def get_job(self, job_id: str) -> Optional[CronJob]
    
    @abstractmethod
    async def list_jobs(self, enabled_only: bool = False) -> List[CronJob]
```

**Implementations:**
- `FileBasedCronStore`: File storage (project/user level)
- `InMemoryCronStore`: Memory storage (session level)

### 4.3 CronExecutor

**Responsibilities:**
- Convert `CronJob` to `Task`
- Create appropriate context based on execution mode

**Key Flow:**
```python
class CronExecutor:
    async def execute(self, job: CronJob) -> TaskResponse:
        # 1. Build Task based on execution mode
        task = await self._build_task(job)
        
        # 2. Execute using TaskRunner
        runner = TaskRunner(task)
        result = await runner.run()
        
        return result
    
    async def _build_task(self, job: CronJob) -> Task:
        # Resolve Agent instance
        agent = self._resolve_agent(job.payload.agent_name)
        
        # Build Context
        context = None
        if job.execution_mode.target == "main_session":
            # Session continuation: restore original context
            context = await self._load_session_context(
                job.execution_mode.session_id,
                context_messages=job.execution_mode.context_messages
            )
        
        # Create Task
        return Task(
            input=job.payload.message,
            agent=agent,
            tool_names=job.payload.tool_names,
            context=context,
            timeout=job.payload.timeout_seconds or 0,
        )
```

## 5. User Interfaces

### 5.1 Agent Tool: cron_tool

**Tool Definition:**
```python
@be_tool(
    tool_name='cron',
    tool_desc="Manage scheduled tasks (cron jobs)"
)
def cron_tool(
    action: Literal["add", "list", "update", "remove", "run", "status"],
    
    # add parameters
    name: Optional[str] = None,
    message: Optional[str] = None,
    schedule_type: Optional[Literal["at", "every", "cron"]] = None,
    schedule_value: Optional[str] = None,
    execution_mode: Optional[Literal["main_session", "isolated"]] = "isolated",
    agent_name: Optional[str] = "Aworld",
    tools: Optional[List[str]] = None,
    delete_after_run: Optional[bool] = None,
    
    # update/remove/run parameters
    job_id: Optional[str] = None,
    enabled: Optional[bool] = None,
    
    # list parameters
    include_disabled: Optional[bool] = False,
    
    # Internal (auto-filled by Agent)
    _session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute cron operations"""
    # Implementation...
```

**Usage Examples:**
```python
# User: "每天早上9点提醒我运行测试"
cron_tool(
    action="add",
    name="Daily Test Reminder",
    message="提醒：现在是每天的测试运行时间",
    schedule_type="cron",
    schedule_value="0 9 * * *",
    execution_mode="main_session",
    _session_id="<current_session_id>",
)

# User: "30分钟后提醒我提交代码"
cron_tool(
    action="add",
    name="Code commit reminder",
    message="提醒：30分钟前你要求我提醒你提交代码",
    schedule_type="at",
    schedule_value="2026-04-08T11:30:00+08:00",
    execution_mode="main_session",
    _session_id="<current_session_id>",
    delete_after_run=True,
)
```

**Tool Registration:**
- **Location**: `aworld/tools/builtin/cron_tool.py`
- **Default Enabled**: Aworld agent includes `"cron"` in `tool_names`
- **Other Agents**: Explicitly add `tool_names=["cron"]`

### 5.2 Slash Command: /cron

**Implementation:**
```python
# aworld-cli/src/aworld_cli/commands/cron_cmd.py

@register_command
class CronCommand(Command):
    @property
    def name(self) -> str:
        return "cron"
    
    @property
    def command_type(self) -> str:
        return "prompt"  # Generate prompt for Agent
    
    async def get_prompt(self, context: CommandContext) -> str:
        args = context.args
        
        if not args:
            return "使用 cron tool 列出所有定时任务"
        
        action, *rest = args.split(maxsplit=1)
        
        if action == "add":
            return f"创建定时任务：{rest[0]}"
        elif action == "remove":
            return f"使用 cron tool 删除任务 {rest[0]}"
        # ... other actions
```

**Usage:**
```bash
> /cron                          # List all tasks
> /cron add 每天早上9点运行测试    # Create task
> /cron remove job-abc123        # Remove task
> /cron run job-abc123           # Trigger immediately
```

### 5.3 CLI Subcommand: aworld-cli cron

**Implementation:**
```python
# aworld-cli/src/aworld_cli/cron_cli.py

@click.group(name="cron")
def cron_cli():
    """Manage scheduled tasks"""
    pass

@cron_cli.command(name="list")
@click.option("--all", is_flag=True)
def list_tasks(all: bool):
    """List all scheduled tasks"""
    # Implementation...

@cron_cli.command(name="add")
@click.option("--name", required=True)
@click.option("--message", required=True)
@click.option("--schedule", required=True)
def add_task(name, message, schedule):
    """Add a new scheduled task"""
    # Implementation...
```

**Usage:**
```bash
# List tasks
aworld-cli cron list

# Add task
aworld-cli cron add \
  --name "Daily Benchmark" \
  --message "Run GAIA tests" \
  --schedule "cron 0 9 * * *"

# Remove task
aworld-cli cron remove job-abc123

# Trigger immediately
aworld-cli cron run job-abc123
```

## 6. Session Continuation Mode

### 6.1 Challenge

When a task triggers, the original session may be:
- Still active (CLI not closed) ✓ Ideal
- Terminated (CLI closed) ✗ Need fallback
- In a different session

### 6.2 Solution: Layered Handling

**Session State Detection:**
```python
class CronExecutor:
    async def _execute_in_session(self, job: CronJob) -> TaskResponse:
        session_id = job.execution_mode.session_id
        
        # Check if session is still active
        session_manager = get_session_manager()
        session = session_manager.get_session(session_id)
        
        if session and session.is_active():
            # Session active → inject message
            return await self._inject_into_active_session(job, session)
        else:
            # Session inactive → fallback
            return await self._handle_inactive_session(job)
```

**SessionManager:**
```python
class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, Session] = {}
    
    def register_session(self, session: Session):
        """Register active session"""
        self._sessions[session.id] = session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Get session"""
        return self._sessions.get(session_id)
```

**Session Enhancement:**
```python
class Session:
    def is_active(self) -> bool:
        """Check if session is active"""
        return self._active
    
    def set_inactive(self):
        """Mark session as inactive"""
        self._active = False
        if self._inactive_callback:
            self._inactive_callback()
```

**Fallback Strategy:**
```python
async def _handle_inactive_session(self, job: CronJob):
    """Handle inactive session"""
    
    logger.warning(f"Session inactive, falling back to isolated execution")
    
    # Strategy 1: Switch to isolated mode
    return await self._execute_isolated(job)
    
    # Strategy 2: Record as missed reminder
    # store_missed_reminder(job)
```

### 6.3 Missed Reminders

```python
# aworld/core/scheduler/missed_reminders.py

class MissedReminderStore:
    def add_missed(self, job_id: str, message: str, timestamp: str):
        """Add missed reminder"""
        # Store to .aworld/missed_reminders.json
    
    def get_pending(self) -> List[Dict]:
        """Get pending reminders"""
        # Return unacknowledged reminders

# CLI startup check
async def start():
    store = MissedReminderStore()
    pending = store.get_pending()
    
    if pending:
        print("\n📌 You have missed reminders:")
        for r in pending:
            print(f"  [{r['timestamp']}] {r['message']}")
```

## 7. Services Mode Extensions

### 7.1 Backend Abstraction

```python
# aworld/core/scheduler/backends/base.py

class SchedulerBackend(ABC):
    @abstractmethod
    async def schedule_job(self, job: CronJob)
    
    @abstractmethod
    async def cancel_job(self, job_id: str)

# aworld/core/scheduler/backends/local.py
class LocalSchedulerBackend(SchedulerBackend):
    """Local scheduler (CLI mode)"""
    # Use asyncio timers

# aworld/core/scheduler/backends/celery.py (Optional)
class CelerySchedulerBackend(SchedulerBackend):
    """Celery scheduler (Services mode)"""
    # Use Celery Beat
```

### 7.2 Configuration

```yaml
# .aworld/config.yaml

scheduler:
  enabled: true
  backend: local  # or celery
  
  store:
    type: file
    path: .aworld/cron.json
  
  celery:
    broker_url: redis://localhost:6379/0
    result_backend: redis://localhost:6379/0
  
  error_handling:
    max_retries: 3
    retry_backoff: exponential
    failure_alert: true
```

## 8. Error Handling & Reliability

### 8.1 Retry Strategy

```python
async def execute_with_retry(self, job: CronJob) -> TaskResponse:
    max_retries = 3
    backoff_base = 2
    
    for attempt in range(max_retries + 1):
        try:
            result = await self.execute(job)
            if result.success:
                return result
            
            if attempt >= max_retries:
                return result
            
            wait_seconds = backoff_base ** attempt
            await asyncio.sleep(wait_seconds)
        except Exception as e:
            # Retry logic...
```

### 8.2 Timeout Protection

```python
async def execute(self, job: CronJob) -> TaskResponse:
    timeout = job.payload.timeout_seconds or 300
    
    try:
        return await asyncio.wait_for(
            self._execute_impl(job),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        return TaskResponse(success=False, msg="Timeout")
```

### 8.3 Failure Alerting

```python
class FailureAlerter:
    async def alert(self, job: CronJob, error: str):
        # Check cooldown (avoid spam)
        if not self._should_alert(job):
            return
        
        message = f"⚠️ Cron job '{job.name}' failed: {error}"
        
        # 1. Log
        logger.error(message)
        
        # 2. File record
        self._write_to_file(message)
        
        # 3. External notification (optional)
        # await self._send_notification(message)
```

### 8.4 Health Check

```python
async def health_check(self) -> Dict[str, Any]:
    jobs = await self.store.list_jobs()
    
    return {
        "status": "healthy" if self.running else "stopped",
        "total_jobs": len(jobs),
        "enabled_jobs": len([j for j in jobs if j.enabled]),
        "failing_jobs": len([j for j in jobs if j.consecutive_errors > 0]),
        "uptime_seconds": time.time() - self._start_time,
    }
```

## 9. Implementation Phases

### Phase 1: Core Functionality (MVP)

**Goal:** Support basic scheduled tasks in CLI mode

**Scope:**
- ✅ Data models (CronJob, CronSchedule, CronPayload)
- ✅ CronStore (FileBasedCronStore + InMemoryCronStore)
- ✅ CronScheduler (basic scheduling loop)
- ✅ CronExecutor (Task creation and execution)
- ✅ CronTool (basic add/list/remove operations)
- ✅ Isolated instance execution mode

**Validation:**
```python
async def test_basic_cron():
    job = CronJob(
        name="test",
        schedule=CronSchedule(kind="every", every_seconds=60),
        payload=CronPayload(kind="agent_turn", message="echo test"),
        execution_mode=CronExecutionMode(target="isolated"),
    )
    
    scheduler = get_scheduler()
    await scheduler.add_job(job)
    
    assert job.id in [j.id for j in await scheduler.list_jobs()]
    
    result = await scheduler.run_job(job.id)
    assert result.success
```

**Estimated Time:** 2-3 days

### Phase 2: CLI Integration

**Goal:** Users can use cron via CLI

**Scope:**
- ✅ /cron Slash command
- ✅ aworld-cli cron subcommand
- ✅ Aworld agent default enables cron_tool
- ✅ Scheduler lifecycle management (start/stop with CLI)

**Validation:**
```bash
aworld-cli
> /cron add 每小时检查一次
✓ Created task...

> /cron list
[显示任务列表]

aworld-cli cron list
aworld-cli cron add --name test --schedule "every 1h" --message "test"
```

**Estimated Time:** 1-2 days

### Phase 3: Session Continuation Mode

**Goal:** Support waking Agent in original session

**Scope:**
- ✅ SessionManager
- ✅ Session active state detection
- ✅ Fallback strategy for inactive sessions
- ✅ Missed reminder persistence and display

**Validation:**
```python
async def test_session_continuation():
    session = Session(id="test-session")
    job = create_reminder_job(session_id=session.id)
    
    # Active session
    result = await scheduler.run_job(job.id)
    assert result.success
    assert len(session.context.messages) > 0
    
    # Inactive session
    session.set_inactive()
    result = await scheduler.run_job(job.id)
    # Should fallback to isolated mode or record as missed
```

**Estimated Time:** 2-3 days

### Phase 4: Production Enhancements

**Goal:** Services mode support

**Scope:**
- ⚪ SchedulerBackend abstraction
- ⚪ Celery Backend implementation (optional)
- ⚪ Configuration file support
- ⚪ Error retry strategy
- ⚪ Failure alerting
- ⚪ Health check API

**Priority:** Low (decide after Phase 1-3 completion based on requirements)

## 10. Potential Issues & Considerations

### 10.1 Timezone Handling

**Problem:** Cron expressions in different timezones

**Solution:**
```python
def calculate_next_run(job: CronJob) -> datetime:
    # Parse user timezone
    tz = pytz.timezone(job.schedule.timezone or "UTC")
    
    # Calculate next run in user timezone
    now_in_tz = datetime.now(tz)
    cron = croniter(job.schedule.cron_expr, now_in_tz)
    next_run_in_tz = cron.get_next(datetime)
    
    # Convert to UTC for storage
    next_run_utc = next_run_in_tz.astimezone(pytz.UTC)
    return next_run_utc
```

### 10.2 Concurrent Execution

**Problem:** Resource contention when multiple tasks trigger simultaneously

**Solution:**
```python
class CronScheduler:
    def __init__(self, max_concurrent_jobs: int = 5):
        self.semaphore = asyncio.Semaphore(max_concurrent_jobs)
    
    async def _trigger_job(self, job: CronJob):
        async with self.semaphore:
            await self.executor.execute(job)
```

### 10.3 State Recovery

**Problem:** Scheduler crash recovery

**Solution:**
```python
async def start(self):
    """Recover state on startup"""
    
    # Load all jobs
    jobs = await self.store.list_jobs()
    
    # Recalculate next_run_time
    for job in jobs:
        if job.enabled:
            next_run = self._calculate_next_run(job, datetime.now())
            await self.store.update_job(job.id, next_run_at=next_run.isoformat())
    
    # Handle missed executions
    await self._handle_missed_executions(jobs)
```

### 10.4 Agent Instance Caching

**Problem:** Creating new Agent instance for each execution may be slow

**Solution:**
```python
class CronExecutor:
    def __init__(self):
        self._agent_cache = {}
    
    def _resolve_agent(self, agent_name: str) -> Agent:
        if agent_name not in self._agent_cache:
            self._agent_cache[agent_name] = load_agent(agent_name)
        return self._agent_cache[agent_name]
```

### 10.5 File Locking

**Problem:** Multiple processes accessing same cron.json

**Solution:**
```python
import fcntl

class FileBasedCronStore:
    async def _save(self, data: Dict):
        with open(self.file_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(data, f)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

## 11. Testing Strategy

### 11.1 Unit Tests

```python
# tests/core/scheduler/test_scheduler.py
async def test_schedule_calculation()
async def test_job_persistence()
async def test_execution_modes()

# tests/core/scheduler/test_store.py
async def test_file_store()
async def test_memory_store()

# tests/tools/test_cron_tool.py
async def test_cron_tool_add()
async def test_cron_tool_list()
```

### 11.2 Integration Tests

```python
# tests/integration/test_cron_cli.py
def test_slash_command()
def test_subcommand()

# tests/integration/test_session_continuation.py
async def test_active_session_injection()
async def test_inactive_session_fallback()
```

### 11.3 Benchmark Validation (BDD)

```bash
# After implementing scheduler improvements
cd examples/gaia
python run.py --split validation --start 0 --end 20

# Expected: No regression in GAIA pass rate
# Document any performance impact in commit message
```

## 12. Dependencies

### 12.1 New Dependencies

```txt
croniter>=1.4.0      # Cron expression parsing
pytz>=2023.3         # Timezone support
fcntl                # File locking (standard library)
```

### 12.2 Existing Dependencies

- `aworld.core.task.Task` - Task execution
- `aworld.core.task.TaskRunner` - Task runner
- `aworld.core.agent.swarm.Swarm` - Agent orchestration
- `aworld.tools` - Tool system

## 13. Documentation Updates

### 13.1 User Documentation

- `README.md` - Add cron capabilities to feature list
- `docs/user-guide/cron.md` - NEW: Comprehensive cron guide
- `docs/user-guide/cli-commands.md` - Add `/cron` and `aworld-cli cron`

### 13.2 Developer Documentation

- `CLAUDE.md` - Update architecture section
- `docs/architecture/scheduler.md` - NEW: Scheduler architecture
- `docs/contributing/testing.md` - Add scheduler testing guidelines

## 14. Migration Plan

### 14.1 First Release (v1.0)

- No migration needed (new feature)
- All cron storage formats are v1

### 14.2 Future Versions

If schema changes:
```python
def migrate_cron_store(old_version: int) -> None:
    if old_version == 1:
        # Migrate v1 -> v2
        pass
```

## 15. Success Metrics

### 15.1 Functional Metrics

- ✅ Can create periodic tasks (at/every/cron)
- ✅ Can execute in both modes (main_session/isolated)
- ✅ Agent can use cron_tool naturally
- ✅ CLI commands work correctly
- ✅ Session continuation gracefully degrades

### 15.2 Performance Metrics

- Scheduler overhead < 1% CPU when idle
- Job trigger accuracy within ±5 seconds
- No memory leaks with long-running scheduler

### 15.3 Reliability Metrics

- No task loss after scheduler restart
- Graceful handling of execution failures
- Clear error messages for user troubleshooting

## 16. Open Questions

1. **Storage backend for Services mode**: Redis? PostgreSQL? Keep file-based?
2. **Distributed scheduling**: If multiple Services instances, how to coordinate?
3. **Task priority**: Should some cron jobs have higher priority?
4. **Execution history retention**: How long to keep execution records?

## 17. Future Enhancements

**Post-MVP:**
- Web UI for cron management (Services mode)
- Task dependencies (Job B runs after Job A succeeds)
- Conditional execution (only run if condition met)
- Cron job templates (common task presets)
- Metrics dashboard (success rate, execution time trends)

## 18. References

**Similar Systems:**
- openclaw cron implementation (`/Users/wuman/Documents/workspace/openclaw/src/cron/`)
- Celery Beat
- APScheduler

**Relevant Issues:**
- N/A (new feature)

**Related PRs:**
- N/A (new feature)

---

**Next Steps:**
1. Review design with team
2. Spike Phase 1 core implementation (1 day)
3. Begin Phase 1 development
4. Validate with basic end-to-end test
5. Iterate based on feedback
