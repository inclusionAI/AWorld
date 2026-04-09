# Aworld Cron Scheduler Design (Implementable MVP)

**Date:** 2026-04-08  
**Status:** Design Phase - Ready for Implementation  
**Author:** AI Assistant + wuman  
**Version:** 3.1

## 1. Overview

### 1.1 Goal

Add cron-like scheduled task capability to Aworld with the following **MVP semantics**:

- Create one-time delayed tasks (`at`)
- Create fixed-interval tasks (`every`)
- Create cron-expression tasks (`cron`)
- Execute each scheduled task in **isolated mode** using a fresh `Task` / `Runners.run()` call
- Allow users to manage jobs from Aworld conversation and `/cron` slash command
- Surface cron completion/failure notifications in `aworld-cli` TUI

This MVP is designed to fit the **current Aworld CLI architecture** and be implemented without introducing a daemon process.

### 1.2 Important Product Boundary

This design is **not** equivalent to OpenClaw's long-lived cron service.

For this MVP:
- Scheduler runs only while `aworld-cli` runtime is alive
- Jobs persist to disk, but they do **not** trigger while CLI is offline
- On next CLI startup, scheduler performs recovery and recalculates future runs
- Missed runs during offline time are **not replayed** in MVP

That tradeoff is intentional because current codebase has no standalone scheduler service yet.

### 1.3 Scope

**Included in MVP**
- CLI-only scheduler lifecycle
- File-based storage in `.aworld/cron.json`
- `at` / `every` / `cron`
- Isolated execution via `Runners.run()`
- `cron` tool for agent-side management
- `/cron` slash command
- TUI-only task completion notifications
- Startup recovery
- Timeout / retry / bounded concurrency
- Single-process correctness

**Explicitly excluded from MVP**
- Standalone daemon / background service
- Service/web runtime support
- Main-session continuation / wake original chat session
- Delivery semantics (Discord, email, webhook, push)
- Injecting cron completion as synthetic user input back into the agent loop
- Catch-up replay for missed offline windows
- Distributed coordination / multi-writer support
- Web UI
- `aworld-cli cron` top-level CLI subcommand

### 1.4 Design Principles

1. Reuse existing runtime and runner infrastructure.
2. Match current codebase instead of introducing imaginary registry or tool-loading paths.
3. Prefer correctness over feature breadth.
4. Keep scheduler state explicit and serializable.
5. Leave a clear upgrade path to a future daemon-based scheduler.

## 2. Architecture

### 2.1 Runtime Model

MVP architecture:

```text
User / Agent
  -> cron tool or /cron command
  -> CronScheduler
  -> FileBasedCronStore (.aworld/cron.json)
  -> CronExecutor
  -> CronNotificationSink (CLI runtime)
  -> AWorldCLI notification rendering
  -> LocalAgentRegistry / LocalAgent.get_swarm()
  -> Runners.run()
```

### 2.2 Why This Shape

This shape aligns with current implementation reality:

- `BaseCliRuntime` owns the interactive runtime lifecycle
- agents are resolved through `aworld_cli.core.agent_registry.LocalAgentRegistry`, not a `get_agent_builder()` helper
- slash commands are registered through `aworld_cli.commands.__init__`
- tool registration must use paths that are actually loaded by current ToolFactory flow
- `aworld-cli` currently has no Claude-Code-style unified queued-command loop, so cron completion should use a runtime-owned notification channel rather than re-entering the model as synthetic prompt text

## 3. MVP vs Future Proper Cron

### 3.1 MVP in This Document

This document covers:
- scheduler embedded in CLI runtime
- persisted jobs
- no offline execution

### 3.2 Future OpenClaw-like Version

A future "proper cron" version should add:
- standalone daemon process
- CLI as CRUD control plane only
- always-on scheduling independent of interactive sessions
- optional delivery hooks
- optional catch-up policy

That is **not** part of this document.

## 4. Directory Layout

### 4.1 Files to Add

```text
aworld/
├── core/
│   └── scheduler/
│       ├── __init__.py
│       ├── types.py
│       ├── store.py
│       ├── scheduler.py
│       └── executor.py
└── tools/
    └── cron_tool.py

aworld-cli/
└── src/aworld_cli/
    ├── commands/
    │   └── cron_cmd.py
    ├── commands/__init__.py        # modify: import cron_cmd
    ├── runtime/base.py             # modify: start/stop scheduler
    ├── runtime/cron_notifications.py  # add: TUI notification center
    ├── console.py                  # modify: render pending cron notifications
    └── inner_plugins/smllc/agents/aworld_agent.py  # modify: expose cron tool
```

