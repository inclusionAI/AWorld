## ADDED Requirements

### Requirement: Runtime activates plugin-contributed capability surfaces
The AWorld runtime SHALL be able to activate plugin-contributed runtime surfaces such as tools, contexts, runners, agents, and swarms through the framework plugin system.

#### Scenario: Starting runtime with enabled plugins
- **WHEN** runtime starts with enabled framework plugins
- **THEN** it can resolve and activate the plugin-contributed runtime surfaces required for execution
- **AND** runtime activation occurs through the plugin system rather than bespoke per-surface loading logic

### Requirement: Runtime integrates plugin-contributed context behavior
The AWorld runtime SHALL integrate plugin-contributed context behavior as part of task execution when the relevant plugins are enabled.

#### Scenario: Executing a task with a context plugin
- **WHEN** a task runs under a scope where a context-capable plugin is active
- **THEN** the runtime can apply the plugin's declared context behavior during execution
- **AND** context integration is governed by plugin activation and scope rules

### Requirement: Runtime manages context plugins through explicit lifecycle phases
The AWorld runtime SHALL apply plugin-contributed context behavior through explicit lifecycle phases rather than ad hoc mutation.

#### Scenario: Running with context lifecycle phases
- **WHEN** runtime executes work with active context plugins
- **THEN** it applies context plugin behavior through defined phases such as bootstrap, enrichment, propagation, and persistence
- **AND** each phase operates only on the context roles declared by the plugin

### Requirement: Runtime provides scoped plugin state during active execution
The AWorld runtime SHALL provide scoped plugin state handles for active plugins so multiple entrypoints from one plugin can coordinate through the framework instead of ad hoc files.

#### Scenario: Session-scoped plugin state during execution
- **WHEN** runtime executes work in a session where a plugin is active
- **THEN** eligible plugin entrypoints can access that plugin's session-scoped state through the runtime contract
- **AND** the state lifecycle follows the session unless explicitly promoted or persisted by declared plugin behavior

### Requirement: Runtime produces HUD context for active plugins
The AWorld runtime SHALL expose a structured HUD context snapshot that active HUD providers can consume without scraping CLI output or probing runtime internals ad hoc.

#### Scenario: Refreshing the bottom toolbar
- **WHEN** the CLI refreshes its bottom toolbar while framework plugins are active
- **THEN** the runtime provides a structured HUD context snapshot for rendering
- **AND** HUD providers consume that snapshot rather than re-deriving state from raw console output

#### Scenario: HUD refresh uses one shared snapshot
- **WHEN** multiple HUD providers are rendered in the same refresh cycle
- **THEN** runtime produces one shared HUD context snapshot for that cycle
- **AND** providers consume that shared snapshot rather than triggering per-provider state collection
