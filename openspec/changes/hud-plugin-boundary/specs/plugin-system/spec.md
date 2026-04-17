## ADDED Requirements

### Requirement: Built-in HUD plugins use the same plugin capability contract as external HUD plugins
The plugin system SHALL treat built-in HUD plugins as ordinary plugin capability providers rather than as privileged host-side behavior paths.

#### Scenario: Activating the built-in HUD plugin
- **WHEN** the built-in `aworld-hud` plugin is active
- **THEN** AWorld loads its HUD, hook, and related entrypoints through the same plugin capability contracts used for an external HUD plugin
- **AND** built-in status changes only packaging and default availability, not the behavior contract

#### Scenario: Changing built-in HUD content
- **WHEN** contributors change HUD content policy such as fields, grouping, or summaries for `aworld-hud`
- **THEN** those changes are implemented through plugin-owned entrypoints and data contracts
- **AND** the host does not require a plugin-name-specific behavior branch to support the change

### Requirement: HUD capability evolution does not depend on per-plugin host branches
The plugin system SHALL allow HUD capability evolution through generic plugin contracts so a HUD plugin can gain new behavior without requiring host code paths keyed to that specific plugin.

#### Scenario: A third-party HUD plugin adds a new summary field
- **WHEN** an external HUD plugin adds a new summary derived from supported hook and context contracts
- **THEN** the plugin can render that summary through the HUD capability contract
- **AND** AWorld does not require a host branch keyed to that plugin identifier