### 4.2 Placement Notes

`cron_tool.py` should live under `aworld/tools/`, not `aworld/tools/builtin/`.

Reason:
- current `aworld.tools` package is scanned automatically
- `aworld/core/tool/builtin/__init__.py` is not a general-purpose auto-loading path
- placing the tool under `aworld/tools/` avoids special-case loader work

## 5. Data Model

### 5.1 Core Types

`aworld/core/scheduler/types.py` already exists and should remain the canonical model layer.

Recommended model shape:

```python
from dataclasses import dataclass, field
from typing import Literal, Optional, List
import uuid
from datetime import datetime

@dataclass
class CronSchedule:
    kind: Literal["at", "every", "cron"]
    at: Optional[str] = None
    every_seconds: Optional[int] = None
    cron_expr: Optional[str] = None
    timezone: str = "UTC"

@dataclass
class CronPayload:
    message: str
    agent_name: str = "Aworld"
    tool_names: List[str] = field(default_factory=list)
    timeout_seconds: Optional[int] = None

@dataclass
class CronJobState:
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_status: Optional[Literal["ok", "error", "timeout"]] = None
    last_error: Optional[str] = None
    running: bool = False
    consecutive_errors: int = 0

@dataclass
class CronJob:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: Optional[str] = None
    enabled: bool = True
    delete_after_run: bool = False
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="cron"))
    payload: CronPayload = field(default_factory=lambda: CronPayload(message=""))
    state: CronJobState = field(default_factory=CronJobState)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
```

### 5.2 State Semantics

`next_run_at` is the scheduler source of truth.

Required rule:
- every due job must be **claimed** before execution by atomically:
  - verifying it is still due
  - setting `running=True`
  - setting `last_run_at=now`
  - advancing `next_run_at` to the next future schedule, or `None` for one-shot jobs

Without that claim step, the same due job can be triggered repeatedly by concurrent scheduler ticks.

### 5.3 Storage Format

```json
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
        "tool_names": ["cron", "CAST_SEARCH"],
        "timeout_seconds": 600
      },
      "state": {
        "next_run_at": "2026-04-09T09:00:00+08:00",
        "last_run_at": null,
        "last_status": null,
        "last_error": null,
        "running": false,
        "consecutive_errors": 0
      },
      "created_at": "2026-04-08T10:00:00Z",
      "updated_at": "2026-04-08T10:00:00Z"
    }
  ]
}
```

## 6. Store Design

### 6.1 Requirements

`FileBasedCronStore` must support:
- list / get / add / update / remove
- transactional update of a single job
- claim-due-job semantics
- atomic replace on write
- in-process lock for correctness

### 6.2 Concurrency Model

MVP assumes **single Aworld CLI process** owns `.aworld/cron.json`.

So correctness target is:
- safe within one process
- robust against crash during write
- not safe for two independent CLI processes both running the scheduler

### 6.3 Recommended Implementation

Do not rely on "lock temp file only" as correctness story.

Use:
- one `asyncio.Lock` / process-local mutex inside store
- read-modify-write performed under that lock
- write to temp file, then `replace()`

Optional `fcntl` can still be used, but it is secondary in MVP because the product contract is single-process.

### 6.4 Store API

Recommended store API:

```python
class FileBasedCronStore:
    async def list_jobs(self, enabled_only: bool = False) -> list[CronJob]: ...
    async def get_job(self, job_id: str) -> CronJob | None: ...
    async def add_job(self, job: CronJob) -> CronJob: ...
    async def update_job(self, job_id: str, **updates) -> CronJob: ...
    async def remove_job(self, job_id: str) -> None: ...
    async def replace_job(self, job: CronJob) -> CronJob: ...
    async def claim_due_job(self, job_id: str, now_iso: str) -> CronJob | None: ...
```

`claim_due_job()` is the key method that makes scheduling safe.

## 7. Scheduler Design

### 7.1 Responsibilities

`CronScheduler` is responsible for:
- startup recovery
- periodic polling
- selecting due jobs
- claiming jobs before execution
- bounded concurrency
- manual trigger

### 7.2 Startup Recovery

On startup:

1. Load all jobs
2. For jobs with `running=True`, mark them as failed with `last_error="Scheduler restarted"`
3. Recalculate `next_run_at`
4. Do **not** replay missed executions from offline period

