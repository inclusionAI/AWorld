## ADDED Requirements

### Requirement: The shared memory factory MUST support hybrid provider registration

The shared memory factory MUST support registering a provider shape that
combines runtime message memory with CLI durable memory.

#### Scenario: CLI runtime requests the hybrid provider

- **WHEN** `aworld-cli` initializes memory for a session using the hybrid
  provider
- **THEN** the shared memory factory MUST be able to construct that provider
- **AND** initialization MUST NOT require replacing the existing runtime
  message-memory implementation

### Requirement: Runtime message-memory semantics MUST remain stable under the hybrid provider

Using the hybrid provider for `aworld-cli` MUST NOT change the meaning of
existing runtime message-memory behavior.

#### Scenario: Runtime message history is queried during a CLI task

- **WHEN** the executing agent reads recent message history, summaries, or tool
  call sequences
- **THEN** the runtime message-memory subsystem MUST continue to behave as it
  does before this change
- **AND** durable instruction-memory additions MUST NOT corrupt runtime message
  retrieval semantics

### Requirement: Durable instruction-memory APIs MUST remain distinct from runtime message-memory APIs

The hybrid provider MUST NOT collapse CLI durable memory and runtime message
memory into one undifferentiated storage model.

#### Scenario: CLI prompt augmentation requests layered instruction memory

- **WHEN** the CLI prompt-augmentation path needs layered instruction memory or
  relevant durable-memory recall
- **THEN** the hybrid provider MUST route that request to the durable-memory
  subsystem
- **AND** runtime message-memory calls such as turn history retrieval MUST
  continue to route to the runtime subsystem

### Requirement: Workspace-scoped session-log storage MUST be supported without rewriting runtime message stores

The hybrid provider design MUST allow workspace-scoped durable session logs to be
written without changing the underlying runtime message-memory storage format.

#### Scenario: Turn-end durable-memory extraction writes a session log entry

- **WHEN** a completed CLI turn yields durable-memory candidates
- **THEN** the durable-memory subsystem MUST be able to append those candidates
  to workspace-scoped session-log storage
- **AND** the runtime message-memory store format and ownership MUST remain
  unchanged

### Requirement: The hybrid provider path MUST preserve append-only `llm_calls` truth records

The hybrid provider path MUST support append-only `llm_calls` persistence for
real model calls without mutating prior request records.

#### Scenario: A model call completes under the hybrid provider

- **WHEN** a model request is finalized and executed within a CLI session using
  the hybrid provider path
- **THEN** an append-only `llm_calls` record MUST be preserved for that call
- **AND** the record MUST preserve the final request snapshot used for the
  provider call
- **AND** the record MUST preserve the internal `request_id`
- **AND** it MUST preserve the provider `request_id` when available

### Requirement: `llm_calls` records MUST preserve normalized usage and raw provider usage

The provider-facing memory path MUST retain both normalized usage fields and raw
provider usage payloads for each captured request.

#### Scenario: Provider usage metadata is available

- **WHEN** the model provider returns usage metadata for a captured request
- **THEN** the corresponding `llm_calls` record MUST preserve normalized usage
  for stable cross-provider consumers
- **AND** it MUST preserve the raw usage payload without flattening away
  provider-specific detail
- **AND** cache usage fields, when present, MUST remain request-linked metadata
  rather than semantic messages

### Requirement: Trajectory consumers MUST prefer `llm_calls` request snapshots

The memory-provider integration MUST allow trajectory consumers to prefer
preserved `llm_calls` request snapshots before reconstructing messages from
runtime memory.

#### Scenario: A trajectory consumer reads a task with `llm_calls`

- **WHEN** a downstream trajectory builder processes a task whose `llm_calls`
  entries contain `request.messages`
- **THEN** the integration contract MUST make those request snapshots available
  as the preferred source of trajectory messages
- **AND** memory reconstruction MUST remain only a backward-compatible fallback
  for tasks that lack `llm_calls`
