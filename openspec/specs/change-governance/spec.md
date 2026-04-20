# change-governance Specification

## Purpose
Define how this repository manages active changes, stable capability contracts, and the historical status of the legacy superpowers change-doc workflow.
## Requirements
### Requirement: OpenSpec governs repository changes
The repository SHALL use OpenSpec as the active system for proposed changes, implementation tasks, and stable capability contracts.

#### Scenario: Proposing a behavior change
- **WHEN** a contributor wants to change repository behavior, interfaces, or contributor workflow
- **THEN** the proposal is created under `openspec/changes/<change-name>/`
- **AND** the stable contract is represented in `openspec/specs/`

### Requirement: Legacy superpowers change docs are archival only
The repository SHALL retain `docs/superpowers/` only as historical context and SHALL NOT use it as the active entry point for new changes.

#### Scenario: Looking for the current source of truth
- **WHEN** a contributor needs the current stable behavior or an in-flight proposal
- **THEN** they use `openspec/specs/` and `openspec/changes/`
- **AND** they treat `docs/superpowers/` as background history only
