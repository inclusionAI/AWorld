# Ralph Session Loop

This document explains how to use the built-in Ralph session-loop plugin in AWorld CLI.

## What It Does

The Ralph plugin keeps a task running inside the **current interactive CLI session**.

The flow is:

1. Start a loop with `/ralph-loop`
2. Let the agent work on the task
3. Type `exit`
4. The plugin `stop` hook intercepts the exit
5. If the loop is not finished, the same task is fed back as a follow-up prompt
6. The loop continues until completion, cancellation, or iteration limit

This is the phase-1 interactive Ralph model. It is **not** the fresh-process orchestration model.

## Boundary With RalphRunner

The Ralph session-loop plugin and `RalphRunner` are intentionally different layers.

- the Ralph plugin is an `aworld-cli` capability for continuing work across an interactive session
- `RalphRunner` is an `aworld` framework capability for running Ralph-style convergence inside task execution

The phase-1 plugin does **not** call `RalphRunner`, wrap `RalphRunner`, or depend on `RalphRunner`.

The intended boundary is:

- outer plugin controls whether the current CLI session should continue into another round
- inner runner controls whether a single task execution has converged

That means Ralph support in AWorld is **not** limited to the CLI plugin. Framework users can still use Ralph through runner-level APIs, while CLI users can use the session-loop plugin as a separate interaction model.

For the framework-side runtime, see [Ralph Runner](../../Agents/Runtime/Ralph%20Runner.md).

## Commands

Start a Ralph loop:

```text
/ralph-loop "Build a REST API" --max-iterations 5
```

Start a Ralph loop with declarative verification:

```text
/ralph-loop "Create a CLI tool" --verify "pytest tests/cli -q" --completion-promise "COMPLETE"
```

Cancel the active loop:

```text
/cancel-ralph
```

## Supported Arguments

`/ralph-loop` supports:

- prompt text
- repeatable `--verify`
- optional `--completion-promise`
- optional `--max-iterations`

Examples:

```text
/ralph-loop "Build a Python course"
```

```text
/ralph-loop "Build a REST API" --max-iterations 5
```

```text
/ralph-loop "Create a CLI tool" --verify "pytest tests/cli -q" --verify "ruff check ." --completion-promise "COMPLETE"
```

## How Verification Works

Phase 1 uses **declarative verification**, not stop-hook-executed verification.

That means:

- `--verify` values are stored in plugin state
- they are injected into the effective task prompt
- the agent is instructed to run them before claiming completion
- the plugin itself does not execute those commands inside the stop hook

For example:

```text
/ralph-loop "Create a CLI tool" --verify "pytest tests/cli -q" --completion-promise "COMPLETE"
```

The follow-up prompt will include sections similar to:

```text
Task:
Create a CLI tool

Verification requirements:
1. Run: pytest tests/cli -q

Completion rule:
Only output <promise>COMPLETE</promise> when every verification requirement passes.
```

## Completion Behavior

If you set a completion promise, the loop stops only when the latest final answer contains the exact promise tag.

Example:

```text
/ralph-loop "Create a CLI tool" --completion-promise "COMPLETE"
```

Required completion output:

```text
<promise>COMPLETE</promise>
```

The match is exact and case-sensitive.

## Iteration Limit Behavior

If you set `--max-iterations`, the loop stops once the recorded iteration reaches that value.

Example:

```text
/ralph-loop "Build a REST API" --max-iterations 5
```

Expected behavior:

- first few `exit` attempts continue the loop
- once iteration `5` is reached, `exit` is allowed

Important:

- plugin `--max-iterations` applies only to the outer session loop
- it does not override or configure `RalphRunner` internal `max_iterations`
- if a task is ever executed by a runner with its own internal iteration cap, that cap remains an inner execution concern

## Typical Workflow

1. Start `aworld-cli`
2. Run a Ralph command
3. Let the agent complete one pass
4. Type `exit`
5. If the task is incomplete, the loop continues
6. Repeat until:
   - the completion promise is satisfied
   - the iteration limit is reached
   - you run `/cancel-ralph`

## HUD

When the loop is active, the HUD can show summary state such as:

- `Ralph: active`
- `Iter: 2/5`
- `Promise: COMPLETE`

If no loop is active, the HUD shows `Ralph: inactive`.

## Current Limits

The current phase-1 plugin does **not** support:

- fresh-process Ralph orchestration
- `--model`
- `--work-dir`
- stop-hook-executed verification commands
- advanced multi-branch planning artifacts like `prd.json` and `progress.txt`

Those belong to a later phase.

## Manual Smoke Check

Try this:

```text
/ralph-loop "Build a REST API" --verify "pytest tests/cli -q" --completion-promise "COMPLETE" --max-iterations 3
```

Then:

1. let the agent answer once
2. type `exit`

Expected:

- the session does not exit immediately
- a Ralph iteration message is shown
- the task is continued with a follow-up prompt

Then cancel:

```text
/cancel-ralph
```

Type `exit` again.

Expected:

- the session exits normally

## Troubleshooting

If `exit` does not continue the task:

- confirm that `/ralph-loop` was started in the current session
- confirm that the loop was not already cancelled
- confirm that `--max-iterations` was not already reached
- confirm that the completion promise was not already satisfied

If the loop never completes:

- use a smaller task
- add one or more `--verify` commands
- add an explicit `--completion-promise`
- set a reasonable `--max-iterations`
