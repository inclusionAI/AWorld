# Candidate transactions and original inputs

`aworld.core.task_workspace.store.TaskWorkspaceStore` is a framework library for
the native workbench/session facade. The caller binds workspace, scope, allowed
roots, selection policy and validator. None of those authority choices should
be supplied by model tool arguments. There are no aggregate task time limits.

The default parent is `AWORLD_TASK_WORKSPACE_ROOT`, otherwise
`~/.local/state/aworld/task-workspaces`; if that would be inside the workspace,
the fallback is `/tmp/aworld-task-workspaces-<uid>`. Each canonical scope has a
SHA-256 subdirectory, with `scope.json` as its public opening record.
`store_path`, `status()`, `input_snapshot()`, `current_input_snapshot_id`,
`protected_input_files()`, `immutable_input_evidence()`, `provenance()` and
`list_candidates(limit=20, offset=0)` expose state without private-manifest access.
`open_existing(store_path)` and construction recover interrupted transactions.
`last_recovery_result` remains available after a subsequent no-op `recover()`;
`status()` includes it and the last committed transaction for recovery audit.

All methods are synchronous except `validate_candidate` and `revalidate_best`.
Validation accepts an async or synchronous trusted callback with the A3
`validate_candidate(candidate_files, inputs, checks, scope=..., working_dir=...)`
contract. Candidate/input arguments are independent staged copies; command
checks run from the bound workspace so public checker scripts resolve normally.

`protect_inputs(paths)` preserves each original once and accumulates additions.
Declarations can be paths or `{path, immutable, source}` objects. Only an
explicit `immutable: true` requires the original path/hash to remain unchanged;
ordinary captured inputs may be transformed in place. Immutable status cannot
be downgraded by subsequent calls. Exact external file grants never become
parent-directory grants when the file is removed. Restoring inputs never
overwrites newer or damaged files: restore missing files or create a new
`working_copy(snapshot_id, destination)` instead.

Input grouping is format-based and pluggable through `InputGroupStrategy`.
The SQLite strategy recognizes the database header, captures existing DB/WAL/SHM
members with before/after identity checks, and rejects concurrent changes. It
validates and backs up the captured group in a private directory to produce a
committed working copy. It never opens the original database through SQLite.
Current-epoch WAL header/frame checksums are checked before SQLite recovery, so
SQLite cannot silently discard corrupted frames and advertise an incomplete
dataset as a valid capture.
Writers must be quiescent during capture; this is not a live transactional backup
service. Raw sidecar bytes remain recoverable separately from the normalized view.

`register_candidate({output_path: source_path}, provenance=...)` snapshots bounded
regular files. Provenance binds each artifact and source byte range to captured
hashes; derivation claims are explicitly not semantic proof. `validate_candidate`
runs actual checks and registers a MAC-bound receipt over candidate/input/check
definitions/policy hashes. Model-provided pass/metric objects are not accepted.
Executable/checker paths returned by the actual validator are rehashed before
receipt registration, promotion and final readback. Caller-bound execution
environment hashes are checked when present in the policy.

A policy has `mandatory_checks: [id, ...]`, optional `hard_constraints` such as
`{artifact: "result.json", max_bytes: 1024}` or `{metric: "check.error", op: "<=",
value: 0}`, and optionally `objective: {metric: "check.score", direction:
"maximize"}` (or `minimize`). `check_definitions_sha256` can bind the current
caller definitions. Only qualifying candidates are promoted. With an objective,
a candidate must improve its executed metric; without one, the first qualifying
delivery is retained. After a policy change, `revalidate_best` evaluates the
incumbent again. If it becomes ineligible, a new qualifying candidate may replace
it. Revalidating/promoting the same bytes refreshes the receipt, and can restore
missing or damaged published bytes without inventing a better metric.

Single-file publication uses fsync and an atomic replacement. Multi-file
publication records a durable journal before any replacement. A crash before
commit rolls back; a committed transaction completes on reopening. Unexpected
later edits cause a recovery conflict rather than being overwritten. Prior
accepted candidates/receipts remain in store history. `readback()` verifies the
actual final bytes and immutable input hashes again.

This recovery store does not resist task root deliberately modifying it, and
does not turn self-checks into external benchmark reward. POSIX file locking is
currently required. Snapshot file/count allowances are configurable resource
bounds, not task-duration budgets.
