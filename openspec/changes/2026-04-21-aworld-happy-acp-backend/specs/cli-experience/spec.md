## ADDED Requirements

### Requirement: CLI can host an ACP-compatible AWorld backend for external controllers

The AWorld CLI SHALL be able to expose an ACP-compatible backend host so external controller processes can drive AWorld agents without requiring direct changes to the AWorld Agent SDK core.

#### Scenario: Happy-compatible controller launches an AWorld backend

- **WHEN** an operator deploys AWorld as a backend controlled by an external host process that already supports ACP-style agent backends
- **THEN** the AWorld CLI can launch an ACP-compatible backend host for that controller
- **AND** the implementation lives in the CLI / host layer rather than requiring changes inside `aworld/core`

### Requirement: CLI-hosted ACP backend uses a local stdio host boundary on the worker machine

The AWorld CLI SHALL expose the ACP backend as a worker-local host process over `stdin/stdout`, with non-protocol diagnostics isolated from protocol output.

#### Scenario: Happy-compatible runner launches the AWorld backend locally

- **WHEN** Happy CLI / daemon launches the AWorld backend on the worker host
- **THEN** the backend speaks ACP over `stdin/stdout`
- **AND** diagnostics are written to `stderr` instead of mixing with ACP frames
- **AND** the design does not require Happy to call a custom AWorld HTTP channel API

#### Scenario: Happy consumes ACP frames from stdout

- **WHEN** Happy CLI / daemon reads the backend protocol stream from `stdout`
- **THEN** `stdout` contains only ACP protocol frames in the expected NDJSON-style wire format
- **AND** startup banners, debug prints, and human-readable logs are not emitted on `stdout`

### Requirement: AWorld integrates against Happy's generic ACP backend contract rather than private agent implementations

The AWorld CLI SHALL integrate with Happy through the generic ACP backend contract exposed at the host boundary, without depending on Happy-private agent runner details.

#### Scenario: Happy CLI/daemon hosts the AWorld backend

- **WHEN** Happy CLI / daemon launches and manages the AWorld backend on the worker host
- **THEN** AWorld relies only on the generic ACP process contract at that boundary
- **AND** the design does not require coupling to Happy-private agent implementation details

### Requirement: Generic ACP implementation naming remains host-agnostic

The AWorld CLI SHALL keep ACP implementation modules, commands, and public naming host-agnostic, even when Happy is the first integration target.

#### Scenario: Implementing the AWorld ACP host and runtime adapter

- **WHEN** contributors add directories, modules, commands, or public entrypoints for the ACP capability
- **THEN** they use generic ACP-oriented naming rather than Happy-specific naming
- **AND** any Happy-specific wording is confined to validation artifacts, examples, or integration documentation instead of the core ACP implementation namespace

### Requirement: ACP implementation remains isolated from unrelated aworld-cli behavior

The AWorld CLI SHALL keep ACP implementation changes concentrated in a unified ACP-specific directory and use only thin integration seams into existing CLI entrypoints.

#### Scenario: Adding the ACP host to aworld-cli

- **WHEN** contributors implement the ACP capability inside `aworld-cli`
- **THEN** the main ACP logic is added under a dedicated ACP-oriented module tree
- **AND** existing CLI flows are modified only as needed to expose a thin command or bootstrap seam
- **AND** the design avoids broad changes that would unnecessarily expand unrelated aworld-cli regression surface

### Requirement: ACP enhancements prefer existing plugin and hook extension points when feasible

The AWorld CLI SHALL prefer existing plugin and hook extension patterns for non-core ACP enhancements, rather than hard-coding all optional behavior into the ACP core path.

#### Scenario: Adding non-core ACP enhancement behavior

- **WHEN** contributors need to add optional ACP-related enhancement behavior such as context augmentation, telemetry observation, or artifact helpers
- **THEN** they first evaluate whether the behavior can be expressed through existing plugin or hook extension points
- **AND** the ACP core path remains responsible only for protocol correctness, session control, and runtime event mapping

