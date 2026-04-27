## Context

The external Ralph references show two distinct execution models that should not be conflated in the same first implementation slice:

1. In-session continuation loop
   - same interactive session
   - same conversational surface
   - loop continuation triggered by an exit interception point
   - persistence carried by workspace files, transcript, and lightweight loop state

2. Fresh-run orchestration loop
   - every iteration launches a new agent/tool process
   - continuity is carried by explicit artifacts such as `prd.json`, `progress.txt`, and git history
   - orchestration becomes its own runtime concern instead of an interactive CLI concern

The requested AWorld capability is explicitly the first model. The current AWorld plugin framework already supports the required host primitives:

- slash-style prompt commands
- `stop` hooks with `block_and_continue`
- session- or workspace-scoped plugin state
- HUD providers

The current `RalphRunner` is not the right primary abstraction for this phase because it is shaped as a runner-level loop executor, not as a CLI session control layer.

## Goals / Non-Goals

**Goals**

- Add a phase-1 Ralph capability as a standalone plugin.
- Keep the implementation in the CLI/plugin host layer.
- Reuse existing plugin `commands`, `hooks`, `state`, and `hud` contracts instead of introducing a Ralph-specific kernel API.
- Support explicit completion promises and iteration limits.
- Support declarative verification requirements that are stored structurally and injected into the agent prompt.
- Keep the plugin implementation compatible with future phase-2 evolution.

**Non-Goals**

- Do not build phase-1 on top of `RalphRunner`.
- Do not change `aworld/core` for phase 1.
- Do not implement fresh-process or fresh-session orchestration in phase 1.
- Do not execute verification commands inside the stop hook in phase 1.
- Do not add phase-1 loop-local runtime overrides such as `--model` or `--work-dir`.
- Do not introduce Claude-specific state files such as `.claude/ralph-loop.local.md`.
- Do not redesign the general plugin framework as part of this change.

## Decisions

### Decision: Phase 1 is a standalone plugin-hosted session loop

The first AWorld Ralph capability should be implemented as a normal plugin, not as a core runner feature.

The plugin should own these entrypoints:

- `commands/ralph-loop.md`
- `commands/cancel-ralph.md`
- `hooks/stop_hook.py`
- `hud/status.py`
- `.aworld-plugin/plugin.json`

Why:

- This matches the current requirement exactly: keep looping inside the current CLI session.
- The AWorld CLI already has the host behavior needed for continuation on `exit`.
- This keeps the phase-1 change focused, testable, and independent of runner internals.

Rejected alternative:

- Build the phase-1 interactive loop directly on `RalphRunner`.
  Rejected because `RalphRunner` is a task-execution loop, not a session-lifecycle controller.

### Decision: The stop hook is the only loop controller

The phase-1 control path should be:

1. `/ralph-loop` initializes loop state.
2. The current session executes the task.
3. When the operator attempts to exit, the `stop` hook evaluates whether the loop should continue.
4. If unfinished, the hook returns `block_and_continue` with a follow-up prompt.

The stop hook should be the only component allowed to decide whether the loop continues or exits.

Why:

- It keeps loop control in one place.
- It aligns with the current plugin hook contract.
- It avoids splitting continuation rules between command code, HUD code, and ad hoc session logic.

### Decision: Phase-1 state lives in plugin-scoped persisted state

The Ralph loop should persist through AWorld's plugin state store instead of writing a host-specific state file.

Recommended minimum state shape:

```json
{
  "active": true,
  "prompt": "Implement feature X",
  "iteration": 1,
  "max_iterations": 20,
  "completion_promise": "COMPLETE",
  "verify_commands": [
    "pytest tests/api -q",
    "ruff check ."
  ],
  "started_at": "2026-04-27T10:00:00Z",
  "last_stop_reason": null,
  "last_final_answer_excerpt": null
}
```

Scope:

- session scope is the default for the active loop controller
- no Claude-specific dotfile should be part of the stable phase-1 contract

Why:

- This keeps the plugin host-agnostic inside AWorld.
- The plugin framework already exposes persisted state APIs.
- It avoids introducing a second state persistence model just for Ralph.

### Decision: `/ralph-loop` stores structured verify requirements, but the hook does not run them

Phase-1 verification requirements should be declared structurally and then injected into the effective prompt.

Recommended user-facing contract:

```text
/ralph-loop "Implement the todo API" \
  --verify "pytest tests/api -q" \
  --verify "ruff check ." \
  --completion-promise "COMPLETE" \
  --max-iterations 20
```

