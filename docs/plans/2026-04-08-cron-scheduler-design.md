# Aworld Cron Scheduler Design (MVP)

**Date:** 2026-04-08  
**Status:** Design Phase - MVP Scope  
**Author:** AI Assistant + wuman  
**Version:** 2.0 (Revised after design review)

## 1. Overview

### 1.1 Goal

Add **isolated-mode** cron-like scheduled task capabilities to Aworld CLI, enabling:
- Periodic task execution (e.g., daily benchmarks)
- One-time delayed tasks (e.g., "remind me in 30 minutes")
- Agent-driven task scheduling during conversation

### 1.2 MVP Scope (Revised)

**What's Included:**
- ✅ Isolated execution mode ONLY (new independent Agent instance)
- ✅ Three scheduling modes (at/every/cron)
- ✅ File-based storage (`.aworld/cron.json`)
- ✅ CLI interactive mode support
- ✅ Agent Tool (`cron_tool`)
- ✅ Slash Command (`/cron`)
- ✅ Reliable scheduler core (startup recovery, retry, timeout, concurrency control)

**What's Explicitly Excluded (MVP):**
- ❌ Main session continuation mode (requires heartbeat/system-event semantics)
- ❌ Delivery semantics (notifications to external channels)
- ❌ Multiple storage backends (Redis/PostgreSQL)
- ❌ `aworld-cli cron` subcommand (requires CLI parser changes)
- ❌ Services/web mode support (focus on CLI first)
- ❌ Failure alerting (can be added later)
- ❌ Web UI

### 1.3 Design Principles

1. **Definition-Execution Separation**: `CronJob` (persistent definition) vs `Task` (runtime instance)
2. **Reuse Existing Infrastructure**: Use `Runners.run()` for execution
3. **Minimal Invasion**: No modification to core Agent/Runner logic
4. **CLI-First**: Focus on interactive CLI mode
5. **Reliable Core**: OpenClaw-inspired reliability (startup recovery, atomic writes, retry)

## 2. Architecture

### 2.1 Component Layout

```
┌─────────────────────────────────────┐
│  User Interface Layer                │
│  - Agent cron_tool                  │
│  - /cron Slash Command              │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│  Scheduler Core (aworld/core/scheduler/) │
│  - CronScheduler (timer loop)       │
│  - FileBasedCronStore (persistence) │
│  - CronExecutor (Task builder)      │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│  Execution Layer (Reuse Existing)   │
│  - Runners.run() (entry point)      │
│  - Task + TaskRunner                │
│  - Swarm/Agent                      │
└─────────────────────────────────────┘
```

### 2.2 Directory Structure

```
aworld/
├── core/
│   └── scheduler/              # NEW: Scheduler core
│       ├── __init__.py         # get_scheduler() singleton
│       ├── types.py            # CronJob, CronSchedule, CronPayload (simplified)
│       ├── scheduler.py        # CronScheduler (timer loop + recovery)
│       ├── store.py            # FileBasedCronStore (atomic writes + locking)
│       └── executor.py         # CronExecutor (builds Task, calls Runners.run())
└── tools/
    └── builtin/                # NEW: Builtin tools directory
        ├── __init__.py
        ├── cron_tool.py        # NEW: Cron tool
        └── context_tool.py     # Existing context tool (moved here)

aworld-cli/
├── src/aworld_cli/
│   ├── runtime/
│   │   └── base.py             # MODIFY: Add scheduler lifecycle in start()/stop()
│   ├── commands/
│   │   └── cron_cmd.py         # NEW: /cron slash command
│   └── inner_plugins/smllc/agents/
│       └── aworld_agent.py     # MODIFY: Add "cron" to default tool_names
```

## 3. Data Models (Simplified)

### 3.1 Core Types