### Requirement: ACP plugin/hook reuse must not require broad interactive-runtime coupling

The AWorld CLI SHALL reuse plugin and hook capabilities through a narrow bootstrap path rather than coupling the ACP host to the full interactive CLI runtime when such coupling would unnecessarily enlarge regression scope.

#### Scenario: Enabling plugin or hook support for the ACP host

- **WHEN** contributors add plugin or hook reuse to the ACP host
- **THEN** they prefer a narrow shared bootstrap helper or equivalent thin integration seam
- **AND** they avoid depending on unrelated interactive runtime behavior unless that dependency is explicitly justified as unavoidable

### Requirement: ACP sessions map one-to-one to host-local AWorld sessions

The AWorld CLI SHALL maintain a host-local mapping from ACP sessions to stable AWorld runtime sessions for the lifetime of the backend process.

#### Scenario: Controller reconnects to an existing ACP session

- **WHEN** an ACP client loads an existing session handled by the same backend process
- **THEN** the backend reuses the same mapped AWorld `session_id`
- **AND** turn execution continues against the same workspace/runtime context instead of creating an unrelated new session

### Requirement: ACP-backed turns are serialized per session

The AWorld CLI SHALL enforce at most one active turn per ACP session in phase 1.

#### Scenario: New prompt arrives while a turn is still active

- **WHEN** the same ACP session submits another prompt before the previous turn reaches a terminal state
- **THEN** the backend rejects or defers the new prompt according to the MVP turn policy
- **AND** it does not silently create concurrent turns for the same mapped AWorld session

### Requirement: Phase-1 ACP host exposes a fixed minimum method contract

The AWorld CLI SHALL treat `initialize`, `newSession`, `prompt`, and `cancel` as the required phase-1 ACP method surface, and SHALL NOT expand phase-1 scope by making richer session-control methods a prerequisite for Happy integration.

#### Scenario: Phase-1 controller uses the minimum required ACP methods

- **WHEN** a controller launches the phase-1 AWorld ACP host and drives a standard interactive turn
- **THEN** the backend supports `initialize`, `newSession`, `prompt`, and `cancel`
- **AND** the design does not require `loadSession`, session-listing, or mode/model mutation methods to make the primary Happy control path work

### Requirement: Phase-1 ACP prompt and cancel semantics are explicit and session-local

The AWorld CLI SHALL define `prompt` and `cancel` semantics explicitly on a per-session basis rather than relying on implicit queueing or implicit preemption.

#### Scenario: Prompt arrives while the same session already has an active or cancelling turn

- **WHEN** a `prompt` request arrives for an ACP session whose current turn is still `running` or `cancelling`
- **THEN** the backend rejects that `prompt` as busy or conflicting
- **AND** it does not silently queue the prompt
- **AND** it does not implicitly cancel the in-flight turn to accept the new one

#### Scenario: Cancel arrives for an idle session

- **WHEN** a `cancel` request arrives for an existing ACP session with no active turn
- **THEN** the backend returns a successful no-op outcome
- **AND** it does not treat the request as a protocol failure

#### Scenario: Cancel arrives for an unknown session

- **WHEN** a `cancel` request names a session that is not present in the host-local session store
- **THEN** the backend returns an explicit session-not-found style error
- **AND** it does not silently coerce the request into a successful no-op

### Requirement: Phase-1 prompt streams runtime output through sessionUpdate notifications

The AWorld CLI SHALL treat `prompt` as turn control-plane entry and SHALL deliver streamed runtime output through `sessionUpdate` notifications rather than through the `prompt` success payload.

#### Scenario: Prompt is accepted and runtime starts streaming text or tool activity

- **WHEN** the backend accepts a phase-1 `prompt` and the mapped runtime begins producing assistant-visible output
- **THEN** the backend emits that output through `sessionUpdate` notifications for the target session
- **AND** the streamed content does not depend on embedding assistant text or tool transcript inside the `prompt` success response