The plugin should persist these `verify_commands` in plugin state and normalize the working prompt into a task package similar to:

```text
Task:
Implement the todo API

Verification requirements:
1. Run: pytest tests/api -q
2. Run: ruff check .

Completion rule:
Only output <promise>COMPLETE</promise> when every verification requirement passes.
If verification fails, fix the issue and continue iterating.
```

Why:

- It gives the operator a structured way to express machine-checkable expectations without turning the stop hook into a mini-runner.
- It preserves a clean migration path to phase-2 orchestrated verification.
- It keeps phase-1 aligned with the interactive session-loop boundary.

Rejected alternative:

- Have the stop hook execute verification commands itself.
  Rejected because it would blur the line between loop control and task execution orchestration.

### Decision: Phase-1 stop conditions remain intentionally small

The stop hook should only enforce these phase-1 conditions:

1. no active loop -> allow exit
2. `max_iterations` reached -> allow exit and clear state
3. `<promise>...</promise>` matches `completion_promise` exactly -> allow exit and clear state
4. otherwise -> `block_and_continue`

The hook may record diagnostic metadata such as the last final answer excerpt, but it should not accumulate richer policy in phase 1.

Why:

- Minimal policy is easier to trust and debug.
- The operator prompt and verify contract remain the main behavior driver.
- Larger policy surfaces belong in a later orchestration phase if needed.

### Decision: HUD is observational only

HUD should display status but never own control flow.

Recommended fields:

- `Ralph: active` or `inactive`
- `Iter: 3/20` or `3/unbounded`
- `Promise: COMPLETE` or `none`

Why:

- HUD should help the operator understand the loop state at a glance.
- Control decisions already belong to the stop hook.

### Decision: Phase 2 fresh-run orchestration stays separate from this change

This change should explicitly defer fresh-run orchestration.

Phase 2 may later evaluate:

- a standalone orchestrator under `aworld-cli`
- selective reuse of shared Ralph concepts
- selective reuse of parts of `RalphRunner`

But phase 2 should not be implied as a guaranteed `RalphRunner` integration.

Why:

- The current `RalphRunner` and the desired fresh-run orchestrator are related but not identical abstractions.
- Prematurely promising a runner dependency would constrain the later design before real requirements are documented.

## Plugin Contract

### Command Contract

`/ralph-loop`

- accepts task prompt text
- accepts repeatable `--verify`
- accepts optional `--completion-promise`
- accepts optional `--max-iterations`
- initializes or replaces the active Ralph session state
- emits a confirmation message describing the active loop policy

Explicitly deferred from the phase-1 command surface:

- `--model`
- `--work-dir`

`/cancel-ralph`

- clears the active Ralph loop state
- emits a confirmation message describing that the loop has been cancelled

### Hook Contract

The stop hook:

- reads the plugin state
- reads current stop-related event payload and available transcript context
- updates iteration and diagnostic fields when continuing
- returns one of:
  - `allow`
  - `block_and_continue`
  - `deny` only for exceptional corruption or invalid-state cases

### HUD Contract

The HUD provider reads plugin state and renders status lines only.

## Risks / Trade-offs

- **Prompt-only verification can be gamed by the model**
  This is accepted in phase 1 because the goal is an interactive loop plugin, not a hardened orchestrator.

- **Transcript-driven completion detection may be approximate**
  Phase 1 should keep the completion rule intentionally narrow and explicit to reduce ambiguity.

- **Standalone plugin logic may later want shared helpers**
  This is acceptable as long as the first change keeps those helpers in the CLI/plugin host layer rather than forcing runner coupling.

## Validation Approach

Phase-1 validation should cover:

- command registration and manifest loading
- `/ralph-loop` state initialization
- `/cancel-ralph` state clearing
- stop-hook `block_and_continue` behavior
- exact completion-promise match behavior
- max-iteration stop behavior
- HUD rendering from plugin state
- verify requirement normalization into the effective follow-up prompt

Recommended simple acceptance cases for phase 1:

- default unbounded loop:
  `/ralph-loop "Build a Python course"`
- explicit iteration cap:
  `/ralph-loop "Build a REST API" --max-iterations 5`
- declarative verification:
  `/ralph-loop "Create a CLI tool" --verify "pytest tests/cli -q" --completion-promise "COMPLETE"`

Examples intentionally not adopted as phase-1 acceptance cases:

- model override cases such as `--model claude-sonnet-4-6`
- working-directory override cases such as `--work-dir ./my-project`

Those cases belong to a later orchestration/runtime-configuration phase, not to the standalone session-loop plugin slice.
