# Self-Evolve Framework Repair Goal

## Problem statement

The self-evolve framework conflates trajectory cardinality with control flow,
mixes execution status with failure ownership, skips repair conformance for
single-case datasets, loses causal failures before lesson extraction, has an
incomplete target trust boundary, and does not enforce a real run-wide budget.

## Desired outcome

Implement plans 002–007 so one trajectory and multiple trajectories use the
same normalized lifecycle. Cardinality changes coverage and cost only. All
distinct conformance shapes are validated, causal events remain structured,
lesson memory is aggregated, target mutation is fail-closed by provenance, and
candidate scheduling is governed by observed/reserved budget.

## Acceptance criteria

- [ ] CI runs platform-neutral and native-sandbox self-evolve tests.
- [ ] Every verified target has resolved provenance and passes explicit trust policy.
- [ ] Replay status, failure owner/stage/scope, and comparability are orthogonal.
- [ ] Candidate-owned preflight failures do not terminate the remaining population.
- [ ] Repair conformance runs for one and multiple trajectory cases and covers all distinct shapes.
- [ ] Diagnostics and lessons consume typed causal events and aggregate duplicates.
- [ ] Population reports reflect actual lifecycle stages and exact skip reasons.
- [ ] A run-wide ledger reserves and debits generation/replay/evaluation work.
- [ ] Tests cover one case and three cases, including repeated and distinct shapes.
- [ ] Full `tests/self_evolve` passes on the supported macOS sandbox host.

## Non-goals

- No branch keyed by a historical run, target, case, fixture excerpt, or exact error text.
- No weaker replay, conformance, trust, or judge gates.
- No unsafe Linux no-sandbox fallback.
- No changes to unrelated user artifacts or tracked skill deletions.

## Constraints

- Preserve the dirty root worktree and unrelated untracked files.
- Use generic synthetic fixtures in tests.
- Maintain backward readability of existing replay and lesson artifacts.
- Follow plans 002–007 and their STOP conditions.