#### Scenario: Notification payload is emitted for a session update

- **WHEN** the backend emits a phase-1 streamed update during a turn
- **THEN** the notification payload identifies the target session and carries an `update` object containing the session-update subtype
- **AND** the design does not rely on controller-side inference from raw stdout text outside the ACP notification channel

### Requirement: Phase-1 ACP event contract prioritizes Happy-consumable text and tool lifecycle updates

The AWorld CLI SHALL emit a constrained ACP event subset that is directly consumable by Happy's generic ACP backend path, without inventing additional host-specific status events in phase 1.

#### Scenario: Runtime emits text output that Happy should render as assistant text

- **WHEN** the mapped runtime produces assistant-visible text during a turn
- **THEN** the backend emits `agent_message_chunk` updates carrying stable text content
- **AND** those updates do not mix in terminal-only logs, HUD state, or reasoning text that should not be rendered as normal output

#### Scenario: Runtime emits reasoning output that cannot be reliably separated from normal text

- **WHEN** the mapped runtime cannot stably distinguish reasoning/thought output from normal assistant output
- **THEN** the backend omits `agent_thought_chunk` rather than guessing
- **AND** it preserves correctness of normal assistant text delivery

#### Scenario: Runtime performs a tool call

- **WHEN** the mapped runtime begins and later completes a tool call
- **THEN** the backend emits a `tool_call` start event followed by a `tool_call_update` terminal event
- **AND** both events reuse the same stable `toolCallId`
- **AND** the backend does not emit duplicate tool-call starts for the same `toolCallId` within one turn

#### Scenario: Tool-call updates are shaped for Happy's current session-update handlers

- **WHEN** the backend emits phase-1 `tool_call` or `tool_call_update` session updates
- **THEN** it uses `kind` as the primary tool-name field
- **AND** it places tool input or output payload in `content`
- **AND** it does not require Happy to recover tool identity primarily from optional display-only fields such as `title`

#### Scenario: Runtime only yields a final assistant message

- **WHEN** the mapped runtime reaches normal turn completion without having emitted stable text chunks earlier
- **THEN** the backend emits at least one `agent_message_chunk` containing the final assistant text before the `prompt` call resolves successfully
- **AND** the final assistant text is not downgraded into diagnostic or error-only output

#### Scenario: Cancel has been accepted for the active turn

- **WHEN** the backend has accepted `cancel` for the active session turn
- **THEN** it does not start any new tool-call lifecycle after that acceptance point
- **AND** it only allows terminal completion races that come from already in-flight runtime completion

### Requirement: ACP capability advertisement matches the phase actually implemented

The AWorld CLI SHALL advertise only the ACP prompt/session capabilities that are actually bridged at the current phase, rather than pre-declaring future-stage support.

#### Scenario: Phase-1 host has no stable image prompt bridge

- **WHEN** the phase-1 ACP host does not implement a stable host-layer bridge for ACP image prompt blocks
- **THEN** the backend does not advertise image prompt capability during `initialize`
- **AND** contributors do not rely on future-stage roadmap items as justification for claiming that support early

#### Scenario: Phase-1 host normalizes text-compatible prompt blocks only

- **WHEN** the phase-1 ACP host accepts text-compatible prompt inputs such as plain text blocks or explicitly normalized text-bearing resources
- **THEN** it advertises only those prompt capabilities it can actually normalize and pass through correctly
- **AND** it does not silently reinterpret unsupported rich prompt blocks as if full support existed

### Requirement: LoadSession remains a continuity-gated capability rather than a phase-1 prerequisite

The AWorld CLI SHALL treat `loadSession` as a continuity capability that enters scope only after session identity, workspace continuity, and resume semantics are explicitly defined, rather than as a prerequisite for the primary phase-1 Happy integration path.