### 7.3 Polling Strategy

Simple polling is sufficient for MVP:
- wake every 1 second when there is due work
- otherwise sleep until min(next due in 60s, 60s)

### 7.4 Triggering Rule

Pseudo-flow:

```python
async def _schedule_loop(self):
    while self.running:
        jobs = await self.store.list_jobs(enabled_only=True)
        due_jobs = [j for j in jobs if is_due(j.state.next_run_at)]

        if not due_jobs:
            await asyncio.sleep(self._next_sleep_seconds(jobs))
            continue

        for job in due_jobs:
            claimed = await self.store.claim_due_job(job.id, now_iso())
            if claimed is not None:
                asyncio.create_task(self._execute_claimed_job(claimed))

        await asyncio.sleep(1)
```

The scheduler must never execute a job that it has not successfully claimed.

### 7.5 Execution Semantics

For a claimed job:

1. Run under semaphore
2. Call executor with timeout
3. Update `last_status`, `last_error`, `consecutive_errors`
4. Set `running=False`
5. If `delete_after_run=True` and no future `next_run_at`, remove the job
6. Emit a terminal notification event to the CLI runtime if a notification sink is attached

### 7.6 Manual Trigger

`run_job(job_id, force=True)` should:
- bypass due-time check
- still respect semaphore / timeout / state update logic
- not mutate schedule definition
- for recurring jobs, not break future cadence

## 8. TUI Task Notification Design

### 8.1 Why Aworld Should Not Copy Claude Code Literally

Claude Code routes background completion through a unified queued-command system and can safely feed `task-notification` events back into the model loop.

`aworld-cli` does **not** currently have that architecture:
- chat loop is driven directly by `AWorldCLI.run_chat_session()`
- slash commands execute inline
- there is no existing inbox / queued-command abstraction shared by runtime and model loop

Therefore, the Aworld design should copy the **user-visible outcome** of Claude Code task notifications, but use a simpler mechanism:
- scheduler emits terminal events
- CLI runtime buffers them
- TUI renders them between turns / before next prompt
- notifications are **not** injected into the agent as synthetic user messages in MVP

This gives the user proactive visibility without destabilizing the current prompt loop.

### 8.2 Scope

This section is limited to `aworld-cli` TUI only.

Included:
- show a concise notification when a cron task completes, fails, or times out
- show notifications near-real-time while the user is idle at the input prompt
- preserve notifications until they have been rendered once
- include enough summary for the user to decide whether to inspect `/cron list`

Excluded:
- waking a past chat session
- auto-follow-up from the agent after notification
- OS desktop notifications
- remote/web delivery
- injecting notifications into the agent/model loop as synthetic input
- interrupting in-progress streaming output to show a notification

### 8.3 Core Components

Add a lightweight runtime notification path:

```text
CronScheduler
  -> CronNotificationSink (protocol/callback)
  -> BaseCliRuntime-owned CronNotificationCenter
  -> AWorldCLI.render_pending_notifications()
```

Recommended new file:

```text
aworld-cli/src/aworld_cli/runtime/cron_notifications.py
```

Recommended types:

```python
@dataclass
class CronNotification:
    id: str
    job_id: str
    job_name: str
    status: Literal["ok", "error", "timeout"]
    summary: str
    created_at: str
    next_run_at: Optional[str] = None


class CronNotificationCenter:
    async def publish(self, notification: CronNotification) -> None: ...
    async def drain(self) -> list[CronNotification]: ...
```

Design constraints:
- in-memory only for MVP
- process-local only
- FIFO order
- bounded size (for example keep last 100 notifications to avoid unbounded growth)
- notification payload is for TUI summary only, not full execution detail

### 8.4 Notification Payload Rules

Each terminal cron execution should produce one `CronNotification`.

Required fields:
- `job_id`
- `job_name`
- `status`
- `summary`
- `created_at`

Recommended summary rules:
- success: `Cron task "<name>" completed`
- error: `Cron task "<name>" failed`
- timeout: `Cron task "<name>" timed out after <N>s`

Summary must be short and safe for one-line or two-line TUI rendering.

Do **not** embed raw error text, large model output, or free-form execution output in the notification body.

The summary string should be treated as a fixed template, not a pass-through of runtime error content.

### 8.5 Scheduler Integration

`CronScheduler` should accept an optional notification sink:

```python
class CronScheduler:
    def __init__(..., notification_sink: Optional[CronNotificationSink] = None):
        ...
```

