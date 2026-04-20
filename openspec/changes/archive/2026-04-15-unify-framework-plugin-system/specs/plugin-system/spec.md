## ADDED Requirements

### Requirement: AWorld provides a framework-level plugin model
The framework SHALL provide a plugin system as a first-class capability rather than limiting plugins to CLI-managed directory conventions.

#### Scenario: Loading a framework plugin
- **WHEN** AWorld discovers a plugin for activation
- **THEN** the plugin is handled through the framework plugin system
- **AND** plugin loading does not depend solely on ad hoc CLI directory scanning

### Requirement: Plugins declare explicit metadata and capability surfaces
Each plugin SHALL declare explicit metadata and contributed capability surfaces through a manifest-driven contract.

#### Scenario: Validating plugin structure
- **WHEN** AWorld inspects a plugin before activation
- **THEN** it validates plugin metadata, source identity, and declared capability surfaces
- **AND** activation is blocked if the plugin contract is invalid

### Requirement: Plugins support multi-surface capability contribution
A plugin SHALL be able to contribute one or more framework capability surfaces, including agents, swarms, tools, MCP servers, runners, hooks, contexts, HUD providers, skills, and CLI commands.

#### Scenario: A plugin contributes multiple surfaces
- **WHEN** a plugin declares more than one capability surface
- **THEN** AWorld can register each declared surface through the unified plugin system
- **AND** the plugin remains a single managed unit for lifecycle and policy purposes

### Requirement: Plugins expose typed entrypoints rather than only filesystem conventions
The plugin system SHALL model plugin contributions as typed entrypoints with explicit descriptors rather than relying only on implicit directory semantics.

#### Scenario: Registering plugin entrypoints
- **WHEN** a plugin declares command, hook, context, HUD, skill, or agent-like contributions
- **THEN** AWorld registers them as typed entrypoints owned by that plugin
- **AND** each entrypoint carries stable identity, type, scope, and consumer-visible metadata

### Requirement: Plugins support explicit lifecycle management
The plugin system SHALL define explicit lifecycle phases for plugin discovery, validation, resolution, loading, activation, deactivation, and unload.

#### Scenario: Activating a plugin
- **WHEN** a plugin is enabled for a scope
- **THEN** the plugin system advances it through the defined lifecycle phases
- **AND** activation occurs only after validation and resolution succeed

### Requirement: Plugins support scope, version, and policy control
The plugin system SHALL model plugin source, scope, version, and policy state independently from any specific consumer such as CLI.

#### Scenario: Enabling a plugin in a workspace
- **WHEN** a plugin is enabled for a workspace scope
- **THEN** the plugin system records that enablement independently of CLI-only loader assumptions
- **AND** version and policy rules are applied before activation

### Requirement: Plugins provide packaged assets and scoped state
The plugin system SHALL provide stable resource contracts for plugin-packaged assets and plugin-owned state across scopes.

#### Scenario: A plugin command and hook share state
- **WHEN** one entrypoint in a plugin writes session-scoped plugin state
- **THEN** another entrypoint from the same plugin can read that state through the framework resource contract
- **AND** the state remains scoped to the active session unless explicitly persisted elsewhere

#### Scenario: Resolving a plugin-packaged asset
- **WHEN** an entrypoint references a packaged script, template, or other plugin asset
- **THEN** AWorld resolves that reference relative to the validated plugin root
- **AND** the entrypoint cannot escape the plugin root through ad hoc relative-path traversal

### Requirement: Plugins support entrypoint-level policy
The plugin system SHALL allow policy to be applied at the individual entrypoint level in addition to whole-plugin activation.

#### Scenario: Restricting a command entrypoint
- **WHEN** a plugin is active but one command entrypoint is blocked by policy
- **THEN** AWorld keeps the plugin active for other allowed entrypoints
- **AND** the blocked command is not exposed or executable for that scope

### Requirement: Context is a first-class plugin surface
The plugin system SHALL allow plugins to extend context behavior through explicit context capability declarations.

#### Scenario: A plugin extends context propagation
- **WHEN** a plugin declares context-related capabilities
- **THEN** AWorld can register context schema, enrichment, propagation, persistence, or retrieval behavior through the plugin system
- **AND** those context extensions participate in scoped plugin activation

### Requirement: HUD is a first-class plugin surface
The plugin system SHALL allow plugins to extend the CLI bottom status bar through explicit HUD capability declarations.

#### Scenario: A plugin contributes HUD lines
- **WHEN** a plugin declares HUD-related capabilities
- **THEN** AWorld can register HUD providers for that plugin through the unified plugin system
- **AND** the plugin remains subject to the same scope, lifecycle, and policy controls as other plugin surfaces

### Requirement: HUD providers use an explicit line-provider contract
The plugin system SHALL define HUD contributions as explicit line providers with stable identity and deterministic metadata rather than ad hoc toolbar mutation.

#### Scenario: Registering a HUD line provider
- **WHEN** a plugin declares a HUD provider
- **THEN** the provider declares a stable provider identity, section, and priority
- **AND** the provider can emit zero or more HUD lines through the defined HUD provider contract

#### Scenario: Rejecting invalid HUD provider identities
- **WHEN** a plugin declares duplicate HUD provider identities within the same plugin
- **THEN** plugin validation fails
- **AND** the plugin is not activated until the conflict is resolved

### Requirement: Context capabilities declare explicit management roles
The plugin system SHALL model context capabilities through explicit management roles rather than treating context as an undifferentiated runtime mutation point.

#### Scenario: Declaring context management roles
- **WHEN** a plugin contributes context capabilities
- **THEN** it can declare one or more roles such as schema registration, bootstrap, enrichment, propagation, persistence, or retrieval
- **AND** those roles are activated only through the framework plugin lifecycle
