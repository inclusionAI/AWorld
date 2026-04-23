## 1. Design Alignment

- [x] 1.1 Review and refine the ACP backend scope, including the hard constraints: no Happy changes and no `aworld/core` changes.
- [x] 1.2 Confirm the worker-host deployment model for both same-host and distributed Happy Server topologies.
- [x] 1.3 Confirm whether the backend entrypoint lives under `aworld-cli` only or also exposes optional gateway-host reuse.
- [x] 1.4 Freeze the ACP host shape as `aworld-cli acp` over `stdio`, with `stderr` reserved for diagnostics only.
- [x] 1.5 Freeze the integration boundary: Happy CLI/daemon is the required host role, while AWorld depends only on the generic ACP backend contract.
- [x] 1.6 Freeze generic naming rules so ACP implementation directories, modules, and commands remain host-agnostic rather than Happy-specific.
- [x] 1.7 Freeze implementation isolation rules so ACP logic stays concentrated in a dedicated `aworld-cli` ACP module tree with only thin entrypoint changes elsewhere.
- [x] 1.8 Freeze plugin/hook reuse rules so non-core ACP enhancements prefer existing extension points rather than expanding ACP core scope.

## 2. Protocol Definition

- [x] 2.1 Enumerate the minimum ACP method surface required for the first phase, prioritizing `initialize`, `newSession`, `prompt`, and `cancel`, while explicitly deferring `loadSession` behind the Stage-2 continuity gate.
- [x] 2.2 Define the host-local session model: ACP `sessionId` to stable AWorld `session_id` mapping and bridge-process-lifetime semantics.
- [x] 2.3 Define the first-phase behavior for turn lifecycle, single-turn serialization, cancel/abort, and terminal error termination.
- [x] 2.4 Define how existing AWorld executor outputs (`ChunkOutput`, `MessageOutput`, `ToolResultOutput`) map into ACP updates.
- [x] 2.5 Freeze the phase-1 method contract so `initialize`, `newSession`, `prompt`, and `cancel` are the only required methods for the primary Happy control path.
- [x] 2.6 Freeze the per-session turn state machine (`idle`, `running`, `cancelling`, terminal) and the prompt/cancel truth table before implementation starts.
- [x] 2.7 Freeze the phase-1 prompt content normalization rules for text, text-bearing resources, resource links, and explicitly unsupported rich blocks.
- [x] 2.8 Freeze `initialize` capability advertisement rules so prompt/session capabilities never over-claim beyond the current implementation phase.
- [x] 2.9 Freeze the recommended minimal phase-1 `initialize` payload, including which capability fields must stay false or absent until later stages.
- [x] 2.10 Freeze the recommended minimal phase-1 `newSession` payload so config/mode/model metadata is not treated as a phase-1 prerequisite.
- [x] 2.11 Freeze the minimal phase-1 turn/session error-code family and map each code to its intended failure boundary.
- [x] 2.12 Freeze the recommended structured error-detail shape so turn-level validations can assert machine-matchable fields.
- [x] 2.13 Freeze the required non-interactive `self-test` contract for Layer-1 validation and keep any `debug_client` optional.
- [x] 2.14 Freeze the minimum automated `self-test` assertion matrix and separate it from optional manual debugging flows.
- [x] 2.15 Freeze the machine-checkable `self-test` result contract, including stable case identifiers, summary fields, and exit-code semantics.
- [x] 2.16 Freeze the required phase-1 handling of `newSession.cwd` and `newSession.mcpServers` so Happy host inputs do not become undocumented compatibility debt.
- [x] 2.17 Freeze ACP SDK terminology alignment in design artifacts, especially `serverInfo` naming and `sessionUpdate` notification terminology.

## 3. Capability Boundaries

