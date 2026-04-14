# AWorld Cron Runner Supervision Design

Date: 2026-04-09
Status: Proposed
Owner: Codex

## 1. Background

The current cron execution model is coupled to the `aworld-cli` interactive runtime.

- `aworld-cli` starts the scheduler when the CLI runtime starts.
- `aworld-cli` stops the scheduler when the CLI runtime exits.
- Cron completion notifications are delivered through an in-memory, process-local queue.

This means scheduled jobs do not continue to execute after `aworld-cli` exits, and a dead or hung CLI process can effectively stop cron execution until the user starts the CLI again.

The existing scheduler implementation already has several properties that are useful for a service model:

- persistent job storage
- atomic due-job claiming
- startup recovery for stale `running` state
- recalculation of `next_run_at` on startup

The missing capability is not schedule persistence. The missing capability is a persistent process model.

## 2. Problem Statement

We need cron execution to continue when `aworld-cli` is not running.

More specifically:

- scheduled tasks must keep running after the interactive CLI exits
- if the cron execution process crashes, it must be restarted automatically
- if the cron execution process is alive but unhealthy, the system must detect that and recover
- the CLI should remain a control plane, not the only execution host

## 3. Goals

- Decouple cron scheduling from `aworld-cli` lifecycle
- Introduce a dedicated cron runner process
- Provide a minimal health contract that supports supervision and restart decisions
- Support OS-level service management through `launchd` and `systemd`
- Preserve current file-based cron store and scheduler recovery behavior
- Preserve current `/cron` task management UX as much as possible

## 4. Non-Goals

- Building a full cross-platform process supervisor inside `aworld-cli`
- Replacing the existing cron store with a database in the first iteration
- Designing a distributed multi-node cron system
- Adding catch-up replay for missed interval runs during downtime
- Reworking the scheduler execution semantics beyond what is needed for service mode

## 5. User Outcome

After this design is implemented:

- users can add and manage cron jobs from `aworld-cli`
- cron jobs keep running even when `aworld-cli` is closed
- if the runner crashes, the operating system restarts it automatically
- when users run `/cron status`, they can see both job state and runner health

## 6. Options Considered

### Option A: Keep scheduler inside `aworld-cli` and add health checks

Description:
Add health probes to the current CLI-managed scheduler and try to restart parts of the runtime from inside the CLI process.

Pros:

- minimal code movement
- smallest short-term change

Cons:

- does not solve the core issue if `aworld-cli` is not running
- mixes interactive UX with background service responsibilities
- still leaves the CLI as a single point of liveness

Decision:
Rejected. This improves observability but does not provide the required execution model.

### Option B: Add an internal `aworld-cli supervisor` process

Description:
Create a long-running CLI-managed supervisor mode that spawns and watches a cron runner.

Pros:

- unified product surface
- portable supervision logic can be written once in Python

Cons:

- still requires an outer long-running process to exist
- if the supervisor dies, there is no reliable recovery without OS help
- duplicates service management that `launchd` and `systemd` already provide well

Decision:
Rejected for v1. This may be useful later as a convenience wrapper, but it should not be the primary reliability model.

### Option C: Dedicated cron runner plus OS supervision

Description:
Move scheduling to a dedicated, long-running runner process and rely on `launchd` or `systemd` for automatic restart. Add a health/status contract so the runner can expose whether it is healthy or degraded.

Pros:

- directly solves the lifecycle problem
- aligns with existing scheduler persistence and recovery behavior
- keeps responsibilities clear: CLI is control plane, runner is execution plane, OS is supervisor
- simplest reliable model in production

Cons:

- requires service installation and management
- introduces another process boundary

Decision:
Recommended.

## 7. Selected Design

Adopt Option C.

The system is split into three layers:

1. `aworld-cli`
   - user-facing control plane
   - manages cron jobs
   - reads runner health and status
   - renders notifications and status in the terminal

2. `cron-runner`
   - dedicated long-running process
   - owns `CronScheduler.start()`
   - executes scheduled jobs
   - writes health state and durable notification events

3. OS supervisor
   - `launchd` on macOS
   - `systemd` on Linux
   - restarts the runner when it exits unexpectedly or becomes unhealthy

## 8. Architecture

### 8.1 Process Model

Current:

- interactive CLI process starts scheduler
- interactive CLI process stops scheduler

Target:

- interactive CLI never owns scheduler lifetime
- a single dedicated runner process owns scheduler lifetime
- the runner starts independently of any interactive session

This design makes cron execution service-based instead of session-based.

### 8.2 Data Plane

The existing file-based cron store remains the source of truth for jobs.

V1 keeps:

- file-based store
- file locking
- atomic writes
- atomic due-job claiming
- existing startup recovery behavior

This avoids unnecessary risk while changing only the process model.

### 8.3 Health Plane

The runner exposes a minimal health contract designed for supervision, not for rich diagnostics.

V1 transport:

- write a local status file at `.aworld/cron-runner-health.json`
- `aworld-cli cron-runner health --json` reads and prints that file
- `aworld-cli cron-runner status` renders a human-readable view of the same state

V1 does not introduce a local HTTP server or Unix socket. A status file is simpler, avoids another always-on listener, and is sufficient for a single-host supervision model.

Required fields:

- `process_alive`
- `scheduler_running`
- `last_tick_at`
- `tick_lag_seconds`
- `store_read_ok`
- `jobs_total`
- `jobs_enabled`
- `jobs_running`
- `next_due_at`
- `status` with values `healthy`, `degraded`, or `unhealthy`
- `degraded_reasons`

Semantics:

- `healthy`: process is alive, scheduler loop is running, tick lag is under threshold, store access works
- `degraded`: process is alive but one or more critical signals are outside normal bounds
- `unhealthy`: process cannot safely continue and should exit to allow supervisor restart