#### Scenario: Phase-1 Happy integration is evaluated before continuity prerequisites are frozen

- **WHEN** the phase-1 Happy integration path is being defined or validated
- **THEN** the design does not require `loadSession` to make the primary control path work
- **AND** the backend is not considered incomplete merely because `loadSession` remains deferred

#### Scenario: Contributors want to add `loadSession` in a later stage

- **WHEN** contributors plan to enable `loadSession`
- **THEN** they first define how ACP `sessionId` rebinds to the correct AWorld `session_id`
- **AND** they define the expected continuity for workspace/cwd and agent identity
- **AND** they avoid claiming that `loadSession` reattaches unknown in-flight turns unless that behavior is explicitly designed and validated

### Requirement: Phase-1 initialize and newSession payloads remain minimal and honest

The AWorld CLI SHALL keep the phase-1 `initialize` and `newSession` payloads limited to the minimum metadata needed for the primary Happy control path, and SHALL NOT use those payloads to pre-advertise future-stage capabilities.

#### Scenario: Phase-1 initialize response is emitted

- **WHEN** the phase-1 ACP host responds to `initialize`
- **THEN** it uses ACP SDK-aligned field naming such as `serverInfo`
- **AND** it advertises only the prompt/session capabilities that are already implemented and validated in the current phase
- **AND** it does not advertise `loadSession`, session listing, image/audio prompt support, or embedded-context support unless those capabilities are actually bridged

#### Scenario: Phase-1 newSession response is emitted

- **WHEN** the phase-1 ACP host responds to `newSession`
- **THEN** the response is allowed to contain only the session identifier needed for follow-up `prompt` calls
- **AND** the design does not require config-options, mode catalogs, model catalogs, or richer session metadata to make the phase-1 Happy control path work

### Requirement: Phase-1 newSession input handling is compatible with Happy host inputs

The AWorld CLI SHALL define explicit phase-1 handling for `newSession` inputs such as `cwd` and `mcpServers` so Happy can launch sessions without parameter-shape mismatches.

#### Scenario: Happy sends cwd and mcpServers in newSession

- **WHEN** the phase-1 backend receives a `newSession` request containing `cwd` and `mcpServers`
- **THEN** it accepts those fields at the host boundary
- **AND** it uses `cwd` as the initial session working-directory input unless rejected by explicit host policy
- **AND** it does not silently pretend unsupported `mcpServers` entries were fully bridged if they were not

### Requirement: Phase-1 human-in-loop failures are terminal at the turn level, not at the backend level

The AWorld CLI SHALL treat unbridged human-in-loop or approval flows as current-turn terminal failures with stable diagnostic identity, rather than as evidence that the ACP backend process itself is unhealthy.

#### Scenario: Runtime reaches a human approval or human input branch during an ACP-controlled turn

- **WHEN** the mapped runtime enters a branch that would require hidden worker-terminal human interaction in the current phase
- **THEN** the backend fails the current `prompt` with an explicit, diagnosable terminal error
- **AND** the error uses a stable host-owned code family for unsupported human-in-loop behavior
- **AND** the backend process remains available for subsequent prompts on the same session

#### Scenario: Contributors model phase-1 human-in-loop unsupported behavior

- **WHEN** contributors define or implement phase-1 handling for unsupported human approval/input flows
- **THEN** they do not model that case as a backend-wide `stopped` outcome
- **AND** they do not silently coerce it into `cancelled`
- **AND** they do not hide it inside normal assistant text as if the turn had succeeded

### Requirement: ACP mode prevents hidden worker-terminal blocking on CLIHumanHandler paths

The AWorld CLI SHALL prevent ACP-controlled turns from blocking on the default local-terminal `CLIHumanHandler` path.

#### Scenario: Runtime reaches a human-interaction branch during ACP execution