- [x] 3.1 Decide the first-phase behavior for tool-call visualization and whether thinking output is required in phase 1.
- [x] 3.2 Decide the first-phase behavior when no stable chunk stream is available and whether final text must be backfilled as a single ACP message chunk.
- [x] 3.3 Decide the phase-1 behavior for human-in-loop / approval requests so the backend never blocks on hidden terminal input.
- [x] 3.4 Decide the first-phase behavior for file/artifact references and whether they are deferred or minimally bridged.
- [x] 3.5 Decide which advanced features stay explicitly out of scope for phase 1, including rich approvals and subagent-specific visualization.
- [x] 3.6 Freeze the event ordering contract for `agent_message_chunk`, optional `agent_thought_chunk`, `tool_call`, `tool_call_update`, and final-text fallback.
- [x] 3.7 Freeze the rule that phase 1 does not introduce a separate host-specific status event surface beyond what Happy already derives from prompt and update flow.
- [x] 3.8 Freeze the Stage-2 continuity gate for `loadSession`, including what continuity is required before it can move into implementation scope.
- [x] 3.9 Freeze the phase-1 human-in-loop terminal error model so unsupported approval/input branches fail the current turn without being treated as backend-wide failure.
- [x] 3.10 Freeze the stable diagnostic error-code family for phase-1 unsupported human-in-loop behavior before implementation starts.
- [x] 3.11 Freeze the shared plugin/hook bootstrap helper contract: allowed inputs, returned bootstrap surfaces, and prohibited responsibilities.
- [x] 3.12 Freeze the side-effect rules for the shared bootstrap helper so command sync and interactive-runtime refresh remain opt-in or disabled in phase 1.
- [x] 3.13 Freeze the minimal normalized runtime event schema produced by `runtime_adapter` before any ACP mapping code is written.
- [x] 3.14 Freeze the adapter/mapper boundary so raw AWorld output objects never become the public contract consumed by `event_mapper`.
- [x] 3.15 Freeze the `tool_call_id` normalization priority, synthetic-id fallback rule, and tool-start backfill rule for phase-1 tool lifecycle closure.
- [x] 3.16 Freeze the exact normalized event field set and representative examples for `text_delta`, `thought_delta`, `tool_start`, `tool_end`, `final_text`, and `turn_error`.
- [x] 3.17 Freeze the `turn_error` translation rules so prompt failure, post-error event suppression, and known-tool lifecycle closure are deterministic.
- [x] 3.18 Freeze the rule that `available_commands_update` and similar command-catalog metadata stay out of the required phase-1 ACP contract.
- [x] 3.19 Freeze the explicit `prompt` control-plane vs `sessionUpdate` data-plane relationship so streaming delivery is not conflated with prompt response payloads.
- [x] 3.20 Freeze the host-owned interception strategy for `CLIHumanHandler` paths so ACP mode never blocks on hidden terminal input.
- [x] 3.21 Freeze the Happy-compatible `tool_call` / `tool_call_update` field contract, especially `kind`, `content`, and `toolCallId`.
- [x] 3.22 Freeze the stdout cleanliness and NDJSON framing rules so no non-protocol bytes can leak into ACP stdout.
- [x] 3.23 Freeze the Happy idle-detection compatibility contract so tool closure and final chunk ordering support downstream idle inference.

## 4. Implementation Planning

- [x] 4.1 Produce the concrete module breakdown for the AWorld ACP backend implementation, including session store, turn controller, and runtime event mapper.
- [x] 4.2 Minimize touch points outside the ACP module tree and explicitly list any unavoidable thin integration seams.
- [x] 4.3 Identify which planned ACP-adjacent enhancements should use existing plugin/hook extension points versus core ACP modules.
- [x] 4.4 Classify every near-term ACP capability against the core / plugin-hook / validation-layer decision table before implementation starts.
- [x] 4.5 Freeze the first-phase capability inventory from the near-term capability table before implementation starts.
- [x] 4.6 Freeze the first-phase file touch-point map, including allowed thin-touch files and explicitly avoided files.
- [x] 4.7 Decide the plugin/hook bootstrap strategy for phase 1: narrow shared helper preferred, no broad interactive-runtime coupling by default.
- [x] 4.8 Freeze the first-phase implementation skeleton so each slice has named new files, allowed thin-touch files, expected tests, and bootstrap fallback rules.
- [x] 4.9 Freeze the preferred extraction target for any shared plugin/hook bootstrap helper and the explicit fallback to a no-bootstrap phase-1 path.
- [x] 4.10 Produce the implementation plan after the design review is complete.
- [x] 4.11 Validate the final design against OpenSpec deltas before code work begins.

