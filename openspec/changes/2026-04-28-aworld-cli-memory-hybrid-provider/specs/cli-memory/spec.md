## ADDED Requirements

### Requirement: `aworld-cli` MUST expose memory as a built-in plugin capability

`aworld-cli` MUST implement its upgraded memory experience through a built-in
plugin rather than only through ad hoc console command handling.

#### Scenario: Built-in plugin discovery includes memory capability

- **WHEN** the CLI loads built-in plugins
- **THEN** a built-in `memory` plugin MUST be discoverable
- **AND** that plugin MUST be able to provide memory commands and lifecycle
  hooks

### Requirement: `aworld-cli` MUST support layered instruction memory under AWorld naming

The CLI MUST load layered instruction memory from AWorld-named files rather
than from only one discovered `AWORLD.md`.

#### Scenario: Layered instruction memory is discovered for a workspace session

- **WHEN** the operator starts a session with workspace root `W`
- **THEN** the CLI MUST be able to discover instruction memory from
  `~/.aworld/AWORLD.md`
- **AND** `W/.aworld/AWORLD.md`
- **AND** it MAY read `W/AWORLD.md` only for backward compatibility

### Requirement: Later instruction layers MUST override broader instruction layers

Workspace-local instruction MUST override broader user-level guidance when they
conflict.

#### Scenario: Workspace instruction overrides user-level guidance

- **WHEN** `~/.aworld/AWORLD.md` contains guidance `A`
- **AND** `W/.aworld/AWORLD.md` contains conflicting guidance `B`
- **THEN** the effective instruction memory for workspace `W` MUST prefer `B`

### Requirement: Phase-1 memory writes MUST target the canonical workspace file

Phase-1 workspace memory edits MUST target `W/.aworld/AWORLD.md` rather than
splitting writes across multiple workspace instruction surfaces.

#### Scenario: Operator edits workspace memory

- **WHEN** the operator uses the memory plugin to create or update workspace
  instruction memory
- **THEN** the plugin MUST write to `W/.aworld/AWORLD.md`
- **AND** it MUST NOT create `W/AWORLD.local.md` or `W/.aworld/rules/*.md` in
  phase 1

### Requirement: The memory plugin MUST support explicit durable-memory writes

The built-in memory plugin MUST provide an explicit way for the operator to
write durable memory without waiting for automatic extraction.

#### Scenario: Operator explicitly remembers durable guidance

- **WHEN** the operator invokes `/remember` or edits durable memory through the
  memory plugin
- **THEN** the durable memory store MUST be updated immediately
- **AND** the resulting memory MUST be available to later prompts in that CLI
  session

### Requirement: Completed turns MUST append durable memory candidates to workspace-scoped session logs

Every completed query loop MUST append extracted durable-memory candidates to a
workspace-scoped session-log area even when no candidate is promoted into primary
durable instruction memory.

#### Scenario: A completed turn yields low-confidence memory candidates

- **WHEN** a query loop completes and extraction finds only low-confidence
  durable-memory candidates
- **THEN** those candidates MUST be appended to workspace-scoped session logs
- **AND** the primary durable instruction-memory files MUST remain unchanged

### Requirement: Current turn-end durable-memory handling MUST remain session-log-first

The current branch MUST preserve primary durable instruction files during
turn-end extraction unless the operator performs an explicit durable write.

#### Scenario: Temporary task-local information is extracted

- **WHEN** a completed turn contains temporary task-local state that is useful
  only for the current task
- **THEN** that information MUST remain in session logs only
- **AND** it MUST NOT be promoted into primary durable instruction-memory files

#### Scenario: High-confidence feedback is extracted during automatic handling

- **WHEN** a completed turn contains an explicit, durable user correction about
  how future work should be done
- **THEN** that feedback MUST still remain in session logs in the current
  branch
- **AND** promotion into primary durable instruction memory MUST remain an
  explicit-write path until a later governance change lands

### Requirement: Relevant durable-memory recall MUST be selective

The CLI MUST avoid replaying all durable memory into every prompt.

#### Scenario: Query-specific recall loads only relevant durable memories

- **WHEN** a user asks about a specific task or constraint
- **THEN** the CLI MUST be able to recall only durable memories that are
  clearly relevant to that query
- **AND** unrelated durable memories SHOULD NOT be injected into the prompt

### Requirement: CLI task artifacts MUST preserve append-only `llm_calls` for model requests

The CLI memory flow MUST preserve an append-only `llm_calls` record for each
real model request that participates in workspace-scoped session logging or
downstream task artifacts.

#### Scenario: A model call is captured for a CLI task

- **WHEN** a CLI task performs a real model call
- **THEN** the resulting task artifacts MUST include a new append-only
  `llm_calls` entry for that call
- **AND** the entry MUST preserve the final provider-bound request snapshot
- **AND** the entry MUST include the internal `request_id`
- **AND** it MUST include the provider `request_id` when available

### Requirement: CLI observability MUST preserve normalized and raw request-linked usage

The CLI memory flow MUST preserve both normalized usage totals and raw
provider-native usage details for each captured `llm_calls` entry.

#### Scenario: Cache-related usage is reported by the provider

- **WHEN** a provider reports usage data including cache usage fields
- **THEN** the corresponding `llm_calls` entry MUST preserve normalized usage
  totals for stable accounting
- **AND** it MUST also preserve the raw usage payload for observability
- **AND** any cache usage detail MUST remain linked to that request by
  `request_id`

### Requirement: Trajectories MUST prefer preserved request snapshots over memory reconstruction

When a task contains `llm_calls`, trajectory generation for CLI-observable task
artifacts MUST prefer the preserved request snapshot before reconstructing
messages from memory.

#### Scenario: A task has both `llm_calls` and reconstructable memory history

- **WHEN** trajectory output is built for a task whose `llm_calls` entries
  contain `request.messages`
- **THEN** the trajectory MUST prefer `llm_calls[*].request.messages`
- **AND** memory reconstruction MUST be used only as a fallback for tasks that
  lack the relevant `llm_calls` snapshot

### Requirement: Cache usage MUST NOT become trajectory semantic content

Cache usage is observability metadata, not semantic message content.

#### Scenario: CLI trajectory output is emitted for a request with cache usage

- **WHEN** a captured `llm_calls` entry includes cache usage details
- **THEN** that cache usage MAY be surfaced in request-linked metadata or logs
- **AND** it MUST NOT be injected into `trajectory.state.messages`
- **AND** it MUST NOT be treated as durable memory recall content