- **WHEN** an ACP-controlled turn reaches a branch that would otherwise invoke `CLIHumanHandler` or equivalent local-terminal input handling
- **THEN** the host intercepts that path before hidden terminal input is awaited
- **AND** it converts the turn into the designed phase-1 terminal requires-human or approval-unsupported failure surface
- **AND** it does not require changes inside `aworld/core` to do so

### Requirement: Shared plugin and hook bootstrap helper remains a narrow host-owned surface

The AWorld CLI SHALL keep any phase-1 shared plugin/hook bootstrap helper limited to discovery, activation, hook/context loading, and optional state-store setup, without coupling ACP host correctness to the full interactive CLI runtime.

#### Scenario: Contributors extract a shared bootstrap helper for ACP reuse

- **WHEN** contributors introduce a shared helper to reuse plugin/hook capabilities in the ACP host
- **THEN** that helper returns only bootstrap surfaces such as plugins, registry, hooks, contexts, state store, and diagnostic warnings
- **AND** it does not absorb session control, turn execution, runtime adaptation, or ACP request dispatch responsibilities

#### Scenario: ACP host uses the shared bootstrap helper in phase 1

- **WHEN** the phase-1 ACP host invokes the shared bootstrap helper
- **THEN** command registration side effects remain disabled by default
- **AND** the helper does not require CLI prompt refresh, HUD refresh, or other interactive-runtime-only side effects to succeed
- **AND** plugin bootstrap failures degrade to warnings or skipped plugins rather than making the ACP host itself unusable

### Requirement: Phase-1 turn-level errors use a stable minimal host-owned code family

The AWorld CLI SHALL define a small, stable, host-owned error-code family for phase-1 turn/session failures so validation and downstream consumers can match behavior without relying on ad hoc error strings.

#### Scenario: Prompt is rejected because the target session is busy

- **WHEN** a phase-1 `prompt` request is rejected because the session already has an active or cancelling turn
- **THEN** the backend reports a stable busy-style error code rather than an unstructured free-form string

#### Scenario: Prompt contains unsupported phase-1 content

- **WHEN** the phase-1 ACP host receives prompt content that it cannot honestly normalize or bridge
- **THEN** it reports a stable unsupported-prompt-content error code
- **AND** it does not pretend the prompt was fully supported

#### Scenario: Human-in-loop or approval flow is encountered in phase 1

- **WHEN** the phase-1 ACP host reaches an unbridged human-input or approval path
- **THEN** it reports a stable human-in-loop-related error code for the failed turn
- **AND** tests and validation can assert that code directly instead of matching the full human-readable message text

### Requirement: Phase-1 ACP host provides a non-interactive self-test entrypoint

The AWorld CLI SHALL provide a non-interactive Layer-1 self-validation entrypoint for the ACP host, and SHALL NOT make phase-1 correctness depend on an interactive debug client workflow.

#### Scenario: Operator or CI validates the phase-1 ACP host locally

- **WHEN** an operator or automated test invokes the phase-1 ACP self-validation entrypoint
- **THEN** the entrypoint can launch the local ACP host, exercise the minimum method flow, and verify stdio behavior without requiring human interaction
- **AND** the design does not require a REPL-style debug client to make that validation possible

#### Scenario: Contributors add an interactive debug client later

- **WHEN** contributors add a developer-facing debug client on top of the ACP host
- **THEN** that client remains optional
- **AND** it reuses the already-frozen self-testable server-launch and stdio contract rather than becoming the only supported validation path

### Requirement: Phase-1 self-test results are machine-checkable

The AWorld CLI SHALL make the phase-1 ACP self-test produce a machine-checkable result surface so local automation and CI can assert Layer-1 correctness without depending on human-readable logs.

#### Scenario: Self-test completes successfully

- **WHEN** the phase-1 ACP self-test finishes with all required cases passing
- **THEN** it emits a machine-checkable summary on `stdout`
- **AND** it exits successfully
- **AND** its pass/fail judgment does not depend on parsing free-form diagnostics from `stderr`

#### Scenario: Self-test detects a required-case failure