On terminal state update in `_execute_claimed_job()` and `run_job()`:
- update persisted job state first
- then publish a notification event

Recommended ordering:
1. persist final state
2. persist execution detail / error trace location if available
3. remove one-shot job if needed
4. publish notification

This ordering ensures `/cron list` reflects the final truth when the user sees the notification.

### 8.6 Runtime Integration

`BaseCliRuntime` should own one `CronNotificationCenter`.

On startup:
- create notification center
- create scheduler with `notification_sink=center.publish`

During chat loop:
- before waiting for user input, drain and render pending notifications
- after each agent turn completes, drain and render pending notifications again
- while the prompt is idle, run a lightweight background poller that drains and renders notifications near-real-time
- suspend that idle poller while agent execution / streaming output is active

This keeps the design simple while still achieving near-real-time visibility at the prompt, and avoids injecting notifications during model streaming.

Recommended implementation shape:

```python
class BaseCliRuntime:
    async def _drain_notifications(self) -> list[CronNotification]: ...


class AWorldCLI:
    async def start_idle_notification_poller(self, runtime): ...
    async def stop_idle_notification_poller(self): ...
```

The idle poller should:
- wake on a short interval (for example 0.5-1.0s)
- only render when the CLI is waiting for user input
- no-op while agent output is in progress
- render and clear pending notifications atomically

### 8.7 TUI Rendering Rules

`AWorldCLI` should provide a dedicated renderer, for example:

```python
def render_cron_notifications(self, notifications: list[CronNotification]) -> None:
    ...
```

UX rules:
- render as compact Rich panels or prefixed lines
- use color by status: green=`ok`, yellow=`timeout`, red=`error`
- cap visible batch size (for example 3), then print `... and N more cron notifications`
- include:
  - job name
  - terminal status
  - fixed-template short summary
  - next run time for recurring jobs when available

Example output:

```text
[Cron] Daily Benchmark completed
  next run: 2026-04-09T09:00:00+08:00

[Cron] Repo Health Check failed
  details: /cron list
```

Do not display raw `last_error` inline in the TUI notification.

### 8.8 Error Trace Persistence

Notification text is intentionally minimal, so full execution detail must remain inspectable elsewhere.

Required persisted detail:
- terminal status
- full error text when present
- last run time
- next run time

MVP acceptable options:

Option A: store-only
- persist full error in `CronJob.state.last_error`
- expose it through `/cron list`

Option B: store + run record file
- persist summary state in `.aworld/cron.json`
- write detailed per-run record to a file such as:
  - `.aworld/cron_runs/<job_id>/<timestamp>.json`
- optionally store `last_result_path` on the job state

Recommended record fields:
- `job_id`
- `job_name`
- `started_at`
- `finished_at`
- `status`
- `error`
- `result_summary`
- `next_run_at`

For MVP, Option A is sufficient if `/cron list` reliably surfaces `last_error`.

### 8.9 Persistence and Recovery Semantics

Notifications are **not** persisted across CLI restarts in MVP.

Rationale:
- persistent source of truth already exists in `.aworld/cron.json`
- keeping notification state in memory avoids a second durable queue
- after restart, user can inspect `/cron list` for terminal states

Recovery behavior:
- scheduler still recovers jobs from store
- no attempt is made to reconstruct or replay old TUI notifications

### 8.10 Relationship to Job State

Notification delivery is secondary to persisted scheduler state.

Source of truth remains:
- `.aworld/cron.json`
- `CronJob.state.last_status`
- `CronJob.state.last_error`
- `CronJob.state.last_run_at`
- optional per-run trace file if introduced

Notifications are only a UX layer over that state.

## 9. Execution Design

### 9.1 Core Rule

Execution must reuse `Runners.run()`:

```python
result = await Runners.run(
    input=job.payload.message,
    swarm=swarm,
    tool_names=job.payload.tool_names,
    session_id=None,
)
```

This matches current runner model and preserves isolated execution.

### 9.2 Agent Resolution

Do **not** use a fictional `get_agent_builder()`.

Current codebase should resolve agents through `LocalAgentRegistry`, then build or fetch a swarm via `LocalAgent.get_swarm()`.

Recommended executor logic:

