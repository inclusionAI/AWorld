## ADDED Requirements

### Requirement: Skills can be contributed through framework plugins
The repository SHALL allow plugins to contribute skills through the unified plugin system instead of treating plugin skills as a separate loader convention.

#### Scenario: Loading skills from a plugin
- **WHEN** an enabled plugin declares skill contributions
- **THEN** AWorld can register those skills through the framework plugin system
- **AND** the skill system recognizes them as plugin-provided capabilities

### Requirement: Plugin-provided skills follow plugin scope and policy
Plugin-provided skills SHALL only be available when the corresponding plugin is active for the current scope and not blocked by policy.

#### Scenario: Skills from a disabled plugin
- **WHEN** a plugin is disabled for the current scope
- **THEN** skills contributed by that plugin are not exposed as active skills
- **AND** skill availability follows framework plugin activation state
