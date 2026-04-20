## ADDED Requirements

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
