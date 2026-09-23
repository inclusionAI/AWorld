# Task lifetime and goal continuation

Ordinary CLI work and `/goal` have no aggregate time or token budget.
`LocalAgentExecutor.chat()` creates tasks with `Task.timeout=None` and does not
copy a runtime's external benchmark deadline into an AWorld stop condition.
Model, tool, hook, transport, and trajectory-finalization liveness timeouts remain
separate operations. A timed-out operation is not successful task completion.

## Goal continuation

A goal continues until the objective is complete, the user pauses or clears it,
or an optional `/goal ... --max-turns N` attempt limit is reached. No maximum is
imposed by default. `/goal` has no time-budget or deadline arguments, and old
goal-state time fields do not restrict continuation or resume.

Goal state persists the attempt count, last task identity/epoch, configured agent
identities, and outcome. `/goal pause` preserves state and interrupts execution;
`/goal clear` interrupts and discards it. `/goal resume` uses the same session and
rebinds recovery records to the new task and agent identities. Resume does not
reset an explicitly supplied maximum attempt count.

Continuation is iterative, not recursive. Each attempt receives its own task and
trajectory identity. Trusted continuation explicitly migrates the prior work
ledger via `carry_goal_work_state`; durable resume uses the persisted source
identity and `resume_goal_work_state`. Requirements, plans, and historical
observations survive, while completion state and fresh validation evidence do not
carry over to the new attempt.

The completion control plane is `TaskResponse.semantic_status`,
`completion_reason`, and `recoverable`. Incomplete or exhausted attempts do not
become success because final prose or a FINISHED message exists. An active goal
continues after an incomplete result or a failed attempt, retaining the error for
the next attempt. Repeated reads and no-progress advice do not impose another
stop rule. Reaching the optional attempt limit preserves the unsuccessful outcome
(the existing persisted status name is `budget_limited`). User stop always wins
over a late completion or error hook.

Typed success completes a goal without a promise. When a promise is specified it
must match and the control plane must also report success. Explicit `--verify`
commands execute with real exit codes and `bash` pipefail; their results must be
reobtained for each new attempt. Caller-provided checks remain part of the
completion contract.

## Library compatibility

The generic `Task` API retains optional caller-supplied `timeout` and
`deadline_epoch_seconds` support for library consumers. Both default to `None`.
A finite duration binds when constructed, the earliest bound wins, and a child,
retry, or wall-clock rollback cannot extend it. Invalid explicit values fail
closed; the historical `timeout=0` sentinel is now `None`. This compatibility API
is independent of CLI/goal execution and adds no CLI time-budget entry point.

The event runner handles `Task.request_pause()` and `Task.request_cancel()` during
bootstrap and execution. Finalization joins the existing terminal snapshot under
repeated cancellation instead of replacing the primary failure.

These mechanisms do not demonstrate a benchmark reward improvement. A scored
result still requires a new qualified execution and the unmodified verifier. The
benchmark's external protocol and scoring deadlines are unchanged.
