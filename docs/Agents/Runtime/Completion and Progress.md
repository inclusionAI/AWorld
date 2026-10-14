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

The bundled Agent also enables success-first terminal control. Short work stays
on the historical fail-open path. Generic Tool/stagnation/deadline evidence may
produce one bounded model-owned replan and an advisory convergence stage:
produce a declared candidate, then validate, repair from public evidence, or
submit. Built-in policies do not use static Tool-effect analysis to deny
ordinary sandbox work; an explicit developer policy is required to enable that
legacy hard admission mode. Once a candidate reaches the terminal boundary,
the runtime reserves a bounded decision/finalization window and may request one
model-owned review plus at most one repair. The review shares the original task
budget, cannot mint verifier authority, cannot be blocked by candidate Tool
admission, and is never retried after an unavailable or inconclusive result.

An unavailable initial execution-profile response does not suppress later
controller-requested replans. A newer ordinary stagnation or delivery-debt
checkpoint is exposed as a replan boundary even when the initial profile failed
open; terminal delivery checkpoints retain their bounded fail-open behavior.

## Public task contract ledger

CLI task setup derives a bounded `aworld.public-task-contract/v1` ledger only
from the authoritative public task text. Conservative extractors recognize
explicit deliverable files, structured field names, function/import signatures,
service ports, public commands, numeric thresholds, prohibitions, and process
liveness clauses. Every row carries a public-source excerpt fingerprint. The
ledger is advisory task grounding: it helps the Agent keep a final evidence
checklist and request evidence, but it is not a completion contract, Tool-write
allowlist, or verifier result. Mentioned inputs, examples, and prohibited
changes do not become output files.

The ledger never reads Dataset verifier files, reward, post-run verifier output,
historical expected answers, or solver-generated requirements. Its request hash
must match the current caller-owned task before either the Agent or the fresh
reviewer will consume it. Ambiguous requirements are deliberately omitted and
remain the model's responsibility.

## Explicit verification

The generic completion contract is disabled by default. A caller must set
`AWORLD_COMPLETION_MODE=observe` or `enforce` and provide at least one structured
field: `AWORLD_REQUIRED_ARTIFACTS_JSON` or
`AWORLD_VALIDATION_COMMANDS_JSON`. Required artifacts are a JSON array of output
paths. They record structured completion evidence in `observe` mode and block a
completion claim only in `enforce` mode. Natural-language output detection is
always advisory; the legacy `AWORLD_INFER_REQUIRED_ARTIFACTS` environment value
is ignored and cannot create a completion contract.

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

The lazy fresh-context reviewer uses the same public boundary. It receives the
public task, a request-hash-matched ledger, public declared paths, bounded
read-only snapshots of current declared files, and framework-owned public check
receipts. It does not receive hidden verifier content or reward. A solver claim
alone cannot produce `ready`; missing evidence, lifecycle failures, timeouts,
multiple decisions, and malformed output remain `uncertain`. Transport Context
copies resolve public contracts through their framework-owned task Context, not
through solver text. Current file bytes can support static artifact review, but
an executable obligation such as a function, import, command, service, or live
process still requires a fresh public validation receipt before `ready` is
promoted. The reviewer must challenge interacting public constraints with a
task-derived counterexample and recommend a runnable public probe when that
evidence is absent.