```python
from aworld.runner import Runners
from aworld_cli.core.agent_registry import LocalAgentRegistry

class CronExecutor:
    def __init__(self, agent_registry: LocalAgentRegistry):
        self.agent_registry = agent_registry
        self._swarm_cache = {}

    async def execute(self, job: CronJob):
        swarm = await self._resolve_swarm(job.payload.agent_name)
        return await Runners.run(
            input=job.payload.message,
            swarm=swarm,
            tool_names=job.payload.tool_names,
            session_id=None,
        )

    async def _resolve_swarm(self, agent_name: str):
        if agent_name in self._swarm_cache:
            return self._swarm_cache[agent_name]

        local_agent = self.agent_registry.get(agent_name)
        if not local_agent:
            raise ValueError(f"Agent not found: {agent_name}")

        swarm = await local_agent.get_swarm()
        self._swarm_cache[agent_name] = swarm
        return swarm
```

### 9.3 Tool Semantics

`payload.tool_names` is a whitelist of Aworld tool names, not arbitrary sandbox action names.

Examples:
- `cron`
- `CAST_SEARCH`
- `async_spawn_subagent`

Do not document values like `read_file` unless they are confirmed to be valid top-level tool names in the current tool system.

## 10. Tool Design

### 10.1 Tool Location

Add `aworld/tools/cron_tool.py`.

This makes it discoverable by the current `aworld.tools` package scan.

### 10.2 Tool Contract

Use `@be_tool(tool_name="cron")`.

Supported actions:
- `add`
- `list`
- `remove`
- `run`
- `status`
- `enable`
- `disable`

`enable` / `disable` should be included in MVP because the store model already has `enabled`, and operationally this is cheaper than delete/recreate.

### 10.3 Async Tool Example

The tool function should be async:

```python
@be_tool(tool_name="cron", tool_desc="Manage scheduled tasks")
async def cron_tool(...)-> Dict[str, Any]:
    ...
```

### 10.4 Schedule Parsing Rules

- `at`: accept ISO8601 timestamp
- `every`: accept duration strings like `30m`, `2h`, `1d`
- `cron`: accept standard 5-field cron expression
- timezone defaults to `UTC`, but caller may set explicit timezone

## 11. Slash Command Design

### 11.1 Command File

Add:

```text
aworld-cli/src/aworld_cli/commands/cron_cmd.py
```

### 11.2 Registration Requirement

Also modify:

```text
aworld-cli/src/aworld_cli/commands/__init__.py
```

to import `cron_cmd`, otherwise the command will not register.

### 11.3 Command Type

`/cron` should remain a prompt command with:

```python
@property
def allowed_tools(self) -> List[str]:
    return ["cron"]
```

That keeps its execution constrained and consistent with the existing command system.

## 12. CLI Lifecycle Integration

### 12.1 Runtime Hook

Modify `aworld-cli/src/aworld_cli/runtime/base.py` to:
- create scheduler on runtime start
- create notification center on runtime start
- stop scheduler on runtime shutdown

### 12.2 Expected Behavior

When user enters `aworld-cli`:
- scheduler starts
- existing jobs are loaded and recovered
- pending in-memory notification queue starts empty

When user exits `aworld-cli`:
- scheduler stops
- jobs remain persisted
- no future runs happen until next startup
- in-memory notifications are discarded

### 12.3 TUI Notification Hook

Modify `aworld-cli/src/aworld_cli/console.py` / runtime interaction so that:
- notifications are rendered before next prompt
- notifications are rendered after slash command / agent execution finishes
- rendering never interrupts streaming output mid-turn

## 13. Scheduler Singleton

Recommended:

```python
_scheduler_instance = None

def get_scheduler(agent_registry=None) -> CronScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        store = FileBasedCronStore(".aworld/cron.json")
        executor = CronExecutor(agent_registry=agent_registry or default_registry())
        _scheduler_instance = CronScheduler(store, executor)
    return _scheduler_instance
```

Important:
- singleton construction must have access to the actual local agent registry
- do not hardcode imports to nonexistent helpers

## 14. Dependencies

### 14.1 New Dependencies

```txt
croniter>=1.4.0
```

`pytz` is not required for MVP if standard-library `zoneinfo` is used consistently.

### 14.2 Existing Dependencies

- `aworld.runner.Runners`
- `aworld.runners.task_runner.TaskRunner`
- `aworld_cli.core.agent_registry.LocalAgentRegistry`
- `aworld.tools` scan/registration flow

## 15. Implementation Plan

### Phase 1: Core Scheduler

Files:
- `aworld/core/scheduler/store.py`
- `aworld/core/scheduler/scheduler.py`
- `aworld/core/scheduler/executor.py`
- `aworld/core/scheduler/__init__.py`

