## MODIFIED Requirements

### Requirement: CLI evaluator command runs a complete evaluation flow

The CLI SHALL provide an evaluator command that can run a complete evaluation flow against a supported local target or a supported evaluation input source such as a task file, task+answer file, serialized evaluation state, or AWorld trajectory log.

#### Scenario: User evaluates a local target
- **WHEN** a user invokes the evaluator command with a supported local evaluation target
- **THEN** the CLI SHALL resolve the target, build an evaluation flow, execute the selected suite, and return a completed evaluation result

#### Scenario: User evaluates a source input
- **WHEN** a user invokes the evaluator command with a supported source input and source kind
- **THEN** the CLI SHALL resolve the input, select the matching framework source adapter, build a source-backed evaluation flow, and return a completed evaluation result

### Requirement: CLI evaluator is an official plugin-backed command

The evaluator command SHALL integrate with the CLI through the same builtin plugin command model used by other official top-level commands.

#### Scenario: CLI loads official evaluator command
- **WHEN** the CLI initializes builtin top-level command providers
- **THEN** the evaluator command SHALL be exposed through a builtin plugin-backed command entry rather than only through an ad hoc direct registration path

#### Scenario: Source-backed evaluator mode uses existing command registration
- **WHEN** the CLI exposes source-backed evaluator usage
- **THEN** it SHALL do so through the existing evaluator command object and builtin evaluator plugin registration rather than a separate top-level command or standalone script

### Requirement: CLI evaluator extensibility uses hooks for peripheral customization

The evaluator command SHALL support plugin and hook-based extensibility for CLI-specific discovery, assembly, and output concerns without moving framework evaluation semantics into CLI handlers.

#### Scenario: Plugin customizes evaluator discovery or assembly
- **WHEN** an installed or builtin CLI plugin participates in evaluator discovery or pre-run assembly
- **THEN** the CLI SHALL provide hook points for those lifecycle stages without requiring the plugin to redefine framework execution, scoring, or gate logic

#### Scenario: Plugin extends evaluator rendering or post-run handling
- **WHEN** an installed or builtin CLI plugin needs to append summary output, upload reports, or trigger notifications after evaluation
- **THEN** the CLI SHALL provide hook points for rendering and post-run handling while preserving the framework-owned evaluation result and report contract

#### Scenario: Source-backed evaluator flow invokes evaluator hooks
- **WHEN** a source-backed evaluator run is assembled or completed
- **THEN** the CLI SHALL invoke the same evaluator hook infrastructure used by target-backed runs, with source-aware event fields that identify mode, input path, source kind, task filters, judge agent, execution agent, workspace path, and output path when available

### Requirement: CLI evaluator hook contracts are explicit

The evaluator command SHALL document the event payloads, mutable state surface, and allowed side effects for evaluator-specific CLI hooks.

#### Scenario: Plugin author implements an evaluator lifecycle hook
- **WHEN** a plugin author uses an evaluator-specific hook such as pre-run, post-run, or summary rendering
- **THEN** the CLI SHALL provide a documented hook contract describing which fields are guaranteed and what a hook may modify

#### Scenario: Source-backed hook remains CLI-scoped
- **WHEN** a plugin hook observes or customizes a source-backed evaluator run
- **THEN** the hook SHALL be limited to CLI assembly metadata, side effects, and rendering, and SHALL NOT replace framework source parsing, state adaptation, execution, scoring, gate decisions, or report schema semantics

## ADDED Requirements

### Requirement: CLI evaluator supports source-backed run mode

The evaluator command SHALL provide a source-backed run mode that accepts an input path, source kind, optional field mappings, optional task filters, optional execution agent, and judge agent configuration.

#### Scenario: User evaluates an AWorld trajectory log
- **WHEN** a user runs the evaluator with `--input`, `--kind aworld-trajectory-log`, `--task-id`, and `--judge-agent`
- **THEN** the CLI SHALL use framework trajectory-log source and replay adapters to evaluate the selected task without implementing trajectory parsing in CLI code

#### Scenario: User evaluates task and answer records
- **WHEN** a user runs the evaluator with `--input`, `--kind task-answer`, and `--judge-agent`
- **THEN** the CLI SHALL use framework task+answer source and answer-state adapters to evaluate existing answers without re-executing the target

#### Scenario: User overrides task and answer field names
- **WHEN** a user runs the evaluator with `--kind task-answer` and custom field mapping flags
- **THEN** the CLI SHALL pass those mappings to the framework source while defaulting omitted mappings to `id`, `input`, and `answer`

#### Scenario: User requests a deferred source kind
- **WHEN** a user runs the evaluator with a source kind that is defined as a future framework source but not implemented yet
- **THEN** the CLI SHALL fail with a clear unsupported-kind error rather than implementing that source kind in CLI code

### Requirement: CLI evaluator preserves source-oriented canonical commands

The evaluator CLI SHALL treat source kinds as input adapters under a single canonical source-backed command path rather than creating independent evaluator stacks for each source format.

#### Scenario: Source kind selects adapter
- **WHEN** a user specifies a supported source kind such as `task-answer` or `aworld-trajectory-log`
- **THEN** the CLI SHALL select the matching framework source adapter while preserving the same evaluation flow and report semantics

#### Scenario: Source kind is not yet supported by framework
- **WHEN** a user specifies a source kind that the framework source layer has not implemented yet
- **THEN** the CLI SHALL fail with a clear unsupported-kind error instead of implementing source parsing in CLI code

#### Scenario: Case-specific alias delegates to canonical flow
- **WHEN** a future CLI alias is added for a common source kind
- **THEN** that alias SHALL delegate to the canonical source-backed evaluator flow rather than implementing separate parsing, judging, scoring, or gating behavior
