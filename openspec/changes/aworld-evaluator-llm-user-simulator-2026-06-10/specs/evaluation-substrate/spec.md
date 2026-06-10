## MODIFIED Requirements

### Requirement: Adaptive user simulation

Runtime-composed evaluation flows SHALL support adaptive user simulators that can react to previous assistant outputs and rollout state.

#### Scenario: Simulator generates user turn from rollout context
- **WHEN** a runtime harness requests the next user turn from an adaptive simulator
- **THEN** the simulator SHALL receive the evaluation case, target metadata, current rollout state, last assistant output, and turn index
- **AND** it SHALL be able to return the next serializable user turn

#### Scenario: Simulator is async
- **WHEN** a simulator returns an awaitable next-turn result
- **THEN** the runtime harness SHALL await the result before appending the user turn

#### Scenario: Simulator stops conversation
- **WHEN** a simulator returns `None` or an explicit stop signal
- **THEN** the runtime harness SHALL stop requesting additional user turns for that rollout

#### Scenario: Simulator returns metadata
- **WHEN** a simulator returns turn metadata
- **THEN** the framework SHALL preserve only serializable metadata in trajectory/report state

### Requirement: Provider-neutral LLM simulator boundary

LLM-backed user simulation SHALL be provider-neutral at the evaluator substrate layer.

#### Scenario: Suite uses external LLM client
- **WHEN** a suite author wants to use an OpenAI, Anthropic, local, or custom model-backed user simulator
- **THEN** the evaluator substrate SHALL accept an injected callable or simulator instance rather than constructing a provider client itself

#### Scenario: Simulator contains live model client
- **WHEN** a simulator instance holds a live client, credential, or transport handle
- **THEN** the framework SHALL NOT serialize that handle into rollout state, evaluator state, or reports
