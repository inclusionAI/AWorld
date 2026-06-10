## MODIFIED Requirements

### Requirement: Runtime-composed evaluation harnesses

Suite-backed evaluation flows SHALL support opt-in rollout-owning runtime harnesses that execute multi-turn cases and produce normalized rollout state while preserving existing single-shot evaluator behavior.

#### Scenario: Suite selects a rollout-owning harness
- **WHEN** a suite-backed evaluator declares a runtime-composition harness
- **THEN** the framework SHALL execute the case through that harness lifecycle rather than treating the harness as only an execution-spec holder

#### Scenario: Existing single-shot suites remain compatible
- **WHEN** a suite-backed evaluator does not declare a runtime-composition harness
- **THEN** the framework SHALL preserve the current static, agent, task, and program execution behavior

#### Scenario: Runtime harness returns rollout state
- **WHEN** a runtime harness completes a case rollout
- **THEN** the framework SHALL normalize the rollout into evaluator state containing terminal answer, trajectory, tool calls, usage, timing, error, and metadata fields usable by existing scorer helpers

### Requirement: Multi-turn rollout state

Runtime-composed evaluation flows SHALL represent multi-turn execution as serializable rollout state.

#### Scenario: Rollout has multiple turns
- **WHEN** a runtime-composed harness executes multiple user/assistant/tool turns
- **THEN** the framework SHALL preserve ordered turns, normalized messages, trajectory entries, tool calls, terminal status, and terminal answer

#### Scenario: Runtime composition creates child states
- **WHEN** a runtime wrapper retries or falls back to another harness attempt
- **THEN** the framework SHALL preserve child or attempt state so reports can explain the composed execution path

#### Scenario: Rollout state is serializable
- **WHEN** rollout state is converted into evaluator state or report payloads
- **THEN** the framework SHALL exclude live runtime handles, clients, agent instances, and simulator objects

### Requirement: User simulation

Runtime-composed evaluation flows SHALL support deterministic user simulators that drive controlled multi-turn rollouts.

#### Scenario: Scripted simulator provides turns
- **WHEN** a case includes scripted user turns
- **THEN** the framework SHALL let the scripted simulator provide those turns in order until it reaches a terminal condition

#### Scenario: Single-prompt simulator preserves one-shot behavior
- **WHEN** a case only includes a single prompt or query
- **THEN** the framework SHALL support a simulator that emits one user turn and then terminates unless the harness requests additional turns

#### Scenario: Simulator errors are captured
- **WHEN** a user simulator cannot produce a valid next turn
- **THEN** the framework SHALL mark the rollout state as failed with a serializable error rather than storing the simulator object

### Requirement: Step-level rewards

Runtime-composed evaluation flows SHALL support step-level reward records that can be aggregated into normal evaluator metrics.

#### Scenario: Step rewarder evaluates rollout steps
- **WHEN** a step rewarder inspects a rollout step
- **THEN** it SHALL emit a reward record containing metric name, step index, numeric value, reason, and serializable metadata

#### Scenario: Rewards aggregate into metrics
- **WHEN** a rollout contains step reward records
- **THEN** the framework SHALL aggregate configured reward metrics into case metrics, aggregate metrics, and structured gate inputs

#### Scenario: Rewards do not replace final judge output
- **WHEN** a suite uses both typed judge output and step rewards
- **THEN** the framework SHALL keep judge metrics and reward metrics distinct while allowing composite gates to reference both

### Requirement: Runtime composition wrappers

Runtime-composed evaluation flows SHALL support at least one wrapper harness that composes around a base harness and preserves attempt state.

#### Scenario: Retry wrapper reruns failed rollout
- **WHEN** a retry wrapper receives a failed terminal rollout or a configured failed reward condition
- **THEN** it SHALL rerun the base harness up to the configured limit and preserve each attempt as child or attempt state

#### Scenario: Retry wrapper reports terminal attempt
- **WHEN** a retry wrapper finishes
- **THEN** it SHALL expose the selected terminal attempt as the main rollout state while retaining previous attempts for inspection

### Requirement: Runtime-composition adoption suite

The framework SHALL include one opt-in adoption suite that exercises runtime composition and v2 extensibility together.

#### Scenario: Adoption suite uses active v2 capabilities
- **WHEN** the adoption suite is selected
- **THEN** it SHALL use a typed judge schema, composite gate, trajectory scorer, step-level reward metric, scripted user simulator, and rollout-owning harness

#### Scenario: App evaluator remains unchanged
- **WHEN** callers use the existing `app-evaluator` suite
- **THEN** its behavior SHALL remain compatible unless a later explicit migration change updates that suite
