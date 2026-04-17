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
