# Task lifetime and goal continuation

Task lifetime is unbounded unless the caller supplies a budget. This contract
applies to ordinary CLI work and direct Task callers independently of the goal
plugin. `Task.timeout`
defaults to `None`; `LocalAgentExecutor.chat()` and `/goal` have no default
aggregate duration or turn limit. Model-generation, tool-call, hook, transport,
and trajectory-finalization liveness timeouts remain separate operations.

## Explicit caller limits

- `Task(timeout=seconds)` binds a positive finite duration when constructed.
- `Task(deadline_epoch_seconds=epoch)` supplies a portable absolute Unix deadline.
- When both exist, the earliest wins. A child cannot extend its parent's deadline.
- A live task also retains a monotonic upper bound, so wall-clock rollback cannot
  renew it. Repeated binding, runner recreation, and a larger replacement timeout
  do not extend the bound. Serialize and restore `deadline_epoch_seconds` along
  with the task identity; restoring only the original duration creates a new task.
- Invalid explicit values (including zero durations, negative values, NaN,
  infinity, booleans, and non-numeric values) fail closed. Migrate the historical
  `timeout=0` sentinel to `None`. An epoch of zero is valid and already expired.
- CLI honors `AWORLD_TASK_DEADLINE_EPOCH_SECONDS`, the same caller contract used by
  the terminal server. An absent variable is unbounded; an invalid one is an error.
  `chat(timeout=..., deadline_epoch_seconds=...)` is also available to API callers.

The event runner checks the bound while bootstrapping and while waiting for events,
not only after a model or tool returns. `Task.request_pause()` and
`Task.request_cancel()` stop waiting/running execution regardless of a deadline.
Finalization joins the existing terminal snapshot under repeated cancellation;
it does not replace a primary provider/bootstrap failure with a later cancellation.

## Goal continuation

`/goal ... --max-turns N`, `--timeout-seconds N`, and
`--deadline-epoch-seconds EPOCH` are explicit optional budgets. Goal state persists
the absolute deadline, turn count, last task identity/epoch, and outcome. `/goal
pause` preserves state and interrupts execution; `/goal clear` interrupts and
discards it. `/goal resume` uses the same session and original deadline. It cannot
renew an exhausted budget. A new explicit goal is required to replace that budget.

Continuation is iterative, not recursive. Each turn gets its own task/trajectory
identity and the same aggregate deadline. Trusted goal continuation explicitly
migrates the prior work ledger via `carry_goal_work_state`; durable resume uses
the persisted source identity and `resume_goal_work_state`. Historical observations
are retained as history, never promoted to fresh verification in the new turn.

The completion control plane is `TaskResponse.semantic_status`,
`completion_reason`, and `recoverable`. `incomplete` or `budget_exhausted` cannot
become success merely because a FINISHED message or final prose exists. Goal
continuation is allowed for recoverable incomplete work; a non-recoverable result
pauses it or marks its explicit budget exhausted. An expired deadline always wins
over a textual promise. Typed success completes a goal without a promise; when a
promise is specified it must match and the control plane must not be incomplete.

These mechanisms do not assert a benchmark reward improvement. The scored result
still requires a new qualified execution and the unmodified verifier.
