## ADDED Requirements

### Requirement: CLI exposes a generic HUD host surface
The AWorld CLI SHALL expose the bottom HUD as a generic host-owned surface that any active HUD provider can target through the plugin system.

#### Scenario: Rendering a built-in HUD plugin
- **WHEN** the built-in `aworld-hud` plugin contributes HUD lines
- **THEN** the CLI mounts and renders those lines through the same generic bottom-toolbar surface used for any other HUD provider
- **AND** the renderer does not depend on `aworld-hud`-specific presentation branches

#### Scenario: Rendering a third-party HUD plugin
- **WHEN** a third-party HUD plugin contributes HUD lines through the supported capability contract
- **THEN** the CLI renders those lines through the same generic HUD surface
- **AND** no host code change is required solely because the provider is external

### Requirement: CLI HUD rendering stays presentation-focused
The AWorld CLI SHALL keep HUD host code focused on presentation concerns such as mounting, styling, refresh cadence, ordering, and truncation rather than embedding plugin-specific business semantics.

#### Scenario: A HUD plugin changes its field grouping
- **WHEN** a HUD plugin changes how it groups or summarizes its own data
- **THEN** the CLI continues to render the resulting HUD lines through the existing presentation pipeline
- **AND** the change does not require host code to learn new plugin-specific field semantics