- **WHEN** at least one required Layer-1 self-test case fails
- **THEN** the self-test returns a non-success exit status
- **AND** the machine-checkable summary identifies the failing case by stable case identifier rather than only by free-form prose

### Requirement: Runtime adapter exposes a normalized host-owned event schema

The AWorld CLI SHALL place a host-owned normalization boundary between raw AWorld runtime outputs and ACP event mapping so the event mapper is not coupled directly to internal `Output` classes.

#### Scenario: Runtime produces mixed output objects during a turn

- **WHEN** the underlying AWorld runtime emits objects such as chunk outputs, message outputs, tool-result outputs, or other internal output variants
- **THEN** the runtime adapter converts them into a normalized host-owned event schema before ACP mapping occurs
- **AND** the event mapper does not need to inspect raw runtime output classes directly

#### Scenario: Contributors are tempted to pass raw outputs directly to the event mapper

- **WHEN** contributors implement or evolve the ACP host
- **THEN** they do not treat raw AWorld output objects as the public contract between the runtime adapter and event mapper
- **AND** they keep ACP update generation downstream of the normalized event schema rather than embedding ACP-specific logic into the runtime adapter

#### Scenario: Normalized runtime events are emitted with stable typed fields

- **WHEN** the runtime adapter emits a normalized event for phase-1 ACP mapping
- **THEN** the event carries a stable event type plus the minimum required fields for that event kind
- **AND** ordering between normalized events is represented by the host-owned event stream rather than inferred from raw runtime object identity

### Requirement: Tool lifecycle identity closes deterministically within a turn

The AWorld CLI SHALL ensure that phase-1 tool lifecycle updates close deterministically within a single turn, even when raw runtime outputs do not arrive in a perfect ACP-native order.

#### Scenario: Runtime provides a native tool call identifier

- **WHEN** the runtime adapter can obtain a stable native tool-call identifier from the relevant AWorld output objects
- **THEN** it reuses that identifier for the normalized tool lifecycle
- **AND** the downstream ACP mapping reuses the same identifier for `tool_call` and `tool_call_update`

#### Scenario: Runtime emits a tool result without a previously emitted start event

- **WHEN** the runtime adapter encounters a tool result for which no prior normalized tool-start event has been emitted in the current turn
- **THEN** it synthesizes the minimal missing tool-start event before closing that lifecycle
- **AND** it does not pass an unclosable tool-result event directly into ACP mapping

#### Scenario: Runtime lacks a stable native tool call identifier

- **WHEN** the runtime adapter cannot recover a stable native tool-call identifier from the phase-1 runtime outputs
- **THEN** it generates a host-local turn-scoped identifier and reuses it consistently for that tool lifecycle
- **AND** the design does not require that synthetic identifier to survive beyond the current turn

### Requirement: Turn-level runtime errors terminate the prompt without being rendered as normal assistant output

The AWorld CLI SHALL treat a normalized phase-1 `turn_error` as a turn-terminal failure surface for the active `prompt`, rather than translating it into normal assistant text or backend-wide host failure.

#### Scenario: Runtime adapter reports a terminal turn error

- **WHEN** the normalized runtime event stream yields a terminal `turn_error`
- **THEN** the backend finishes the active `prompt` as a structured failure
- **AND** it does not translate that error into `agent_message_chunk`
- **AND** it does not report the backend process itself as stopped unless the process is actually unhealthy

#### Scenario: A known tool lifecycle is still open when the turn errors

- **WHEN** a `turn_error` occurs after the backend has already emitted a phase-1 `tool_call` for a given tool lifecycle
- **THEN** the backend closes that known lifecycle before returning the terminal prompt failure
- **AND** it does not leave the previously emitted tool lifecycle permanently unclosed

### Requirement: Self-test automates the minimum Layer-1 correctness matrix

The AWorld CLI SHALL make the phase-1 ACP self-test cover the minimum automated correctness matrix for host validation, rather than limiting it to process startup smoke only.

