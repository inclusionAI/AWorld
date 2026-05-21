## Context

The existing AWorld memory model already solves a different problem well:

- runtime message retention
- short-term summary compression
- tool-result history reconstruction
- structured long-term extraction such as `UserProfile`, `Fact`, and
  `AgentExperience`

That runtime contract is used deeply by agent execution and should not be
destabilized just to improve CLI memory ergonomics.

`aworld-cli` also already exposes a lightweight workspace-context file flow:

- `/memory` in the console edits or views `AWORLD.md`
- `AWORLDFileNeuron` injects a single discovered file into the system prompt

The current gap is not "AWorld has no memory." The gap is that `aworld-cli`
lacks a plugin-backed, layered workspace-memory experience for long-running
collaboration.

The agreed direction is:

1. keep `AWORLD.md / .aworld/` naming
2. ship memory as a built-in CLI plugin
3. separate CLI durable memory from runtime message memory
4. compose both through a hybrid provider seam

## Current Delivery Boundary

This change delivers the phase-1 memory MVP:

- hybrid provider seam and provider-backed prompt loading
- layered workspace instruction discovery under `AWORLD.md / .aworld/`
- built-in `memory` plugin commands and hooks
- explicit durable writes into the canonical workspace instruction file
- append-only workspace session logs
- selective session-log recall
- append-only `llm_calls` capture for real model requests within the same
  session-log-oriented delivery boundary
- request-linked usage observability that preserves normalized totals plus raw
  provider usage detail when available
- trajectory preference for preserved request snapshots before legacy memory
  reconstruction fallback

This change does not yet deliver:

- typed durable-memory taxonomy beyond workspace instruction text
- governed auto-promotion from session logs into durable memory
- promotion quality metrics
- optional memory HUD or richer plugin-local workflow state
- a full prompt-cache redesign or provider-specific cache optimization
- a broad rewrite of runtime memory or message history semantics

## Goals / Non-Goals

**Goals**

- Add a built-in `memory` plugin to `aworld-cli`.
- Upgrade `AWORLD.md` loading into layered instruction memory.
- Add workspace-scoped durable memory logs for session-derived memory capture.
- Add query-time selective relevant-memory recall for `aworld-cli`.
- Establish a durable-memory seam that can later support governed promotion.
- Keep current runtime message-memory semantics stable.
- Introduce a provider seam that lets CLI durable memory be injected into the
  shared stack without broad `aworld/core` churn.
- Preserve append-only `llm_calls` as the truth source for real model calls.
- Preserve final request snapshots, internal `request_id`, and provider
  `request_id` when available.
- Preserve normalized usage plus raw provider usage for request-linked
  observability.
- Prefer `llm_calls[*].request.messages` when building trajectories for new
  tasks.
- Keep cache usage out of trajectory semantic message content.

**Non-Goals**

- Do not replace `AworldMemory` as the runtime message-memory engine.
- Do not rewrite current summary generation or long-term extraction behavior.
- Do not add typed durable-memory taxonomy in this branch.
- Do not add governed auto-promotion in this branch.
- Do not add promotion precision / pollution metrics in this branch.
- Do not add optional HUD state in this branch.
- Do not add timed promotion in phase 1.
- Do not add background batch rewriting of primary memory files in phase 1.
- Do not add team-memory synchronization protocols in phase 1.
- Do not rename AWorld memory files to Claude-branded naming.
- Do not redesign prompt caching end to end in this branch.
- Do not introduce provider-specific cache optimization strategy in this
  branch.
- Do not rewrite runtime memory, summary generation, or semantic message
  reconstruction beyond the `llm_calls` preference/fallback rule.

## Decisions

### Decision: Memory becomes a built-in `aworld-cli` plugin

The CLI-facing memory experience should move into a built-in plugin rooted
under `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/`.

The plugin should own:

- memory commands
- turn-end extraction hooks

Why:

