## MODIFIED Requirements

### Requirement: Execution-backed suite flows reuse framework evaluation primitives

The framework SHALL support suite-backed evaluation flows that execute targets through existing AWorld runtime primitives and adapter-backed program execution while exposing reusable execution results for downstream scoring.

#### Scenario: Suite-backed flow lowers execution through a reusable harness boundary
- **WHEN** a suite-backed evaluator declares execution directly or through an evaluator harness
- **THEN** the framework SHALL compile the flow through a single harness boundary that owns execution configuration and adapter selection while preserving the existing suite/case/state model

#### Scenario: Suite-backed flow executes through current task or agent runtime
- **WHEN** a suite-backed evaluator is configured to execute through the existing AWorld agent or task runtime
- **THEN** the framework SHALL adapt the suite flow through framework-owned execution adapters instead of hardcoding runner invocation details across evaluator targets

#### Scenario: Suite-backed flow executes through a program-backed runtime
- **WHEN** a suite-backed evaluator is configured with a program-backed execution reference
- **THEN** the framework SHALL execute that program through a framework-owned execution adapter and normalize the result into the common evaluator execution state

#### Scenario: Program-backed runtime is bounded to importable callables
- **WHEN** a suite-backed evaluator declares program-backed execution
- **THEN** the framework SHALL require an importable callable reference and reject unsupported command, sandbox, workflow-engine, or missing-reference configuration for this change

#### Scenario: Importable callable execution is trusted
- **WHEN** a suite-backed evaluator declares a program reference or task-builder reference
- **THEN** the framework SHALL treat that reference as trusted in-process code controlled by the runner or workspace owner and SHALL NOT expose it through declared JSON manifests in this change

#### Scenario: Program-backed runtime returns supported output
- **WHEN** a program-backed evaluator returns an `EvalState`, an `EvalState`-shaped mapping, a `TaskResponse`, or a bare answer value
- **THEN** the framework SHALL normalize that output into the common evaluator execution state without storing live runtime handles in the state

#### Scenario: Existing static suite execution remains available
- **WHEN** a suite-backed evaluator is defined without execution-backed target settings
- **THEN** the framework SHALL continue to support the current static evaluation path as a valid suite execution mode

### Requirement: Schema-constrained judge outputs

Suite-backed evaluation flows SHALL validate judge outputs against an explicit typed judge schema before final scoring and reporting are completed, while preserving compatibility for current lightweight schema definitions.

#### Scenario: Judge output matches the declared typed schema
- **WHEN** a suite-backed evaluator returns a result that satisfies the declared typed judge-output model
- **THEN** the framework SHALL accept the result for downstream scoring, gating, and reporting

#### Scenario: Judge output violates the declared typed schema
- **WHEN** a suite-backed evaluator returns a result that fails the declared typed judge-output model
- **THEN** the framework SHALL surface the typed schema violation as an evaluation failure or invalid result state rather than silently accepting malformed output

#### Scenario: Legacy schema definitions remain valid during migration
- **WHEN** an existing suite-backed evaluator still uses the current lightweight required-field schema definition
- **THEN** the framework SHALL continue to validate that suite through a compatibility path without forcing immediate migration

#### Scenario: Judge schema metadata is exposed once per report
- **WHEN** a suite-backed evaluator has a typed or compatibility judge schema
- **THEN** the framework SHALL expose the derived judge schema metadata at the report level rather than duplicating the schema in every case result

### Requirement: First-class gate outcomes

Suite-backed evaluation flows SHALL evaluate a declared structured gate policy and produce a gate outcome of `pass`, `fail`, or `needs_approval`.

#### Scenario: Composite pass conditions succeed
- **WHEN** all required pass conditions in the structured gate policy are satisfied
- **THEN** the framework SHALL emit a `pass` gate outcome

#### Scenario: Approval-stage conditions match
- **WHEN** pass conditions are not satisfied but the structured gate policy marks the result as eligible for human review
- **THEN** the framework SHALL emit a `needs_approval` gate outcome

#### Scenario: Composite gate conditions fail without approval path
- **WHEN** required pass conditions are not satisfied and no approval-stage conditions apply
- **THEN** the framework SHALL emit a `fail` gate outcome

#### Scenario: Legacy threshold gates remain valid
- **WHEN** an existing suite-backed evaluator uses the current single-threshold gate definition
- **THEN** the framework SHALL preserve that behavior through a compatibility lowering into the structured gate policy model

#### Scenario: Gate conditions use explicit comparison operators
- **WHEN** a structured gate condition compares a metric to a threshold
- **THEN** the framework SHALL support `>=`, `<=`, `>`, `<`, `==`, and `!=` operators and surface unsupported operators as invalid gate configuration

#### Scenario: Gate references a missing metric
- **WHEN** a structured gate condition references a metric that is not present in aggregate results
- **THEN** the framework SHALL fail the gate closed, include the missing condition in `failed_conditions`, and still return the completed case results and available metrics

### Requirement: Suite-declared trajectory evaluation

Suite-backed evaluation flows SHALL support normalized trajectory-level scoring alongside result-level judge scoring while preserving the common report metric shape.

#### Scenario: Suite declares trajectory scorers
- **WHEN** a suite-backed evaluator declares trajectory scorer definitions
- **THEN** the framework SHALL lower those definitions into normal evaluator scoring criteria that inspect the normalized execution state trajectory

#### Scenario: Trajectory evaluation remains single-shot in this change
- **WHEN** a suite-backed evaluator uses trajectory scorers in this change
- **THEN** the framework SHALL score the trajectory already captured in `EvalState` and SHALL NOT claim multi-turn rollout ownership, user simulation, lifecycle hooks, or step-level training reward semantics

#### Scenario: Trajectory scorer results participate in gates and reports
- **WHEN** a trajectory scorer emits a metric result
- **THEN** the framework SHALL include that metric in case metrics, aggregate metrics, and structured gate evaluation without replacing the final-result judge score

#### Scenario: Suite has no trajectory scorers
- **WHEN** a suite-backed evaluator omits trajectory scorer definitions
- **THEN** the framework SHALL preserve the current result-focused scoring behavior