#### Scenario: Self-test validates the phase-1 ACP host

- **WHEN** the phase-1 self-test entrypoint is executed
- **THEN** it automatically validates the minimum method flow, session creation, prompt execution, process-boundary discipline, and at least one closed tool lifecycle
- **AND** it includes assertions for busy-prompt rejection, idle-session cancel no-op, and final-text fallback behavior

#### Scenario: Optional manual debugging exists alongside self-test

- **WHEN** contributors or operators use an optional manual debugging flow
- **THEN** that flow may provide richer observation
- **AND** it does not replace the minimum automated Layer-1 assertion matrix required for phase-1 correctness

### Requirement: Phase-1 command-catalog metadata remains out of the ACP core contract

The AWorld CLI SHALL keep `available_commands_update` and similar command-catalog metadata out of the required phase-1 ACP contract so Happy integration does not depend on command-discovery projection.

#### Scenario: Phase-1 host completes initialize, session creation, and prompt flow

- **WHEN** the phase-1 ACP host is evaluated for correctness against the primary Happy control path
- **THEN** the absence of `available_commands_update` does not count as a failure
- **AND** the host is not required to surface plugin- or hook-discovered commands as ACP command metadata

#### Scenario: Contributors want to add command-catalog metadata later

- **WHEN** contributors consider introducing `available_commands_update` or similar metadata after phase 1
- **THEN** they treat it as an optional later-stage metadata capability
- **AND** they do not retroactively make it a prerequisite for the already-frozen phase-1 Happy control path

### Requirement: CLI-hosted backend supports worker-host deployment independent from the control plane

The AWorld CLI SHALL support deployment where the backend host runs on the worker machine independently from the remote control plane.

#### Scenario: Happy Server and AWorld worker are deployed separately

- **WHEN** the remote control plane is deployed on one host and the AWorld execution environment is deployed on another host
- **THEN** the CLI-hosted backend can run on the worker host that owns the workspace and tool environment
- **AND** the design does not require the AWorld backend to be co-located with the remote control plane service

### Requirement: CLI-hosted ACP backend exposes an MVP runtime event subset

The AWorld CLI SHALL translate existing runtime outputs into the minimum ACP-compatible event subset needed for Happy-controlled interactive turns.

#### Scenario: Runtime emits text and tool activity during a turn

- **WHEN** the mapped AWorld runtime produces streaming text, tool-call start, and tool-call completion signals
- **THEN** the backend emits corresponding ACP message-chunk and tool-call updates
- **AND** the controller can render the turn without requiring terminal-specific Rich output parsing

#### Scenario: Runtime only yields a final assistant message

- **WHEN** the runtime does not produce stable text chunks but does produce a final assistant response
- **THEN** the backend still emits assistant text to the ACP client before the terminal turn-complete signal

### Requirement: CLI-hosted ACP backend supports best-effort cancellation without framework changes

The AWorld CLI SHALL expose cancel / abort behavior for the active turn using host-layer coordination rather than `aworld/core` changes.

#### Scenario: Controller cancels an in-flight turn

- **WHEN** the ACP client issues `cancel` for the active session turn
- **THEN** the backend attempts to stop the running turn via host-layer cancellation
- **AND** the turn resolves to a terminal cancelled or already-completed outcome without requiring `aworld/core` protocol changes

### Requirement: Phase-1 ACP backend does not block on local terminal-only human input

The AWorld CLI SHALL fail explicitly instead of hanging when the runtime enters a human-input flow that is not bridged in phase 1.

#### Scenario: Runtime requests human approval/input during an ACP-controlled turn

- **WHEN** the mapped AWorld runtime reaches a human-in-loop branch that would normally wait on `CLIHumanHandler`
- **THEN** the ACP backend returns an explicit unsupported / requires-human terminal outcome
- **AND** it does not wait for hidden worker-terminal input that Happy cannot surface