- It matches how other durable CLI capabilities are now added to `aworld-cli`.
- It avoids continuing to grow special-case console logic.
- It gives memory a clean lifecycle surface for commands and hooks, while
  leaving HUD and richer workflow state for later follow-up work.

### Decision: Layered instruction memory preserves AWorld naming

Phase 1 treats the active CLI workspace root `W` as the local instruction-memory
anchor. `W` comes from the current `workspace_path` or CLI working directory.
Git repository detection is not required for this mechanism.

Phase 1 layered instruction-memory discovery should support:

1. `~/.aworld/AWORLD.md`
2. `W/.aworld/AWORLD.md`
3. `W/AWORLD.md` as compatibility read only

`W/.aworld/AWORLD.md` is the canonical workspace-level file for phase 1.
`W/AWORLD.md` remains readable only to preserve compatibility with existing
workspaces and should not be the primary write target for new edits.

Workspace-local instruction should override broader user-level guidance when
they conflict.

Why:

- This keeps AWorld product language intact.
- It provides the minimum layered control needed for a good workspace memory
  experience.
- It reduces phase-1 complexity by avoiding local-private and multi-file rule
  merges.

### Decision: `AWORLD.local.md` and `rules/*.md` are deferred beyond phase 1

The following instruction-memory surfaces are explicitly out of scope for phase
1:

- `W/AWORLD.local.md`
- `W/.aworld/rules/*.md`

Why:

- They add merge, precedence, and explainability complexity that is not needed
  to establish the workspace-memory baseline.
- They can be added later without invalidating the simpler global-plus-workspace
  model.

### Decision: Durable memory is scoped to workspace, not repository identity

Phase 1 durable memory should use the active workspace as its only local scope
boundary for:

- session-log writes
- relevant recall
- canonical workspace instruction writes

This design must not require introducing a separate repository identity model
or repo-keyed durable-memory store. If the workspace happens to be a git
repository, that is only one common way to obtain `W`.

Why:

- `aworld-cli` already has an established `workspace_path` mechanism.
- This keeps scope aligned with existing hooks, plugin state, and ACP session
  context.
- It avoids adding project-identity semantics that the product does not need in
  phase 1.

### Decision: Runtime memory and durable memory remain separate subsystems

The current runtime message-memory provider must continue to own:

- `add/get/get_all/get_last_n/search`
- summary generation
- message / tool-call history integrity
- current long-term extraction paths

The new durable-memory provider should own:

- layered instruction file discovery and parsing
- canonical workspace instruction writes
- session log writing
- relevant recall

Why:

- These two systems solve different product problems.
- Runtime execution correctness must not be coupled to CLI durable-memory
  evolution.
- This separation keeps existing message-memory semantics stable.

### Decision: `llm_calls` is the append-only truth source at the model boundary

Within this existing memory change, `llm_calls` becomes the append-only truth
source for real model requests and responses. Capture must happen at the model
boundary after request hooks have finalized the provider-bound payload and
before downstream consumers attempt to reconstruct message history.

Each appended record must preserve:

- the final request snapshot, especially `request.messages`
- the internal `request_id`
- the provider `request_id` when the provider exposes one
- normalized usage for stable cross-provider accounting
- raw provider usage payloads for request-linked observability, including cache
  usage fields when available

Trajectory generation must prefer `llm_calls[*].request.messages` for new
tasks before falling back to memory reconstruction. That preference improves
fidelity because it uses the actual submitted request instead of a reconstructed
semantic history.

Cache usage remains observability metadata linked to the corresponding
`request_id`. It must not become semantic message content in trajectories,
reconstructed prompt history, or durable memory recall. The scope here is only
to preserve and surface cache usage alongside the request record, not to change
trajectory semantics or implement cache optimization policy.

Why:

- the model boundary is the only stable place that sees the final provider
  request, internal request identifiers, and provider response metadata
- append-only `llm_calls` records fit the existing session-log-oriented memory
  design without mutating runtime memory semantics
- trajectory fidelity improves only if preserved request snapshots outrank
  reconstructed message history
