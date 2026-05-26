# Ralph Session Loop

This document explains how the built-in Ralph compatibility commands work in AWorld CLI.

## What It Does

The Ralph surface remains:

- `/ralph-loop` to start work
- `/cancel-ralph` to discard Ralph-owned loop state

Under the hood, those commands now write into the shared `goal-session` contract. That shared contract owns:

- persisted goal state
- continuation prompts
- completion and turn-budget status
- exit protection while a goal is still active

This is still the phase-1 interactive model. It is not fresh-process orchestration.

## Control Flow

The actual control flow is:

1. `/ralph-loop` creates a goal-session record with source `ralph_compat`
2. The agent completes a task pass
3. The `goal-session` `task_completed` hook decides whether to continue immediately
4. If the goal is still active, trying to `exit` is denied until you pause or clear it
5. `/cancel-ralph` clears only Ralph-owned active goal state

The important change is that continuation no longer comes from the Ralph `stop` hook. Continuation comes from the shared goal-session lifecycle hooks, while the goal-session `stop` hook only protects against accidental exit.

## Commands

Start a Ralph loop:

```text
/ralph-loop "Build a REST API" --max-iterations 5
```

Start a Ralph loop with declarative verification:

```text
/ralph-loop "Create a CLI tool" --verify "pytest tests/cli -q" --completion-promise "COMPLETE"
```

Cancel the active Ralph-owned loop:

```text
/cancel-ralph
```

Inspect or control the shared goal state:

```text
/goal status
/goal pause
/goal clear
```

`/cancel-ralph` is intentionally narrower than `/goal clear`: it only clears active state that was created by `/ralph-loop`.

## Goal Contract

`/ralph-loop` now produces a persisted goal contract shaped like:

```text
<goal_contract>
Objective: Build a REST API
Status: active
Turns: 1/5
Source: ralph_compat
Verification commands:
1. pytest tests/api -q
Completion promise: COMPLETE
</goal_contract>
```

That prompt is what the agent sees when the shared goal-session hook decides another turn is needed.

## Verification

Phase 1 still uses declarative verification:

- `--verify` values are stored structurally
- they are injected into the goal contract
- the agent is told to run them before claiming completion
- the plugin does not execute those commands itself

The completion promise remains exact and case-sensitive:

```text
<promise>COMPLETE</promise>
```

## Turn Limits

`--max-iterations` on `/ralph-loop` maps to the shared goal-session turn budget.

Example:

```text
/ralph-loop "Build a REST API" --max-iterations 5
```

Behavior:

- unfinished turns continue automatically through the task-completed hook
- once turn `5` finishes without the promise, the goal becomes `budget_limited`
- after that point, no further continuation is triggered

## Exit Behavior

If a goal is still active, `exit` is denied with guidance to either:

- `/goal pause` to keep the goal for later
- `/goal clear` to discard it

If the goal is already `complete`, `paused`, or `budget_limited`, normal exit is allowed.

## HUD

The shared HUD now reports goal-session state rather than Ralph-specific labels. Typical segments are:

- `Goal: active`
- `Turns: 2/5`
- `Verify: 1`

## Boundary With RalphRunner

The Ralph command surface and `RalphRunner` are still separate layers.

- the CLI commands manage interactive session state
- `RalphRunner` manages convergence inside task execution

The phase-1 compatibility layer does not call `RalphRunner`, wrap it, or depend on it.