### 8.4 Notification Plane

The current in-memory notification queue is insufficient for a daemonized model because it is process-local and non-persistent.

V1 changes notification delivery to two layers:

- durable event log written by the runner
- optional in-memory fast path for a currently connected interactive CLI

Recommended durable format for v1:

- append-only JSON Lines file under `.aworld/cron-events.jsonl`

Retention policy for v1:

- retain the newest 1000 events
- when the file grows beyond 1200 events, compact it back to the newest 1000 events

This keeps the implementation simple while bounding disk growth.

Each event should include:

- event id
- job id
- job name
- terminal status
- summary
- created at
- next run at

The CLI can read and render unseen events when it starts, which allows users to see cron outcomes even if the CLI was offline when the job completed.

## 9. External Interfaces

### 9.1 New Commands

Add runner-oriented commands:

- `aworld-cli cron-runner start`
- `aworld-cli cron-runner status`
- `aworld-cli cron-runner health --json`
- `aworld-cli cron-runner install-service`
- `aworld-cli cron-runner uninstall-service`

These commands are for local service management and inspection.

The `status` and `health --json` commands read runner state from the local health status file in v1.

### 9.2 Existing `/cron` Behavior

Keep existing `/cron add`, `/cron list`, `/cron remove`, `/cron enable`, `/cron disable`, and `/cron status`.

Update semantics:

- task CRUD continues to operate on the persistent store
- `/cron status` shows both scheduler/job information and runner health information
- interactive runtime no longer starts or stops the scheduler automatically

## 10. Runner Responsibilities

The runner is responsible for:

- bootstrapping the cron scheduler
- publishing durable cron completion events
- updating health state on every schedule loop tick
- self-reporting degraded or unhealthy states
- exiting with non-zero status when recovery should be delegated to the OS supervisor

The runner is not responsible for:

- interactive chat UX
- slash command parsing
- replacing OS service managers

## 11. Health and Recovery Rules

### 11.1 Liveness Signals

The runner must update `last_tick_at` whenever the main scheduling loop completes a successful cycle.

Suggested rule:

- if `now - last_tick_at` exceeds a configured threshold, mark the runner as `degraded`
- if the lag exceeds a higher threshold or initialization fails irrecoverably, mark `unhealthy` and exit

### 11.2 Store Failures

If the cron store cannot be read:

- mark `store_read_ok = false`
- transition to `degraded` on first transient failures
- transition to `unhealthy` and exit if the store remains unavailable past a configured limit

### 11.3 Startup Recovery

Runner startup should continue to use the current scheduler recovery sequence:

- clear stale `running` flags
- recalculate `next_run_at` for enabled jobs
- begin normal scheduling loop

This preserves the existing behavior while making it available after runner restart.

## 12. Supervisor Model

Service management should be delegated to the operating system.

### 12.1 macOS

Use `launchd` to install and supervise the runner.

Responsibilities:

- keep the runner alive
- restart it after unexpected exit
- optionally run health probes through a small wrapper if needed

### 12.2 Linux

Use `systemd` to install and supervise the runner.

Responsibilities mirror the macOS path.

### 12.3 Why OS Supervision

OS service managers already provide:

- restart policies
- boot-time startup
- logging integration
- operator familiarity

Reimplementing this inside the CLI would increase complexity without increasing reliability.

Service installation policy for v1:

- installation is opt-in
- cron job creation should not silently install or start background services
- the CLI may print a recommendation when jobs are created and no runner service is installed

## 13. Migration Plan

### Phase 1: Runner extraction

- introduce runner entry point
- move scheduler ownership out of interactive runtime
- keep current store and executor

### Phase 2: Health contract

- add runner health state
- expose local `status` and `health --json`
- define `healthy`, `degraded`, `unhealthy` thresholds

### Phase 3: Durable notifications

- add append-only event log
- let CLI render unread events on startup and during sessions

### Phase 4: Service installation helpers

- add `install-service` and `uninstall-service`
- generate `launchd` and `systemd` units with conservative defaults

### Phase 5: `/cron status` integration

- merge job-level state and runner-level state into one status view

## 14. Testing Strategy

### Unit Tests

- runner health state transitions
- tick lag threshold logic
- durable event writing and replay
- CLI status rendering with healthy and degraded runner states

### Integration Tests

- runner starts and executes due jobs without interactive CLI
- runner restart performs startup recovery correctly
- `/cron status` reports runner health accurately
- durable notifications survive CLI absence and process restart

### Manual Verification

- install runner service
- create recurring job
- close CLI
- verify job continues to run
- kill runner process
- verify OS supervisor restarts it
- verify subsequent jobs continue to execute

## 15. Risks and Mitigations

Risk:
Two runner instances may start accidentally.

Mitigation:
Keep a single-runner lock or pid/lock-file guard in addition to store claim protection.

V1 choice:

- create `.aworld/cron-runner.pid`
- acquire an exclusive lock on the pid file for the lifetime of the runner
- if the lock cannot be acquired, the second runner exits immediately with an error

Risk:
Health state may say "alive" while the loop is stalled.

Mitigation:
Track `last_tick_at` and define explicit stall thresholds.

Risk:
Notification event log grows indefinitely.

Mitigation:
Add rotation or retention policy in a later step. V1 may use bounded retention or periodic compaction.

Risk:
Service installation differs across operating systems.

Mitigation:
Keep platform-specific templates small and generated from one internal model.

## 16. Recommendation

Proceed with a dedicated `cron-runner` process supervised by `launchd` or `systemd`, and keep `aworld-cli` as the control plane.

This directly addresses the failure mode where cron execution stops when the CLI exits, while preserving the existing scheduler and store implementation as much as possible.
