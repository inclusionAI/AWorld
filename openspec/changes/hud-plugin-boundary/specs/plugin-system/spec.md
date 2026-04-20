## ADDED Requirements

### Requirement: Plugins mutate scoped state through explicit framework APIs
The plugin system SHALL expose explicit framework APIs for reading and writing plugin-scoped state so plugin entrypoints do not need private file or host-internal access to coordinate behavior.

#### Scenario: A hook updates session-scoped plugin state
- **WHEN** a plugin hook needs to persist HUD-related session state during task execution
- **THEN** it writes that state through the framework's plugin-state contract
- **AND** later entrypoints from the same plugin can read the updated state through the same contract

#### Scenario: Plugin state access stays within declared boundaries
- **WHEN** a plugin reads or writes scoped state
- **THEN** it uses the explicit plugin-state contract rather than ad hoc filesystem writes or host-internal modules
- **AND** the scope rules for session, workspace, or global state remain enforced by the framework

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

### Requirement: HUD providers receive plugin-owned state as part of their explicit contract
The plugin system SHALL define HUD providers as consuming both shared host context and plugin-owned scoped state so plugin summaries can remain inside plugin code.

#### Scenario: Rendering a stateful HUD provider
- **WHEN** a HUD provider renders after hooks have updated plugin-owned state
- **THEN** the provider receives both the shared host context and the plugin-owned state for that plugin
- **AND** the host does not need to fold plugin-specific summaries back into generic context first

### Requirement: Plugin-facing HUD helper APIs are explicit
The plugin system SHALL expose any reusable HUD helper APIs for plugins through an explicit plugin-facing boundary rather than by relying on private host implementation modules.

#### Scenario: A built-in and external HUD plugin share a formatter
- **WHEN** both a built-in HUD plugin and a third-party HUD plugin need the same reusable formatting helper
- **THEN** that helper is provided through an explicit plugin-facing contract
- **AND** private host internals are not treated as part of the supported plugin API