```python
# aworld/core/scheduler/types.py

from dataclasses import dataclass, field
from typing import Literal, Optional, List
import uuid
from datetime import datetime

@dataclass
class CronSchedule:
    """Scheduling configuration"""
    kind: Literal["at", "every", "cron"]
    
    # at: One-time task (ISO 8601 timestamp)
    at: Optional[str] = None
    
    # every: Interval repetition (seconds)
    every_seconds: Optional[int] = None
    
    # cron: Cron expression
    cron_expr: Optional[str] = None
    timezone: Optional[str] = "UTC"

@dataclass
class CronPayload:
    """Task execution content (serializable)"""
    message: str                          # Task input
    agent_name: str = "Aworld"           # Agent to use
    tool_names: List[str] = field(default_factory=list)
    timeout_seconds: Optional[int] = None

@dataclass
class CronJobState:
    """Runtime state"""
    next_run_at: Optional[str] = None     # ISO 8601
    last_run_at: Optional[str] = None
    last_status: Optional[Literal["ok", "error", "timeout"]] = None
    last_error: Optional[str] = None
    running: bool = False
    consecutive_errors: int = 0

@dataclass
class CronJob:
    """Cron task definition (serializable to file)"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    enabled: bool = True
    delete_after_run: bool = False        # One-time task
    
    schedule: CronSchedule
    payload: CronPayload
    state: CronJobState = field(default_factory=CronJobState)
    
    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
```

### 3.2 Storage Format

```json
// .aworld/cron.json
{
  "version": 1,
  "jobs": [
    {
      "id": "job-abc123",
      "name": "Daily Benchmark",
      "enabled": true,
      "delete_after_run": false,
      "schedule": {
        "kind": "cron",
        "cron_expr": "0 9 * * *",
        "timezone": "Asia/Shanghai"
      },
      "payload": {
        "message": "Run GAIA benchmark validation",
        "agent_name": "Aworld",
        "tool_names": ["terminal", "read_file"],
        "timeout_seconds": 600
      },
      "state": {
        "next_run_at": "2026-04-09T09:00:00+08:00",
        "last_run_at": null,
        "last_status": null,
        "running": false,
        "consecutive_errors": 0
      },
      "created_at": "2026-04-08T10:00:00Z",
      "updated_at": "2026-04-08T10:00:00Z"
    }
  ]
}
```

## 4. Core Components

### 4.1 CronScheduler

**Responsibilities:**
- Manage timer loop
- Startup recovery (clean stale running, recalculate next runs)
- Trigger job execution with concurrency control

**Key Methods:**
```python
class CronScheduler:
    def __init__(self, store: CronStore, executor: CronExecutor, max_concurrent: int = 5):
        self.store = store
        self.executor = executor
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running = False
        self._timer_task = None
    
    async def start()                    # Start scheduler + recovery
    async def stop()                     # Stop scheduler
    async def add_job(job: CronJob)      # Add new job
    async def update_job(id, **updates)  # Update job
    async def remove_job(id)             # Remove job
    async def run_job(id, force=False)   # Manually trigger
    async def list_jobs()                # List all jobs
```

**Startup Recovery:**
```python
async def start(self):
    """启动调度器（带恢复）"""
    # 1. Clean stale running states
    await self._cleanup_stale_running()
    
    # 2. Recalculate next_run_time for all enabled jobs
    await self._recalculate_next_runs()
    
    # 3. Start timer loop
    self._timer_task = asyncio.create_task(self._schedule_loop())

async def _cleanup_stale_running(self):
    """清理启动前异常中断的任务"""
    jobs = await self.store.list_jobs()
    for job in jobs:
        if job.state.running:
            await self.store.update_job(
                job.id,
                state={"running": False, "last_status": "error", 
                       "last_error": "Scheduler restarted"}
            )
```

