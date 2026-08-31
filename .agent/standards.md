# Project Standards

## Architecture

- Existing truth sources remain authoritative: provider requests in `llm_calls`; actions/results in runtime events.
- Control-plane models contain identity, decisions, counts, fidelity, checksums, and refs—not duplicated raw semantics.
- Core Context policy is provider-neutral. Provider-specific cache/lowering behavior stays at provider boundaries.
- All finalization and lifecycle transitions have explicit state machines; no correctness-critical bare background task.
- Compatibility is additive and versioned. Legacy behavior is retained behind explicit modes during migration.
- Benchmark adapters contain no task names, expected answers, solution logic, or verifier-specific policy.

## Python Quality

- Public contracts are typed and serializable; use frozen dataclasses/enums where immutability is required.
- Avoid mutable defaults and implicit singleton/global state.
- Exceptions and degraded fidelity are explicit; never swallow a correctness failure into an empty trajectory.
- Async cancellation is propagated after cleanup; timeouts produce typed results.
- Names describe domain semantics; checksum/canonicalization functions specify their byte representation.

## Security and Privacy

- Default traces/logs contain redacted previews and metadata only.
- Artifact paths are scoped and validated; full sensitive content is never embedded into committed test fixtures.
- Hashes and counts remain available after redaction.
- Tests must not read or print local provider credentials.

## Testing

- Tests lead implementation and assert observable contracts, not private call order unless concurrency correctness requires it.
- Cover complete, empty, partial, timeout, exception, cancellation, malformed legacy data, and checksum mismatch.
- Use deterministic clocks/IDs where exact snapshots matter.
- Real Docker/provider tests are opt-in gates; unit/integration suites remain hermetic by default.
- Run targeted tests during TDD, then the affected subsystem suite before commit.

## Git and Workspace

- Preserve unrelated dirty/untracked user files.
- Stage only milestone files; one logical commit per task or integrated milestone.
- No destructive reset/checkout operations.
- Update `.agent/progress.md` after each completed task, review, or decision.

