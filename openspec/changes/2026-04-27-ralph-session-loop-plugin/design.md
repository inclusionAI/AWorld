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
- Do not introduce Claude-specific state files such as `.claude/goal.local.md`.
- Do not redesign the general plugin framework as part of this change.

## Decisions

### Decision: Phase 1 is a standalone plugin-hosted session loop

The first AWorld Ralph capability should be implemented as a normal plugin, not as a core runner feature.

The plugin should own these entrypoints:

- `hooks/stop.py`
- `hooks/task_completed.py`
- `hooks/task_error.py`
- `hooks/task_interrupted.py`
- `hud/status.py`
- `.aworld-plugin/plugin.json`

Why:

- This matches the current requirement exactly: keep looping inside the current CLI session.
- The AWorld CLI already has the host behavior needed for continuation on `exit`.
- This keeps the phase-1 change focused, testable, and independent of runner internals.

Rejected alternative:

- Build the phase-1 interactive loop directly on `RalphRunner`.
  Rejected because `RalphRunner` is a task-execution loop, not a session-lifecycle controller.

### Decision: Ralph plugin and RalphRunner remain parallel abstractions

Phase 1 must preserve a clear layer boundary between the CLI Ralph plugin and framework-level `RalphRunner`.

Boundary definition:

- the Ralph plugin is an `aworld-cli` interaction-layer capability
- `RalphRunner` is an `aworld` framework execution-layer capability
- the plugin controls whether the current session continues into another round
- the runner controls whether a single task execution has converged

Implications:

- the phase-1 plugin must not invoke `RalphRunner` implicitly
- the plugin must not treat `RalphRunner` as the only way AWorld can expose Ralph semantics
- plugin `--max-turns` applies only to session continuation
- runner `completion_criteria.max_iterations` applies only to inner task execution
- phase 1 does not define a priority or override relationship between those two limits

Why:

- This keeps the CLI interaction model independent from framework runtime choices.
- It avoids hidden coupling between session control and task execution.
- It preserves two valid adoption paths: interactive CLI Ralph and framework/programmatic Ralph.

### Decision: Goal-session state is the only loop controller

The phase-1 control path should be:

1. `/goal "..."` initializes loop state.
2. The current session executes the task.
3. The shared `goal-session` task lifecycle hooks update turn state and decide whether unfinished work should continue immediately.
4. When the operator attempts to exit, the `goal-session` `stop` hook only decides whether exit is safe, paused, or should be denied.

The `goal-session` persisted state should be the only source of truth for whether the loop is active, complete, paused, or budget-limited.

Why:

- It keeps loop control in one place even when multiple user-facing commands share the same contract.
- It allows Ralph compatibility commands and future goal-native commands to reuse the same persisted state.
- It keeps stop-hook behavior small and focused on safe exit handling.

### Decision: Phase-1 state lives in plugin-scoped persisted state

The Ralph loop should persist through AWorld's plugin state store instead of writing a host-specific state file.

Recommended minimum state shape:

```json
{
  "active": true,
  "status": "active",
  "objective": "Implement feature X",
  "turn_count": 1,
  "max_turns": 20,
  "completion_promise": "COMPLETE",
  "verification_commands": [
    "pytest tests/api -q",
    "ruff check ."
  ],
  "source": "goal",
  "started_at": "2026-04-27T10:00:00Z",
  "last_task_status": "initialized",
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

### Decision: `/goal` stores structured verify requirements in goal-session state, but hooks do not run them

Phase-1 verification requirements should be declared structurally and then injected into the effective prompt.

Recommended user-facing contract:

```text
/goal "Implement the todo API" \
  --verify "pytest tests/api -q" \
  --verify "ruff check ." \
  --completion-promise "COMPLETE" \
  --max-turns 20
```

The plugin should persist these `verification_commands` in goal-session state and normalize the working prompt into a goal contract similar to:

```text
<goal_contract>
Objective: Implement the todo API
Status: active
Turns: 1/20
Source: goal
Verification commands:
1. pytest tests/api -q
2. ruff check .
Completion promise: COMPLETE
Only emit <promise>COMPLETE</promise> when the objective is fully complete and every verification command passes.
</goal_contract>
```

Why:

- It gives the operator a structured way to express machine-checkable expectations without turning the stop hook into a mini-runner.
- It preserves a clean migration path to phase-2 orchestrated verification.
- It keeps phase-1 aligned with the interactive session-loop boundary.

Rejected alternative:

- Have the stop hook execute verification commands itself.
  Rejected because it would blur the line between loop control and task execution orchestration.

### Decision: Phase-1 continuation and stop conditions remain intentionally small

The task-completed hook should only enforce these phase-1 continuation conditions:

1. no active goal -> allow
2. `<promise>...</promise>` matches `completion_promise` exactly -> mark `complete` and stop continuing
3. `max_turns` reached -> mark `budget_limited` and stop continuing
4. otherwise -> increment `turn_count` and `block_and_continue`

The stop hook should only enforce these phase-1 exit conditions:

1. no active goal -> allow exit
2. active goal -> deny exit and direct the operator to `/goal pause` or `/goal clear`

Hooks may record diagnostic metadata such as the last final answer excerpt, last error excerpt, or last partial answer excerpt, but they should not accumulate richer policy in phase 1.

Why:

- Minimal policy is easier to trust and debug.
- The operator prompt and verify contract remain the main behavior driver.
- Larger policy surfaces belong in a later orchestration phase if needed.

### Decision: HUD is observational only

HUD should display status but never own control flow.

Recommended fields:

- `Goal: active`, `paused`, `budget_limited`, or `complete`
- `Turns: 3/20` or `3/unbounded`
- `Verify: 2`

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

`/goal`

- accepts task prompt text
- accepts repeatable `--verify`
- accepts optional `--completion-promise`
- accepts optional `--max-turns`
- initializes or replaces the active Ralph session state
- emits a confirmation message describing the active loop policy

Explicitly deferred from the phase-1 command surface:

- `--model`
- `--work-dir`

`/goal clear`

- clears the active goal loop state
- emits a confirmation message describing that the loop has been cleared

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
- `/goal` state initialization
- `/goal clear` state clearing
- `/goal` status, pause, and clear behavior
- task-completed continuation behavior
- exact completion-promise match behavior
- max-turn termination behavior
- stop-hook deny behavior for active goals
- HUD rendering from plugin state
- verify requirement normalization into the effective follow-up prompt

Recommended simple acceptance cases for phase 1:

- default unbounded loop:
  `/goal "Build a Python course"`
- explicit iteration cap:
  `/goal "Build a REST API" --max-turns 5`
- declarative verification:
  `/goal "Create a CLI tool" --verify "pytest tests/cli -q" --completion-promise "COMPLETE"`

Examples intentionally not adopted as phase-1 acceptance cases:

- model override cases such as `--model claude-sonnet-4-6`
- working-directory override cases such as `--work-dir ./my-project`

Those cases belong to a later orchestration/runtime-configuration phase, not to the standalone session-loop plugin slice.