**Timer Loop:**
```python
async def _schedule_loop(self):
    """主调度循环"""
    while self.running:
        jobs = await self.store.list_jobs(enabled_only=True)
        
        # Find next job to run
        next_job, wait_seconds = self._find_next_job(jobs)
        
        if next_job and wait_seconds <= 0:
            # Trigger execution (non-blocking)
            asyncio.create_task(self._trigger_job(next_job))
            await asyncio.sleep(1)  # Prevent tight loop
        else:
            # Wait until next job or check interval
            await asyncio.sleep(min(wait_seconds, 60) if next_job else 60)

async def _trigger_job(self, job: CronJob):
    """触发任务（带并发控制、超时、重试）"""
    async with self.semaphore:  # Concurrency control
        try:
            # Mark as running
            await self.store.update_job(
                job.id, 
                state={"running": True, "last_run_at": datetime.utcnow().isoformat()}
            )
            
            # Execute with timeout
            result = await asyncio.wait_for(
                self.executor.execute_with_retry(job),
                timeout=job.payload.timeout_seconds or 600
            )
            
            # Update state
            await self.store.update_job(
                job.id,
                state={
                    "running": False,
                    "last_status": "ok" if result.success else "error",
                    "last_error": result.msg if not result.success else None,
                    "consecutive_errors": 0 if result.success else job.state.consecutive_errors + 1
                }
            )
            
            # Delete one-time jobs
            if job.delete_after_run:
                await self.store.remove_job(job.id)
        
        except asyncio.TimeoutError:
            await self.store.update_job(
                job.id,
                state={"running": False, "last_status": "timeout", 
                       "last_error": "Execution timeout"}
            )
        except Exception as e:
            await self.store.update_job(
                job.id,
                state={"running": False, "last_status": "error", 
                       "last_error": str(e)}
            )
```

### 4.2 FileBasedCronStore

**Responsibilities:**
- Atomic file writes with locking
- CRUD operations on jobs

**Implementation:**
```python
import fcntl
import json
from pathlib import Path
from typing import List, Optional, Dict

class FileBasedCronStore:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self._ensure_file_exists()
    
    def _ensure_file_exists(self):
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_data({"version": 1, "jobs": []})
    
    def _read_data(self) -> Dict:
        """Locked read"""
        with open(self.file_path, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    
    def _write_data(self, data: Dict):
        """Atomic locked write"""
        temp_file = self.file_path.with_suffix('.tmp')
        
        with open(temp_file, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock
            try:
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        # Atomic replace
        temp_file.replace(self.file_path)
    
    async def add_job(self, job: CronJob) -> CronJob:
        data = self._read_data()
        data["jobs"].append(self._job_to_dict(job))
        self._write_data(data)
        return job
    
    async def update_job(self, job_id: str, **updates) -> CronJob:
        data = self._read_data()
        for job_dict in data["jobs"]:
            if job_dict["id"] == job_id:
                # Update fields
                for key, value in updates.items():
                    if key == "state":
                        job_dict["state"].update(value)
                    else:
                        job_dict[key] = value
                job_dict["updated_at"] = datetime.utcnow().isoformat()
                break
        self._write_data(data)
        return await self.get_job(job_id)
    
    async def list_jobs(self, enabled_only: bool = False) -> List[CronJob]:
        data = self._read_data()
        jobs = [self._dict_to_job(j) for j in data["jobs"]]
        if enabled_only:
            jobs = [j for j in jobs if j.enabled]
        return jobs
```

### 4.3 CronExecutor

**Responsibilities:**
- Build Task from CronJob
- Call `Runners.run()` to execute
- Handle retry logic

