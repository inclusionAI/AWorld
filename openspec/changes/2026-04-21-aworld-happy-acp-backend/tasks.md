## 1. Design Alignment

- [ ] 1.1 Review and refine the ACP backend scope, including the hard constraints: no Happy changes and no `aworld/core` changes.
- [ ] 1.2 Confirm the worker-host deployment model for both same-host and distributed Happy Server topologies.
- [ ] 1.3 Confirm whether the backend entrypoint lives under `aworld-cli` only or also exposes optional gateway-host reuse.
- [ ] 1.4 Freeze the ACP host shape as `aworld-cli acp` over `stdio`, with `stderr` reserved for diagnostics only.
- [ ] 1.5 Freeze the integration boundary: Happy CLI/daemon is the required host role, while AWorld depends only on the generic ACP backend contract.
- [ ] 1.6 Freeze generic naming rules so ACP implementation directories, modules, and commands remain host-agnostic rather than Happy-specific.
- [ ] 1.7 Freeze implementation isolation rules so ACP logic stays concentrated in a dedicated `aworld-cli` ACP module tree with only thin entrypoint changes elsewhere.
- [ ] 1.8 Freeze plugin/hook reuse rules so non-core ACP enhancements prefer existing extension points rather than expanding ACP core scope.

## 2. Protocol Definition

- [ ] 2.1 Enumerate the minimum ACP method surface required for the first phase, prioritizing `initialize`, `newSession`, `prompt`, and `cancel`, while explicitly deferring `loadSession` behind the Stage-2 continuity gate.
- [ ] 2.2 Define the host-local session model: ACP `sessionId` to stable AWorld `session_id` mapping and bridge-process-lifetime semantics.
- [ ] 2.3 Define the first-phase behavior for turn lifecycle, single-turn serialization, cancel/abort, and terminal error termination.
- [ ] 2.4 Define how existing AWorld executor outputs (`ChunkOutput`, `MessageOutput`, `ToolResultOutput`) map into ACP updates.
- [ ] 2.5 Freeze the phase-1 method contract so `initialize`, `newSession`, `prompt`, and `cancel` are the only required methods for the primary Happy control path.
- [ ] 2.6 Freeze the per-session turn state machine (`idle`, `running`, `cancelling`, terminal) and the prompt/cancel truth table before implementation starts.
- [ ] 2.7 Freeze the phase-1 prompt content normalization rules for text, text-bearing resources, resource links, and explicitly unsupported rich blocks.
- [ ] 2.8 Freeze `initialize` capability advertisement rules so prompt/session capabilities never over-claim beyond the current implementation phase.
- [ ] 2.9 Freeze the recommended minimal phase-1 `initialize` payload, including which capability fields must stay false or absent until later stages.
- [ ] 2.10 Freeze the recommended minimal phase-1 `newSession` payload so config/mode/model metadata is not treated as a phase-1 prerequisite.
- [ ] 2.11 Freeze the minimal phase-1 turn/session error-code family and map each code to its intended failure boundary.
- [ ] 2.12 Freeze the recommended structured error-detail shape so turn-level validations can assert machine-matchable fields.
- [ ] 2.13 Freeze the required non-interactive `self-test` contract for Layer-1 validation and keep any `debug_client` optional.
- [ ] 2.14 Freeze the minimum automated `self-test` assertion matrix and separate it from optional manual debugging flows.
- [ ] 2.15 Freeze the machine-checkable `self-test` result contract, including stable case identifiers, summary fields, and exit-code semantics.
- [ ] 2.16 Freeze the required phase-1 handling of `newSession.cwd` and `newSession.mcpServers` so Happy host inputs do not become undocumented compatibility debt.
- [ ] 2.17 Freeze ACP SDK terminology alignment in design artifacts, especially `serverInfo` naming and `sessionUpdate` notification terminology.

## 3. Capability Boundaries

