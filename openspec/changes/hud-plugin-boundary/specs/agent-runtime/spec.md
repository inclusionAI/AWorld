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
