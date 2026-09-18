# Task lifetime and goal continuation

Task lifetime is unbounded unless the caller supplies a budget. This contract
applies to ordinary CLI work and direct Task callers independently of the goal
plugin. `Task.timeout`
defaults to `None`; ordinary CLI work has no default aggregate duration. Model-generation, tool-call, hook, transport,
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


The event runner checks the bound while bootstrapping and while waiting for events,
not only after a model or tool returns. `Task.request_pause()` and
`Task.request_cancel()` stop waiting/running execution regardless of a deadline.
Finalization joins the existing terminal snapshot under repeated cancellation;
it does not replace a primary provider/bootstrap failure with a later cancellation.
