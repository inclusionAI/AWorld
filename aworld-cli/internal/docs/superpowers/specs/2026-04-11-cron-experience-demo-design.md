# Cron Experience Demo Design

## Goal

Create an independent demo under `examples/` that lets users fully experience AWorld's cron capability, using a workflow inspired by Claude Code scheduled tasks: one-time reminders, recurring tasks, task listing, asynchronous notifications, and cleanup.

## Scope

This design covers:

- A new standalone demo directory under `examples/`
- A manual walkthrough via `README.md`
- An automatic demo script that exercises the end-to-end cron flow quickly
- Demo-specific runtime helpers and outputs

This design does not cover:

- Changes to scheduler core behavior
- Changes to AWorld CLI production UX outside what the demo reuses
- Web UI or browser UI for cron management

## Background

The Claude Code scheduled tasks documentation models a complete task scheduling experience around:

- one-time reminders
- recurring tasks
- task listing and cancellation
- autonomous background execution

AWorld already has matching building blocks:

- `cron` tool for add/list/remove/run/enable/disable/status
- file-backed scheduler and job store
- CLI runtime startup wiring for scheduler
- notification center and console rendering

The missing piece is a self-contained example that demonstrates the capability end to end without making users reverse-engineer the current implementation from source.

## User Experience Goals

The demo should let a user understand, in less than a few minutes:

1. How to create a one-time reminder
2. How to create a recurring task
3. How to inspect task state and next-run time
4. How notifications appear when a task completes
5. How to clean up tasks afterward

The demo should support both:

- a transparent, step-by-step manual walkthrough
- a fast one-command automated showcase

## Proposed Directory

New directory:

- `/Users/wuman/Documents/workspace/aworld-mas/aworld/examples/cron_experience_demo/`

## Proposed Files

### `README.md`

Purpose:

- explain what the demo covers
- map Claude scheduled-task concepts onto AWorld cron concepts
- provide manual walkthrough steps
- explain how to run the auto demo
- explain what files are produced and how to clean them up

### `demo_setup.py`

Purpose:

- prepare a demo-specific runtime workspace
- create or reset a demo-specific `.aworld` directory
- ensure output directories exist
- keep the demo isolated from the user's normal cron data

### `demo_tasks.py`

Purpose:

- centralize definitions for the demo jobs
- describe:
  - one-time reminder task
  - recurring heartbeat task
  - output-writing task

This file should keep demo job construction declarative and reusable.

### `demo_runtime.py`

Purpose:

- wrap existing AWorld cron primitives into demo-friendly operations
- start or stop scheduler
- attach notification sink
- list current jobs
- drain and print notifications
- clean up demo-created jobs

This file should not reimplement scheduler logic. It should orchestrate existing runtime pieces for demonstration.

### `run_auto_demo.py`

Purpose:

- provide a single entry point for a short end-to-end automated experience
- initialize demo state
- create demo jobs
- run long enough to show execution and notifications
- print progress snapshots
- clean up at the end

### `outputs/`

Purpose:

- store demo-visible artifacts such as heartbeat logs or execution markers
- make cron effects tangible to the user

## Demo Experience Design

### Manual Walkthrough

The manual flow should mirror the mental model from the Claude scheduled-tasks docs:

1. Start the demo environment
2. Create a one-time reminder
3. Create a recurring task
4. List tasks and inspect status
5. Observe asynchronous notification delivery
6. Remove or disable tasks
7. Inspect output files

The manual path should emphasize that AWorld cron is not only for reminders; it can also run actual task payloads on a schedule.

### Automated Demo

The auto demo should finish quickly enough for interactive use. Target runtime:

- about 60 to 90 seconds total

Suggested demo jobs:

1. One-time reminder-style job
   - triggers once after a short interval
   - demonstrates reminder semantics

2. Recurring heartbeat job
   - runs every minute or every short supported interval
   - appends a line to an output file

3. Visible task-output job
   - runs a simple prompt or message flow that produces a visible artifact or summary

The script should periodically print:

- current job summaries
- newly received notifications
- latest output file state

The script should finish by cleaning up demo jobs it created.

## Isolation Strategy

The demo must not reuse the user's regular scheduler state by default.

It should isolate:

- cron store path
- output directory
- demo-created artifacts

This makes the example safe to run repeatedly and prevents confusion from pre-existing jobs.

## Mapping To Claude Scheduled Tasks Concepts

The README should include a small comparison section:

- Claude "Set a one-time reminder" → AWorld one-time cron task
- Claude "Manage scheduled tasks" → AWorld `/cron list`, `/cron remove`, `/cron disable`, `/cron enable`
- Claude recurring scheduled checks → AWorld recurring cron or interval tasks
- Claude background scheduled execution → AWorld scheduler + notification center

The point is not to clone Claude behavior exactly, but to make the conceptual bridge explicit.

## Runtime Design

The demo runtime should reuse existing AWorld cron components rather than shelling out to the CLI where possible.

Preferred approach:

- call scheduler APIs directly from Python
- attach the existing notification center or a demo-local equivalent
- print state transitions in a way that is easy to follow

This avoids brittle subprocess orchestration for the automated path while keeping the manual path documented through the real CLI commands.

## Output Design

The demo should make cron activity observable in three ways:

1. Console log lines
   - setup complete
   - jobs created
   - scheduler running
   - notification received
   - cleanup complete

2. Job state snapshots
   - id
   - name
   - next run
   - last status

3. Output files
   - heartbeat log
   - reminder marker or task result file

## Error Handling

The demo should handle these cases clearly:

- scheduler startup failure
- task creation failure
- no notification received during the expected demo window
- cleanup failure

For each case, the demo should print a clear message and avoid leaving half-created state when possible.

## Testing Strategy

The demo should be testable at the file-and-runtime-helper level without requiring a long real-time wait for every test.

Recommended tests:

1. Setup test
   - demo directories are created correctly
   - isolated paths are used

2. Task-definition test
   - demo jobs are created with expected schedule types and payloads

3. Runtime helper test
   - listing, notification draining, and cleanup helpers work against a temporary cron store

4. Lightweight smoke test
   - validate that the auto demo can initialize and create jobs without crashing

The README itself does not need automated testing, but the commands it documents should match the actual demo layout.

## Risks And Mitigations

### Risk: Demo Pollutes Real Cron State

Mitigation:

- isolate cron storage and outputs in the demo directory

### Risk: Demo Takes Too Long To Experience

Mitigation:

- keep the automated path short
- choose short intervals where supported
- surface intermediate progress so users see something immediately

### Risk: Demo Becomes CLI-Only Or Python-Only

Mitigation:

- keep both manual CLI walkthrough and direct Python automated demo

### Risk: Demo Reimplements Production Logic

Mitigation:

- wrap existing scheduler and notification components instead of duplicating them

## Acceptance Criteria

1. A new standalone demo exists under `examples/cron_experience_demo/`.
2. The demo includes both a manual walkthrough and an automated run path.
3. The experience demonstrates one-time reminder behavior, recurring execution, task listing, notifications, and cleanup.
4. The demo uses isolated scheduler state rather than the user's default cron store.
5. The demo makes cron execution observable through console output and generated artifacts.
