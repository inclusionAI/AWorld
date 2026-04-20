## ADDED Requirements

### Requirement: Hooks can drive plugin-owned HUD state
The hook system SHALL allow plugins to use supported hook points to maintain plugin-owned HUD state without requiring direct customization of CLI host behavior.

#### Scenario: A plugin hook captures task lifecycle state for HUD rendering
- **WHEN** a plugin hook runs at supported task lifecycle points such as task start, progress, completion, or error
- **THEN** it can publish or update plugin-scoped state used by that plugin's HUD provider
- **AND** the HUD provider can consume that state through the framework contract instead of scraping terminal output

#### Scenario: Multiple hook points contribute to one HUD summary
- **WHEN** a plugin uses more than one hook point to assemble HUD state
- **THEN** each hook contributes through the same plugin-owned state path
- **AND** HUD rendering observes the resulting composed state without requiring per-hook host branches

### Requirement: Hooks support task lifecycle visibility for HUD-capable plugins
The hook system SHALL expose task lifecycle hook points suitable for HUD-capable plugins to observe live execution state.

#### Scenario: A plugin observes task start and progress
- **WHEN** a task starts and later emits progress during execution
- **THEN** hook points such as `task_started` and `task_progress` are available to active plugins
- **AND** those hooks can observe enough structured data to update plugin-owned HUD state

#### Scenario: A plugin observes task completion and error
- **WHEN** a task completes successfully or fails
- **THEN** hook points such as `task_completed` and `task_error` are available to active plugins
- **AND** those hooks can finalize or clear plugin-owned HUD state without host-specific HUD branches