- cache usage is useful operational data, but treating it as semantic content
  would pollute trajectories and durable memory with non-message metadata

### Decision: A hybrid provider adapts durable memory into the shared stack

Phase 1 should introduce a hybrid provider shape:

- `AworldMemory` remains the runtime provider
- `CliDurableMemoryProvider` manages layered instruction memory and durable
  logs
- `HybridMemoryProvider` composes both

The hybrid provider should not pretend that all memory is one homogeneous
storage model. Instead, it should route API calls to the subsystem that owns
them.

Why:

- It gives `aworld-cli` a seam for durable-memory evolution without forcing a
  rewrite of runtime memory behavior.
- It opens a path for future memory backends through provider registration.
- It reduces integration risk by keeping current runtime-memory calls intact.

### Decision: Delivery is phased and must start with the lowest-risk slice

Implementation and rollout should proceed in phased slices:

- Phase 1A
  - runtime memory contract regression coverage
  - workspace instruction discovery
  - hybrid provider seam and provider registration
  - provider-backed prompt augmentation
  - legacy fallback switch
- Phase 1B
  - built-in memory plugin shell
  - canonical `/memory` workspace-file editing flow
  - `/remember` explicit durable writes
- Phase 1C
  - workspace session logs
  - session-log recall baseline

- Governance follow-up
  - typed durable-memory taxonomy
  - governed auto-promotion
  - promotion metrics and thresholds

The current branch intentionally stops after Phase 1C-lite. Phase 1A verified
that runtime message-memory behavior stayed unchanged before the CLI started
mutating or recalling durable memory at turn boundaries.

Why:

- This follows the risk ordering identified in review.
- It prevents memory UX work from destabilizing agent runtime semantics.
- It gives the team a clean checkpoint after the provider seam is in place.

### Decision: Legacy fallback must remain available during phase 1 rollout

Phase 1 should preserve a rollback path that disables the new CLI durable-memory
integration and returns to the legacy behavior:

- `AworldMemory` as the only active memory provider
- single-file `AWORLDFileNeuron` behavior
- existing console `/memory` behavior if the plugin path is unavailable

The fallback may be activated by a runtime flag. Prompt-loading code should
also retain a local legacy fallback if provider-backed instruction loading is
unavailable.

Why:

- The `AworldMemory` runtime contract is deeply coupled to task execution.
- Prompt-augmentation failures must not strand the CLI without workspace
  context.
- A rollback path reduces adoption risk while phase-1 coverage matures.

### Decision: Canonical workspace-file migration is guided, not automatic

Phase 1 should treat `W/.aworld/AWORLD.md` as the canonical workspace file and
`W/AWORLD.md` as compatibility read only.

If a workspace still relies on `W/AWORLD.md`, the CLI should warn that:

- the file is being read for backward compatibility
- new edits should move to `W/.aworld/AWORLD.md`
- no automatic rewrite or merge will happen in phase 1

Why:

- This preserves compatibility without silently rewriting user-owned files.
- It keeps the canonical write path explicit.
- It avoids migration logic before the new provider seam is stable.

### Decision: Promotion is explicit-first and log-first

Phase 1 durable updates distinguish two implemented cases plus one deferred
case:

1. explicit durable writes
   - `/memory`
   - `/remember`
   - user explicitly asks the agent to remember durable guidance

2. turn-end extraction
   - every completed query loop writes candidate observations to workspace-scoped
     session logs

3. automatic promotion
   - deferred beyond this branch

Phase 1 must not include:

- governed auto-promotion
- timed promotion
- background batch rewriting of primary memory files

Why:

- Append-only session logs are safer than direct mutation of primary instruction
  files.
- This preserves auditability and reduces pollution risk while the provider seam
  and recall path mature.

### Decision: Promotion triggers occur at stable turn boundaries

Turn-end extraction should happen only after a complete query loop, meaning:

- the assistant has produced a final answer
- there are no pending tool calls for that turn
- the turn transcript is stable enough for extraction

Immediate durable promotion is allowed for:

