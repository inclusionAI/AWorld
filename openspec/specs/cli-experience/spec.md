# cli-experience Specification

## Purpose
Define the stable contributor-facing CLI experience, including supported interaction modes and how CLI behavior changes are governed.
## Requirements
### Requirement: CLI supports interactive and direct task execution
The AWorld CLI SHALL support both interactive sessions and direct one-shot task execution.

#### Scenario: Running from the terminal
- **WHEN** a user starts `aworld-cli`
- **THEN** the CLI can enter an interactive session
- **AND** users can also execute direct tasks through command-line arguments

### Requirement: CLI exposes command-oriented workflows
The AWorld CLI SHALL expose command-style workflows such as listing agents and slash-command interactions.

#### Scenario: Using a command-oriented workflow
- **WHEN** a user invokes supported command flows such as `list` or slash commands
- **THEN** the CLI routes the request through its command system
- **AND** the interaction remains part of the supported CLI experience

### Requirement: CLI behavior changes follow OpenSpec governance
Contributor-facing CLI behavior changes SHALL be proposed and tracked through OpenSpec.

#### Scenario: Changing CLI interaction behavior
- **WHEN** a contributor modifies supported CLI commands, prompts, or interaction flows
- **THEN** the change is represented in `openspec/changes/`
- **AND** the stable CLI contract is reflected in `openspec/specs/cli-experience/spec.md`

### Requirement: CLI manages framework plugins rather than standalone filesystem conventions
The AWorld CLI SHALL manage plugins as framework plugin units rather than treating CLI-installed plugin directories as the source of truth.

#### Scenario: Listing installed plugins
- **WHEN** a user lists plugins from the CLI
- **THEN** the CLI reports framework plugin units and their activation state
- **AND** the CLI view is backed by the framework plugin system

### Requirement: CLI supports plugin lifecycle operations
The AWorld CLI SHALL support lifecycle operations such as install, remove, enable, disable, and reload for framework plugins.

#### Scenario: Enabling a plugin from the CLI
- **WHEN** a user enables a plugin through the CLI
- **THEN** the CLI applies that operation through the framework plugin system
- **AND** the resulting plugin state is reflected in subsequent runtime activation

### Requirement: CLI exposes plugin commands through an explicit command contract
The AWorld CLI SHALL expose plugin-contributed commands through an explicit command contract rather than ad hoc file discovery alone.

#### Scenario: Registering a plugin command
- **WHEN** an active plugin contributes a command entrypoint
- **THEN** the CLI exposes the command using its declared name, description, visibility, and argument metadata
- **AND** command conflicts are resolved through the framework plugin system rather than incidental import order

### Requirement: CLI resolves plugin command resources through the framework
The AWorld CLI SHALL execute plugin commands through framework-resolved handlers and packaged resources.

#### Scenario: A plugin command references packaged resources
- **WHEN** a plugin command uses a packaged prompt template, script, or similar asset
- **THEN** the CLI resolves that resource through the plugin framework contract
- **AND** command execution does not rely on hard-coded filesystem assumptions outside the plugin model

### Requirement: CLI applies entrypoint-level tool policy for plugin commands
The AWorld CLI SHALL enforce command-specific tool permissions declared through the plugin system.

#### Scenario: A plugin command has restricted tool permissions
- **WHEN** a user invokes a plugin command whose entrypoint declares a restricted tool policy
- **THEN** the CLI enforces that command-level tool policy for the resulting command flow
- **AND** other commands from the same plugin are not implicitly granted the same permissions

### Requirement: CLI renders a composable plugin-aware bottom toolbar
The AWorld CLI SHALL render its bottom status bar from composable HUD line providers rather than relying only on a fixed hard-coded toolbar implementation.

#### Scenario: Active HUD plugins add lines to the bottom toolbar
- **WHEN** one or more active plugins provide HUD line contributions
- **THEN** the CLI merges those lines with built-in HUD providers using defined ordering and rendering rules
- **AND** the CLI retains final control over truncation, refresh cadence, and toolbar rendering

### Requirement: CLI applies deterministic HUD ordering and truncation
The AWorld CLI SHALL order and truncate HUD lines deterministically so multiple plugins can coexist without non-repeatable layout behavior.

#### Scenario: Rendering HUD lines from multiple providers
- **WHEN** multiple built-in and plugin HUD providers emit lines in the same refresh cycle
- **THEN** the CLI orders those lines by the defined HUD section and priority rules
- **AND** ties are resolved deterministically rather than by incidental plugin load timing

#### Scenario: Narrow terminal width or constrained HUD space
- **WHEN** the available bottom-toolbar space cannot display every HUD line
- **THEN** the CLI truncates or drops lower-priority HUD lines according to the defined rendering policy
- **AND** plugins do not bypass that policy by writing directly to terminal output

### Requirement: CLI owns HUD presentation while providers stay data-focused
The AWorld CLI SHALL remain the only renderer for the bottom toolbar, while HUD providers contribute plain-text lines based on structured context.

#### Scenario: A provider contributes a HUD line
- **WHEN** a HUD provider returns line content for rendering
- **THEN** the provider returns plain-text line data rather than raw prompt-toolkit markup or direct terminal writes
- **AND** the CLI applies the final styling and presentation rules
