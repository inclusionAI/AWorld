# skills-system Specification

## Purpose
Define the stable repository contract for reusable skills, supported skill locations, and governance of skill-system behavior.
## Requirements
### Requirement: Skills are reusable capability units
The repository SHALL treat skills as reusable capability definitions that can be exposed to agent workflows.

#### Scenario: Loading a reusable skill
- **WHEN** a skill is made available through the repository skill system
- **THEN** it is represented as a reusable capability artifact rather than inline one-off task logic
- **AND** it can be referenced by agent workflows that support skills

### Requirement: Skills can be sourced from repository-managed locations
The repository SHALL support skill discovery from repository-managed skill locations such as `aworld-skills/`.

#### Scenario: Discovering project-provided skills
- **WHEN** contributors inspect the repository for built-in reusable skills
- **THEN** they can find repository-managed skills under supported skill directories
- **AND** those locations serve as a maintained source for shared capabilities

### Requirement: Skill behavior changes are spec-governed
Changes that alter the supported skill-system behavior SHALL be tracked through OpenSpec.

#### Scenario: Changing skill discovery or usage rules
- **WHEN** a contributor changes how the repository exposes or documents supported skill behavior
- **THEN** the change is proposed in `openspec/changes/`
- **AND** the resulting stable behavior is reflected in the skills-system spec

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