- `/remember`
- direct `/memory` edits
- explicit user instructions to remember durable guidance

Why:

- Stable turn boundaries avoid logging intermediate reasoning or failed
  attempts.
- Explicit writes are user-authorized and should not be delayed.

### Decision: Typed durable-memory taxonomy is deferred beyond the current branch

The longer-term durable-memory design may still adopt explicit types such as
`user`, `feedback`, `workspace`, and `reference`, but this branch does not add
that storage model yet.

Current explicit durable writes continue to land in the canonical workspace
instruction surface instead of a separate typed durable-memory store.

Why:

- The provider seam, plugin lifecycle, session-log capture, and recall path are
  the lower-risk foundations.
- A typed store without promotion governance would create a misleading sense of
  completeness.

### Decision: Relevant recall is selective, not full replay

Layered instruction memory is always loaded according to precedence. Session-log
memory should be selectively recalled based on the active query rather than
being fully replayed into every prompt.

Phase 1 relevant recall should:

- scan lightweight session-log entries
- rank or select only clearly useful memories
- avoid injecting unrelated memory into the prompt

Why:

- Full replay causes prompt pollution and token waste.
- Selective recall produces a Claude Code-like operator experience without
  bloating every session.

## Session Update Model

After the upgrade, each `aworld-cli` session updates memory through two
parallel paths.

### Runtime path

Existing runtime message memory continues to record:

- conversation messages
- tool results
- summaries
- current long-term extraction flows

This path remains owned by `AworldMemory`.

### Durable path

The new memory plugin and durable provider manage:

- layered instruction memory reads
- explicit writes through `/memory` and `/remember`
- turn-end extraction into workspace-scoped session logs
- selective recall into later prompts

## Validation Strategy

Validation should be structured in four layers.

### 1. Contract validation

Verify:

- existing `AworldMemory` add/get/get_all/get_last_n/search behavior
- summary generation and message/tool-call pairing semantics
- layered discovery order
- precedence and merge rules
- provider registration and hybrid delegation
- session-log and workspace instruction file layout

### 2. Behavior validation

Verify:

- `/memory` edits affect later prompts immediately
- `/remember` promotes durable memory immediately
- turn-end extraction writes session logs without rewriting primary memory
  files
- relevant recall injects only useful memories

### 3. Plugin integration validation

Verify:

- built-in plugin discovery
- command registration
- hook execution
- plugin enable / disable lifecycle

### 4. Acceptance validation

Define end-to-end scenarios for:

- user preference memory
- workspace file memory
- workspace override of global guidance
- session-log-only cases
- relevant recall with prompt cleanliness

## Rollout And Rollback

Phase 1 production rollout should follow these gates:

1. complete Phase 1A and run focused runtime regression coverage
2. enable hybrid-provider-backed prompt augmentation for workspace instruction
   memory
3. enable plugin-owned explicit durable writes
4. enable session-log capture and recall
5. treat auto-promotion as a follow-up phase after separate design and metrics
   work

Rollback behavior must be straightforward:

- disable hybrid-provider-backed durable memory
- keep `AworldMemory` runtime paths active
- fall back to legacy single-file `AWORLDFileNeuron` discovery
- leave workspace durable files and session logs untouched on disk

## Implementation Structure

Recommended phase-1 module layout:

- `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/`
- `aworld-cli/src/aworld_cli/memory/`
  - discovery
  - relevance
  - provider
  - hybrid
- provider-registry updates in `aworld/core/memory.py` and
  `aworld/memory/main.py`
- layered instruction-memory integration replacing single-file-only
  `AWORLDFileNeuron` behavior

## Deferred Work

The following are intentionally deferred beyond phase 1:

- typed durable-memory taxonomy
- governed auto-promotion
- promotion precision / pollution metrics
- optional HUD state
- `/dream`
- timed consolidation
- background batch rewriting of primary memory files
- `AWORLD.local.md`
- `.aworld/rules/*.md`
- networked team-memory synchronization
- replacing current runtime message-memory semantics
