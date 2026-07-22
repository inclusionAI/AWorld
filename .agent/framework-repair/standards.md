# Framework Repair Standards

## Architecture

- Cardinality is data: one case is one normalized member; N cases are N members.
- Coverage is by semantic requirement/fixture shape, never representative case alone.
- Status, owner, stage, scope, repairability, and comparability are separate fields.
- Causal events, not prose strings, are the cross-layer integration contract.
- Trust authorization is independent of behavioral verification.
- Budget estimates are monotonic with distinct work and reconciled to observed usage.

## Code quality

- Use typed dataclasses/enums/literals and bounded serialization helpers.
- Preserve additive backward compatibility for persisted artifacts.
- Keep replay aggregation pure where possible and runner orchestration thin.
- No target-, run-, case-, protocol-message-, or fixture-specific branches.
- No raw replay payloads or secrets in diagnostics, lessons, or reports.

## Testing

- Add failing tests first for changed semantics.
- Parameterize new framework behavior for one and three cases.
- Include same-shape and distinct-shape multi-case inputs.
- Assert exact status/owner/stage/scope and backend invocation counts.
- Do not encode currently misleading report strings as contracts.

## Workspace

- Modify only task-scoped files.
- Preserve unrelated dirty and untracked files.
- No new dependencies without a documented reason.
