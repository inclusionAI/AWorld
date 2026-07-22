# Self-Evolve Framework Repair Milestones

## Architecture overview

Normalize all datasets to member-shaped execution records. Typed causal events
carry execution status, owner, stage, scope, code, and repairability across
replay, gates, diagnostics, lessons, reporting, and scheduling. Conformance is
an always-on candidate capability stage over distinct requirement shapes;
representative screening remains optional task-quality optimization. A run-wide
ledger schedules lifecycle stages from observed and reserved cost.

## Milestone 1: Verification and trust boundary

- Plan 002: CI and cardinality contract matrix — pending.
- Plan 003: total provenance resolution and fail-closed trust — pending.
- Parallelism: implementation may run in parallel in isolated worktrees; merge
  verification is sequential.

## Milestone 2: Replay lifecycle foundation

- Plan 004: typed failure event, normalized members, correct population stop — pending.
- Depends on milestone 1 verification baseline.

## Milestone 3: Conformance and causal memory

- Plan 005: cardinality-independent, shape-complete conformance — pending.
- Plan 006: causal diagnostics and aggregated lesson memory — pending.
- Depends on plan 004. Implementation may be split, but runner integration is merged sequentially.

## Milestone 4: Budgeted scheduling and reporting

- Plan 007: lifecycle records, population funnel, budget ledger, focused repair scheduler — pending.
- Depends on plans 004–006.

## Milestone 5: Cross-cutting verification

- Run focused tests after every merge.
- Run full `tests/self_evolve` in the supported environment.
- Perform fresh architectural review against the cross-plan invariants.