**Implementation:**
```python
from aworld.runner import Runners
from aworld.core.agent.swarm import Swarm
from aworld.core.task import TaskResponse

class CronExecutor:
    def __init__(self):
        self._agent_cache = {}  # Cache agents
    
    async def execute(self, job: CronJob) -> TaskResponse:
        """Execute job (isolated mode only)"""
        from aworld.runner import Runners
        
        # Resolve agent
        agent = self._resolve_agent(job.payload.agent_name)
        swarm = Swarm(agent)
        
        # Execute using Runners.run()
        result = await Runners.run(
            input=job.payload.message,
            swarm=swarm,
            tool_names=job.payload.tool_names,
            session_id=None,  # Isolated mode: always None
        )
        
        return result
    
    async def execute_with_retry(self, job: CronJob, max_retries: int = 3) -> TaskResponse:
        """Execute with exponential backoff retry"""
        backoff_base = 2
        
        for attempt in range(max_retries + 1):
            try:
                result = await self.execute(job)
                
                if result.success:
                    return result
                
                if attempt >= max_retries:
                    return result
                
                # Exponential backoff
                wait_seconds = backoff_base ** attempt
                logger.warning(f"Job {job.id} failed (attempt {attempt+1}/{max_retries+1}), "
                             f"retrying in {wait_seconds}s...")
                await asyncio.sleep(wait_seconds)
            
            except Exception as e:
                if attempt >= max_retries:
                    return TaskResponse(
                        success=False,
                        msg=f"Execution failed after {max_retries} retries: {str(e)}"
                    )
                
                wait_seconds = backoff_base ** attempt
                await asyncio.sleep(wait_seconds)
    
    def _resolve_agent(self, agent_name: str):
        """Resolve agent from registry (with cache)"""
        if agent_name not in self._agent_cache:
            from aworld_cli.core.agent_registry import get_agent_builder
            builder = get_agent_builder(agent_name)
            
            # Builder returns Swarm, we need the root agent
            swarm = builder()
            if hasattr(swarm, 'root'):
                self._agent_cache[agent_name] = swarm.root
            else:
                # Fallback: get first agent
                agents = list(swarm.agents.values())
                self._agent_cache[agent_name] = agents[0] if agents else None
        
        return self._agent_cache[agent_name]
```

## 5. User Interfaces

### 5.1 Agent Tool: cron_tool

**Location:** `aworld/tools/builtin/cron_tool.py`

**Implementation:**
```python
from aworld.core.tool.func_to_tool import be_tool
from pydantic import Field
from typing import Literal, Optional, List, Dict, Any

@be_tool(
    tool_name='cron',
    tool_desc="Manage scheduled tasks. Actions: add, list, remove, run, status"
)
def cron_tool(
    action: Literal["add", "list", "remove", "run", "status"] = Field(
        description="Action: add/list/remove/run/status"
    ),
    
    # add parameters
    name: Optional[str] = Field(default=None, description="Task name"),
    message: Optional[str] = Field(default=None, description="Task message/instruction"),
    schedule_type: Optional[Literal["at", "every", "cron"]] = Field(
        default=None,
        description="Schedule type: 'at' (once), 'every' (interval), 'cron' (expression)"
    ),
    schedule_value: Optional[str] = Field(
        default=None,
        description="Schedule value: ISO timestamp for 'at', duration for 'every' (e.g. '30m'), cron expr for 'cron'"
    ),
    agent_name: Optional[str] = Field(default="Aworld", description="Agent to use"),
    tools: Optional[List[str]] = Field(default=None, description="Tool names to enable"),
    delete_after_run: Optional[bool] = Field(default=None, description="Delete after execution (for reminders)"),
    
    # update/remove/run parameters
    job_id: Optional[str] = Field(default=None, description="Job ID"),
    enabled: Optional[bool] = Field(default=None, description="Enable/disable"),
    
    # list parameters
    include_disabled: Optional[bool] = Field(default=False, description="Include disabled tasks"),
) -> Dict[str, Any]:
    """Execute cron operations"""
    from aworld.core.scheduler import get_scheduler
    from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload
    
    scheduler = get_scheduler()
    
    if action == "add":
        if not all([name, message, schedule_type, schedule_value]):
            return {"success": False, "error": "Missing required parameters"}
        
        # Parse schedule
        schedule = _parse_schedule(schedule_type, schedule_value)
        
        # Build job
        job = CronJob(
            name=name,
            schedule=schedule,
            payload=CronPayload(
                message=message,
                agent_name=agent_name,
                tool_names=tools or [],
            ),
            delete_after_run=delete_after_run or (schedule_type == "at"),
        )
        
        result = await scheduler.add_job(job)
        
        return {
            "success": True,
            "job_id": result.id,
            "message": f"Created task '{name}' (ID: {result.id})",
            "next_run": result.state.next_run_at,
        }
    
    elif action == "list":
        jobs = await scheduler.list_jobs(enabled_only=not include_disabled)
        return {
            "success": True,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "schedule": _format_schedule(j.schedule),
                    "next_run": j.state.next_run_at,
                    "enabled": j.enabled,
                    "last_status": j.state.last_status,
                }
                for j in jobs
            ],
        }
    
    elif action == "remove":
        if not job_id:
            return {"success": False, "error": "job_id required"}
        await scheduler.remove_job(job_id)
        return {"success": True, "message": f"Removed job {job_id}"}
    
    elif action == "run":
        if not job_id:
            return {"success": False, "error": "job_id required"}
        result = await scheduler.run_job(job_id, force=True)
        return {
            "success": result.success,
            "message": f"Job executed: {result.msg}",
        }
    
    elif action == "status":
        status = await scheduler.get_status()
        return {
            "success": True,
            "running": status.running,
            "total_jobs": len(await scheduler.list_jobs()),
        }

def _parse_schedule(schedule_type: str, schedule_value: str) -> 'CronSchedule':
    """Parse schedule from user input"""
    from aworld.core.scheduler.types import CronSchedule
    
    if schedule_type == "at":
        return CronSchedule(kind="at", at=schedule_value)
    elif schedule_type == "every":
        seconds = _parse_duration(schedule_value)  # "30m" -> 1800
        return CronSchedule(kind="every", every_seconds=seconds)
    elif schedule_type == "cron":
        return CronSchedule(kind="cron", cron_expr=schedule_value)

def _parse_duration(duration_str: str) -> int:
    """Parse duration string to seconds"""
    import re
    match = re.match(r'(\d+)([smhd])', duration_str)
    if not match:
        raise ValueError(f"Invalid duration: {duration_str}")
    
    value, unit = int(match.group(1)), match.group(2)
    multipliers = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    return value * multipliers[unit]
```