## 5. Validation Planning

- [x] 5.1 Define targeted tests for ACP session creation/loading, turn serialization, and best-effort cancel.
- [x] 5.2 Define targeted tests for runtime-to-ACP mapping, especially tool-call start/end and final-text fallback behavior.
- [x] 5.3 Freeze the validation gate sequence so AWorld ACP self-validation passes before Happy contract validation, and Happy contract validation passes before capability-preservation claims.
- [x] 5.4 Define Gate 1 exit criteria for local stdio host correctness, including stdout/stderr boundary assertions and self-test coverage.
- [x] 5.5 Define Gate 2 exit criteria for same-host and distributed Happy smoke validation without expanding ACP scope.
- [x] 5.6 Define Gate 3 exit criteria for capability preservation, especially Happy voice/session routing continuity rather than Happy voice-provider internals.
- [x] 5.7 Freeze the Layer-1 self-test pass/fail contract so automation can assert required-case outcomes without parsing free-form diagnostics.
- [x] 5.8 Freeze the Layer-2 turn-failure assertions so Happy-integrated validation distinguishes turn-level failure from backend-level host failure.
- [x] 5.9 Define startup validation for `initialize` / `newSession` timeout and retry safety so Happy host retry behavior is exercised before implementation is considered stable.

## Phase-1 Validation Execution

- [x] ACP slice regression executed on 2026-04-22
  Command: `python -m pytest tests/acp -q`
- [x] stdio stderr safety regression executed on 2026-04-22
  Command: `python -m pytest tests/test_mcp_stdio_stderr.py -q`
- [x] OpenSpec change validation executed on 2026-04-22
  Command: `openspec validate 2026-04-21-aworld-happy-acp-backend`
- [x] repo-local ACP self-test executed on 2026-04-22
  Command: `PYTHONPATH="$(pwd)/aworld-cli/src:$(pwd)" python -m aworld_cli.main --no-banner acp self-test`
- [x] ACP phase-1 regression re-executed on 2026-04-23 after `turn_error` suppression tightening
  Command: `python -m pytest tests/acp -q`

## Implementation Checkpoints

