## ADDED Requirements

### Requirement: AWorld MUST separate context content production from prompt assembly

The AWorld context system MUST keep `amni` responsible for context content production and governance, while a separate assembly layer is responsible for prompt structuring.

#### Scenario: Existing `amni` context producers remain the source of content

- **WHEN** AWorld prepares a model request using AWORLD.md, relevant memory, summaries, history, or other neuron-produced content
- **THEN** `amni` MUST remain the source of those content items
- **AND** the assembly layer MUST consume those items without taking over their underlying production logic

### Requirement: AWorld MUST build a provider-neutral prompt assembly plan

The AWorld context system MUST express prompt structure through a provider-neutral prompt assembly object rather than directly embedding provider-specific cache fields in context-layer data models.

#### Scenario: Prompt assembly is constructed for a cache-capable request

- **WHEN** AWorld assembles a request intended to be cache-friendly
- **THEN** it MUST produce a provider-neutral assembly plan that distinguishes stable and dynamic prompt sections
- **AND** that assembly plan MUST NOT require Anthropic-specific or other provider-specific request fields to exist in the context layer

### Requirement: Stable prefix MUST be request-time only and MUST NOT be persisted as ordinary history

The AWorld context system MUST treat stable prompt prefix assembly as request-time state rather than as persisted conversation history.

#### Scenario: A request reuses the same stable prompt prefix

- **WHEN** two requests produce the same stable prompt content according to the configured stable-prefix strategy
- **THEN** AWorld MAY reuse the stable prefix through request-time runtime state
- **AND** it MUST NOT require that stable prefix to be stored as an ordinary persisted system message in history or memory

### Requirement: Phase-1 stable and dynamic classification MUST follow the agreed defaults

The AWorld context system MUST classify stable and dynamic prompt inputs according to the phase-1 default boundary unless explicitly extended by a future change.

#### Scenario: Phase-1 prompt sections are assembled

- **WHEN** AWorld builds the phase-1 prompt assembly plan
- **THEN** base system rules, AWORLD.md or workspace instruction, stable skill or policy descriptions, and tools semantic hints MUST be treated as stable inputs
- **AND** relevant memory recall, conversation history, summaries, and current task-related prompt injection MUST be treated as dynamic inputs

### Requirement: Context cache assembly MUST be configurable at both agent and model layers

The AWorld context system MUST allow both agent-level and model-level configuration to enable or disable context cache assembly behavior.

#### Scenario: Either config layer disables the feature

- **WHEN** the agent config or model config explicitly disables context cache assembly
- **THEN** AWorld MUST fall back to ordinary prompt assembly behavior for that request path
- **AND** it MUST NOT require provider-native prompt cache handling to keep the request functional