**Tool Registration:**
```python
# aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/aworld_agent.py

tool_names=[
    CONTEXT_TOOL,
    'CAST_SEARCH',
    'async_spawn_subagent',
    'cron',  # ← Add this line
],
```

### 5.2 Slash Command: /cron

**Location:** `aworld-cli/src/aworld_cli/commands/cron_cmd.py`

**Implementation:**
```python
from aworld_cli.core.command_system import Command, CommandContext, register_command
from typing import List

@register_command
class CronCommand(Command):
    @property
    def name(self) -> str:
        return "cron"
    
    @property
    def description(self) -> str:
        return "Manage scheduled tasks"
    
    @property
    def command_type(self) -> str:
        return "prompt"  # Generate prompt for Agent
    
    @property
    def allowed_tools(self) -> List[str]:
        return ["cron"]
    
    async def get_prompt(self, context: CommandContext) -> str:
        args = context.user_args  # FIXED: Use user_args
        
        if not args:
            return "使用 cron tool 列出所有定时任务，以表格形式展示"
        
        parts = args.split(maxsplit=1)
        action = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        
        if action == "add":
            return f"""创建定时任务："{rest}"

请分析需求，确定：
1. 任务名称
2. 调度时间（at/every/cron）
3. 要执行的具体内容

然后使用 cron tool 的 add 操作创建任务。"""
        
        elif action == "list":
            return "使用 cron tool 列出所有定时任务"
        
        elif action in ["remove", "rm"]:
            return f"使用 cron tool 删除任务 {rest}"
        
        elif action == "run":
            return f"使用 cron tool 立即执行任务 {rest}"
        
        elif action == "status":
            return "使用 cron tool 查看调度器状态"
        
        else:
            return f"未知命令：{action}。支持：add, list, remove, run, status"
```

**Usage:**
```bash
> /cron
> /cron add 每天早上9点运行测试
> /cron list
> /cron remove job-abc123
> /cron run job-abc123
```

## 6. Lifecycle Management

### 6.1 Scheduler Startup

