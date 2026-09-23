# Execution completion and recoverable progress

Agent termination, completion evidence, and reward are separate concepts. A
non-empty model response or a tool transport success does not prove that the
requested work was completed.

## Completion boundaries

The model response boundary retains the provider's `finish_reason`. Length
stops, reasoning-only responses, and invalid native tool-call JSON are retried
within the configured model attempt budget. An invalid batch is discarded as a
whole: no prefix of the batch is executed. Exhaustion produces an `incomplete`
execution state. Repeated response repair without an executable response is not
automatically retried forever by a goal.

Benchmark runtimes set `AWORLD_REQUIRE_STREAM_FINISH_REASON=true`. In that mode,
an implicit end-of-stream without a provider `finish_reason` is treated as an
incomplete response and enters the same bounded retry path. A one-shot action
repair must also be complete: a truncated or unterminated repair raises the
typed `action_repair_exhausted` stop instead of being returned as normal output.
Standalone integrations retain the compatibility default unless they opt in.

A step-budget boundary requests one tool-free final answer from the model. When
that synthesis succeeds, its completion status is preserved and the exhausted
budget is recorded separately as execution metadata; answer correctness remains
the verifier's responsibility. When synthesis cannot produce an answer, the
state is `budget_exhausted` under `agent_execution_state`, with task/epoch
identity, reason, and recoverability. The task handler projects that incomplete
state into TaskResponse without fabricating success.

## Explicit verification

`AWORLD_REQUIRED_ARTIFACTS_JSON` is a caller-supplied JSON array of output paths.
It records structured completion evidence but remains advisory unless
`AWORLD_COMPLETION_MODE=enforce` is explicitly set. Optional natural-language
output inference remains advisory and must not add guessed requirements to an
explicit contract.

`AWORLD_COMPLETION_MAX_REPAIRS` optionally limits model-driven completion repair
turns with a non-negative integer. Unset or blank retains the historical
unbounded contract, while `0` prevents a repair turn after a rejected completion
claim. The repair prompt instructs the Agent to take a concrete Tool action that
changes or verifies the result before attempting another final answer.

`AWORLD_VALIDATION_COMMANDS_JSON` accepts explicit objects containing
`command_id`, `argv`, optional `cwd`, and optional `timeout_seconds`. Validation
commands are not inferred from model or tool text. They execute at the completion
boundary and produce actual exit codes and output hashes. Output is drained with
bounded memory; timeouts/cancellation terminate the validation process group.
A message saying `PASS` cannot override a failed process. Caller-supplied
expected artifact hashes are checked against actual file contents.

Goal `--verify` commands use `configure_goal_completion`. These are explicit
user shell commands, executed with Bash `pipefail` so a failing test piped into
a successful log formatter cannot pass. Individual command timeouts protect
liveness and do not replace the task's caller-owned overall deadline. No
verification result is inferred from repeated prose or response length.

## Persistent work progress

The existing adaptive Tool ledger now retains:

- Public task input, with source and content hash.
- The latest agent plan, explicitly labelled as an unverified agent claim.
- Observed commands/results and recent failures.
- Explicit output requirements, observed candidate state, and pending outputs.
- Runtime self-check evidence, separately from transport success.
- Exact repeated read/result hash evidence, with observation sequence numbers.

The ledger is saved through WorkingState and the existing lightweight Amni
checkpoint before the next model request and at termination. Plain Contexts
without a usable checkpoint destination retain in-memory state and report
`memory_only`; they do not claim durable persistence. Recovery messages keep
untrusted values inside an escaped data boundary. They never promote text inside
a tool result into instructions or verification.

Three occurrences of the same operation/result pair inside the bounded recent
window produce an advisory recovery guard, including interleaved A-B-A-B-A
cycles. The next action must mutate a task artifact or produce new validation
evidence; reads remain allowed, while real artifact or evidence progress resets
the window.
A goal may explicitly call `carry_goal_work_state` to transfer historical evidence
to a new execution segment, or `resume_goal_work_state` to rebind a named prior
segment loaded from a checkpoint. Completion state and fresh verification receipts
are not carried forward. An exhausted segment which repeats the prior exhausted
segment's same evidence can pause for intervention instead of cycling forever.

These checks cannot establish arbitrary domain correctness. Tasks without a
structured completion contract still require independent evaluation; a successful
agent run is not a benchmark reward claim.