- [ ] 3.1 Decide the first-phase behavior for tool-call visualization and whether thinking output is required in phase 1.
- [ ] 3.2 Decide the first-phase behavior when no stable chunk stream is available and whether final text must be backfilled as a single ACP message chunk.
- [ ] 3.3 Decide the phase-1 behavior for human-in-loop / approval requests so the backend never blocks on hidden terminal input.
- [ ] 3.4 Decide the first-phase behavior for file/artifact references and whether they are deferred or minimally bridged.
- [ ] 3.5 Decide which advanced features stay explicitly out of scope for phase 1, including rich approvals and subagent-specific visualization.
- [ ] 3.6 Freeze the event ordering contract for `agent_message_chunk`, optional `agent_thought_chunk`, `tool_call`, `tool_call_update`, and final-text fallback.
- [ ] 3.7 Freeze the rule that phase 1 does not introduce a separate host-specific status event surface beyond what Happy already derives from prompt and update flow.
- [ ] 3.8 Freeze the Stage-2 continuity gate for `loadSession`, including what continuity is required before it can move into implementation scope.
- [ ] 3.9 Freeze the phase-1 human-in-loop terminal error model so unsupported approval/input branches fail the current turn without being treated as backend-wide failure.
- [ ] 3.10 Freeze the stable diagnostic error-code family for phase-1 unsupported human-in-loop behavior before implementation starts.
- [ ] 3.11 Freeze the shared plugin/hook bootstrap helper contract: allowed inputs, returned bootstrap surfaces, and prohibited responsibilities.
- [ ] 3.12 Freeze the side-effect rules for the shared bootstrap helper so command sync and interactive-runtime refresh remain opt-in or disabled in phase 1.
- [ ] 3.13 Freeze the minimal normalized runtime event schema produced by `runtime_adapter` before any ACP mapping code is written.
- [ ] 3.14 Freeze the adapter/mapper boundary so raw AWorld output objects never become the public contract consumed by `event_mapper`.
- [ ] 3.15 Freeze the `tool_call_id` normalization priority, synthetic-id fallback rule, and tool-start backfill rule for phase-1 tool lifecycle closure.
- [ ] 3.16 Freeze the exact normalized event field set and representative examples for `text_delta`, `thought_delta`, `tool_start`, `tool_end`, `final_text`, and `turn_error`.
- [ ] 3.17 Freeze the `turn_error` translation rules so prompt failure, post-error event suppression, and known-tool lifecycle closure are deterministic.
- [ ] 3.18 Freeze the rule that `available_commands_update` and similar command-catalog metadata stay out of the required phase-1 ACP contract.
- [ ] 3.19 Freeze the explicit `prompt` control-plane vs `sessionUpdate` data-plane relationship so streaming delivery is not conflated with prompt response payloads.
- [ ] 3.20 Freeze the host-owned interception strategy for `CLIHumanHandler` paths so ACP mode never blocks on hidden terminal input.
- [ ] 3.21 Freeze the Happy-compatible `tool_call` / `tool_call_update` field contract, especially `kind`, `content`, and `toolCallId`.
- [ ] 3.22 Freeze the stdout cleanliness and NDJSON framing rules so no non-protocol bytes can leak into ACP stdout.
- [ ] 3.23 Freeze the Happy idle-detection compatibility contract so tool closure and final chunk ordering support downstream idle inference.

## 4. Implementation Planning

- [ ] 4.1 Produce the concrete module breakdown for the AWorld ACP backend implementation, including session store, turn controller, and runtime event mapper.
- [ ] 4.2 Minimize touch points outside the ACP module tree and explicitly list any unavoidable thin integration seams.
- [ ] 4.3 Identify which planned ACP-adjacent enhancements should use existing plugin/hook extension points versus core ACP modules.
- [ ] 4.4 Classify every near-term ACP capability against the core / plugin-hook / validation-layer decision table before implementation starts.
- [ ] 4.5 Freeze the first-phase capability inventory from the near-term capability table before implementation starts.
- [ ] 4.6 Freeze the first-phase file touch-point map, including allowed thin-touch files and explicitly avoided files.
- [ ] 4.7 Decide the plugin/hook bootstrap strategy for phase 1: narrow shared helper preferred, no broad interactive-runtime coupling by default.
- [ ] 4.8 Freeze the first-phase implementation skeleton so each slice has named new files, allowed thin-touch files, expected tests, and bootstrap fallback rules.
- [ ] 4.9 Freeze the preferred extraction target for any shared plugin/hook bootstrap helper and the explicit fallback to a no-bootstrap phase-1 path.
- [ ] 4.10 Produce the implementation plan after the design review is complete.
- [ ] 4.11 Validate the final design against OpenSpec deltas before code work begins.

## 5. Validation Planning

- [ ] 5.1 Define targeted tests for ACP session creation/loading, turn serialization, and best-effort cancel.
- [ ] 5.2 Define targeted tests for runtime-to-ACP mapping, especially tool-call start/end and final-text fallback behavior.
- [ ] 5.3 Freeze the validation gate sequence so AWorld ACP self-validation passes before Happy contract validation, and Happy contract validation passes before capability-preservation claims.
- [ ] 5.4 Define Gate 1 exit criteria for local stdio host correctness, including stdout/stderr boundary assertions and self-test coverage.
- [ ] 5.5 Define Gate 2 exit criteria for same-host and distributed Happy smoke validation without expanding ACP scope.
- [ ] 5.6 Define Gate 3 exit criteria for capability preservation, especially Happy voice/session routing continuity rather than Happy voice-provider internals.
- [ ] 5.7 Freeze the Layer-1 self-test pass/fail contract so automation can assert required-case outcomes without parsing free-form diagnostics.
- [ ] 5.8 Freeze the Layer-2 turn-failure assertions so Happy-integrated validation distinguishes turn-level failure from backend-level host failure.
- [ ] 5.9 Define startup validation for `initialize` / `newSession` timeout and retry safety so Happy host retry behavior is exercised before implementation is considered stable.

## Phase-1 Validation Execution

- [x] ACP slice regression executed on 2026-04-22
  Command: `python -m pytest tests/acp -q`
- [x] stdio stderr safety regression executed on 2026-04-22
  Command: `python -m pytest tests/test_mcp_stdio_stderr.py -q`
- [x] OpenSpec change validation executed on 2026-04-22
  Command: `openspec validate 2026-04-21-aworld-happy-acp-backend`
- [x] repo-local ACP self-test executed on 2026-04-22
  Command: `PYTHONPATH="$(pwd)/aworld-cli/src:$(pwd)" python -m aworld_cli.main --no-banner acp self-test`