- [x] ACP executor path no longer depends on process-global `os.chdir()` for session `cwd`; session workspace semantics are injected explicitly through ACP-local executor wiring.
- [x] ACP executor workspace provisioning no longer mutates process-global `WORKSPACE_PATH`; session workspace base paths are injected through ACP-local context config so independent prompts do not leak filesystem state across sessions.
- [x] ACP host no longer serializes independent sessions behind a host-global execution lock; only narrow bootstrap/agent loading remains serialized.
- [x] ACP-safe bootstrap now feeds agent directories into the bridge and attaches an ACP-local plugin runtime surface to executors for plugin/hook reuse.
- [x] Prompt normalization now supports phase-1 text-compatible `resource` embedded text and `resource_link` reference fallback, while preserving explicit rejection of unsupported rich-only prompts.
- [x] Layer-1 `self-test` summary now reports stable case ids for `initialize_handshake`, `new_session_usable`, `prompt_visible_text`, `cancel_idle_noop`, `stdout_protocol_only`, and `stderr_diagnostics_only`.
- [x] ACP server request dispatch now processes prompt/cancel requests concurrently enough to support active-turn `cancel` and same-session `busy` rejection over the stdio host boundary.
- [x] Layer-1 `self-test` now covers `tool_lifecycle_closes`, `final_text_fallback`, `prompt_busy_rejected`, and `cancel_active_terminal` using an ACP-local deterministic bridge fixture rather than the message-echo fallback path.
- [x] ACP Layer-1 server-launch and stdio client wiring are now extracted into a reusable generic harness module so future Layer-2 validation can reuse the same host transport path without growing `self_test.py`.
- [x] Phase-1 ACP required-case semantics are now centralized in a reusable validation module so `self-test` and future host-integration validation can share the same assertion matrix.
- [x] Phase-1 validation semantics are now profile-driven and can execute against an arbitrary stdio command via a generic runner, so later host-integration validation only needs a launch command plus profile-specific fixtures.
- [x] A default-skipped external stdio host contract test scaffold now exists under `tests/integration/`, so later same-host or distributed host validation can be enabled by command/env injection without adding Happy-specific production code here.
- [x] `aworld-cli acp validate-stdio-host` now exposes the generic phase-1 stdio host contract runner as a CLI entrypoint, so later Layer-2 validation is not gated on pytest-only environment-driven scaffolding.
- [x] `validate-stdio-host` setup failures now emit machine-checkable error payloads and return exit code `2`, so host-launch or parameter errors do not collapse into stderr-only diagnostics.
- [x] The default-skipped external host integration scaffold now goes through the same `validate-stdio-host` CLI path that manual same-host/distributed smoke will use, rather than bypassing the CLI through direct Python helper calls.
- [x] `validate-stdio-host` now has regression coverage proving explicit `newSession` payloads are applied to the target host, so topology-specific session inputs are not merely accepted by the CLI surface but actually exercised end-to-end.
- [x] External host smoke inputs now support declarative `--config-file` loading, and the integration layer includes a machine-readable template asset so same-host/distributed validation can start from a stable generic config shape instead of ad hoc flag assembly.
- [x] Validation assets now include separate same-host and distributed config templates plus a colocated execution README, so later topology smoke can start from fixed generic artifacts rather than reconstructing the workflow from discussion history.
- [x] Validation config files now support environment-variable placeholder expansion with regression coverage for both success and machine-checkable setup failure, so topology templates can remain generic while still being directly runnable in concrete environments.
- [x] Validation config files can now declare `topology` directly inside `--config-file` payloads and inherit built-in template defaults before local overrides are applied, so topology-based host smoke does not depend on choosing between checked-in config files and CLI template selection.
- [x] `validate-stdio-host` now supports startup timeout and retry controls with regression coverage for timeout failure and retry-safe recovery, closing the validation gap around `initialize/newSession` startup behavior before Layer-2 host smoke.
- [x] `render-validation-config` now exposes topology templates through the ACP CLI with machine-checkable metadata and placeholder expansion, so operators can generate same-host/distributed validation configs directly instead of hand-editing fixture files.
- [x] Validation config authoring now has machine-checkable schema discovery (`configAllowedFields`, `topologies`, `configSchemaPath`) and regression coverage for unsupported fields/topology values, reducing the risk that future same-host/distributed smoke drifts into silently ignored config keys while giving operators a stable checked-in JSON Schema for offline validation.
- [x] The integration smoke scaffold now supports topology-only execution via `AWORLD_ACP_VALIDATION_TOPOLOGY`, so same-host/distributed validation can be triggered directly from built-in templates without first materializing a config file.
- [x] `render-validation-config` can now write rendered topology configs directly to disk while preserving the same machine-checkable stdout payload, so manual smoke setup no longer depends on shell redirection semantics.
- [x] `turn_error` terminal handling now deterministically closes every previously opened tool lifecycle with `tool_call_update(status=failed)`, suppresses post-error assistant/tool events, and preserves session reusability for the next prompt.
