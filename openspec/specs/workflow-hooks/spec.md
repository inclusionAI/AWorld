# workflow-hooks Specification

## Purpose
Define the stable hook lifecycle model for runtime interception, ordered hook execution, and runtime visibility or control around task processing.
## Requirements
### Requirement: Hooks intercept defined runtime lifecycle points
The runtime SHALL support hook execution at predefined lifecycle interception points without requiring contributors to modify core execution code.

#### Scenario: Adding hook-based behavior
- **WHEN** a contributor registers hook logic for a supported lifecycle point
- **THEN** the runtime executes that hook at the matching point in task execution
- **AND** the core runner flow remains intact

### Requirement: Hook chains execute in deterministic order
The runtime SHALL allow multiple hooks at the same lifecycle point and SHALL execute them as an ordered chain with deterministic ordering rules.

#### Scenario: Multiple hooks share a lifecycle point
- **WHEN** more than one hook is registered for the same lifecycle point
- **THEN** the runtime invokes them sequentially in the defined order for that point
- **AND** later hooks receive the output state produced by earlier hooks

### Requirement: Hooks support runtime visibility and control
The runtime SHALL allow hooks to support monitoring, transformation, auditing, and execution control around task processing.

#### Scenario: Observing a tool call through hooks
- **WHEN** a hook is registered around tool execution
- **THEN** the runtime can inspect or transform execution data at that point
- **AND** the hook result participates in the task lifecycle outcome

### Requirement: Hooks can be contributed by framework plugins
The hook system SHALL allow enabled framework plugins to contribute hook registrations through the unified plugin system.

#### Scenario: A plugin declares hook behavior
- **WHEN** a plugin declares hook contributions for supported lifecycle points
- **THEN** AWorld can load those hook contributions through the plugin system
- **AND** the hooks participate in normal hook execution ordering

### Requirement: Plugin hook loading follows plugin lifecycle and policy
Hook contributions from plugins SHALL be subject to plugin validation, scope, policy, and activation state before they run.

#### Scenario: A blocked plugin declares hooks
- **WHEN** a plugin is blocked or inactive for the current scope
- **THEN** its hook contributions are not activated
- **AND** hook execution only includes contributions from validated active plugins

### Requirement: Hooks consume structured event input and return typed control results
The hook system SHALL define plugin hook execution around structured event payloads and typed hook results rather than shell-only side effects.

#### Scenario: A hook rewrites interactive input
- **WHEN** a plugin hook runs for an interactive input-related hook point
- **THEN** it can return a typed result that leaves execution allowed while supplying updated input or metadata
- **AND** downstream execution consumes the updated values through the defined hook contract

#### Scenario: A hook denies continuation
- **WHEN** a plugin hook determines execution must not continue
- **THEN** it can return a typed deny or stop result with a reason
- **AND** AWorld stops the affected flow without requiring the hook to mutate consumer internals directly

### Requirement: Interactive hooks can block termination and continue the session
The hook system SHALL support interactive hook points whose results can block a termination path and inject follow-up session input or system guidance.

#### Scenario: A stop hook continues a session loop
- **WHEN** an interactive stop or termination hook decides the session should continue
- **THEN** it can block termination and provide follow-up prompt content or system guidance through the hook result
- **AND** AWorld continues the same session without requiring an external wrapper loop

### Requirement: Hook ordering and failure handling are deterministic
The hook system SHALL define deterministic ordering and failure behavior for plugin-provided hooks.

#### Scenario: Multiple plugins contribute hooks to the same hook point
- **WHEN** more than one active plugin contributes hooks for the same hook point
- **THEN** AWorld executes them in the defined order
- **AND** hook errors degrade according to the hook policy instead of causing incidental reordering or undefined flow control