Work:
- implement transactional file store
- implement schedule calculation
- implement due-claim semantics
- implement timeout / retry / concurrency

### Phase 2: CLI and Tool Integration

Files:
- `aworld/tools/cron_tool.py`
- `aworld-cli/src/aworld_cli/commands/cron_cmd.py`
- `aworld-cli/src/aworld_cli/commands/__init__.py`
- `aworld-cli/src/aworld_cli/runtime/base.py`
- `aworld-cli/src/aworld_cli/runtime/cron_notifications.py`
- `aworld-cli/src/aworld_cli/console.py`
- `aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/aworld_agent.py`

Work:
- expose `cron` tool to Aworld agent
- register `/cron`
- start/stop scheduler with CLI runtime
- add TUI notification center and renderer

### Phase 3: Validation

Work:
- add unit tests
- add integration tests
- run manual recovery scenarios

## 16. Testing Strategy

### 15.1 Unit Tests

Add:

```text
tests/core/scheduler/
├── test_types.py
├── test_store.py
├── test_executor.py
└── test_scheduler.py
```

Critical cases:
- schedule parsing
- next-run calculation
- claim-due-job is single-fire
- startup recovery clears stale running state
- one-shot jobs delete correctly
- manual trigger does not corrupt recurring schedule

### 15.2 Integration Tests

Add:

```text
tests/integration/test_cron_cli.py
```

Critical cases:
- `/cron list` works
- agent can call `cron` tool
- runtime starts scheduler
- jobs survive CLI restart
- completed cron job produces one TUI notification
- failed cron job produces one TUI notification
- idle prompt poller renders notification while user is waiting at prompt
- notifications do not render during agent streaming output
- notification body uses fixed template only (no raw error text)
- full error remains available via persisted job state or run record file
- notifications are not replayed after CLI restart

### 15.3 Manual Test Matrix

```bash
# Scenario 1
aworld-cli
> /cron add 每30分钟运行一次 git status 检查
> /cron list

# Scenario 2
aworld-cli
> /cron run <job_id>

# Scenario 3
# create a recurring job
# exit aworld-cli
# restart aworld-cli
# verify job is still present and next_run_at is recalculated

# Scenario 4
aworld-cli
> /cron add 1分钟后提醒我检查测试结果
# wait for execution
# verify TUI shows completion/failure notification while idle at prompt
# verify detailed error/result remains inspectable via /cron list
```

## 17. Known Limitations

1. CLI must stay alive for jobs to fire.
2. No offline catch-up replay.
3. Single-process only.
4. Notifications are TUI-only and in-memory only.
5. No delivery channel outside `aworld-cli` TUI.
6. No original-session continuation.
7. Notifications are not injected back into the agent loop automatically.
8. Near-real-time delivery is only guaranteed while idle at prompt, not during active streaming output.

## 18. Success Criteria

### Functional

- Can create `at`, `every`, `cron` jobs
- Can list, enable, disable, remove, and manually run jobs
- Jobs execute through `Runners.run()` in isolated mode
- Scheduler restarts cleanly with CLI

### Correctness

- Due jobs are not double-fired in one scheduler process
- Store writes do not lose in-process updates
- Restart recovery leaves jobs in a consistent state

### UX

- Agent can naturally create scheduled jobs using `cron` tool
- `/cron` works without exposing unrelated tools
- user sees a proactive TUI notification when a cron task reaches terminal state
- notification appears near-real-time while idle at prompt
- notification does not appear during streaming output
- notification uses fixed template text only
- detailed error remains inspectable through persisted state

## 19. Future Upgrade Path

When Aworld needs OpenClaw-like cron behavior, keep this job model and replace only the runtime layer:

1. move scheduler loop into a daemon process
2. keep `.aworld/cron.json` or migrate to DB
3. let CLI and agent tools operate as CRUD clients
4. optionally evolve TUI notifications into a durable inbox
5. add delivery hooks and catch-up policy

This preserves the MVP model work and avoids rewrite of job schema.

## 20. References

- OpenClaw cron: `/Users/wuman/Documents/workspace/openclaw/src/cron/`
- Current Aworld runtime: `aworld-cli/src/aworld_cli/runtime/base.py`
- Current Aworld agent registry: `aworld-cli/src/aworld_cli/core/agent_registry.py`
- Current slash command registry: `aworld-cli/src/aworld_cli/commands/__init__.py`

---

## 21. Next Step

Implement the CLI-embedded MVP exactly as documented here. Do not expand to daemon mode in the first pass.