**Location:** `aworld-cli/src/aworld_cli/runtime/base.py`

```python
class BaseCliRuntime:
    def __init__(self, agent_name: Optional[str] = None):
        self.agent_name = agent_name
        self._running = False
        self.cli = AWorldCLI()
        self._scheduler = None  # NEW
    
    async def start(self) -> None:
        """Start the CLI interaction loop."""
        self._running = True
        
        # Start scheduler
        await self._start_scheduler()
        
        # ... existing code (load agents, etc.)
    
    async def stop(self) -> None:
        """Stop the CLI loop."""
        self._running = False
        
        # Stop scheduler
        await self._stop_scheduler()
    
    async def _start_scheduler(self) -> None:
        """Start Cron scheduler"""
        try:
            from aworld.core.scheduler import get_scheduler
            self._scheduler = get_scheduler()
            await self._scheduler.start()
        except Exception as e:
            from aworld.logs.util import logger
            logger.warning(f"Failed to start cron scheduler: {e}")
    
    async def _stop_scheduler(self) -> None:
        """Stop Cron scheduler"""
        if self._scheduler:
            try:
                await self._scheduler.stop()
            except Exception as e:
                from aworld.logs.util import logger
                logger.warning(f"Failed to stop cron scheduler: {e}")
```

### 6.2 Scheduler Singleton

**Location:** `aworld/core/scheduler/__init__.py`

```python
_scheduler_instance = None

def get_scheduler() -> 'CronScheduler':
    """Get global scheduler singleton"""
    global _scheduler_instance
    
    if _scheduler_instance is None:
        from .scheduler import CronScheduler
        from .store import FileBasedCronStore
        from .executor import CronExecutor
        
        store = FileBasedCronStore(".aworld/cron.json")
        executor = CronExecutor()
        
        _scheduler_instance = CronScheduler(store, executor)
    
    return _scheduler_instance

def reset_scheduler():
    """Reset scheduler singleton (for testing)"""
    global _scheduler_instance
    _scheduler_instance = None
```

## 7. Implementation Plan

### Phase 1: Core Infrastructure (3-4 days)

**Files to Create:**
- `aworld/core/scheduler/types.py` - Data models
- `aworld/core/scheduler/store.py` - FileBasedCronStore
- `aworld/core/scheduler/executor.py` - CronExecutor
- `aworld/core/scheduler/scheduler.py` - CronScheduler
- `aworld/core/scheduler/__init__.py` - Singleton

**Tests:**
```bash
tests/core/scheduler/
├── test_types.py          # Data model validation
├── test_store.py          # File operations + locking
├── test_executor.py       # Agent resolution + execution
└── test_scheduler.py      # Timer loop + recovery
```

**Validation:**
```python
async def test_basic_cron():
    from aworld.core.scheduler import get_scheduler, reset_scheduler
    from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload
    
    reset_scheduler()
    scheduler = get_scheduler()
    
    job = CronJob(
        name="test",
        schedule=CronSchedule(kind="every", every_seconds=60),
        payload=CronPayload(message="print('Hello')"),
    )
    
    added = await scheduler.add_job(job)
    assert added.id
    
    # Manual trigger
    result = await scheduler.run_job(added.id, force=True)
    assert result.success
```

### Phase 2: Agent Integration (2 days)

**Files to Modify:**
- `aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/aworld_agent.py` - Add "cron" to tool_names
- `aworld-cli/src/aworld_cli/runtime/base.py` - Add scheduler lifecycle

**Files to Create:**
- `aworld/tools/builtin/cron_tool.py` - Cron tool
- `aworld/tools/builtin/__init__.py` - Export cron_tool
- `aworld-cli/src/aworld_cli/commands/cron_cmd.py` - /cron command

**Validation:**
```bash
aworld-cli
> 每小时检查一次 git 状态
Agent: [调用 cron_tool add] 已创建任务...

> /cron list
Task ID    Name              Schedule   Next Run
job-abc    Hourly git check  every 1h   2026-04-08 12:00
```

