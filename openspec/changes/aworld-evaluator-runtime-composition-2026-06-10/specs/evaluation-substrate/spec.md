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
- **THEN** the framework SHALL normalize the rollout into evaluator state containing terminal answer, outcome data, trajectory, tool calls, usage, timing, standard rollout metrics, error, and metadata fields usable by existing scorer helpers

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

#### Scenario: Standard rollout metrics are derived
- **WHEN** rollout state contains turns, tool calls, token usage, or timing data
- **THEN** the framework SHALL derive standard metrics such as turn count, tool-call count, token usage, and duration without requiring suite-specific custom scorers

### Requirement: Outcome and state-check grading

Runtime-composed evaluation flows SHALL support outcome graders that verify final environment, artifact, or domain state separately from final text answer and trajectory.

#### Scenario: Outcome grader checks final state
- **WHEN** a runtime-composed suite declares an outcome or state-check grader
- **THEN** the framework SHALL evaluate the rollout state's terminal outcome or serializable environment snapshot and emit normal evaluator metrics with pass/fail details

#### Scenario: Outcome metrics remain distinct
- **WHEN** a suite uses typed judge output, trajectory scorers, step rewards, and outcome graders together
- **THEN** the framework SHALL keep outcome metrics distinct while allowing composite gates to reference them alongside judge, trajectory, and reward metrics

#### Scenario: Environment check needs sandbox reset
- **WHEN** an outcome grader requires clean-environment isolation, command execution, or sandbox reset semantics
- **THEN** the framework SHALL treat that as unsupported in this change and leave it to a dedicated environment-isolation change

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
- **THEN** it SHALL emit a reward record containing metric name, step index, numeric value, optional weight, optional partial-credit marker, reason, and serializable metadata

#### Scenario: Rewards aggregate into metrics
- **WHEN** a rollout contains step reward records
- **THEN** the framework SHALL aggregate configured reward metrics into case metrics, aggregate metrics, and structured gate inputs, including weighted and partial-credit summaries when configured

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

#### Scenario: Retry is not trial evaluation
- **WHEN** retry or fallback wrapper results are reported
- **THEN** the framework SHALL NOT label those attempts as independent trials, pass@k, or pass^k metrics

### Requirement: Evaluation purpose metadata

Suite-backed evaluation flows SHALL allow suites to declare whether they are intended for capability evaluation or regression evaluation.

#### Scenario: Suite declares evaluation purpose
- **WHEN** a suite declares evaluation-purpose metadata
- **THEN** the framework SHALL preserve that metadata in the resolved suite/report context without changing scorer semantics

### Requirement: Runtime-composition adoption suite

The framework SHALL include one opt-in adoption suite that exercises runtime composition and v2 extensibility together.

#### Scenario: Adoption suite uses active v2 capabilities
- **WHEN** the adoption suite is selected
- **THEN** it SHALL use a typed judge schema, composite gate, outcome/state-check grader, trajectory scorer, step-level reward metric, scripted user simulator, and rollout-owning harness

#### Scenario: App evaluator remains unchanged
- **WHEN** callers use the existing `app-evaluator` suite
- **THEN** its behavior SHALL remain compatible unless a later explicit migration change updates that suite

### Requirement: Multi-trial metrics are deferred

Runtime composition SHALL distinguish retry/fallback execution from independent trial-based evaluation.

#### Scenario: Caller requests pass@k or pass^k
- **WHEN** a caller needs independent repeated trials, pass@k, pass^k, or trial-distribution metrics
- **THEN** the framework SHALL treat that as out of scope for this change and require a later multi-trial evaluator change
