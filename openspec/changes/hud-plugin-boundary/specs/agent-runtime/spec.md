## ADDED Requirements

### Requirement: Runtime exposes generic HUD context and plugin-state bridges
The AWorld runtime SHALL expose generic HUD context together with plugin-scoped state bridges so HUD providers can render from shared context and plugin-owned state without relying on plugin-specific runtime logic.

#### Scenario: Refreshing HUD with shared context and plugin-owned state
- **WHEN** the CLI refreshes the bottom HUD while one or more HUD plugins are active
- **THEN** runtime provides shared generic HUD context for that refresh cycle
- **AND** plugin entrypoints can also consume their plugin-scoped state through the framework contract

#### Scenario: Built-in HUD plugin uses runtime bridges
- **WHEN** the built-in `aworld-hud` plugin renders HUD lines
- **THEN** it consumes the same generic runtime context and plugin-state bridges available to other HUD plugins
- **AND** runtime does not assemble `aworld-hud`-specific business summaries on the plugin's behalf

### Requirement: Runtime provides plugin-state write-back for active entrypoints
The AWorld runtime SHALL provide active plugin entrypoints with a generic write-back path for plugin-scoped state so hook-driven plugin coordination does not depend on host-specific code.

#### Scenario: A hook writes session-scoped HUD state during execution
- **WHEN** an active plugin hook needs to record HUD-related session state during task execution
- **THEN** runtime provides a generic plugin-state write path for that update
- **AND** subsequent entrypoints from the same plugin can observe the persisted state through the framework contract

### Requirement: Runtime assembles HUD refresh inputs without hiding plugin state behind host semantics
The AWorld runtime SHALL assemble HUD refresh inputs so shared context remains generic while plugin-owned state remains separately available to the provider contract.

#### Scenario: Refreshing a third-party HUD provider
- **WHEN** a third-party HUD provider renders during a toolbar refresh
- **THEN** runtime supplies shared generic context and the plugin-owned state for that provider separately
- **AND** runtime does not need to precompute plugin-specific summaries in the generic context