### Phase 3: Reliability Testing (1-2 days)

**Test Cases:**
- Startup recovery (clean stale running)
- Concurrent job execution
- Timeout interruption
- File locking under contention
- Agent resolution caching
- Retry on transient failures

**Performance:**
- Scheduler overhead < 1% CPU when idle
- Job trigger accuracy ±5 seconds
- No memory leaks with long-running scheduler

## 8. Dependencies

### 8.1 New Dependencies

```txt
croniter>=1.4.0      # Cron expression parsing
pytz>=2023.3         # Timezone support
```

### 8.2 Existing Dependencies

- `aworld.runner.Runners` - Task execution
- `aworld.core.agent.swarm.Swarm` - Agent orchestration
- `aworld_cli.core.agent_registry` - Agent resolution

## 9. Testing Strategy

### 9.1 Unit Tests

```python
# tests/core/scheduler/test_store.py
async def test_file_locking()
async def test_atomic_writes()

# tests/core/scheduler/test_scheduler.py
async def test_startup_recovery()
async def test_concurrent_jobs()
async def test_timeout_handling()
```

### 9.2 Integration Tests

```python
# tests/integration/test_cron_cli.py
async def test_cron_tool_add()
async def test_slash_command()
async def test_scheduler_lifecycle()
```

### 9.3 Manual Testing

```bash
# Scenario 1: Create and trigger task
aworld-cli
> 每天早上9点运行测试
> /cron list
> /cron run job-abc123

# Scenario 2: Scheduler recovery
# - Create task
# - Kill CLI (Ctrl+C)
# - Restart CLI
# - Verify task still exists and next_run is correct
```

## 10. Future Enhancements (Post-MVP)

1. **Main Session Mode** (requires heartbeat/system-event semantics)
2. **Delivery Semantics** (notifications to external channels)
3. **Services Mode Support** (`aworld web`)
4. **`aworld-cli cron` Subcommand** (requires CLI parser changes)
5. **Multiple Storage Backends** (Redis, PostgreSQL)
6. **Failure Alerting** (email, Slack, webhook)
7. **Web UI** (for Services mode)
8. **Task Dependencies** (Job B runs after Job A succeeds)
9. **Conditional Execution** (only run if condition met)

## 11. Migration & Compatibility

**First Release (v1.0):**
- No migration needed (new feature)
- Storage format is v1
- All fields in CronJob are required for v1

**Future Versions:**
If schema changes:
```python
def migrate_cron_store(old_version: int) -> None:
    if old_version == 1:
        # Migrate v1 -> v2
        pass
```

## 12. Known Limitations (MVP)

1. **CLI-only**: No support for Services/web mode yet
2. **Isolated-only**: Cannot wake up in original session
3. **File-based storage**: No distributed coordination
4. **Single-process**: Only one CLI instance should manage `.aworld/cron.json`
5. **No alerting**: Silent failures (check logs)

## 13. Success Metrics

### Functional Metrics
- ✅ Can create periodic tasks (at/every/cron)
- ✅ Can execute in isolated mode
- ✅ Agent can use cron_tool naturally
- ✅ CLI commands work correctly
- ✅ Scheduler survives restarts

### Performance Metrics
- Scheduler overhead < 1% CPU when idle
- Job trigger accuracy within ±5 seconds
- No memory leaks with long-running scheduler

### Reliability Metrics
- No task loss after scheduler restart
- Graceful handling of execution failures
- Clear error messages for troubleshooting

## 14. References

**Similar Systems:**
- OpenClaw cron (`/Users/wuman/Documents/workspace/openclaw/src/cron/`)
- Celery Beat
- APScheduler

**Design Review:**
- Key feedback: Focus on isolated mode, reuse Runners.run(), CLI-first approach
- Excluded: main_session mode, delivery semantics, Services mode (for now)

---

**Next Steps:**
1. Review updated design
2. Begin Phase 1 implementation (core infrastructure)
3. Validate with basic end-to-end test
4. Iterate based on feedback
